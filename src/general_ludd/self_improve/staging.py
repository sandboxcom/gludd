"""Typed staging boundary for managed self-improvement approvals.

Periodic gap detection cannot create an executable plan by itself: an
independent baseline/reference commit pair is still required.  This module
preserves the detected gap as a bounded, canonical request and later validates
the exact approved plan produced from that request.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final, cast

from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    TaskSpec,
)

MANAGED_SELF_IMPROVE_APPROVAL_POLICY: Final = "managed_self_improve_plan"
MANAGED_PLAN_REQUEST_ARTIFACT_TYPE: Final = "managed_self_improve_plan_request"

_REQUEST_SCHEMA_VERSION: Final = 1
_MAX_REQUEST_BYTES: Final = 262_144
_MAX_IDENTITY_BYTES: Final = 128
_MAX_TEXT_BYTES: Final = 65_536
_MAX_RECENT_TODOS: Final = 32
_LEGACY_CONFIG_FIELDS: Final = frozenset(
    {"capability_required", "change_content", "kind", "reason", "target_paths"}
)
_LEGACY_NON_CONFIG_FIELDS: Final = frozenset(
    {
        "description",
        "kind",
        "project_id",
        "schema_version",
        "title",
        "worktree_path",
    }
)


class ManagedSelfImproveArtifactKind(StrEnum):
    """Stable discriminator for approval artifacts sharing one legacy column."""

    MANAGED_PLAN_REQUEST = "managed_plan_request"
    MANAGED_APPROVED_PLAN = "managed_approved_plan"
    MALFORMED_MANAGED = "malformed_managed"
    LEGACY_CONFIG = "legacy_config"
    LEGACY_NON_CONFIG = "legacy_non_config"
    LEGACY_UNKNOWN = "legacy_unknown"


def _bounded_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{label} must be bounded text")
    if value != value.strip() or (not value and not allow_empty):
        raise ValueError(f"{label} must be canonical text")
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise ValueError(f"{label} exceeds the bounded text limit")
    return value


def _bounded_identity(value: object, label: str, *, allow_empty: bool = False) -> str:
    text = _bounded_text(value, label, allow_empty=allow_empty)
    if len(text.encode("utf-8")) > _MAX_IDENTITY_BYTES:
        raise ValueError(f"{label} exceeds the bounded identity limit")
    return text


def _source_path(value: object) -> str:
    text = _bounded_text(value, "source_file", allow_empty=True)
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text:
        raise ValueError("source_file must be a confined repository-relative path")
    return path.as_posix()


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"managed plan request contains duplicate field: {key}")
        value[key] = item
    return value


def _exact_mapping(
    value: object,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields are malformed")
    return cast(dict[str, object], value)


def _task_json_value(task: TaskSpec) -> dict[str, object]:
    return {
        "canonical_make_commands": list(task.canonical_make_commands),
        "objective": task.objective,
        "reference_elapsed_seconds": float(task.reference_elapsed_seconds),
        "task_id": task.task_id,
    }


def _task_from_json_value(value: object) -> TaskSpec:
    mapping = _exact_mapping(
        value,
        fields=frozenset(
            {
                "canonical_make_commands",
                "objective",
                "reference_elapsed_seconds",
                "task_id",
            }
        ),
        label="managed request task",
    )
    commands = mapping["canonical_make_commands"]
    if not isinstance(commands, list) or not all(
        isinstance(command, str) for command in commands
    ):
        raise ValueError("managed request task commands are malformed")
    elapsed = mapping["reference_elapsed_seconds"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        raise ValueError("managed request elapsed time is malformed")
    return TaskSpec(
        task_id=_bounded_identity(mapping["task_id"], "task_id"),
        objective=_bounded_text(mapping["objective"], "objective"),
        canonical_make_commands=tuple(commands),
        reference_elapsed_seconds=float(elapsed),
    )


@dataclass(frozen=True, slots=True)
class ManagedSelfImprovePlanRequest:
    """Immutable gap record awaiting explicit independent reference commits."""

    project_id: str
    source: str
    gap_type: str
    source_file: str
    title: str
    work_type: str
    task_type: str
    blocker_kind: str
    incident_count: int
    recent_todo_ids: tuple[str, ...]
    task: TaskSpec

    def __post_init__(self) -> None:
        """Reject ambiguous, unbounded, or path-escaping request state."""
        _bounded_identity(self.project_id, "project_id")
        if len(self.project_id.encode("utf-8")) > 32:
            raise ValueError("project_id exceeds the persisted identity limit")
        _bounded_identity(self.source, "source")
        _bounded_identity(self.gap_type, "gap_type")
        if _source_path(self.source_file) != self.source_file:
            raise ValueError("source_file is not canonical")
        _bounded_text(self.title, "title")
        if len(self.title.encode("utf-8")) > 512:
            raise ValueError("title exceeds the persisted title limit")
        _bounded_identity(self.work_type, "work_type")
        _bounded_identity(self.task_type, "task_type", allow_empty=True)
        _bounded_identity(self.blocker_kind, "blocker_kind", allow_empty=True)
        if (
            isinstance(self.incident_count, bool)
            or not isinstance(self.incident_count, int)
            or not 0 <= self.incident_count <= 1_000_000
        ):
            raise ValueError("incident_count must be a bounded non-negative integer")
        if (
            not isinstance(self.recent_todo_ids, tuple)
            or len(self.recent_todo_ids) > _MAX_RECENT_TODOS
        ):
            raise ValueError("recent_todo_ids must be a bounded immutable tuple")
        for todo_id in self.recent_todo_ids:
            _bounded_identity(todo_id, "recent_todo_id")
            if len(todo_id.encode("utf-8")) > 32:
                raise ValueError("recent_todo_id exceeds the persisted identity limit")
        if not isinstance(self.task, TaskSpec):
            raise ValueError("task must be an immutable TaskSpec")

    def to_json(self) -> str:
        """Serialize the exact request in one stable, bounded representation."""
        raw = json.dumps(
            {
                "artifact_type": MANAGED_PLAN_REQUEST_ARTIFACT_TYPE,
                "blocker_kind": self.blocker_kind,
                "gap_type": self.gap_type,
                "incident_count": self.incident_count,
                "project_id": self.project_id,
                "recent_todo_ids": list(self.recent_todo_ids),
                "schema_version": _REQUEST_SCHEMA_VERSION,
                "source": self.source,
                "source_file": self.source_file,
                "task": _task_json_value(self.task),
                "task_type": self.task_type,
                "title": self.title,
                "work_type": self.work_type,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(raw.encode("utf-8")) > _MAX_REQUEST_BYTES:
            raise ValueError("managed plan request exceeds the bounded artifact limit")
        return raw

    @classmethod
    def from_json(cls, raw: object) -> ManagedSelfImprovePlanRequest:
        """Parse only the canonical request schema with duplicate-key rejection."""
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw.encode("utf-8")) > _MAX_REQUEST_BYTES
        ):
            raise ValueError("managed plan request must be bounded JSON text")
        try:
            value = json.loads(raw, object_pairs_hook=_duplicate_safe_object)
        except json.JSONDecodeError as exc:
            raise ValueError("managed plan request is malformed JSON") from exc
        mapping = _exact_mapping(
            value,
            fields=frozenset(
                {
                    "artifact_type",
                    "blocker_kind",
                    "gap_type",
                    "incident_count",
                    "project_id",
                    "recent_todo_ids",
                    "schema_version",
                    "source",
                    "source_file",
                    "task",
                    "task_type",
                    "title",
                    "work_type",
                }
            ),
            label="managed plan request",
        )
        if mapping["artifact_type"] != MANAGED_PLAN_REQUEST_ARTIFACT_TYPE:
            raise ValueError("managed plan request artifact type is unsupported")
        if mapping["schema_version"] != _REQUEST_SCHEMA_VERSION:
            raise ValueError("managed plan request schema version is unsupported")
        recent = mapping["recent_todo_ids"]
        if not isinstance(recent, list) or not all(
            isinstance(todo_id, str) for todo_id in recent
        ):
            raise ValueError("managed plan request recent todo ids are malformed")
        request = cls(
            project_id=_bounded_identity(mapping["project_id"], "project_id"),
            source=_bounded_identity(mapping["source"], "source"),
            gap_type=_bounded_identity(mapping["gap_type"], "gap_type"),
            source_file=_source_path(mapping["source_file"]),
            title=_bounded_text(mapping["title"], "title"),
            work_type=_bounded_identity(mapping["work_type"], "work_type"),
            task_type=_bounded_identity(
                mapping["task_type"], "task_type", allow_empty=True
            ),
            blocker_kind=_bounded_identity(
                mapping["blocker_kind"], "blocker_kind", allow_empty=True
            ),
            incident_count=cast(int, mapping["incident_count"]),
            recent_todo_ids=tuple(recent),
            task=_task_from_json_value(mapping["task"]),
        )
        if raw != request.to_json():
            raise ValueError("managed plan request JSON is not canonical")
        return request


def _request_task_id(identity: dict[str, object]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"S{int(hashlib.sha256(canonical).hexdigest()[:16], 16)}"


def build_managed_plan_request_payload(
    todo: dict[str, object],
    *,
    project_id: str,
) -> dict[str, object]:
    """Build the two persistence fields for one periodic managed gap todo."""
    if not isinstance(todo, dict):
        raise ValueError("managed plan request source must be a todo mapping")
    project = _bounded_identity(project_id, "project_id")
    title = _bounded_text(todo.get("title", "Self-improvement task"), "title")
    description = _bounded_text(
        todo.get("description", ""), "description", allow_empty=True
    )
    objective = description or title
    source = _bounded_identity(
        todo.get("source", "self_improve_harness"), "source"
    )
    gap_type = _bounded_identity(todo.get("gap_type", "unspecified"), "gap_type")
    source_file = _source_path(todo.get("source_file", ""))
    work_type = _bounded_identity(todo.get("work_type", "code"), "work_type")
    task_type = _bounded_identity(
        todo.get("task_type", ""), "task_type", allow_empty=True
    )
    blocker_kind = _bounded_identity(
        todo.get("blocker_kind", ""), "blocker_kind", allow_empty=True
    )
    incident_count = todo.get("incident_count", 0)
    recent_raw = todo.get("recent_todo_ids", [])
    if not isinstance(recent_raw, (list, tuple)) or not all(
        isinstance(todo_id, str) for todo_id in recent_raw
    ):
        raise ValueError("recent_todo_ids must be a sequence of identities")
    commands_raw = todo.get("test_commands", ["make gate"])
    if not isinstance(commands_raw, (list, tuple)) or not all(
        isinstance(command, str) for command in commands_raw
    ):
        raise ValueError("test_commands must be a sequence of make commands")
    task_identity: dict[str, object] = {
        "blocker_kind": blocker_kind,
        "gap_type": gap_type,
        "objective": objective,
        "project_id": project,
        "source": source,
        "source_file": source_file,
        "task_type": task_type,
        "title": title,
        "work_type": work_type,
    }
    task = TaskSpec(
        task_id=_request_task_id(task_identity),
        objective=objective,
        canonical_make_commands=tuple(commands_raw),
    )
    request = ManagedSelfImprovePlanRequest(
        project_id=project,
        source=source,
        gap_type=gap_type,
        source_file=source_file,
        title=title,
        work_type=work_type,
        task_type=task_type,
        blocker_kind=blocker_kind,
        incident_count=cast(int, incident_count),
        recent_todo_ids=tuple(recent_raw),
        task=task,
    )
    return {
        "approval_policy": MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        "plan_artifact": request.to_json(),
    }


def classify_self_improve_artifact(
    raw: object,
    approval_policy: object,
) -> ManagedSelfImproveArtifactKind:
    """Classify managed artifacts strictly while retaining legacy visibility."""
    if approval_policy == MANAGED_SELF_IMPROVE_APPROVAL_POLICY:
        try:
            ManagedSelfImprovePlanRequest.from_json(raw)
        except ValueError:
            pass
        else:
            return ManagedSelfImproveArtifactKind.MANAGED_PLAN_REQUEST
        try:
            plan = ApprovedSelfImprovePlan.from_json(cast(str, raw))
            if raw != plan.to_json():
                raise ValueError("approved plan artifact is not canonical")
        except (TypeError, ValueError):
            return ManagedSelfImproveArtifactKind.MALFORMED_MANAGED
        return ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN

    if not isinstance(raw, str):
        return ManagedSelfImproveArtifactKind.LEGACY_UNKNOWN
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ManagedSelfImproveArtifactKind.LEGACY_UNKNOWN
    if not isinstance(value, dict):
        return ManagedSelfImproveArtifactKind.LEGACY_UNKNOWN
    fields = frozenset(value)
    if fields == _LEGACY_CONFIG_FIELDS:
        return ManagedSelfImproveArtifactKind.LEGACY_CONFIG
    if fields == _LEGACY_NON_CONFIG_FIELDS:
        return ManagedSelfImproveArtifactKind.LEGACY_NON_CONFIG
    return ManagedSelfImproveArtifactKind.LEGACY_UNKNOWN


def validate_bound_managed_plan(
    raw: object,
    *,
    todo_id: str,
    project_id: str,
    repo_root: Path,
    expected_task: TaskSpec | None = None,
    baseline_ref: str | None = None,
    reference_ref: str | None = None,
) -> ApprovedSelfImprovePlan:
    """Return a canonical plan only when every persisted identity remains bound."""
    if not isinstance(raw, str):
        raise ValueError("managed self-improve approved plan is missing")
    plan = ApprovedSelfImprovePlan.from_json(raw)
    if raw != plan.to_json():
        raise ValueError("managed self-improve approved plan is not canonical")
    if plan.todo_id != todo_id or plan.approval_id != todo_id:
        raise ValueError("managed self-improve todo identity drifted")
    if plan.project_id != project_id:
        raise ValueError("managed self-improve project identity drifted")
    try:
        canonical_root = repo_root.resolve(strict=True)
        plan_root = plan.repo_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("managed self-improve repository is unavailable") from exc
    if not canonical_root.is_dir() or plan_root != canonical_root:
        raise ValueError("managed self-improve repository identity drifted")
    if expected_task is not None and plan.task != expected_task:
        raise ValueError("managed self-improve task identity drifted")
    if baseline_ref is not None and plan.reference.baseline_sha != baseline_ref:
        raise ValueError("managed self-improve baseline identity drifted")
    if reference_ref is not None and plan.reference.reference_sha != reference_ref:
        raise ValueError("managed self-improve reference identity drifted")
    return plan


__all__ = [
    "MANAGED_PLAN_REQUEST_ARTIFACT_TYPE",
    "MANAGED_SELF_IMPROVE_APPROVAL_POLICY",
    "ManagedSelfImproveArtifactKind",
    "ManagedSelfImprovePlanRequest",
    "build_managed_plan_request_payload",
    "classify_self_improve_artifact",
    "validate_bound_managed_plan",
]
