"""Repository implementations for the agentic harness."""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import (
    AgentMessageModel,
    AuditEventModel,
    AuditEventType,
    BenchmarkResultModel,
    FeatureModel,
    FeatureStatus,
    ProjectModel,
    PromptProfileModel,
    QueueModel,
    RoleRunModel,
    SpendRecordModel,
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


def _is_sqlite_busy(exc: OperationalError) -> bool:
    """True when an OperationalError is a SQLITE_BUSY ('database is locked').

    SQLite raises this when a write cannot acquire the database lock under
    contention. In the claim path it means "another caller is mid-write" — a
    lost race to be skipped, not a fault to propagate. A non-lock
    OperationalError (e.g. a schema error) returns False so it bubbles up.
    """
    return "database is locked" in str(exc).lower()


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

    async def update(
        self,
        todo_id: str,
        updates: dict[str, Any],
        expected_version: int,
        project_id: str | None = None,
    ) -> TodoModel:
        """Optimistically-locked, optionally project-scoped update.

        The UPDATE is guarded on ``todo_id`` (+ project_id when given) AND
        ``version == expected_version``. If a concurrent writer already bumped
        the version (or the row is owned by a different project), the guarded
        UPDATE affects zero rows and we raise :class:`ConcurrencyError` — the
        loser never silently clobbers the winner.
        """
        from sqlalchemy import update as sa_update

        now = datetime.now(UTC)
        values = dict(updates)
        values["version"] = expected_version + 1
        values["updated_at"] = now
        guard = (
            sa_update(TodoModel)
            .where(
                TodoModel.todo_id == todo_id,
                TodoModel.version == expected_version,
            )
            .values(**values)
        )
        if project_id is not None:
            guard = guard.where(TodoModel.project_id == project_id)
        res = await self._session.execute(guard)
        if (res.rowcount or 0) != 1:  # type: ignore[attr-defined]  # CursorResult at runtime
            # Row missing, wrong project scope, or version moved underneath us:
            # the optimistic guard matched no row -> lost update.
            raise ConcurrencyError(
                f"Concurrent update of todo {todo_id!r}: "
                f"expected version {expected_version} (project={project_id!r})"
            )
        await self._session.flush()
        # Re-read so the returned ORM object reflects the committed values even
        # if a stale copy was already in this session's identity map.
        todo = await self.get_by_id(todo_id, project_id=project_id)
        if todo is not None:
            await self._session.refresh(todo)
        assert todo is not None
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
        """Claim QUEUED todos for execution with a guarded conditional UPDATE.

        SQLite has no row-level locking (``with_for_update`` is silently dropped),
        so the claim cannot rely on a lock. Instead, each candidate is flipped
        QUEUED->ACTIVE with an optimistic ``WHERE id=? AND status='queued' AND
        version=?`` UPDATE. Only the caller whose UPDATE affects a row (rowcount
        == 1) "wins" the claim; a caller that lost the race (rowcount == 0) skips
        the row, so every todo is returned to exactly one caller -> no double
        claim / double dispatch.
        """
        from sqlalchemy import update

        stmt = select(TodoModel).where(TodoModel.status == TodoStatus.QUEUED.value)
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
        stmt = stmt.limit(limit)
        with contextlib.suppress(Exception):
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        now = datetime.now(UTC)
        claimed: list[TodoModel] = []
        for todo in candidates:
            old_status = todo.status
            old_version = todo.version
            # Guarded conditional claim: transition only if the row is STILL
            # queued at the same version we read. Mirrors transition()'s
            # version/status guard so a concurrent claimer cannot also win.
            guard = (
                update(TodoModel)
                .where(
                    TodoModel.id == todo.id,
                    TodoModel.status == TodoStatus.QUEUED.value,
                    TodoModel.version == old_version,
                )
                .values(status=TodoStatus.ACTIVE.value, version=old_version + 1, updated_at=now)
            )
            try:
                res = await self._session.execute(guard)
            except OperationalError as exc:
                if _is_sqlite_busy(exc):
                    # SQLITE_BUSY ('database is locked') on the guarded UPDATE is a
                    # LOST RACE under contention, not a fault: skip this row rather
                    # than propagating the lock error to the caller.
                    with contextlib.suppress(Exception):
                        await self._session.refresh(todo)
                    continue
                raise
            if (res.rowcount or 0) != 1:  # type: ignore[attr-defined]  # CursorResult at runtime
                # Lost the race: another caller already claimed this row. Drop our
                # stale in-memory copy and skip it so it is never returned twice.
                await self._session.refresh(todo)
                continue
            # We won: sync the in-memory ORM object to the committed values.
            todo.status = TodoStatus.ACTIVE.value
            todo.version = old_version + 1
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
            claimed.append(todo)
        await self._session.flush()
        return claimed

    async def count_active(self, project_id: str | None = None) -> int:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(TodoModel).where(
            TodoModel.status == TodoStatus.ACTIVE.value
        )
        if project_id is not None:
            stmt = stmt.where(TodoModel.project_id == project_id)
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

    async def transition(
        self,
        todo_id: str,
        new_status: TodoStatus,
        expected_version: int,
        project_id: str | None = None,
    ) -> TodoModel:
        """Optimistically-locked, optionally project-scoped status transition.

        Validates the transition is legal for the current status, then performs
        a guarded UPDATE on ``version == expected_version`` (+ project_id when
        given). A concurrent writer that already bumped the version — or a
        wrong-project scope — makes the UPDATE affect zero rows and we raise
        :class:`ConcurrencyError`.
        """
        from sqlalchemy import update as sa_update

        todo = await self.get_by_id(todo_id, project_id=project_id)
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
        now = datetime.now(UTC)
        guard = (
            sa_update(TodoModel)
            .where(
                TodoModel.todo_id == todo_id,
                TodoModel.version == expected_version,
            )
            .values(status=new_status.value, version=expected_version + 1, updated_at=now)
        )
        if project_id is not None:
            guard = guard.where(TodoModel.project_id == project_id)
        res = await self._session.execute(guard)
        if (res.rowcount or 0) != 1:  # type: ignore[attr-defined]  # CursorResult at runtime
            raise ConcurrencyError(
                f"Concurrent transition of todo {todo_id!r}: "
                f"expected version {expected_version} (project={project_id!r})"
            )
        await self._session.flush()
        await self._session.refresh(todo)
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
        """Claim 'created' task-returns for review with a guarded conditional UPDATE.

        TaskReturnModel has no version column, so the optimistic guard is on
        ``status`` alone: each candidate is flipped 'created'->'claimed_for_review'
        with ``WHERE id=? AND status='created'``. Only the caller whose UPDATE
        affects a row (rowcount == 1) claims it; a loser (rowcount == 0) skips it.
        This makes each return claimed by exactly one caller -> no double-review.
        """
        from sqlalchemy import update

        stmt = select(TaskReturnModel).where(TaskReturnModel.status == "created")
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        stmt = stmt.order_by(TaskReturnModel.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        now = datetime.now(UTC)
        claimed: list[TaskReturnModel] = []
        for row in candidates:
            guard = (
                update(TaskReturnModel)
                .where(
                    TaskReturnModel.id == row.id,
                    TaskReturnModel.status == "created",
                )
                .values(status="claimed_for_review", updated_at=now)
            )
            try:
                res = await self._session.execute(guard)
            except OperationalError as exc:
                if _is_sqlite_busy(exc):
                    # SQLITE_BUSY on the guarded UPDATE == lost race: skip the row.
                    with contextlib.suppress(Exception):
                        await self._session.refresh(row)
                    continue
                raise
            if (res.rowcount or 0) != 1:  # type: ignore[attr-defined]  # CursorResult at runtime
                # Lost the race: another caller already claimed this return.
                await self._session.refresh(row)
                continue
            row.status = "claimed_for_review"
            row.updated_at = now
            claimed.append(row)
        await self._session.flush()
        return claimed


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

    async def record_typed(
        self,
        event_type: AuditEventType,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEventModel:
        """Record an audit event from the typed AuditEventType taxonomy.

        Serializes ``details`` to JSON and delegates to :meth:`create`. This is
        the typed entry point the event loop uses so audit rows carry values
        from the AuditEventType enum rather than ad-hoc magic strings.
        """
        import json as _json

        return await self.create(
            event_type=event_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=_json.dumps(details) if details is not None else None,
        )

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
        """Upsert a (namespace, key) variable using ON CONFLICT, race-safe.

        Two concurrent FIRST writes of the same (namespace, project_id) would
        otherwise both INSERT a namespace and one would raise IntegrityError on
        the ``uq_namespace_project`` unique constraint (data loss). We instead
        use SQLite ``INSERT ... ON CONFLICT DO UPDATE`` so the second writer
        converges on the first writer's row instead of failing. The value insert
        is upserted the same way on ``uq_variable_namespace_key`` (last writer
        wins).
        """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        now = datetime.now(UTC)
        # --- Resolve / upsert the namespace row ---
        # NOTE: under SQLite, NULL values are DISTINCT in a unique index, so the
        # (namespace, project_id) ON CONFLICT only fires for a non-NULL scope.
        # We therefore SELECT first (dedups the project_id IS NULL case) and only
        # fall back to an ON CONFLICT insert when absent — that insert protects
        # the concurrent non-NULL first-write race (converge, not IntegrityError).
        ns_row = await self._session.execute(
            select(VariableNamespaceModel).where(
                VariableNamespaceModel.namespace == namespace,
                VariableNamespaceModel.project_id == project_id,
            )
        )
        ns = ns_row.scalar_one_or_none()
        if ns is None:
            ns_ins = sqlite_insert(VariableNamespaceModel).values(
                namespace=namespace, project_id=project_id
            )
            ns_ins = ns_ins.on_conflict_do_update(
                index_elements=[
                    VariableNamespaceModel.namespace,
                    VariableNamespaceModel.project_id,
                ],
                set_={"updated_at": now},
            )
            await self._session.execute(ns_ins)
            await self._session.flush()
            ns_row = await self._session.execute(
                select(VariableNamespaceModel).where(
                    VariableNamespaceModel.namespace == namespace,
                    VariableNamespaceModel.project_id == project_id,
                )
            )
            ns = ns_row.scalar_one()

        # --- Upsert the value row (idempotent on uq_variable_namespace_key) ---
        val_ins = sqlite_insert(VariableValueModel).values(
            namespace_id=ns.id, key=key, value=value
        )
        val_ins = val_ins.on_conflict_do_update(
            index_elements=[
                VariableValueModel.namespace_id,
                VariableValueModel.key,
            ],
            set_={"value": value, "updated_at": now},
        )
        await self._session.execute(val_ins)
        await self._session.flush()
        row_res = await self._session.execute(
            select(VariableValueModel).where(
                VariableValueModel.namespace_id == ns.id,
                VariableValueModel.key == key,
            )
        )
        row = row_res.scalar_one()
        # Ensure the returned ORM object reflects the just-written value even if
        # a stale copy was already cached in this session's identity map.
        await self._session.refresh(row)
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
        """Upsert keyed by the unique ``name`` using ON CONFLICT, race-safe.

        Two concurrent first-writes of the same name would otherwise both INSERT
        and the loser would raise IntegrityError on the unique ``name``. We use
        SQLite ``INSERT ... ON CONFLICT(name) DO UPDATE`` so the second writer
        converges on the existing row (last writer wins) instead of failing.
        """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        values = dict(data)
        values.setdefault("id", f"pp-{uuid4().hex[:8]}")
        update_cols = {k: v for k, v in values.items() if k not in ("id", "name")}
        update_cols["updated_at"] = datetime.now(UTC)
        ins = sqlite_insert(PromptProfileModel).values(**values)
        ins = ins.on_conflict_do_update(
            index_elements=[PromptProfileModel.name],
            set_=update_cols,
        )
        await self._session.execute(ins)
        await self._session.flush()
        row = await self.get_by_name(data["name"])
        assert row is not None
        await self._session.refresh(row)
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
        """Mark a message read with a guarded, first-writer-wins UPDATE.

        Only the FIRST ack stamps ``read_at`` (the UPDATE is guarded on
        ``read_at IS NULL``); a concurrent second ack matches zero rows and
        leaves the original timestamp intact (no clobber). The stamp is a
        tz-aware UTC datetime so it compares equal to a tz-aware expected value,
        and we normalize the returned value to tz-aware UTC in case the SQLite
        round-trip yielded a naive datetime.
        """
        from sqlalchemy import update as sa_update

        row = await self.get_by_id(message_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        guard = (
            sa_update(AgentMessageModel)
            .where(
                AgentMessageModel.id == message_id,
                AgentMessageModel.read_at.is_(None),
            )
            .values(read_at=now)
        )
        res = await self._session.execute(guard)
        await self._session.flush()
        if (res.rowcount or 0) == 1:  # type: ignore[attr-defined]  # CursorResult at runtime
            # We won the ack race: reflect the stamp on the in-memory object.
            row.read_at = now
        else:
            # Lost the race (or already read): re-read the committed timestamp.
            await self._session.refresh(row)
        if row.read_at is not None and row.read_at.tzinfo is None:
            row.read_at = row.read_at.replace(tzinfo=UTC)
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


class FeatureRepository:
    """Persistence for the feature database (FeatureModel).

    JSON (de)serialization happens at the repo boundary — callers pass / receive
    Python objects (lists/dicts); the DB stores JSON-in-Text as do PromptProfileModel
    and TodoModel.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(value: Any) -> str:
        import json as _json

        if isinstance(value, str):
            return value
        return _json.dumps(value)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert(self, data: dict[str, Any]) -> FeatureModel:
        """Insert or update a feature row, keyed by name (unique).

        Fields that are lists/dicts in ``data`` are serialized to JSON before
        being written to the DB.  The returned model has JSON-string columns.
        """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        json_fields = {"acceptance_criteria", "evidence"}
        serialized: dict[str, Any] = {}
        for key, val in data.items():
            if key in json_fields:
                serialized[key] = self._serialize(val)
            elif key == "status" and isinstance(val, FeatureStatus):
                # Core INSERT needs the column's string value, not the enum.
                serialized[key] = val.value
            else:
                serialized[key] = val

        # Race-safe upsert keyed by the unique ``name``: two concurrent
        # first-writes converge via ON CONFLICT(name) DO UPDATE instead of one
        # raising IntegrityError.
        serialized.setdefault("id", f"FEAT-{uuid4().hex[:8].upper()}")
        update_cols = {
            k: v for k, v in serialized.items() if k not in ("id", "name")
        }
        ins = sqlite_insert(FeatureModel).values(**serialized)
        if update_cols:
            ins = ins.on_conflict_do_update(
                index_elements=[FeatureModel.name],
                set_=update_cols,
            )
        else:
            ins = ins.on_conflict_do_nothing(index_elements=[FeatureModel.name])
        await self._session.execute(ins)
        await self._session.flush()
        row = await self.get_by_name(data["name"])
        assert row is not None
        await self._session.refresh(row)
        return row

    async def set_status(
        self,
        feature_id: str,
        status: FeatureStatus,
        detail: dict[str, Any] | None = None,
        verified_at: datetime | None = None,
    ) -> FeatureModel:
        """Update status, optional verified_at, and persist the verify-detail JSON."""
        import json as _json

        row = await self.get_by_id(feature_id)
        if row is None:
            raise KeyError(f"Feature {feature_id!r} not found")
        row.status = status.value
        if verified_at is not None:
            row.verified_at = verified_at
        if detail is not None:
            row.last_verify_detail = _json.dumps(detail)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_name(self, name: str) -> FeatureModel | None:
        stmt = select(FeatureModel).where(FeatureModel.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, feature_id: str) -> FeatureModel | None:
        stmt = select(FeatureModel).where(FeatureModel.id == feature_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[FeatureModel]:
        result = await self._session.execute(select(FeatureModel))
        return list(result.scalars().all())

    async def list_by_status(self, status: FeatureStatus) -> list[FeatureModel]:
        stmt = select(FeatureModel).where(FeatureModel.status == status.value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_category(self, category: str) -> list[FeatureModel]:
        stmt = select(FeatureModel).where(FeatureModel.category == category)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class SpendRepository:
    """Persistence for rolling-window spend records.

    Each row represents one spend event recorded by :class:`SpendLimiter`.
    ``ts`` is an epoch float (e.g. from ``time.monotonic()`` or
    ``time.time()``); the rolling-window math compares timestamps as plain
    floats.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        ts: float,
        cost_usd: float,
        kind: str,
        project_id: str | None = None,
        model: str | None = None,
    ) -> SpendRecordModel:
        """Persist a spend event and return the new row.

        Args:
            ts:         Epoch float timestamp of the event.
            cost_usd:   Amount spent in USD.
            kind:       Resource kind (e.g. ``"token"``, ``"infra"``).
            project_id: Optional project scope.
            model:      Optional model identifier.

        Returns:
            The newly created :class:`SpendRecordModel` instance.
        """
        row = SpendRecordModel(
            ts=ts,
            cost_usd=cost_usd,
            kind=kind,
            project_id=project_id,
            model=model,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_since(
        self,
        since_epoch: float,
        project_id: str | None = None,
    ) -> list[SpendRecordModel]:
        """Return all records with ``ts >= since_epoch``.

        Args:
            since_epoch: Lower bound for ``ts`` (inclusive).
            project_id:  When set, restrict to records for this project.

        Returns:
            List of :class:`SpendRecordModel` rows ordered by ``ts`` ascending.
        """
        stmt = select(SpendRecordModel).where(SpendRecordModel.ts >= since_epoch)
        if project_id is not None:
            stmt = stmt.where(SpendRecordModel.project_id == project_id)
        stmt = stmt.order_by(SpendRecordModel.ts.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def total_since(
        self,
        since_epoch: float,
        project_id: str | None = None,
    ) -> float:
        """Sum of ``cost_usd`` for all records with ``ts >= since_epoch``.

        Args:
            since_epoch: Lower bound for ``ts`` (inclusive).
            project_id:  When set, restrict to records for this project.

        Returns:
            Total spend in USD (0.0 when no matching records).
        """
        from sqlalchemy import func

        stmt = select(func.sum(SpendRecordModel.cost_usd)).where(
            SpendRecordModel.ts >= since_epoch
        )
        if project_id is not None:
            stmt = stmt.where(SpendRecordModel.project_id == project_id)
        result = await self._session.execute(stmt)
        total: float | None = result.scalar_one_or_none()
        return float(total) if total is not None else 0.0


class RoleRunRepository:
    """Persistence for per-project role-run records.

    Each row records that a named role executed once for a given project.
    ``count_by_role`` returns the {role: run_count} map used by the
    accounting ledger.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, project_id: str | None, role: str) -> RoleRunModel:
        """Insert a single role-run record."""
        row = RoleRunModel(project_id=project_id, role=role)
        self._session.add(row)
        await self._session.flush()
        return row

    async def count_by_role(self, project_id: str | None = None) -> dict[str, int]:
        """Return {role: count} for the given project_id (or all if None)."""
        stmt = select(RoleRunModel)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.role] = counts.get(row.role, 0) + 1
        return counts

    async def list_all(self, project_id: str | None = None) -> list[RoleRunModel]:
        stmt = select(RoleRunModel)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
