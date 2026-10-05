#!/usr/bin/env python3
"""Build, validate, or replay the exact non-promoting FreeLLMAPI candidate."""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn, Protocol, cast

import psutil
from scripts.freellmapi_upstream_admission import GitHubCLIClient, UpstreamClient

from general_ludd.models.freellmapi.candidate_bundle import (
    CandidateBundleError,
    FreeLLMAPICandidateBundle,
)
from general_ludd.models.freellmapi.three_arm_contracts import (
    ThreeArmContractError,
    ThreeArmCorpus,
    ThreeArmGroup,
    ThreeArmPlan,
    build_frozen_documents,
    canonical_digest,
    validate_three_arm_documents,
)
from general_ludd.models.freellmapi.three_arm_delta import (
    ReplayObservation,
    ReplayResources,
    ThreeArmDecision,
    ThreeArmDeltaError,
    evaluate_three_arm,
)
from general_ludd.models.freellmapi_frozen_delta_validation import validate_candidate
from general_ludd.models.freellmapi_scoring_contracts import (
    FreeLLMScoringBatchResult,
    FreeLLMScoringFactors,
    FreeLLMScoringInput,
    FreeLLMScoringSource,
)
from general_ludd.models.freellmapi_scoring_kernel import FreeLLMScoringKernel
from general_ludd.models.freellmapi_upstream_source import inspect_upstream_archive

_REPOSITORY = "tashfeenahmed/freellmapi"
_COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
_SOURCE_PATH = "server/src/services/scoring.ts"
_SOURCE_SHA256 = "475557a97ff5a69150a4d30ad194edf05cb45b4d5d3b132d018b55344f3642f1"
_GLOBAL_NAME = "FreeLLMAPICandidateV0111"
_ESBUILD_VERSION = "0.28.1"
_BUILD_TIMEOUT_SECONDS = 120
_MAX_JSON_BYTES = 1024 * 1024
_ADMITTED_BUNDLE = "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"
_CANDIDATE_BUNDLE = "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
_CANDIDATE_ID = (
    "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"
)
_NODE_TIMEOUT_SECONDS = 30
_CANONICAL_BUILD_ARGV = (
    "esbuild",
    "candidate_v0_11_1_entry.ts",
    "--bundle",
    "--format=iife",
    f"--global-name={_GLOBAL_NAME}",
    "--platform=neutral",
    "--target=es2020",
    "--tree-shaking=true",
    "--legal-comments=none",
    "--charset=utf8",
    "--log-level=warning",
)


class _Executor(Protocol):
    def output(self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]) -> str: ...

    def run(self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]) -> int: ...


class _SubprocessExecutor:
    def output(self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]) -> str:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            _fail("bundle_build_failed")
        return result.stdout.strip()

    def run(self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]) -> int:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            check=False,
            capture_output=True,
            timeout=_BUILD_TIMEOUT_SECONDS,
        )
        return result.returncode


class ThreeArmReplayScriptError(RuntimeError):
    """Content-free operator workflow failure."""


def _fail(fault: str) -> NoReturn:
    raise ThreeArmReplayScriptError(fault)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value: object = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("input_invalid")
    if not raw or len(raw) > _MAX_JSON_BYTES or not isinstance(value, dict):
        _fail("input_invalid")
    return cast(dict[str, object], value)


def _clean_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "TZ": "UTC",
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _compile_once(
    *,
    root: Path,
    source: bytes,
    adapter: bytes,
    executor: _Executor,
) -> bytes:
    esbuild = (root / ".opencode/node_modules/.bin/esbuild").resolve()
    if not esbuild.is_file():
        _fail("bundler_unavailable")
    environment = _clean_environment()
    version = executor.output((str(esbuild), "--version"), cwd=root, env=environment)
    if version != _ESBUILD_VERSION:
        _fail("bundler_invalid")
    with tempfile.TemporaryDirectory(prefix="gludd-freellmapi-candidate-bundle-") as directory:
        stage = Path(directory)
        (stage / "scoring.ts").write_bytes(source)
        (stage / "candidate_v0_11_1_entry.ts").write_bytes(adapter)
        output = stage / "candidate_v0_11_1.iife.js"
        argv = (
            str(esbuild),
            "candidate_v0_11_1_entry.ts",
            *_CANONICAL_BUILD_ARGV[2:],
            f"--outfile={output.name}",
        )
        if executor.run(argv, cwd=stage, env=environment) != 0:
            _fail("bundle_build_failed")
        try:
            return b'"use strict";\n' + output.read_bytes()
        except OSError:
            _fail("bundle_build_failed")


def refresh_candidate_bundle(
    *,
    repository_root: Path,
    candidate_path: Path,
    client: UpstreamClient | None = None,
    executor: _Executor | None = None,
) -> dict[str, object]:
    """Rebuild the IIFE twice from the verified archive and publish exact bytes."""
    candidate = _load_object(candidate_path)
    try:
        candidate_id, symbols = validate_candidate(candidate)
    except Exception:
        _fail("candidate_invalid")
    upstream = candidate.get("upstream")
    archive_record = candidate.get("archive")
    sources = candidate.get("sources")
    if not all(isinstance(value, Mapping) for value in (upstream, archive_record, sources)):
        _fail("candidate_invalid")
    upstream_map = cast(Mapping[str, object], upstream)
    archive_map = cast(Mapping[str, object], archive_record)
    sources_map = cast(Mapping[str, object], sources)
    scoring = sources_map.get("scoring")
    if not isinstance(scoring, Mapping):
        _fail("candidate_invalid")
    if (
        upstream_map.get("repository") != _REPOSITORY
        or upstream_map.get("commit") != _COMMIT
        or upstream_map.get("tag") != "v0.11.1"
        or scoring.get("path") != _SOURCE_PATH
        or scoring.get("sha256") != _SOURCE_SHA256
        or "expectedReliability" not in symbols
    ):
        _fail("candidate_invalid")
    transport = client or GitHubCLIClient()
    archive = transport.get_bytes(f"repos/{_REPOSITORY}/tarball/{_COMMIT}")
    if (
        _sha256(archive) != archive_map.get("sha256")
        or len(archive) != archive_map.get("size_bytes")
    ):
        _fail("archive_invalid")
    try:
        evidence = inspect_upstream_archive(archive, max_archive_bytes=64 * 1024 * 1024)
    except Exception:
        _fail("archive_invalid")
    source = evidence.scoring_bytes
    if _sha256(source) != _SOURCE_SHA256:
        _fail("source_invalid")
    adapter_path = (
        repository_root
        / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1_entry.ts"
    )
    try:
        adapter = adapter_path.read_bytes()
    except OSError:
        _fail("adapter_invalid")
    build_executor = executor or _SubprocessExecutor()
    first = _compile_once(
        root=repository_root,
        source=source,
        adapter=adapter,
        executor=build_executor,
    )
    second = _compile_once(
        root=repository_root,
        source=source,
        adapter=adapter,
        executor=build_executor,
    )
    if not first or first != second:
        _fail("bundle_nondeterministic")
    bundle_path = (
        repository_root
        / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.iife.js"
    )
    manifest_path = (
        repository_root
        / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.json"
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "upstream_repository": _REPOSITORY,
        "upstream_tag": "v0.11.1",
        "upstream_commit": _COMMIT,
        "source_path": _SOURCE_PATH,
        "source_sha256": _SOURCE_SHA256,
        "adapter_sha256": _sha256(adapter),
        "bundle_sha256": _sha256(first),
        "format": "iife",
        "global_name": _GLOBAL_NAME,
        "selected_export": "expectedReliability",
        "invocation": "__gludd_freellmapi_candidate_batch",
        "builder": {
            "name": "esbuild",
            "version": _ESBUILD_VERSION,
            "argv": list(_CANONICAL_BUILD_ARGV),
        },
        "host_capabilities": [],
        "runtime_admitted": False,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_write(bundle_path, first)
    _atomic_write(manifest_path, manifest_bytes)
    return {
        "bundle_sha256": manifest["bundle_sha256"],
        "candidate_id": candidate_id,
        "mode": "refresh-bundle",
        "runtime_admitted": False,
    }


def refresh_frozen_documents(*, plan_path: Path, corpus_path: Path) -> dict[str, object]:
    """Write the deterministic preregistered plan and corpus artifacts."""
    plan, corpus = build_frozen_documents(
        candidate_id=_CANDIDATE_ID,
        candidate_bundle_sha256=_CANDIDATE_BUNDLE,
        admitted_bundle_sha256=_ADMITTED_BUNDLE,
    )
    _atomic_write(
        plan_path,
        json.dumps(plan, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    _atomic_write(
        corpus_path,
        json.dumps(corpus, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    return {
        "corpus_sha256": canonical_digest(corpus),
        "exclusion_count": 8,
        "group_count": 32,
        "mode": "refresh-config",
        "runtime_admitted": False,
    }


def _validate_replay_inputs(
    *,
    candidate_path: Path,
    plan_path: Path,
    corpus_path: Path,
) -> tuple[ThreeArmPlan, ThreeArmCorpus, dict[str, object], FreeLLMAPICandidateBundle]:
    candidate = _load_object(candidate_path)
    try:
        candidate_id, _ = validate_candidate(candidate)
    except Exception:
        _fail("candidate_invalid")
    if candidate_id != _CANDIDATE_ID:
        _fail("candidate_invalid")
    plan_document = _load_object(plan_path)
    corpus_document = _load_object(corpus_path)
    try:
        plan, corpus = validate_three_arm_documents(plan_document, corpus_document)
    except ThreeArmContractError as exc:
        _fail(exc.fault.value)
    if plan.candidate_id != candidate_id:
        _fail("candidate_invalid")
    try:
        runner = FreeLLMAPICandidateBundle(candidate_lock=candidate)
    except CandidateBundleError as exc:
        _fail(exc.fault.value)
    return plan, corpus, candidate, runner


def _scoring_input(group: ThreeArmGroup) -> FreeLLMScoringInput:
    return FreeLLMScoringInput(
        candidate_identity_digest=group.candidate_identity_digest,
        successes=group.successes,
        failures=group.failures,
        community_successes=group.community_successes,
        community_failures=group.community_failures,
        tokens_per_second=60.0,
        ttfb_ms=250.0,
        used_tokens=10.0,
        budget_tokens=100.0,
        rate_window_used_fraction=0.1,
        rate_limit_penalty=0.0,
    )


def _native_factor(value: FreeLLMScoringInput) -> FreeLLMScoringFactors:
    alpha = value.successes + 1.0
    beta = value.failures + 1.0
    return FreeLLMScoringFactors(
        candidate_identity_digest=value.candidate_identity_digest,
        reliability_alpha=alpha,
        reliability_beta=beta,
        expected_reliability=alpha / (alpha + beta),
        speed=0.5,
        headroom=0.9,
        rate_window_headroom=0.9,
        rate_limit=1.0,
    )


def _node_crosscheck(
    *,
    repository_root: Path,
    inputs: tuple[FreeLLMScoringInput, ...],
) -> tuple[float, ...]:
    environment = _clean_environment()
    node = shutil.which("node", path=environment["PATH"])
    if node is None:
        _fail("node_unavailable")
    payload = json.dumps(
        [
            {
                "successes": value.successes,
                "failures": value.failures,
                "community_successes": value.community_successes,
                "community_failures": value.community_failures,
            }
            for value in inputs
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    helper = repository_root / "scripts/freellmapi_three_arm_node.mjs"
    bundle = (
        repository_root
        / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.iife.js"
    )
    try:
        result = subprocess.run(
            (node, "--no-warnings", str(helper), str(bundle)),
            input=payload,
            cwd=repository_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=_NODE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        _fail("node_failure")
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > _MAX_JSON_BYTES:
        _fail("node_failure")
    try:
        decoded: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        _fail("node_failure")
    if not isinstance(decoded, list) or len(decoded) != len(inputs):
        _fail("node_failure")
    values: list[float] = []
    for item in decoded:
        if isinstance(item, bool) or not isinstance(item, int | float):
            _fail("node_failure")
        number = float(item)
        if not 0.0 <= number <= 1.0:
            _fail("node_failure")
        values.append(number)
    return tuple(values)


def _rss_mib() -> float:
    return psutil.Process().memory_info().rss / (1024.0 * 1024.0)


def _require_admitted_result(result: FreeLLMScoringBatchResult) -> float:
    if (
        result.source is not FreeLLMScoringSource.FREELLMAPI_SHADOW
        or result.fault is not None
        or len(result.factors) != 1
    ):
        _fail("admitted_bridge_fault")
    return result.factors[0].expected_reliability


def _decision_report(
    *,
    plan: ThreeArmPlan,
    corpus: ThreeArmCorpus,
    decision: ThreeArmDecision,
    resources: ReplayResources,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "gate": plan.gate,
        "plan_id": plan.plan_id,
        "corpus_id": corpus.corpus_id,
        "candidate_id": plan.candidate_id,
        "candidate_bundle_sha256": plan.candidate_bundle_sha256,
        "admitted_bundle_sha256": plan.admitted_bundle_sha256,
        "group_count": len(corpus.groups),
        "exclusion_count": len(corpus.exclusions),
        "decision": decision.decision,
        "all_gates_passed": decision.all_gates_passed,
        "promotion_permitted": decision.promotion_permitted,
        "runtime_admitted": decision.runtime_admitted,
        "serving_bundle_sha256": decision.serving_bundle_sha256,
        "comparisons": {
            arm: asdict(comparison)
            for arm, comparison in decision.comparisons.items()
        },
        "failed_gates": list(decision.failed_gates),
        "candidate_max_latency_ms": decision.candidate_max_latency_ms,
        "rss_delta_mib": decision.rss_delta_mib,
        "rss_samples_mib": list(resources.rss_samples_mib),
        "bridge_fault_count": decision.bridge_fault_count,
        "node_crosscheck_passed": "node_crosscheck" not in decision.failed_gates,
        "network_calls": resources.network_calls,
        "cost_usd": resources.cost_usd,
    }
    report["evidence_id"] = f"sha256:{canonical_digest(report)}"
    return report


def run_three_arm_replay(
    *,
    repository_root: Path,
    plan: ThreeArmPlan,
    corpus: ThreeArmCorpus,
    candidate_runner: FreeLLMAPICandidateBundle,
) -> dict[str, object]:
    """Execute native, admitted, and exact candidate arms without promotion."""
    inputs = tuple(_scoring_input(group) for group in corpus.groups)
    fallbacks = tuple(_native_factor(value) for value in inputs)
    admitted = FreeLLMScoringKernel(enabled=True)
    try:
        warm_candidate = candidate_runner.score_batch(inputs)
        warm_admitted = admitted.factor_batch(inputs, fallback=fallbacks)
    except CandidateBundleError as exc:
        _fail(exc.fault.value)
    if (
        len(warm_candidate.probabilities) != len(inputs)
        or warm_admitted.source is not FreeLLMScoringSource.FREELLMAPI_SHADOW
        or warm_admitted.fault is not None
    ):
        _fail("bridge_fault")
    node_probabilities = _node_crosscheck(
        repository_root=repository_root,
        inputs=inputs,
    )
    for index in range(4):
        try:
            candidate_runner.score_batch((inputs[index],))
        except CandidateBundleError as exc:
            _fail(exc.fault.value)
        _require_admitted_result(
            admitted.factor_batch((inputs[index],), fallback=(fallbacks[index],))
        )
    native_probabilities = [0.0] * len(inputs)
    admitted_probabilities = [0.0] * len(inputs)
    candidate_probabilities = [0.0] * len(inputs)
    native_latencies = [0.0] * len(inputs)
    admitted_latencies = [0.0] * len(inputs)
    candidate_latencies = [0.0] * len(inputs)
    gc.collect()
    rss_samples = [_rss_mib()]
    for index, (scoring_input, fallback) in enumerate(
        zip(inputs, fallbacks, strict=True)
    ):
        started = time.perf_counter_ns()
        native_probabilities[index] = _native_factor(scoring_input).expected_reliability
        native_latencies[index] = (time.perf_counter_ns() - started) / 1_000_000.0

        started = time.perf_counter_ns()
        admitted_probabilities[index] = _require_admitted_result(
            admitted.factor_batch((scoring_input,), fallback=(fallback,))
        )
        admitted_latencies[index] = (time.perf_counter_ns() - started) / 1_000_000.0

        started = time.perf_counter_ns()
        try:
            candidate = candidate_runner.score_batch((scoring_input,))
        except CandidateBundleError as exc:
            _fail(exc.fault.value)
        candidate_latencies[index] = (time.perf_counter_ns() - started) / 1_000_000.0
        candidate_probabilities[index] = candidate.probabilities[0]
        if (index + 1) % 8 == 0:
            gc.collect()
            rss_samples.append(_rss_mib())
    observations = tuple(
        ReplayObservation(
            group_digest=group.group_digest,
            truth=group.truth,
            probabilities={
                "gludd_native": native_probabilities[index],
                "freellmapi_v0_9_9": admitted_probabilities[index],
                "freellmapi_v0_11_1": candidate_probabilities[index],
            },
            latency_ms={
                "gludd_native": native_latencies[index],
                "freellmapi_v0_9_9": admitted_latencies[index],
                "freellmapi_v0_11_1": candidate_latencies[index],
            },
            node_candidate_probability=node_probabilities[index],
            bridge_fault=None,
        )
        for index, group in enumerate(corpus.groups)
    )
    resources = ReplayResources(
        rss_samples_mib=tuple(rss_samples),
        network_calls=0,
        cost_usd=0.0,
    )
    try:
        decision = evaluate_three_arm(plan, corpus, observations, resources)
    except ThreeArmDeltaError as exc:
        _fail(exc.fault.value)
    return _decision_report(
        plan=plan,
        corpus=corpus,
        decision=decision,
        resources=resources,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("validate", "replay", "refresh-bundle", "refresh-config"),
        required=True,
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit three-arm operator mode."""
    args = _parser().parse_args(argv)
    root = args.repository_root.resolve()
    candidate = args.candidate if args.candidate.is_absolute() else root / args.candidate
    plan_path = args.plan if args.plan.is_absolute() else root / args.plan
    corpus_path = args.corpus if args.corpus.is_absolute() else root / args.corpus
    report_path = args.report if args.report.is_absolute() else root / args.report
    try:
        if args.mode == "refresh-bundle":
            result = refresh_candidate_bundle(
                repository_root=root,
                candidate_path=candidate,
            )
        elif args.mode == "refresh-config":
            result = refresh_frozen_documents(
                plan_path=plan_path,
                corpus_path=corpus_path,
            )
        else:
            plan, corpus, _, runner = _validate_replay_inputs(
                candidate_path=candidate,
                plan_path=plan_path,
                corpus_path=corpus_path,
            )
            if args.mode == "validate":
                result = {
                    "candidate_bundle_sha256": plan.candidate_bundle_sha256,
                    "decision": "HOLD",
                    "exclusion_count": len(corpus.exclusions),
                    "group_count": len(corpus.groups),
                    "mode": "validate",
                    "runtime_admitted": False,
                    "serving_bundle_sha256": plan.active_bundle_sha256,
                }
            else:
                result = run_three_arm_replay(
                    repository_root=root,
                    plan=plan,
                    corpus=corpus,
                    candidate_runner=runner,
                )
                _atomic_write(
                    report_path,
                    json.dumps(result, indent=2, sort_keys=True).encode("utf-8")
                    + b"\n",
                )
                result = {
                    "decision": result["decision"],
                    "evidence_id": result["evidence_id"],
                    "mode": "replay",
                    "runtime_admitted": False,
                    "serving_bundle_sha256": result["serving_bundle_sha256"],
                }
    except ThreeArmReplayScriptError as exc:
        if args.mode == "replay":
            failure = {
                "all_gates_passed": False,
                "decision": "HOLD",
                "fault": str(exc),
                "promotion_permitted": False,
                "runtime_admitted": False,
                "serving_bundle_sha256": _ADMITTED_BUNDLE,
            }
            with contextlib.suppress(OSError):
                _atomic_write(
                    report_path,
                    json.dumps(failure, indent=2, sort_keys=True).encode("utf-8")
                    + b"\n",
                )
        print(json.dumps({"fault": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    except Exception:
        fault = "replay_failure"
        if args.mode == "replay":
            failure = {
                "all_gates_passed": False,
                "decision": "HOLD",
                "fault": fault,
                "promotion_permitted": False,
                "runtime_admitted": False,
                "serving_bundle_sha256": _ADMITTED_BUNDLE,
            }
            with contextlib.suppress(OSError):
                _atomic_write(
                    report_path,
                    json.dumps(failure, indent=2, sort_keys=True).encode("utf-8")
                    + b"\n",
                )
        print(json.dumps({"fault": fault, "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
