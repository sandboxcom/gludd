"""Tests for PromptProfileRepository and BenchmarkRepository."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import Base
from general_ludd.db.repository import BenchmarkRepository, PromptProfileRepository


@pytest.fixture
def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    return eng


@pytest.fixture
async def session(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestPromptProfileRepository:
    @pytest.mark.asyncio
    async def test_upsert_creates(self, session):
        repo = PromptProfileRepository(session)
        profile = await repo.upsert(data={
            "name": "aider_main",
            "source": "aider",
            "prompt_text": "You are an AI coding assistant.",
            "source_url": "https://github.com/aider",
            "task_types": json.dumps(["bug_fix", "feature"]),
            "tags": json.dumps(["collected", "aider"]),
        })
        assert profile.name == "aider_main"
        assert profile.source == "aider"
        assert json.loads(profile.task_types) == ["bug_fix", "feature"]

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, session):
        repo = PromptProfileRepository(session)
        await repo.upsert(data={
            "name": "aider_main",
            "source": "aider",
            "prompt_text": "v1",
        })
        updated = await repo.upsert(data={
            "name": "aider_main",
            "source": "aider",
            "prompt_text": "v2 updated",
        })
        assert updated.prompt_text == "v2 updated"
        all_profiles = await repo.list_all()
        assert len(all_profiles) == 1

    @pytest.mark.asyncio
    async def test_get_by_name(self, session):
        repo = PromptProfileRepository(session)
        await repo.upsert(data={"name": "test_profile", "source": "test", "prompt_text": "hello"})
        found = await repo.get_by_name("test_profile")
        assert found is not None
        assert found.prompt_text == "hello"

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self, session):
        repo = PromptProfileRepository(session)
        assert await repo.get_by_name("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_by_id(self, session):
        repo = PromptProfileRepository(session)
        created = await repo.upsert(data={"name": "by_id_test", "source": "test", "prompt_text": "hi"})
        found = await repo.get_by_id(created.id)
        assert found is not None
        assert found.name == "by_id_test"

    @pytest.mark.asyncio
    async def test_list_all(self, session):
        repo = PromptProfileRepository(session)
        await repo.upsert(data={"name": "a", "source": "s1", "prompt_text": "t1"})
        await repo.upsert(data={"name": "b", "source": "s2", "prompt_text": "t2"})
        profiles = await repo.list_all()
        assert len(profiles) == 2

    @pytest.mark.asyncio
    async def test_list_by_source(self, session):
        repo = PromptProfileRepository(session)
        await repo.upsert(data={"name": "a1", "source": "aider", "prompt_text": "t1"})
        await repo.upsert(data={"name": "s1", "source": "swe_agent", "prompt_text": "t2"})
        aider = await repo.list_by_source("aider")
        assert len(aider) == 1
        assert aider[0].source == "aider"

    @pytest.mark.asyncio
    async def test_list_for_task_type(self, session):
        repo = PromptProfileRepository(session)
        await repo.upsert(data={
            "name": "general",
            "source": "test",
            "prompt_text": "t1",
            "task_types": json.dumps([]),
        })
        await repo.upsert(data={
            "name": "specific",
            "source": "test",
            "prompt_text": "t2",
            "task_types": json.dumps(["bug_fix"]),
        })
        results = await repo.list_for_task_type("bug_fix")
        names = [p.name for p in results]
        assert "general" in names
        assert "specific" in names


def _make_benchmark_data(**overrides):
    base = {
        "model_profile_id": "openai",
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


class TestBenchmarkRepository:
    @pytest.mark.asyncio
    async def test_record_result(self, session):
        repo = BenchmarkRepository(session)
        row = await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.8,
            instruction_adherence_score=0.85,
            token_efficiency_score=0.7,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.01,
        ))
        assert row.model_profile_id == "openai"
        assert row.completion_score == 0.9
        assert row.success is True

    @pytest.mark.asyncio
    async def test_record_result_with_prompt(self, session):
        repo = BenchmarkRepository(session)
        row = await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            task_type="feature",
            completion_score=0.5,
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
            prompt_profile_id="pp-aider",
        ))
        assert row.prompt_profile_id == "pp-aider"

    @pytest.mark.asyncio
    async def test_get_aggregate_scores(self, session):
        repo = BenchmarkRepository(session)
        for i in range(5):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.8 + i * 0.02,
                code_quality_score=0.7,
                instruction_adherence_score=0.75,
                token_efficiency_score=0.6,
            ))
        agg = await repo.get_aggregate_scores(task_type="bug_fix")
        assert len(agg) == 1
        assert agg[0]["sample_count"] == 5
        assert float(agg[0]["avg_completion"]) > 0.8

    @pytest.mark.asyncio
    async def test_get_aggregate_scores_failed_excluded(self, session):
        repo = BenchmarkRepository(session)
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            success=False,
        ))
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.9,
            instruction_adherence_score=0.9,
            token_efficiency_score=0.9,
        ))
        agg = await repo.get_aggregate_scores(task_type="bug_fix")
        assert len(agg) == 1
        assert agg[0]["sample_count"] == 1

    @pytest.mark.asyncio
    async def test_get_best_for_task(self, session):
        repo = BenchmarkRepository(session)
        for _ in range(4):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="openai",
                completion_score=0.9,
                code_quality_score=0.8,
                instruction_adherence_score=0.85,
                token_efficiency_score=0.7,
            ))
        for _ in range(4):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id="anthropic",
                completion_score=0.6,
                code_quality_score=0.5,
                instruction_adherence_score=0.55,
                token_efficiency_score=0.4,
            ))
        best = await repo.get_best_for_task("bug_fix", min_samples=3)
        assert best is not None
        assert best[0]["model_profile_id"] == "openai"

    @pytest.mark.asyncio
    async def test_get_best_for_task_insufficient(self, session):
        repo = BenchmarkRepository(session)
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.9,
            instruction_adherence_score=0.9,
            token_efficiency_score=0.9,
        ))
        best = await repo.get_best_for_task("bug_fix", min_samples=3)
        assert len(best) == 0

    @pytest.mark.asyncio
    async def test_get_model_scores(self, session):
        repo = BenchmarkRepository(session)
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            completion_score=0.9,
            code_quality_score=0.8,
            instruction_adherence_score=0.85,
            token_efficiency_score=0.7,
        ))
        await repo.record_result(data=_make_benchmark_data(
            model_profile_id="anthropic",
            task_type="feature",
            completion_score=0.8,
            code_quality_score=0.7,
            instruction_adherence_score=0.75,
            token_efficiency_score=0.6,
        ))
        openai_scores = await repo.get_model_scores("openai")
        assert len(openai_scores) == 1
        assert openai_scores[0].model_profile_id == "openai"

    @pytest.mark.asyncio
    async def test_list_recent(self, session):
        repo = BenchmarkRepository(session)
        for i in range(5):
            await repo.record_result(data=_make_benchmark_data(
                model_profile_id=f"model-{i}",
            ))
        recent = await repo.list_recent(limit=3)
        assert len(recent) == 3

    @pytest.mark.asyncio
    async def test_record_result_with_error(self, session):
        repo = BenchmarkRepository(session)
        row = await repo.record_result(data=_make_benchmark_data(
            model_profile_id="openai",
            success=False,
            error_message="API timeout",
        ))
        assert row.success is False
        assert row.error_message == "API timeout"
