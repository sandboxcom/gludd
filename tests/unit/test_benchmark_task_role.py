"""Tests for BenchmarkResult task_role field: schema, model, repository."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.schemas.benchmark import BenchmarkResult, BenchmarkScores, TaskRole, TaskType


class TestTaskRoleEnum:
    def test_valid_roles(self):
        assert TaskRole.PLANNER == "planner"
        assert TaskRole.CODER == "coder"
        assert TaskRole.REVIEWER == "reviewer"
        assert TaskRole.EDITOR == "editor"
        assert TaskRole.COMPACTOR == "compactor"
        assert TaskRole.ENUMERATOR == "enumerator"


class TestBenchmarkResultSchema:
    def test_defaults_to_none(self):
        result = BenchmarkResult(
            model_profile_id="test-model",
            task_type=TaskType.BUG_FIX,
            scores=BenchmarkScores(
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
            ),
        )
        assert result.task_role is None

    def test_accepts_planner(self):
        result = BenchmarkResult(
            model_profile_id="test-model",
            task_type=TaskType.BUG_FIX,
            scores=BenchmarkScores(
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
            ),
            task_role=TaskRole.PLANNER,
        )
        assert result.task_role == TaskRole.PLANNER

    def test_accepts_coder(self):
        result = BenchmarkResult(
            model_profile_id="test-model",
            task_type=TaskType.BUG_FIX,
            scores=BenchmarkScores(
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
            ),
            task_role=TaskRole.CODER,
        )
        assert result.task_role == TaskRole.CODER

    def test_accepts_reviewer(self):
        result = BenchmarkResult(
            model_profile_id="test-model",
            task_type=TaskType.BUG_FIX,
            scores=BenchmarkScores(
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
            ),
            task_role=TaskRole.REVIEWER,
        )
        assert result.task_role == TaskRole.REVIEWER


class TestBenchmarkResultModel:
    def test_has_task_role_field(self):
        assert hasattr(BenchmarkResultModel, "task_role")

    def test_task_role_column_is_nullable(self):
        col = BenchmarkResultModel.__table__.c.task_role
        assert col.nullable


# — repository tests —


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


def _make_benchmark_data(**overrides):
    base = {
        "model_profile_id": "test-model",
        "task_type": "bug_fix",
        "completion_score": 0.0,
        "code_quality_score": 0.0,
        "instruction_adherence_score": 0.0,
        "token_efficiency_score": 0.0,
        "success": True,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "time_seconds": 0.0,
        "error_message": "",
    }
    base.update(overrides)
    return base


class TestBenchmarkRepositoryTaskRole:
    @pytest.mark.asyncio
    async def test_record_result_with_task_role(self, session):
        repo = BenchmarkRepository(session)
        row = await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.8,
            instruction_adherence_score=0.85,
            token_efficiency_score=0.7,
            task_role="planner",
        ))
        assert row.task_role == "planner"

    @pytest.mark.asyncio
    async def test_record_result_defaults_to_none(self, session):
        repo = BenchmarkRepository(session)
        row = await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.8,
            instruction_adherence_score=0.85,
            token_efficiency_score=0.7,
        ))
        assert row.task_role is None

    @pytest.mark.asyncio
    async def test_filter_by_task_role(self, session):
        repo = BenchmarkRepository(session)
        for _ in range(3):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
                task_role="planner",
            ))
        for _ in range(2):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.7,
                code_quality_score=0.6,
                instruction_adherence_score=0.65,
                token_efficiency_score=0.5,
                task_role="coder",
            ))

        planner_agg = await repo.get_aggregate_scores(task_role="planner")
        assert len(planner_agg) >= 1
        assert all(r["task_role"] == "planner" for r in planner_agg)

    @pytest.mark.asyncio
    async def test_aggregate_includes_task_role_in_output(self, session):
        repo = BenchmarkRepository(session)
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.8,
            instruction_adherence_score=0.85,
            token_efficiency_score=0.7,
            task_role="planner",
        ))
        agg = await repo.get_aggregate_scores(task_type="bug_fix")
        assert len(agg) >= 1
        assert "task_role" in agg[0]
        assert agg[0]["task_role"] == "planner"

    @pytest.mark.asyncio
    async def test_role_breakdown_in_aggregates(self, session):
        repo = BenchmarkRepository(session)
        for _ in range(3):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
                task_role="planner",
            ))
        for _ in range(2):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.7,
                code_quality_score=0.6,
                instruction_adherence_score=0.65,
                token_efficiency_score=0.5,
                task_role="coder",
            ))
        for _ in range(1):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.8,
                code_quality_score=0.7,
                instruction_adherence_score=0.75,
                token_efficiency_score=0.6,
                task_role="reviewer",
            ))

        agg = await repo.get_aggregate_scores(task_type="bug_fix")
        roles = {r["task_role"] for r in agg}
        assert "planner" in roles
        assert "coder" in roles
        assert "reviewer" in roles

        planner_row = next(r for r in agg if r["task_role"] == "planner")
        assert planner_row["sample_count"] == 3
        coder_row = next(r for r in agg if r["task_role"] == "coder")
        assert coder_row["sample_count"] == 2
        reviewer_row = next(r for r in agg if r["task_role"] == "reviewer")
        assert reviewer_row["sample_count"] == 1
