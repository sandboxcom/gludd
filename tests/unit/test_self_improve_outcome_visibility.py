"""Tests for bounded, cross-worker self-improvement outcome visibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, OrnithTrainingPairModel
from general_ludd.ornith.training_data import TrainingExample
from general_ludd.ornith.training_repo import compute_scaffold_hash
from general_ludd.routers import self_improve
from general_ludd.self_improve.outcome_visibility import (
    OUTCOME_GROUP_LIMIT,
    OUTCOME_LOOKBACK_DAYS,
    OUTCOME_ROW_LIMIT,
    summarize_outcome_examples,
)


def _example(index: int) -> TrainingExample:
    return TrainingExample(
        instruction=f"private instruction {index}: premature stop",
        response=f"private scaffold {index}",
        outcome="rejected_by_gate",
        metadata={
            "model_sha": f"model-{index}",
            "scaffold_kind": f"kind-{index}",
            "target_files": [f"/private/path/{index}"],
            "tokens_consumed": index,
            "outcome_details": {"exception": f"private exception {index}"},
        },
    )


def test_summary_caps_groups_and_redacts_source_material() -> None:
    report = summarize_outcome_examples(
        [_example(index) for index in range(OUTCOME_ROW_LIMIT)]
    )

    assert report["status"] == "available"
    assert report["sample_count"] == OUTCOME_ROW_LIMIT
    assert report["group_count"] == OUTCOME_GROUP_LIMIT
    assert report["rows_at_limit"] is True
    assert report["groups_at_limit"] is True
    groups = report["groups"]
    assert isinstance(groups, list)
    assert len(groups) == OUTCOME_GROUP_LIMIT
    assert report["pattern_counts"] == {"premature_stop": OUTCOME_ROW_LIMIT}
    serialized = str(report)
    assert "private instruction" not in serialized
    assert "private scaffold" not in serialized
    assert "/private/path" not in serialized
    assert "private exception" not in serialized


def test_summary_reports_available_no_data_without_raw_fields() -> None:
    report = summarize_outcome_examples([])

    assert report == {
        "status": "available",
        "sample_count": 0,
        "outcome_counts": {},
        "pattern_counts": {},
        "groups": [],
        "group_count": 0,
        "rows_at_limit": False,
        "groups_at_limit": False,
        "limits": {
            "rows": OUTCOME_ROW_LIMIT,
            "lookback_days": OUTCOME_LOOKBACK_DAYS,
            "groups": OUTCOME_GROUP_LIMIT,
        },
    }


def test_summary_handles_other_patterns_and_malformed_aggregate_fields() -> None:
    examples = [
        TrainingExample(
            instruction="inline grind consumed too many tokens",
            response="private response",
            outcome="reverted",
            metadata={
                "model_sha": "",
                "scaffold_kind": None,
                "tokens_consumed": -4,
                "duration_ms": True,
            },
        ),
        TrainingExample(
            instruction="ordinary rejection",
            response="private response",
            outcome="rejected_by_review",
            metadata={},
        ),
    ]
    analyzer = MagicMock()
    analyzer.analyze.return_value = {
        "suggestions": [
            None,
            {
                "task_type": "",
                "model": None,
                "pass_rate": True,
                "sample_count": False,
                "avg_tokens": "not-a-number",
                "avg_duration_ms": -2,
                "suggestion": "",
            },
        ]
    }

    report = summarize_outcome_examples(examples, analyzer=analyzer)

    assert report["pattern_counts"] == {"generic_failure": 1, "grind_failure": 1}
    assert report["groups"] == [
        {
            "task_type": "unknown",
            "model": "unknown",
            "pass_rate": 0.0,
            "sample_count": 0,
            "avg_tokens": 0.0,
            "avg_duration_ms": 0.0,
            "suggestion": "unknown",
        }
    ]


def test_summary_ignores_non_list_analyzer_output() -> None:
    analyzer = MagicMock()
    analyzer.analyze.return_value = {"suggestions": "not-public-groups"}

    report = summarize_outcome_examples([_example(1)], analyzer=analyzer)

    assert report["groups"] == []
    assert report["group_count"] == 0


async def _app_with_shared_database() -> tuple[
    FastAPI,
    async_sessionmaker[AsyncSession],
    AsyncEngine,
]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        rows = [
            OrnithTrainingPairModel(
                id="recent-rejected",
                invoked_at=now,
                task_description="secret premature stop instruction",
                target_files='["/secret/path.py"]',
                scaffold_kind="patch",
                scaffold_content="secret scaffold",
                scaffold_hash=compute_scaffold_hash("secret scaffold"),
                iterations_used=2,
                tokens_consumed=13,
                model_sha="model-safe-id",
                outcome_status="rejected_by_review",
                outcome_details='{"exception":"secret traceback"}',
                project_id="shared-project",
                agent_id="worker-a",
            ),
            OrnithTrainingPairModel(
                id="old-rejected",
                invoked_at=now - timedelta(days=OUTCOME_LOOKBACK_DAYS + 1),
                task_description="old failure",
                target_files="[]",
                scaffold_kind="patch",
                scaffold_content="old scaffold",
                scaffold_hash=compute_scaffold_hash("old scaffold"),
                iterations_used=1,
                tokens_consumed=3,
                model_sha="old-model",
                outcome_status="rejected_by_gate",
                outcome_details="{}",
                project_id="shared-project",
                agent_id="worker-b",
            ),
            OrnithTrainingPairModel(
                id="recent-success",
                invoked_at=now,
                task_description="successful instruction",
                target_files="[]",
                scaffold_kind="patch",
                scaffold_content="successful scaffold",
                scaffold_hash=compute_scaffold_hash("successful scaffold"),
                iterations_used=1,
                tokens_consumed=5,
                model_sha="model-safe-id",
                outcome_status="succeeded",
                outcome_details="{}",
                project_id="shared-project",
                agent_id="worker-c",
            ),
        ]
        session.add_all(rows)
        await session.commit()

    app = FastAPI()
    app.state._session_factory = factory
    self_improve.register(app, {})
    return app, factory, engine


@pytest.mark.asyncio
async def test_status_reads_shared_database_once_when_worker_state_is_empty() -> None:
    app, _factory, engine = await _app_with_shared_database()
    statements: list[str] = []

    def _record_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record_select)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/admin/self-improve/status")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["findings_count"] == 0
        report = body["outcome_analysis"]
        assert report["status"] == "available"
        assert report["sample_count"] == 1
        assert report["outcome_counts"] == {"rejected_by_review": 1}
        assert report["limits"] == {
            "rows": OUTCOME_ROW_LIMIT,
            "lookback_days": OUTCOME_LOOKBACK_DAYS,
            "groups": OUTCOME_GROUP_LIMIT,
        }
        serialized = response.text
        assert "secret premature stop instruction" not in serialized
        assert "secret scaffold" not in serialized
        assert "/secret/path.py" not in serialized
        assert "secret traceback" not in serialized
        assert len(statements) == 1
        assert "LIMIT" in statements[0].upper()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record_select)
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_preserves_existing_keys_and_marks_database_unavailable() -> None:
    app = FastAPI()
    self_improve.register(
        app,
        {
            "self_improve_last_analysis": {
                "findings": [{"kind": "test"}],
                "findings_count": 1,
                "todos_enqueued": 2,
            }
        },
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/self-improve/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["findings"] == [{"kind": "test"}]
    assert body["findings_count"] == 1
    assert body["todos_enqueued"] == 2
    assert body["outcome_analysis"] == {
        "status": "unavailable",
        "reason": "database_session_factory_unavailable",
        "limits": {
            "rows": OUTCOME_ROW_LIMIT,
            "lookback_days": OUTCOME_LOOKBACK_DAYS,
            "groups": OUTCOME_GROUP_LIMIT,
        },
    }


@pytest.mark.asyncio
async def test_status_redacts_database_query_failure() -> None:
    app = FastAPI()

    def _failing_factory() -> object:
        raise RuntimeError("private database exception")

    app.state._session_factory = _failing_factory
    self_improve.register(app, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/self-improve/status")

    assert response.status_code == 200
    assert response.json()["outcome_analysis"]["status"] == "unavailable"
    assert response.json()["outcome_analysis"]["reason"] == "database_query_failed"
    assert "private database exception" not in response.text
