"""Repository implementations for the agentic harness."""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import (
    AgentMessageModel,
    AuditEventModel,
    BenchmarkResultModel,
    ProjectModel,
    PromptProfileModel,
    QueueModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.schemas.todo import TodoStatus

VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.BACKLOG: {TodoStatus.QUEUED},
    TodoStatus.QUEUED: {TodoStatus.ACTIVE, TodoStatus.FAILED, TodoStatus.BLOCKED},
    TodoStatus.ACTIVE: {
        TodoStatus.COMPLETE, TodoStatus.FAILED, TodoStatus.BLOCKED,
        TodoStatus.REVIEWING_RETURN, TodoStatus.MANUAL_HOLD,
        TodoStatus.NEEDS_MORE_WORK, TodoStatus.QUEUED,
    },
    TodoStatus.REVIEWING_RETURN: {
        TodoStatus.COMPLETE, TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.FAILED, TodoStatus.BLOCKED, TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.NEEDS_MORE_WORK: {TodoStatus.QUEUED, TodoStatus.ACTIVE},
    TodoStatus.MANUAL_HOLD: {TodoStatus.QUEUED, TodoStatus.ACTIVE},
    TodoStatus.BLOCKED: {TodoStatus.QUEUED},
    TodoStatus.FAILED: {TodoStatus.QUEUED},
    TodoStatus.COMPLETE: set(),
}


class ConcurrencyError(Exception):
    pass


class InvalidTransitionError(ConcurrencyError):
    pass


class TodoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, todo_data: dict[str, Any]) -> TodoModel:
        todo = TodoModel(**todo_data)
        self._session.add(todo)
        await self._session.flush()
        return todo

    async def get_by_id(self, todo_id: str, project_id: str | None = None) -> TodoModel | None:
        stmt = select(TodoModel).where(TodoModel.todo_id == todo_id)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, todo_id: str, updates: dict[str, Any], expected_version: int) -> TodoModel:
        todo = await self.get_by_id(todo_id)
        if todo is None:
            raise InvalidTransitionError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(f"Version mismatch: expected {expected_version}, actual {todo.version}")
        for key, value in updates.items():
            setattr(todo, key, value)
        todo.version = expected_version + 1
        todo.updated_at = datetime.now(UTC)
        await self._session.flush()
        return todo

    async def list_by_status(
        self, status: TodoStatus, project_id: str | None = None
    ) -> list[TodoModel]:
        stmt = select(TodoModel).where(TodoModel.status == status.value)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        queue: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[TodoModel]:
        stmt = select(TodoModel)
        if queue is not None:
            stmt = stmt.where(TodoModel.queue == queue)
        if status is not None:
            stmt = stmt.where(TodoModel.status == status)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim_runnable(self, limit: int = 10, project_id: str | None = None) -> list[TodoModel]:
        stmt = select(TodoModel).where(TodoModel.status == TodoStatus.QUEUED.value)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        stmt = stmt.limit(limit)
        with contextlib.suppress(Exception):
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        todos = list(result.scalars().all())
        now = datetime.now(UTC)
        for todo in todos:
            old_status = todo.status
            todo.status = TodoStatus.ACTIVE.value
            todo.version += 1
            todo.updated_at = now
            evt = TodoEventModel(
                todo_id=todo.todo_id,
                event_type="status_change",
                old_status=old_status,
                new_status=TodoStatus.ACTIVE.value,
                actor="claim_runnable",
                reason="Claimed for execution",
            )
            self._session.add(evt)
        await self._session.flush()
        return todos

    async def count_active(self) -> int:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(TodoModel).where(
            TodoModel.status == TodoStatus.ACTIVE.value
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def status_summary(self, project_id: str | None = None) -> dict[str, Any]:
        """Aggregate todo facts: counts by status / queue / work_type, oldest age,
        backlog size. Reused by the /api/facts aggregation endpoint."""
        stmt = select(TodoModel)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        by_status: dict[str, int] = {}
        by_queue: dict[str, int] = {}
        by_work_type: dict[str, int] = {}
        oldest_created: datetime | None = None
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_queue[r.queue] = by_queue.get(r.queue, 0) + 1
            by_work_type[r.work_type] = by_work_type.get(r.work_type, 0) + 1
            created = r.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                if oldest_created is None or created < oldest_created:
                    oldest_created = created
        oldest_age_seconds: float | None = None
        if oldest_created is not None:
            oldest_age_seconds = (datetime.now(UTC) - oldest_created).total_seconds()
        backlog = by_status.get(TodoStatus.BACKLOG.value, 0) + by_status.get(
            TodoStatus.QUEUED.value, 0
        )
        return {
            "total": len(rows),
            "by_status": by_status,
            "by_queue": by_queue,
            "by_work_type": by_work_type,
            "oldest_age_seconds": oldest_age_seconds,
            "backlog_size": backlog,
        }

    async def transition(self, todo_id: str, new_status: TodoStatus, expected_version: int) -> TodoModel:
        todo = await self.get_by_id(todo_id)
        if todo is None:
            raise InvalidTransitionError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(f"Version mismatch: expected {expected_version}, actual {todo.version}")
        current = TodoStatus(todo.status)
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidTransitionError(f"Invalid transition: {current.value} -> {new_status.value}")
        todo.status = new_status.value
        todo.version = expected_version + 1
        todo.updated_at = datetime.now(UTC)
        await self._session.flush()
        return todo


class TaskReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> TaskReturnModel:
        row = TaskReturnModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, return_id: str) -> TaskReturnModel | None:
        stmt = select(TaskReturnModel).where(TaskReturnModel.return_id == return_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def work_summary(self, project_id: str | None = None) -> dict[str, Any]:
        """In-flight/claimed task-return counts by status / queue / work_type.

        Task returns represent dispatched work; this is the "work" facet of
        /api/facts. Reused, not duplicated, by the facts endpoint."""
        stmt = select(TaskReturnModel)
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        by_status: dict[str, int] = {}
        by_queue: dict[str, int] = {}
        by_work_type: dict[str, int] = {}
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_queue[r.queue] = by_queue.get(r.queue, 0) + 1
            by_work_type[r.work_type] = by_work_type.get(r.work_type, 0) + 1
        return {
            "total": len(rows),
            "by_status": by_status,
            "by_queue": by_queue,
            "by_work_type": by_work_type,
        }

    async def history_summary(
        self, project_id: str | None = None, recent_limit: int = 10
    ) -> dict[str, Any]:
        """Recent returns + success/failure rates (exit_code 0 == success)."""
        stmt = select(TaskReturnModel)
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        stmt = stmt.order_by(TaskReturnModel.created_at.desc())
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        total = len(rows)
        successes = sum(1 for r in rows if r.exit_code == 0)
        failures = total - successes
        recent = [
            {
                "return_id": r.return_id,
                "playbook": r.playbook,
                "status": r.status,
                "exit_code": r.exit_code,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in rows[:recent_limit]
        ]
        return {
            "total_returns": total,
            "success_count": successes,
            "failure_count": failures,
            "success_rate": (successes / total) if total else 0.0,
            "recent": recent,
        }

    async def claim_unreviewed(self, project_id: str | None = None, limit: int = 10) -> list[TaskReturnModel]:
        stmt = select(TaskReturnModel).where(TaskReturnModel.status == "created")
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        stmt = stmt.order_by(TaskReturnModel.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "claimed_for_review"
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return rows


class AuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: str | None = None,
    ) -> AuditEventModel:
        row = AuditEventModel(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=details,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_by_entity(self, entity_type: str, entity_id: str, limit: int = 50) -> list[AuditEventModel]:
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.entity_type == entity_type, AuditEventModel.entity_id == entity_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(self, project_id: str, limit: int = 50) -> list[AuditEventModel]:
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.project_id == project_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class VariableNamespaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_vars_for_project(self, project_id: str | None) -> dict[str, str]:
        stmt = (
            select(VariableValueModel)
            .join(VariableNamespaceModel)
            .where(
                (VariableNamespaceModel.project_id == project_id)
                | (VariableNamespaceModel.project_id.is_(None))
            )
            .order_by(
                VariableNamespaceModel.project_id.is_(None).desc()
            )
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        result_dict: dict[str, str] = {}
        for row in rows:
            result_dict[row.key] = row.value
        return result_dict

    async def create_namespace(self, namespace: str, project_id: str | None = None) -> VariableNamespaceModel:
        row = VariableNamespaceModel(namespace=namespace, project_id=project_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_var(
        self, namespace: str, key: str, value: str, project_id: str | None = None
    ) -> VariableValueModel:
        stmt = select(VariableNamespaceModel).where(
            VariableNamespaceModel.namespace == namespace,
            VariableNamespaceModel.project_id == project_id,
        )
        result = await self._session.execute(stmt)
        ns = result.scalar_one_or_none()
        if ns is None:
            ns = VariableNamespaceModel(namespace=namespace, project_id=project_id)
            self._session.add(ns)
            await self._session.flush()
        existing = await self._session.execute(
            select(VariableValueModel).where(
                VariableValueModel.namespace_id == ns.id,
                VariableValueModel.key == key,
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            row.value = value
            row.updated_at = datetime.now(UTC)
        else:
            row = VariableValueModel(namespace_id=ns.id, key=key, value=value)
            self._session.add(row)
        await self._session.flush()
        return row


class BenchmarkRepository:
    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory

    async def _execute_with_session(self, fn: Callable[[AsyncSession], Any]) -> Any:
        if self._session_factory is not None:
            async with self._session_factory() as session, session.begin():
                    result = await fn(session)
                    if hasattr(result, "_sa_instance_state"):
                        session.expunge(result)
                    return result
        if self._session is not None:
            return await fn(self._session)
        raise RuntimeError("BenchmarkRepository: no session or session_factory")

    async def record_result(self, data: dict[str, Any]) -> BenchmarkResultModel:
        async def _do(session: AsyncSession) -> BenchmarkResultModel:
            row = BenchmarkResultModel(**data)
            session.add(row)
            await session.flush()
            return row
        return cast(BenchmarkResultModel, await self._execute_with_session(_do))

    async def get_aggregate_scores(self, task_type: str | None = None) -> list[dict[str, Any]]:
        async def _do(session: AsyncSession) -> list[dict[str, Any]]:
            from sqlalchemy import func
            stmt = (
                select(
                    BenchmarkResultModel.prompt_profile_id,
                    BenchmarkResultModel.model_profile_id,
                    BenchmarkResultModel.task_type,
                    func.avg(BenchmarkResultModel.completion_score).label("avg_completion"),
                    func.avg(BenchmarkResultModel.code_quality_score).label("avg_quality"),
                    func.avg(BenchmarkResultModel.instruction_adherence_score).label("avg_instruction"),
                    func.avg(BenchmarkResultModel.token_efficiency_score).label("avg_efficiency"),
                    func.count().label("sample_count"),
                    func.avg(
                        BenchmarkResultModel.completion_score * 0.4
                        + BenchmarkResultModel.code_quality_score * 0.3
                        + BenchmarkResultModel.instruction_adherence_score * 0.2
                        + BenchmarkResultModel.token_efficiency_score * 0.1
                    ).label("composite_score"),
                )
                .where(BenchmarkResultModel.success.is_(True))
                .group_by(
                    BenchmarkResultModel.prompt_profile_id,
                    BenchmarkResultModel.model_profile_id,
                    BenchmarkResultModel.task_type,
                )
            )
            if task_type is not None:
                stmt = stmt.where(BenchmarkResultModel.task_type == task_type)
            result = await session.execute(stmt)
            rows = result.all()
            return [
                {
                    "prompt_profile_id": r.prompt_profile_id,
                    "model_profile_id": r.model_profile_id,
                    "task_type": r.task_type,
                    "avg_completion": r.avg_completion,
                    "avg_quality": r.avg_quality,
                    "avg_instruction": r.avg_instruction,
                    "avg_efficiency": r.avg_efficiency,
                    "sample_count": r.sample_count,
                    "composite_score": getattr(r, "composite_score", None),
                }
                for r in rows
            ]
        return cast("list[dict[str, Any]]", await self._execute_with_session(_do))

    async def get_best_for_task(self, task_type: str, min_samples: int = 3) -> list[dict[str, Any]]:
        scores = await self.get_aggregate_scores(task_type=task_type)
        filtered = [s for s in scores if s["sample_count"] >= min_samples]
        filtered.sort(key=lambda s: s.get("composite_score", 0) or 0, reverse=True)
        return filtered

    async def get_model_scores(self, model_profile_id: str) -> list[BenchmarkResultModel]:
        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            stmt = (
                select(BenchmarkResultModel)
                .where(BenchmarkResultModel.model_profile_id == model_profile_id)
                .order_by(BenchmarkResultModel.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))

    async def list_recent(self, limit: int = 50) -> list[BenchmarkResultModel]:
        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            stmt = (
                select(BenchmarkResultModel)
                .order_by(BenchmarkResultModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))


class PromptProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, data: dict[str, Any]) -> PromptProfileModel:
        existing = await self.get_by_name(data["name"])
        if existing is not None:
            for key, value in data.items():
                setattr(existing, key, value)
            existing.updated_at = datetime.now(UTC)
            await self._session.flush()
            return existing
        row = PromptProfileModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_name(self, name: str) -> PromptProfileModel | None:
        stmt = select(PromptProfileModel).where(PromptProfileModel.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: str) -> PromptProfileModel | None:
        stmt = select(PromptProfileModel).where(PromptProfileModel.id == profile_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[PromptProfileModel]:
        stmt = select(PromptProfileModel)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_source(self, source: str) -> list[PromptProfileModel]:
        stmt = select(PromptProfileModel).where(PromptProfileModel.source == source)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_task_type(self, task_type: str) -> list[PromptProfileModel]:
        from sqlalchemy import or_
        stmt = select(PromptProfileModel).where(
            or_(
                PromptProfileModel.task_types.contains(task_type),
                PromptProfileModel.task_types == "[]",
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class QueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> QueueModel:
        row = QueueModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_name(self, name: str) -> QueueModel | None:
        stmt = select(QueueModel).where(QueueModel.queue_name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[QueueModel]:
        result = await self._session.execute(select(QueueModel))
        return list(result.scalars().all())

    async def list_enabled(self) -> list[QueueModel]:
        stmt = select(QueueModel).where(QueueModel.queue_enabled.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> ProjectModel:
        row = ProjectModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, project_id: str) -> ProjectModel | None:
        stmt = select(ProjectModel).where(ProjectModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[ProjectModel]:
        stmt = select(ProjectModel).where(ProjectModel.active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, project_id: str) -> None:
        project = await self.get_by_id(project_id)
        if project is not None:
            project.active = False
            await self._session.flush()


BROADCAST_RECIPIENT = "broadcast"


class AgentMessageRepository:
    """Persistence for the inter-agent message queue (AgentMessageModel)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def send(self, data: dict[str, Any]) -> AgentMessageModel:
        row = AgentMessageModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, message_id: str) -> AgentMessageModel | None:
        stmt = select(AgentMessageModel).where(AgentMessageModel.id == message_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def inbox(
        self,
        recipient: str,
        unread_only: bool = True,
        include_broadcast: bool = True,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentMessageModel]:
        """Return messages addressed to ``recipient`` (and broadcasts).

        Expired messages (past their ttl) are never returned.
        """
        from sqlalchemy import or_

        target: Any
        if include_broadcast:
            target = AgentMessageModel.recipient.in_([recipient, BROADCAST_RECIPIENT])
        else:
            target = AgentMessageModel.recipient == recipient
        stmt = select(AgentMessageModel).where(target)
        if unread_only:
            stmt = stmt.where(AgentMessageModel.read_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        stmt = stmt.order_by(AgentMessageModel.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(UTC)
        return [r for r in rows if not self._is_expired(r, now)]

    async def ack(self, message_id: str) -> AgentMessageModel | None:
        """Mark a message read. Returns the row, or None if it does not exist."""
        row = await self.get_by_id(message_id)
        if row is None:
            return None
        if row.read_at is None:
            row.read_at = datetime.now(UTC)
            await self._session.flush()
        return row

    async def purge_expired(self) -> int:
        """Delete every message whose ttl has elapsed. Returns the count purged."""
        stmt = select(AgentMessageModel).where(AgentMessageModel.ttl_seconds.isnot(None))
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(UTC)
        purged = 0
        for row in rows:
            if self._is_expired(row, now):
                await self._session.delete(row)
                purged += 1
        if purged:
            await self._session.flush()
        return purged

    async def unread_counts(self, project_id: str | None = None) -> dict[str, int]:
        """Per-recipient unread counts (excludes expired). Used by /api/facts."""
        stmt = select(AgentMessageModel).where(AgentMessageModel.read_at.is_(None))
        if project_id is not None:
            from sqlalchemy import or_
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(UTC)
        counts: dict[str, int] = {}
        for row in rows:
            if self._is_expired(row, now):
                continue
            counts[row.recipient] = counts.get(row.recipient, 0) + 1
        return counts

    @staticmethod
    def _is_expired(row: AgentMessageModel, now: datetime) -> bool:
        if row.ttl_seconds is None:
            return False
        created = row.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (now - created).total_seconds() > row.ttl_seconds
