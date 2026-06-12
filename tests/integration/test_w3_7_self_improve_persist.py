"""W3.7 (H2): self-improvement persists its todos via TodoRepository.

Previously _phase_self_improve enqueued into a throwaway in-memory harness, so
"todos_enqueued: N" reported todos that were immediately discarded. The phase
must write generated fix-todos through TodoRepository so they survive the tick.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy.ext.asyncio import create_async_engine

from general_ludd.db.repository import TodoRepository
from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await ensure_tables(engine)
    return create_async_session_factory(engine)


class TestSelfImprovePersistence:
    async def test_self_improve_writes_todos_to_db(self, monkeypatch):
        factory = await _make_session_factory()

        # Stub the harness so the test does not depend on real repo gap analysis.
        fake_findings = [{"type": "missing_tests", "file": "x.py", "severity": "high",
                          "message": "x.py has no test file"}]
        fake_todos = [
            {"title": "Add tests for x.py", "description": "x.py has no test file",
             "work_type": "test", "priority": "high"},
        ]

        import general_ludd.self_improve.harness as harness_mod

        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = fake_findings
        fake_harness.generate_fix_todos.return_value = fake_todos
        monkeypatch.setattr(
            harness_mod, "SelfImprovementHarness", lambda *a, **k: fake_harness
        )

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
            assert persisted.status == TodoStatus.BACKLOG.value

        # The harness's in-memory enqueue must NOT be the persistence path.
        fake_harness.enqueue_todos.assert_not_called()

    async def test_self_improve_disabled_when_interval_zero(self):
        factory = await _make_session_factory()
        loop = EventLoop(session=factory, self_improve_interval=0)
        await loop.tick()
        async with factory() as session:
            repo = TodoRepository(session)
            rows = await repo.list_all()
            assert rows == []
