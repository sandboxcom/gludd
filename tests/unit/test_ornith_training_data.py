"""Unit tests for TrainingDataCollector and TrainingExample.

Uses SQLite in-memory with async sessions via aiosqlite, mirroring
tests/unit/test_ornith_training_repo.py.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.ornith import sandbox as ornith_sandbox
from general_ludd.ornith.training_data import (
    TrainingDataCollector,
    TrainingExample,
)
from general_ludd.ornith.training_repo import OrnithInvocation


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def collector(async_session: AsyncSession) -> TrainingDataCollector:
    return TrainingDataCollector(async_session)


def _inv(**overrides) -> OrnithInvocation:
    defaults = dict(
        task_description="Fix the off-by-one in loop.py",
        target_files=["src/x/loop.py"],
        scaffold_kind="patch",
        scaffold_content="--- a/x\n+++ b/x\n@@\n-  for i in range(n)\n+  for i in range(n+1)\n",
        agent_id="agent-1",
        iterations_used=3,
        tokens_consumed=1500,
        model_sha="ornith-9b-sha-abc",
    )
    defaults.update(overrides)
    return OrnithInvocation(**defaults)


class TestTrainingExample:
    def test_to_dict_roundtrip(self):
        ex = TrainingExample(
            instruction="Fix the bug",
            response="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
            outcome="succeeded",
            reward=1.0,
            metadata={"pair_id": "ORN-abc123", "tokens_consumed": 500},
        )
        d = ex.to_dict()
        assert d["instruction"] == "Fix the bug"
        assert d["response"].startswith("--- a/x")
        assert d["outcome"] == "succeeded"
        assert d["reward"] == 1.0
        assert d["metadata"]["pair_id"] == "ORN-abc123"

    async def test_from_pair(self, async_session: AsyncSession):
        collector = TrainingDataCollector(async_session)
        row = await collector.capture(
            instruction="Refactor foo.py",
            response="# scaffold content",
            scaffold_kind="patch",
            agent_id="agent-1",
            tokens_consumed=999,
        )
        await async_session.flush()
        ex = TrainingExample.from_pair(row)
        assert ex.instruction == "Refactor foo.py"
        assert ex.response == "# scaffold content"
        assert ex.outcome == "pending"
        assert ex.reward is None  # pending has no reward
        assert ex.metadata["pair_id"] == row.id
        assert ex.metadata["tokens_consumed"] == 999


class TestTrainingDataCollector:
    async def test_capture_returns_row(self, collector: TrainingDataCollector):
        row = await collector.capture(
            instruction="Fix the bug",
            response="some patch",
            scaffold_kind="patch",
            agent_id="agent-1",
        )
        await collector._session.flush()
        assert row.id is not None
        assert row.id.startswith("ORN-")
        assert row.outcome_status == "pending"
        assert row.task_description == "Fix the bug"
        assert row.scaffold_content == "some patch"

    async def test_capture_propagates_fields(self, collector: TrainingDataCollector):
        row = await collector.capture(
            instruction="Refactor loop",
            response="# patch\n--- a/x\n+++ b/x\n",
            scaffold_kind="patch",
            agent_id="agent-2",
            target_files=["src/x/loop.py", "src/x/utils.py"],
            model_sha="model-abc-123",
            project_id="proj-foo",
            iterations_used=5,
            tokens_consumed=2500,
        )
        await collector._session.flush()
        assert row.agent_id == "agent-2"
        files = _json.loads(row.target_files)
        assert files == ["src/x/loop.py", "src/x/utils.py"]
        assert row.model_sha == "model-abc-123"
        assert row.project_id == "proj-foo"
        assert row.iterations_used == 5
        assert row.tokens_consumed == 2500

    async def test_capture_many(self, collector: TrainingDataCollector):
        invs = [
            _inv(scaffold_content=f"content-{i}", task_description=f"task-{i}")
            for i in range(3)
        ]
        rows = await collector.capture_many(invs)
        assert len(rows) == 3
        for i, row in enumerate(rows):
            assert row.task_description == f"task-{i}"
            assert row.scaffold_content == f"content-{i}"

    async def test_resolve_outcome(self, collector: TrainingDataCollector):
        row = await collector.capture(
            instruction="Test resolve",
            response="# patch",
            scaffold_kind="patch",
            agent_id="agent-1",
        )
        await collector._session.flush()
        updated = await collector.resolve_outcome(
            row.id, "succeeded", {"gate_passed": True}
        )
        assert updated.outcome_status == "succeeded"
        assert updated.outcome_set_at is not None
        details = _json.loads(updated.outcome_details)
        assert details["gate_passed"] is True

    async def test_batch_resolve(self, collector: TrainingDataCollector):
        rows = []
        for i in range(3):
            row = await collector.capture(
                instruction=f"task-{i}",
                response=f"patch-{i}",
                scaffold_kind="patch",
                agent_id="agent-1",
            )
            rows.append(row)
        await collector._session.flush()

        updates = [
            (rows[0].id, "succeeded", {"score": 1.0}),
            (rows[1].id, "rejected_by_gate", {"gate_output": "FAIL"}),
            (rows[2].id, "reverted", {"reason": "broke build"}),
        ]
        updated = await collector.batch_resolve(updates)
        assert len(updated) == 3
        statuses = {r.id: r.outcome_status for r in updated}
        assert statuses[rows[0].id] == "succeeded"
        assert statuses[rows[1].id] == "rejected_by_gate"
        assert statuses[rows[2].id] == "reverted"

    async def test_get_returns_none_for_missing(
        self, collector: TrainingDataCollector
    ):
        assert await collector.get("ORN-nonexistent") is None

    async def test_get_returns_row(self, collector: TrainingDataCollector):
        row = await collector.capture(
            instruction="test get", response="# p", scaffold_kind="patch", agent_id="a"
        )
        await collector._session.flush()
        fetched = await collector.get(row.id)
        assert fetched is not None
        assert fetched.id == row.id

    async def test_list_examples_returns_training_examples(
        self, collector: TrainingDataCollector
    ):
        await collector.capture(
            instruction="task 1", response="# p1", scaffold_kind="patch", agent_id="a"
        )
        await collector.capture(
            instruction="task 2", response="# p2", scaffold_kind="patch", agent_id="a"
        )
        await collector._session.flush()
        examples = await collector.list_examples()
        assert len(examples) == 2
        assert all(isinstance(ex, TrainingExample) for ex in examples)
        assert {ex.instruction for ex in examples} == {"task 1", "task 2"}

    async def test_list_examples_filters_by_status(
        self, collector: TrainingDataCollector
    ):
        r1 = await collector.capture(
            instruction="good", response="# p1", scaffold_kind="patch", agent_id="a"
        )
        r2 = await collector.capture(
            instruction="bad", response="# p2", scaffold_kind="patch", agent_id="a"
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector.resolve_outcome(r2.id, "reverted")
        await collector._session.flush()
        succeeded = await collector.list_examples(status="succeeded")
        assert len(succeeded) == 1
        assert succeeded[0].instruction == "good"

    async def test_list_by_statuses(self, collector: TrainingDataCollector):
        r1 = await collector.capture(
            instruction="a", response="# p1", scaffold_kind="patch", agent_id="a"
        )
        r2 = await collector.capture(
            instruction="b", response="# p2", scaffold_kind="patch", agent_id="a"
        )
        await collector.capture(
            instruction="c", response="# p3", scaffold_kind="patch", agent_id="a"
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector.resolve_outcome(r2.id, "reverted")
        # r3 stays pending
        await collector._session.flush()
        results = await collector.list_by_statuses(["succeeded", "reverted"])
        assert len(results) == 2
        statuses = {ex.outcome for ex in results}
        assert statuses == {"succeeded", "reverted"}

    async def test_export_finetuning_dataset_skips_pending(
        self, collector: TrainingDataCollector, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ornith_sandbox, "_ALLOWED_EXPORT_ROOTS", [str(tmp_path)])
        r1 = await collector.capture(
            instruction="good",
            response="# good patch",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector.capture(
            instruction="pending one",
            response="# pending",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector._session.flush()

        out = tmp_path / "ft.jsonl"
        await collector.export_finetuning_dataset(out_path=out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        obj = _json.loads(lines[0])
        assert obj["instruction"] == "good"
        assert obj["outcome"] == "succeeded"
        assert obj["reward"] == 1.0

    async def test_export_finetuning_dataset_only_positive(
        self, collector: TrainingDataCollector, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ornith_sandbox, "_ALLOWED_EXPORT_ROOTS", [str(tmp_path)])
        r1 = await collector.capture(
            instruction="good",
            response="# p1",
            scaffold_kind="patch",
            agent_id="a",
        )
        r2 = await collector.capture(
            instruction="bad",
            response="# p2",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector.resolve_outcome(r2.id, "reverted")
        await collector._session.flush()

        out = tmp_path / "positive.jsonl"
        await collector.export_finetuning_dataset(out_path=out, only_positive=True)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert _json.loads(lines[0])["reward"] == 1.0

    async def test_export_rollout_log_includes_all_statuses(
        self, collector: TrainingDataCollector, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ornith_sandbox, "_ALLOWED_EXPORT_ROOTS", [str(tmp_path)])
        r1 = await collector.capture(
            instruction="good", response="# p1", scaffold_kind="patch", agent_id="a"
        )
        await collector.capture(
            instruction="pending",
            response="# p2",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector._session.flush()

        out = tmp_path / "rollout.jsonl"
        await collector.export_rollout_log(out_path=out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        statuses = {_json.loads(line)["outcome_status"] for line in lines}
        assert statuses == {"succeeded", "pending"}

    async def test_quality_report(self, collector: TrainingDataCollector):
        for i in range(4):
            await collector.capture(
                instruction=f"task-{i}",
                response=f"patch-{i}",
                scaffold_kind="patch",
                agent_id="a",
                tokens_consumed=1000,
            )
        await collector.capture(
            instruction="different kind",
            response="# playbook",
            scaffold_kind="playbook",
            agent_id="a",
        )
        await collector._session.flush()

        # resolve some
        pairs = await collector.list_examples()
        for i, pair in enumerate(pairs):
            if i < 2:
                await collector.resolve_outcome(
                    pair.metadata["pair_id"], "succeeded"
                )
            elif i == 2:
                await collector.resolve_outcome(
                    pair.metadata["pair_id"], "reverted"
                )
        await collector._session.flush()

        report = await collector.quality_report()
        assert report["total_pairs"] == 5
        assert report["resolved"] == 3
        assert report["pending"] == 2
        assert "positive_examples" in report
        assert "negative_examples" in report
        assert "token_stats" in report
        assert report["token_stats"]["avg"] > 0
        assert "patch" in report["counts_by_scaffold_kind"]
        assert "playbook" in report["counts_by_scaffold_kind"]

    async def test_deduplicate_removes_duplicate_pending(
        self, collector: TrainingDataCollector
    ):
        await collector.capture(
            instruction="task 1",
            response="same content",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector.capture(
            instruction="task 2",
            response="same content",
            scaffold_kind="patch",
            agent_id="b",
        )
        await collector._session.flush()

        removed = await collector.deduplicate()
        assert removed == 1

        remaining = await collector.list_examples()
        assert len(remaining) == 1

    async def test_deduplicate_skips_resolved(
        self, collector: TrainingDataCollector
    ):
        r1 = await collector.capture(
            instruction="task 1",
            response="same content",
            scaffold_kind="patch",
            agent_id="a",
        )
        r2 = await collector.capture(
            instruction="task 2",
            response="same content",
            scaffold_kind="patch",
            agent_id="b",
        )
        await collector._session.flush()
        await collector.resolve_outcome(r1.id, "succeeded")
        await collector.resolve_outcome(r2.id, "succeeded")
        await collector._session.flush()

        removed = await collector.deduplicate(statuses=("succeeded",))
        assert removed == 1

    async def test_resolve_pending_filters_by_age(
        self, collector: TrainingDataCollector
    ):
        old = _inv(
            invoked_at=datetime.now(UTC) - timedelta(minutes=30)
        )
        fresh = _inv(
            invoked_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        await collector.capture_many([old, fresh])
        await collector._session.flush()

        old_only = await collector.resolve_pending(older_than_minutes=10)
        assert len(old_only) == 1

        all_pending = await collector.resolve_pending()
        assert len(all_pending) == 2

    async def test_stats_runs_without_errors(
        self, collector: TrainingDataCollector
    ):
        stats = await collector.stats()
        assert isinstance(stats, dict)
        assert stats["total"] == 0
        assert stats["success_rate"] == 0.0

    async def test_quality_report_empty(self, collector: TrainingDataCollector):
        report = await collector.quality_report()
        assert report["total_pairs"] == 0
        assert report["resolved"] == 0

    async def test_deduplicate_no_duplicates(
        self, collector: TrainingDataCollector
    ):
        await collector.capture(
            instruction="task 1",
            response="content a",
            scaffold_kind="patch",
            agent_id="a",
        )
        await collector.capture(
            instruction="task 2",
            response="content b",
            scaffold_kind="patch",
            agent_id="b",
        )
        await collector._session.flush()
        removed = await collector.deduplicate()
        assert removed == 0
