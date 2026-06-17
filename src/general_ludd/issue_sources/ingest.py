"""Ingest: map IssueRecords onto internal todos, dedup, and drive write-back.

This module is the bridge between the pluggable issue sources and the harness's
internal todo model. It is deliberately *pure* with respect to persistence: it
produces internal todo dicts and decides write-backs, but it does NOT itself
touch the database or the event loop — wiring that is a deferred step (see the
DEFERRED INTEGRATION note below).

Responsibilities
----------------
- :func:`record_to_todo` — map a canonical
  :class:`~general_ludd.issue_sources.base.IssueRecord` onto an internal todo
  ``dict`` shaped like the rows :class:`general_ludd.db.repository.TodoRepository`
  consumes (``title`` / ``description`` / ``status`` / ``priority`` / ``tags`` /
  ``external_id`` / ...). The external status is mapped through
  :func:`~general_ludd.issue_sources.base.map_external_status`.
- :func:`dedup_key` / :func:`ingest_records` — dedup by ``(source, external_id)``
  so re-polling the same source never produces duplicate todos. ``ingest_records``
  takes the set of *already-seen* keys and returns only the new todos plus the
  updated key set.
- :func:`lifecycle_write_back` — translate an internal
  :class:`~general_ludd.schemas.todo.TodoStatus` change into a backend write-back
  (``ACTIVE`` -> :class:`Transition.CLAIM`, ``COMPLETE`` -> :class:`Transition.DONE`)
  and apply it idempotently through the owning source.

DEFERRED INTEGRATION (intentionally NOT done in this wave — do not edit
daemon.py / event_loop / db models here):

  1. **Additive ``external_id`` column.** Persisting :func:`dedup_key` across
     restarts needs a nullable ``external_id`` column on ``TodoModel`` (string,
     indexed, format ``"{source}:{external_id}"``) plus an Alembic migration that
     ADDS the column (no backfill, nullable -> safe for existing rows). The
     dedup key produced here is exactly that column's value, so the column is a
     drop-in. Until it lands, callers pass the in-memory ``seen`` set.

  2. **Event-loop poller hookup.** A periodic task in ``event_loop/loop.py``
     (registered by ``daemon.py``) should, each tick: call ``source.fetch(spec)``
     for every configured source, run :func:`ingest_records` against the todos
     already carrying that source's ``external_id``, persist the new todos via
     ``TodoRepository.create``, and on local ``ACTIVE`` / ``COMPLETE`` transitions
     call :func:`lifecycle_write_back` to push the change outward. That wiring is
     a separate wave so this package stays free of daemon/loop/db coupling.
"""

from __future__ import annotations

from typing import Any

from general_ludd.issue_sources.base import (
    IssueRecord,
    IssueSource,
    Transition,
    map_external_status,
)
from general_ludd.schemas.todo import TodoStatus

# Map a mapped TodoStatus onto the write-back Transition the harness pushes
# externally when a LOCAL todo reaches that state. Only the two lifecycle points
# we mirror outward are represented; every other state is local-only.
_STATUS_TO_TRANSITION: dict[TodoStatus, Transition] = {
    TodoStatus.ACTIVE: Transition.CLAIM,
    TodoStatus.COMPLETE: Transition.DONE,
}


def dedup_key(source: str, external_id: str) -> str:
    """Return the stable ``"{source}:{external_id}"`` dedup key.

    This is the exact value the deferred additive ``external_id`` column on
    ``TodoModel`` is intended to hold (see the module docstring).
    """
    return f"{source}:{external_id}"


def record_to_todo(
    record: IssueRecord,
    source: str,
    *,
    queue: str = "intake",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Map a canonical :class:`IssueRecord` onto an internal todo dict.

    The returned dict matches the columns ``TodoRepository.create`` consumes
    (status stored as the ``TodoStatus`` string value, tags as a list). The
    external provenance is carried under ``external_id`` (the deferred column)
    and surfaced in ``tags``.
    """
    mapped = map_external_status(record["status"])
    labels = list(record["labels"])
    tags = [f"source:{source}", *labels]
    description = record["body"] or ""
    if record["url"]:
        description = (description + f"\n\nExternal: {record['url']}").strip()
    todo: dict[str, Any] = {
        "title": record["title"] or f"{source} {record['external_id']}",
        "description": description,
        "status": mapped.value,
        "priority": _priority_to_int(record["priority"]),
        "queue": queue,
        "work_type": "unknown",
        "tags": tags,
        "external_id": dedup_key(source, record["external_id"]),
        "project_id": project_id,
        "created_by": f"issue_source:{source}",
    }
    return todo


_PRIORITY_WORDS: dict[str, int] = {
    "lowest": 0,
    "low": 0,
    "minor": 0,
    "normal": 1,
    "medium": 1,
    "default": 1,
    "high": 2,
    "major": 2,
    "highest": 3,
    "critical": 3,
    "urgent": 3,
    "immediate": 3,
    "blocker": 3,
}


def _priority_to_int(priority: str | None) -> int:
    if priority is None:
        return 1
    text = str(priority).strip().lower()
    if text.isdigit():
        # Clamp a numeric backend priority into the 0..3 internal band.
        return max(0, min(3, int(text)))
    return _PRIORITY_WORDS.get(text, 1)


def ingest_records(
    records: list[IssueRecord],
    source: str,
    seen_keys: set[str] | None = None,
    *,
    queue: str = "intake",
    project_id: str | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Dedup ``records`` by ``(source, external_id)`` and map the new ones to todos.

    ``seen_keys`` is the set of dedup keys already ingested (in the deferred
    persistent design, the set of ``TodoModel.external_id`` values for this
    source). Records whose key is already present — or that repeat within this
    same batch — are skipped.

    Returns ``(new_todos, updated_seen_keys)``. The call is idempotent: passing
    the same records with the returned key set yields an empty ``new_todos``.
    """
    seen: set[str] = set(seen_keys) if seen_keys else set()
    new_todos: list[dict[str, Any]] = []
    for record in records:
        if not record["external_id"]:
            continue  # un-keyable record cannot be deduped — skip rather than dup
        key = dedup_key(source, record["external_id"])
        if key in seen:
            continue
        seen.add(key)
        new_todos.append(
            record_to_todo(record, source, queue=queue, project_id=project_id)
        )
    return new_todos, seen


def transition_for_status(status: TodoStatus) -> Transition | None:
    """Return the outward :class:`Transition` for a local ``status``, or None.

    Only ``ACTIVE`` (claim) and ``COMPLETE`` (done) are mirrored outward; every
    other local status returns ``None`` (nothing to push).
    """
    return _STATUS_TO_TRANSITION.get(status)


def lifecycle_write_back(
    source: IssueSource,
    external_id: str,
    status: TodoStatus,
) -> bool:
    """Push a local ``status`` change to ``external_id`` via the owning source.

    Translates the internal ``TodoStatus`` to a :class:`Transition` and calls
    ``source.write_back``. A status with no outward mapping is a no-op success
    (nothing to push). Idempotency is the source adapter's responsibility, so
    repeated calls for the same already-applied transition stay safe.
    """
    transition = transition_for_status(status)
    if transition is None:
        return True
    return source.write_back(external_id, transition)
