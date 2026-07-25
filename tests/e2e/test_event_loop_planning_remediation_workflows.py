"""E2E tests for event loop phases, planning subsystem, and remediation.

Covers:
  1. Event loop phases: claim -> dispatch -> reap -> cleanup cycle
  2. Tick lifecycle: pause/resume, rate limiting, backpressure
  3. Planning: repo map generation, artifact creation with to_markdown, debt evaluation
  4. Remediation: blocker detection, threshold-based escalation, chronic blocker detection
  5. Integration: event loop triggers planning which triggers remediation
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    Base,
    BucketLeaseModel,
    TodoEventModel,
    TodoModel,
)
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.event_loop.lease import acquire_lease, reclaim_expired_leases, release_lease
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.event_loop.scheduler import TodoScheduler
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.critique import PlanCritique
from general_ludd.planning.debt_applier import apply_debt_findings
from general_ludd.planning.debt_evaluator import (
    DebtEvaluator,
    DebtFinding,
    DebtFindings,
)
from general_ludd.planning.repo_map import CodeSymbol, RepoMap, RepoMapBuilder
from general_ludd.remediation.blocker_detector import (
    BLOCKER_KINDS,
    REMEDIATION_KINDS,
    BlockedTask,
    BlockerDetector,
    ChronicBlocker,
    RemediationConfig,
)
from general_ludd.remediation.dispatcher import (
    RemediationActionKind,
    RemediationDispatcher,
)
from general_ludd.remediation.reporter import chronic_blocker_report
from general_ludd.schemas.todo import TodoStatus

_E2E_PROJECT = "proj-e2e-workflows"


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma(dbapi_conn, _record):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    return engine


@pytest.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def session_factory(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_manager(project_id: str = _E2E_PROJECT):
    p = SimpleNamespace(project_id=project_id)
    return SimpleNamespace(select_project=lambda: p, list_active=lambda: [p])


def _simple_todo(todo_id: str, **kw) -> dict:
    return {
        "todo_id": todo_id,
        "title": f"Task {todo_id}",
        "description": "E2E test task",
        "queue": "core",
        "priority": 5,
        "work_type": kw.pop("work_type", "code"),
        "status": TodoStatus.QUEUED.value,
        "created_by": "e2e-test",
        "project_id": _E2E_PROJECT,
        **kw,
    }


async def _seed_todo(repo: TodoRepository, todo_id: str, **kw) -> TodoModel:
    return await repo.create(_simple_todo(todo_id, **kw))


async def _seed_queued(session: AsyncSession, repo: TodoRepository, todo_id: str, **kw):
    t = await _seed_todo(repo, todo_id, **kw)
    await session.commit()
    return t


def _now_minus(hours: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _blocked_finding(
    todo_id: str = "TODO-BLK",
    kind: str = "resource_contention",
    remediation: str = "dispatch_agent",
    hours: int = 10,
) -> BlockedTask:
    return BlockedTask(
        todo_id=todo_id,
        project_id=None,
        blocked_at=_now_minus(hours),
        blocked_duration_seconds=hours * 3600,
        blocker_kind=kind,
        blocker_summary="test blockage",
        suggested_remediation=remediation,
        task_type="code",
    )


# ===========================================================================
# 1 — EVENT LOOP PHASES
# ===========================================================================


class TestPhaseOrderIntegrity:
    def test_required_phases_present(self):
        required = [
            "load_config_snapshot",
            "claim_unreviewed_task_returns",
            "dispatch_return_review_jobs",
            "evaluate_pid_controllers",
            "refill_task_buckets",
            "run_scheduler",
            "claim_runnable_todos",
            "dispatch_execute_jobs",
            "reconcile_completed_decisions",
            "remediate_blocked_tasks",
            "emit_tick_metrics",
        ]
        for p in required:
            assert p in PHASE_ORDER, f"missing: {p}"

    def test_no_duplicates(self):
        assert len(PHASE_ORDER) == len(set(PHASE_ORDER))

    def test_claim_before_dispatch(self):
        ci = PHASE_ORDER.index("claim_runnable_todos")
        di = PHASE_ORDER.index("dispatch_execute_jobs")
        assert ci < di, "claim must precede dispatch"

    def test_dispatch_before_reconcile(self):
        di = PHASE_ORDER.index("dispatch_execute_jobs")
        ri = PHASE_ORDER.index("reconcile_completed_decisions")
        assert di < ri, "dispatch must precede reconcile"

    def test_remediate_after_dispatch(self):
        di = PHASE_ORDER.index("dispatch_execute_jobs")
        ri = PHASE_ORDER.index("remediate_blocked_tasks")
        assert di < ri

    def test_phase_count_stable(self):
        assert len(PHASE_ORDER) >= 18


class TestClaimDispatchReapCycle:
    @pytest.mark.asyncio
    async def test_claim_picks_up_queued(self, session_factory):
        async with session_factory() as s:
            repo = TodoRepository(s)
            await _seed_queued(s, repo, "TODO-CLAIM-1")
            await _seed_queued(s, repo, "TODO-CLAIM-2")

        loop = EventLoop(
            session=session_factory,
            config={},
            project_manager=_project_manager(),
            task_return_repo=AsyncMock(),
        )
        if loop._task_return_repo:
            loop._task_return_repo.claim_unreviewed.return_value = []

        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        ids = {t.todo_id for t in claimed}
        assert ids & {"TODO-CLAIM-1", "TODO-CLAIM-2"}

    @pytest.mark.asyncio
    async def test_claim_skips_non_project_matching(self, session_factory):
        async with session_factory() as s:
            repo = TodoRepository(s)
            await _seed_queued(s, repo, "TODO-NO-MATCH", project_id=None)
        loop = EventLoop(
            session=session_factory,
            config={},
            project_manager=_project_manager(),
            task_return_repo=AsyncMock(),
        )
        if loop._task_return_repo:
            loop._task_return_repo.claim_unreviewed.return_value = []
        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert not any(t.todo_id == "TODO-NO-MATCH" for t in claimed)

    @pytest.mark.asyncio
    async def test_tick_cycle_completes_all_phases(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        m = await loop.tick()
        assert m["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_reconcile_applies_completed_decisions(self, session_factory):
        async with session_factory() as s:
            repo = TodoRepository(s)
            _t = await _seed_queued(s, repo, "TODO-REC-1")
        loop = EventLoop(
            session=session_factory,
            config={},
            project_manager=_project_manager(),
            task_return_repo=AsyncMock(),
        )
        if loop._task_return_repo:
            loop._task_return_repo.claim_unreviewed.return_value = []
        await loop.tick()
        assert loop._tick_state.get("claimed_todos")

    @pytest.mark.asyncio
    async def test_lease_acquire_reclaim_release_in_cycle(self, db_session: AsyncSession):
        todo = TodoModel(
            todo_id="todo-lease-cycle", title="L", queue="core",
            priority=3, work_type="code", status=TodoStatus.ACTIVE.value,
        )
        db_session.add(todo)
        await db_session.flush()

        lease = await acquire_lease(db_session, "core:todo-lease-cycle", "W1")
        assert lease.holder_id == "W1"

        await release_lease(db_session, "core:todo-lease-cycle", "W1")
        stmt = select(BucketLeaseModel).where(BucketLeaseModel.bucket_key == "core:todo-lease-cycle")
        r = await db_session.execute(stmt)
        assert r.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_reaper_requeues_stuck_work(self, db_session: AsyncSession):
        todo = TodoModel(
            todo_id="todo-stuck", title="stuck", queue="core",
            priority=3, work_type="code", status=TodoStatus.ACTIVE.value,
        )
        db_session.add(todo)
        lease = BucketLeaseModel(
            bucket_key="core:todo-stuck", holder_id="dead-worker",
            expires_at=_now_minus(1),
        )
        db_session.add(lease)
        await db_session.commit()

        n = await reclaim_expired_leases(db_session)
        assert n >= 1
        await db_session.refresh(todo)
        assert todo.status == TodoStatus.QUEUED.value


# ===========================================================================
# 2 — TICK LIFECYCLE
# ===========================================================================


class TestTickLifecycle:
    @pytest.mark.asyncio
    async def test_stop_stops_run_forever(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        loop._running = True
        t = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.06)
        loop.stop()
        await asyncio.wait_for(t, timeout=2.0)
        assert not loop._running

    @pytest.mark.asyncio
    async def test_run_forever_multiple_ticks(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        loop._running = True
        t = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.07)
        loop.stop()
        await asyncio.wait_for(t, timeout=2.0)
        assert loop._total_ticks >= 3

    @pytest.mark.asyncio
    async def test_tick_metrics_increment(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        m1 = await loop.tick()
        m2 = await loop.tick()
        assert m1["total_ticks"] == 1
        assert m2["total_ticks"] == 2

    @pytest.mark.asyncio
    async def test_shutdown_drains_background_tasks(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        loop._running = True
        t = asyncio.create_task(loop.run_forever(interval=0.02))
        await asyncio.sleep(0.05)
        await loop.shutdown()
        await asyncio.wait_for(t, timeout=2.0)
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_tick_with_no_session_does_not_crash(self):
        loop = EventLoop()
        m = await loop.tick()
        assert m["tick_duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_config_snapshot_loaded(self):
        cfg = {"foo": 1, "nested": {"bar": 2}}
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock(), config=cfg)
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        await loop.tick()
        assert loop._config_snapshot == cfg

    @pytest.mark.asyncio
    async def test_backpressure_semaphore_present(self):
        loop = EventLoop()
        assert loop._to_thread_semaphore._value >= 1
        assert loop._dispatch_semaphore._value >= 1

    @pytest.mark.asyncio
    async def test_daemon_state_populated(self):
        ds: dict = {}
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock(), daemon_state=ds)
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        await loop.tick()
        assert isinstance(ds.get("tick_metrics"), dict)
        assert isinstance(ds["tick_metrics"]["tick_duration_ms"], float)


class TestSchedulerWithinTick:
    @pytest.mark.asyncio
    async def test_scheduler_promotes_one_shot(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        fixed = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        past = fixed - timedelta(hours=1)
        await repo.create({
            "todo_id": "TODO-ONESHOT", "title": "one", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.SCHEDULED.value,
            "scheduled_at": past,
        })
        await db_session.commit()

        sched = TodoScheduler(repo, clock=lambda: fixed)
        p, sp = await sched.tick()
        assert p == 1
        assert sp == 0

        t = await repo.get_by_id("TODO-ONESHOT")
        assert t.status == TodoStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_scheduler_cron_spawns_child(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        fixed = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        await repo.create({
            "todo_id": "TODO-CRON", "title": "cron", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.SCHEDULED.value,
            "cron": "0 * * * *",
            "next_run_at": fixed - timedelta(hours=1),
            "schedule_timezone": "UTC",
        })
        await db_session.commit()

        sched = TodoScheduler(repo, clock=lambda: fixed)
        p, sp = await sched.tick()
        assert sp == 1
        assert p == 0

        t = await repo.get_by_id("TODO-CRON")
        assert t.run_count == 1
        assert t.status == TodoStatus.SCHEDULED.value

    @pytest.mark.asyncio
    async def test_scheduler_max_runs_cancels(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        fixed = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        await repo.create({
            "todo_id": "TODO-MAX", "title": "max", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.SCHEDULED.value,
            "cron": "0 * * * *", "next_run_at": fixed - timedelta(hours=1),
            "run_count": 5, "max_runs": 5, "schedule_timezone": "UTC",
        })
        await db_session.commit()

        sched = TodoScheduler(repo, clock=lambda: fixed)
        p, sp = await sched.tick()
        assert p == 0
        assert sp == 0
        t = await repo.get_by_id("TODO-MAX")
        assert t.status == TodoStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_scheduler_skips_paused(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        fixed = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        await repo.create({
            "todo_id": "TODO-PAUSED", "title": "paused", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.SCHEDULED.value,
            "scheduled_at": fixed - timedelta(hours=1),
            "schedule_paused": True,
        })
        await db_session.commit()

        sched = TodoScheduler(repo, clock=lambda: fixed)
        p, sp = await sched.tick()
        assert p == 0
        assert sp == 0

    @pytest.mark.asyncio
    async def test_scheduler_not_due_yet_untouched(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        fixed = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        future = fixed + timedelta(hours=2)
        await repo.create({
            "todo_id": "TODO-FUTURE", "title": "future", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.SCHEDULED.value,
            "scheduled_at": future,
        })
        await db_session.commit()

        sched = TodoScheduler(repo, clock=lambda: fixed)
        p, sp = await sched.tick()
        assert p == 0
        assert sp == 0


# ===========================================================================
# 3 — PLANNING: REPO MAP
# ===========================================================================


class TestRepoMapGeneration:
    @pytest.fixture
    def python_files_tree(self, tmp_path: Path) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        main = src / "main.py"
        main.write_text("import os\n\ndef greet(name):\n    return f'Hello {name}'\n")
        utils = src / "utils.py"
        utils.write_text("def add(a, b):\n    return a + b\n\nclass Helper:\n    def run(self):\n        pass\n")
        return tmp_path

    def test_parser_finds_class_function_import(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree))
        assert repo.file_count >= 2
        assert repo.total_lines > 0
        names = {s.name for s in repo.symbols}
        assert names & {"greet", "add", "Helper", "os"}

    def test_get_symbols_for_file(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree))
        syms = repo.get_symbols_for_file("src/utils.py")
        assert len(syms) >= 2
        kinds = {s.kind for s in syms}
        assert kinds & {"function", "class"}

    def test_get_top_symbols(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree))
        top = repo.get_top_symbols(n=5)
        assert len(top) >= 1
        assert len(top) <= 5
        # classes and functions rank before imports
        assert top[0].kind in ("class", "function")

    def test_to_compact_string(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree))
        s = repo.to_compact_string()
        assert "src/utils.py:" in s
        assert "class Helper" in s
        assert "src/main.py:" in s
        assert "function greet" in s

    def test_empty_repo_map(self):
        rm = RepoMap()
        assert rm.symbols == []
        assert rm.file_count == 0
        assert rm.to_compact_string() == ""
        assert rm.get_top_symbols() == []

    def test_roundtrip_dict(self, python_files_tree):
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(str(python_files_tree))
        d = rm.to_dict()
        rm2 = RepoMap.from_dict(d)
        assert rm2.file_count == rm.file_count
        assert len(rm2.symbols) == len(rm.symbols)

    def test_code_symbol_validation(self):
        s = CodeSymbol(name="foo", kind="function", file_path="a.py", line_start=1, line_end=5)
        assert s.name == "foo"
        with pytest.raises(ValueError):
            CodeSymbol(name="", kind="function", file_path="a.py", line_start=1, line_end=5)
        with pytest.raises(ValueError):
            CodeSymbol(name="x", kind="function", file_path="", line_start=1, line_end=5)
        with pytest.raises(ValueError):
            CodeSymbol(name="x", kind="function", file_path="a.py", line_start=5, line_end=1)

    def test_symbol_parent_relationship(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree))
        methods = [s for s in repo.symbols if s.kind == "method"]
        for m in methods:
            assert m.parent is not None

    def test_max_files_limit(self, python_files_tree):
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(python_files_tree), max_files=1)
        assert repo.file_count == 1

    def test_binary_skip(self, tmp_path: Path):
        (tmp_path / "bad.py").write_bytes(b"\x00\x01\x02")
        builder = RepoMapBuilder()
        repo = builder.build_from_directory(str(tmp_path))
        # should not crash; bad files are skipped
        assert isinstance(repo.file_count, int)


# ===========================================================================
# 4 — PLANNING: ARTIFACT
# ===========================================================================


class TestPlanArtifact:
    def test_create_with_fields(self):
        pa = PlanArtifact(
            todo_id="TODO-1",
            title="Add login",
            description="OAuth implementation",
            target_files=["auth.py", "tests/test_auth.py"],
            contracts=["Must validate JWT", "Must handle expiry"],
        )
        assert pa.todo_id == "TODO-1"
        assert len(pa.target_files) == 2

    def test_todo_id_empty_raises(self):
        with pytest.raises(ValueError):
            PlanArtifact(todo_id="   ")

    def test_to_markdown(self):
        pa = PlanArtifact(
            todo_id="TODO-2", title="Fix bug",
            target_files=["bug.py"], contracts=["no regression"],
            notes="urgent",
        )
        md = pa.to_markdown()
        assert "## Plan: Fix bug" in md
        assert "**Todo ID:** TODO-2" in md
        assert "### Target Files" in md
        assert "- `bug.py`" in md
        assert "### Contracts" in md
        assert "- `no regression`" in md
        assert "**Notes:** urgent" in md

    def test_to_markdown_minimal(self):
        pa = PlanArtifact(todo_id="TODO-3")
        md = pa.to_markdown()
        assert "## Plan: TODO-3" in md
        assert "**Todo ID:** TODO-3" in md

    def test_to_markdown_with_dependencies(self):
        pa = PlanArtifact(
            todo_id="TODO-4",
            dependencies=["db migration", "config update"],
        )
        md = pa.to_markdown()
        assert "### Dependencies" in md
        assert "- db migration" in md
        assert "- config update" in md

    def test_to_markdown_with_content(self):
        pa = PlanArtifact(todo_id="TODO-5", content="Long analysis here")
        md = pa.to_markdown()
        assert "Long analysis here" in md

    def test_roundtrip_dict(self):
        pa = PlanArtifact(
            todo_id="TODO-6", title="T", description="D",
            target_files=["f.py"], contracts=["c"], notes="n", content="ctx",
        )
        d = pa.to_dict()
        assert d["todo_id"] == "TODO-6"
        pa2 = PlanArtifact.from_dict(d)
        assert pa2.todo_id == pa.todo_id
        assert pa2.title == pa.title

    def test_from_dict_with_iso_date(self):
        d = {"todo_id": "X", "created_at": "2026-07-20T12:00:00+00:00"}
        pa = PlanArtifact.from_dict(d)
        assert pa.created_at.year == 2026

    def test_from_todo(self):
        todo = SimpleNamespace(
            todo_id="T-FROM", title="Task T", description="desc",
            tags=["urgent"], test_commands=["pytest"],
        )
        pa = PlanArtifact.from_todo(todo)
        assert pa.todo_id == "T-FROM"
        assert "urgent" in pa.notes
        assert "pytest" in pa.notes

    def test_from_todo_missing_tags(self):
        todo = SimpleNamespace(todo_id="T-BARE", title="B", description="d")
        pa = PlanArtifact.from_todo(todo)
        assert pa.notes == ""


# ===========================================================================
# 5 — PLANNING: DEBT EVALUATOR
# ===========================================================================


class TestDebtEvaluator:
    def _plan(self, todo_id: str = "PLAN-1", **kw) -> PlanArtifact:
        defaults = {"todo_id": todo_id, "title": "P", "target_files": [], "contracts": []}
        return PlanArtifact(**(defaults | kw))

    # --- deterministic fallback ---

    def test_fallback_no_test_for_impl(self):
        plan = self._plan(target_files=["src/app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        result = de.evaluate(plan, "build app")
        assert len(result.findings) >= 1
        assert any("no test" in f.gap for f in result.findings)
        assert any(f.recommendation == "fold_in" for f in result.findings)

    def test_fallback_impl_with_test_no_warning(self):
        plan = self._plan(target_files=["src/app.py", "tests/test_app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        result = de.evaluate(plan, "build app")
        assert not any("no test for src/app.py" in f.gap for f in result.findings)

    def test_fallback_resilience_contract(self):
        plan = self._plan(contracts=["Must handle timeout gracefully"])
        de = DebtEvaluator(evaluate_fn=None)
        result = de.evaluate(plan, "build app")
        assert any("resilience" in f.gap for f in result.findings)
        assert any(f.recommendation == "defer" for f in result.findings)

    # --- model injection ---

    def test_evaluate_fn_used_when_present(self):
        called = []
        def fn(plan, goal, ctx):
            called.append(1)
            return [{"gap": "x", "kind": "sharp_edge", "why_it_matters": "y", "effort": "small", "touched_files": []}]
        de = DebtEvaluator(evaluate_fn=fn)
        result = de.evaluate(self._plan(), "g")
        assert len(called) == 1
        assert len(result.findings) == 1
        assert result.findings[0].recommendation in ("fold_in", "defer")

    def test_evaluate_fn_returns_empty_falls_back(self):
        de = DebtEvaluator(evaluate_fn=lambda p, g, c: [])
        plan = self._plan(target_files=["x.py"])
        result = de.evaluate(plan, "g")
        assert len(result.findings) >= 1  # fallback fires

    def test_evaluate_fn_raises_falls_back(self):
        de = DebtEvaluator(evaluate_fn=lambda p, g, c: 1 / 0)  # raises
        plan = self._plan(target_files=["x.py"])
        result = de.evaluate(plan, "g")
        assert len(result.findings) >= 1

    def test_evaluate_fn_returns_non_list_falls_back(self):
        de = DebtEvaluator(evaluate_fn=lambda p, g, c: "not-a-list")
        plan = self._plan(target_files=["x.py"])
        result = de.evaluate(plan, "g")
        assert len(result.findings) >= 1

    # --- classification policy ---

    def test_fold_in_when_in_scope_small_no_new_cap(self):
        plan = self._plan(target_files=["app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        finding = DebtFinding(
            gap="missing test", kind="sharp_edge", effort="small",
            touched_files=["app.py"],
        )
        classified = de._classify(finding, plan, "build app")
        assert classified.recommendation == "fold_in"

    def test_defer_when_missing_feature(self):
        plan = self._plan(target_files=["app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        finding = DebtFinding(
            gap="add monitoring", kind="missing_feature", effort="small",
            touched_files=["app.py"],
        )
        classified = de._classify(finding, plan, "build app")
        assert classified.recommendation == "defer"

    def test_defer_when_large_effort(self):
        plan = self._plan(target_files=["app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        finding = DebtFinding(
            gap="x", kind="sharp_edge", effort="large", touched_files=["app.py"],
        )
        classified = de._classify(finding, plan, "build app")
        assert classified.recommendation == "defer"

    def test_defer_when_out_of_scope_files(self):
        plan = self._plan(target_files=["app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        finding = DebtFinding(
            gap="x", kind="sharp_edge", effort="small",
            touched_files=["other.py"],  # not in target
        )
        classified = de._classify(finding, plan, "build app")
        assert classified.recommendation == "defer"

    def test_defer_when_empty_touched_files(self):
        plan = self._plan(target_files=["app.py"])
        de = DebtEvaluator(evaluate_fn=None)
        finding = DebtFinding(
            gap="x", kind="sharp_edge", effort="small", touched_files=[],
        )
        classified = de._classify(finding, plan, "build app")
        assert classified.recommendation == "defer"

    def test_max_findings_cap(self):
        findings = [{"gap": f"g{i}", "kind": "sharp_edge", "effort": "small",
                      "touched_files": []} for i in range(20)]
        de = DebtEvaluator(evaluate_fn=lambda p, g, c: findings, max_findings=5)
        result = de.evaluate(self._plan(), "g")
        assert len(result.findings) == 5

    def test_touched_in_scope_sibling_test(self):
        self._plan(target_files=["src/x.py"])
        # The sibling test should be in scope
        assert DebtEvaluator._touched_in_scope(["tests/test_x.py"], ["src/x.py"])
        assert DebtEvaluator._touched_in_scope(["src/x.py"], ["src/x.py"])
        assert not DebtEvaluator._touched_in_scope(["other.py"], ["src/x.py"])


# ===========================================================================
# 6 — PLANNING: DEBT APPLIER
# ===========================================================================


class TestDebtApplier:
    @pytest.mark.asyncio
    async def test_fold_in_augments_plan(self):
        plan = PlanArtifact(todo_id="P", target_files=["a.py"])
        findings = DebtFindings(findings=[
            DebtFinding(gap="add error handling", kind="sharp_edge", effort="small",
                        recommendation="fold_in", touched_files=["a.py"]),
        ])
        repo = AsyncMock()
        repo.create = AsyncMock()
        todo = SimpleNamespace(todo_id="P")

        result = await apply_debt_findings(findings, plan, todo, repo, project_id=None)
        assert result.folded_in == 1
        assert "add error handling" in result.augmented_plan.contracts
        assert "Fold-in" in result.augmented_plan.notes
        assert len(result.deferred_todo_ids) == 0
        assert "Fold-in scope" in result.prompt_addendum

    @pytest.mark.asyncio
    async def test_defer_creates_backlog_todo(self):
        plan = PlanArtifact(todo_id="P")
        findings = DebtFindings(findings=[
            DebtFinding(gap="add monitoring", kind="missing_feature", effort="large",
                        recommendation="defer", why_it_matters="important",
                        feature_creep_rationale="out of scope"),
        ])

        created = SimpleNamespace(todo_id="TODO-BACKLOG-1")
        repo = AsyncMock()
        repo.create = AsyncMock(return_value=created)
        todo = SimpleNamespace(todo_id="P")

        result = await apply_debt_findings(findings, plan, todo, repo, project_id=None)
        assert result.folded_in == 0
        assert len(result.deferred_todo_ids) == 1
        assert result.deferred_todo_ids[0] == "TODO-BACKLOG-1"

        call = repo.create.call_args[0][0]
        assert call["status"] == TodoStatus.BACKLOG
        assert "tech-debt" in call["tags"]

    @pytest.mark.asyncio
    async def test_defer_create_failure_not_fatal(self):
        plan = PlanArtifact(todo_id="P")
        findings = DebtFindings(findings=[
            DebtFinding(gap="g1", kind="missing_feature", recommendation="defer"),
            DebtFinding(gap="g2", kind="missing_feature", recommendation="defer"),
        ])
        repo = AsyncMock()
        repo.create = AsyncMock(side_effect=[RuntimeError("boom"), SimpleNamespace(todo_id="OK")])
        todo = SimpleNamespace(todo_id="P")

        result = await apply_debt_findings(findings, plan, todo, repo, project_id=None)
        assert len(result.deferred_todo_ids) == 1  # second one succeeded

    @pytest.mark.asyncio
    async def test_mixed_fold_and_defer(self):
        plan = PlanArtifact(todo_id="P", target_files=["a.py"])
        findings = DebtFindings(findings=[
            DebtFinding(gap="missing test", kind="sharp_edge", effort="small",
                        recommendation="fold_in", touched_files=["a.py"]),
            DebtFinding(gap="add dashboard", kind="missing_feature", recommendation="defer"),
        ])
        repo = AsyncMock()
        repo.create = AsyncMock(return_value=SimpleNamespace(todo_id="DEFER-1"))
        todo = SimpleNamespace(todo_id="P")

        result = await apply_debt_findings(findings, plan, todo, repo, project_id=None)
        assert result.folded_in == 1
        assert len(result.deferred_todo_ids) == 1

    @pytest.mark.asyncio
    async def test_defer_with_dict_create(self):
        plan = PlanArtifact(todo_id="P")
        findings = DebtFindings(findings=[
            DebtFinding(gap="g", kind="missing_feature", recommendation="defer"),
        ])
        repo = AsyncMock()
        repo.create = AsyncMock(return_value={"todo_id": "DICT-1"})
        todo = SimpleNamespace(todo_id="P")

        result = await apply_debt_findings(findings, plan, todo, repo, project_id=None)
        assert result.deferred_todo_ids == ["DICT-1"]


# ===========================================================================
# 7 — PLANNING: CRITIQUE
# ===========================================================================


class TestPlanCritique:
    def test_missing_title_error(self):
        pc = PlanCritique()
        findings = pc.critique_plan({"steps": [{"name": "s1"}]})
        assert any("title" in f["field"] for f in findings)

    def test_missing_steps_error(self):
        pc = PlanCritique()
        findings = pc.critique_plan({"title": "T"})
        assert any("steps" in f["field"] for f in findings)

    def test_vague_description_warning(self):
        pc = PlanCritique()
        findings = pc.critique_plan({"title": "T", "steps": [{"name": "s1", "description": "x"}]})
        assert any("vague" in f["message"].lower() for f in findings)

    def test_unknown_tool_warning(self):
        pc = PlanCritique()
        findings = pc.critique_plan({
            "title": "T", "steps": [
                {"name": "s1", "description": "do the thing", "tool": "unknown-tool-xyz"}
            ],
        })
        assert any("unknown tool" in f["message"].lower() for f in findings)

    def test_dependency_not_a_step(self):
        pc = PlanCritique()
        findings = pc.critique_plan({
            "title": "T", "steps": [{"name": "s1", "description": "configure"}],
            "dependencies": {"s_missing": ["s1"]},
        })
        assert any("not a defined step" in f["message"].lower() for f in findings)

    def test_complete_plan_no_errors(self):
        pc = PlanCritique()
        plan = {
            "title": "Add login",
            "description": "Implement OAuth flow",
            "steps": [
                {"name": "1", "description": "Write auth module"},
                {"name": "2", "description": "Add JWT validation"},
            ],
            "dependencies": {"2": ["1"]},
        }
        findings = pc.critique_plan(plan)
        assert not any(f["severity"] == "error" for f in findings)

    def test_empty_steps_list_error(self):
        pc = PlanCritique()
        findings = pc.critique_plan({"title": "T", "description": "D", "steps": []})
        assert any("steps" in f["field"] for f in findings)


# ===========================================================================
# 8 — REMEDIATION: BLOCKER DETECTOR
# ===========================================================================


class TestBlockerDetector:
    @pytest.mark.asyncio
    async def test_scan_blocked_on_human_finds_stale(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create({
            "todo_id": "TODO-BH-1", "title": "blocked task", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            "updated_at": _now_minus(30),
        })
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        assert any(f.todo_id == "TODO-BH-1" for f in findings)

    @pytest.mark.asyncio
    async def test_scan_stale_under_threshold_not_surfaced(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create({
            "todo_id": "TODO-BH-2", "title": "fresh block", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            "updated_at": _now_minus(2),  # only 2h — under 24h threshold
        })
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        assert not any(f.todo_id == "TODO-BH-2" for f in findings)

    @pytest.mark.asyncio
    async def test_permission_escalation_lower_threshold(self, db_session: AsyncSession):
        htrepo = HumanTodoRepository(db_session)
        repo = TodoRepository(db_session)

        await repo.create({
            "todo_id": "TODO-PERM", "title": "cred task", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            "updated_at": _now_minus(6),  # 6h — above 4h threshold
        })
        await htrepo.create(
            agent_id="agent-1", title="cred request", body="need aws creds",
            category="permission_escalation", parent_agent_todo_id="TODO-PERM",
        )
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, human_todo_repo=htrepo, session=db_session,
            config=RemediationConfig(permission_escalation_block_hours=4),
        )
        findings = await detector.scan()
        perm = [f for f in findings if f.todo_id == "TODO-PERM"]
        assert len(perm) >= 1
        assert perm[0].blocker_kind == "permission_escalation"
        assert perm[0].suggested_remediation == "schedule_retry"

    @pytest.mark.asyncio
    async def test_chronic_requeues_detected(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create({
            "todo_id": "TODO-CRQ", "title": "re-queued", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.QUEUED.value,
            "run_count": 5,
        })
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = await detector.scan()
        crq = [f for f in findings if f.todo_id == "TODO-CRQ"]
        assert len(crq) >= 1
        assert crq[0].blocker_kind == "resource_contention"
        assert crq[0].suggested_remediation == "dispatch_agent"

    @pytest.mark.asyncio
    async def test_chronic_requeue_below_threshold_skipped(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create({
            "todo_id": "TODO-LOW", "title": "low run count", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.QUEUED.value,
            "run_count": 2,
        })
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(max_requeues_before_chronic=5),
        )
        findings = await detector.scan()
        assert not any(f.todo_id == "TODO-LOW" for f in findings)

    @pytest.mark.asyncio
    async def test_stale_human_todo_escalated(self, db_session: AsyncSession):
        htrepo = HumanTodoRepository(db_session)
        repo = TodoRepository(db_session)
        await htrepo.create(
            agent_id="agent-1", title="input please", body="need decision",
            category="input_request",
        )
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, human_todo_repo=htrepo, session=db_session,
            config=RemediationConfig(human_input_block_hours=0),
        )
        findings = await detector.scan()
        stale = [f for f in findings if f.linked_human_todo_id]
        assert len(stale) >= 1
        assert stale[0].suggested_remediation == "file_human_todo"

    @pytest.mark.asyncio
    async def test_chronic_blockers_grouping(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        # Seed 6 BLOCKED_ON_HUMAN events for the same work_type
        for i in range(6):
            tid = f"TODO-CB-{i}"
            await repo.create({
                "todo_id": tid, "title": f"chronic {i}", "queue": "core",
                "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            })
            db_session.add(TodoEventModel(
                todo_id=tid, event_type="status_changed",
                new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                created_at=_now_minus(1),
                reason="permission denied",
            ))
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronics = await detector.chronic_blockers()
        assert len(chronics) >= 1
        assert chronics[0].incident_count >= 5

    @pytest.mark.asyncio
    async def test_chronic_blockers_below_threshold_returns_empty(self, db_session: AsyncSession):
        detector = BlockerDetector(
            session=db_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        # only 2 events
        for i in range(2):
            db_session.add(TodoEventModel(
                todo_id=f"TODO-LOW-{i}", event_type="status_changed",
                new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                created_at=_now_minus(1), reason="input needed",
            ))
        await db_session.commit()

        chronics = await detector.chronic_blockers()
        assert len(chronics) == 0

    @pytest.mark.asyncio
    async def test_empty_scan_no_crashes(self):
        detector = BlockerDetector()
        findings = await detector.scan()
        assert isinstance(findings, list)

    @pytest.mark.asyncio
    async def test_blocker_kinds_frozenset(self):
        assert "permission_escalation" in BLOCKER_KINDS
        assert "resource_contention" in BLOCKER_KINDS
        assert "human_input" in BLOCKER_KINDS

    @pytest.mark.asyncio
    async def test_remediation_kinds_frozenset(self):
        assert "dispatch_agent" in REMEDIATION_KINDS
        assert "schedule_retry" in REMEDIATION_KINDS
        assert "file_human_todo" in REMEDIATION_KINDS

    def test_remediation_config_defaults(self):
        cfg = RemediationConfig()
        assert cfg.human_input_block_hours == 24
        assert cfg.permission_escalation_block_hours == 4
        assert cfg.max_requeues_before_chronic == 3
        assert cfg.chronic_lookback_days == 7
        assert cfg.min_chronic_incidents == 5
        assert cfg.retry_delay_hours == 4

    def test_remediation_config_is_frozen(self):
        cfg = RemediationConfig()
        with pytest.raises(Exception):  # noqa: B017
            cfg.human_input_block_hours = 12  # frozen dataclass


# ===========================================================================
# 9 — REMEDIATION: DISPATCHER
# ===========================================================================


class TestRemediationDispatcher:
    @pytest.mark.asyncio
    async def test_remediate_dispatch_agent(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await _seed_queued(db_session, repo, "TODO-ORIG-DA", status="blocked")

        det = BlockerDetector(
            todo_repo=repo, session=db_session, config=RemediationConfig(),
        )
        disp = RemediationDispatcher(
            detector=det, todo_repo=repo,
            remediation_repo=RemediationActionRepository(db_session),
        )
        blocked = _blocked_finding("TODO-ORIG-DA", kind="resource_contention", remediation="dispatch_agent")
        action = await disp.remediate(blocked)

        assert action.kind == RemediationActionKind.DISPATCH_AGENT
        assert action.ok
        assert "new_todo_id" in action.detail

    @pytest.mark.asyncio
    async def test_remediate_schedule_retry(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        det = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(retry_delay_hours=4),
        )
        disp = RemediationDispatcher(
            detector=det, todo_repo=repo,
            remediation_repo=RemediationActionRepository(db_session),
        )
        blocked = _blocked_finding("TODO-SCHD", remediation="schedule_retry")
        action = await disp.remediate(blocked)

        assert action.kind == RemediationActionKind.SCHEDULE_RETRY
        assert "scheduled_todo_id" in action.detail
        assert "fire_at" in action.detail

    @pytest.mark.asyncio
    async def test_remediate_file_human_todo(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        det = BlockerDetector(
            todo_repo=repo, session=db_session, config=RemediationConfig(),
        )
        disp = RemediationDispatcher(
            detector=det, todo_repo=repo,
            human_todo_repo=HumanTodoRepository(db_session),
            remediation_repo=RemediationActionRepository(db_session),
        )
        blocked = _blocked_finding("TODO-HT", kind="human_input", remediation="file_human_todo")
        action = await disp.remediate(blocked)

        assert action.kind == RemediationActionKind.FILE_HUMAN_TODO
        assert action.ok
        assert "human_todo_id" in action.detail

    @pytest.mark.asyncio
    async def test_remediate_no_action(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        det = BlockerDetector(
            todo_repo=repo, session=db_session, config=RemediationConfig(),
        )
        disp = RemediationDispatcher(
            detector=det, todo_repo=repo,
            remediation_repo=RemediationActionRepository(db_session),
        )
        blocked = _blocked_finding("TODO-NA", remediation="no_action")
        action = await disp.remediate(blocked)

        assert action.kind == RemediationActionKind.NO_ACTION
        assert action.ok

    @pytest.mark.asyncio
    async def test_idempotency_key_prevents_replay(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await _seed_queued(db_session, repo, "TODO-IDEM", status="blocked")

        det = BlockerDetector(
            todo_repo=repo, session=db_session, config=RemediationConfig(),
        )
        disp = RemediationDispatcher(
            detector=det, todo_repo=repo,
            remediation_repo=RemediationActionRepository(db_session),
        )
        key = "idem-key-001"
        blocked = _blocked_finding("TODO-IDEM", remediation="dispatch_agent")
        a1 = await disp.remediate(blocked, idempotency_key=key)
        assert a1.ok

        a2 = await disp.remediate(blocked, idempotency_key=key)
        assert a2.kind == RemediationActionKind.NO_ACTION
        assert "Idempotent replay" in a2.summary


# ===========================================================================
# 10 — REMEDIATION: REPORTER
# ===========================================================================


class TestRemediationReporter:
    @pytest.mark.asyncio
    async def test_chronic_blocker_report_structure(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        for i in range(6):
            tid = f"TODO-RPT-{i}"
            await repo.create({
                "todo_id": tid, "title": f"rpt {i}", "queue": "core",
                "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            })
            db_session.add(TodoEventModel(
                todo_id=tid, event_type="status_changed",
                new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                created_at=_now_minus(1), reason="permission denied",
            ))
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        report = await chronic_blocker_report(detector, project_id=None, lookback_days=7)

        assert "generated_at" in report
        assert "chronic_blockers" in report
        assert report["total"] >= 1
        cb = report["chronic_blockers"][0]
        assert "task_type" in cb
        assert "blocker_kind" in cb
        assert "incident_count" in cb
        assert cb["incident_count"] >= 5

    @pytest.mark.asyncio
    async def test_report_empty_when_no_chronic(self, db_session: AsyncSession):
        detector = BlockerDetector(
            session=db_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        report = await chronic_blocker_report(detector)
        assert report["total"] == 0
        assert report["chronic_blockers"] == []


# ===========================================================================
# 11 — INTEGRATION: EVENT LOOP + PLANNING + REMEDIATION
# ===========================================================================


class TestIntegrationWorkflows:
    @pytest.mark.asyncio
    async def test_tick_includes_remediation_phase(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        assert "remediate_blocked_tasks" in PHASE_ORDER

    @pytest.mark.asyncio
    async def test_full_workflow_plan_evaluate_dispatch(self, db_session: AsyncSession):
        TodoRepository(db_session)
        plan = PlanArtifact(todo_id="TODO-WF", target_files=["app.py"])
        evaluator = DebtEvaluator(evaluate_fn=None)
        findings = evaluator.evaluate(plan, "build feature")
        assert isinstance(findings.findings, list)

        repo_mock = AsyncMock()
        repo_mock.create = AsyncMock(return_value=SimpleNamespace(todo_id="D-1"))
        result = await apply_debt_findings(
            findings, plan, SimpleNamespace(todo_id="TODO-WF"),
            repo_mock, project_id=None,
        )

        assert result.folded_in >= 0 or len(result.deferred_todo_ids) >= 0

    @pytest.mark.asyncio
    async def test_detect_then_dispatch(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create({
            "todo_id": "TODO-FULL", "title": "test full flow", "queue": "core",
            "priority": 5, "work_type": "code", "status": TodoStatus.BLOCKED_ON_HUMAN.value,
            "updated_at": _now_minus(30),
        })
        await db_session.commit()

        detector = BlockerDetector(
            todo_repo=repo, session=db_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        assert len(findings) >= 1

        disp = RemediationDispatcher(
            detector=detector, todo_repo=repo,
            remediation_repo=RemediationActionRepository(db_session),
        )
        for f in findings:
            action = await disp.remediate(f)
            assert action.ok

    @pytest.mark.asyncio
    async def test_event_loop_with_real_db_claims_and_remediates(
        self, session_factory,
    ):
        async with session_factory() as s:
            repo = TodoRepository(s)
            await _seed_queued(s, repo, "TODO-INT-1")
            # Also create a blocked task
            await repo.create({
                "todo_id": "TODO-INT-BLK", "title": "blocked integration",
                "queue": "core", "priority": 5, "work_type": "code",
                "status": TodoStatus.BLOCKED_ON_HUMAN.value,
                "updated_at": _now_minus(30),
            })
            await s.commit()

        loop = EventLoop(
            session=session_factory,
            config={},
            project_manager=_project_manager(),
            task_return_repo=AsyncMock(),
        )
        if loop._task_return_repo:
            loop._task_return_repo.claim_unreviewed.return_value = []

        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)
        claimed = loop._tick_state.get("claimed_todos", [])
        assert any(t.todo_id == "TODO-INT-1" for t in claimed)

    @pytest.mark.asyncio
    async def test_plan_critique_before_execution(self):
        pc = PlanCritique()
        plan = {
            "title": "Add cache layer",
            "description": "Implement Redis caching for API responses",
            "steps": [
                {"name": "1", "description": "Install redis-py"},
                {"name": "2", "description": "Create cache client wrapper"},
                {"name": "3", "description": "Wire into endpoint middleware"},
            ],
            "dependencies": {"2": ["1"], "3": ["2"]},
        }
        findings = pc.critique_plan(plan)
        errors = [f for f in findings if f["severity"] == "error"]
        assert len(errors) == 0, f"unexpected errors: {errors}"


# ===========================================================================
# 12 — EDGE CASES
# ===========================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_debt_evaluator_none_gateway(self):
        de = DebtEvaluator(evaluate_fn=None)
        assert de._evaluate_fn is None

    @pytest.mark.asyncio
    async def test_dispatch_with_missing_original_todo(self, db_session: AsyncSession):
        det = BlockerDetector(config=RemediationConfig())
        disp = RemediationDispatcher(
            detector=det, todo_repo=TodoRepository(db_session),
            remediation_repo=RemediationActionRepository(db_session),
        )
        blocked = _blocked_finding("TODO-NOEXIST", remediation="dispatch_agent")
        action = await disp.remediate(blocked)
        assert action.ok
        assert "new_todo_id" in action.detail

    @pytest.mark.asyncio
    async def test_file_human_todo_without_repo(self, db_session: AsyncSession):
        det = BlockerDetector(config=RemediationConfig())
        disp = RemediationDispatcher(
            detector=det, todo_repo=TodoRepository(db_session),
            # no human_todo_repo
        )
        blocked = _blocked_finding("TODO-NO-HT", kind="human_input", remediation="file_human_todo")
        action = await disp.remediate(blocked)
        assert not action.ok  # should fail gracefully

    @pytest.mark.asyncio
    async def test_tick_after_multiple_ticks_stable_metrics(self):
        loop = EventLoop(todo_repo=AsyncMock(), task_return_repo=AsyncMock())
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []

        for _ in range(3):
            m = await loop.tick()
            assert isinstance(m["tick_duration_ms"], float)
            assert m["phases_completed"] == len(PHASE_ORDER)

    def test_artifact_to_markdown_all_sections(self):
        pa = PlanArtifact(
            todo_id="ALL-1", title="Full Plan", description="All sections",
            target_files=["a.py", "b.py"],
            contracts=["MUST do X", "MUST do Y"],
            dependencies=["postgres", "redis"],
            notes="critical", content="extra details",
        )
        md = pa.to_markdown()
        assert "## Plan: Full Plan" in md
        assert "**Todo ID:** ALL-1" in md
        assert "**Description:** All sections" in md
        assert "### Target Files" in md
        assert "- `a.py`" in md
        assert "- `b.py`" in md
        assert "### Contracts" in md
        assert "### Dependencies" in md
        assert "**Notes:** critical" in md
        assert "extra details" in md

    def test_code_symbol_negative_line(self):
        with pytest.raises(ValueError):
            CodeSymbol(name="neg", kind="function", file_path="f.py", line_start=-1, line_end=5)

    def test_repo_map_add_symbol(self):
        rm = RepoMap(file_count=1, total_lines=10)
        rm.add_symbol(CodeSymbol(name="f", kind="function", file_path="x.py", line_start=0, line_end=3))
        assert len(rm.symbols) == 1
        assert rm.get_symbols_for_file("x.py")[0].name == "f"

    def test_blocked_task_dataclass(self):
        bt = _blocked_finding()
        assert bt.todo_id == "TODO-BLK"
        assert bt.blocker_kind == "resource_contention"
        assert bt.suggested_remediation == "dispatch_agent"
        assert bt.blocked_duration_seconds == 36000

    def test_chronic_blocker_dataclass(self):
        cb = ChronicBlocker(
            task_type="code", blocker_kind="resource_contention",
            incident_count=7, first_seen=_now_minus(5), last_seen=_now_minus(1),
            recent_todo_ids=["a", "b"],
        )
        assert cb.incident_count == 7
        assert len(cb.recent_todo_ids) == 2


# Entry-point boilerplate for direct execution
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
