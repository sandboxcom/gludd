"""Repository pattern classes for database access."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import (
    ProjectModel,
    QueueModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
)
from general_ludd.schemas.todo import VALID_TRANSITIONS, TodoStatus


class ConcurrencyError(Exception):
    pass


class InvalidTransitionError(Exception):
    pass


class TodoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, todo_data: dict[str, Any]) -> TodoModel:
        todo = TodoModel(**todo_data)
        self._session.add(todo)
        await self._session.flush()
        return todo

    async def get_by_id(self, todo_id: str) -> TodoModel | None:
        stmt = select(TodoModel).where(TodoModel.todo_id == todo_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, todo_id: str, updates: dict[str, Any], expected_version: int) -> TodoModel:
        todo = await self.get_by_id(todo_id)
        if todo is None:
            raise ConcurrencyError(f"Todo {todo_id} not found")
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
            raise ConcurrencyError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(
                f"Version mismatch: expected {expected_version}, actual {todo.version}"
            )
        current = TodoStatus(todo.status)
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Invalid transition from {current.value} to {new_status.value}"
            )
        old_status = todo.status
        todo.status = new_status.value
        todo.version = expected_version + 1
        todo.updated_at = datetime.now(UTC)
        if new_status == TodoStatus.COMPLETE:
            todo.completed_at = datetime.now(UTC)
        evt = TodoEventModel(
            todo_id=todo.todo_id,
            event_type="status_change",
            old_status=old_status,
            new_status=new_status.value,
            actor="transition",
        )
        self._session.add(evt)
        await self._session.flush()
        return todo


class TaskReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> TaskReturnModel:
        tr = TaskReturnModel(**data)
        self._session.add(tr)
        await self._session.flush()
        return tr

    async def get_by_id(self, return_id: str) -> TaskReturnModel | None:
        stmt = select(TaskReturnModel).where(TaskReturnModel.return_id == return_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def claim_unreviewed(
        self, limit: int = 10, project_id: str | None = None
    ) -> list[TaskReturnModel]:
        stmt = (
            select(TaskReturnModel)
            .where(TaskReturnModel.status == "created")
        )
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        stmt = stmt.limit(limit)
        with contextlib.suppress(Exception):
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        returns = list(result.scalars().all())
        for tr in returns:
            tr.status = "claimed_for_review"
        await self._session.flush()
        return returns


class QueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_name(self, name: str) -> QueueModel | None:
        stmt = select(QueueModel).where(QueueModel.queue_name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_enabled(self) -> list[QueueModel]:
        stmt = select(QueueModel).where(QueueModel.queue_enabled == True)  # noqa: E712
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> ProjectModel:
        project = ProjectModel(**data)
        self._session.add(project)
        await self._session.flush()
        return project

    async def get_by_id(self, project_id: str) -> ProjectModel | None:
        stmt = select(ProjectModel).where(ProjectModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[ProjectModel]:
        stmt = select(ProjectModel).where(ProjectModel.active == True)  # noqa: E712
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, project_id: str) -> None:
        project = await self.get_by_id(project_id)
        if project is not None:
            project.active = False
            await self._session.flush()
