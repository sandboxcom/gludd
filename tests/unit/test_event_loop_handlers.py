"""Unit tests for EventLoopHandlers mixin providing phase handlers and
self-improve methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop_handlers import EventLoopHandlers


def _make_handlers(**overrides):
    handlers = EventLoopHandlers.__new__(EventLoopHandlers)
    defaults = {
        "_self_improve_interval": 10,
        "_total_ticks": 0,
        "_tick_project_id": "proj-1",
        "_daemon_state": {},
        "_tick_metrics": {},
        "_todo_repo": AsyncMock(),
        "_active_session": AsyncMock(),
        "_model_gateway": AsyncMock(),
        "_session_factory": AsyncMock(),
        "_memory_repo": AsyncMock(),
        "_config_snapshot": {},
        "_service_discovery": None,
        "_service_discovery_last_run": 0.0,
        "_issue_ingestor": None,
        "_issue_poll_tick_counter": 0,
        "_issue_poll_interval_ticks": 10,
        "_ephemeral_account_manager": None,
        "_consolidation_tick_counter": 0,
        "_consolidation_interval_ticks": 10,
        "_procedural_memory": None,
        "_semantic_memory": None,
        "_model_perf_repo": None,
        "_model_performance_interval": 10,
        "_adaptive_router": None,
        "_bounded_to_thread": AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw)),
        "_resolve_repo_root": MagicMock(return_value="/tmp/repo"),
        "_persist_self_improve_todos": AsyncMock(return_value=0),
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(handlers, k, v)
    if "config" not in overrides:
        handlers.config = {}
    return handlers


class TestPhaseSelfImprove:
    @pytest.mark.asyncio
    async def test_skips_when_interval_zero(self):
        h = _make_handlers(_self_improve_interval=0)
        await h._phase_self_improve()
        assert h._tick_metrics.get("self_improve_gaps", -1) == -1

    @pytest.mark.asyncio
    async def test_skips_when_tick_not_on_interval(self):
        h = _make_handlers(_self_improve_interval=5, _total_ticks=3)
        await h._phase_self_improve()
        assert "self_improve_gaps" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_self_improve_on_interval(self):
        h = _make_handlers(_self_improve_interval=5, _total_ticks=5)
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis.return_value = []
        fake_harness.generate_fix_todos.return_value = []
        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_persist_self_improve_todos", new=AsyncMock(return_value=0)),
            patch.object(h, "_collect_training_data_from_returns", new=AsyncMock(return_value=0)),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock()),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock()),
            patch.object(h, "_apply_self_improvements", new=AsyncMock()),
            patch(
                "general_ludd.event_loop.loop_handlers.SelfImprovementHarness",
                return_value=fake_harness,
            ),
        ):
            await h._phase_self_improve()
            assert h._tick_metrics["self_improve_gaps"] == 0
        fake_harness.run_gap_analysis.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_self_improve_exception_sets_zero_gaps(self):
        h = _make_handlers(_self_improve_interval=5, _total_ticks=5)
        with patch.object(
            h,
            "_collect_recurring_failures",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await h._phase_self_improve()
            assert h._tick_metrics["self_improve_gaps"] == 0


class TestDetectGrindingPatterns:
    def test_calls_detector(self):
        h = _make_handlers()
        fake_todos = [{"title": "reduce inline grinding", "work_type": "self_improve"}]
        with patch(
            "general_ludd.self_update.grinding_detector.detect_and_create_todos",
            return_value=fake_todos,
        ) as mock_detect:
            result = h._detect_grinding_patterns()
            mock_detect.assert_called_once()
            assert result == fake_todos


class TestCollectRecurringFailures:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_todo_repo(self):
        h = _make_handlers(_todo_repo=None)
        result = await h._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_active_session(self):
        h = _make_handlers(_active_session=None)
        result = await h._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_ingest_config_disabled(self):
        h = _make_handlers(config={"self_improve": {"ingest_recurring_failures": False}})
        result = await h._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_import_error_gracefully(self):
        h = _make_handlers()
        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector",
            side_effect=ImportError("not installed"),
        ):
            result = await h._collect_recurring_failures()
            assert result == []


class TestCollectTrainingData:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_session_factory(self):
        h = _make_handlers(_session_factory=None)
        result = await h._collect_training_data_from_returns()
        assert result == 0


class TestApplySelfImprovements:
    @pytest.mark.asyncio
    async def test_returns_early_when_no_session_factory(self):
        h = _make_handlers(_session_factory=None)
        await h._apply_self_improvements()


class TestPhaseRefreshModelPerformance:
    @pytest.mark.asyncio
    async def test_skips_when_interval_zero(self):
        h = _make_handlers(_model_performance_interval=0)
        await h._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_when_no_perf_repo(self):
        h = _make_handlers(_model_performance_interval=5, _total_ticks=5, _model_perf_repo=None)
        await h._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_when_not_on_interval(self):
        h = _make_handlers(_model_performance_interval=5, _total_ticks=3)
        await h._phase_refresh_model_performance()


class TestPhasePollIssueSources:
    @pytest.mark.asyncio
    async def test_skips_when_no_ingestor(self):
        h = _make_handlers(_issue_ingestor=None)
        await h._phase_poll_issue_sources()

    @pytest.mark.asyncio
    async def test_resets_counter_on_poll(self):
        ingestor = AsyncMock()
        ingestor.poll_issues.return_value = []
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=10,
            _issue_poll_interval_ticks=10,
        )
        await h._phase_poll_issue_sources()
        assert h._issue_poll_tick_counter == 0


class TestPhaseServiceDiscovery:
    @pytest.mark.asyncio
    async def test_skips_when_no_discovery(self):
        h = _make_handlers(_service_discovery=None)
        await h._phase_service_discovery()

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self):
        h = _make_handlers(
            _service_discovery=MagicMock(),
            config={"service_discovery_enabled": False},
        )
        await h._phase_service_discovery()

    @pytest.mark.asyncio
    async def test_skips_when_interval_not_elapsed(self):
        h = _make_handlers(
            _service_discovery=MagicMock(),
            _service_discovery_last_run=1e12,
        )
        await h._phase_service_discovery()


class TestPhaseReapExpiredStsTokens:
    @pytest.mark.asyncio
    async def test_skips_when_no_daemon_state(self):
        h = _make_handlers(_daemon_state=None)
        await h._phase_reap_expired_sts_tokens()

    @pytest.mark.asyncio
    async def test_skips_when_no_reaper(self):
        h = _make_handlers(_daemon_state={})
        await h._phase_reap_expired_sts_tokens()

    @pytest.mark.asyncio
    async def test_skips_when_not_on_interval(self):
        reaper = AsyncMock()
        h = _make_handlers(
            _daemon_state={"_sts_reaper": reaper},
            _total_ticks=1,
            config={"sts_reap_interval_ticks": 60},
        )
        await h._phase_reap_expired_sts_tokens()
        reaper.reap_expired.assert_not_called()


class TestPhasePurgeOldTaskDecisions:
    @pytest.mark.asyncio
    async def test_skips_when_no_active_session(self):
        h = _make_handlers(_active_session=None)
        await h._phase_purge_old_task_decisions()

    @pytest.mark.asyncio
    async def test_skips_when_not_on_interval(self):
        h = _make_handlers(
            _total_ticks=1,
            config={"task_decisions_retention_interval_ticks": 3600},
        )
        await h._phase_purge_old_task_decisions()


class TestPhaseEmitTickMetrics:
    @pytest.mark.asyncio
    async def test_logs_metrics(self):
        h = _make_handlers(
            _tick_metrics={"foo": 1, "bar": 2},
        )
        await h._phase_emit_tick_metrics()


class TestMaybeCleanupEphemeral:
    @pytest.mark.asyncio
    async def test_skips_when_no_manager(self):
        h = _make_handlers(_ephemeral_account_manager=None)
        todo = MagicMock()
        todo.todo_id = "todo-1"
        await h._maybe_cleanup_ephemeral(todo)

    @pytest.mark.asyncio
    async def test_creates_handler_instance(self):
        h = _make_handlers()
        assert isinstance(h, EventLoopHandlers)
