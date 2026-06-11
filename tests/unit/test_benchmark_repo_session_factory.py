from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.repository import BenchmarkRepository


class TestBenchmarkRepositoryConstruction:
    def test_construct_with_session_factory(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine)
        repo = BenchmarkRepository(session_factory=factory)
        assert repo is not None

    def test_construct_with_session(self):
        from unittest.mock import AsyncMock

        session = AsyncMock(spec=AsyncSession)
        repo = BenchmarkRepository(session=session)
        assert repo is not None

    async def test_record_result_with_session_factory(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine)

        from general_ludd.db.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        repo = BenchmarkRepository(session_factory=factory)
        result = await repo.record_result(data={
            "model_profile_id": "test-model",
            "task_type": "bug_fix",
            "prompt_profile_id": "test-prompt",
            "completion_score": 0.8,
            "code_quality_score": 0.7,
            "instruction_adherence_score": 0.9,
            "token_efficiency_score": 0.6,
            "success": True,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.01,
            "time_seconds": 1.5,
            "error_message": "",
        })
        assert result is not None
        assert result.model_profile_id == "test-model"

        await engine.dispose()

    async def test_record_result_then_get_aggregate_scores(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine)

        from general_ludd.db.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        repo = BenchmarkRepository(session_factory=factory)
        await repo.record_result(data={
            "model_profile_id": "m1",
            "task_type": "bug_fix",
            "prompt_profile_id": "p1",
            "completion_score": 0.8,
            "code_quality_score": 0.7,
            "instruction_adherence_score": 0.9,
            "token_efficiency_score": 0.6,
            "success": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "time_seconds": 0.0,
            "error_message": "",
        })

        scores = await repo.get_aggregate_scores(task_type="bug_fix")
        assert len(scores) == 1

        await engine.dispose()
