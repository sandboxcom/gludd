"""Repository implementations for the agentic harness."""
from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import (
    AuditEventModel,
    BenchmarkResultModel,
    BucketLeaseModel,
    PromptProfileModel,
    ProjectModel,
    QueueModel,
    TaskDecisionModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
    VariableNamespaceModel,
)
from general_ludd.schemas.todo import TodoStatus

VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.QUEUED: {TodoStatus.ACTIVE, TodoStatus.FAILED, TodoStatus.BLOCKED},
    TodoStatus.ACTIVE: {TodoStatus.COMPLETE, TodoStatus.FAILED, TodoStatus.BLOCKED, TodoStatus.REVIEWING_RETURN, TodoStatus.MANUAL_HOLD, TodoStatus.NEEDS_MORE_WORK, TodoStatus.QUEUED},
    TodoStatus.REVIEWING_RETURN: {TodoStatus.COMPLETE, TodoStatus.NEEDS_MORE_WORK, TodoStatus.FAILED, TodoStatus.BLOCKED, TodoStatus.MANUAL_HOLD},
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
            raise ConcurrencyError(
                f"Version mismatch: expected {expected_version}, actual {todo.version}"
            )
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

    async def claim_runnable(
        self, limit: int = 10, project_id: str | None = None
    ) -> list[TodoModel]:
        stmt = (
            select(TodoModel)
            .where(TodoModel.status == TodoStatus.QUEUED.value)
        )
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

    async def transition(self, todo_id: str, new_status: TodoStatus, expected_version: int) -> TodoModel:
        todo = await self.get_by_id(todo_id)
        if todo is None:
            raise InvalidTransitionError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(
                f"Version mismatch: expected {expected_version}, actual {todo.version}"
            )
        current = TodoStatus(todo.status)
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Invalid transition: {current.value} -> {new_status.value}"
            )
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

    async def claim_unreviewed(self, project_id: str | None = None) -> list[TaskReturnModel]:
        stmt = select(TaskReturnModel).where(TaskReturnModel.reviewed == False)
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        stmt = stmt.order_by(TaskReturnModel.created_at.asc()).limit(10)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            row.reviewed = True
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
            .where(AuditEventModel.entity_type == entity_type)
            .where(AuditEventModel.entity_id == entity_id)
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
        stmt = select(VariableNamespaceModel)
        if project_id is not None:
            stmt = stmt.where(
                (VariableNamespaceModel.project_id == project_id)
                | (VariableNamespaceModel.project_id == None)
            )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        result_dict: dict[str, str] = {}
        global_vars: dict[str, str] = {}
        project_vars: dict[str, str] = {}
        for row in rows:
            entry = {row.var_key: row.var_value}
            if row.project_id is None:
                global_vars.update(entry)
            elif row.project_id == project_id:
                project_vars.update(entry)
        result_dict.update(global_vars)
        result_dict.update(project_vars)
        return result_dict

    async def create_namespace(self, namespace: str, project_id: str | None = None) -> VariableNamespaceModel:
        row = VariableNamespaceModel(namespace=namespace, project_id=project_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_var(self, namespace: str, key: str, value: str, project_id: str | None = None) -> VariableNamespaceModel:
        row = VariableNamespaceModel(
            namespace=namespace,
            var_key=key,
            var_value=value,
            project_id=project_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row


class BenchmarkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_result(self, data: dict[str, Any]) -> BenchmarkResultModel:
        row = BenchmarkResultModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_aggregate_scores(self, task_type: str | None = None) -> list[dict[str, Any]]:
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
            )
            .group_by(
                BenchmarkResultModel.prompt_profile_id,
                BenchmarkResultModel.model_profile_id,
                BenchmarkResultModel.task_type,
            )
        )
        if task_type is not None:
            stmt = stmt.where(BenchmarkResultModel.task_type == task_type)
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            {
                "prompt_profile_id": row.prompt_profile_id,
                "model_profile_id": row.model_profile_id,
                "task_type": row.task_type,
                "avg_completion": row.avg_completion,
                "avg_quality": row.avg_quality,
                "avg_instruction": row.avg_instruction,
                "avg_efficiency": row.avg_efficiency,
                "sample_count": row.sample_count,
            }
            for row in rows
        ]

    async def get_best_for_task(self, task_type: str, min_samples: int = 3) -> list[dict[str, Any]]:
        scores = await self.get_aggregate_scores(task_type=task_type)
        return [s for s in scores if s["sample_count"] >= min_samples]

    async def get_model_scores(self, model_profile_id: str) -> list[BenchmarkResultModel]:
        stmt = (
            select(BenchmarkResultModel)
            .where(BenchmarkResultModel.model_profile_id == model_profile_id)
            .order_by(BenchmarkResultModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent(self, limit: int = 50) -> list[BenchmarkResultModel]:
        stmt = (
            select(BenchmarkResultModel)
            .order_by(BenchmarkResultModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


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
        stmt = select(PromptProfileModel).where(
            PromptProfileModel.task_types.contains(task_type)
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

    async def list_all(self) -> list[QueueModel]:
        result = await self._session.execute(select(QueueModel))
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
