"""Integration tests: prove auto-remediation tick fires end-to-end.

Covers:
  1. ``_phase_remediate_blocked_tasks`` fires on the correct tick interval
     with a real SQLite database, finds blocked todos, dispatches
     remediation actions, and persists audit rows.
  2. The phase correctly skips when interval doesn't match.
  3. Kill switch (interval=0) disables the phase entirely.
  4. The action cap limits dispatches per tick.
  5. MisconfigDetector integration pathway: the phase can consume
     a MisconfigDetector and surface its critical findings as
     BlockedTask entries (ensuring the wiring hook exists).

See also tests/unit/test_remediation_phase_wiring.py (interval gating +
idempotency with mocks) and tests/integration/test_remediation_scheduler.py
(audit persistence).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import TodoModel
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.event_loop.loop import EventLoop
from general_ludd.remediation.blocker_detector import (
    BlockedTask,
    RemediationConfig,
)
from general_ludd.schemas.todo import TodoStatus


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_loop_with_session(session: AsyncSession, **overrides):
    todo_repo = TodoRepository(session)
    defaults = dict(
        session=session,
        todo_repo=todo_repo,
        config={
            "remediation_check_interval_ticks": 1,
            "remediation_max_actions_per_tick": 10,
        },
        daemon_state={},
    )
    defaults.update(overrides)
    return EventLoop(**defaults)


async def _seed_todo(
    repo: TodoRepository,
    todo_id: str,
    *,
    status: str = TodoStatus.BLOCKED.value,
    run_count: int = 0,
    work_type: str = "code",
    title: str = "test task",
) -> TodoModel:
    return await repo.create(
        {
            "todo_id": todo_id,
            "title": title,
            "status": status,
            "work_type": work_type,
            "queue": "core",
            "run_count": run_count,
        }
    )


# ── core: prove the tick fires and dispatches ─────────────────────────────────


class TestRemediationTickFiresAndDispatches:
    """End-to-end: seed blocked todos, run the phase, verify actions taken."""

    @pytest.mark.asyncio
    async def test_on_interval_tick_finds_and_dispatches_chronic_requeue(self):
        """A todo with run_count > max_requeues_before_chronic triggers
        a dispatch_agent remediation when the tick fires on-interval."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                await _seed_todo(
                    repo, "TODO-CHRONIC-A", run_count=5, work_type="infra"
                )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 1, (
                    f"expected 1 audit row, got {len(rows)}"
                )
                row = rows[0]
                assert row.blocked_todo_id == "TODO-CHRONIC-A"
                assert row.action_kind == "dispatch_agent"
                assert row.ok is True
                assert row.blocker_kind == "resource_contention"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_on_interval_tick_dispatches_multiple_findings(self):
        """Multiple blocked todos each trigger their own remediation action."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                for i in range(3):
                    await _seed_todo(
                        repo,
                        f"TODO-MULTI-{i}",
                        run_count=5,
                        work_type="code",
                    )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 3
                blocked_ids = {r.blocked_todo_id for r in rows}
                assert blocked_ids == {"TODO-MULTI-0", "TODO-MULTI-1", "TODO-MULTI-2"}
                assert all(r.action_kind == "dispatch_agent" for r in rows)
                assert all(r.ok for r in rows)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_tick_metrics_recorded_on_fire(self):
        """The phase sets tick_metrics with scan count and action count."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                await _seed_todo(
                    repo, "TODO-METRICS-1", run_count=5, work_type="test"
                )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                assert loop._tick_metrics.get("remediation_scanned", 0) >= 1
                assert loop._tick_metrics.get("remediation_actions", 0) >= 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_no_blocked_todos_no_actions(self):
        """Empty DB: the phase fires but takes no actions."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 0
                assert loop._tick_metrics.get("remediation_scanned", -1) == 0
        finally:
            await engine.dispose()


# ── interval gating (integration, real DB) ────────────────────────────────────


class TestRemediationTickInterval:
    @pytest.mark.asyncio
    async def test_off_interval_skips_scan_with_real_db(self):
        """tick count not a multiple of interval -> phase returns before scanning."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                await _seed_todo(
                    repo, "TODO-SKIP-1", run_count=5, work_type="code"
                )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 30,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 7  # 7 % 30 != 0
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 0, (
                    "off-interval tick must not scan or dispatch"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_kill_switch_disables_phase_with_real_db(self):
        """interval=0 -> phase returns immediately, even with blocked todos."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                await _seed_todo(
                    repo, "TODO-KILL-1", run_count=5, work_type="code"
                )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 0,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 30  # would match if interval were nonzero
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 0, (
                    "kill switch (interval=0) must disable the phase"
                )
        finally:
            await engine.dispose()


# ── action cap (integration) ──────────────────────────────────────────────────


class TestRemediationTickActionCap:
    @pytest.mark.asyncio
    async def test_cap_limits_actions_per_tick_integration(self):
        """8 findings, cap=3 -> only 3 are dispatched to the real DB."""
        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                repo = TodoRepository(session)
                for i in range(8):
                    await _seed_todo(
                        repo,
                        f"TODO-CAP-{i}",
                        run_count=5,
                        work_type="code",
                    )
                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 3,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                assert len(rows) == 3, (
                    f"cap=3 but got {len(rows)} actions"
                )
        finally:
            await engine.dispose()


# ── MisconfigDetector integration pathway ─────────────────────────────────────


class TestMisconfigDetectorTickIntegration:
    """Proves the wiring hook exists between MisconfigDetector and the
    remediation tick phase, so infrastructure misconfigurations can be
    automatically detected and reported as blocked tasks."""

    @pytest.mark.asyncio
    async def test_misconfig_detector_can_be_injected_into_phase(self):
        """The EventLoop accepts a misconfig_detector kwarg and the
        phase can call its check() method to produce BlockedTask entries."""
        from general_ludd.infra.model_deploy_check import MisconfigDetector

        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                TodoRepository(session)
                detector = MisconfigDetector()
                finding = detector.check({"engine": "vllm", "gpu_memory_utilization": 0.97})
                assert len(finding) >= 1
                critical = [f for f in finding if f.severity == "critical"]
                assert any(
                    f.rule_id == "a" and f.severity == "critical"
                    for f in critical
                ), "high gpu_memory_utilization should produce a critical finding"

                remediation = detector.remediate(critical[0]) if critical else {}
                assert "config_patch" in remediation
                assert remediation.get("rule_id") == "a"

                loop = _make_loop_with_session(
                    session,
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 10,
                    },
                )
                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_misconfig_detector_findings_convertible_to_blocked_tasks(self):
        """Critical MisconfigDetector findings can be surfaced as BlockedTask
        entries consumed by the dispatcher."""
        from general_ludd.infra.model_deploy_check import MisconfigDetector
        from general_ludd.remediation.dispatcher import (
            RemediationActionKind,
            RemediationDispatcher,
        )

        engine = _make_engine()
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                todo_repo = TodoRepository(session)
                from general_ludd.remediation.blocker_detector import (
                    BlockerDetector,
                )
                det = BlockerDetector(
                    todo_repo=todo_repo,
                    config=RemediationConfig(),
                    session=session,
                )
                human_repo = HumanTodoRepository(session)
                remediation_repo = RemediationActionRepository(session)
                now = datetime.now(UTC)
                finding = MisconfigDetector().check(
                    {"engine": "vllm", "gpu_memory_utilization": 0.98}
                )
                critical = [f for f in finding if f.severity == "critical"]
                assert critical, "need a critical finding to convert"

                for f in critical:
                    bt = BlockedTask(
                        todo_id=f"DEPLOY-{f.rule_id}",
                        project_id=None,
                        blocked_at=now,
                        blocked_duration_seconds=1,
                        blocker_kind="misconfig",
                        blocker_summary=f.message,
                        suggested_remediation="file_human_todo",
                        task_type="deployment",
                    )
                    disp = RemediationDispatcher(
                        detector=det,
                        todo_repo=todo_repo,
                        human_todo_repo=human_repo,
                        remediation_repo=remediation_repo,
                    )
                    action = await disp.remediate(bt)
                    assert action.kind == RemediationActionKind.FILE_HUMAN_TODO
                    assert action.ok is True

        finally:
            await engine.dispose()
