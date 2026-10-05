"""Validation and decoding helpers for managed self-improvement dispatch."""

from __future__ import annotations

import hmac
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan, ManagedRunResult
from general_ludd.self_improve.result_artifact import ManagedSelfImproveResultArtifact
from general_ludd.self_improve.staging import self_improve_artifact_digest

ExecutionMode = Literal["local", "worker"]
BindingResolver = Callable[[str], ProjectRepositoryBinding | None]


@dataclass(frozen=True, slots=True)
class ApprovedPlanValidation:
    """A decoded approved plan or one stable rejection reason."""

    todo_id: str
    plan: ApprovedSelfImprovePlan | None = None
    rejection_reason: str | None = None


def _string_attribute(value: object, name: str) -> str:
    member = getattr(value, name, "")
    return member if isinstance(member, str) else ""


def validate_approved_plan(
    todo: object,
    project_id: str | None,
) -> ApprovedPlanValidation:
    """Decode an immutable plan and bind it to its approved todo and project."""
    plan_artifact = _string_attribute(todo, "plan_artifact")
    todo_id = _string_attribute(todo, "todo_id")
    if not plan_artifact:
        return ApprovedPlanValidation(todo_id, rejection_reason="missing_plan_artifact")

    approved_digest = getattr(todo, "approved_artifact_digest", None)
    try:
        actual_digest = self_improve_artifact_digest(plan_artifact)
    except ValueError:
        actual_digest = ""
    if not isinstance(approved_digest, str) or not hmac.compare_digest(
        approved_digest,
        actual_digest,
    ):
        return ApprovedPlanValidation(
            todo_id,
            rejection_reason="approval_artifact_digest_mismatch",
        )
    try:
        plan = ApprovedSelfImprovePlan.from_json(plan_artifact)
    except (TypeError, ValueError):
        return ApprovedPlanValidation(todo_id, rejection_reason="invalid_plan_artifact")
    if plan.todo_id != todo_id:
        return ApprovedPlanValidation(todo_id, rejection_reason="todo_identity_mismatch")
    if project_id is None or plan.project_id != project_id:
        return ApprovedPlanValidation(todo_id, rejection_reason="project_identity_mismatch")
    return ApprovedPlanValidation(todo_id, plan=plan)


def configured_execution_mode(config: object) -> ExecutionMode | None:
    """Return the bounded execution mode, or ``None`` for invalid config."""
    self_improve = config.get("self_improve", {}) if isinstance(config, dict) else {}
    mode = self_improve.get("execution_mode", "local") if isinstance(self_improve, dict) else "local"
    if mode == "local":
        return "local"
    if mode == "worker":
        return "worker"
    return None


def resolve_repository_binding(
    plan: ApprovedSelfImprovePlan,
    project_id: str,
    execution_mode: ExecutionMode,
    resolver: BindingResolver,
) -> tuple[ProjectRepositoryBinding | None, str | None]:
    """Resolve and verify the approved path-independent repository identity."""
    if plan.repository_binding_digest:
        binding = resolver(project_id)
        if binding is None:
            return None, "repository_unavailable"
        if not hmac.compare_digest(plan.repository_binding_digest, binding.digest):
            return None, "repository_binding_stale"
        return binding, None
    if execution_mode == "worker":
        return None, "repository_binding_required"
    return None, None


def bind_local_plan(
    plan: ApprovedSelfImprovePlan,
    repo_root: Path | None,
    binding: ProjectRepositoryBinding | None,
) -> tuple[ApprovedSelfImprovePlan | None, str | None]:
    """Bind a local plan to current repository evidence without widening it."""
    if repo_root is None:
        return None, "repository_unavailable"
    if binding is not None:
        try:
            return (
                plan.bind_execution_repository(
                    repo_root,
                    repository_binding_digest=binding.digest,
                ),
                None,
            )
        except (OSError, TypeError, ValueError):
            return None, "repository_binding_stale"
    if plan.repo_root != repo_root:
        return None, "repository_identity_mismatch"
    return plan, None


def serialize_run_result(
    plan: ApprovedSelfImprovePlan,
    result: ManagedRunResult,
) -> tuple[str, int]:
    """Validate managed result identity and return its durable JSON and exit code."""
    artifact = ManagedSelfImproveResultArtifact.from_run_result(result)
    if (
        artifact.plan_identity_digest != plan.identity_digest
        or artifact.attempt_identity_digest != plan.attempt_identity_digest
    ):
        raise ValueError("managed result identity does not match approved plan")
    return artifact.to_json(), 0 if artifact.accepted else 1


async def decode_worker_response(response: object) -> tuple[dict[str, object], object]:
    """Normalize dictionary and HTTP response shapes without trusting their body."""
    if isinstance(response, dict):
        return cast(dict[str, object], response), 200
    response_json = getattr(response, "json", None)
    if not callable(response_json):
        raise ValueError("worker response has no JSON body")
    data: object = response_json()
    if inspect.isawaitable(data):
        data = await cast(Awaitable[object], data)
    if not isinstance(data, dict):
        raise ValueError("worker response body is not a mapping")
    return cast(dict[str, object], data), getattr(response, "status_code", 200)


def worker_rejection_reason(data: dict[str, object], status_code: object) -> str | None:
    """Map a non-success worker response to one bounded local reason."""
    if isinstance(status_code, int) and 200 <= status_code < 300:
        return None
    detail = data.get("detail")
    remote_reason_value = detail.get("reason") if isinstance(detail, dict) else None
    remote_reason = remote_reason_value if isinstance(remote_reason_value, str) else ""
    return {
        "self_improve_repository_binding_stale": "repository_binding_stale",
        "self_improve_repository_unavailable": "repository_unavailable",
    }.get(remote_reason, "worker_rejected")


def validate_worker_result(
    plan: ApprovedSelfImprovePlan,
    data: dict[str, object],
) -> str:
    """Validate a worker result artifact and return its unchanged JSON body."""
    result_summary = data.get("result_summary")
    if not isinstance(result_summary, str):
        raise TypeError("worker result summary must be serialized JSON")
    artifact = ManagedSelfImproveResultArtifact.from_json(result_summary)
    if (
        artifact.plan_identity_digest != plan.identity_digest
        or artifact.attempt_identity_digest != plan.attempt_identity_digest
    ):
        raise ValueError("worker result identity does not match approved plan")
    return result_summary


__all__ = (
    "ApprovedPlanValidation",
    "ExecutionMode",
    "bind_local_plan",
    "configured_execution_mode",
    "decode_worker_response",
    "resolve_repository_binding",
    "serialize_run_result",
    "validate_approved_plan",
    "validate_worker_result",
    "worker_rejection_reason",
)
