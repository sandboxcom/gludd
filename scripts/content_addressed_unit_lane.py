#!/usr/bin/env python3
"""Run the allowlisted pure-unit lane through Pants' hermetic process CAS.

Gludd deliberately does not infer or hash Python dependency edges here. Pants owns
the transitive graph and the process cache key. This controller only enforces the
reviewed allowlist, selects cached versus forced-fresh execution, normalizes Pants
reports, and signs exact candidate provenance after the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from scripts.ci_receipt_auth import (
        ReceiptSigner,
        create_receipt_envelope,
        load_gate_receipt_auth_context,
        parse_revoked_signers,
    )
    from scripts.gate_status_attestation import repository_state_id
    from scripts.resource_arbiter import resource_root as project_resource_root
else:
    from ci_receipt_auth import (
        ReceiptSigner,
        create_receipt_envelope,
        load_gate_receipt_auth_context,
        parse_revoked_signers,
    )
    from gate_status_attestation import repository_state_id
    from resource_arbiter import resource_root as project_resource_root

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "pants_unit_cache_lane.json"
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "pants_version",
        "targets",
        "control_files",
        "relevant_environment",
        "dynamic_inputs",
        "external_inputs",
        "max_local_workers",
        "hosted_workers",
        "minimum_aggregate_coverage",
        "minimum_file_coverage",
    }
)
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_PANTS_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ENVIRONMENT_NAME = re.compile(r"^GLUDD_[A-Z0-9_]+$")
_TARGET_ADDRESS = re.compile(r"^[A-Za-z0-9_./-]+:[A-Za-z0-9_.-]+$")
_MAX_LIST_ITEMS = 32
_MAX_STRING_LENGTH = 256
ExecutionMode = Literal["cached", "fresh", "nightly-fresh"]
GraphStatus = Literal["owned", "ambiguous", "unowned", "error"]


@dataclass(frozen=True, slots=True)
class LaneManifest:
    """Reviewed, bounded policy for the single cached unit slice."""

    schema_version: int
    pants_version: str
    targets: tuple[str, ...]
    control_files: tuple[str, ...]
    relevant_environment: tuple[str, ...]
    dynamic_inputs: tuple[str, ...]
    external_inputs: tuple[str, ...]
    max_local_workers: int
    hosted_workers: int
    minimum_aggregate_coverage: int
    minimum_file_coverage: int


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Runtime facts that determine whether reuse is safe."""

    mode: ExecutionMode
    hosted_ci: bool
    graph_status: GraphStatus = "owned"
    dynamic_inputs: tuple[str, ...] = ()
    external_inputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """Bounded cache and concurrency decision for one Pants invocation."""

    force_fresh: bool
    allow_cross_commit_cache: bool
    workers: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Observable result from one owned Pants child."""

    returncode: int
    elapsed_seconds: float


def canonical_sha256(payload: object) -> str:
    """Return the canonical SHA-256 digest of JSON-compatible evidence."""
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_strings(value: object, *, field: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        raise ValueError(f"{field} must be a bounded list")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if any(
        not isinstance(item, str)
        or not item
        or len(item) > _MAX_STRING_LENGTH
        for item in value
    ):
        raise ValueError(f"{field} contains an invalid value")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} contains duplicates")
    return tuple(value)


def load_manifest(path: Path) -> LaneManifest:
    """Load the reviewed cache allowlist with an exact fail-closed schema."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("lane manifest is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
        raise ValueError("lane manifest fields do not match the exact schema")
    if raw["schema_version"] != 1:
        raise ValueError("lane manifest schema version is unsupported")
    pants_version = raw["pants_version"]
    if not isinstance(pants_version, str) or not _PANTS_VERSION.fullmatch(pants_version):
        raise ValueError("Pants version must be an exact stable version")
    targets = _bounded_strings(raw["targets"], field="targets", allow_empty=False)
    if any(_TARGET_ADDRESS.fullmatch(target) is None or "::" in target for target in targets):
        raise ValueError("target allowlist contains a broad or malformed target")
    control_files = _bounded_strings(
        raw["control_files"],
        field="control_files",
        allow_empty=False,
    )
    for control_file in control_files:
        candidate = PurePosixPath(control_file)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("control file must remain below the repository root")
    relevant_environment = _bounded_strings(
        raw["relevant_environment"],
        field="relevant_environment",
        allow_empty=False,
    )
    if any(_ENVIRONMENT_NAME.fullmatch(name) is None for name in relevant_environment):
        raise ValueError("environment allowlist contains an unsafe name")
    dynamic_inputs = _bounded_strings(
        raw["dynamic_inputs"],
        field="dynamic_inputs",
        allow_empty=True,
    )
    external_inputs = _bounded_strings(
        raw["external_inputs"],
        field="external_inputs",
        allow_empty=True,
    )
    max_local_workers = raw["max_local_workers"]
    hosted_workers = raw["hosted_workers"]
    if type(max_local_workers) is not int or not 1 <= max_local_workers <= 2:
        raise ValueError("local workers must be in 1..2")
    if hosted_workers != 1:
        raise ValueError("hosted workers must equal one")
    aggregate_floor = raw["minimum_aggregate_coverage"]
    file_floor = raw["minimum_file_coverage"]
    if type(aggregate_floor) is not int or aggregate_floor < 85 or aggregate_floor > 100:
        raise ValueError("aggregate coverage must remain in 85..100")
    if type(file_floor) is not int or file_floor < 75 or file_floor > 100:
        raise ValueError("file coverage must remain in 75..100")
    return LaneManifest(
        schema_version=1,
        pants_version=pants_version,
        targets=targets,
        control_files=control_files,
        relevant_environment=relevant_environment,
        dynamic_inputs=dynamic_inputs,
        external_inputs=external_inputs,
        max_local_workers=max_local_workers,
        hosted_workers=hosted_workers,
        minimum_aggregate_coverage=aggregate_floor,
        minimum_file_coverage=file_floor,
    )


def pants_process_identity(inputs: Mapping[str, bytes]) -> dict[str, object]:
    """Describe an opaque Pants process identity without walking dependencies.

    Production reuse is authorized only by Pants' own hermetic process CAS. The
    mapping accepted here is evidence supplied by that boundary or by mutation
    tests; this function never discovers files or dependency edges itself.
    """
    if not inputs or len(inputs) > _MAX_LIST_ITEMS:
        raise ValueError("Pants process inputs must be nonempty and bounded")
    digest = hashlib.sha256(b"gludd-pants-process-evidence-v1\0")
    for category in sorted(inputs):
        value = inputs[category]
        if (
            not isinstance(category, str)
            or not category
            or len(category) > 64
            or type(value) is not bytes
        ):
            raise ValueError("Pants process input evidence is malformed")
        digest.update(category.encode("ascii"))
        digest.update(b"\0")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return {
        "authority": "pants-hermetic-process-cas-v1",
        "digest": digest.hexdigest(),
        "input_categories": sorted(inputs),
    }


def decide_execution(
    request: ExecutionRequest,
    *,
    max_local_workers: int,
    hosted_workers: int,
) -> ExecutionDecision:
    """Fail closed to a forced-fresh execution when reuse is not provably safe."""
    if max_local_workers not in {1, 2} or hosted_workers != 1:
        raise ValueError("worker policy exceeds the admitted bound")
    if request.mode not in {"cached", "fresh", "nightly-fresh"}:
        raise ValueError("execution mode is unsupported")
    if request.graph_status not in {"owned", "ambiguous", "unowned", "error"}:
        raise ValueError("dependency graph status is unsupported")
    reasons: list[str] = []
    if request.hosted_ci:
        reasons.append("hosted-ci-cold-lane")
    if request.mode == "fresh":
        reasons.append("operator-forced-fresh")
    elif request.mode == "nightly-fresh":
        reasons.append("nightly-forced-fresh")
    graph_reasons = {
        "ambiguous": "dependency-ambiguous",
        "unowned": "dependency-unowned",
        "error": "dependency-probe-error",
    }
    if request.graph_status in graph_reasons:
        reasons.append(graph_reasons[request.graph_status])
    if request.dynamic_inputs:
        reasons.append("dynamic-input")
    if request.external_inputs:
        reasons.append("external-input")
    force_fresh = bool(reasons)
    return ExecutionDecision(
        force_fresh=force_fresh,
        allow_cross_commit_cache=not force_fresh,
        workers=hosted_workers if request.hosted_ci else max_local_workers,
        reasons=tuple(reasons),
    )


def build_pants_command(
    *,
    pants_binary: str,
    decision: ExecutionDecision,
    targets: Sequence[str],
    cache_root: Path,
    workdir: Path,
    distdir: Path,
) -> list[str]:
    """Build one bounded Pants test command; Pants retains cache-key ownership."""
    if decision.workers not in {1, 2} or not targets:
        raise ValueError("Pants command exceeds the lane bound")
    command = [
        pants_binary,
        "--no-pantsd",
        "--no-dynamic-ui",
        f"--process-execution-local-parallelism={decision.workers}",
        f"--local-store-dir={cache_root / 'lmdb_store'}",
        f"--named-caches-dir={cache_root / 'named_caches'}",
        f"--pants-workdir={workdir}",
        f"--pants-distdir={distdir}",
    ]
    if decision.force_fresh:
        command.append("--test-force")
    if "hosted-ci-cold-lane" in decision.reasons:
        command.append("--no-local-cache")
    command.extend(["test", *targets])
    return command


def normalized_junit_manifest(report_root: Path) -> dict[str, object]:
    """Normalize Pants' JUnit output to exact node/outcome evidence."""
    reports = sorted(report_root.rglob("*.xml"))
    if not reports:
        raise ValueError("Pants JUnit report is missing")
    nodes: list[dict[str, str]] = []
    for report in reports:
        try:
            root = ET.parse(report).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ValueError("Pants JUnit report is malformed") from exc
        for testcase in root.iter("testcase"):
            classname = testcase.attrib.get("classname", "")
            name = testcase.attrib.get("name", "")
            if not classname or not name:
                raise ValueError("Pants JUnit testcase identity is incomplete")
            if testcase.find("error") is not None or testcase.find("failure") is not None:
                outcome = "failed"
            elif testcase.find("skipped") is not None:
                outcome = "skipped"
            else:
                outcome = "passed"
            nodes.append({"node": f"{classname}::{name}", "outcome": outcome})
    nodes.sort(key=lambda item: item["node"])
    if not nodes or len({item["node"] for item in nodes}) != len(nodes):
        raise ValueError("Pants JUnit node set is empty or ambiguous")
    counts = {
        outcome: sum(item["outcome"] == outcome for item in nodes)
        for outcome in ("failed", "passed", "skipped")
    }
    return {"nodes": nodes, "counts": counts}


def normalized_coverage_manifest(
    report: Path,
    *,
    aggregate_floor: int,
    file_floor: int,
) -> dict[str, object]:
    """Normalize branch-aware Pants coverage and enforce both release floors."""
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pants coverage report is unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        raise ValueError("Pants coverage report is malformed")
    if payload["meta"].get("branch_coverage") is not True:
        raise ValueError("Pants coverage report is not branch-aware")
    totals = payload.get("totals")
    files = payload.get("files")
    if not isinstance(totals, dict) or not isinstance(files, dict) or not files:
        raise ValueError("Pants coverage totals or files are missing")
    aggregate = totals.get("percent_covered")
    if not isinstance(aggregate, int | float):
        raise ValueError("Pants aggregate coverage is missing")
    per_file: dict[str, float] = {}
    for name, evidence in sorted(files.items()):
        if not isinstance(name, str) or not isinstance(evidence, dict):
            raise ValueError("Pants per-file coverage is malformed")
        summary = evidence.get("summary")
        if not isinstance(summary, dict):
            raise ValueError("Pants per-file coverage summary is missing")
        percentage = summary.get("percent_covered")
        if not isinstance(percentage, int | float):
            raise ValueError("Pants per-file percentage is missing")
        per_file[name] = round(float(percentage), 6)
    aggregate_value = round(float(aggregate), 6)
    floors_satisfied = aggregate_value >= aggregate_floor and all(
        value >= file_floor for value in per_file.values()
    )
    return {
        "branch_coverage": True,
        "aggregate_percent": aggregate_value,
        "files": per_file,
        "floors": {"aggregate": aggregate_floor, "file": file_floor},
        "floors_satisfied": floors_satisfied,
    }


def compare_manifests(
    cached: Mapping[str, object],
    fresh: Mapping[str, object],
) -> dict[str, object]:
    """Compare exact nodes, outcomes, and branch coverage for nightly drift."""
    mismatches: list[str] = []
    cached_junit = cached.get("junit")
    fresh_junit = fresh.get("junit")
    if cached_junit != fresh_junit:
        mismatches.append("junit-node-outcomes")
    cached_coverage = cached.get("coverage")
    fresh_coverage = fresh.get("coverage")
    if cached_coverage != fresh_coverage:
        mismatches.append("branch-coverage")
    return {"equivalent": not mismatches, "mismatches": mismatches}


def build_receipt_payload(
    *,
    candidate: Mapping[str, object],
    execution: Mapping[str, object],
    result: Mapping[str, object],
    mode: str,
    comparison: Mapping[str, object] | None,
) -> dict[str, object]:
    """Build exact final provenance while excluding SHA from cache identity."""
    candidate_sha = candidate.get("candidate_sha")
    state_id = candidate.get("repository_state_id")
    if not isinstance(candidate_sha, str) or not _HEX_40.fullmatch(candidate_sha):
        raise ValueError("candidate SHA is invalid")
    if not isinstance(state_id, str) or not _HEX_64.fullmatch(state_id):
        raise ValueError("repository state identity is invalid")
    if candidate_sha in json.dumps(execution, sort_keys=True):
        raise ValueError("candidate SHA must not contaminate cache execution identity")
    returncode = result.get("returncode")
    comparison_ok = comparison is None or comparison.get("equivalent") is True
    release_eligible = bool(
        candidate.get("clean") is True
        and candidate.get("exact_sha") is True
        and returncode == 0
        and comparison_ok
    )
    return {
        "schema_version": 1,
        "receipt_kind": "pants-unit-cache-terminal",
        "candidate_provenance": dict(candidate),
        "execution_identity": dict(execution),
        "result": dict(result),
        "mode": mode,
        "comparison": None if comparison is None else dict(comparison),
        "release_eligible": release_eligible,
    }


def authenticate_payload(
    payload: Mapping[str, object],
    *,
    signer: ReceiptSigner,
) -> dict[str, object]:
    """Sign the exact terminal payload with Gludd's established HMAC model."""
    execution = payload.get("execution_identity")
    result = payload.get("result")
    if not isinstance(execution, Mapping) or not isinstance(result, Mapping):
        raise ValueError("terminal receipt payload is incomplete")
    return create_receipt_envelope(
        receipt_kind="pass" if result.get("returncode") == 0 else "failure",
        action_digest=canonical_sha256(execution),
        content_sha256=canonical_sha256(payload),
        signer=signer,
    )


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(
                json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git_output(*arguments: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.returncode, completed.stdout.strip()


def _candidate_provenance() -> dict[str, object]:
    head_rc, head_sha = _git_output("rev-parse", "HEAD")
    status_rc, status = _git_output("status", "--porcelain", "--untracked-files=all")
    expected_sha = os.environ.get("GLUDD_CANDIDATE_SHA", head_sha)
    clean = status_rc == 0 and not status
    return {
        "candidate_sha": head_sha,
        "expected_sha": expected_sha,
        "repository_state_id": repository_state_id(
            ROOT,
            source="index" if clean else "worktree",
        ),
        "clean": clean,
        "exact_sha": head_rc == 0 and head_sha == expected_sha,
    }


def _control_input_evidence(
    manifest: LaneManifest,
    *,
    graph_output: str,
) -> dict[str, bytes]:
    """Capture fixed control-plane evidence; Pants owns transitive source inputs."""
    control_digest = hashlib.sha256()
    for relative in manifest.control_files:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"control file is missing or unsafe: {relative}")
        control_digest.update(relative.encode("utf-8"))
        control_digest.update(b"\0")
        control_digest.update(path.read_bytes())
    environment = {
        name: os.environ.get(name, "") for name in manifest.relevant_environment
    }
    return {
        "pants_transitive_graph": graph_output.encode("utf-8"),
        "control_plane": control_digest.digest(),
        "interpreter": (
            f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ).encode("ascii"),
        "plugin": f"pants-{manifest.pants_version}".encode("ascii"),
        "platform": f"{sys.platform}-{platform.machine()}".encode("ascii"),
        "environment": json.dumps(environment, sort_keys=True).encode("utf-8"),
        "coverage": (
            f"branch:85/75:{manifest.minimum_aggregate_coverage}/{manifest.minimum_file_coverage}"
        ).encode("ascii"),
    }


def _graph_probe_command(
    *,
    pants_binary: str,
    workers: int,
    cache_root: Path,
    workdir: Path,
    targets: Sequence[str],
) -> list[str]:
    return [
        pants_binary,
        "--no-pantsd",
        "--no-dynamic-ui",
        f"--process-execution-local-parallelism={workers}",
        f"--local-store-dir={cache_root / 'lmdb_store'}",
        f"--named-caches-dir={cache_root / 'named_caches'}",
        f"--pants-workdir={workdir}",
        "dependencies",
        "--transitive",
        *targets,
    ]


def _probe_graph(
    *,
    pants_binary: str,
    workers: int,
    cache_root: Path,
    workdir: Path,
    targets: Sequence[str],
) -> tuple[GraphStatus, str]:
    command = _graph_probe_command(
        pants_binary=pants_binary,
        workers=workers,
        cache_root=cache_root,
        workdir=workdir,
        targets=targets,
    )
    print(f"PANTS-CACHE-GRAPH command={json.dumps(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode == 0:
        return "owned", completed.stdout
    normalized = output.lower()
    if "ambigu" in normalized:
        return "ambiguous", completed.stdout
    if "unowned" in normalized or "no owner" in normalized:
        return "unowned", completed.stdout
    return "error", completed.stdout


def _run_command(command: Sequence[str], *, policy_environment: str) -> CommandResult:
    environment = os.environ.copy()
    environment["GLUDD_PANTS_UNIT_POLICY"] = policy_environment
    started = time.monotonic()
    print(f"PANTS-CACHE-EXEC command={json.dumps(list(command))}", flush=True)
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=environment,
        check=False,
    )
    elapsed = time.monotonic() - started
    print(
        f"PANTS-CACHE-EXEC-DONE returncode={completed.returncode} elapsed_seconds={elapsed:.3f}",
        flush=True,
    )
    return CommandResult(completed.returncode, elapsed)


def _find_one(root: Path, name: str) -> Path:
    matches = sorted(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} below Pants distdir")
    return matches[0]


def _result_manifest(
    distdir: Path,
    *,
    manifest: LaneManifest,
) -> dict[str, object]:
    junit = normalized_junit_manifest(distdir)
    coverage = normalized_coverage_manifest(
        _find_one(distdir, "coverage.json"),
        aggregate_floor=manifest.minimum_aggregate_coverage,
        file_floor=manifest.minimum_file_coverage,
    )
    if not coverage["floors_satisfied"]:
        files = coverage.get("files")
        below = (
            {
                name: percentage
                for name, percentage in files.items()
                if isinstance(name, str)
                and isinstance(percentage, int | float)
                and percentage < manifest.minimum_file_coverage
            }
            if isinstance(files, dict)
            else {}
        )
        raise ValueError(
            "Pants cached slice failed the 85/75 coverage floor: "
            f"aggregate={coverage.get('aggregate_percent')} below_files={below}"
        )
    return {"junit": junit, "coverage": coverage}


def _write_terminal_receipt(
    receipt_root: Path,
    *,
    payload: Mapping[str, object],
) -> Path:
    key_path = Path(
        os.environ.get(
            "GLUDD_GATE_KEY_PATH",
            str(Path.home() / ".config" / "gludd" / "gate-attestation.key"),
        )
    )
    auth = load_gate_receipt_auth_context(
        key_path,
        issued_at=int(time.time()),
        revoked_signers=parse_revoked_signers(
            os.environ.get("GLUDD_RECEIPT_REVOKED_SIGNERS", "")
        ),
    )
    envelope = authenticate_payload(payload, signer=auth.signer)
    candidate = payload["candidate_provenance"]
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate receipt provenance is malformed")
    candidate_sha = candidate["candidate_sha"]
    destination = receipt_root / str(candidate_sha)
    _atomic_write_json(destination / "receipt.json", payload)
    _atomic_write_json(destination / "attestation.json", envelope)
    print(
        f"PANTS-CACHE-RECEIPT candidate_sha={candidate_sha} path={destination} authenticated=true",
        flush=True,
    )
    return destination


def _print_decision(label: str, decision: ExecutionDecision) -> None:
    print(
        f"PANTS-CACHE-DECISION phase={label} force_fresh={str(decision.force_fresh).lower()} "
        f"cross_commit_cache={str(decision.allow_cross_commit_cache).lower()} "
        f"workers={decision.workers} reasons={','.join(decision.reasons) or 'none'}",
        flush=True,
    )


def _validate_control_files(manifest: LaneManifest) -> None:
    _control_input_evidence(manifest, graph_output="validate-only")


def _run_lane(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    _validate_control_files(manifest)
    hosted_ci = bool(args.hosted_ci)
    resources = project_resource_root(ROOT) / "pants-unit-cache"
    cache_root = args.cache_root or resources / "cache"
    receipt_root = args.receipt_root or resources / "receipts"
    run_id = hashlib.sha256(f"{os.getpid()}:{time.time_ns()}:{ROOT}".encode()).hexdigest()[:16]
    run_root = ROOT / ".pants.d" / "gludd-unit-cache" / run_id
    graph_workdir = run_root / "graph"
    if args.validate_only:
        request = ExecutionRequest(
            mode="fresh" if args.mode == "fresh" else "cached",
            hosted_ci=hosted_ci,
            dynamic_inputs=manifest.dynamic_inputs,
            external_inputs=manifest.external_inputs,
        )
        decision = decide_execution(
            request,
            max_local_workers=manifest.max_local_workers,
            hosted_workers=manifest.hosted_workers,
        )
        _print_decision("validate-only", decision)
        command = build_pants_command(
            pants_binary=args.pants_bin,
            decision=decision,
            targets=manifest.targets,
            cache_root=cache_root,
            workdir=run_root / "work",
            distdir=run_root / "dist",
        )
        print(f"PANTS-CACHE-VALIDATE command={json.dumps(command)}", flush=True)
        return 0

    try:
        graph_status, graph_output = _probe_graph(
            pants_binary=args.pants_bin,
            workers=(manifest.hosted_workers if hosted_ci else manifest.max_local_workers),
            cache_root=cache_root,
            workdir=graph_workdir,
            targets=manifest.targets,
        )
        candidate = _candidate_provenance()
        execution = pants_process_identity(
            _control_input_evidence(manifest, graph_output=graph_output)
        )
        phases: list[tuple[str, ExecutionDecision]]
        if args.mode == "nightly":
            phases = [
                (
                    "cached",
                    decide_execution(
                        ExecutionRequest(
                            mode="cached",
                            hosted_ci=hosted_ci,
                            graph_status=graph_status,
                            dynamic_inputs=manifest.dynamic_inputs,
                            external_inputs=manifest.external_inputs,
                        ),
                        max_local_workers=manifest.max_local_workers,
                        hosted_workers=manifest.hosted_workers,
                    ),
                ),
                (
                    "fresh",
                    decide_execution(
                        ExecutionRequest(
                            mode="nightly-fresh",
                            hosted_ci=hosted_ci,
                            graph_status=graph_status,
                            dynamic_inputs=manifest.dynamic_inputs,
                            external_inputs=manifest.external_inputs,
                        ),
                        max_local_workers=manifest.max_local_workers,
                        hosted_workers=manifest.hosted_workers,
                    ),
                ),
            ]
        else:
            phases = [
                (
                    args.mode,
                    decide_execution(
                        ExecutionRequest(
                            mode=args.mode,
                            hosted_ci=hosted_ci,
                            graph_status=graph_status,
                            dynamic_inputs=manifest.dynamic_inputs,
                            external_inputs=manifest.external_inputs,
                        ),
                        max_local_workers=manifest.max_local_workers,
                        hosted_workers=manifest.hosted_workers,
                    ),
                )
            ]
        manifests: dict[str, dict[str, object]] = {}
        elapsed: dict[str, float] = {}
        terminal_rc = 0
        for label, decision in phases:
            _print_decision(label, decision)
            workdir = run_root / label / "work"
            distdir = run_root / label / "dist"
            command = build_pants_command(
                pants_binary=args.pants_bin,
                decision=decision,
                targets=manifest.targets,
                cache_root=cache_root,
                workdir=workdir,
                distdir=distdir,
            )
            result = _run_command(command, policy_environment=args.policy_environment)
            elapsed[label] = round(result.elapsed_seconds, 3)
            if result.returncode:
                terminal_rc = result.returncode
                break
            try:
                manifests[label] = _result_manifest(distdir, manifest=manifest)
            except ValueError as exc:
                print(f"PANTS-CACHE-REPORT-FAIL phase={label} error={exc}", flush=True)
                terminal_rc = 2
                break
        comparison = None
        if terminal_rc == 0 and args.mode == "nightly":
            comparison = compare_manifests(manifests["cached"], manifests["fresh"])
            mismatches = comparison.get("mismatches")
            if not isinstance(mismatches, list) or any(
                not isinstance(item, str) for item in mismatches
            ):
                raise ValueError("nightly comparison diagnostics are malformed")
            print(
                f"PANTS-CACHE-NIGHTLY equivalent={str(comparison['equivalent']).lower()} "
                f"mismatches={','.join(mismatches) or 'none'}",
                flush=True,
            )
            if not comparison["equivalent"]:
                terminal_rc = 1
        final_manifest = manifests.get("fresh") or manifests.get("cached") or {}
        result_evidence = {
            "returncode": terminal_rc,
            "manifest_sha256": canonical_sha256(final_manifest),
            "manifest": final_manifest,
            "elapsed_seconds": elapsed,
            "graph_status": graph_status,
        }
        payload = build_receipt_payload(
            candidate=candidate,
            execution=execution,
            result=result_evidence,
            mode=args.mode,
            comparison=comparison,
        )
        _write_terminal_receipt(receipt_root, payload=payload)
        return terminal_rc
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"PANTS-CACHE-LANE-FAIL error={type(exc).__name__}:{exc}", flush=True)
        return 2
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pants-bin", default="pants")
    parser.add_argument("--mode", choices=("cached", "fresh", "nightly"), default="cached")
    parser.add_argument("--hosted-ci", type=int, choices=(0, 1), default=0)
    parser.add_argument("--validate-only", type=int, choices=(0, 1), default=1)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--receipt-root", type=Path)
    parser.add_argument("--policy-environment", default="v1")
    return parser


def main() -> int:
    """Run or validate the bounded Pants content-addressed unit lane."""
    args = _parser().parse_args()
    args.validate_only = bool(args.validate_only)
    return _run_lane(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
