"""Tests for the auto-remediation tick phase wiring (#52).

Covers ``EventLoop._phase_remediate_blocked_tasks`` (loop.py) and the
daemon-side single config-source fix (``daemon.py``'s
``_remediation_config_from_uc`` + ``routers/remediation.py``'s
``_get_remediation_config``):

  - The phase is gated by ``remediation_check_interval_ticks`` (default 30)
    and NEVER constructs a ``BlockerDetector`` off-interval.
  - ``remediation_check_interval_ticks: 0`` is a hard kill switch regardless
    of the current tick count.
  - At most ``remediation_max_actions_per_tick`` findings are dispatched per
    tick (a large blocked backlog is drained gradually).
  - Idempotency: a finding already acted on within the configured
    ``retry_delay_hours`` cooldown is skipped via
    ``RemediationActionRepository.exists_recent`` (real SQLite, end to end).
  - Dead-wiring fix: ``UserConfig.remediation`` overrides reach the
    ``RemediationConfig`` that ``GET /admin/remediation/config`` returns.

See also ``tests/unit/test_event_loop.py`` and
``tests/e2e/test_obj04_event_loop.py`` for the PHASE_ORDER pins (updated
alongside this file to include ``remediate_blocked_tasks``), and
``tests/unit/test_routers_remediation_endpoints.py`` for the router's own
direct-injection config test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.remediation.blocker_detector import BlockedTask


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.add = MagicMock()
    todo_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={
            "remediation_check_interval_ticks": 30,
            "remediation_max_actions_per_tick": 5,
        },
        session=session,
        todo_repo=todo_repo,
        daemon_state={},
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {"session": session, "todo_repo": todo_repo}


class TestRemediationPhaseInterval:
    @pytest.mark.asyncio
    async def test_off_interval_never_constructs_detector(self):
        """1 tick % 30 != 0 -> the phase must return before touching the DB."""
        loop, _mocks = _make_loop(
            config={"remediation_check_interval_ticks": 30}
        )
        loop._total_ticks = 1
        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector"
        ) as mock_detector_cls:
            await loop._phase_remediate_blocked_tasks()
        mock_detector_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_interval_zero_is_kill_switch(self):
        """``remediation_check_interval_ticks: 0`` disables the phase outright,
        even on a tick count that would match a nonzero interval."""
        loop, _mocks = _make_loop(
            config={"remediation_check_interval_ticks": 0}
        )
        loop._total_ticks = 30
        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector"
        ) as mock_detector_cls:
            await loop._phase_remediate_blocked_tasks()
        mock_detector_cls.assert_not_called()


class TestRemediationPhaseActionCap:
    @pytest.mark.asyncio
    async def test_cap_limits_actions_per_tick(self):
        """8 findings, cap=5 -> only 5 are dispatched (and only 5 cooldown
        checks are made — the cap is enforced BEFORE the exists_recent
        lookup for findings beyond the cap)."""
        loop, _mocks = _make_loop(
            config={
                "remediation_check_interval_ticks": 1,
                "remediation_max_actions_per_tick": 5,
            }
        )
        loop._total_ticks = 1
        findings = [
            BlockedTask(
                todo_id=f"TODO-{i}",
                project_id=None,
                blocked_at=datetime.now(UTC),
                blocked_duration_seconds=100,
                blocker_kind="human_input",
                blocker_summary="blocked",
                suggested_remediation="file_human_todo",
            )
            for i in range(8)
        ]
        with (
            patch(
                "general_ludd.remediation.blocker_detector.BlockerDetector"
            ) as mock_det_cls,
            patch(
                "general_ludd.remediation.dispatcher.RemediationDispatcher"
            ) as mock_disp_cls,
            patch("general_ludd.db.repository.HumanTodoRepository"),
            patch(
                "general_ludd.db.repository.RemediationActionRepository"
            ) as mock_repo_cls,
        ):
            mock_det_cls.return_value.scan = AsyncMock(return_value=findings)
            mock_repo_cls.return_value.exists_recent = AsyncMock(
                return_value=False
            )
            mock_disp_cls.return_value.remediate = AsyncMock()
            await loop._phase_remediate_blocked_tasks()

        assert mock_disp_cls.return_value.remediate.await_count == 5
        assert mock_repo_cls.return_value.exists_recent.await_count == 5


class TestRemediationPhaseIdempotency:
    @pytest.mark.asyncio
    async def test_second_qualifying_tick_suppresses_duplicate_action(self):
        """Real SQLite, two ticks: the second tick's scan re-finds the SAME
        still-blocked (chronically re-queued) task, but ``exists_recent``
        suppresses a second dispatch — only one audit row / one child todo
        exists after both ticks."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import TodoModel
        from general_ludd.db.repository import (
            RemediationActionRepository,
            TodoRepository,
        )
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )
        from general_ludd.schemas.todo import TodoStatus

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)
            async with factory() as session:
                todo_repo = TodoRepository(session)
                await todo_repo.create(
                    {
                        "todo_id": "TODO-CHRONIC-1",
                        "title": "chronically re-queued task",
                        "status": TodoStatus.BLOCKED.value,
                        "work_type": "code",
                        "queue": "core",
                        "run_count": 5,
                    }
                )

                loop = EventLoop(
                    session=session,
                    todo_repo=todo_repo,
                    daemon_state={},
                    config={
                        "remediation_check_interval_ticks": 1,
                        "remediation_max_actions_per_tick": 5,
                    },
                )

                loop._total_ticks = 1
                await loop._phase_remediate_blocked_tasks()

                loop._total_ticks = 2
                await loop._phase_remediate_blocked_tasks()

                remediation_repo = RemediationActionRepository(session)
                rows = await remediation_repo.list_for_project(project_id=None)
                dup_rows = [
                    r for r in rows if r.blocked_todo_id == "TODO-CHRONIC-1"
                ]
                assert len(dup_rows) == 1, (
                    "second qualifying tick must be suppressed by "
                    "exists_recent (retry_delay_hours cooldown); got "
                    f"{len(dup_rows)} action rows"
                )

                child_todos = (
                    (
                        await session.execute(
                            select(TodoModel).where(
                                TodoModel.parent_todo_id == "TODO-CHRONIC-1"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(child_todos) == 1
        finally:
            await engine.dispose()


class TestRemediationConfigDeadWiringFix:
    """Proves UserConfig.remediation overrides reach the HTTP endpoint.

    Previously ``daemon_state["remediation_config"]`` was never populated at
    all (``load_startup_config``'s ``startup_config["remediation_config"]``
    was hardcoded ``None`` and nothing copied it — or anything else — into
    ``daemon_state``), so ``GET /admin/remediation/config`` always returned
    hardcoded defaults regardless of operator config. This drives
    ``daemon._remediation_config_from_uc`` (the fix) with a real
    ``UserConfig`` override and asserts the HTTP response reflects it.
    """

    def test_operator_overrides_reach_the_config_endpoint(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd import daemon as daemon_module
        from general_ludd.config.user_config import RemediationSettings, UserConfig
        from general_ludd.routers.remediation import register

        uc = UserConfig(
            remediation=RemediationSettings(
                human_input_block_hours=2,
                max_requeues_before_chronic=9,
                retry_delay_hours=1,
            )
        )
        cfg = daemon_module._remediation_config_from_uc(uc)
        daemon_state: dict[str, object] = {"remediation_config": cfg}

        app = FastAPI()
        register(app, daemon_state)
        client = TestClient(app)
        resp = client.get("/admin/remediation/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["human_input_block_hours"] == 2
        assert data["max_requeues_before_chronic"] == 9
        assert data["retry_delay_hours"] == 1
        # Untouched fields keep RemediationConfig's defaults.
        assert data["permission_escalation_block_hours"] == 4

    def test_no_user_config_falls_back_to_defaults(self):
        from general_ludd import daemon as daemon_module
        from general_ludd.remediation.blocker_detector import RemediationConfig

        cfg = daemon_module._remediation_config_from_uc(None)
        assert cfg == RemediationConfig()
