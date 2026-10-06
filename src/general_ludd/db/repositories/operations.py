"""Human-facing and remediation repository implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import (
    HumanTodoModel,
    QueueModel,
    RemediationActionModel,
    SlurmJobModel,
)
from general_ludd.db.repositories.shared import InvalidTransitionError, current_list_limit

HUMAN_TODO_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "done", "dismissed", "superseded"})
HUMAN_TODO_TERMINAL: frozenset[str] = frozenset({"done", "dismissed", "superseded"})
HUMAN_TODO_CATEGORIES: frozenset[str] = frozenset(
    {
        "permission_escalation",
        "external_action",
        "decision",
        "input_request",
        "blocker",
    }
)
HUMAN_TODO_PRIORITIES: frozenset[str] = frozenset({"low", "medium", "high", "urgent"})

_HUMAN_TODO_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "done", "dismissed", "superseded"}),
    "in_progress": frozenset({"done", "dismissed", "superseded", "open"}),
    "done": frozenset(),
    "dismissed": frozenset(),
    "superseded": frozenset(),
}


class HumanTodoRepository:
    """Persistence + state-machine for bot→human requests.

    Separate from :class:`TodoRepository` (agent todos). The link between the
    two is ``parent_agent_todo_id``: when a human-todo with a parent is filed,
    the parent agent todo transitions to ``blocked_on_human``; when the
    human-todo resolves (done/dismissed), the parent moves back to ``queued``
    (done) or ``cancelled`` (dismissed). The blocking integration is opt-in:
    a human-todo filed without ``parent_agent_todo_id`` is just a logged need.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the human-request state machine with a database session."""
        self._session = session

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in HUMAN_TODO_CATEGORIES:
            raise ValueError(f"invalid category {category!r}; must be one of: {sorted(HUMAN_TODO_CATEGORIES)}")

    @staticmethod
    def _validate_priority(priority: str) -> None:
        if priority not in HUMAN_TODO_PRIORITIES:
            raise ValueError(f"invalid priority {priority!r}; must be one of: {sorted(HUMAN_TODO_PRIORITIES)}")

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        allowed = _HUMAN_TODO_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise InvalidTransitionError(f"invalid human-todo transition: {current!r} -> {target!r}")

    async def create(
        self,
        *,
        agent_id: str,
        title: str,
        body: str,
        category: str,
        priority: str = "medium",
        parent_agent_todo_id: str | None = None,
        session_id: str | None = None,
        due_at: datetime | None = None,
        tags: list[str] | None = None,
    ) -> HumanTodoModel:
        """Validate, persist, and return an open human request.

        Raises:
            ValueError: If required text, category, or priority is invalid.
        """
        if not title or not title.strip():
            raise ValueError("title must not be empty")
        if not body or not body.strip():
            raise ValueError("body must not be empty")
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must not be empty")
        self._validate_category(category)
        self._validate_priority(priority)
        import json as _json

        row = HumanTodoModel(
            agent_id=agent_id,
            title=title.strip(),
            body=body,
            category=category,
            priority=priority,
            parent_agent_todo_id=parent_agent_todo_id,
            session_id=session_id,
            due_at=due_at,
            tags=_json.dumps(tags or []),
            status="open",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, human_todo_id: str) -> HumanTodoModel | None:
        """Return a human request by identifier."""
        stmt = select(HumanTodoModel).where(HumanTodoModel.id == human_todo_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_open(
        self,
        filter_category: str | None = None,
        filter_agent_id: str | None = None,
        filter_priority: str | None = None,
    ) -> list[HumanTodoModel]:
        """List bounded open human requests matching optional filters."""
        stmt = select(HumanTodoModel).where(HumanTodoModel.status == "open")
        if filter_category is not None:
            stmt = stmt.where(HumanTodoModel.category == filter_category)
        if filter_agent_id is not None:
            stmt = stmt.where(HumanTodoModel.agent_id == filter_agent_id)
        if filter_priority is not None:
            stmt = stmt.where(HumanTodoModel.priority == filter_priority)
        stmt = stmt.order_by(HumanTodoModel.created_at.asc()).limit(current_list_limit())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        agent_id: str | None = None,
    ) -> list[HumanTodoModel]:
        """List a bounded page of human requests matching optional filters."""
        stmt = select(HumanTodoModel)
        if status is not None:
            stmt = stmt.where(HumanTodoModel.status == status)
        if category is not None:
            stmt = stmt.where(HumanTodoModel.category == category)
        if priority is not None:
            stmt = stmt.where(HumanTodoModel.priority == priority)
        if agent_id is not None:
            stmt = stmt.where(HumanTodoModel.agent_id == agent_id)
        stmt = (
            stmt.order_by(HumanTodoModel.created_at.desc())
            .offset(max(0, offset))
            .limit(min(limit, current_list_limit()))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_changed_since(self, since: datetime) -> list[HumanTodoModel]:
        """List bounded human requests updated at or after a timestamp."""
        stmt = (
            select(HumanTodoModel)
            .where(HumanTodoModel.updated_at >= since)
            .order_by(HumanTodoModel.updated_at.asc())
            .limit(current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _transition(
        self,
        human_todo_id: str,
        target: str,
        *,
        human_resolver: str | None = None,
        resolution_text: str | None = None,
    ) -> HumanTodoModel:
        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        self._validate_transition(row.status, target)
        now = datetime.now(UTC)
        row.status = target
        row.updated_at = now
        if human_resolver is not None:
            row.human_resolver = human_resolver
        if resolution_text is not None:
            row.human_resolution = resolution_text
        if target in HUMAN_TODO_TERMINAL:
            row.resolved_at = now
        await self._session.flush()
        return row

    async def mark_done(
        self,
        human_todo_id: str,
        human_resolver: str,
        resolution_text: str,
    ) -> HumanTodoModel:
        """Resolve a human request with a nonempty resolution."""
        if not resolution_text or not resolution_text.strip():
            raise ValueError("resolution_text must not be empty")
        return await self._transition(
            human_todo_id,
            "done",
            human_resolver=human_resolver,
            resolution_text=resolution_text,
        )

    async def mark_in_progress(self, human_todo_id: str) -> HumanTodoModel:
        """Transition an open human request to in progress."""
        return await self._transition(human_todo_id, "in_progress")

    async def dismiss(
        self,
        human_todo_id: str,
        human_resolver: str,
        reason: str,
    ) -> HumanTodoModel:
        """Dismiss a human request with a nonempty reason."""
        if not reason or not reason.strip():
            raise ValueError("dismiss reason must not be empty")
        return await self._transition(
            human_todo_id,
            "dismissed",
            human_resolver=human_resolver,
            resolution_text=reason,
        )

    async def supersede(
        self,
        human_todo_id: str,
        new_id: str,
        reason: str,
    ) -> HumanTodoModel:
        """Mark a human request as replaced by another request."""
        return await self._transition(
            human_todo_id,
            "superseded",
            resolution_text=f"superseded by {new_id}: {reason}",
        )

    async def get_done_for_parent(self, parent_todo_id: str) -> HumanTodoModel | None:
        """Return the most-recently-resolved DONE human-todo for a parent agent todo.

        Filters in SQL (not Python) so callers are not loading every recent
        human-todo row per dispatch. E12: replaces the N+1 pattern where
        EventLoop._resolve_human_input_for_todo loaded all 50 human-todos and
        filtered in Python.
        """
        stmt = (
            select(HumanTodoModel)
            .where(
                HumanTodoModel.parent_agent_todo_id == parent_todo_id,
                HumanTodoModel.status == "done",
            )
            .order_by(
                HumanTodoModel.resolved_at.desc().nulls_last(),
                HumanTodoModel.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_tag(self, human_todo_id: str, tag: str) -> HumanTodoModel:
        """Add a tag to a human request if it is not already present."""
        import json as _json

        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        tags: list[str] = _json.loads(row.tags or "[]")
        if tag not in tags:
            tags.append(tag)
            row.tags = _json.dumps(tags)
            row.updated_at = datetime.now(UTC)
            await self._session.flush()
        return row

    async def remove_tag(self, human_todo_id: str, tag: str) -> HumanTodoModel:
        """Remove a tag from a human request if it is present."""
        import json as _json

        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        tags: list[str] = _json.loads(row.tags or "[]")
        if tag in tags:
            tags.remove(tag)
            row.tags = _json.dumps(tags)
            row.updated_at = datetime.now(UTC)
            await self._session.flush()
        return row

    async def search(self, query: str) -> list[HumanTodoModel]:
        """Search bounded human-request titles and bodies for literal text."""
        if not query or not query.strip():
            return []
        from sqlalchemy import or_

        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = (
            select(HumanTodoModel)
            .where(
                or_(
                    HumanTodoModel.title.like(like, escape="\\"),
                    HumanTodoModel.body.like(like, escape="\\"),
                )
            )
            .order_by(HumanTodoModel.created_at.desc())
            .limit(current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class RemediationActionRepository:
    """Persistence for remediation audit-trail rows.

    The dispatcher writes one row per action via :meth:`record`; operators
    read the history via :meth:`list_for_project` / :meth:`list_since`.
    Reads use the same session as the caller; writes ``flush`` so the row
    is visible in the caller's transaction without committing.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def record(
        self,
        *,
        blocked_todo_id: str,
        action_kind: str,
        blocker_kind: str,
        summary: str = "",
        detail: str = "{}",
        project_id: str | None = None,
        ok: bool = True,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> RemediationActionModel:
        """Persist and return a remediation action audit record."""
        row = RemediationActionModel(
            blocked_todo_id=blocked_todo_id,
            action_kind=action_kind,
            blocker_kind=blocker_kind,
            summary=summary,
            detail=detail,
            project_id=project_id,
            ok=ok,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, remediation_id: str) -> RemediationActionModel | None:
        """Return a remediation action by identifier."""
        stmt = select(RemediationActionModel).where(RemediationActionModel.id == remediation_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RemediationActionModel]:
        """List a bounded remediation-history page, optionally by project."""
        stmt = select(RemediationActionModel).order_by(RemediationActionModel.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(RemediationActionModel.project_id == project_id)
        stmt = stmt.offset(max(0, offset)).limit(min(limit, current_list_limit()))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_since(self, since: datetime, project_id: str | None = None) -> list[RemediationActionModel]:
        """List remediation actions recorded since a timestamp."""
        stmt = (
            select(RemediationActionModel)
            .where(RemediationActionModel.created_at >= since)
            .order_by(RemediationActionModel.created_at.asc())
            .limit(current_list_limit())
        )
        if project_id is not None:
            stmt = stmt.where(RemediationActionModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def exists_recent(self, blocked_todo_id: str, since: datetime) -> bool:
        """True if an action was already recorded for this blocked task since ``since``.

        Used by the auto-remediation tick phase (#52) for idempotency: a
        finding whose ``blocked_todo_id`` already has an audit row within the
        configured ``retry_delay_hours`` cooldown is skipped so a
        still-blocked task doesn't get a fresh dispatch/retry/human-todo
        filed on every single tick before the operator has had time to react.
        """
        stmt = (
            select(RemediationActionModel.id)
            .where(RemediationActionModel.blocked_todo_id == blocked_todo_id)
            .where(RemediationActionModel.created_at >= since)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def exists_recent_by_action(
        self,
        blocked_todo_id: str,
        action_kind: str,
        since: datetime,
    ) -> bool:
        """True if a (target, action) pair was already recorded since ``since``.

        C25: Dedupe on (action, target, window) — both ``blocked_todo_id``
        AND ``action_kind`` must match.  A prior ``schedule_retry`` on the
        same target is NOT a duplicate of a new ``dispatch_agent``.
        """
        stmt = (
            select(RemediationActionModel.id)
            .where(RemediationActionModel.blocked_todo_id == blocked_todo_id)
            .where(RemediationActionModel.action_kind == action_kind)
            .where(RemediationActionModel.created_at >= since)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_by_idempotency_key(self, idempotency_key: str) -> list[RemediationActionModel]:
        """List actions sharing an idempotency key in creation order."""
        stmt = (
            select(RemediationActionModel)
            .where(RemediationActionModel.idempotency_key == idempotency_key)
            .order_by(RemediationActionModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class QueueRepository:
    """Persist queue configuration and expose bounded queue listings."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> QueueModel:
        """Persist and return a queue configuration."""
        row = QueueModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_name(self, name: str) -> QueueModel | None:
        """Return a queue configuration by name."""
        stmt = select(QueueModel).where(QueueModel.queue_name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int | None = None, offset: int = 0) -> list[QueueModel]:
        """List a bounded page of queue configurations."""
        stmt = (
            select(QueueModel)
            .offset(offset)
            .limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_enabled(self, limit: int | None = None, offset: int = 0) -> list[QueueModel]:
        """List a bounded page of enabled queue configurations."""
        stmt = (
            select(QueueModel)
            .where(QueueModel.queue_enabled.is_(True))
            .offset(offset)
            .limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())



class SlurmJobRepository:
    """Persistence for Slurm job lifecycle tracking.

    Each row represents one Slurm job submitted by a daemon process.
    Used by the shutdown hook to scancel active jobs and by startup
    to detect orphaned jobs from a prior daemon instance.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> SlurmJobModel:
        """Persist and return a Slurm job record."""
        row = SlurmJobModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_job_id(self, job_id: str) -> SlurmJobModel | None:
        """Return a Slurm job by scheduler identifier."""
        stmt = select(SlurmJobModel).where(SlurmJobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        cost_incurred: float | None = None,
    ) -> bool:
        """Update status (and optional cost) for a Slurm job by job_id.

        Returns True if at least one row was updated, False otherwise.
        """
        from sqlalchemy import update as _update

        values: dict[str, Any] = {"status": status}
        if cost_incurred is not None:
            values["cost_incurred"] = cost_incurred
        if status in ("completed", "failed", "cancelled"):
            values["completed_at"] = datetime.now(UTC)
        guard = _update(SlurmJobModel).where(SlurmJobModel.job_id == job_id).values(**values)
        res = await self._session.execute(guard)
        await self._session.flush()
        return (cast("CursorResult[Any]", res).rowcount or 0) > 0

    async def list_active(self, daemon_pid: int | None = None) -> list[SlurmJobModel]:
        """Return jobs with status 'submitted' or 'running'.

        When ``daemon_pid`` is provided, only jobs matching that pid are returned.
        """
        stmt = (
            select(SlurmJobModel).where(SlurmJobModel.status.in_(["submitted", "running"])).limit(current_list_limit())
        )
        if daemon_pid is not None:
            stmt = stmt.where(SlurmJobModel.daemon_pid == daemon_pid)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_deployment(self, deployment_id: str) -> list[SlurmJobModel]:
        """List bounded Slurm jobs associated with a deployment."""
        stmt = select(SlurmJobModel).where(SlurmJobModel.deployment_id == deployment_id).limit(current_list_limit())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_orphans(self, current_pid: int) -> list[SlurmJobModel]:
        """Return jobs with status 'running' where daemon_pid != current_pid."""
        stmt = (
            select(SlurmJobModel)
            .where(
                SlurmJobModel.status == "running",
                SlurmJobModel.daemon_pid != current_pid,
            )
            .limit(current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


