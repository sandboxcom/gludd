"""D2 integration: project gate wired into review/reconcile path end-to-end.

Simulates a review of an external project whose project.yml declares a gate.
Verifies that ``run_project_gate`` is invoked and that:
  * A PASSING project gate allows the todo to reach COMPLETE.
  * A FAILING project gate downgrades COMPLETE -> NEEDS_MORE_WORK.
  * A project without project.yml is unaffected (gate skipped).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.repository import TaskReturnRepository, TodoRepository
from general_ludd.db.session import (
    create_async_session_factory,
    ensure_tables,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
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


async def _seed_todo_in_review(session, todo_id: str) -> int:
    repo = TodoRepository(session)
    todo = await repo.create(
        {
            "todo_id": todo_id,
            "title": "external project review",
            "status": TodoStatus.REVIEWING_RETURN.value,
            "queue": "core",
            "work_type": "code",
        }
    )
    await session.flush()
    return todo.version


async def _seed_return(session, return_id: str, todo_id: str) -> None:
    repo = TaskReturnRepository(session)
    await repo.create(
        {
            "return_id": return_id,
            "todo_id": todo_id,
            "job_id": f"JOB-{return_id}",
            "playbook": "noop.yml",
            "queue": "core",
            "status": "created",
        }
    )
    await session.flush()


def _write_project_yml(root: Path, *, test_passes: bool = True) -> None:
    test_cmd = "true" if test_passes else "false"
    (root / "project.yml").write_text(
        "name: integration-test-project\n"
        'allowed_exec: ["true", "false"]\n'
        "commands:\n"
        f'  lint: "true"\n'
        f'  test: "{test_cmd}"\n',
        encoding="utf-8",
    )


class TestProjectGateIntegration:
    """End-to-end: reviewer → apply_decision → run_project_gate → transition."""

    async def test_passing_gate_allows_complete(self, session_factory, tmp_path):
        """A project whose gate passes (lint + test green) reaches COMPLETE."""
        factory = session_factory
        _write_project_yml(tmp_path, test_passes=True)

        # Satisfy verify_completion's evidence gate with a real artifact file.
        artifact = tmp_path / "diff.patch"
        artifact.write_text("--- a\n+++ b\n")

        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-D2-PASS")
            await _seed_return(session, "RET-D2-PASS", "TODO-D2-PASS")
            await session.commit()

        reviewer = MagicMock()
        reviewer.review_return.return_value = TaskDecision(
            return_id="RET-D2-PASS",
            matched_todo_id="TODO-D2-PASS",
            decision="complete",
            confidence=0.9,
            evidence_refs=["artifact:diff.patch"],
        )

        loop = EventLoop(
            session=factory,
            reviewer=reviewer,
            config={"repo_root": str(tmp_path)},
        )
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-D2-PASS")
            assert todo is not None
            assert todo.status == TodoStatus.COMPLETE.value

    async def test_failing_gate_downgrades_to_needs_more_work(
        self, session_factory, tmp_path
    ):
        """A project whose gate fails (test FAILS) is downgraded to needs_more_work."""
        factory = session_factory
        _write_project_yml(tmp_path, test_passes=False)

        artifact = tmp_path / "diff.patch"
        artifact.write_text("--- a\n+++ b\n")

        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-D2-FAIL")
            await _seed_return(session, "RET-D2-FAIL", "TODO-D2-FAIL")
            await session.commit()

        reviewer = MagicMock()
        reviewer.review_return.return_value = TaskDecision(
            return_id="RET-D2-FAIL",
            matched_todo_id="TODO-D2-FAIL",
            decision="complete",
            confidence=0.9,
            evidence_refs=["artifact:diff.patch"],
        )

        loop = EventLoop(
            session=factory,
            reviewer=reviewer,
            config={"repo_root": str(tmp_path)},
        )
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-D2-FAIL")
            assert todo is not None
            assert todo.status != TodoStatus.COMPLETE.value
            assert todo.status == TodoStatus.NEEDS_MORE_WORK.value

    async def test_no_project_yml_skips_gate(self, session_factory, tmp_path):
        """A todo with NO project.yml is unaffected — gate is skipped."""
        factory = session_factory
        # No _write_project_yml call — gate is opt-in via project.yml.

        artifact = tmp_path / "diff.patch"
        artifact.write_text("--- a\n+++ b\n")

        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-D2-NOPROJ")
            await _seed_return(session, "RET-D2-NOPROJ", "TODO-D2-NOPROJ")
            await session.commit()

        reviewer = MagicMock()
        reviewer.review_return.return_value = TaskDecision(
            return_id="RET-D2-NOPROJ",
            matched_todo_id="TODO-D2-NOPROJ",
            decision="complete",
            confidence=0.9,
            evidence_refs=["artifact:diff.patch"],
        )

        loop = EventLoop(
            session=factory,
            reviewer=reviewer,
            config={"repo_root": str(tmp_path)},
        )
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-D2-NOPROJ")
            assert todo is not None
            assert todo.status == TodoStatus.COMPLETE.value
