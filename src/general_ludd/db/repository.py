"""Repository pattern classes for database access."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from general_ludd.db.models import (
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
        stmt = select(QueueModel).where(QueueModel.queue_enabled.is_(True))
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
        stmt = select(ProjectModel).where(ProjectModel.active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, project_id: str) -> None:
        project = await self.get_by_id(project_id)
        if project is not None:
            project.active = False
            await self._session.flush()


class AuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        *,
        project_id: str | None = None,
        actor: str = "agent",
        correlation_id: str | None = None,
        details: str = "{}",
    ) -> AuditEventModel:
        audit = AuditEventModel(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            actor=actor,
            correlation_id=correlation_id,
            details=details,
        )
        self._session.add(audit)
        await self._session.flush()
        return audit

    async def list_by_entity(
        self, entity_type: str, entity_id: str, limit: int = 50
    ) -> list[AuditEventModel]:
        stmt = (
            select(AuditEventModel)
            .where(
                AuditEventModel.entity_type == entity_type,
                AuditEventModel.entity_id == entity_id,
            )
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(
        self, project_id: str, limit: int = 100
    ) -> list[AuditEventModel]:
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

    async def load_vars_for_project(self, project_id: str | None = None) -> dict[str, str]:
        stmt = select(VariableNamespaceModel).options(selectinload(VariableNamespaceModel.values))
        if project_id is not None:
            stmt = stmt.where(
                (VariableNamespaceModel.project_id == project_id)
                | (VariableNamespaceModel.project_id.is_(None))
            )
        else:
            stmt = stmt.where(VariableNamespaceModel.project_id.is_(None))
        result = await self._session.execute(stmt)
        namespaces = list(result.scalars().all())
        merged: dict[str, str] = {}
        for ns in namespaces:
            if ns.project_id is not None:
                continue
            for v in ns.values:
                merged[v.key] = v.value
        for ns in namespaces:
            if ns.project_id is None:
                continue
            for v in ns.values:
                merged[v.key] = v.value
        return merged

    async def create_namespace(
        self, namespace: str, project_id: str | None = None, description: str = ""
    ) -> VariableNamespaceModel:
        ns = VariableNamespaceModel(
            namespace=namespace,
            project_id=project_id,
            description=description,
        )
        self._session.add(ns)
        await self._session.flush()
        return ns

    async def set_var(
        self, namespace_id: int, key: str, value: str, value_type: str = "string"
    ) -> VariableValueModel:
        stmt = select(VariableValueModel).where(
            VariableValueModel.namespace_id == namespace_id,
            VariableValueModel.key == key,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.value = value
            existing.value_type = value_type
            await self._session.flush()
            return existing
        var = VariableValueModel(
            namespace_id=namespace_id,
            key=key,
            value=value,
            value_type=value_type,
        )
        self._session.add(var)
        await self._session.flush()
        return var


class PromptProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        name: str,
        source: str,
        prompt_text: str,
        source_url: str = "",
        task_types: list[str] | None = None,
        tags: list[str] | None = None,
        version: str = "latest",
    ) -> PromptProfileModel:
        result = await self._session.execute(
            select(PromptProfileModel).where(PromptProfileModel.name == name)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.source = source
            existing.source_url = source_url
            existing.prompt_text = prompt_text
            existing.task_types = json.dumps(task_types or [])
            existing.tags = json.dumps(tags or [])
            existing.version = version
            await self._session.flush()
            return existing
        profile = PromptProfileModel(
            name=name,
            source=source,
            source_url=source_url,
            prompt_text=prompt_text,
            task_types=json.dumps(task_types or []),
            tags=json.dumps(tags or []),
            version=version,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get_by_name(self, name: str) -> PromptProfileModel | None:
        result = await self._session.execute(
            select(PromptProfileModel).where(PromptProfileModel.name == name)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: str) -> PromptProfileModel | None:
        result = await self._session.execute(
            select(PromptProfileModel).where(PromptProfileModel.id == profile_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[PromptProfileModel]:
        result = await self._session.execute(
            select(PromptProfileModel).order_by(PromptProfileModel.name)
        )
        return list(result.scalars().all())

    async def list_by_source(self, source: str) -> list[PromptProfileModel]:
        result = await self._session.execute(
            select(PromptProfileModel)
            .where(PromptProfileModel.source == source)
            .order_by(PromptProfileModel.name)
        )
        return list(result.scalars().all())

    async def list_for_task_type(self, task_type: str) -> list[PromptProfileModel]:
        result = await self._session.execute(select(PromptProfileModel))
        profiles = list(result.scalars().all())
        return [
            p
            for p in profiles
            if task_type in json.loads(p.task_types)
            or not json.loads(p.task_types)
        ]


class BenchmarkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_result(
        self,
        model_profile_id: str,
        task_type: str,
        scores: dict[str, float],
        success: bool,
        prompt_profile_id: str | None = None,
        task_description: str = "",
        time_seconds: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        error_message: str = "",
        raw_output: str = "",
    ) -> BenchmarkResultModel:
        row = BenchmarkResultModel(
            prompt_profile_id=prompt_profile_id,
            model_profile_id=model_profile_id,
            task_type=task_type,
            task_description=task_description,
            completion_score=scores.get("completion", 0.0),
            code_quality_score=scores.get("code_quality", 0.0),
            instruction_adherence_score=scores.get("instruction", 0.0),
            token_efficiency_score=scores.get("token_efficiency", 0.0),
            time_seconds=time_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            success=success,
            error_message=error_message,
            raw_output=raw_output,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_aggregate_scores(
        self, task_type: str | None = None
    ) -> list[dict[str, Any]]:
        cols = [
            BenchmarkResultModel.prompt_profile_id,
            BenchmarkResultModel.model_profile_id,
            BenchmarkResultModel.task_type,
            func.count(BenchmarkResultModel.id).label("sample_count"),
            func.avg(BenchmarkResultModel.completion_score).label("avg_completion"),
            func.avg(BenchmarkResultModel.code_quality_score).label("avg_code_quality"),
            func.avg(BenchmarkResultModel.instruction_adherence_score).label(
                "avg_instruction"
            ),
            func.avg(BenchmarkResultModel.token_efficiency_score).label(
                "avg_token_efficiency"
            ),
            func.avg(BenchmarkResultModel.cost_usd).label("avg_cost"),
            func.avg(
                BenchmarkResultModel.completion_score * 0.35
                + BenchmarkResultModel.code_quality_score * 0.25
                + BenchmarkResultModel.instruction_adherence_score * 0.25
                + BenchmarkResultModel.token_efficiency_score * 0.15
            ).label("composite_score"),
        ]
        q = select(*cols).where(BenchmarkResultModel.success.is_(True))
        if task_type is not None:
            q = q.where(BenchmarkResultModel.task_type == task_type)
        q = q.group_by(
            BenchmarkResultModel.prompt_profile_id,
            BenchmarkResultModel.model_profile_id,
            BenchmarkResultModel.task_type,
        )
        result = await self._session.execute(q)
        return [dict(row._mapping) for row in result.all()]

    async def get_best_for_task(
        self, task_type: str, min_samples: int = 3
    ) -> dict[str, Any] | None:
        aggregates = await self.get_aggregate_scores(task_type=task_type)
        qualified = [a for a in aggregates if a["sample_count"] >= min_samples]
        if not qualified:
            return None
        return max(qualified, key=lambda a: float(a["composite_score"]))

    async def get_model_scores(
        self, model_profile_id: str
    ) -> list[dict[str, Any]]:
        return [
            a
            for a in await self.get_aggregate_scores()
            if a["model_profile_id"] == model_profile_id
        ]

    async def list_recent(
        self, limit: int = 50
    ) -> list[BenchmarkResultModel]:
        result = await self._session.execute(
            select(BenchmarkResultModel)
            .order_by(BenchmarkResultModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
