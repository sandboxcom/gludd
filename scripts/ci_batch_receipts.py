#!/usr/bin/env python3
"""Write and validate exact-gate pass and non-reusable failure receipts.

This module implements shadow writers only.  It never selects a test, restores
a coverage fragment, or skips execution.  Failure receipts are sanitized
diagnostics and are explicitly ineligible for reuse.  Receipt admission is a
separate rollout phase.
"""

from __future__ import annotations

import configparser
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.ci_receipt_auth import ReceiptSigner, create_receipt_envelope
else:
    from ci_receipt_auth import ReceiptSigner, create_receipt_envelope

from coverage import CoverageData
from coverage.exceptions import CoverageException
from defusedxml import ElementTree

RECEIPT_SCHEMA_VERSION = 1
MAX_RECEIPT_GENERATIONS = 2
MAX_RECEIPT_BYTES = 2 * 1024**3
MAX_JSON_BYTES = 64 * 1024**2
MAX_JUNIT_BYTES = 64 * 1024**2
MAX_SEMANTIC_COVERAGE_ITEMS = 2_000_000
MAX_FAILURE_NODES = 32
MAX_FAILURE_RECEIPTS_PER_GENERATION = 256
MAX_FAILURE_ELAPSED_SECONDS = 24 * 60 * 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SHARD = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:secret|password|credential|api_key|access_key|token)(?:$|_)",
    re.IGNORECASE,
)
_RECEIPT_ENV_ALLOWLIST = ("CI", "GITHUB_ACTIONS", "PYTHONHASHSEED", "TZ")
_RESTRICTED_ENV_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|ACCESS_KEY)(?:$|_)",
    re.IGNORECASE,
)
_RECEIPT_FILES = frozenset(
    {"complete.json", "coverage.data", "manifest.json", "outcomes.json"}
)
_FAILURE_RECEIPT_FILES = frozenset(
    {"complete.json", "failure.json", "manifest.json"}
)
_AUTH_RECEIPT_FILE = "attestation.json"
_FAILURE_CLASSES = frozenset(
    {
        "interrupted",
        "no-tests-collected",
        "pytest-internal-error",
        "pytest-usage-error",
        "runner-nonzero",
        "test-failure",
    }
)


@dataclass(frozen=True)
class BatchReceiptRequest:
    """Complete evidence offered to the shadow writer after owned cleanup."""

    action_identity: Mapping[str, object]
    observed_action_identity: Mapping[str, object]
    coverage_path: Path
    outcome_manifest: Mapping[str, object] | None
    originating_run_id: str
    started_at: str
    completed_at: str
    returncode: int
    cleanup_returncode: int


@dataclass(frozen=True)
class FailureReceiptRequest:
    """Sanitized evidence offered after one nonzero executed batch."""

    action_identity: Mapping[str, object]
    observed_action_identity: Mapping[str, object]
    failure_node_metadata: Mapping[str, object] | None
    originating_run_id: str
    elapsed_seconds: float
    returncode: int
    cleanup_returncode: int


@dataclass(frozen=True)
class ReceiptPublication:
    """Content-free result of one shadow publication attempt."""

    published: bool
    reason: str
    path: Path | None = None
    action_digest: str | None = None


@dataclass(frozen=True)
class ReceiptValidation:
    """Content-free result of validating one receipt directory."""

    valid: bool
    reason: str
    action_digest: str | None = None


def canonical_json_bytes(payload: object) -> bytes:
    """Encode JSON evidence deterministically and reject non-finite values."""
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(payload: object) -> str:
    """Return the SHA-256 of the canonical JSON representation."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash one regular evidence file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def coverage_data_error(path: Path, *, require_branch: bool = False) -> str | None:
    """Return why a Coverage.py database is unsafe, or ``None`` when valid."""
    if path.is_symlink():
        return "symbolic links are not accepted"
    try:
        metadata = path.stat()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if not path.is_file() or metadata.st_size == 0:
        return "file is missing or empty"
    try:
        before = file_sha256(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"

    data = CoverageData(basename=str(path))
    try:
        data.read()
        if not data.measured_files():
            return "coverage contains no measured files"
        if require_branch and not data.has_arcs():
            return "branch coverage is required"
    except (CoverageException, OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        data.close()
    try:
        after = file_sha256(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if after != before:
        return "validation mutated the coverage database"
    return None


def coverage_semantic_sha256(path: Path) -> tuple[str | None, str | None]:
    """Hash bounded branch-coverage meaning, independent of SQLite bytes."""
    basic_error = coverage_data_error(path, require_branch=True)
    if basic_error is not None:
        return None, basic_error
    try:
        before = file_sha256(path)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"

    digest = hashlib.sha256()
    digest.update(canonical_json_bytes({"schema_version": 1}) + b"\n")
    data = CoverageData(basename=str(path))
    try:
        data.read()
        measured_files = sorted(data.measured_files())
        item_count = len(measured_files)
        if item_count > MAX_SEMANTIC_COVERAGE_ITEMS:
            return None, "semantic coverage exceeds audit bound"
        for filename in measured_files:
            arcs = sorted(data.arcs(filename) or ())
            item_count += len(arcs)
            if item_count > MAX_SEMANTIC_COVERAGE_ITEMS:
                return None, "semantic coverage exceeds audit bound"
            digest.update(
                canonical_json_bytes(
                    {
                        "arcs": arcs,
                        "file": filename,
                        "file_tracer": data.file_tracer(filename),
                    }
                )
                + b"\n"
            )
    except (CoverageException, OSError, TypeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        data.close()
    try:
        after = file_sha256(path)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if after != before:
        return None, "validation mutated the coverage database"
    return digest.hexdigest(), None


def _xml_tag(element: Any) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _junit_root(path: Path) -> Any:
    """Return one size-bounded defused JUnit root element."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("JUnit evidence must be one regular file")
    size = path.stat().st_size
    if size == 0 or size > MAX_JUNIT_BYTES:
        raise ValueError("JUnit evidence size is outside the supported bound")
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        raise ValueError(f"invalid JUnit evidence: {type(exc).__name__}") from exc
    if root is None or _xml_tag(root) not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit evidence has an unsupported root")
    return root


def normalize_junit_outcomes(path: Path) -> dict[str, object]:
    """Return a bounded content-free terminal-outcome manifest from JUnit XML."""
    root = _junit_root(path)

    outcomes: list[dict[str, str]] = []
    counts = {"errors": 0, "failures": 0, "passed": 0, "skipped": 0, "tests": 0}
    seen: set[str] = set()
    for case in root.iter():
        if _xml_tag(case) != "testcase":
            continue
        name = case.attrib.get("name", "")
        classname = case.attrib.get("classname", "")
        source_file = case.attrib.get("file", "")
        if not name or len(name) > 4096 or len(classname) > 4096 or len(source_file) > 4096:
            raise ValueError("JUnit testcase identity is missing or oversized")
        node_digest = canonical_json_sha256(
            {"classname": classname, "file": source_file, "name": name}
        )
        if node_digest in seen:
            raise ValueError("JUnit evidence contains a duplicate testcase identity")
        seen.add(node_digest)
        child_tags = {_xml_tag(child) for child in case}
        if len(child_tags.intersection({"error", "failure", "skipped"})) > 1:
            raise ValueError("JUnit testcase has ambiguous terminal outcomes")
        if "error" in child_tags:
            outcome = "error"
            counts["errors"] += 1
        elif "failure" in child_tags:
            outcome = "failure"
            counts["failures"] += 1
        elif "skipped" in child_tags:
            outcome = "skipped"
            counts["skipped"] += 1
        else:
            outcome = "passed"
            counts["passed"] += 1
        counts["tests"] += 1
        outcomes.append({"node_sha256": node_digest, "outcome": outcome})
    if not outcomes:
        raise ValueError("JUnit evidence contains no terminal testcases")
    return {
        "schema_version": 1,
        "counts": counts,
        "node_id_sha256": canonical_json_sha256(sorted(seen)),
        "terminal_outcome_sha256": canonical_json_sha256(outcomes),
    }


def normalize_junit_failure_metadata(path: Path) -> dict[str, object]:
    """Return bounded hashes for failed/error nodes without retaining payloads."""
    root = _junit_root(path)
    nodes: list[dict[str, str]] = []
    failing_count = 0
    seen: set[str] = set()
    digest = hashlib.sha256()
    for case in root.iter():
        if _xml_tag(case) != "testcase":
            continue
        name = case.attrib.get("name", "")
        classname = case.attrib.get("classname", "")
        source_file = case.attrib.get("file", "")
        if not name or len(name) > 4096 or len(classname) > 4096 or len(source_file) > 4096:
            raise ValueError("JUnit testcase identity is missing or oversized")
        node_digest = canonical_json_sha256(
            {"classname": classname, "file": source_file, "name": name}
        )
        if node_digest in seen:
            raise ValueError("JUnit evidence contains a duplicate testcase identity")
        seen.add(node_digest)
        child_tags = {_xml_tag(child) for child in case}
        if len(child_tags.intersection({"error", "failure", "skipped"})) > 1:
            raise ValueError("JUnit testcase has ambiguous terminal outcomes")
        outcome = (
            "error"
            if "error" in child_tags
            else "failure"
            if "failure" in child_tags
            else None
        )
        if outcome is None:
            continue
        entry = {"node_sha256": node_digest, "outcome": outcome}
        digest.update(canonical_json_bytes(entry) + b"\n")
        failing_count += 1
        if len(nodes) < MAX_FAILURE_NODES:
            nodes.append(entry)
    return {
        "schema_version": 1,
        "status": "available",
        "failing_node_count": failing_count,
        "failing_nodes_sha256": digest.hexdigest(),
        "nodes": nodes,
        "nodes_truncated": failing_count > len(nodes),
    }


def _contains_sensitive_key(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if not isinstance(key, str) or _SENSITIVE_KEY.search(key):
                return True
            if _contains_sensitive_key(value):
                return True
    elif isinstance(payload, (list, tuple)):
        return any(_contains_sensitive_key(value) for value in payload)
    return False


def outcome_manifest_error(payload: Mapping[str, object] | None) -> str | None:
    if payload is None or set(payload) != {
        "schema_version",
        "counts",
        "node_id_sha256",
        "terminal_outcome_sha256",
    }:
        return "schema"
    if payload.get("schema_version") != 1:
        return "schema"
    counts = payload.get("counts")
    if not isinstance(counts, Mapping) or set(counts) != {
        "errors",
        "failures",
        "passed",
        "skipped",
        "tests",
    }:
        return "counts"
    if any(type(counts[name]) is not int or counts[name] < 0 for name in counts):
        return "counts"
    if counts["tests"] != sum(
        counts[name] for name in ("errors", "failures", "passed", "skipped")
    ):
        return "counts"
    if counts["tests"] < 1 or counts["errors"] or counts["failures"]:
        return "terminal-outcome"
    for name in ("node_id_sha256", "terminal_outcome_sha256"):
        if not isinstance(payload.get(name), str) or not _SHA256.fullmatch(
            str(payload[name])
        ):
            return "digest"
    return None


def failure_node_metadata_error(
    payload: Mapping[str, object] | None,
) -> str | None:
    """Return why bounded failing-node metadata is unsafe."""
    if payload is None or set(payload) != {
        "failing_node_count",
        "failing_nodes_sha256",
        "nodes",
        "nodes_truncated",
        "schema_version",
        "status",
    }:
        return "schema"
    if payload.get("schema_version") != 1 or payload.get("status") not in {
        "available",
        "unavailable",
    }:
        return "schema"
    failing_count = payload.get("failing_node_count")
    nodes = payload.get("nodes")
    truncated = payload.get("nodes_truncated")
    digest = payload.get("failing_nodes_sha256")
    if (
        type(failing_count) is not int
        or failing_count < 0
        or not isinstance(nodes, list)
        or len(nodes) > MAX_FAILURE_NODES
        or type(truncated) is not bool
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        return "bounds"
    if len(nodes) != min(failing_count, MAX_FAILURE_NODES):
        return "bounds"
    if truncated is not (failing_count > MAX_FAILURE_NODES):
        return "bounds"
    if payload.get("status") == "unavailable" and (
        failing_count != 0
        or nodes
        or truncated
        or digest != hashlib.sha256().hexdigest()
    ):
        return "unavailable"
    for node in nodes:
        if (
            not isinstance(node, Mapping)
            or set(node) != {"node_sha256", "outcome"}
            or not isinstance(node.get("node_sha256"), str)
            or not _SHA256.fullmatch(str(node["node_sha256"]))
            or node.get("outcome") not in {"error", "failure"}
        ):
            return "node"
    return None


def failure_class_for_returncode(returncode: int) -> str:
    """Map one nonzero runner result to a stable, payload-free class."""
    if returncode in {2, 130, 143}:
        return "interrupted"
    return {
        1: "test-failure",
        3: "pytest-internal-error",
        4: "pytest-usage-error",
        5: "no-tests-collected",
    }.get(returncode, "runner-nonzero")


def _unavailable_failure_nodes() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "failing_node_count": 0,
        "failing_nodes_sha256": hashlib.sha256().hexdigest(),
        "nodes": [],
        "nodes_truncated": False,
    }


def _failure_batch_identity(
    action_identity: Mapping[str, object],
) -> dict[str, object]:
    if action_identity_error(action_identity) is not None:
        raise ValueError("action identity is ineligible")
    source = action_identity.get("source")
    plan = action_identity.get("plan")
    assert isinstance(source, Mapping)
    assert isinstance(plan, Mapping)
    shard = plan.get("shard")
    batch_index = plan.get("batch_index")
    test_files_sha256 = source.get("test_files_sha256")
    collection_manifest_sha256 = plan.get("collection_manifest_sha256")
    if (
        not isinstance(shard, str)
        or not _SHARD.fullmatch(shard)
        or type(batch_index) is not int
        or batch_index < 1
        or not isinstance(test_files_sha256, str)
        or not _SHA256.fullmatch(test_files_sha256)
        or not isinstance(collection_manifest_sha256, str)
        or not _SHA256.fullmatch(collection_manifest_sha256)
    ):
        raise ValueError("batch identity is invalid")
    return {
        "action_digest": canonical_json_sha256(action_identity),
        "batch_index": batch_index,
        "candidate_sha": source["candidate_sha"],
        "collection_manifest_sha256": collection_manifest_sha256,
        "shard": shard,
        "test_files_sha256": test_files_sha256,
    }


def _failure_receipt_digest(
    batch_identity: Mapping[str, object],
    failure: Mapping[str, object],
    originating_run_id_sha256: str,
) -> str:
    return canonical_json_sha256(
        {
            "batch_identity": batch_identity,
            "failure": failure,
            "originating_run_id_sha256": originating_run_id_sha256,
        }
    )


def private_path_error(path: Path, *, directory: bool) -> str | None:
    if path.is_symlink():
        return "symlink"
    try:
        metadata = path.stat()
    except OSError as exc:
        return type(exc).__name__
    if directory and not stat.S_ISDIR(metadata.st_mode):
        return "not-directory"
    if not directory and not stat.S_ISREG(metadata.st_mode):
        return "not-regular"
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        return "foreign-owner"
    allowed = 0o700 if directory else 0o600
    if stat.S_IMODE(metadata.st_mode) & ~allowed:
        return "unsafe-mode"
    return None


def _reject_duplicate_json_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON key: {key}")
        payload[key] = value
    return payload


def read_strict_json(path: Path) -> object:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON artifact exceeds the supported bound")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_reject_duplicate_json_pairs)


# Compatibility aliases for the phase-one tests and callers that predate the
# public replay-audit helpers.
_outcome_manifest_error = outcome_manifest_error
_private_path_error = private_path_error
_read_json = read_strict_json


def _write_json(path: Path, payload: object) -> None:
    encoded = canonical_json_bytes(payload) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _copy_private(source: Path, destination: Path) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_tree_size(path: Path) -> tuple[int, str | None]:
    total = 0
    try:
        for candidate in path.rglob("*"):
            if candidate.is_symlink():
                return 0, "symlink"
            metadata = candidate.stat()
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
            elif not stat.S_ISDIR(metadata.st_mode):
                return 0, "special-file"
    except OSError as exc:
        return 0, type(exc).__name__
    return total, None


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "absent"


def _installed_distributions_sha256() -> str:
    distributions: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        if not name.strip():
            raise RuntimeError("installed distribution is missing its canonical name")
        distributions.append(
            {
                "name": name.strip().lower().replace("_", "-"),
                "version": distribution.version,
            }
        )
    return canonical_json_sha256(
        sorted(distributions, key=lambda item: (item["name"], item["version"]))
    )


def _pytest_plugin_identity() -> dict[str, object]:
    entry_points = importlib.metadata.entry_points()
    pytest_entries = (
        entry_points.select(group="pytest11")
        if hasattr(entry_points, "select")
        else entry_points.get("pytest11", ())
    )
    plugins: list[dict[str, str]] = []
    for entry_point in pytest_entries:
        distribution = getattr(entry_point, "dist", None)
        distribution_name = "unknown"
        distribution_version = "unknown"
        if distribution is not None:
            candidate_name = distribution.metadata["Name"]
            if candidate_name:
                distribution_name = candidate_name
            distribution_version = distribution.version
        plugins.append(
            {
                "distribution": distribution_name,
                "name": entry_point.name,
                "value": entry_point.value,
                "version": distribution_version,
            }
        )
    inventory = sorted(
        plugins,
        key=lambda item: (
            item["distribution"],
            item["name"],
            item["value"],
            item["version"],
        ),
    )
    return {
        "inventory_kind": "installed-pytest11-v1",
        "inventory_sha256": canonical_json_sha256(inventory),
        "pytest": _distribution_version("pytest"),
        "pytest_cov": _distribution_version("pytest-cov"),
        "coverage": _distribution_version("coverage"),
        "pytest_timeout": _distribution_version("pytest-timeout"),
        "pytest_asyncio": _distribution_version("pytest-asyncio"),
        "pytest_xdist": _distribution_version("pytest-xdist"),
    }


def _fingerprinted_files(paths: Mapping[str, Path]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for name, path in sorted(paths.items()):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"receipt identity input is unavailable: {name}")
        evidence[name] = file_sha256(path)
    return evidence


def _uv_version(root: Path) -> str:
    completed = subprocess.run(
        ["uv", "--version"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version or len(version) > 128:
        raise RuntimeError("uv version probe failed")
    return version


def _coverage_identity(path: Path) -> dict[str, object]:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path, encoding="utf-8"):
        raise RuntimeError("coverage configuration is unavailable")
    if not parser.has_section("run"):
        raise RuntimeError("coverage configuration has no run section")

    def split(value: str) -> list[str]:
        return [part.strip() for part in value.splitlines() if part.strip()]

    paths = {
        section: [value.strip() for _name, value in parser.items(section) if value.strip()]
        for section in parser.sections()
        if section == "paths" or section.startswith("paths.")
    }
    return {
        "config_sha256": file_sha256(path),
        "branch": parser.getboolean("run", "branch", fallback=False),
        "source": split(parser.get("run", "source", fallback="")),
        "omit": split(parser.get("run", "omit", fallback="")),
        "paths": paths,
        "concurrency": split(parser.get("run", "concurrency", fallback="")),
        "data_schema": f"coverage.py-{_distribution_version('coverage').split('.', maxsplit=1)[0]}",
    }


def _environment_identity(environment: Mapping[str, str]) -> dict[str, object]:
    allowlist: dict[str, str | None] = {}
    for name in _RECEIPT_ENV_ALLOWLIST:
        value = environment.get(name)
        if value is not None and len(value) > 4096:
            raise RuntimeError(f"receipt environment value is oversized: {name}")
        allowlist[name] = value
    undeclared_names = sorted(set(environment).difference(_RECEIPT_ENV_ALLOWLIST))
    return {
        "allowlist": allowlist,
        "undeclared_names_sha256": canonical_json_sha256(undeclared_names),
        "restricted_inputs_present": any(
            _RESTRICTED_ENV_NAME.search(name) for name in undeclared_names
        ),
        "values_outside_allowlist_stored": False,
    }


def build_batch_runtime_identity(
    *,
    root: Path,
    scripts: Path,
    runner: Path,
    coverage_config: Path,
    interpreter: Mapping[str, str],
    include_uv_probe: bool,
    environment: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    """Build the non-plan families of one exact batch action identity."""
    implementation = _fingerprinted_files(
        {
            "batch_receipts": scripts / "ci_batch_receipts.py",
            "batch_replay_audit": scripts / "ci_batch_replay_audit.py",
            "gate_progress": scripts / "ci_gate_progress.py",
            "serial_runner": runner,
        }
    )
    attestation = _fingerprinted_files(
        {"gate_status_attestation": scripts / "gate_status_attestation.py"}
    )
    dependency_files = _fingerprinted_files(
        {
            "dependency_profiles": scripts / "dependency_profiles.py",
            "pyproject": root / "pyproject.toml",
            "uv_lock": root / "uv.lock",
        }
    )
    executable = Path(interpreter["executable"])
    return {
        "runner": {
            "receipt_schema": RECEIPT_SCHEMA_VERSION,
            "implementation_sha256": canonical_json_sha256(implementation),
            "attestation_sha256": canonical_json_sha256(attestation),
        },
        "toolchain": {
            "implementation": interpreter["implementation"],
            "version": interpreter["version"],
            "executable": interpreter["executable"],
            "executable_sha256": file_sha256(executable),
            "uv_version": _uv_version(root) if include_uv_probe else None,
            "lockfile_sha256": dependency_files["uv_lock"],
            "dependency_profile_sha256": canonical_json_sha256(dependency_files),
            "installed_distributions_sha256": _installed_distributions_sha256(),
        },
        "plugins": _pytest_plugin_identity(),
        "coverage": _coverage_identity(coverage_config),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_abi": sys.implementation.cache_tag or "unknown",
            "resource_namespace_schema": 1,
        },
        "environment": _environment_identity(environment),
    }


def validate_batch_receipt(
    path: Path,
    *,
    expected_action_identity: Mapping[str, object],
    require_content_addressed_name: bool = True,
) -> ReceiptValidation:
    """Validate one candidate receipt without admitting it into a test run."""
    directory_error = private_path_error(path, directory=True)
    if directory_error is not None:
        return ReceiptValidation(False, f"corrupt-directory-{directory_error}")
    try:
        children = {child.name for child in path.iterdir()}
    except OSError as exc:
        return ReceiptValidation(False, f"corrupt-layout-{type(exc).__name__}")
    if children not in {
        _RECEIPT_FILES,
        _RECEIPT_FILES | {_AUTH_RECEIPT_FILE},
    }:
        return ReceiptValidation(False, "corrupt-layout")
    for name in sorted(children):
        artifact_error = private_path_error(path / name, directory=False)
        if artifact_error is not None:
            return ReceiptValidation(False, f"corrupt-{name}-{artifact_error}")

    try:
        manifest = read_strict_json(path / "manifest.json")
        completion = read_strict_json(path / "complete.json")
        outcomes = read_strict_json(path / "outcomes.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return ReceiptValidation(False, f"corrupt-json-{type(exc).__name__}")
    if not isinstance(manifest, dict) or set(manifest) != {
        "action_digest",
        "action_identity",
        "cleanup_returncode",
        "completed_at",
        "coverage",
        "format",
        "originating_run_id",
        "outcomes",
        "returncode",
        "schema_version",
        "started_at",
        "status",
    }:
        return ReceiptValidation(False, "corrupt-manifest-schema")
    action_digest = manifest.get("action_digest")
    if not isinstance(action_digest, str) or not _SHA256.fullmatch(action_digest):
        return ReceiptValidation(False, "corrupt-action-digest")
    expected_digest = canonical_json_sha256(expected_action_identity)
    if action_digest != expected_digest or manifest.get("action_identity") != dict(
        expected_action_identity
    ):
        return ReceiptValidation(False, "corrupt-action-identity", action_digest)
    if require_content_addressed_name and path.name != action_digest:
        return ReceiptValidation(False, "corrupt-content-address", action_digest)
    if (
        manifest.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or manifest.get("format") != "json+coverage-sqlite"
        or manifest.get("status") != "pass"
        or manifest.get("returncode") != 0
        or manifest.get("cleanup_returncode") != 0
        or not isinstance(manifest.get("started_at"), str)
        or not isinstance(manifest.get("completed_at"), str)
        or not isinstance(manifest.get("originating_run_id"), str)
        or not _RUN_ID.fullmatch(str(manifest["originating_run_id"]))
    ):
        return ReceiptValidation(False, "corrupt-manifest-result", action_digest)

    if not isinstance(outcomes, dict) or outcome_manifest_error(outcomes) is not None:
        return ReceiptValidation(False, "corrupt-outcomes-schema", action_digest)
    outcome_evidence = manifest.get("outcomes")
    outcome_bytes = canonical_json_bytes(outcomes) + b"\n"
    if not isinstance(outcome_evidence, dict) or outcome_evidence != {
        "artifact": "outcomes.json",
        "bytes": len(outcome_bytes),
        "schema_version": 1,
        "sha256": hashlib.sha256(outcome_bytes).hexdigest(),
    }:
        return ReceiptValidation(False, "corrupt-outcomes-digest", action_digest)

    coverage_path = path / "coverage.data"
    coverage_evidence = manifest.get("coverage")
    try:
        coverage_size = coverage_path.stat().st_size
        coverage_digest = file_sha256(coverage_path)
    except OSError as exc:
        return ReceiptValidation(
            False,
            f"corrupt-coverage-{type(exc).__name__}",
            action_digest,
        )
    if not isinstance(coverage_evidence, dict) or coverage_evidence != {
        "artifact": "coverage.data",
        "branch": True,
        "bytes": coverage_size,
        "semantic_validation": "coverage.py-read-only-v1",
        "sha256": coverage_digest,
    }:
        return ReceiptValidation(False, "corrupt-coverage-digest", action_digest)
    coverage_error = coverage_data_error(coverage_path, require_branch=True)
    if coverage_error is not None:
        return ReceiptValidation(False, "corrupt-coverage-data", action_digest)

    if not isinstance(completion, dict) or set(completion) != {
        "action_digest",
        "manifest_sha256",
        "schema_version",
    }:
        return ReceiptValidation(False, "corrupt-completion-schema", action_digest)
    if completion != {
        "action_digest": action_digest,
        "manifest_sha256": file_sha256(path / "manifest.json"),
        "schema_version": RECEIPT_SCHEMA_VERSION,
    }:
        return ReceiptValidation(False, "corrupt-completion-digest", action_digest)
    return ReceiptValidation(True, "valid", action_digest)


def validate_failure_receipt(
    path: Path,
    *,
    expected_action_digest: str | None = None,
    require_content_addressed_name: bool = True,
) -> ReceiptValidation:
    """Validate one explicitly non-reusable sanitized failure receipt."""
    directory_error = private_path_error(path, directory=True)
    if directory_error is not None:
        return ReceiptValidation(False, f"corrupt-directory-{directory_error}")
    try:
        children = {child.name for child in path.iterdir()}
    except OSError as exc:
        return ReceiptValidation(False, f"corrupt-layout-{type(exc).__name__}")
    if children not in {
        _FAILURE_RECEIPT_FILES,
        _FAILURE_RECEIPT_FILES | {_AUTH_RECEIPT_FILE},
    }:
        return ReceiptValidation(False, "corrupt-layout")
    for name in sorted(children):
        artifact_error = private_path_error(path / name, directory=False)
        if artifact_error is not None:
            return ReceiptValidation(False, f"corrupt-{name}-{artifact_error}")
    try:
        manifest = read_strict_json(path / "manifest.json")
        completion = read_strict_json(path / "complete.json")
        failure = read_strict_json(path / "failure.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return ReceiptValidation(False, f"corrupt-json-{type(exc).__name__}")
    if not isinstance(manifest, dict) or set(manifest) != {
        "batch_identity",
        "failure",
        "failure_digest",
        "format",
        "kind",
        "originating_run_id_sha256",
        "reusable",
        "schema_version",
        "skip_authorized",
        "status",
    }:
        return ReceiptValidation(False, "corrupt-manifest-schema")
    if not isinstance(failure, dict) or set(failure) != {
        "elapsed_seconds",
        "failing_nodes",
        "failure_class",
        "kind",
        "reusable",
        "schema_version",
        "skip_authorized",
    }:
        return ReceiptValidation(False, "corrupt-failure-schema")
    batch_identity = manifest.get("batch_identity")
    if not isinstance(batch_identity, dict) or set(batch_identity) != {
        "action_digest",
        "batch_index",
        "candidate_sha",
        "collection_manifest_sha256",
        "shard",
        "test_files_sha256",
    }:
        return ReceiptValidation(False, "corrupt-batch-identity")
    action_digest = batch_identity.get("action_digest")
    candidate_sha = batch_identity.get("candidate_sha")
    shard = batch_identity.get("shard")
    batch_index = batch_identity.get("batch_index")
    if (
        not isinstance(action_digest, str)
        or not _SHA256.fullmatch(action_digest)
        or (
            expected_action_digest is not None
            and action_digest != expected_action_digest
        )
        or not isinstance(candidate_sha, str)
        or not _GIT_SHA.fullmatch(candidate_sha)
        or path.parent.name != "failures"
        or path.parent.parent.name != candidate_sha
        or not isinstance(shard, str)
        or not _SHARD.fullmatch(shard)
        or type(batch_index) is not int
        or batch_index < 1
        or not isinstance(batch_identity.get("test_files_sha256"), str)
        or not _SHA256.fullmatch(str(batch_identity["test_files_sha256"]))
        or not isinstance(batch_identity.get("collection_manifest_sha256"), str)
        or not _SHA256.fullmatch(str(batch_identity["collection_manifest_sha256"]))
    ):
        return ReceiptValidation(False, "corrupt-batch-identity")
    elapsed = failure.get("elapsed_seconds")
    failing_nodes = failure.get("failing_nodes")
    if (
        manifest.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or manifest.get("format") != "canonical-json"
        or manifest.get("kind") != "non-reusable-failure"
        or manifest.get("status") != "failure"
        or manifest.get("reusable") is not False
        or manifest.get("skip_authorized") is not False
        or failure.get("schema_version") != 1
        or failure.get("kind") != "non-reusable-failure"
        or failure.get("reusable") is not False
        or failure.get("skip_authorized") is not False
        or failure.get("failure_class") not in _FAILURE_CLASSES
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
        or float(elapsed) > MAX_FAILURE_ELAPSED_SECONDS
        or not isinstance(failing_nodes, Mapping)
        or failure_node_metadata_error(failing_nodes) is not None
        or _contains_sensitive_key(manifest)
        or _contains_sensitive_key(failure)
    ):
        return ReceiptValidation(False, "corrupt-failure-result", action_digest)
    run_id_digest = manifest.get("originating_run_id_sha256")
    failure_digest = manifest.get("failure_digest")
    if (
        not isinstance(run_id_digest, str)
        or not _SHA256.fullmatch(run_id_digest)
        or not isinstance(failure_digest, str)
        or not _SHA256.fullmatch(failure_digest)
        or failure_digest
        != _failure_receipt_digest(batch_identity, failure, run_id_digest)
    ):
        return ReceiptValidation(False, "corrupt-failure-digest", action_digest)
    if require_content_addressed_name and path.name != failure_digest:
        return ReceiptValidation(False, "corrupt-content-address", action_digest)
    failure_bytes = canonical_json_bytes(failure) + b"\n"
    if manifest.get("failure") != {
        "artifact": "failure.json",
        "bytes": len(failure_bytes),
        "schema_version": 1,
        "sha256": hashlib.sha256(failure_bytes).hexdigest(),
    }:
        return ReceiptValidation(False, "corrupt-failure-artifact", action_digest)
    if not isinstance(completion, dict) or completion != {
        "failure_digest": failure_digest,
        "manifest_sha256": file_sha256(path / "manifest.json"),
        "schema_version": RECEIPT_SCHEMA_VERSION,
    }:
        return ReceiptValidation(False, "corrupt-completion", action_digest)
    return ReceiptValidation(True, "valid-non-reusable-failure", action_digest)


def action_identity_error(identity: Mapping[str, object]) -> str | None:
    """Return why one replay/write identity is unsafe, or ``None`` when exact."""
    if set(identity) != {
        "coverage",
        "environment",
        "external_inputs",
        "plan",
        "platform",
        "plugins",
        "runner",
        "schema_version",
        "source",
        "toolchain",
    } or identity.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return "action-identity-invalid"
    if _contains_sensitive_key(identity):
        return "sensitive-identity-key"
    if any(
        not isinstance(identity.get(family), Mapping)
        for family in (
            "coverage",
            "plan",
            "platform",
            "plugins",
            "runner",
            "toolchain",
        )
    ) or identity.get("external_inputs") != []:
        return "action-identity-invalid"
    environment = identity.get("environment")
    if not isinstance(environment, Mapping) or set(environment) != {
        "allowlist",
        "restricted_inputs_present",
        "undeclared_names_sha256",
        "values_outside_allowlist_stored",
    }:
        return "environment-identity-invalid"
    if environment.get("restricted_inputs_present") is True:
        return "restricted-environment"
    if (
        environment.get("restricted_inputs_present") is not False
        or environment.get("values_outside_allowlist_stored") is not False
    ):
        return "environment-identity-invalid"
    try:
        canonical_json_sha256(identity)
    except (TypeError, ValueError):
        return "action-identity-invalid"
    source = identity.get("source")
    if not isinstance(source, Mapping):
        return "source-identity-invalid"
    candidate_sha = source.get("candidate_sha")
    if (
        not isinstance(candidate_sha, str)
        or not _GIT_SHA.fullmatch(candidate_sha)
        or source.get("clean") is not True
    ):
        return "source-ineligible"
    if set(source) != {
        "branch",
        "candidate_sha",
        "clean",
        "exact_sha",
        "expected_sha",
        "queries_ok",
        "repository_state_id",
        "test_files",
        "test_files_sha256",
    }:
        return "source-identity-invalid"
    if (
        source.get("expected_sha") != candidate_sha
        or source.get("exact_sha") is not True
        or source.get("queries_ok") is not True
    ):
        return "source-ineligible"
    repository_state_identifier = source.get("repository_state_id")
    branch = source.get("branch")
    test_files = source.get("test_files")
    if (
        not isinstance(repository_state_identifier, str)
        or not repository_state_identifier
        or len(repository_state_identifier) > 256
        or not isinstance(branch, str)
        or not isinstance(test_files, list)
        or not test_files
        or any(
            not isinstance(test_file, str)
            or not test_file
            or Path(test_file).is_absolute()
            or ".." in Path(test_file).parts
            for test_file in test_files
        )
        or source.get("test_files_sha256") != canonical_json_sha256(test_files)
    ):
        return "source-identity-invalid"
    return None


class ShadowBatchReceiptWriter:
    """Atomically publish bounded candidate receipts without ever reading hits."""

    def __init__(
        self,
        cache_root: Path,
        *,
        max_generations: int = MAX_RECEIPT_GENERATIONS,
        max_bytes: int = MAX_RECEIPT_BYTES,
        signer: ReceiptSigner | None = None,
    ) -> None:
        if max_generations < 1 or max_generations > MAX_RECEIPT_GENERATIONS:
            raise ValueError("max_generations must be within the phase-one bound")
        if max_bytes < 1 or max_bytes > MAX_RECEIPT_BYTES:
            raise ValueError("max_bytes must be within the phase-one bound")
        self._cache_root = cache_root
        self._max_generations = max_generations
        self._max_bytes = max_bytes
        self._signer = signer

    def _preflight(self, request: BatchReceiptRequest) -> ReceiptPublication | None:
        if request.returncode != 0:
            return ReceiptPublication(False, "batch-not-passing")
        if request.cleanup_returncode != 0:
            return ReceiptPublication(False, "cleanup-incomplete")
        if outcome_manifest_error(request.outcome_manifest) is not None:
            return ReceiptPublication(False, "outcomes-invalid")
        identity_error = action_identity_error(request.action_identity)
        if identity_error is not None:
            return ReceiptPublication(False, identity_error)
        try:
            expected_digest = canonical_json_sha256(request.action_identity)
            observed_digest = canonical_json_sha256(request.observed_action_identity)
        except (TypeError, ValueError):
            return ReceiptPublication(False, "action-identity-invalid")
        if expected_digest != observed_digest or dict(request.action_identity) != dict(
            request.observed_action_identity
        ):
            return ReceiptPublication(False, "action-identity-drift")
        if not _RUN_ID.fullmatch(request.originating_run_id):
            return ReceiptPublication(False, "run-id-invalid")
        coverage_error = coverage_data_error(request.coverage_path, require_branch=True)
        if coverage_error == "branch coverage is required":
            return ReceiptPublication(False, "coverage-not-branch-aware")
        if coverage_error is not None:
            return ReceiptPublication(False, "coverage-invalid")
        return None

    def publish(self, request: BatchReceiptRequest) -> ReceiptPublication:
        """Publish one pass receipt after coverage and cleanup have succeeded."""
        refused = self._preflight(request)
        if refused is not None:
            return refused
        action_identity = dict(request.action_identity)
        action_digest = canonical_json_sha256(action_identity)
        source = action_identity["source"]
        assert isinstance(source, Mapping)
        candidate_sha = str(source["candidate_sha"])

        if self._cache_root.is_symlink():
            return ReceiptPublication(
                False,
                "cache-root-symlink",
                action_digest=action_digest,
            )
        if self._cache_root.parent.is_symlink():
            return ReceiptPublication(
                False,
                "cache-parent-symlink",
                action_digest=action_digest,
            )
        cache_root_existed = self._cache_root.exists()
        try:
            self._cache_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            if not cache_root_existed:
                os.chmod(self._cache_root, 0o700)
        except OSError:
            return ReceiptPublication(False, "cache-root-unavailable", action_digest=action_digest)
        root_error = private_path_error(self._cache_root, directory=True)
        if root_error is not None:
            return ReceiptPublication(False, f"cache-root-{root_error}", action_digest=action_digest)
        version_root = self._cache_root / f"v{RECEIPT_SCHEMA_VERSION}"
        if version_root.is_symlink():
            return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
        version_root_existed = version_root.exists()
        try:
            version_root.mkdir(mode=0o700, exist_ok=True)
            if not version_root_existed:
                os.chmod(version_root, 0o700)
            generation_entries = tuple(version_root.iterdir())
        except OSError:
            return ReceiptPublication(False, "cache-layout-unavailable", action_digest=action_digest)
        if private_path_error(version_root, directory=True) is not None:
            return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
        generations: list[Path] = []
        for entry in generation_entries:
            if (
                entry.is_symlink()
                or not entry.is_dir()
                or not _GIT_SHA.fullmatch(entry.name)
                or private_path_error(entry, directory=True) is not None
            ):
                return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
            generations.append(entry)
        generation = version_root / candidate_sha
        if not generation.exists() and len(generations) >= self._max_generations:
            return ReceiptPublication(False, "generation-limit", action_digest=action_digest)

        current_bytes, size_error = _safe_tree_size(self._cache_root)
        if size_error is not None:
            return ReceiptPublication(False, "cache-size-unknown", action_digest=action_digest)
        outcome_bytes = canonical_json_bytes(request.outcome_manifest) + b"\n"
        try:
            incoming_bytes = request.coverage_path.stat().st_size + len(outcome_bytes)
        except OSError:
            return ReceiptPublication(False, "coverage-invalid", action_digest=action_digest)
        # Manifests are small relative to coverage, but count a conservative
        # canonical preview so the cap is a hard upper bound rather than a hint.
        incoming_bytes += len(canonical_json_bytes(action_identity)) + 16 * 1024
        if current_bytes + incoming_bytes > self._max_bytes:
            return ReceiptPublication(False, "byte-limit", action_digest=action_digest)

        if generation.is_symlink():
            return ReceiptPublication(False, "generation-invalid", action_digest=action_digest)
        generation_existed = generation.exists()
        try:
            generation.mkdir(mode=0o700, exist_ok=True)
            if not generation_existed:
                os.chmod(generation, 0o700)
        except OSError:
            return ReceiptPublication(False, "generation-unavailable", action_digest=action_digest)
        if private_path_error(generation, directory=True) is not None:
            return ReceiptPublication(False, "generation-invalid", action_digest=action_digest)
        destination = generation / action_digest
        if destination.exists() or destination.is_symlink():
            return ReceiptPublication(
                False,
                "already-present",
                path=destination,
                action_digest=action_digest,
            )

        temporary = Path(
            tempfile.mkdtemp(prefix=f".{action_digest}.", dir=generation)
        )
        os.chmod(temporary, 0o700)
        try:
            outcomes_path = temporary / "outcomes.json"
            coverage_path = temporary / "coverage.data"
            _write_json(outcomes_path, request.outcome_manifest)
            _copy_private(request.coverage_path, coverage_path)
            outcome_size = outcomes_path.stat().st_size
            coverage_size = coverage_path.stat().st_size
            manifest = {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "format": "json+coverage-sqlite",
                "action_digest": action_digest,
                "action_identity": action_identity,
                "originating_run_id": request.originating_run_id,
                "started_at": request.started_at,
                "completed_at": request.completed_at,
                "status": "pass",
                "returncode": 0,
                "cleanup_returncode": 0,
                "coverage": {
                    "artifact": "coverage.data",
                    "branch": True,
                    "bytes": coverage_size,
                    "semantic_validation": "coverage.py-read-only-v1",
                    "sha256": file_sha256(coverage_path),
                },
                "outcomes": {
                    "artifact": "outcomes.json",
                    "bytes": outcome_size,
                    "schema_version": 1,
                    "sha256": file_sha256(outcomes_path),
                },
            }
            manifest_path = temporary / "manifest.json"
            _write_json(manifest_path, manifest)
            if self._signer is not None:
                _write_json(
                    temporary / _AUTH_RECEIPT_FILE,
                    create_receipt_envelope(
                        receipt_kind="pass",
                        action_digest=action_digest,
                        content_sha256=file_sha256(manifest_path),
                        signer=self._signer,
                    ),
                )
            _write_json(
                temporary / "complete.json",
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "action_digest": action_digest,
                    "manifest_sha256": file_sha256(manifest_path),
                },
            )
            _fsync_directory(temporary)
            validation = validate_batch_receipt(
                temporary,
                expected_action_identity=action_identity,
                require_content_addressed_name=False,
            )
            if not validation.valid:
                return ReceiptPublication(
                    False,
                    f"self-validation-{validation.reason}",
                    action_digest=action_digest,
                )
            os.rename(temporary, destination)
            _fsync_directory(generation)
            final_validation = validate_batch_receipt(
                destination,
                expected_action_identity=action_identity,
            )
            if not final_validation.valid:
                shutil.rmtree(destination)
                return ReceiptPublication(
                    False,
                    f"post-publish-{final_validation.reason}",
                    action_digest=action_digest,
                )
            return ReceiptPublication(
                True,
                "published",
                path=destination,
                action_digest=action_digest,
            )
        except (OSError, TypeError, ValueError):
            return ReceiptPublication(False, "publication-failed", action_digest=action_digest)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if not generation_existed:
                with suppress(OSError):
                    generation.rmdir()


class ShadowFailureReceiptWriter:
    """Atomically publish bounded failures that can never authorize reuse."""

    def __init__(
        self,
        cache_root: Path,
        *,
        max_generations: int = MAX_RECEIPT_GENERATIONS,
        max_bytes: int = MAX_RECEIPT_BYTES,
        max_failure_receipts: int = MAX_FAILURE_RECEIPTS_PER_GENERATION,
        signer: ReceiptSigner | None = None,
    ) -> None:
        if max_generations < 1 or max_generations > MAX_RECEIPT_GENERATIONS:
            raise ValueError("max_generations is outside the receipt bound")
        if max_bytes < 1 or max_bytes > MAX_RECEIPT_BYTES:
            raise ValueError("max_bytes is outside the receipt bound")
        if (
            max_failure_receipts < 1
            or max_failure_receipts > MAX_FAILURE_RECEIPTS_PER_GENERATION
        ):
            raise ValueError("max_failure_receipts is outside the receipt bound")
        self._cache_root = cache_root
        self._max_generations = max_generations
        self._max_bytes = max_bytes
        self._max_failure_receipts = max_failure_receipts
        self._signer = signer

    def _preflight(
        self,
        request: FailureReceiptRequest,
    ) -> tuple[dict[str, object], dict[str, object]] | ReceiptPublication:
        if type(request.returncode) is not int or request.returncode == 0:
            return ReceiptPublication(False, "batch-not-failing")
        if request.cleanup_returncode != 0:
            return ReceiptPublication(False, "cleanup-incomplete")
        elapsed = request.elapsed_seconds
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed))
            or float(elapsed) < 0
            or float(elapsed) > MAX_FAILURE_ELAPSED_SECONDS
        ):
            return ReceiptPublication(False, "elapsed-invalid")
        if not _RUN_ID.fullmatch(request.originating_run_id):
            return ReceiptPublication(False, "run-id-invalid")
        identity_error = action_identity_error(request.action_identity)
        if identity_error is not None:
            return ReceiptPublication(False, identity_error)
        try:
            expected_digest = canonical_json_sha256(request.action_identity)
            observed_digest = canonical_json_sha256(request.observed_action_identity)
        except (TypeError, ValueError):
            return ReceiptPublication(False, "action-identity-invalid")
        if expected_digest != observed_digest or dict(request.action_identity) != dict(
            request.observed_action_identity
        ):
            return ReceiptPublication(False, "action-identity-drift")
        try:
            batch_identity = _failure_batch_identity(request.action_identity)
        except ValueError:
            return ReceiptPublication(False, "batch-identity-invalid")
        node_metadata = (
            _unavailable_failure_nodes()
            if request.failure_node_metadata is None
            else dict(request.failure_node_metadata)
        )
        if failure_node_metadata_error(node_metadata) is not None:
            return ReceiptPublication(False, "failure-nodes-invalid")
        failure = {
            "schema_version": 1,
            "kind": "non-reusable-failure",
            "reusable": False,
            "skip_authorized": False,
            "failure_class": failure_class_for_returncode(request.returncode),
            "elapsed_seconds": round(float(elapsed), 3),
            "failing_nodes": node_metadata,
        }
        return batch_identity, failure

    def publish(self, request: FailureReceiptRequest) -> ReceiptPublication:
        """Publish sanitized diagnostics after a failed batch and clean teardown."""
        prepared = self._preflight(request)
        if isinstance(prepared, ReceiptPublication):
            return prepared
        batch_identity, failure = prepared
        action_digest = str(batch_identity["action_digest"])
        candidate_sha = str(batch_identity["candidate_sha"])
        run_id_digest = canonical_json_sha256(request.originating_run_id)
        failure_digest = _failure_receipt_digest(
            batch_identity,
            failure,
            run_id_digest,
        )
        failure_bytes = canonical_json_bytes(failure) + b"\n"
        manifest = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "format": "canonical-json",
            "kind": "non-reusable-failure",
            "status": "failure",
            "reusable": False,
            "skip_authorized": False,
            "failure_digest": failure_digest,
            "originating_run_id_sha256": run_id_digest,
            "batch_identity": batch_identity,
            "failure": {
                "artifact": "failure.json",
                "bytes": len(failure_bytes),
                "schema_version": 1,
                "sha256": hashlib.sha256(failure_bytes).hexdigest(),
            },
        }

        if self._cache_root.is_symlink():
            return ReceiptPublication(False, "cache-root-symlink", action_digest=action_digest)
        if self._cache_root.parent.is_symlink():
            return ReceiptPublication(False, "cache-parent-symlink", action_digest=action_digest)
        cache_root_existed = self._cache_root.exists()
        try:
            self._cache_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            if not cache_root_existed:
                os.chmod(self._cache_root, 0o700)
        except OSError:
            return ReceiptPublication(False, "cache-root-unavailable", action_digest=action_digest)
        root_error = private_path_error(self._cache_root, directory=True)
        if root_error is not None:
            return ReceiptPublication(False, f"cache-root-{root_error}", action_digest=action_digest)
        version_root = self._cache_root / f"v{RECEIPT_SCHEMA_VERSION}"
        if version_root.is_symlink():
            return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
        version_root_existed = version_root.exists()
        try:
            version_root.mkdir(mode=0o700, exist_ok=True)
            if not version_root_existed:
                os.chmod(version_root, 0o700)
            generation_entries = tuple(version_root.iterdir())
        except OSError:
            return ReceiptPublication(False, "cache-layout-unavailable", action_digest=action_digest)
        if private_path_error(version_root, directory=True) is not None:
            return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
        generations: list[Path] = []
        for entry in generation_entries:
            if (
                entry.is_symlink()
                or not entry.is_dir()
                or not _GIT_SHA.fullmatch(entry.name)
                or private_path_error(entry, directory=True) is not None
            ):
                return ReceiptPublication(False, "cache-layout-invalid", action_digest=action_digest)
            generations.append(entry)
        generation = version_root / candidate_sha
        if not generation.exists() and len(generations) >= self._max_generations:
            return ReceiptPublication(False, "generation-limit", action_digest=action_digest)
        current_bytes, size_error = _safe_tree_size(self._cache_root)
        if size_error is not None:
            return ReceiptPublication(False, "cache-size-unknown", action_digest=action_digest)
        incoming_bytes = (
            len(failure_bytes)
            + len(canonical_json_bytes(manifest))
            + 16 * 1024
        )
        if current_bytes + incoming_bytes > self._max_bytes:
            return ReceiptPublication(False, "byte-limit", action_digest=action_digest)
        if generation.is_symlink():
            return ReceiptPublication(False, "generation-invalid", action_digest=action_digest)
        generation_existed = generation.exists()
        failures_root = generation / "failures"
        failures_root_existed = failures_root.exists()
        temporary: Path | None = None
        try:
            generation.mkdir(mode=0o700, exist_ok=True)
            if not generation_existed:
                os.chmod(generation, 0o700)
            if private_path_error(generation, directory=True) is not None:
                return ReceiptPublication(False, "generation-invalid", action_digest=action_digest)
            if failures_root.is_symlink():
                return ReceiptPublication(False, "failure-layout-invalid", action_digest=action_digest)
            failures_root.mkdir(mode=0o700, exist_ok=True)
            if not failures_root_existed:
                os.chmod(failures_root, 0o700)
            if private_path_error(failures_root, directory=True) is not None:
                return ReceiptPublication(False, "failure-layout-invalid", action_digest=action_digest)
            entries = tuple(failures_root.iterdir())
            for entry in entries:
                if (
                    entry.is_symlink()
                    or not entry.is_dir()
                    or not _SHA256.fullmatch(entry.name)
                    or private_path_error(entry, directory=True) is not None
                ):
                    return ReceiptPublication(False, "failure-layout-invalid", action_digest=action_digest)
            destination = failures_root / failure_digest
            if destination.exists() or destination.is_symlink():
                validation = validate_failure_receipt(
                    destination,
                    expected_action_digest=action_digest,
                )
                if not validation.valid:
                    return ReceiptPublication(False, "existing-failure-corrupt", action_digest=action_digest)
                return ReceiptPublication(
                    False,
                    "already-present",
                    path=destination,
                    action_digest=action_digest,
                )
            if len(entries) >= self._max_failure_receipts:
                return ReceiptPublication(False, "failure-receipt-limit", action_digest=action_digest)
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{failure_digest}.", dir=failures_root)
            )
            os.chmod(temporary, 0o700)
            _write_json(temporary / "failure.json", failure)
            manifest_path = temporary / "manifest.json"
            _write_json(manifest_path, manifest)
            if self._signer is not None:
                _write_json(
                    temporary / _AUTH_RECEIPT_FILE,
                    create_receipt_envelope(
                        receipt_kind="failure",
                        action_digest=action_digest,
                        content_sha256=file_sha256(manifest_path),
                        signer=self._signer,
                    ),
                )
            _write_json(
                temporary / "complete.json",
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "failure_digest": failure_digest,
                    "manifest_sha256": file_sha256(manifest_path),
                },
            )
            _fsync_directory(temporary)
            validation = validate_failure_receipt(
                temporary,
                expected_action_digest=action_digest,
                require_content_addressed_name=False,
            )
            if not validation.valid:
                return ReceiptPublication(
                    False,
                    f"self-validation-{validation.reason}",
                    action_digest=action_digest,
                )
            os.rename(temporary, destination)
            temporary = None
            _fsync_directory(failures_root)
            final_validation = validate_failure_receipt(
                destination,
                expected_action_digest=action_digest,
            )
            if not final_validation.valid:
                shutil.rmtree(destination)
                return ReceiptPublication(
                    False,
                    f"post-publish-{final_validation.reason}",
                    action_digest=action_digest,
                )
            return ReceiptPublication(
                True,
                "published",
                path=destination,
                action_digest=action_digest,
            )
        except (OSError, TypeError, ValueError):
            return ReceiptPublication(False, "publication-failed", action_digest=action_digest)
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if not failures_root_existed:
                with suppress(OSError):
                    failures_root.rmdir()
            if not generation_existed:
                with suppress(OSError):
                    generation.rmdir()


__all__ = [
    "MAX_FAILURE_NODES",
    "MAX_FAILURE_RECEIPTS_PER_GENERATION",
    "MAX_RECEIPT_BYTES",
    "MAX_RECEIPT_GENERATIONS",
    "MAX_SEMANTIC_COVERAGE_ITEMS",
    "BatchReceiptRequest",
    "FailureReceiptRequest",
    "ReceiptPublication",
    "ReceiptValidation",
    "ShadowBatchReceiptWriter",
    "ShadowFailureReceiptWriter",
    "action_identity_error",
    "build_batch_runtime_identity",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "coverage_data_error",
    "coverage_semantic_sha256",
    "failure_class_for_returncode",
    "failure_node_metadata_error",
    "file_sha256",
    "normalize_junit_failure_metadata",
    "normalize_junit_outcomes",
    "outcome_manifest_error",
    "private_path_error",
    "read_strict_json",
    "validate_batch_receipt",
    "validate_failure_receipt",
]
