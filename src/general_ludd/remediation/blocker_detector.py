"""Blocker detector — scan todos / human-todos for stalled work.

The detector is the read half of the remediation system. It does NOT mutate
state; it returns :class:`BlockedTask` findings that the
:class:`~general_ludd.remediation.dispatcher.RemediationDispatcher` acts on.

Three signal sources:

  1. **Todos in ``blocked_on_human`` status** older than the per-category
     threshold. The linked :class:`HumanTodoModel` (matched via
     ``parent_agent_todo_id``) classifies the blocker kind.
  2. **Chronically re-queued todos** — todos whose ``run_count`` exceeds
     :attr:`RemediationConfig.max_requeues_before_chronic`. These have already
     been retried multiple times; the system retries once more with a note.
  3. **Stale open human-todos** — human-todos that have been ``open`` for more
     than the threshold without any resolution. These are escalated.

The classification → remediation mapping is deterministic:

  - ``permission_escalation`` human-todo → ``schedule_retry`` (the operator
    may have just approved; give it time, then re-ping).
  - ``input_request`` human-todo → ``file_human_todo`` (escalate to a higher
    priority reminder).
  - ``resource_contention`` (chronic re-queue with no human-todo) →
    ``dispatch_agent`` (try a fresh agent with a different approach).
  - everything else → ``no_action``.

Pure / testable: inject the repositories + a clock callable. No session
ownership — the caller's session flows through the repos.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from general_ludd.db.models import HumanTodoModel, TodoEventModel, TodoModel
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)


# ─── configuration ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RemediationConfig:
    """Operator-tunable thresholds for the remediation system.

    Defaults are deliberately conservative so a healthy project does nothing.
    Override via the constructor (loaded from ``config/remediation.yml`` or
    env vars by the caller); the dataclass is frozen so a running scan sees
    a consistent snapshot.
    """

    human_input_block_hours: int = 24
    permission_escalation_block_hours: int = 4
    max_requeues_before_chronic: int = 3
    chronic_lookback_days: int = 7
    min_chronic_incidents: int = 5
    # Delay (hours) used by RemediationDispatcher.schedule_retry.
    retry_delay_hours: int = 4
    # Minimum hours a todo must have been in NEEDS_MORE_WORK before it is
    # eligible for the NMW→QUEUED requeue sweep. Shorter than the
    # human-input threshold so work cycles back automatically before a
    # human-todo is filed.
    needs_more_work_cooldown_hours: int = 24


# ─── findings ────────────────────────────────────────────────────────────────


BLOCKER_KINDS: frozenset[str] = frozenset({"human_input", "permission_escalation", "resource_contention", "unknown"})

REMEDIATION_KINDS: frozenset[str] = frozenset({"dispatch_agent", "schedule_retry", "file_human_todo", "no_action"})


@dataclass(frozen=True)
class BlockedTask:
    """One blocked-task finding produced by :meth:`BlockerDetector.scan`.

    ``suggested_remediation`` is the detector's classification; the
    dispatcher may override it (e.g. an operator policy that forces
    permission escalations to ``file_human_todo`` instead of retry).
    """

    todo_id: str
    project_id: str | None
    blocked_at: datetime
    blocked_duration_seconds: int
    blocker_kind: str
    blocker_summary: str
    suggested_remediation: str
    # Linked human-todo id when the block came from a HumanTodo (None for
    # resource_contention / chronic re-queue findings).
    linked_human_todo_id: str | None = None
    # Original task type / work_type — used by ChronicBlocker grouping.
    task_type: str = ""


@dataclass(frozen=True)
class ChronicBlocker:
    """A (task_type, blocker_kind) pair that crosses the incident threshold."""

    task_type: str
    blocker_kind: str
    incident_count: int
    first_seen: datetime
    last_seen: datetime
    recent_todo_ids: list[str] = field(default_factory=list)


# ─── detector ────────────────────────────────────────────────────────────────


def _safe_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        # SQLite returns offset-naive datetimes even for tz-aware columns;
        # assume UTC so subtraction against `now(UTC)` doesn't raise.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return None


def _classify_blocker(human_todo: HumanTodoModel | None, *, is_chronic_requeue: bool) -> tuple[str, str]:
    """Return ``(blocker_kind, suggested_remediation)`` for one finding.

    Classification precedence:
      1. Linked human-todo category (the operator-facing signal).
      2. Chronic re-queue with no human-todo ⇒ resource contention.
      3. Fallback: unknown / no_action.
    """
    if human_todo is not None:
        category = getattr(human_todo, "category", "") or ""
        if category == "permission_escalation":
            return ("permission_escalation", "schedule_retry")
        if category == "input_request":
            return ("human_input", "file_human_todo")
        # Other human-todo categories (external_action, decision, blocker):
        # treat as a generic human_input block — the operator needs to act.
        return ("human_input", "file_human_todo")

    if is_chronic_requeue:
        return ("resource_contention", "dispatch_agent")

    return ("unknown", "no_action")


class BlockerDetector:
    """Scan the todo + human-todo tables for stalled work.

    Pure read: callers pass the live SQLAlchemy session via the repos; the
    detector never commits.
    """

    def __init__(
        self,
        todo_repo: Any | None = None,
        human_todo_repo: Any | None = None,
        event_log_repo: Any | None = None,
        config: RemediationConfig | None = None,
        *,
        session: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize a read-only detector with optional repository seams."""
        self._todo_repo = todo_repo
        self._human_todo_repo = human_todo_repo
        self._event_log_repo = event_log_repo
        self._config = config or RemediationConfig()
        # An async SQLAlchemy session used for the queries that the detector
        # runs itself (event-log lookups for chronic analysis). May be None
        # in tests that inject mocks for everything.
        self._session = session
        self._clock: Callable[[], datetime] = clock if clock is not None else (lambda: datetime.now(UTC))

    @property
    def config(self) -> RemediationConfig:
        """Return the immutable remediation threshold configuration."""
        return self._config

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------

    async def scan(self, project_id: str | None = None) -> list[BlockedTask]:
        """Return every blocked task older than its category threshold."""
        now = self._clock()
        findings: list[BlockedTask] = []
        blocked_on_human = await self._scan_blocked_on_human(now, project_id)
        findings.extend(blocked_on_human)
        findings.extend(await self._scan_chronic_requeues(now, project_id))

        blocked_todo_ids = {finding.todo_id for finding in blocked_on_human}
        stale_human_todos = await self._scan_stale_human_todos(now, project_id)
        findings.extend(finding for finding in stale_human_todos if finding.todo_id not in blocked_todo_ids)
        return findings

    async def _scan_blocked_on_human(self, now: datetime, project_id: str | None) -> list[BlockedTask]:
        if self._todo_repo is None:
            return []
        try:
            todos = await self._todo_repo.list_by_status(TodoStatus.BLOCKED_ON_HUMAN, project_id=project_id)
        except Exception as exc:
            logger.warning("BlockerDetector: list_by_status failed: %s", exc)
            return []

        out: list[BlockedTask] = []
        for todo in todos:
            updated_at = _safe_datetime(getattr(todo, "updated_at", None))
            if updated_at is None:
                continue
            age = now - updated_at
            linked = await self._lookup_linked_human_todo(todo)
            kind, _rem = _classify_blocker(linked, is_chronic_requeue=False)
            threshold_h = self._threshold_hours_for_kind(kind)
            if age < timedelta(hours=threshold_h):
                continue
            kind2, rem2 = _classify_blocker(linked, is_chronic_requeue=False)
            summary = self._summary_for(todo, linked, kind2)
            out.append(
                BlockedTask(
                    todo_id=str(getattr(todo, "todo_id", "")),
                    project_id=getattr(todo, "project_id", None),
                    blocked_at=updated_at,
                    blocked_duration_seconds=int(age.total_seconds()),
                    blocker_kind=kind2,
                    blocker_summary=summary,
                    suggested_remediation=rem2,
                    linked_human_todo_id=(str(getattr(linked, "id", "")) if linked is not None else None),
                    task_type=str(getattr(todo, "work_type", "") or ""),
                )
            )
        return out

    async def _scan_chronic_requeues(self, now: datetime, project_id: str | None) -> list[BlockedTask]:
        """Surface todos whose run_count exceeds the chronic threshold.

        Looks across QUEUED / BLOCKED / BLOCKED_ON_HUMAN — i.e. todos that
        are still alive but have been retried many times. COMPLETE / CANCELLED
        rows are excluded (their run_count is historical, not a live block).
        """
        if self._session is None or self._todo_repo is None:
            return []
        live_states = (
            TodoStatus.QUEUED.value,
            TodoStatus.BLOCKED.value,
            TodoStatus.BLOCKED_ON_HUMAN.value,
            TodoStatus.NEEDS_MORE_WORK.value,
        )
        try:
            stmt = select(TodoModel).where(TodoModel.status.in_(live_states))
            if project_id is not None:
                stmt = stmt.where(TodoModel.project_id == project_id)
            result = await self._session.execute(stmt)
            todos = list(result.scalars().all())
        except Exception as exc:
            logger.warning("BlockerDetector: chronic re-queue query failed: %s", exc)
            return []

        out: list[BlockedTask] = []
        threshold = self._config.max_requeues_before_chronic
        for todo in todos:
            run_count = int(getattr(todo, "run_count", 0) or 0)
            if run_count < threshold:
                continue
            updated_at = _safe_datetime(getattr(todo, "updated_at", None)) or now
            age = now - updated_at
            kind, rem = _classify_blocker(None, is_chronic_requeue=True)
            summary = (
                f"Task re-queued {run_count} times (threshold {threshold}) "
                f"without a human-todo — likely resource contention."
            )
            out.append(
                BlockedTask(
                    todo_id=str(getattr(todo, "todo_id", "")),
                    project_id=getattr(todo, "project_id", None),
                    blocked_at=updated_at,
                    blocked_duration_seconds=int(age.total_seconds()),
                    blocker_kind=kind,
                    blocker_summary=summary,
                    suggested_remediation=rem,
                    task_type=str(getattr(todo, "work_type", "") or ""),
                )
            )
        return out

    async def _scan_stale_human_todos(self, now: datetime, project_id: str | None) -> list[BlockedTask]:
        """Open human-todos past the per-category threshold.

        These are escalated via ``file_human_todo`` — the dispatcher files a
        fresh high-priority reminder. The original parent todo (if any) is
        the blocked task; if the human-todo has no parent, the finding is
        filed against the human-todo itself (synthetic todo_id).
        """
        if self._human_todo_repo is None:
            return []
        try:
            open_human_todos = await self._human_todo_repo.list_open()
        except Exception as exc:
            logger.warning("BlockerDetector: list_open human-todos failed: %s", exc)
            return []

        out: list[BlockedTask] = []
        for ht in open_human_todos:
            created_at = _safe_datetime(getattr(ht, "created_at", None))
            if created_at is None:
                continue
            age = now - created_at
            category = getattr(ht, "category", "") or ""
            kind = "permission_escalation" if category == "permission_escalation" else "human_input"
            threshold_h = self._threshold_hours_for_kind(kind)
            if age < timedelta(hours=threshold_h):
                continue
            # Skip if the parent todo is already surfaced by _scan_blocked_on_human
            # (it will be — but if project_id filter excludes it, surface here).
            parent_id = getattr(ht, "parent_agent_todo_id", None)
            todo_id = str(parent_id) if parent_id else f"HTODO:{getattr(ht, 'id', '')}"
            summary = (
                f"Open {category} human-todo '{getattr(ht, 'title', '')[:80]}' "
                f"unresolved for {int(age.total_seconds() // 3600)}h."
            )
            # Permission escalations get a scheduled retry (the operator may
            # have just approved); all other categories get a fresh
            # high-priority reminder.
            rem = "schedule_retry" if category == "permission_escalation" else "file_human_todo"
            out.append(
                BlockedTask(
                    todo_id=todo_id,
                    project_id=project_id,
                    blocked_at=created_at,
                    blocked_duration_seconds=int(age.total_seconds()),
                    blocker_kind=kind,
                    blocker_summary=summary,
                    suggested_remediation=rem,
                    linked_human_todo_id=str(getattr(ht, "id", "")),
                    task_type="",
                )
            )
        return out

    # ------------------------------------------------------------------
    # chronic
    # ------------------------------------------------------------------

    async def chronic_blockers(self, lookback_days: int | None = None) -> list[ChronicBlocker]:
        """Group recent blocked-task incidents by (task_type, blocker_kind).

        Uses the ``todo_events`` audit trail: every BLOCKED_ON_HUMAN
        transition is one incident. Returns pairs whose incident count
        crosses :attr:`RemediationConfig.min_chronic_incidents` over the
        lookback window.
        """
        if self._session is None:
            return []
        days = lookback_days if lookback_days is not None else self._config.chronic_lookback_days
        cutoff = self._clock() - timedelta(days=days)
        try:
            stmt = (
                select(TodoEventModel)
                .where(TodoEventModel.event_type == "status_changed")
                .where(TodoEventModel.new_status == TodoStatus.BLOCKED_ON_HUMAN.value)
                .where(TodoEventModel.created_at >= cutoff)
                .order_by(TodoEventModel.created_at.asc())
            )
            result = await self._session.execute(stmt)
            events = list(result.scalars().all())
        except Exception as exc:
            logger.warning("BlockerDetector: chronic events query failed: %s", exc)
            return []

        if not events:
            return []

        # Join events → todos to recover work_type. Best-effort: a missing
        # todo (deleted) maps to task_type="" and still participates in
        # grouping so the chronic signal is not lost.
        todo_ids = {str(e.todo_id) for e in events}
        work_types: dict[str, str] = {}
        if self._todo_repo is not None:
            for tid in todo_ids:
                try:
                    t = await self._todo_repo.get_by_id(tid)
                except Exception:
                    t = None
                if t is not None:
                    work_types[tid] = str(getattr(t, "work_type", "") or "")

        # Bucket by (task_type, blocker_kind). blocker_kind is best-effort
        # from the event's reason text; fall back to "unknown".
        buckets: dict[tuple[str, str], list[TodoEventModel]] = defaultdict(list)
        for ev in events:
            tt = work_types.get(str(ev.todo_id), "")
            reason = str(getattr(ev, "reason", "") or "").lower()
            if "permission" in reason:
                bk = "permission_escalation"
            elif "input" in reason:
                bk = "human_input"
            elif "resource" in reason or "contention" in reason:
                bk = "resource_contention"
            else:
                bk = "unknown"
            buckets[(tt, bk)].append(ev)

        out: list[ChronicBlocker] = []
        for (tt, bk), evs in buckets.items():
            if len(evs) < self._config.min_chronic_incidents:
                continue
            out.append(
                ChronicBlocker(
                    task_type=tt,
                    blocker_kind=bk,
                    incident_count=len(evs),
                    first_seen=_safe_datetime(getattr(evs[0], "created_at", None)) or self._clock(),
                    last_seen=_safe_datetime(getattr(evs[-1], "created_at", None)) or self._clock(),
                    recent_todo_ids=[str(e.todo_id) for e in evs[-5:]],
                )
            )
        out.sort(key=lambda c: c.incident_count, reverse=True)
        return out

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _threshold_hours_for_kind(self, kind: str) -> int:
        if kind == "permission_escalation":
            return self._config.permission_escalation_block_hours
        # human_input + unknown + resource_contention all use the longer
        # human-input threshold by default.
        return self._config.human_input_block_hours

    async def _lookup_linked_human_todo(self, todo: Any) -> HumanTodoModel | None:
        """Find the open human-todo whose parent is this todo, if any."""
        if self._human_todo_repo is None:
            return None
        todo_id = str(getattr(todo, "todo_id", ""))
        if not todo_id:
            return None
        try:
            open_human_todos = await self._human_todo_repo.list_open()
        except Exception:
            return None
        for ht in open_human_todos:
            if str(getattr(ht, "parent_agent_todo_id", "") or "") == todo_id:
                return ht if isinstance(ht, HumanTodoModel) else None
        return None

    def _summary_for(self, todo: Any, linked: HumanTodoModel | None, kind: str) -> str:
        title = str(getattr(todo, "title", "") or "")[:80]
        if linked is not None:
            return (
                f"Todo '{title}' blocked on human (kind={kind}, "
                f"category={getattr(linked, 'category', '?')}). "
                f"Linked human-todo: {getattr(linked, 'title', '')[:80]}"
            )
        return f"Todo '{title}' blocked (kind={kind}). No linked human-todo."
