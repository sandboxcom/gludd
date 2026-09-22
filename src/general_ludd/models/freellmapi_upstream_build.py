"""Validate exact-source FreeLLMAPI upstream build evidence.

This universal model-infrastructure boundary proves only that an immutable
upstream source candidate builds and passes its own checks.  It never promotes
or admits executable code; artifact purity, delta, and lifecycle gates remain
separate decisions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import NoReturn, TypedDict, cast

from general_ludd.models.freellmapi_frozen_delta import (
    FreeLLMAPIFrozenDeltaError,
)
from general_ludd.models.freellmapi_frozen_delta_validation import (
    validate_candidate,
)

FREELLMAPI_UPSTREAM_BUILD_GATE = "freellmapi-upstream-build-v1"

_EXPECTED_INSTALL = ("npm", "ci", "--no-audit", "--no-fund")
_EXPECTED_STEPS = (
    ("migrations", ("npm", "run", "test:migrations")),
    ("root_tests", ("npm", "test")),
    ("lint", ("npm", "run", "lint")),
    ("workspace_build", ("npm", "run", "build")),
    ("server_coverage", ("npm", "run", "test:coverage", "-w", "server")),
)
_EXPECTED_TOOLCHAINS = (
    ("20.20.2", "10.8.2"),
    ("22.23.2", "10.9.8"),
)
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "gate",
        "candidate_id",
        "archive_sha256",
        "toolchains",
        "install",
        "steps",
        "step_timeout_seconds",
        "owner",
        "source_retention",
    }
)
_RESULT_KEYS = frozenset({"step_id", "status", "exit_code"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
_NPM_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


class FreeLLMAPIUpstreamBuildFault(StrEnum):
    """Stable failure categories that never expose source or command output."""

    CANDIDATE_INVALID = "candidate_invalid"
    PLAN_INVALID = "plan_invalid"
    ARCHIVE_INVALID = "archive_invalid"
    TOOLCHAIN_INVALID = "toolchain_invalid"
    SCRIPTS_INVALID = "scripts_invalid"
    RESULTS_INVALID = "results_invalid"
    STEP_FAILED = "step_failed"
    INPUT_INVALID = "input_invalid"
    IO_FAILED = "io_failed"


class FreeLLMAPIUpstreamBuildError(ValueError):
    """Fail-closed upstream-build error with content-free text."""

    def __init__(self, fault: FreeLLMAPIUpstreamBuildFault) -> None:
        """Create an error containing only its stable category."""
        self.fault = fault
        super().__init__(fault.value)


class ValidatedUpstreamBuildPlan(TypedDict):
    """Immutable subset safe for the ephemeral build runner."""

    candidate_id: str
    archive_sha256: str
    toolchains: tuple[tuple[str, str], ...]
    install: tuple[str, ...]
    steps: tuple[tuple[str, tuple[str, ...]], ...]
    step_timeout_seconds: int
    plan_id: str


class _ValidatedResult(TypedDict):
    """One content-free upstream phase outcome."""

    step_id: str
    status: str
    exit_code: int | None


def _fail(fault: FreeLLMAPIUpstreamBuildFault) -> NoReturn:
    raise FreeLLMAPIUpstreamBuildError(fault)


def _mapping(
    value: object, fault: FreeLLMAPIUpstreamBuildFault
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(fault)
    return value


def _canonical_sha256(value: object, fault: FreeLLMAPIUpstreamBuildFault) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail(fault)
    return hashlib.sha256(encoded).hexdigest()


def _archive_identity(candidate_lock: Mapping[str, object]) -> tuple[str, int]:
    fault = FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID
    archive = _mapping(candidate_lock.get("archive"), fault)
    digest = archive.get("sha256")
    size = archive.get("size_bytes")
    if (
        not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        _fail(fault)
    return digest, size


def _candidate_identity(candidate_lock: Mapping[str, object]) -> tuple[str, str]:
    try:
        candidate_id, _ = validate_candidate(candidate_lock)
    except FreeLLMAPIFrozenDeltaError:
        _fail(FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID)
    archive_digest, _ = _archive_identity(candidate_lock)
    return candidate_id, archive_digest


def _exact_argv(value: object, expected: tuple[str, ...]) -> tuple[str, ...]:
    fault = FreeLLMAPIUpstreamBuildFault.PLAN_INVALID
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(fault)
    argv = tuple(cast("list[str]", value))
    if argv != expected:
        _fail(fault)
    return argv


def _steps(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    fault = FreeLLMAPIUpstreamBuildFault.PLAN_INVALID
    if not isinstance(value, list) or len(value) != len(_EXPECTED_STEPS):
        _fail(fault)
    parsed: list[tuple[str, tuple[str, ...]]] = []
    for raw, expected in zip(value, _EXPECTED_STEPS, strict=True):
        step = _mapping(raw, fault)
        if set(step) != {"id", "argv"} or step.get("id") != expected[0]:
            _fail(fault)
        parsed.append((expected[0], _exact_argv(step.get("argv"), expected[1])))
    return tuple(parsed)


def _toolchains(value: object) -> tuple[tuple[str, str], ...]:
    fault = FreeLLMAPIUpstreamBuildFault.PLAN_INVALID
    expected = [
        {"node_version": node, "npm_version": npm}
        for node, npm in _EXPECTED_TOOLCHAINS
    ]
    if value != expected:
        _fail(fault)
    return _EXPECTED_TOOLCHAINS


def validate_upstream_build_plan(
    candidate_lock: Mapping[str, object], plan: Mapping[str, object]
) -> ValidatedUpstreamBuildPlan:
    """Bind an execution plan to the exact non-runnable source candidate."""
    candidate_id, archive_digest = _candidate_identity(candidate_lock)
    fault = FreeLLMAPIUpstreamBuildFault.PLAN_INVALID
    if (
        set(plan) != _PLAN_KEYS
        or plan.get("schema_version") != 1
        or plan.get("gate") != FREELLMAPI_UPSTREAM_BUILD_GATE
        or plan.get("candidate_id") != candidate_id
        or plan.get("archive_sha256") != archive_digest
        or plan.get("owner") != "general_ludd.models"
        or plan.get("source_retention") != "ephemeral_verified_archive"
    ):
        _fail(fault)
    timeout = plan.get("step_timeout_seconds")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 60 <= timeout <= 3600
    ):
        _fail(fault)
    return ValidatedUpstreamBuildPlan(
        candidate_id=candidate_id,
        archive_sha256=archive_digest,
        toolchains=_toolchains(plan.get("toolchains")),
        install=_exact_argv(plan.get("install"), _EXPECTED_INSTALL),
        steps=_steps(plan.get("steps")),
        step_timeout_seconds=timeout,
        plan_id="sha256:" + _canonical_sha256(plan, fault),
    )


def validate_upstream_toolchain(
    node_version: str,
    npm_version: str,
    allowed_toolchains: tuple[tuple[str, str], ...],
) -> tuple[int, str, str]:
    """Validate exact tool versions before any upstream command executes."""
    fault = FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID
    node_match = _NODE_VERSION_RE.fullmatch(node_version)
    npm_match = _NPM_VERSION_RE.fullmatch(npm_version)
    if node_match is None or npm_match is None:
        _fail(fault)
    node_major = int(node_match.group(1))
    normalized_node = node_version.removeprefix("v")
    if (normalized_node, npm_version) not in allowed_toolchains:
        _fail(fault)
    return node_major, node_version, npm_version


def _validated_results(
    results: Sequence[Mapping[str, object]], plan: ValidatedUpstreamBuildPlan
) -> tuple[_ValidatedResult, ...]:
    fault = FreeLLMAPIUpstreamBuildFault.RESULTS_INVALID
    expected_ids = ("install", *(step_id for step_id, _ in plan["steps"]))
    if isinstance(results, (str, bytes)) or len(results) != len(expected_ids):
        _fail(fault)
    parsed: list[_ValidatedResult] = []
    stopped = False
    for raw, expected_id in zip(results, expected_ids, strict=True):
        result = _mapping(raw, fault)
        status = result.get("status")
        exit_code = result.get("exit_code")
        if set(result) != _RESULT_KEYS or result.get("step_id") != expected_id:
            _fail(fault)
        if stopped:
            if status != "not_run" or exit_code is not None:
                _fail(fault)
        elif status == "passed":
            if exit_code != 0:
                _fail(fault)
        elif status == "failed":
            if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code <= 0:
                _fail(fault)
            stopped = True
        else:
            _fail(fault)
        parsed.append(
            _ValidatedResult(
                step_id=expected_id,
                status=status,
                exit_code=exit_code,
            )
        )
    return tuple(parsed)


def build_upstream_evidence(
    *,
    candidate_lock: Mapping[str, object],
    plan: Mapping[str, object],
    node_version: str,
    npm_version: str,
    results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Create deterministic, content-free build evidence without admission."""
    validated_plan = validate_upstream_build_plan(candidate_lock, plan)
    node_major, exact_node, exact_npm = validate_upstream_toolchain(
        node_version,
        npm_version,
        validated_plan["toolchains"],
    )
    validated_results = _validated_results(results, validated_plan)
    failed = next(
        (result["step_id"] for result in validated_results if result["status"] == "failed"),
        None,
    )
    record: dict[str, object] = {
        "schema_version": 1,
        "gate": FREELLMAPI_UPSTREAM_BUILD_GATE,
        "candidate_id": validated_plan["candidate_id"],
        "archive_sha256": validated_plan["archive_sha256"],
        "plan_id": validated_plan["plan_id"],
        "node_major": node_major,
        "node_version": exact_node,
        "npm_version": exact_npm,
        "step_count": len(validated_results),
        "steps": [dict(result) for result in validated_results],
        "decision": "rejected_upstream_build" if failed else "upstream_build_verified",
        "failed_step": failed,
        "runtime_admitted": False,
    }
    record["evidence_id"] = "sha256:" + _canonical_sha256(
        record, FreeLLMAPIUpstreamBuildFault.RESULTS_INVALID
    )
    return record


__all__ = [
    "FREELLMAPI_UPSTREAM_BUILD_GATE",
    "FreeLLMAPIUpstreamBuildError",
    "FreeLLMAPIUpstreamBuildFault",
    "build_upstream_evidence",
    "validate_upstream_build_plan",
    "validate_upstream_toolchain",
]
