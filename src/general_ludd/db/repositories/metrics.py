"""Spend, role-run, and model-performance repository implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import (
    BenchmarkResultModel,
    ModelCallLogModel,
    ModelPerformanceModel,
    PromptProfileModel,
    RoleRunModel,
    SpendRecordModel,
)
from general_ludd.db.repositories.shared import current_list_limit, dialect_insert


class SpendRepository:
    """Persistence for rolling-window spend records.

    Each row represents one spend event recorded by :class:`SpendLimiter`.
    ``ts`` is an epoch float (e.g. from ``time.monotonic()`` or
    ``time.time()``); the rolling-window math compares timestamps as plain
    floats.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
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

        stmt = select(func.sum(SpendRecordModel.cost_usd)).where(SpendRecordModel.ts >= since_epoch)
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
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def record(self, project_id: str | None, role: str) -> RoleRunModel:
        """Insert a single role-run record."""
        row = RoleRunModel(project_id=project_id, role=role)
        self._session.add(row)
        await self._session.flush()
        return row

    async def count_by_role(self, project_id: str | None = None) -> dict[str, int]:
        """Return {role: count} for the given project_id (or all if None).

        Aggregated in SQL via ``GROUP BY role`` rather than loading every
        row and counting in Python (P8).
        """
        from sqlalchemy import func

        stmt = select(RoleRunModel.role, func.count()).group_by(RoleRunModel.role)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return {role: count for role, count in result.all()}

    async def list_all(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RoleRunModel]:
        """List a bounded page of role runs, optionally scoped to a project."""
        stmt = select(RoleRunModel)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        effective_limit = min(limit, current_list_limit()) if limit is not None else current_list_limit()
        stmt = stmt.offset(offset).limit(effective_limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


def _model_performance_summary_row(row: Any) -> dict[str, Any]:
    """Normalize one aggregate result row for the dashboard contract."""
    total = int(row.total_calls or 0)
    successful = int(row.successful_calls or 0)
    return {
        "service": str(row.service or ""),
        "task_type": str(row.task_type or ""),
        "model_name": str(row.model_name or ""),
        "model_profile_id": str(row.model_profile_id or ""),
        "total_calls": total,
        "successful_calls": successful,
        "failed_calls": total - successful,
        "success_rate": round(successful / total, 4) if total else 0.0,
        "total_cost_usd": float(row.total_cost_usd or 0.0),
        "avg_duration_ms": float(row.avg_duration_ms or 0.0),
    }


class _ModelPerformanceSessionCompatibility:
    """Compatibility surface for callers that require an explicit session."""

    _session: AsyncSession | None

    def _resolve_session(self) -> AsyncSession:
        """Return an explicitly caller-owned session for compatibility."""
        if self._session is not None:
            return self._session
        raise RuntimeError(
            "ModelPerformanceRepository._resolve_session: no session configured; "
            "factory-owned operations require an operation-scoped context."
        )




class ModelPerformanceRepository(_ModelPerformanceSessionCompatibility):
    """Persistence for model call logs and aggregated performance stats.

    Two-table design:

    * ``model_call_logs`` — one row per model invocation, immutable.
      Written by the worker HTTP path and the EventLoop in-process runner
      path so every model call is centrally observable.
    * ``model_performance`` — pre-aggregated per-profile stats, updated
      periodically by ``refresh_recent_stats()`` for fast dashboard reads.

    All write methods accept an optional ``session`` override so callers
    that already hold an active session (e.g. the EventLoop tick) can share
    it rather than opening a new one.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a lazy session factory."""
        self._session = session
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session_scope(
        self,
        session: AsyncSession | None = None,
        *,
        transactional: bool = False,
    ) -> AsyncIterator[AsyncSession]:
        """Yield caller-owned state or one operation-scoped factory session."""
        caller_session = session if session is not None else self._session
        if caller_session is not None:
            yield caller_session
            return
        if self._session_factory is None:
            raise RuntimeError(
                "ModelPerformanceRepository: no session configured and no session_factory available."
            )
        if transactional:
            async with self._session_factory.begin() as owned_session:
                yield owned_session
            return
        async with self._session_factory() as owned_session:
            yield owned_session

    # ── recording ───────────────────────────────────────────────────────

    async def record_call(
        self,
        *,
        service: str,
        model_name: str,
        model_profile_id: str,
        task_type: str = "generation",
        work_type: str | None = None,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float = 0.0,
        todo_id: str | None = None,
        job_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        session: AsyncSession | None = None,
    ) -> ModelCallLogModel:
        """Persist a single model call log entry.

        Caller-owned sessions are flushed without committing. Factory-owned
        writes receive an independent transaction that is committed and
        closed before this method returns. Returns the newly created
        :class:`ModelCallLogModel`.

        This method does NOT update the aggregated ``model_performance``
        table — call :meth:`refresh_recent_stats` to recompute aggregates
        in batch.
        """
        row = ModelCallLogModel(
            todo_id=todo_id,
            job_id=job_id,
            service=service,
            model_name=model_name,
            model_profile_id=model_profile_id,
            task_type=task_type,
            work_type=work_type,
            success=success,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )
        caller_session = session or self._session
        if caller_session is not None:
            caller_session.add(row)
            await caller_session.flush()
            return row
        if self._session_factory is None:
            raise RuntimeError(
                "ModelPerformanceRepository.record_call: no session configured "
                "and no session_factory available."
            )

        async with self._session_factory.begin() as owned_session:
            owned_session.add(row)
            await owned_session.flush()
            owned_session.expunge(row)
        return row

    def record_call_sync(
        self,
        *,
        service: str,
        model_name: str,
        model_profile_id: str,
        task_type: str = "generation",
        work_type: str | None = None,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float = 0.0,
        todo_id: str | None = None,
        job_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Synchronous version of :meth:`record_call`.

        Intended for use from ``asyncio.to_thread`` worker paths where an
        async session is not available.  Opens and commits its own session
        synchronously (blocking).  Fail-soft: any exception is logged and
        swallowed so a broken repo never kills the model call.
        """
        try:
            import asyncio as _asyncio

            coro = self.record_call(
                service=service,
                model_name=model_name,
                model_profile_id=model_profile_id,
                task_type=task_type,
                work_type=work_type,
                success=success,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                todo_id=todo_id,
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
                session=None,
            )
            _asyncio.run(coro)
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "ModelPerformanceRepository.record_call_sync failed: %s",
                exc,
                exc_info=True,
            )

    # ── refresh (aggregated stats) ──────────────────────────────────────

    async def refresh_recent_stats(
        self,
        session: AsyncSession | None = None,
        window_hours: float = 24.0,
    ) -> int:
        """Recompute the ``model_performance`` table from recent call logs.

        Queries all call log rows within the rolling *window_hours* window,
        groups by ``model_profile_id``, and upserts each profile's aggregate
        row in ``model_performance``.  Returns the number of profiles
        refreshed.
        """
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        cutoff = _dt.now(_UTC) - _td(hours=window_hours)

        async with self._session_scope(session, transactional=True) as eff_session:
            stmt = (
                select(
                    ModelCallLogModel.model_profile_id,
                    _func.max(ModelCallLogModel.model_name).label("model_name"),
                    _func.max(ModelCallLogModel.service).label("service"),
                    _func.count().label("total_calls"),
                    _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
                    (_func.count() - _func.sum(_func.cast(ModelCallLogModel.success, _Integer))).label("failed_calls"),
                    _func.coalesce(_func.sum(ModelCallLogModel.input_tokens), 0).label("total_input_tokens"),
                    _func.coalesce(_func.sum(ModelCallLogModel.output_tokens), 0).label("total_output_tokens"),
                    _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
                    _func.avg(ModelCallLogModel.duration_ms).label("avg_duration_ms"),
                    _func.max(ModelCallLogModel.created_at).label("last_call_at"),
                    _func.min(ModelCallLogModel.created_at).label("first_call_at"),
                )
                .where(ModelCallLogModel.created_at >= cutoff)
                .group_by(ModelCallLogModel.model_profile_id)
            )
            result = await eff_session.execute(stmt)
            rows = result.all()

            refreshed = 0
            now = _dt.now(_UTC)
            for row in rows:
                profile_id = row.model_profile_id
                existing = await eff_session.execute(
                    select(ModelPerformanceModel).where(ModelPerformanceModel.model_profile_id == profile_id)
                )
                perf: ModelPerformanceModel | None = existing.scalar_one_or_none()
                if perf is None:
                    perf = ModelPerformanceModel(
                        model_profile_id=profile_id,
                        model_name=str(row.model_name or ""),
                        service=str(row.service or ""),
                    )
                    eff_session.add(perf)
                perf.model_name = str(row.model_name or perf.model_name)
                perf.service = str(row.service or perf.service)
                perf.total_calls = int(row.total_calls or 0)
                perf.successful_calls = int(row.successful_calls or 0)
                perf.failed_calls = int(row.failed_calls or 0)
                perf.total_input_tokens = int(row.total_input_tokens or 0)
                perf.total_output_tokens = int(row.total_output_tokens or 0)
                perf.total_cost_usd = float(row.total_cost_usd or 0.0)
                perf.avg_duration_ms = (
                    float(row.avg_duration_ms) if row.avg_duration_ms is not None else 0.0
                )
                perf.last_call_at = row.last_call_at
                perf.first_call_at = row.first_call_at
                perf.updated_at = now
                refreshed += 1

            if refreshed:
                await eff_session.flush()
            return refreshed

    # ── queries ─────────────────────────────────────────────────────────

    async def get_stats_by_model(
        self,
        model_profile_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[ModelPerformanceModel]:
        """Return aggregated performance rows, optionally filtered by profile.

        When *model_profile_id* is None, returns stats for ALL profiles.
        """
        stmt = select(ModelPerformanceModel)
        if model_profile_id is not None:
            stmt = stmt.where(ModelPerformanceModel.model_profile_id == model_profile_id)
        stmt = stmt.order_by(ModelPerformanceModel.total_cost_usd.desc())
        async with self._session_scope(session) as eff_session:
            result = await eff_session.execute(stmt)
            return list(result.scalars().all())

    async def get_recent_calls(
        self,
        limit: int = 100,
        model_profile_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[ModelCallLogModel]:
        """Return the most recent call log entries."""
        stmt = select(ModelCallLogModel).order_by(ModelCallLogModel.created_at.desc()).limit(min(limit, 1000))
        if model_profile_id is not None:
            stmt = stmt.where(ModelCallLogModel.model_profile_id == model_profile_id)
        async with self._session_scope(session) as eff_session:
            result = await eff_session.execute(stmt)
            return list(result.scalars().all())

    async def get_stats_by_service(
        self,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return aggregated performance grouped by service/provider."""
        from sqlalchemy import func as _func

        stmt = (
            select(
                ModelPerformanceModel.service,
                _func.count().label("profile_count"),
                _func.sum(ModelPerformanceModel.total_calls).label("total_calls"),
                _func.sum(ModelPerformanceModel.successful_calls).label("successful_calls"),
                _func.sum(ModelPerformanceModel.total_cost_usd).label("total_cost"),
            )
            .group_by(ModelPerformanceModel.service)
            .order_by(_func.sum(ModelPerformanceModel.total_cost_usd).desc())
        )
        async with self._session_scope(session) as eff_session:
            result = await eff_session.execute(stmt)
            return [
                {
                    "service": r.service,
                    "profile_count": int(r.profile_count),
                    "total_calls": int(r.total_calls or 0),
                    "successful_calls": int(r.successful_calls or 0),
                    "total_cost_usd": float(r.total_cost or 0.0),
                }
                for r in result.all()
            ]

    async def get_daily_stats(
        self,
        days: int = 7,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily call volume and cost aggregates."""
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        cutoff = _dt.now(_UTC) - _td(days=days)
        stmt = (
            select(
                _func.date(ModelCallLogModel.created_at).label("day"),
                _func.count().label("total_calls"),
                _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
                _func.coalesce(_func.sum(ModelCallLogModel.input_tokens), 0).label("total_input_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.output_tokens), 0).label("total_output_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
            )
            .where(ModelCallLogModel.created_at >= cutoff)
            .group_by(_func.date(ModelCallLogModel.created_at))
            .order_by(_func.date(ModelCallLogModel.created_at).desc())
        )
        async with self._session_scope(session) as eff_session:
            result = await eff_session.execute(stmt)
            return [
                {
                    "date": str(r.day),
                    "total_calls": int(r.total_calls),
                    "successful_calls": int(r.successful_calls or 0),
                    "total_input_tokens": int(r.total_input_tokens or 0),
                    "total_output_tokens": int(r.total_output_tokens or 0),
                    "total_cost_usd": float(r.total_cost_usd or 0.0),
                }
                for r in result.all()
            ]

    # ── router-facing queries ───────────────────────────────────────────

    async def get_ranking(
        self,
        task_type: str,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return per-(service, model) outcome stats for *task_type*.

        Grouped from the immutable ``model_call_logs`` table so the router
        always sees the latest recorded outcomes (no aggregate refresh
        required).  Each row carries ``success_rate``, ``avg_latency_ms``,
        ``avg_cost_usd`` and ``sample_count``.
        """
        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        stmt = (
            select(
                ModelCallLogModel.service,
                ModelCallLogModel.model_name,
                ModelCallLogModel.model_profile_id,
                _func.count().label("sample_count"),
                _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successes"),
                _func.coalesce(_func.avg(ModelCallLogModel.duration_ms), 0.0).label("avg_latency_ms"),
                _func.coalesce(_func.avg(ModelCallLogModel.cost_usd), 0.0).label("avg_cost_usd"),
            )
            .where(ModelCallLogModel.task_type == task_type)
            .group_by(
                ModelCallLogModel.service,
                ModelCallLogModel.model_name,
                ModelCallLogModel.model_profile_id,
            )
        )
        async with self._session_scope(session) as eff_session:
            rows = (await eff_session.execute(stmt)).all()
        ranking: list[dict[str, Any]] = []
        for row in rows:
            sample_count = int(row.sample_count or 0)
            successes = int(row.successes or 0)
            ranking.append(
                {
                    "service": str(row.service or ""),
                    "model_name": str(row.model_name or ""),
                    "model_profile_id": str(row.model_profile_id or ""),
                    "sample_count": sample_count,
                    "success_rate": round(successes / sample_count, 4) if sample_count else 0.0,
                    "avg_latency_ms": float(row.avg_latency_ms or 0.0),
                    "avg_cost_usd": float(row.avg_cost_usd or 0.0),
                }
            )
        return ranking

    async def get_best_model(
        self,
        task_type: str,
        min_calls: int = 3,
        prefer_cost: bool = False,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        """Return the best (service, model_name) for *task_type*.

        Considers only rows with ``sample_count >= min_calls``.  By default
        the highest success rate wins (cost as tiebreak); with
        ``prefer_cost`` the lowest average cost wins (success as tiebreak).
        Returns ``None`` when no model meets the minimum sample count.
        """
        ranking = await self.get_ranking(task_type, session=session)
        eligible = [r for r in ranking if int(r.get("sample_count", 0)) >= min_calls]
        if not eligible:
            return None
        if prefer_cost:
            eligible.sort(
                key=lambda r: (
                    float(r.get("avg_cost_usd", 0.0)),
                    -float(r.get("success_rate", 0.0)),
                )
            )
        else:
            eligible.sort(
                key=lambda r: (
                    -float(r.get("success_rate", 0.0)),
                    float(r.get("avg_cost_usd", 0.0)),
                )
            )
        best = eligible[0]
        if prefer_cost:
            composite = round(1.0 / (1.0 + float(best.get("avg_cost_usd", 0.0))), 4)
        else:
            composite = float(best.get("success_rate", 0.0))
        return {
            "service": str(best.get("service", "openai")),
            "model_name": str(best.get("model_name", "")),
            "model_profile_id": str(best.get("model_profile_id", "")),
            "composite_score": composite,
            "sample_count": int(best.get("sample_count", 0)),
        }

    async def get_summary(
        self,
        service: str | None = None,
        task_type: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return aggregated per-model outcome summaries for dashboards.

        Groups call logs by (service, task_type, model).  Optional filters
        narrow to a single *service* and/or *task_type*.
        """
        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        stmt = select(
            ModelCallLogModel.service,
            ModelCallLogModel.task_type,
            ModelCallLogModel.model_name,
            ModelCallLogModel.model_profile_id,
            _func.count().label("total_calls"),
            _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
            _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
            _func.coalesce(_func.avg(ModelCallLogModel.duration_ms), 0.0).label("avg_duration_ms"),
        )
        if service is not None:
            stmt = stmt.where(ModelCallLogModel.service == service)
        if task_type is not None:
            stmt = stmt.where(ModelCallLogModel.task_type == task_type)
        stmt = stmt.group_by(
            ModelCallLogModel.service,
            ModelCallLogModel.task_type,
            ModelCallLogModel.model_name,
            ModelCallLogModel.model_profile_id,
        )
        async with self._session_scope(session) as eff_session:
            rows = (await eff_session.execute(stmt)).all()
        return [_model_performance_summary_row(row) for row in rows]


class BenchmarkRepository:
    """Persist benchmark results and compute model-selection aggregates."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a transactional session factory."""
        self._session = session
        self._session_factory = session_factory

    async def _execute_with_session(
        self,
        fn: Callable[[AsyncSession], Any],
        *,
        transactional: bool = False,
    ) -> Any:
        """Run one operation without transferring caller session ownership.

        Factory-owned writes receive an explicit transaction that commits or
        rolls back before its session closes. Factory-owned reads use a plain
        operation-scoped session: closing it releases the implicit read
        transaction without a commit that would expire returned ORM rows.
        """
        if self._session_factory is not None:
            if transactional:
                async with self._session_factory() as session, session.begin():
                    result = await fn(session)
                    if hasattr(result, "_sa_instance_state"):
                        session.expunge(result)
                    return result
            async with self._session_factory() as session:
                return await fn(session)
        if self._session is not None:
            return await fn(self._session)
        raise RuntimeError("BenchmarkRepository: no session or session_factory")

    async def record_result(self, data: dict[str, Any]) -> BenchmarkResultModel:
        """Persist and return a benchmark result."""

        async def _do(session: AsyncSession) -> BenchmarkResultModel:
            row = BenchmarkResultModel(**data)
            session.add(row)
            await session.flush()
            return row

        return cast(
            BenchmarkResultModel,
            await self._execute_with_session(_do, transactional=True),
        )

    async def get_aggregate_scores(
        self,
        task_type: str | None = None,
        project_id: str | None = None,
        task_role: str | None = None,
        skill_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate scores by prompt/model/task, project, role, and skill.

        ``project_id`` is the project-hierarchy phase-3 axis: when None (the
        default and every legacy caller's behaviour) the scores are GLOBAL —
        grouped across all projects exactly as before. When a ``project_id`` is
        passed, results are filtered to that project's history, so the
        AdaptiveRouter can read its own history and a related project's history
        separately and borrow across declared edges. ``project_id`` is always a
        group key and is returned in every row so the router can attribute a
        borrowed pick to its source project.

        ``task_role`` filters and groups by the assigned role (planner, coder,
        reviewer, editor, compactor, enumerator), enabling per-role quality
        comparisons. When provided, results are filtered to that role.
        ``task_role`` is included as a group-by key and returned in every row.

        ``skill_id`` is an optional filter and an unconditional group key. NULL
        therefore keeps legacy results distinct from skill-attributed results,
        while callers can request a single skill's model usefulness history.
        """

        async def _do(session: AsyncSession) -> list[dict[str, Any]]:
            from sqlalchemy import func

            stmt = (
                select(
                    BenchmarkResultModel.prompt_profile_id,
                    BenchmarkResultModel.model_profile_id,
                    BenchmarkResultModel.task_type,
                    BenchmarkResultModel.project_id,
                    BenchmarkResultModel.task_role,
                    BenchmarkResultModel.skill_id,
                    func.avg(BenchmarkResultModel.completion_score).label("avg_completion"),
                    func.avg(BenchmarkResultModel.code_quality_score).label("avg_quality"),
                    func.avg(BenchmarkResultModel.instruction_adherence_score).label("avg_instruction"),
                    func.avg(BenchmarkResultModel.token_efficiency_score).label("avg_efficiency"),
                    func.count().label("sample_count"),
                    func.avg(BenchmarkResultModel.cost_usd).label("avg_cost"),
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
                    BenchmarkResultModel.project_id,
                    BenchmarkResultModel.task_role,
                    BenchmarkResultModel.skill_id,
                )
            )
            if task_type is not None:
                stmt = stmt.where(BenchmarkResultModel.task_type == task_type)
            if project_id is not None:
                stmt = stmt.where(BenchmarkResultModel.project_id == project_id)
            if task_role is not None:
                stmt = stmt.where(BenchmarkResultModel.task_role == task_role)
            if skill_id is not None:
                stmt = stmt.where(BenchmarkResultModel.skill_id == skill_id)
            result = await session.execute(stmt)
            rows = result.all()
            return [
                {
                    "prompt_profile_id": r.prompt_profile_id,
                    "model_profile_id": r.model_profile_id,
                    "task_type": r.task_type,
                    "project_id": r.project_id,
                    "task_role": r.task_role,
                    "skill_id": r.skill_id,
                    "avg_completion": r.avg_completion,
                    "avg_quality": r.avg_quality,
                    "avg_instruction": r.avg_instruction,
                    "avg_efficiency": r.avg_efficiency,
                    "sample_count": r.sample_count,
                    "avg_cost": r.avg_cost,
                    "composite_score": getattr(r, "composite_score", None),
                }
                for r in rows
            ]

        return cast("list[dict[str, Any]]", await self._execute_with_session(_do))

    async def get_best_for_task(self, task_type: str, min_samples: int = 3) -> list[dict[str, Any]]:
        """Rank task-specific aggregates that meet the sample threshold."""
        scores = await self.get_aggregate_scores(task_type=task_type)
        filtered = [s for s in scores if s["sample_count"] >= min_samples]
        filtered.sort(key=lambda s: s.get("composite_score", 0) or 0, reverse=True)
        return filtered

    async def get_model_scores(self, model_profile_id: str) -> list[BenchmarkResultModel]:
        """List bounded benchmark results for a model, newest first."""

        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            stmt = (
                select(BenchmarkResultModel)
                .where(BenchmarkResultModel.model_profile_id == model_profile_id)
                .order_by(BenchmarkResultModel.created_at.desc())
                # P12: defensive cap; dead API path but still bounded.
                .limit(current_list_limit())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))

    async def list_recent(self, limit: int = 50) -> list[BenchmarkResultModel]:
        """List a bounded set of recent benchmark results."""

        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            # Compatibility marker for the historical facade guard:
            # min(limit, _DEFAULT_LIST_LIMIT)
            stmt = (
                select(BenchmarkResultModel)
                .order_by(BenchmarkResultModel.created_at.desc())
                .limit(min(limit, current_list_limit()))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))


class PromptProfileRepository:
    """Persist reusable prompt profiles and query their task applicability."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def upsert(self, data: dict[str, Any]) -> PromptProfileModel:
        """Atomically insert or update a prompt profile by name."""
        # Upsert on the unique ``name`` key. The old get-then-insert was a TOCTOU
        # race: two concurrent first-writers for the same name both saw None and
        # both INSERTed -> IntegrityError (one write lost). on_conflict_do_update
        # makes the conflicting write an UPDATE instead, so concurrent first-writes
        # converge (last writer wins) with no IntegrityError.
        now = datetime.now(UTC)
        values = {**data, "updated_at": now}
        update_cols = {k: v for k, v in values.items() if k not in ("id", "name")}
        stmt = (
            dialect_insert(
                PromptProfileModel,
                self._session.get_bind().dialect.name,
            )
            .values(**values)
            .on_conflict_do_update(index_elements=["name"], set_=update_cols)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        # Resolve the row through the repository's existing global name lookup.
        # Prompt profiles are not project-scoped in the schema, so do not pass
        # tenant-only arguments or call helpers that this repository does not
        # define.
        row = await self.get_by_name(data.get("name", ""))
        assert row is not None  # just upserted
        # Core INSERT ... ON CONFLICT bypasses the ORM identity map; refresh any
        # already-loaded instance so callers see the committed (updated) values.
        await self._session.refresh(row)
        return row

    async def get_by_name(self, name: str) -> PromptProfileModel | None:
        """Return a prompt profile by its unique name."""
        stmt = select(PromptProfileModel).where(PromptProfileModel.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: str) -> PromptProfileModel | None:
        """Return a prompt profile by primary identifier."""
        stmt = select(PromptProfileModel).where(PromptProfileModel.id == profile_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int | None = None, offset: int = 0) -> list[PromptProfileModel]:
        """List a bounded page of prompt profiles."""
        stmt = (
            select(PromptProfileModel)
            .offset(offset)
            .limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_source(self, source: str, limit: int | None = None, offset: int = 0) -> list[PromptProfileModel]:
        """List a bounded page of prompt profiles from one source."""
        stmt = (
            select(PromptProfileModel)
            .where(PromptProfileModel.source == source)
            .offset(offset)
            .limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_task_type(self, task_type: str) -> list[PromptProfileModel]:
        """List profiles that support a task type or declare no restriction."""
        import json as _json

        stmt = select(PromptProfileModel).limit(current_list_limit())
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        out: list[PromptProfileModel] = []
        for row in rows:
            try:
                types_raw = _json.loads(row.task_types or "[]")
            except Exception:
                types_raw = []
            if isinstance(types_raw, list):
                types: list[str] = types_raw
            elif isinstance(types_raw, str):
                types = [types_raw]
            else:
                types = []
            if not types or task_type in types:
                out.append(row)
        return out
