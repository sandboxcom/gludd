"""Unit tests for OrnithTrainingRepo (scaffold/outcome pairs).

Uses SQLite in-memory with async sessions via aiosqlite, mirroring
tests/unit/test_human_todo_repo.py.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.ornith import sandbox as ornith_sandbox
from general_ludd.ornith.training_repo import (
    REWARD_MAP,
    OrnithInvocation,
    OrnithTrainingRepo,
    compute_reward,
    compute_scaffold_hash,
)


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


class TestOrnithTrainingRepo:
    @pytest.mark.asyncio
    async def test_record_pair_returns_id(self, async_session: AsyncSession):
        repo = OrnithTrainingRepo(async_session)
        row = await repo.record_pair(_inv())
        await async_session.flush()
        assert row.id is not None
        assert row.id.startswith("ORN-")
        assert row.outcome_status == "pending"

    @pytest.mark.asyncio
    async def test_set_outcome_updates_status_and_details(
        self, async_session: AsyncSession
    ):
        repo = OrnithTrainingRepo(async_session)
        row = await repo.record_pair(_inv())
        await async_session.flush()
        updated = await repo.set_outcome(
            row.id, "succeeded", {"gate_passed": True}
        )
        assert updated.outcome_status == "succeeded"
        assert updated.outcome_set_at is not None
        details = _json.loads(updated.outcome_details)
        assert details["gate_passed"] is True

    @pytest.mark.asyncio
    async def test_get_pending_outcomes_filters_by_age(
        self, async_session: AsyncSession
    ):
        repo = OrnithTrainingRepo(async_session)
        old_inv = _inv(
            invoked_at=datetime.now(UTC) - timedelta(minutes=30)
        )
        fresh_inv = _inv(
            invoked_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        await repo.record_pair(old_inv)
        await repo.record_pair(fresh_inv)
        await async_session.flush()
        old_only = await repo.get_pending_outcomes(
            older_than_minutes=10
        )
        assert len(old_only) == 1
        all_pending = await repo.get_pending_outcomes()
        assert len(all_pending) == 2

    @pytest.mark.asyncio
    async def test_export_dataset_writes_jsonl_with_reward(
        self, async_session: AsyncSession, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ornith_sandbox, "_ALLOWED_EXPORT_ROOTS", [str(tmp_path)])
        repo = OrnithTrainingRepo(async_session)
        r1 = await repo.record_pair(_inv())
        r2 = await repo.record_pair(_inv(scaffold_content="different"))
        await async_session.flush()
        await repo.set_outcome(r1.id, "succeeded", {"gate_passed": True})
        await repo.set_outcome(r2.id, "reverted", {"reverted_because": "broke build"})
        await async_session.flush()
        out = tmp_path / "ds.jsonl"
        path = await repo.export_dataset(out_path=out)
        assert path == out
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = _json.loads(line)
            assert "scaffold" in obj
            assert "outcome" in obj
            assert "reward" in obj
            assert obj["scaffold"]["content"]
            assert obj["scaffold"]["hash"]
        rewards = sorted(_json.loads(line)["reward"] for line in lines)
        assert rewards == [-0.2, 1.0]

    @pytest.mark.asyncio
    async def test_export_dataset_skips_pending(
        self, async_session: AsyncSession, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(ornith_sandbox, "_ALLOWED_EXPORT_ROOTS", [str(tmp_path)])
        repo = OrnithTrainingRepo(async_session)
        r1 = await repo.record_pair(_inv())
        await repo.record_pair(_inv(scaffold_content="another"))
        await async_session.flush()
        await repo.set_outcome(r1.id, "succeeded")
        # r2 stays pending
        out = tmp_path / "ds.jsonl"
        await repo.export_dataset(out_path=out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert _json.loads(lines[0])["outcome"]["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_reward_map_computes_correctly_per_status(self):
        # Spec values.
        assert compute_reward("succeeded") == 1.0
        assert compute_reward("applied") == 0.7
        assert compute_reward("rejected_by_review") == 0.1
        assert compute_reward("rejected_by_gate") == 0.0
        assert compute_reward("reverted") == -0.2
        assert compute_reward("pending") is None
        assert REWARD_MAP["succeeded"] == 1.0

    @pytest.mark.asyncio
    async def test_stats_returns_counts_and_success_rate(
        self, async_session: AsyncSession
    ):
        repo = OrnithTrainingRepo(async_session)
        ids = []
        for i in range(3):
            row = await repo.record_pair(
                _inv(scaffold_content=f"c{i}", tokens_consumed=1000)
            )
            ids.append(row.id)
        row4 = await repo.record_pair(
            _inv(scaffold_content="failed", tokens_consumed=500)
        )
        await async_session.flush()
        for i in ids:
            await repo.set_outcome(i, "succeeded")
        await repo.set_outcome(row4.id, "rejected_by_gate")
        await async_session.flush()
        s = await repo.stats()
        assert s["total"] == 4
        assert s["counts_by_status"]["succeeded"] == 3
        assert s["counts_by_status"]["rejected_by_gate"] == 1
        # 3 of 4 resolved -> success_rate = 3/4 = 0.75
        assert abs(s["success_rate"] - 0.75) < 1e-6
        # (1000*3 + 500) / 4 = 875
        assert abs(s["avg_tokens_per_call"] - 875.0) < 1e-6

    @pytest.mark.asyncio
    async def test_scaffold_hash_dedup(self, async_session: AsyncSession):
        # Same content -> same hash, regardless of other fields.
        h1 = compute_scaffold_hash("abc")
        h2 = compute_scaffold_hash("abc")
        h3 = compute_scaffold_hash("xyz")
        assert h1 == h2
        assert h1 != h3
        repo = OrnithTrainingRepo(async_session)
        r1 = await repo.record_pair(_inv(scaffold_content="same"))
        r2 = await repo.record_pair(
            _inv(scaffold_content="same", task_description="different task")
        )
        await async_session.flush()
        assert r1.scaffold_hash == r2.scaffold_hash
        assert r1.scaffold_hash == compute_scaffold_hash("same")
