"""Bounded read-only adapters for git-release Ansible service roles.

The adapters execute the collection's existing evidence, helper, provenance,
and ZDD decision engines. They never run a caller-provided command and never
report a proposed mutation as completed work.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .deployment import (
    DeploymentConfig,
    DeploymentOrchestrator,
    DeploymentStrategy,
    HealthGate,
    HealthSample,
)
from .evidence import RepoEvidence, collect_repo_evidence
from .helper_catalog import discover_helpers
from .helper_ranker import TaskRequirements, helper_build_file_changes, rank_helpers
from .provenance import (
    Attestation,
    ProvenanceRecord,
    ReceiptPurpose,
    SignatureState,
    build_provenance,
    verify_provenance,
)

GIT_RELEASE_OPERATIONS = frozenset(
    {
        "artifact_build",
        "artifact_verify",
        "conflict_resolve",
        "deploy_orchestrate",
        "helper_build",
        "helper_discover",
        "helper_select",
        "pipeline_triage",
        "release_plan",
        "release_recover",
        "work_recover",
    }
)

_MAX_REQUEST_BYTES = 262_144
_MAX_FILE_BYTES = 32 * 1024 * 1024
_PLAN_ACTIONS = {
    "work_recover": (
        "preserve current repository evidence",
        "inspect reflog and worktree recovery candidates",
        "validate recovered work before applying it",
    ),
    "conflict_resolve": (
        "identify the merge base and both intended changes",
        "prepare a reversible resolution candidate",
        "run the repository's focused validation helper",
    ),
    "release_plan": (
        "pin the exact source revision",
        "select project-authoritative build and test helpers",
        "require provenance and rollback evidence before promotion",
    ),
    "pipeline_triage": (
        "correlate the first failing job with repository-owned helpers",
        "reproduce the smallest failing command in isolation",
        "validate the correction before proposing a pipeline rerun",
    ),
    "release_recover": (
        "halt further promotion",
        "preserve release and deployment evidence",
        "verify the prior known-good artifact before rollback",
    ),
}


class GitReleaseRequestError(ValueError):
    """Signal invalid git-release operation input at the service boundary."""


def _text(
    request: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
    maximum: int = 4096,
) -> str:
    value = request.get(key, default)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(char in value for char in "\r\n\x00")
    ):
        raise GitReleaseRequestError(f"{key} must be a non-empty string")
    return value.strip()


def _integer(
    request: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise GitReleaseRequestError(f"{key} must be between {minimum} and {maximum}")
    return value


def _number(
    request: Mapping[str, Any],
    key: str,
    *,
    default: float | None = None,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GitReleaseRequestError(f"{key} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        raise GitReleaseRequestError(f"{key} must be within the supported range")
    return parsed


def _mapping(request: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = request.get(key)
    if not isinstance(value, Mapping):
        raise GitReleaseRequestError(f"{key} must be an object")
    return value


def _evidence(path: str) -> RepoEvidence:
    try:
        return collect_repo_evidence(path)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        raise GitReleaseRequestError(str(exc)) from exc


def _evidence_dict(evidence: RepoEvidence) -> dict[str, Any]:
    return {
        "path": evidence.path,
        "head_sha": evidence.head_sha,
        "branch": evidence.branch,
        "is_dirty": evidence.is_dirty,
        "is_detached": evidence.is_detached,
    }


def _file_inside(repository: Path, request: Mapping[str, Any], key: str) -> Path:
    relative = _text(request, key)
    root = repository.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise GitReleaseRequestError(f"{key} must resolve inside the repository")
    if not candidate.is_file():
        raise GitReleaseRequestError(f"{key} is not a regular file")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise GitReleaseRequestError(f"{key} cannot be inspected") from exc
    if size < 1 or size > _MAX_FILE_BYTES:
        raise GitReleaseRequestError(f"{key} must be between 1 and {_MAX_FILE_BYTES} bytes")
    return candidate


def _read_bounded(path: Path, key: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitReleaseRequestError(f"{key} cannot be read") from exc


def _requirements(request: Mapping[str, Any]) -> TaskRequirements:
    platforms_raw = request.get("platforms", [])
    if not isinstance(platforms_raw, list) or len(platforms_raw) > 32:
        raise GitReleaseRequestError("platforms must be a list with at most 32 entries")
    if any(not isinstance(value, str) or not value.strip() for value in platforms_raw):
        raise GitReleaseRequestError("platforms entries must be non-empty strings")
    return TaskRequirements(
        kind=_text(request, "kind", default="build", maximum=64),
        needs_dry_run=request.get("needs_dry_run", False) is True,
        needs_rollback=request.get("needs_rollback", False) is True,
        min_score=_integer(request, "min_score", default=50, minimum=0, maximum=100),
        platforms=tuple(value.strip() for value in platforms_raw),
    )


def _helper_operation(
    operation: str,
    request: Mapping[str, Any],
    repository: Path,
) -> dict[str, Any]:
    candidates = discover_helpers(repository)
    if operation == "helper_discover":
        return {
            "operation": operation,
            "state": "observed",
            "mutation_performed": False,
            "candidates": [asdict(candidate) for candidate in candidates],
        }
    requirements = _requirements(request)
    ranked = rank_helpers(candidates, requirements)
    if operation == "helper_select":
        return {
            "operation": operation,
            "state": "selected" if ranked else "blocked",
            "mutation_performed": False,
            "selected": asdict(ranked[0]) if ranked else None,
            "candidates": [asdict(candidate) for candidate in ranked],
        }
    return {
        "operation": operation,
        "state": "no_change" if ranked else "proposal",
        "mutation_performed": False,
        "file_changes": helper_build_file_changes(ranked, repository, requirements),
    }


def _build_artifact_provenance(
    request: Mapping[str, Any],
    repository: Path,
    evidence: RepoEvidence,
) -> dict[str, Any]:
    artifact_path = _file_inside(repository, request, "artifact_path")
    lock_path = _file_inside(repository, request, "dependency_lock_path")
    source_sha = _text(
        request,
        "source_sha",
        default=evidence.head_sha or None,
        maximum=40,
    )
    try:
        signature_state = SignatureState(request.get("signature_state", "unsigned"))
        record = build_provenance(
            artifact_name=artifact_path.name,
            artifact_bytes=_read_bounded(artifact_path, "artifact_path"),
            dependency_lock_bytes=_read_bounded(lock_path, "dependency_lock_path"),
            builder_identity=_text(request, "builder_identity", maximum=256),
            release_id=_text(request, "release_id", maximum=256),
            source_sha=source_sha,
            signature_state=signature_state,
        )
    except (TypeError, ValueError) as exc:
        raise GitReleaseRequestError(str(exc)) from exc
    return {
        "operation": "artifact_build",
        "state": "observed",
        "mutation_performed": False,
        "provenance": asdict(record),
    }


def _provenance_record(value: object) -> ProvenanceRecord:
    if not isinstance(value, Mapping):
        raise GitReleaseRequestError("provenance must be an object")
    attestation_raw = value.get("attestation")
    if not isinstance(attestation_raw, Mapping):
        raise GitReleaseRequestError("provenance.attestation must be an object")
    statement = attestation_raw.get("statement")
    sbom = value.get("sbom")
    if not isinstance(statement, Mapping) or not isinstance(sbom, Mapping):
        raise GitReleaseRequestError("provenance statement and sbom must be objects")
    try:
        attestation = Attestation(
            predicate_type=str(attestation_raw["predicate_type"]),
            statement=statement,
            digest=str(attestation_raw["digest"]),
        )
        return ProvenanceRecord(
            sbom=sbom,
            signature_state=SignatureState(value["signature_state"]),
            attestation=attestation,
            builder_identity=str(value["builder_identity"]),
            dependency_lock_digest=str(value["dependency_lock_digest"]),
            artifact_digest=str(value["artifact_digest"]),
            subject=str(value["subject"]),
            release_id=str(value["release_id"]),
            source_sha=str(value["source_sha"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GitReleaseRequestError("provenance record is malformed") from exc


def _verify_artifact(
    request: Mapping[str, Any],
    repository: Path,
) -> dict[str, Any]:
    artifact_path = _file_inside(repository, request, "artifact_path")
    lock_path = _file_inside(repository, request, "dependency_lock_path")
    record = _provenance_record(request.get("provenance"))
    try:
        required_state = SignatureState(request.get("required_signature_state", record.signature_state.value))
        purpose_raw = request.get("receipt_purpose")
        purpose = ReceiptPurpose(purpose_raw) if purpose_raw is not None else None
    except ValueError as exc:
        raise GitReleaseRequestError("signature state or receipt purpose is invalid") from exc
    result = verify_provenance(
        record,
        expected_artifact_bytes=_read_bounded(artifact_path, "artifact_path"),
        expected_lock_bytes=_read_bounded(lock_path, "dependency_lock_path"),
        expected_subject=request.get("expected_subject"),
        expected_builder_identity=request.get("expected_builder_identity"),
        expected_release_id=request.get("expected_release_id"),
        expected_source_sha=request.get("expected_source_sha"),
        expected_signature_state=required_state,
        verified_attestation_digest=request.get("verified_attestation_digest"),
        authorization_id=request.get("authorization_id"),
        authorization_source_sha=request.get("authorization_source_sha"),
        receipt_purpose=purpose,
        deployment_target=request.get("deployment_target"),
    )
    response: dict[str, Any] = {
        "operation": "artifact_verify",
        "state": "verified" if result.ok else "failed",
        "mutation_performed": False,
        "ok": result.ok,
        "reasons": result.reasons,
    }
    if result.receipt is not None:
        response["receipt"] = asdict(result.receipt)
    return response


def _deploy_decision(request: Mapping[str, Any]) -> dict[str, Any]:
    gate_raw = _mapping(request, "health_gate")
    try:
        strategy = DeploymentStrategy(_text(request, "strategy", maximum=32))
    except ValueError as exc:
        raise GitReleaseRequestError("strategy is invalid") from exc
    gate = HealthGate(
        max_error_rate=_number(gate_raw, "max_error_rate", maximum=1.0),
        min_availability=_number(gate_raw, "min_availability", maximum=1.0),
        max_latency_p99_ms=_number(gate_raw, "max_latency_p99_ms", minimum=0.001),
    )
    config = DeploymentConfig(
        strategy=strategy,
        health_gate=gate,
        abort_threshold=_number(request, "abort_threshold", maximum=1.0),
        max_step_percent=_integer(request, "max_step_percent", default=25, minimum=1, maximum=100),
        observation_window_s=_integer(
            request,
            "observation_window_s",
            default=120,
            minimum=1,
            maximum=3600,
        ),
    )
    sample_raw = request.get("sample")
    sample = None
    if sample_raw is not None:
        if not isinstance(sample_raw, Mapping):
            raise GitReleaseRequestError("sample must be an object or null")
        sample = HealthSample(
            availability=_number(sample_raw, "availability", maximum=1.0),
            error_rate=_number(sample_raw, "error_rate", maximum=1.0),
            latency_p99_ms=_number(sample_raw, "latency_p99_ms", minimum=0.001),
        )
    try:
        orchestrator = DeploymentOrchestrator(
            config=config,
            prior_digest=_text(request, "prior_digest", maximum=256),
            new_digest=_text(request, "new_digest", maximum=256),
        )
        decision = orchestrator.evaluate(stage="candidate", sample=sample)
        shift = orchestrator.next_shift(
            current_percent=_integer(request, "current_percent", default=0, minimum=0, maximum=100)
        )
    except ValueError as exc:
        raise GitReleaseRequestError(str(exc)) from exc
    return {
        "operation": "deploy_orchestrate",
        "state": "decision",
        "mutation_performed": False,
        "decision": {"type": type(decision).__name__, **vars(decision)},
        "next_shift": asdict(shift),
    }


def _plan(
    operation: str,
    evidence: RepoEvidence,
    repository: Path,
) -> dict[str, Any]:
    blockers = []
    if not evidence.head_sha:
        blockers.append("head-sha-unresolved")
    if evidence.is_detached:
        blockers.append("detached-head")
    if evidence.is_dirty and operation in {"release_plan", "release_recover"}:
        blockers.append("working-tree-dirty")
    response: dict[str, Any] = {
        "operation": operation,
        "state": "proposal",
        "mutation_performed": False,
        "evidence": _evidence_dict(evidence),
        "blockers": blockers,
        "proposed_actions": list(_PLAN_ACTIONS[operation]),
    }
    if operation == "pipeline_triage":
        response["helpers"] = [
            asdict(candidate)
            for candidate in discover_helpers(repository)
            if candidate.authority == "ci-used"
        ]
    return response


def dispatch_git_release_operation(
    operation: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Validate and execute one allowlisted, idempotent git-release operation."""
    if operation not in GIT_RELEASE_OPERATIONS:
        raise GitReleaseRequestError(f"unsupported git-release operation: {operation}")
    try:
        encoded_size = len(json.dumps(request, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise GitReleaseRequestError("git-release request must be JSON serializable") from exc
    if len(request) > 128 or encoded_size > _MAX_REQUEST_BYTES:
        raise GitReleaseRequestError("git-release request exceeds the bounded payload size")

    path = _text(request, "path")
    evidence = _evidence(path)
    repository = Path(evidence.path)
    try:
        if operation.startswith("helper_"):
            return _helper_operation(operation, request, repository)
        if operation == "artifact_build":
            return _build_artifact_provenance(request, repository, evidence)
        if operation == "artifact_verify":
            return _verify_artifact(request, repository)
        if operation == "deploy_orchestrate":
            return _deploy_decision(request)
        return _plan(operation, evidence, repository)
    except GitReleaseRequestError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError, TypeError, ValueError) as exc:
        raise GitReleaseRequestError(str(exc)) from exc


__all__ = [
    "GIT_RELEASE_OPERATIONS",
    "GitReleaseRequestError",
    "dispatch_git_release_operation",
]
