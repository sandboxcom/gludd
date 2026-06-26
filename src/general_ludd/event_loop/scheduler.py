"""TodoScheduler: tick-driven promotion of due SCHEDULED todos.

Design:
  - ONE-SHOT (cron is None, scheduled_at set): when now >= scheduled_at the
    scheduler transitions the todo SCHEDULED→QUEUED. Runs exactly once.
  - RECURRING TEMPLATE (cron set): the todo stays SCHEDULED. On each fire the
    scheduler spawns a fresh QUEUED child clone (new todo_id, parent_todo_id
    set to the template), advances template.next_run_at via croniter, increments
    run_count, and sets last_run_at. When run_count reaches max_runs the template
    is transitioned SCHEDULED→CANCELLED (retired permanently).
  - schedule_paused templates are skipped entirely.

Pure / testable: inject the repo and (optionally) a clock callable.
No session ownership — the caller's session flows through the repo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)

# Execution fields copied from a cron template onto each QUEUED child clone.
# Scheduling fields (scheduled_at, cron, next_run_at, …) are intentionally
# omitted — children are plain one-off todos, not new schedules.
_CLONE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "work_type",
    "queue",
    "priority",
    "tags",
    "risk_level",
    "resource_profile",
    "acceptance_criteria",
    "test_commands",
    "molecule_scenarios",
    "molecule_evidence_refs",
    "coverage_requirements",
    "dependencies",
    "model_profile",
    "prompt_profile",
    "worktree",
    "branch_name",
    "plan_artifact",
    "confidence",
    "approval_policy",
    "project_id",
    "assigned_agent",
    "created_by",
)


def _next_cron_dt(expr: str, after: datetime, tz_name: str) -> datetime:
    """Return the next cron fire time for *expr* after *after*, in UTC.

    Uses croniter for cron evaluation and zoneinfo for timezone handling so
    DST-crossing schedules fire at the correct wall-clock time in the
    configured timezone.  Raises ``ValueError`` on an invalid cron expression
    or unknown timezone name.
    """
    from croniter import croniter

    try:
        zone = ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc

    # Convert *after* to the schedule timezone so croniter evaluates the
    # expression against local wall-clock time (important for DST).
    after_local = after.astimezone(zone)
    it = croniter(expr, after_local)
    next_dt: datetime = it.get_next(datetime)
    # croniter returns tz-aware datetime when start_time is tz-aware.
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=zone)
    return next_dt.astimezone(UTC)


class TodoScheduler:
    """Tick-driven scheduler that promotes due SCHEDULED todos.

    Args:
        repo:  A :class:`TodoRepository` bound to the current session.
        clock: Optional callable returning the current UTC datetime. Defaults
               to ``datetime.now(UTC)``. Inject a fixed clock in tests.
    """

    def __init__(
        self,
        repo: TodoRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else (lambda: datetime.now(UTC))
        )

    async def tick(self, now: datetime | None = None) -> tuple[int, int]:
        """Process all due SCHEDULED todos.

        Args:
            now: The reference time for "due" comparison.  Defaults to the
                 injected clock's current value.  Pass explicitly in tests.

        Returns:
            ``(promoted, spawned)`` where *promoted* counts one-shot todos
            transitioned SCHEDULED→QUEUED and *spawned* counts cron child
            clones created.
        """
        if now is None:
            now = self._clock()

        due = await self._repo.list_due_scheduled(now)
        promoted = 0
        spawned = 0

        for todo in due:
            # Belt-and-suspenders: list_due_scheduled already filters paused
            # rows but a stale in-memory object could slip through.
            if getattr(todo, "schedule_paused", False):
                continue

            cron_expr: str | None = getattr(todo, "cron", None)

            if cron_expr is None:
                # ── ONE-SHOT: promote SCHEDULED → QUEUED ──────────────────
                try:
                    await self._repo.transition(
                        todo.todo_id,
                        TodoStatus.QUEUED,
                        todo.version,
                    )
                    promoted += 1
                    logger.debug(
                        "Scheduler: one-shot %s promoted SCHEDULED→QUEUED",
                        todo.todo_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Scheduler: failed to promote one-shot %s: %s",
                        todo.todo_id,
                        exc,
                    )
                continue

            # ── RECURRING TEMPLATE ─────────────────────────────────────────
            run_count: int = int(getattr(todo, "run_count", 0) or 0)
            max_runs: int | None = getattr(todo, "max_runs", None)

            if max_runs is not None and run_count >= max_runs:
                # Cap reached: retire the template SCHEDULED → CANCELLED.
                try:
                    await self._repo.transition(
                        todo.todo_id,
                        TodoStatus.CANCELLED,
                        todo.version,
                    )
                    logger.info(
                        "Scheduler: template %s reached max_runs=%d → CANCELLED",
                        todo.todo_id,
                        max_runs,
                    )
                except Exception as exc:
                    logger.warning(
                        "Scheduler: failed to cancel exhausted template %s: %s",
                        todo.todo_id,
                        exc,
                    )
                continue

            # Advance the template FIRST, then spawn the child (review DEFECT 2).
            # If the process fails between the two writes, advancing first yields
            # a MISSED occurrence (safe, logged) rather than a DUPLICATE child —
            # the template would otherwise re-fire every tick because
            # ``next_run_at`` was never advanced, double-executing the work.
            tz_name: str = getattr(todo, "schedule_timezone", None) or "UTC"
            try:
                next_run = _next_cron_dt(cron_expr, now, tz_name)
            except Exception as exc:
                logger.warning(
                    "Scheduler: invalid cron for template %s (%r): %s — skipping",
                    todo.todo_id,
                    cron_expr,
                    exc,
                )
                continue

            try:
                updates: dict[str, Any] = {
                    "next_run_at": next_run,
                    "run_count": run_count + 1,
                    "last_run_at": now,
                }
                await self._repo.update(
                    todo.todo_id,
                    updates,
                    expected_version=todo.version,
                )
            except Exception as exc:
                logger.warning(
                    "Scheduler: failed to advance template %s: %s — skipping spawn",
                    todo.todo_id,
                    exc,
                )
                continue

            # Template advanced; spawn the QUEUED child clone. A failure here is a
            # missed occurrence (logged), never a duplicate.
            child_data = _build_child_data(todo)
            try:
                await self._repo.create(child_data)
                spawned += 1
                logger.debug(
                    "Scheduler: template %s spawned child, next_run_at=%s",
                    todo.todo_id,
                    next_run,
                )
            except Exception as exc:
                logger.warning(
                    "Scheduler: child clone create failed for %s after advance: %s",
                    todo.todo_id,
                    exc,
                )

        return promoted, spawned


def _build_child_data(template: Any) -> dict[str, Any]:
    """Build the creation dict for a QUEUED child clone of a cron template.

    Only execution fields are copied; scheduling state is excluded so the
    child is a plain one-off todo that will be claimed and executed normally.
    """
    child_todo_id = f"TODO-{uuid4().hex[:8].upper()}"
    data: dict[str, Any] = {
        "todo_id": child_todo_id,
        "status": TodoStatus.QUEUED.value,
        "parent_todo_id": template.todo_id,
    }
    for field in _CLONE_FIELDS:
        val = getattr(template, field, None)
        if val is not None:
            data[field] = val
    return data
