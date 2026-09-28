"""Project-owned runtime identity helpers for the event-loop dispatcher."""

from __future__ import annotations

import json
from typing import Any

from general_ludd.event_loop.review_orchestration import safe_string_attribute
from general_ludd.schemas.project_identity import ProjectWorkIdentity


def runtime_work_identity(
    todo: Any,
    fallback_project_id: str | None = None,
) -> tuple[ProjectWorkIdentity, bool]:
    """Resolve one todo to its canonical project-owned runtime identity."""
    explicit_project_id = safe_string_attribute(todo, "project_id")
    if (
        explicit_project_id is not None
        and fallback_project_id is not None
        and explicit_project_id != fallback_project_id
    ):
        raise ValueError("todo project identity does not match the selected project")
    project_id = explicit_project_id or fallback_project_id
    todo_id = str(safe_string_attribute(todo, "todo_id", "") or id(todo))
    queue = safe_string_attribute(todo, "queue", "core") or "core"
    return (
        ProjectWorkIdentity(project_id or "default", todo_id, queue),
        project_id is not None,
    )


def runtime_lease_bucket_key(
    todo: Any,
    fallback_project_id: str | None = None,
) -> str:
    """Return an owner-disjoint execution-lease key for one todo.

    Legacy projectless work is explicitly namespaced as ``unowned`` instead of
    being aliased to the valid project identifier ``default``.
    """
    identity, project_owned = runtime_work_identity(todo, fallback_project_id)
    if project_owned:
        return identity.lease_bucket_key
    return f"unowned:queue:{identity.queue}:todo:{identity.todo_id}"


def runtime_lease_bucket_keys(
    todo: Any,
    fallback_project_id: str | None = None,
) -> tuple[str, ...]:
    """Return the canonical key plus the v0.1.1 rolling-upgrade fence.

    Older workers still use ``queue:todo``. Holding that compatibility key in
    the same acquisition batch prevents an old and new binary from executing
    the same todo during a zero-downtime rollout. It can be removed only after
    the old runtime is no longer supported.
    """
    identity, _ = runtime_work_identity(todo, fallback_project_id)
    primary = runtime_lease_bucket_key(todo, fallback_project_id)
    legacy = f"{identity.queue}:{identity.todo_id}"
    return (primary, legacy) if primary != legacy else (primary,)


def todo_dependency_ids(todo: Any) -> tuple[str, ...]:
    """Decode a todo's explicit dependency list without coercing values."""
    raw = getattr(todo, "dependencies", None)
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("todo dependencies must be a JSON string array") from exc
    if not isinstance(raw, (list, tuple)):
        # Compatibility for older duck-typed dispatch objects that do not
        # declare this field (notably RPC/test doubles whose ``getattr`` creates
        # an opaque proxy). Persisted JSON strings still fail closed above.
        if not isinstance(raw, (dict, set)):
            return ()
        raise ValueError("todo dependencies must be a string array")
    dependencies: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ValueError("todo dependency identifiers must be non-empty strings")
        if value not in dependencies:
            dependencies.append(value)
    return tuple(dependencies)
