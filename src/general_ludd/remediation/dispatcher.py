"""Remediation dispatcher — apply a strategy to a blocked task.

Reads :class:`BlockedTask` findings from the detector and chooses one of:

  - ``dispatch_agent`` — create a fresh QUEUED todo with the original task
    spec plus a note telling the new agent the prior attempt blocked and to
    try a different approach.
  - ``schedule_retry`` — schedule a one-shot retry in N hours via the
    existing todo scheduler (creates a SCHEDULED todo).
  - ``file_human_todo`` — file a high-priority HumanTodo with the blocker
    summary so the operator is re-pinged.
  - ``no_action`` — record the audit row only.

Every action — including ``no_action`` — is persisted as a
:class:`RemediationActionModel` audit row via
:class:`~general_ludd.db.repository.RemediationActionRepository` so the
operator can query the full history via
``GET /admin/remediation/history``.

The dispatcher is intentionally side-effect-only at the DB layer: it does
NOT call the model gateway / worker directly. ``dispatch_agent`` creates a
QUEUED todo that the event loop picks up on its next tick — the normal
claim → dispatch → review pipeline.
"""

from __future__ import annotations

import enum
import json as _json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from general_ludd.remediation.blocker_detector import (
    BlockedTask,
    RemediationConfig,
)
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)


class RemediationActionKind(enum.StrEnum):
    """The action the dispatcher took for one blocked task."""

    DISPATCH_AGENT = "dispatch_agent"
    SCHEDULE_RETRY = "schedule_retry"
    FILE_HUMAN_TODO = "file_human_todo"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class RemediationAction:
    """Outcome of one :meth:`RemediationDispatcher.remediate` call."""

    kind: RemediationActionKind
    blocked_todo_id: str
    project_id: str | None
    ok: bool
    summary: str
    # Ids of any newly-created rows (new todo, scheduled todo, human-todo).
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _new_todo_id() -> str:
    return f"TODO-{uuid4().hex[:8].upper()}"


def _new_scheduled_todo_id() -> str:
    return f"TODO-{uuid4().hex[:8].upper()}"


class RemediationDispatcher:
    """Apply the strategy chosen by :class:`BlockerDetector`.

    Args:
        detector:        the detector (read for the config + classifier).
        todo_repo:       TodoRepository — for creating retry / dispatch todos.
        human_todo_repo: HumanTodoRepository — for filing high-priority reminders.
        remediation_repo: RemediationActionRepository — audit trail.
        scheduler:       Optional TodoScheduler-compatible object exposing
                         ``schedule_one_shot(todo_data, fire_at)``. When None,
                         schedule_retry falls back to creating a SCHEDULED todo
                         directly via the repo (the event-loop scheduler will
                         promote it).
        clock:           Optional callable returning the current UTC datetime.
    """

    def __init__(
        self,
        detector: Any,
        todo_repo: Any,
        human_todo_repo: Any | None = None,
        remediation_repo: Any | None = None,
        scheduler: Any | None = None,
        *,
        clock: Any | None = None,
    ) -> None:
        self._detector = detector
        self._todo_repo = todo_repo
        self._human_todo_repo = human_todo_repo
        self._remediation_repo = remediation_repo
        self._scheduler = scheduler
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))

    @property
    def config(self) -> RemediationConfig:
        cfg = self._detector.config
        return cfg if isinstance(cfg, RemediationConfig) else RemediationConfig()

    async def remediate(
        self, blocked: BlockedTask, *, idempotency_key: str | None = None
    ) -> RemediationAction:
        """Apply the suggested remediation, persist the audit row, return outcome.

        Idempotency guard: if ``idempotency_key`` is set and a row with that
        key already exists in the remediation audit table, the call is a
        no-op — returns the previously-persisted outcome without re-acting.
        """
        if (
            idempotency_key is not None
            and self._remediation_repo is not None
        ):
            existing = await self._remediation_repo.find_by_idempotency_key(
                idempotency_key
            )
            if existing:
                return RemediationAction(
                    kind=RemediationActionKind.NO_ACTION,
                    blocked_todo_id=blocked.todo_id,
                    project_id=blocked.project_id,
                    ok=True,
                    summary="Idempotent replay — key already exists.",
                    detail={
                        "idempotent_replay": True,
                        "existing_action_id": str(getattr(existing[0], "id", "")),
                    },
                    reason=blocked.blocker_summary,
                )

        try:
            if blocked.suggested_remediation == "dispatch_agent":
                action = await self._dispatch_remediation_agent(blocked)
            elif blocked.suggested_remediation == "schedule_retry":
                action = await self._schedule_retry(blocked)
            elif blocked.suggested_remediation == "file_human_todo":
                action = await self._file_blocker_human_todo(blocked)
            else:
                action = RemediationAction(
                    kind=RemediationActionKind.NO_ACTION,
                    blocked_todo_id=blocked.todo_id,
                    project_id=blocked.project_id,
                    ok=True,
                    summary="No action required.",
                    reason=blocked.blocker_summary,
                )
        except Exception as exc:
            logger.exception(
                "RemediationDispatcher: action %s failed for %s",
                blocked.suggested_remediation,
                blocked.todo_id,
            )
            action = RemediationAction(
                kind=RemediationActionKind.NO_ACTION,
                blocked_todo_id=blocked.todo_id,
                project_id=blocked.project_id,
                ok=False,
                summary=f"Action {blocked.suggested_remediation!r} raised: {exc}",
                reason=str(exc),
            )

        await self._persist(blocked, action, idempotency_key=idempotency_key)
        return action

    # ------------------------------------------------------------------
    # strategies
    # ------------------------------------------------------------------

    async def _dispatch_remediation_agent(
        self, blocked: BlockedTask
    ) -> RemediationAction:
        """Create a fresh QUEUED todo retrying the same task.

        The new todo carries a note in its description that the prior attempt
        blocked and that the new agent should try a different approach or
        document why the block is unavoidable.
        """
        original = await self._todo_repo.get_by_id(blocked.todo_id)
        new_id = _new_todo_id()
        note = (
            f"[remediation] Prior attempt (todo {blocked.todo_id}) blocked on "
            f"{blocked.blocker_kind}: {blocked.blocker_summary}. "
            f"Try a different approach, or document why this is unavoidable."
        )
        if original is not None:
            description = (
                str(getattr(original, "description", "") or "") + "\n\n" + note
            )
            todo_data: dict[str, Any] = {
                "todo_id": new_id,
                "title": str(getattr(original, "title", "") or "remediation retry"),
                "description": description,
                "queue": str(getattr(original, "queue", "core") or "core"),
                "work_type": str(getattr(original, "work_type", "unknown") or "unknown"),
                "priority": int(getattr(original, "priority", 0) or 0),
                "project_id": getattr(original, "project_id", None),
                "parent_todo_id": blocked.todo_id,
                "status": TodoStatus.QUEUED.value,
                "tags": str(getattr(original, "tags", "[]") or "[]"),
            }
        else:
            todo_data = {
                "todo_id": new_id,
                "title": f"Remediation retry for {blocked.todo_id}",
                "description": note,
                "queue": "core",
                "work_type": blocked.task_type or "unknown",
                "priority": 0,
                "project_id": blocked.project_id,
                "parent_todo_id": None,
                "status": TodoStatus.QUEUED.value,
            }
        await self._todo_repo.create(todo_data)
        return RemediationAction(
            kind=RemediationActionKind.DISPATCH_AGENT,
            blocked_todo_id=blocked.todo_id,
            project_id=blocked.project_id,
            ok=True,
            summary=f"Dispatched fresh agent todo {new_id} (prior blocked on {blocked.blocker_kind}).",
            detail={"new_todo_id": new_id},
        )

    async def _schedule_retry(self, blocked: BlockedTask) -> RemediationAction:
        """Schedule a cron-style retry N hours from now."""
        delay = timedelta(hours=self.config.retry_delay_hours)
        fire_at = self._clock() + delay
        new_id = _new_scheduled_todo_id()
        original = await self._todo_repo.get_by_id(blocked.todo_id)
        todo_data = {
            "todo_id": new_id,
            "title": f"Retry: {blocked.blocker_summary[:120]}",
            "description": (
                f"Scheduled retry for blocked task {blocked.todo_id} "
                f"(kind={blocked.blocker_kind}). Re-evaluate after the "
                f"operator has had time to resolve the block."
            ),
            "queue": "core",
            "work_type": blocked.task_type or "unknown",
            "project_id": blocked.project_id,
            "parent_todo_id": blocked.todo_id if original is not None else None,
            "status": TodoStatus.SCHEDULED.value,
            "scheduled_at": fire_at,
        }
        # Prefer the scheduler's helper if present; otherwise the repo create
        # is enough — the event-loop TodoScheduler promotes SCHEDULED→QUEUED.
        if self._scheduler is not None and hasattr(self._scheduler, "schedule_one_shot"):
            await self._scheduler.schedule_one_shot(todo_data, fire_at)
        else:
            await self._todo_repo.create(todo_data)
        return RemediationAction(
            kind=RemediationActionKind.SCHEDULE_RETRY,
            blocked_todo_id=blocked.todo_id,
            project_id=blocked.project_id,
            ok=True,
            summary=f"Scheduled retry at {fire_at.isoformat()} (todo {new_id}).",
            detail={"scheduled_todo_id": new_id, "fire_at": fire_at.isoformat()},
        )

    async def _file_blocker_human_todo(
        self, blocked: BlockedTask
    ) -> RemediationAction:
        """File a high-priority reminder HumanTodo with the blocker summary."""
        if self._human_todo_repo is None:
            return RemediationAction(
                kind=RemediationActionKind.FILE_HUMAN_TODO,
                blocked_todo_id=blocked.todo_id,
                project_id=blocked.project_id,
                ok=False,
                summary="No human-todo repository wired; cannot file reminder.",
            )
        category = (
            "permission_escalation"
            if blocked.blocker_kind == "permission_escalation"
            else "blocker"
        )
        original = await self._todo_repo.get_by_id(blocked.todo_id)
        parent_id = blocked.todo_id if original is not None else None
        ht = await self._human_todo_repo.create(
            agent_id="remediation-system",
            title=f"[remediation] Escalation: {blocked.blocker_summary[:200]}",
            body=(
                f"Automated escalation after the block crossed its threshold.\n\n"
                f"Blocked todo: {blocked.todo_id}\n"
                f"Blocker kind: {blocked.blocker_kind}\n"
                f"Duration: {blocked.blocked_duration_seconds // 3600}h "
                f"({blocked.blocked_duration_seconds}s)\n\n"
                f"Summary: {blocked.blocker_summary}"
            ),
            category=category,
            priority="high",
            parent_agent_todo_id=parent_id,
            tags=["remediation", "auto-escalation"],
        )
        return RemediationAction(
            kind=RemediationActionKind.FILE_HUMAN_TODO,
            blocked_todo_id=blocked.todo_id,
            project_id=blocked.project_id,
            ok=True,
            summary=f"Filed high-priority human-todo {getattr(ht, 'id', '?')}.",
            detail={"human_todo_id": str(getattr(ht, "id", ""))},
        )

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------

    async def _persist(
        self,
        blocked: BlockedTask,
        action: RemediationAction,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        if self._remediation_repo is None:
            return
        try:
            await self._remediation_repo.record(
                blocked_todo_id=blocked.todo_id,
                action_kind=str(action.kind.value),
                blocker_kind=blocked.blocker_kind,
                summary=action.summary,
                detail=_json.dumps(action.detail, default=str),
                project_id=blocked.project_id,
                ok=action.ok,
                reason=action.reason,
                idempotency_key=idempotency_key,
            )
        except Exception:
            logger.exception(
                "RemediationDispatcher: failed to persist audit row for %s",
                blocked.todo_id,
            )
