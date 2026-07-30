"""Integration tests for the OutcomeObserver.

The observer resolves the outcome half of Ornith training pairs via
gate/review/revert event hooks. These tests prove the three hook entry
points correctly update pair statuses in the DB.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.ornith.outcome_observer import OutcomeObserver
from general_ludd.ornith.training_repo import OrnithInvocation, OrnithTrainingRepo


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
async def session_factory(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def async_session(session_factory) -> AsyncSession:
    async with session_factory() as session:
        yield session


def _inv(**overrides) -> OrnithInvocation:
    defaults = dict(
        task_description="t",
        target_files=["a.py"],
        scaffold_kind="patch",
        scaffold_content="diff",
        agent_id="a",
        tokens_consumed=100,
    )
    defaults.update(overrides)
    return OrnithInvocation(**defaults)


async def _record(session_factory, **inv_kwargs) -> str:
    async with session_factory() as session:
        repo = OrnithTrainingRepo(session)
        row = await repo.record_pair(_inv(**inv_kwargs))
        await session.commit()
        return row.id


class TestOutcomeObserver:
    @pytest.mark.asyncio
    async def test_gate_green_marks_succeeded(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        await obs.on_gate_complete(pair_id, gate_passed=True)
        async with session_factory() as session:
            repo = OrnithTrainingRepo(session)
            row = await repo.get(pair_id)
            assert row is not None
            assert row.outcome_status == "succeeded"
            assert row.outcome_set_at is not None

    @pytest.mark.asyncio
    async def test_gate_red_marks_rejected_by_gate(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        await obs.on_gate_complete(pair_id, gate_passed=False)
        async with session_factory() as session:
            repo = OrnithTrainingRepo(session)
            row = await repo.get(pair_id)
            assert row is not None
            assert row.outcome_status == "rejected_by_gate"

    @pytest.mark.asyncio
    async def test_review_rejection_marks_rejected_by_review(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        await obs.on_review_decision(pair_id, approved=False, reason="bad style")
        async with session_factory() as session:
            repo = OrnithTrainingRepo(session)
            row = await repo.get(pair_id)
            assert row is not None
            assert row.outcome_status == "rejected_by_review"
            import json as _json

            details = _json.loads(row.outcome_details)
            assert details["review_reason"] == "bad style"

    @pytest.mark.asyncio
    async def test_review_approval_does_not_resolve(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        await obs.on_review_decision(pair_id, approved=True, reason="lgtm")
        async with session_factory() as session:
            repo = OrnithTrainingRepo(session)
            row = await repo.get(pair_id)
            assert row is not None
            # Approval alone doesn't resolve -- still pending until gate.
            assert row.outcome_status == "pending"

    @pytest.mark.asyncio
    async def test_git_revert_marks_reverted(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        await obs.on_commit_revert(pair_id, reason="broke the build")
        async with session_factory() as session:
            repo = OrnithTrainingRepo(session)
            row = await repo.get(pair_id)
            assert row is not None
            assert row.outcome_status == "reverted"
            import json as _json

            details = _json.loads(row.outcome_details)
            assert details["reverted_because"] == "broke the build"

    @pytest.mark.asyncio
    async def test_subscribers_fire_on_events(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        seen: list[tuple[str, bool]] = []

        async def _listener(pid: str, ok: bool) -> None:
            seen.append((pid, ok))

        obs.subscribe_gate(_listener)
        await obs.on_gate_complete(pair_id, gate_passed=True)
        assert (pair_id, True) in seen

    @pytest.mark.asyncio
    async def test_review_and_revert_subscribers_fire(self, session_factory):
        review_id = await _record(session_factory)
        revert_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)
        reviews: list[tuple[str, bool, str]] = []
        reverts: list[tuple[str, str]] = []

        async def review_listener(pid: str, approved: bool, reason: str) -> None:
            reviews.append((pid, approved, reason))

        async def revert_listener(pid: str, reason: str) -> None:
            reverts.append((pid, reason))

        obs.subscribe_review(review_listener)
        obs.subscribe_revert(revert_listener)
        await obs.on_review_decision(review_id, False, "unsafe")
        await obs.on_commit_revert(revert_id, "regression")

        assert reviews == [(review_id, False, "unsafe")]
        assert reverts == [(revert_id, "regression")]

    @pytest.mark.asyncio
    async def test_mark_applied_and_unknown_pair_are_nonfatal(self, session_factory):
        pair_id = await _record(session_factory)
        obs = OutcomeObserver(session_factory)

        await obs.mark_applied(pair_id)
        await obs.mark_applied("unknown-pair")

        async with session_factory() as session:
            row = await OrnithTrainingRepo(session).get(pair_id)
            assert row is not None
            assert row.outcome_status == "applied"

    @pytest.mark.asyncio
    async def test_poll_once_and_start_stop_lifecycle(self, session_factory):
        await _record(session_factory)
        obs = OutcomeObserver(
            session_factory,
            poll_interval_seconds=10,
            pending_older_than_minutes=0,
        )

        await obs._poll_once()
        task = obs.start()
        assert task.get_name() == "ornith-outcome-observer"
        await obs.stop()
        assert obs._task is None


async def _noop() -> None:
    return None
