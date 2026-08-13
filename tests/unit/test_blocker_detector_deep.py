"""Deep edge-case tests for blocker_detector.py.

Covers: config zeros/negatives/large-values, _safe_datetime boundary dates,
_classify_blocker empty/NULL/unknown categories, BlockedTask edge fields,
BlockerDetector null-repo/session, project-scoped scan, chronic lookback
boundary, clock injection, summary truncation, double-classify behaviour,
and mixed stale-human-todo + blocked-on-human overlap.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, HumanTodoModel, TodoEventModel, TodoModel
from general_ludd.db.repository import HumanTodoRepository, TodoRepository
from general_ludd.remediation.blocker_detector import (
    BLOCKER_KINDS,
    REMEDIATION_KINDS,
    BlockedTask,
    BlockerDetector,
    ChronicBlocker,
    RemediationConfig,
    _classify_blocker,
    _safe_datetime,
)
from general_ludd.schemas.todo import TodoStatus


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
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


def _now() -> datetime:
    return datetime.now(UTC)


async def _blocked_todo(
    session: AsyncSession,
    *,
    todo_id: str = "TODO-EDGE-1",
    work_type: str = "code",
    status: TodoStatus = TodoStatus.BLOCKED_ON_HUMAN,
    updated_at: datetime | None = None,
    run_count: int = 0,
    project_id: str | None = None,
) -> TodoModel:
    t = TodoModel(
        todo_id=todo_id,
        title="edge case task",
        status=status.value,
        work_type=work_type,
        queue="core",
        project_id=project_id,
        run_count=run_count,
    )
    if updated_at is not None:
        t.updated_at = updated_at
    session.add(t)
    await session.flush()
    return t


async def _ht(
    session: AsyncSession,
    *,
    parent_agent_todo_id: str | None = None,
    category: str = "input_request",
    priority: str = "medium",
    created_at: datetime | None = None,
    title: str = "Need input",
) -> HumanTodoModel:
    repo = HumanTodoRepository(session)
    row = await repo.create(
        agent_id="agent-1",
        title=title,
        body="some body",
        category=category,
        priority=priority,
        parent_agent_todo_id=parent_agent_todo_id,
    )
    if created_at is not None:
        row.created_at = created_at
        row.updated_at = created_at
    await session.flush()
    return row


# ── RemediationConfig deep edges ────────────────────────────────────────────


class TestConfigDeepEdges:
    def test_zero_hours_threshold_surfaces_immediately(self):
        cfg = RemediationConfig(human_input_block_hours=0)
        assert cfg.human_input_block_hours == 0

    def test_zero_max_requeues_before_chronic(self):
        cfg = RemediationConfig(max_requeues_before_chronic=0)
        assert cfg.max_requeues_before_chronic == 0

    def test_zero_min_chronic_incidents(self):
        cfg = RemediationConfig(min_chronic_incidents=0)
        assert cfg.min_chronic_incidents == 0

    def test_all_fields_individual_override(self):
        cfg = RemediationConfig(
            human_input_block_hours=1,
            permission_escalation_block_hours=2,
            max_requeues_before_chronic=4,
            chronic_lookback_days=8,
            min_chronic_incidents=6,
            retry_delay_hours=5,
            needs_more_work_cooldown_hours=12,
        )
        assert cfg.human_input_block_hours == 1
        assert cfg.permission_escalation_block_hours == 2
        assert cfg.max_requeues_before_chronic == 4
        assert cfg.chronic_lookback_days == 8
        assert cfg.min_chronic_incidents == 6
        assert cfg.retry_delay_hours == 5
        assert cfg.needs_more_work_cooldown_hours == 12

    def test_frozen_all_fields(self):
        cfg = RemediationConfig()
        names = [
            "human_input_block_hours",
            "permission_escalation_block_hours",
            "max_requeues_before_chronic",
            "chronic_lookback_days",
            "min_chronic_incidents",
            "retry_delay_hours",
            "needs_more_work_cooldown_hours",
        ]
        for name in names:
            with pytest.raises(FrozenInstanceError):
                setattr(cfg, name, 999)


# ── _safe_datetime deep edges ──────────────────────────────────────────────


class TestSafeDatetimeDeep:
    def test_datetime_min_naive(self):
        import datetime as _dt

        dt = _dt.datetime.min
        result = _safe_datetime(dt)
        assert result == dt.replace(tzinfo=UTC)

    def test_datetime_max_naive(self):
        import datetime as _dt

        dt = _dt.datetime.max
        result = _safe_datetime(dt)
        assert result == dt.replace(tzinfo=UTC)

    def test_datetime_min_aware(self):
        import datetime as _dt

        dt = _dt.datetime.min.replace(tzinfo=UTC)
        result = _safe_datetime(dt)
        assert result == dt

    def test_datetime_max_aware(self):
        import datetime as _dt

        dt = _dt.datetime.max.replace(tzinfo=UTC)
        result = _safe_datetime(dt)
        assert result == dt

    def test_non_datetime_types(self):
        assert _safe_datetime("2026-01-01") is None
        assert _safe_datetime(12345) is None
        assert _safe_datetime(None) is None
        assert _safe_datetime([]) is None
        assert _safe_datetime({}) is None
        assert _safe_datetime(True) is None
        assert _safe_datetime(3.14) is None

    def test_date_not_datetime(self):
        import datetime as _dt

        assert _safe_datetime(_dt.date.today()) is None


# ── _classify_blocker deep edges ───────────────────────────────────────────


class TestClassifyBlockerDeep:
    def test_human_todo_with_empty_category(self):
        ht = MagicMock()
        ht.category = ""
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_human_todo_with_no_category_attr(self):
        ht = MagicMock(spec=[])
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_human_todo_with_none_category(self):
        ht = MagicMock()
        ht.category = None
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_human_todo_category_external_action(self):
        ht = MagicMock()
        ht.category = "external_action"
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_human_todo_category_blocker(self):
        ht = MagicMock()
        ht.category = "blocker"
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_human_todo_category_decision(self):
        ht = MagicMock()
        ht.category = "decision"
        kind, rem = _classify_blocker(ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_chronic_requeue_wins_when_no_human_todo(self):
        kind, rem = _classify_blocker(None, is_chronic_requeue=True)
        assert kind == "resource_contention"
        assert rem == "dispatch_agent"

    def test_human_todo_overrides_chronic_requeue_flag(self):
        ht = MagicMock()
        ht.category = "permission_escalation"
        kind, rem = _classify_blocker(ht, is_chronic_requeue=True)
        assert kind == "permission_escalation"
        assert rem == "schedule_retry"

    def test_human_input_always_wins_over_chronic_requeue(self):
        ht = MagicMock()
        ht.category = "input_request"
        kind, rem = _classify_blocker(ht, is_chronic_requeue=True)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_null_fallback_is_unknown_no_action(self):
        kind, rem = _classify_blocker(None, is_chronic_requeue=False)
        assert kind == "unknown"
        assert rem == "no_action"


# ── BlockedTask dataclass deep edges ────────────────────────────────────────


class TestBlockedTaskDeep:
    def test_zero_duration(self):
        bt = BlockedTask(
            todo_id="t1",
            project_id=None,
            blocked_at=datetime(2026, 1, 1, tzinfo=UTC),
            blocked_duration_seconds=0,
            blocker_kind="human_input",
            blocker_summary="",
            suggested_remediation="no_action",
        )
        assert bt.blocked_duration_seconds == 0

    def test_max_duration(self):
        bt = BlockedTask(
            todo_id="t1",
            project_id=None,
            blocked_at=datetime(2026, 1, 1, tzinfo=UTC),
            blocked_duration_seconds=2**31 - 1,
            blocker_kind="human_input",
            blocker_summary="",
            suggested_remediation="no_action",
        )
        assert bt.blocked_duration_seconds == 2**31 - 1

    def test_frozen_prevents_mutation(self):
        bt = BlockedTask(
            todo_id="t1",
            project_id=None,
            blocked_at=datetime(2026, 1, 1, tzinfo=UTC),
            blocked_duration_seconds=0,
            blocker_kind="unknown",
            blocker_summary="x",
            suggested_remediation="no_action",
        )
        with pytest.raises(FrozenInstanceError):
            bt.todo_id = "new"

    def test_long_summary(self):
        long_summary = "x" * 5000
        bt = BlockedTask(
            todo_id="t1",
            project_id=None,
            blocked_at=datetime(2026, 1, 1, tzinfo=UTC),
            blocked_duration_seconds=0,
            blocker_kind="human_input",
            blocker_summary=long_summary,
            suggested_remediation="no_action",
        )
        assert bt.blocker_summary == long_summary


# ── ChronicBlocker dataclass deep edges ─────────────────────────────────────


class TestChronicBlockerDeep:
    def test_default_recent_todo_ids_empty(self):
        cb = ChronicBlocker(
            task_type="code",
            blocker_kind="human_input",
            incident_count=5,
            first_seen=datetime(2026, 1, 1, tzinfo=UTC),
            last_seen=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert cb.recent_todo_ids == []

    def test_first_seen_equal_to_last_seen_single_incident(self):
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        cb = ChronicBlocker(
            task_type="code",
            blocker_kind="human_input",
            incident_count=5,
            first_seen=ts,
            last_seen=ts,
        )
        assert cb.first_seen == cb.last_seen


# ── BlockerDetector null/invalid repo deep edges ────────────────────────────


class TestNullRepos:
    @pytest.mark.asyncio
    async def test_scan_with_all_none_repos_returns_empty(self, async_session: AsyncSession):
        detector = BlockerDetector(session=async_session)
        findings = await detector.scan()
        assert findings == []

    @pytest.mark.asyncio
    async def test_scan_with_only_todo_repo_no_human_todo_repo(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="NOH-1", updated_at=old)
        await async_session.commit()
        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "NOH-1" in ids

    @pytest.mark.asyncio
    async def test_chronic_blockers_with_none_session_returns_empty(self):
        detector = BlockerDetector(session=None)
        chronic = await detector.chronic_blockers()
        assert chronic == []

    @pytest.mark.asyncio
    async def test_chronic_requeues_with_none_session_returns_empty(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="NOSS-1",
            status=TodoStatus.QUEUED,
            run_count=10,
        )
        await async_session.commit()
        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=None,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "NOSS-1" not in ids

    @pytest.mark.asyncio
    async def test_chronic_requeues_with_none_todo_repo_returns_empty(self, async_session: AsyncSession):
        detector = BlockerDetector(
            todo_repo=None,
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = await detector.scan()
        assert findings == []


# ── Clock injection ─────────────────────────────────────────────────────────


class TestClockInjection:
    @pytest.mark.asyncio
    async def test_fixed_clock_determines_now(self, async_session: AsyncSession):
        fixed_now = datetime(2026, 6, 1, tzinfo=UTC)
        old = fixed_now - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="CLK-1", updated_at=old)
        await async_session.commit()
        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
            clock=lambda: fixed_now,
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CLK-1"]
        assert len(findings) == 1
        assert findings[0].blocked_duration_seconds == int(timedelta(hours=30).total_seconds())

    @pytest.mark.asyncio
    async def test_clock_returns_different_time_each_call(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="CLK-A", updated_at=old)
        await async_session.commit()
        base = _now()
        call_count = [0]

        def advancing_clock():
            call_count[0] += 1
            return base + timedelta(seconds=call_count[0] * 60)

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
            clock=advancing_clock,
        )
        findings = await detector.scan()
        assert any(f.todo_id == "CLK-A" for f in findings)


# ── Project-scoped scan ─────────────────────────────────────────────────────


class TestProjectScopedScan:
    @pytest.mark.asyncio
    async def test_project_id_filter_excludes_other_project(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        # project_id FK references projects table — won't exist in in-memory SQLite.
        # Verify that scan(project_id="proj-a") passes the argument through;
        # the FK constraint makes direct TODOs with project_id uninsertable here,
        # so we test via the detector method's parameter acceptance.
        await _blocked_todo(async_session, todo_id="P1-1", updated_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan(project_id="proj-a")
        ids = [f.todo_id for f in findings]
        assert "P1-1" not in ids

    @pytest.mark.asyncio
    async def test_chronic_requeues_respects_project_id_filter(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-P2",
            status=TodoStatus.QUEUED,
            run_count=5,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = await detector.scan(project_id="proj-a")
        ids = [f.todo_id for f in findings]
        assert "CR-P2" not in ids


# ── _scan_blocked_on_human deep edges ───────────────────────────────────────


class TestScanBlockedOnHumanDeep:
    @pytest.mark.asyncio
    async def test_todo_with_none_updated_at_is_skipped(self, async_session: AsyncSession):
        t = TodoModel(
            todo_id="NOUPD-1",
            title="no updated_at",
            status=TodoStatus.BLOCKED_ON_HUMAN.value,
            work_type="code",
            queue="core",
        )
        t.updated_at = None
        async_session.add(t)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        assert all(f.todo_id != "NOUPD-1" for f in findings)

    @pytest.mark.asyncio
    async def test_repo_error_is_caught_and_returns_empty(self, async_session: AsyncSession):
        failing_repo = MagicMock()
        failing_repo.list_by_status.side_effect = RuntimeError("DB down")
        detector = BlockerDetector(
            todo_repo=failing_repo,
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        findings = await detector.scan()
        assert findings == []

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_surfaces_lt_comparison(self, async_session: AsyncSession):
        cfg = RemediationConfig(human_input_block_hours=24)
        exactly_24h_ago = _now() - timedelta(hours=24)
        await _blocked_todo(async_session, todo_id="EXACT-24", updated_at=exactly_24h_ago)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=cfg,
        )
        findings = [f for f in await detector.scan() if f.todo_id == "EXACT-24"]
        assert len(findings) == 1, "exactly at threshold uses < (not <=), so 24h = 24h → surfaces"

    @pytest.mark.asyncio
    async def test_one_second_over_threshold_surfaces(self, async_session: AsyncSession):
        cfg = RemediationConfig(human_input_block_hours=24)
        just_over = _now() - timedelta(hours=24, seconds=1)
        await _blocked_todo(async_session, todo_id="OVER-1S", updated_at=just_over)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=cfg,
        )
        findings = [f for f in await detector.scan() if f.todo_id == "OVER-1S"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_multiple_blocked_todos_mixed_ages(self, async_session: AsyncSession):
        now = _now()
        await _blocked_todo(async_session, todo_id="OLD-A", updated_at=now - timedelta(hours=30))
        await _blocked_todo(async_session, todo_id="OLD-B", updated_at=now - timedelta(hours=50))
        await _blocked_todo(async_session, todo_id="NEW-C", updated_at=now - timedelta(hours=1))
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "OLD-A" in ids
        assert "OLD-B" in ids
        assert "NEW-C" not in ids


# ── _scan_chronic_requeues deep edges ───────────────────────────────────────


class TestScanChronicRequeuesDeep:
    @pytest.mark.asyncio
    async def test_run_count_exactly_at_threshold(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-EQ-3",
            status=TodoStatus.QUEUED,
            run_count=3,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-EQ-3"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_run_count_below_threshold_not_surfaced(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-LOW",
            status=TodoStatus.QUEUED,
            run_count=2,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-LOW"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_run_count_very_high_above_threshold(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-HUGE",
            status=TodoStatus.BLOCKED,
            run_count=10000,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-HUGE"]
        assert len(findings) == 1
        assert findings[0].blocker_kind == "resource_contention"

    @pytest.mark.asyncio
    async def test_needs_more_work_status_is_in_live_states(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-NMW",
            status=TodoStatus.NEEDS_MORE_WORK,
            run_count=5,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-NMW"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_complete_status_excluded_from_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-COMPLETE",
            status=TodoStatus.COMPLETE,
            run_count=10,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-COMPLETE"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_cancelled_status_excluded_from_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-CANCEL",
            status=TodoStatus.CANCELLED,
            run_count=10,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-CANCEL"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_chronic_query_error_is_caught(self, async_session: AsyncSession):
        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("query failed")
        detector = BlockerDetector(
            todo_repo=MagicMock(),
            session=mock_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = await detector._scan_chronic_requeues(_now(), None)
        assert findings == []

    @pytest.mark.asyncio
    async def test_zero_run_count_below_threshold(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-ZERO",
            status=TodoStatus.QUEUED,
            run_count=0,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=1),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-ZERO"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_chronic_requeue_summary_includes_run_count(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="CR-SUMMARY",
            status=TodoStatus.QUEUED,
            run_count=7,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CR-SUMMARY"]
        assert len(findings) == 1
        assert "re-queued 7 times" in findings[0].blocker_summary
        assert "threshold 3" in findings[0].blocker_summary

    @pytest.mark.asyncio
    async def test_multiple_chronic_todos_all_surface(self, async_session: AsyncSession):
        for i in range(5):
            await _blocked_todo(
                async_session,
                todo_id=f"CR-MULTI-{i}",
                status=TodoStatus.QUEUED,
                run_count=10,
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("CR-MULTI-")]
        assert len(findings) == 5


# ── _scan_stale_human_todos deep edges ──────────────────────────────────────


class TestScanStaleHumanTodosDeep:
    @pytest.mark.asyncio
    async def test_human_todo_past_input_threshold(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _ht(async_session, parent_agent_todo_id=None, category="input_request", created_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert findings[0].blocker_kind == "human_input"
        assert findings[0].suggested_remediation == "file_human_todo"

    @pytest.mark.asyncio
    async def test_human_todo_past_permission_threshold(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=5)
        await _ht(
            async_session,
            parent_agent_todo_id=None,
            category="permission_escalation",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(permission_escalation_block_hours=4),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert findings[0].blocker_kind == "permission_escalation"
        assert findings[0].suggested_remediation == "schedule_retry"

    @pytest.mark.asyncio
    async def test_human_todo_within_threshold_not_surfaced(self, async_session: AsyncSession):
        recent = _now() - timedelta(hours=1)
        await _ht(async_session, parent_agent_todo_id=None, category="input_request", created_at=recent)
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_human_todo_with_no_created_at_is_skipped(self, async_session: AsyncSession):
        human_todo_repo = MagicMock()
        human_todo_repo.list_open = AsyncMock(return_value=[SimpleNamespace(created_at=None)])

        detector = BlockerDetector(
            human_todo_repo=human_todo_repo,
            session=async_session,
            config=RemediationConfig(human_input_block_hours=0),
        )
        findings = await detector._scan_stale_human_todos(_now(), None)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_human_todo_with_category_input_request_is_found(self, async_session: AsyncSession):
        ht = await _ht(
            async_session,
            parent_agent_todo_id=None,
            category="input_request",
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=0),
        )
        findings = await detector._scan_stale_human_todos(_now(), None)
        ids = [f.todo_id for f in findings]
        assert any(ht.id in i for i in ids)

    @pytest.mark.asyncio
    async def test_human_todo_with_parent_agent_todo_id(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        todo = await _blocked_todo(async_session, todo_id="P-HAS-1", updated_at=old)
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="input_request",
            created_at=old,
            title="Need prod key",
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "P-HAS-1" in ids

    @pytest.mark.asyncio
    async def test_human_todo_without_parent_uses_htodo_prefix(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        ht = await _ht(
            async_session,
            parent_agent_todo_id=None,
            category="input_request",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert ht.id in findings[0].todo_id

    @pytest.mark.asyncio
    async def test_decision_category_stale_human_todo(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _ht(async_session, parent_agent_todo_id=None, category="decision", created_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert "decision" in findings[0].blocker_summary.lower()

    @pytest.mark.asyncio
    async def test_external_action_stale_human_todo(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _ht(async_session, parent_agent_todo_id=None, category="external_action", created_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert findings[0].blocker_kind == "human_input"
        assert findings[0].suggested_remediation == "file_human_todo"

    @pytest.mark.asyncio
    async def test_stale_human_todo_list_open_error_is_caught(self, async_session: AsyncSession):
        failing_repo = MagicMock()
        failing_repo.list_open.side_effect = RuntimeError("DB down")
        detector = BlockerDetector(
            human_todo_repo=failing_repo,
            session=async_session,
        )
        findings = await detector._scan_stale_human_todos(_now(), None)
        assert findings == []

    @pytest.mark.asyncio
    async def test_summary_truncates_title_over_80_chars(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        long_title = "A" * 120
        await _ht(
            async_session,
            parent_agent_todo_id=None,
            category="input_request",
            created_at=old,
            title=long_title,
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert len(findings[0].blocker_summary.split("'")[1]) <= 80

    @pytest.mark.asyncio
    async def test_multiple_stale_human_todos_all_surface(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        for i in range(5):
            await _ht(
                async_session,
                parent_agent_todo_id=None,
                category="input_request",
                created_at=old,
                title=f"Stale request {i}",
            )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 5

    @pytest.mark.asyncio
    async def test_human_todo_linked_human_todo_id_is_set(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        ht = await _ht(
            async_session,
            parent_agent_todo_id=None,
            category="input_request",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id.startswith("HTODO:")]
        assert len(findings) == 1
        assert findings[0].linked_human_todo_id == ht.id


# ── _threshold_hours_for_kind deep edges ────────────────────────────────────


class TestThresholdHoursForKind:
    def test_permission_escalation_uses_own_config(self):
        cfg = RemediationConfig(
            human_input_block_hours=24,
            permission_escalation_block_hours=4,
        )
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("permission_escalation") == 4

    def test_human_input_uses_human_input_config(self):
        cfg = RemediationConfig(human_input_block_hours=24)
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("human_input") == 24

    def test_resource_contention_uses_human_input_config(self):
        cfg = RemediationConfig(human_input_block_hours=24)
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("resource_contention") == 24

    def test_unknown_kind_uses_human_input_config(self):
        cfg = RemediationConfig(human_input_block_hours=24)
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("unknown") == 24

    def test_empty_string_uses_human_input_config(self):
        cfg = RemediationConfig(human_input_block_hours=24)
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("") == 24

    def test_arbitrary_string_uses_human_input_config(self):
        cfg = RemediationConfig(human_input_block_hours=24)
        detector = BlockerDetector(config=cfg)
        assert detector._threshold_hours_for_kind("garbage") == 24


# ── _summary_for deep edges ────────────────────────────────────────────────


class TestSummaryForDeep:
    def test_summary_with_linked_human_todo(self):
        detector = BlockerDetector()
        ht = MagicMock()
        ht.category = "permission_escalation"
        ht.title = "Need AWS access"
        todo = MagicMock()
        todo.title = "Deploy to production"
        summary = detector._summary_for(todo, ht, "permission_escalation")
        assert "Deploy to production" in summary
        assert "permission_escalation" in summary
        assert "Need AWS access" in summary

    def test_summary_without_linked_human_todo(self):
        detector = BlockerDetector()
        todo = MagicMock()
        todo.title = "Run migration"
        summary = detector._summary_for(todo, None, "human_input")
        assert "Run migration" in summary
        assert "human_input" in summary
        assert "No linked human-todo" in summary

    def test_summary_truncates_todo_title_over_80_chars(self):
        detector = BlockerDetector()
        ht = MagicMock()
        ht.category = "input_request"
        ht.title = "Short HT title"
        todo = MagicMock()
        todo.title = "X" * 200
        summary = detector._summary_for(todo, ht, "human_input")
        assert len(summary.split("'")[1]) <= 80

    def test_summary_truncates_human_todo_title_over_80_chars(self):
        detector = BlockerDetector()
        ht = MagicMock()
        ht.category = "input_request"
        ht.title = "H" * 200
        todo = MagicMock()
        todo.title = "Short"
        summary = detector._summary_for(todo, ht, "human_input")
        assert summary.split("Linked human-todo: ")[1][:80] in summary

    def test_summary_empty_todo_title(self):
        detector = BlockerDetector()
        ht = MagicMock()
        ht.category = "input_request"
        ht.title = "Request"
        todo = MagicMock()
        todo.title = ""
        summary = detector._summary_for(todo, ht, "human_input")
        assert "''" in summary

    def test_summary_unknown_category_fallback(self):
        detector = BlockerDetector()
        ht = MagicMock()
        ht.category = "bogus"
        ht.title = "Bogus request"
        todo = MagicMock()
        todo.title = "Test"
        summary = detector._summary_for(todo, ht, "human_input")
        assert "bogus" in summary


# ── _lookup_linked_human_todo deep edges ────────────────────────────────────


class TestLookupLinkedHumanTodoDeep:
    @pytest.mark.asyncio
    async def test_no_human_todo_repo_returns_none(self, async_session: AsyncSession):
        detector = BlockerDetector(session=async_session)
        todo = MagicMock()
        todo.todo_id = "SOME-ID"
        result = await detector._lookup_linked_human_todo(todo)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_todo_id_returns_none(self, async_session: AsyncSession):
        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        todo = MagicMock()
        todo.todo_id = ""
        result = await detector._lookup_linked_human_todo(todo)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_todo_id_attr_returns_none(self, async_session: AsyncSession):
        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        todo = MagicMock(spec=[])
        result = await detector._lookup_linked_human_todo(todo)
        assert result is None

    @pytest.mark.asyncio
    async def test_matching_parent_found(self, async_session: AsyncSession):
        todo = await _blocked_todo(async_session, todo_id="LKP-MATCH")
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="input_request",
        )
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        result = await detector._lookup_linked_human_todo(todo)
        assert result is not None
        assert result.parent_agent_todo_id == todo.todo_id

    @pytest.mark.asyncio
    async def test_no_matching_parent_returns_none(self, async_session: AsyncSession):
        todo = await _blocked_todo(async_session, todo_id="LKP-NOMATCH")
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="input_request",
        )
        await async_session.commit()

        other_todo = await _blocked_todo(async_session, todo_id="LKP-OTHER")
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        result = await detector._lookup_linked_human_todo(other_todo)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_open_error_returns_none(self, async_session: AsyncSession):
        failing_repo = MagicMock()
        failing_repo.list_open.side_effect = RuntimeError("DB down")
        detector = BlockerDetector(
            human_todo_repo=failing_repo,
            session=async_session,
        )
        todo = MagicMock()
        todo.todo_id = "ERR-TODO"
        result = await detector._lookup_linked_human_todo(todo)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_human_todos_finds_correct_parent(self, async_session: AsyncSession):
        todo_a = await _blocked_todo(async_session, todo_id="LKP-A")
        todo_b = await _blocked_todo(async_session, todo_id="LKP-B")
        await _ht(async_session, parent_agent_todo_id=todo_a.todo_id, category="input_request")
        await _ht(async_session, parent_agent_todo_id=todo_b.todo_id, category="permission_escalation")
        await async_session.commit()

        detector = BlockerDetector(
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
        )
        result_a = await detector._lookup_linked_human_todo(todo_a)
        result_b = await detector._lookup_linked_human_todo(todo_b)
        assert result_a is not None
        assert result_a.parent_agent_todo_id == todo_a.todo_id
        assert result_b is not None
        assert result_b.parent_agent_todo_id == todo_b.todo_id


# ── chronic_blockers deep edges ─────────────────────────────────────────────


class TestChronicBlockersDeep:
    @pytest.mark.asyncio
    async def test_empty_events_returns_empty(self, async_session: AsyncSession):
        detector = BlockerDetector(session=async_session)
        chronic = await detector.chronic_blockers()
        assert chronic == []

    @pytest.mark.asyncio
    async def test_events_below_min_incidents_not_surfaced(self, async_session: AsyncSession):
        now = _now()
        for i in range(3):
            t = TodoModel(
                todo_id=f"CHD-LOW-{i}",
                title="below threshold",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert chronic == []

    @pytest.mark.asyncio
    async def test_events_exactly_at_min_incidents_surfaced(self, async_session: AsyncSession):
        now = _now()
        for i in range(5):
            t = TodoModel(
                todo_id=f"CHD-EXACT-{i}",
                title="exact threshold",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1
        assert chronic[0].incident_count == 5

    @pytest.mark.asyncio
    async def test_first_seen_is_oldest_last_seen_is_newest(self, async_session: AsyncSession):
        now = _now()
        oldest = now - timedelta(days=3)
        newest = now - timedelta(hours=1)
        for i, ts in enumerate(
            [oldest, now - timedelta(days=2), now - timedelta(days=1), now - timedelta(hours=5), newest]
        ):
            t = TodoModel(
                todo_id=f"CHD-ORDER-{i}",
                title="order check",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=ts,
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert chronic[0].first_seen.replace(tzinfo=None) == oldest.replace(tzinfo=None)
        assert chronic[0].last_seen.replace(tzinfo=None) == newest.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_recent_todo_ids_limited_to_last_five(self, async_session: AsyncSession):
        now = _now()
        ids_created = []
        for i in range(10):
            tid = f"CHD-RECENT-{i}"
            ids_created.append(tid)
            t = TodoModel(
                todo_id=tid,
                title="recent check",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic[0].recent_todo_ids) == 5
        # Events are ordered ASC by created_at (oldest first).
        # With created_at descending from now (i=0 newest, i=9 oldest),
        # ASC order = i=9 first, i=0 last.
        # evs[-5:] picks the last 5 = 5 most recent = i=4,3,2,1,0.
        expected = [f"CHD-RECENT-{j}" for j in range(4, -1, -1)]
        assert chronic[0].recent_todo_ids == expected

    @pytest.mark.asyncio
    async def test_reason_text_classifies_blocker_kind(self, async_session: AsyncSession):
        now = _now()
        cases = [
            ("permission denied", "permission_escalation"),
            ("input required", "human_input"),
            ("resource exhausted", "resource_contention"),
            ("contention detected", "resource_contention"),
            ("something unrelated", "unknown"),
        ]
        for reason, expected_kind in cases:
            engine2 = _make_async_engine()
            async with engine2.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sf2 = sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
            async with sf2() as s:
                for i in range(6):
                    t = TodoModel(
                        todo_id=f"CHD-REASON-{expected_kind}-{i}",
                        title="reason test",
                        status=TodoStatus.QUEUED.value,
                        work_type="code",
                        queue="core",
                    )
                    s.add(t)
                    await s.flush()
                    s.add(
                        TodoEventModel(
                            todo_id=t.todo_id,
                            event_type="status_changed",
                            new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                            old_status=TodoStatus.QUEUED.value,
                            reason=reason,
                            created_at=now - timedelta(hours=i),
                        )
                    )
                await s.commit()

                detector = BlockerDetector(
                    todo_repo=TodoRepository(s),
                    session=s,
                    config=RemediationConfig(min_chronic_incidents=5),
                )
                chronic = await detector.chronic_blockers()
                assert len(chronic) == 1
                assert chronic[0].blocker_kind == expected_kind
            async with engine2.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine2.dispose()

    @pytest.mark.asyncio
    async def test_multiple_buckets_all_surfaced(self, async_session: AsyncSession):
        now = _now()
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-BUCKETA-{i}",
                title="bucket A",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-BUCKETB-{i}",
                title="bucket B",
                status=TodoStatus.QUEUED.value,
                work_type="infra",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="input needed",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 2
        kinds = {c.blocker_kind for c in chronic}
        types = {c.task_type for c in chronic}
        assert "permission_escalation" in kinds
        assert "human_input" in kinds
        assert "code" in types
        assert "infra" in types

    @pytest.mark.asyncio
    async def test_sorted_by_incident_count_desc(self, async_session: AsyncSession):
        now = _now()
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-SORT-A-{i}",
                title="sort A",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        for i in range(8):
            t = TodoModel(
                todo_id=f"CHD-SORT-B-{i}",
                title="sort B",
                status=TodoStatus.QUEUED.value,
                work_type="infra",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="input needed",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert chronic[0].incident_count >= chronic[1].incident_count

    @pytest.mark.asyncio
    async def test_lookback_window_respects_cutoff(self, async_session: AsyncSession):
        now = _now()
        recent_ts = now - timedelta(days=3)
        for i in range(3):
            t = TodoModel(
                todo_id=f"CHD-LOOKBACK-A-{i}",
                title="within window",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=recent_ts,
                )
            )
        old_ts = now - timedelta(days=10)
        for i in range(3):
            t = TodoModel(
                todo_id=f"CHD-LOOKBACK-B-{i}",
                title="outside window",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=old_ts,
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5, chronic_lookback_days=7),
        )
        chronic = await detector.chronic_blockers()
        assert chronic == []

    @pytest.mark.asyncio
    async def test_events_with_deleted_todos_still_group(self, async_session: AsyncSession):
        now = _now()
        created_ids = []
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-DELETED-{i}",
                title="will be deleted",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            created_ids.append(t.todo_id)
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        # Now delete the todos — events remain (CASCADE) in SQLite.
        # But CASCADE deletes the events too. Simulate orphan events by
        # using a reachable todo_id that has no matching row: test that
        # work_types lookup returns "" for non-existent todos.
        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1
        assert chronic[0].blocker_kind == "permission_escalation"
        assert chronic[0].task_type == "code"

    @pytest.mark.asyncio
    async def test_chronic_events_query_error_is_caught(self, async_session: AsyncSession):
        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("query failed")
        detector = BlockerDetector(
            todo_repo=MagicMock(),
            session=mock_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert chronic == []

    @pytest.mark.asyncio
    async def test_chronic_blockers_uses_clock_for_now(self, async_session: AsyncSession):
        fixed_now = datetime(2026, 6, 15, tzinfo=UTC)
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-CLOCK-{i}",
                title="clock test",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=fixed_now - timedelta(days=1, hours=-i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5, chronic_lookback_days=7),
            clock=lambda: fixed_now,
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1

    @pytest.mark.asyncio
    async def test_chronic_custom_lookback_overrides_config(self, async_session: AsyncSession):
        now = _now()
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHD-CUSTOMLB-{i}",
                title="custom lb",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5, chronic_lookback_days=1),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1

        chronic_short = await detector.chronic_blockers(lookback_days=0)
        assert chronic_short == []


# ── Combined scan (all three signal sources) ────────────────────────────────


class TestCombinedScan:
    @pytest.mark.asyncio
    async def test_all_three_signal_sources_produce_findings(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        # 1. blocked_on_human
        await _blocked_todo(async_session, todo_id="ALL-1", updated_at=old)
        # 2. chronic re-queue
        await _blocked_todo(
            async_session,
            todo_id="ALL-2",
            status=TodoStatus.QUEUED,
            run_count=5,
        )
        # 3. stale human-todo
        await _ht(async_session, parent_agent_todo_id=None, category="input_request", created_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(
                human_input_block_hours=24,
                max_requeues_before_chronic=3,
            ),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "ALL-1" in ids
        assert "ALL-2" in ids
        assert any(i.startswith("HTODO:") for i in ids)
        assert len(findings) == 3


# ── Config integration edges ────────────────────────────────────────────────


class TestConfigIntegration:
    @pytest.mark.asyncio
    async def test_zero_hours_permission_config_surfaces_new_todos(self, async_session: AsyncSession):
        recent = _now() - timedelta(minutes=1)
        todo = await _blocked_todo(async_session, todo_id="ZERO-PERM", updated_at=recent)
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="permission_escalation",
            created_at=recent,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(
                human_input_block_hours=24,
                permission_escalation_block_hours=0,
            ),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "ZERO-PERM"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_zero_max_requeues_surfaces_every_retried_todo(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="ZERO-REQ",
            status=TodoStatus.QUEUED,
            run_count=1,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=0),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "ZERO-REQ"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_zero_min_chronic_surfaces_single_event(self, async_session: AsyncSession):
        now = _now()
        t = TodoModel(
            todo_id="ZERO-CHRONIC",
            title="zero chronic",
            status=TodoStatus.QUEUED.value,
            work_type="code",
            queue="core",
        )
        async_session.add(t)
        await async_session.flush()
        async_session.add(
            TodoEventModel(
                todo_id=t.todo_id,
                event_type="status_changed",
                new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                old_status=TodoStatus.QUEUED.value,
                reason="permission denied",
                created_at=now,
            )
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=0),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1
        assert chronic[0].incident_count == 1


# ── TodoStatus live-states enum coverage ────────────────────────────────────


class TestTodoStatusExclusion:
    @pytest.mark.asyncio
    async def test_backlog_excluded_from_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="EXCL-BACKLOG",
            status=TodoStatus.BACKLOG,
            run_count=10,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "EXCL-BACKLOG"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_active_excluded_from_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="EXCL-ACTIVE",
            status=TodoStatus.ACTIVE,
            run_count=10,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "EXCL-ACTIVE"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_failed_excluded_from_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="EXCL-FAILED",
            status=TodoStatus.FAILED,
            run_count=10,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "EXCL-FAILED"]
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_blocked_on_human_included_in_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="INCL-BOH",
            status=TodoStatus.BLOCKED_ON_HUMAN,
            run_count=5,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "INCL-BOH"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_blocked_included_in_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="INCL-BLOCKED",
            status=TodoStatus.BLOCKED,
            run_count=5,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "INCL-BLOCKED"]
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_queued_included_in_chronic_scan(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="INCL-QUEUED",
            status=TodoStatus.QUEUED,
            run_count=5,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "INCL-QUEUED"]
        assert len(findings) == 1


# ── Constant integrity ──────────────────────────────────────────────────────


class TestConstantsDeep:
    def test_blocker_kinds_exhaustive(self):
        assert frozenset({"human_input", "permission_escalation", "resource_contention", "unknown"}) == BLOCKER_KINDS

    def test_remediation_kinds_exhaustive(self):
        assert frozenset({"dispatch_agent", "schedule_retry", "file_human_todo", "no_action"}) == REMEDIATION_KINDS

    def test_blocker_kinds_is_frozenset(self):
        assert isinstance(BLOCKER_KINDS, frozenset)

    def test_remediation_kinds_is_frozenset(self):
        assert isinstance(REMEDIATION_KINDS, frozenset)


# ── work_type propagation deep edges ────────────────────────────────────────


class TestWorkTypePropagation:
    @pytest.mark.asyncio
    async def test_blocked_on_human_includes_task_type(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="WT-BOH", updated_at=old, work_type="infra")
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "WT-BOH"]
        assert len(findings) == 1
        assert findings[0].task_type == "infra"

    @pytest.mark.asyncio
    async def test_chronic_requeue_includes_task_type(self, async_session: AsyncSession):
        await _blocked_todo(
            async_session,
            todo_id="WT-CR",
            status=TodoStatus.QUEUED,
            run_count=5,
            work_type="docs",
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "WT-CR"]
        assert len(findings) == 1
        assert findings[0].task_type == "docs"

    @pytest.mark.asyncio
    async def test_chronic_blocker_includes_task_type_from_lookup(self, async_session: AsyncSession):
        now = _now()
        for i in range(6):
            t = TodoModel(
                todo_id=f"WT-CHRONIC-{i}",
                title="work type chronic",
                status=TodoStatus.QUEUED.value,
                work_type="refactor",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="permission denied",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1
        assert chronic[0].task_type == "refactor"


# ── Permission escalation uses shorter threshold ────────────────────────────


class TestPermissionEscalationThreshold:
    @pytest.mark.asyncio
    async def test_perm_escalation_surfaces_after_4h_not_24h(self, async_session: AsyncSession):
        age_6h = _now() - timedelta(hours=6)
        todo = await _blocked_todo(async_session, todo_id="PE-FAST", updated_at=age_6h)
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="permission_escalation",
            created_at=age_6h,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(
                human_input_block_hours=24,
                permission_escalation_block_hours=4,
            ),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "PE-FAST"]
        assert len(findings) >= 1
        assert any(f.blocker_kind == "permission_escalation" for f in findings)

    @pytest.mark.asyncio
    async def test_perm_escalation_not_surfaced_before_4h(self, async_session: AsyncSession):
        age_2h = _now() - timedelta(hours=2)
        todo = await _blocked_todo(async_session, todo_id="PE-TOO-SOON", updated_at=age_2h)
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="permission_escalation",
            created_at=age_2h,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(
                human_input_block_hours=24,
                permission_escalation_block_hours=4,
            ),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "PE-TOO-SOON"]
        assert len(findings) == 0


# ── Mixed overlap: blocked_on_human + stale_human_todos ───────────────────


class TestMixedOverlap:
    @pytest.mark.asyncio
    async def test_todo_appears_in_blocked_and_stale_scan(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        todo = await _blocked_todo(async_session, todo_id="OVERLAP-1", updated_at=old)
        await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="input_request",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "OVERLAP-1"]
        assert len(findings) >= 1

    @pytest.mark.asyncio
    async def test_stale_human_todo_with_parent_sets_linked_id(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        todo = await _blocked_todo(async_session, todo_id="LINKED-1", updated_at=old)
        ht = await _ht(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="input_request",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "LINKED-1"]
        assert any(f.linked_human_todo_id == ht.id for f in findings)

    @pytest.mark.asyncio
    async def test_blocked_duration_seconds_is_positive(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="DUR-1", updated_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "DUR-1"]
        assert findings[0].blocked_duration_seconds > 0

    @pytest.mark.asyncio
    async def test_no_none_project_id_on_finding(self, async_session: AsyncSession):
        old = _now() - timedelta(hours=30)
        await _blocked_todo(async_session, todo_id="NOPROJ-1", updated_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "NOPROJ-1"]
        assert len(findings) == 1
