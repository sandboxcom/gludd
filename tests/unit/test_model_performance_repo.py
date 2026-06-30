"""Unit tests for ModelPerformanceRepository using a real in-memory SQLite DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class TestRecordCall:
    async def test_basic(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        row = await repo.record_call(
            service="openai",
            model_name="gpt-4o",
            model_profile_id="openai/gpt-4o",
            task_type="code",
            success=True,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.015,
            duration_ms=450.0,
        )
        assert row.id is not None
        assert row.service == "openai"
        assert row.model_name == "gpt-4o"
        assert row.model_profile_id == "openai/gpt-4o"
        assert row.task_type == "code"
        assert row.success is True
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert row.cost_usd == 0.015
        assert row.duration_ms == 450.0
        assert row.created_at is not None
        assert row.todo_id is None
        assert row.job_id is None

    async def test_with_error(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        row = await repo.record_call(
            service="anthropic",
            model_name="claude-3-opus",
            model_profile_id="anthropic/claude-3-opus",
            success=False,
            error_code="RATE_LIMITED",
            error_message="Too many requests, try again later",
        )
        assert row.success is False
        assert row.error_code == "RATE_LIMITED"
        assert row.error_message == "Too many requests, try again later"

    async def test_with_todo_and_job_ids(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        row = await repo.record_call(
            service="google",
            model_name="gemini-pro",
            model_profile_id="google/gemini-pro",
            todo_id="TODO-001",
            job_id="JOB-001",
        )
        assert row.todo_id == "TODO-001"
        assert row.job_id == "JOB-001"

    async def test_default_task_type(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        row = await repo.record_call(
            service="openai",
            model_name="gpt-3.5-turbo",
            model_profile_id="openai/gpt-3.5-turbo",
        )
        assert row.task_type == "generation"


class TestRefreshRecentStats:
    async def test_within_window_refreshes_profiles(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            success=True, cost_usd=0.01, duration_ms=100.0,
        )
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            success=True, cost_usd=0.02, duration_ms=200.0,
        )
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            success=False, cost_usd=0.03, duration_ms=300.0,
        )

        count = await repo.refresh_recent_stats(window_hours=24)
        assert count == 1

        stats = await repo.get_stats_by_model("openai/gpt-4o")
        assert len(stats) == 1
        p = stats[0]
        assert p.total_calls == 3
        assert p.successful_calls == 2
        assert p.failed_calls == 1
        assert p.total_cost_usd == 0.06
        assert p.total_input_tokens == 0
        assert p.total_output_tokens == 0

    async def test_multiple_profiles(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            success=True, cost_usd=0.01,
        )
        await repo.record_call(
            service="anthropic", model_name="claude-3", model_profile_id="anthropic/claude-3",
            success=True, cost_usd=0.02,
        )
        await repo.record_call(
            service="google", model_name="gemini-pro", model_profile_id="google/gemini-pro",
            success=False, cost_usd=0.03,
        )

        count = await repo.refresh_recent_stats(window_hours=24)
        assert count == 3

    async def test_outside_window_is_skipped(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        old = datetime.now(UTC) - timedelta(hours=48)
        row = await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
        )
        row.created_at = old
        await session.flush()

        count = await repo.refresh_recent_stats(window_hours=24)
        assert count == 0  # no recent calls within the 24h window

    async def test_empty_log_returns_zero(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        count = await repo.refresh_recent_stats(window_hours=24)
        assert count == 0

    async def test_refresh_is_idempotent(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            success=True, cost_usd=0.01,
        )
        c1 = await repo.refresh_recent_stats(window_hours=24)
        assert c1 == 1

        c2 = await repo.refresh_recent_stats(window_hours=24)
        assert c2 == 1  # same profile still in window

        stats = await repo.get_stats_by_model("openai/gpt-4o")
        assert len(stats) == 1
        assert stats[0].total_calls == 1  # still one call


class TestGetStatsByModel:
    async def test_existing_profile(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
        )
        await repo.refresh_recent_stats(window_hours=24)
        stats = await repo.get_stats_by_model("openai/gpt-4o")
        assert len(stats) == 1
        assert stats[0].model_name == "gpt-4o"
        assert stats[0].service == "openai"

    async def test_nonexistent_profile_returns_empty(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        stats = await repo.get_stats_by_model("nonexistent/profile")
        assert stats == []

    async def test_all_profiles(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
        )
        await repo.record_call(
            service="anthropic", model_name="claude-3", model_profile_id="anthropic/claude-3",
        )
        await repo.refresh_recent_stats(window_hours=24)
        stats = await repo.get_stats_by_model()
        assert len(stats) == 2

    async def test_ordered_by_cost_desc(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            cost_usd=0.10,
        )
        await repo.record_call(
            service="anthropic", model_name="opus", model_profile_id="anthropic/opus",
            cost_usd=0.50,
        )
        await repo.refresh_recent_stats(window_hours=24)
        stats = await repo.get_stats_by_model()
        assert stats[0].model_profile_id == "anthropic/opus"
        assert stats[1].model_profile_id == "openai/gpt-4o"


class TestGetStatsByService:
    async def test_groups_by_service(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="openai", model_name="gpt-4o", model_profile_id="openai/gpt-4o",
            cost_usd=0.01,
        )
        await repo.record_call(
            service="openai", model_name="gpt-3.5-turbo", model_profile_id="openai/gpt-3.5-turbo",
            cost_usd=0.005,
        )
        await repo.record_call(
            service="anthropic", model_name="claude-3", model_profile_id="anthropic/claude-3",
            cost_usd=0.03,
        )
        await repo.refresh_recent_stats(window_hours=24)

        svc = await repo.get_stats_by_service()
        svc_by_name = {s["service"]: s for s in svc}
        assert "openai" in svc_by_name
        assert "anthropic" in svc_by_name
        assert svc_by_name["openai"]["profile_count"] == 2
        assert svc_by_name["openai"]["total_calls"] == 2
        assert svc_by_name["anthropic"]["profile_count"] == 1
        assert svc_by_name["anthropic"]["total_calls"] == 1

    async def test_empty_returns_empty_list(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        svc = await repo.get_stats_by_service()
        assert svc == []

    async def test_ordered_by_cost_desc(self, session: AsyncSession) -> None:
        repo = ModelPerformanceRepository(session=session)
        await repo.record_call(
            service="cheap", model_name="m1", model_profile_id="cheap/m1",
            cost_usd=0.01,
        )
        await repo.record_call(
            service="expensive", model_name="m2", model_profile_id="expensive/m2",
            cost_usd=0.99,
        )
        await repo.refresh_recent_stats(window_hours=24)

        svc = await repo.get_stats_by_service()
        assert svc[0]["service"] == "expensive"
        assert svc[1]["service"] == "cheap"


class TestResolveSession:
    async def test_no_session_raises(self) -> None:
        repo = ModelPerformanceRepository()
        import pytest
        with pytest.raises(RuntimeError, match="no session configured"):
            repo._resolve_session()
