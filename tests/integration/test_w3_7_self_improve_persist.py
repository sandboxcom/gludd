"""W3.7 (H2): self-improvement persists its todos via TodoRepository.

Previously _phase_self_improve enqueued into a throwaway in-memory harness, so
"todos_enqueued: N" reported todos that were immediately discarded. The phase
must write generated fix-todos through TodoRepository so they survive the tick.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import ProjectModel
from general_ludd.db.repository import TodoRepository
from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await ensure_tables(engine)
        yield create_async_session_factory(engine)
    finally:
        await engine.dispose()


class TestSelfImprovePersistence:
    async def test_self_improve_writes_todos_to_db(self, session_factory, monkeypatch):
        factory = session_factory

        # Stub the harness so the test does not depend on real repo gap analysis.
        fake_findings = [{"type": "missing_tests", "file": "x.py", "severity": "high",
                          "message": "x.py has no test file"}]
        fake_todos = [
            {"title": "Add tests for x.py", "description": "x.py has no test file",
             "work_type": "test", "priority": "high"},
        ]

        import general_ludd.event_loop.loop as loop_mod
        import general_ludd.self_improve.harness as harness_mod

        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = fake_findings
        fake_harness.generate_fix_todos.return_value = fake_todos
        monkeypatch.setattr(
            harness_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness
        )
        monkeypatch.setattr(loop_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)

        # interval=1 → runs every tick.
        loop = EventLoop(session=factory, self_improve_interval=1)
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            rows = await repo.list_all()
            titles = [r.title for r in rows]
            assert "Add tests for x.py" in titles, (
                f"self-improve todo not persisted; rows={titles}"
            )
            persisted = next(r for r in rows if r.title == "Add tests for x.py")
            # Security: auto_queue defaults to False, so a self-authored
            # self-improve todo is parked in APPROVAL_REQUIRED behind a human
            # review gate rather than QUEUED (immediate execution) — closing the
            # self-modification approval bypass. A human releases it to QUEUED via
            # SelfImproveApprovalManager (the /admin/self-improve/approvals routes
            # or `gludd self-improve approve`). Set self_improve.auto_queue: true
            # to opt back into immediate QUEUED admission.
            assert persisted.status == TodoStatus.APPROVAL_REQUIRED.value

        # The harness's in-memory enqueue must NOT be the persistence path.
        fake_harness.enqueue_todos.assert_not_called()

    async def test_config_auto_queue_config_value_is_ignored(self, session_factory, monkeypatch):
        """C13: auto_queue removed. Setting self_improve.auto_queue: true in
        config MUST be ignored — todos still land in APPROVAL_REQUIRED."""
        factory = session_factory

        fake_findings = [{"type": "missing_tests", "file": "y.py", "severity": "high",
                          "message": "y.py has no test file"}]
        fake_todos = [
            {"title": "Add tests for y.py", "description": "y.py has no test file",
             "work_type": "test", "priority": "high"},
        ]

        import general_ludd.event_loop.loop as loop_mod
        import general_ludd.self_improve.harness as harness_mod

        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = fake_findings
        fake_harness.generate_fix_todos.return_value = fake_todos
        monkeypatch.setattr(harness_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)
        monkeypatch.setattr(loop_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)

        loop = EventLoop(
            session=factory,
            self_improve_interval=1,
            config={"tick_interval": 1.0, "self_improve": {"auto_queue": True}},
        )
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            rows = await repo.list_all()
            persisted = next(r for r in rows if r.title == "Add tests for y.py")
            assert persisted.status == TodoStatus.APPROVAL_REQUIRED.value
            assert persisted.status != TodoStatus.QUEUED.value

    async def test_self_improve_stamps_tick_project_id(self, session_factory, monkeypatch):
        """W3.7/H2: a persisted self-improve todo must carry the tick's project_id
        (tenant scope) — not be orphaned with project_id=NULL.
        """
        factory = session_factory

        # Seed the project the tick will select.
        async with factory() as session:
            session.add(ProjectModel(project_id="proj-A", name="Project A"))
            await session.commit()

        fake_findings = [{"type": "missing_tests", "file": "a.py", "severity": "high",
                          "message": "a.py has no test file"}]
        fake_todos = [
            {"title": "Add tests for a.py", "description": "a.py has no test file",
             "work_type": "test", "priority": "high"},
        ]

        import general_ludd.event_loop.loop as loop_mod
        import general_ludd.self_improve.harness as harness_mod

        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = fake_findings
        fake_harness.generate_fix_todos.return_value = fake_todos
        monkeypatch.setattr(harness_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)
        monkeypatch.setattr(loop_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)

        loop = EventLoop(session=factory, self_improve_interval=1)
        # The tick selects ONE project; pin it to proj-A.
        with patch.object(loop, "_select_tick_project_id", return_value="proj-A"):
            await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            rows = await repo.list_all()
            persisted = next(r for r in rows if r.title == "Add tests for a.py")
            assert persisted.project_id == "proj-A", (
                "self-improve todo must carry the tick project_id (tenant scope), "
                f"got project_id={persisted.project_id!r}"
            )

    async def test_self_improve_max_open_cap_is_per_project(self, session_factory, monkeypatch):
        """W3.7/H2: the max_open admission cap must be counted PER PROJECT. One
        tenant's open self-improve todos must NOT consume another tenant's cap
        (no cross-tenant leak).
        """
        factory = session_factory

        async with factory() as session:
            session.add(ProjectModel(project_id="proj-A", name="Project A"))
            session.add(ProjectModel(project_id="proj-B", name="Project B"))
            await session.commit()

        # Harness emits 3 distinct fix-todos each cycle.
        fake_findings = [
            {"type": "missing_tests", "file": f"f{i}.py", "severity": "high",
             "message": f"f{i}.py has no test file"} for i in range(3)
        ]
        fake_todos = [
            {"title": f"Fix f{i}.py", "description": f"f{i}.py", "work_type": "test",
             "priority": "high"} for i in range(3)
        ]

        import general_ludd.event_loop.loop as loop_mod
        import general_ludd.self_improve.harness as harness_mod

        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = fake_findings
        fake_harness.generate_fix_todos.return_value = fake_todos
        monkeypatch.setattr(harness_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)
        monkeypatch.setattr(loop_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness)

        # max_open=2 → each project may have at most 2 open self-improve todos.
        loop = EventLoop(
            session=factory,
            self_improve_interval=1,
            config={"tick_interval": 1.0, "self_improve": {"max_open": 2}},
        )

        # Tick 1: fill project A's cap (3 generated, only 2 admitted under cap=2).
        with patch.object(loop, "_select_tick_project_id", return_value="proj-A"):
            await loop.tick()
        # Tick 2: project A is already AT its cap → 0 new admitted.
        with patch.object(loop, "_select_tick_project_id", return_value="proj-A"):
            await loop.tick()
        # Tick 3: project B is a FRESH tenant → its cap is independent, so B gets 2.
        with patch.object(loop, "_select_tick_project_id", return_value="proj-B"):
            await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            a_rows = await repo.list_by_work_type("self_improve", project_id="proj-A")
            b_rows = await repo.list_by_work_type("self_improve", project_id="proj-B")

        assert len(a_rows) == 2, (
            f"project A should be capped at max_open=2, got {len(a_rows)}"
        )
        assert len(b_rows) == 2, (
            "project B's cap must be independent of project A's — A's full cap must "
            f"NOT block B from creating its own self-improve todos; got {len(b_rows)} "
            "(cross-tenant cap leak if 0)"
        )
        # Belt-and-suspenders: every persisted self-improve todo is tenant-scoped.
        assert all(r.project_id == "proj-A" for r in a_rows)
        assert all(r.project_id == "proj-B" for r in b_rows)

    async def test_self_improve_disabled_when_interval_zero(self, session_factory):
        factory = session_factory
        loop = EventLoop(session=factory, self_improve_interval=0)
        await loop.tick()
        async with factory() as session:
            repo = TodoRepository(session)
            rows = await repo.list_all()
            assert rows == []
