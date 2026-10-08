"""Unit tests for EventLoopHandlers mixin from loop_handlers.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop_handlers import EventLoopHandlers


class ConcreteLoop(EventLoopHandlers):
    """Concrete class for testing the EventLoopHandlers mixin."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def handlers():
    """Create a ConcreteLoop with all mocked dependencies set to sensible defaults."""
    return ConcreteLoop(
        _self_improve_interval=10,
        _model_performance_interval=10,
        _total_ticks=0,
        _tick_project_id="test-proj",
        _daemon_state={},
        _tick_metrics={},
        _todo_repo=None,
        _active_session=None,
        _model_gateway=AsyncMock(),
        _session_factory=None,
        _memory_repo=None,
        _config_snapshot={},
        _service_discovery=None,
        _service_discovery_last_run=0.0,
        _issue_ingestor=None,
        _issue_poll_tick_counter=0,
        _issue_poll_interval_ticks=60,
        _ephemeral_account_manager=None,
        _consolidation_tick_counter=0,
        _consolidation_interval_ticks=600,
        _procedural_memory=None,
        _semantic_memory=None,
        _model_perf_repo=None,
        _adaptive_router=None,
        _bounded_to_thread=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
        _resolve_repo_root=MagicMock(return_value="/tmp/repo"),
        _persist_self_improve_todos=AsyncMock(return_value=3),
        config={},
    )


# ---------------------------------------------------------------------------
# _phase_self_improve
# ---------------------------------------------------------------------------


class TestPhaseSelfImprove:
    @pytest.mark.asyncio
    async def test_skips_when_interval_zero(self, handlers):
        handlers._self_improve_interval = 0
        await handlers._phase_self_improve()
        assert handlers._tick_metrics == {}

    @pytest.mark.asyncio
    async def test_skips_when_tick_not_divisible_by_interval(self, handlers):
        handlers._self_improve_interval = 10
        handlers._total_ticks = 7
        await handlers._phase_self_improve()
        assert handlers._tick_metrics == {}

    @pytest.mark.asyncio
    async def test_runs_on_interval_tick(self, handlers):
        handlers._total_ticks = 10
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()

        with patch("general_ludd.event_loop.loop_handlers.SelfImprovementHarness") as mock_sih:
            mock_harness = MagicMock()
            mock_harness.run_gap_analysis = MagicMock(return_value=["gap1", "gap2"])
            mock_harness.generate_fix_todos = MagicMock(return_value=[{"title": "fix1"}])
            mock_sih.return_value = mock_harness

            await handlers._phase_self_improve()

        assert handlers._tick_metrics["self_improve_gaps"] == 2
        assert handlers._tick_metrics["self_improve_todos_persisted"] == 3
        handlers._persist_self_improve_todos.assert_awaited()

    @pytest.mark.asyncio
    async def test_records_zero_gaps_when_no_findings(self, handlers):
        handlers._total_ticks = 10
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()

        with patch("general_ludd.event_loop.loop_handlers.SelfImprovementHarness") as mock_sih:
            mock_harness = MagicMock()
            mock_harness.run_gap_analysis = MagicMock(return_value=[])
            mock_harness.generate_fix_todos = MagicMock(return_value=[])
            mock_sih.return_value = mock_harness

            await handlers._phase_self_improve()

        assert handlers._tick_metrics["self_improve_gaps"] == 0

    @pytest.mark.asyncio
    async def test_handles_self_improve_exception(self, handlers):
        handlers._total_ticks = 10
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()

        with patch("general_ludd.event_loop.loop_handlers.SelfImprovementHarness") as mock_sih:
            mock_harness = MagicMock()
            mock_harness.run_gap_analysis = MagicMock(side_effect=RuntimeError("boom"))
            mock_sih.return_value = mock_harness

            await handlers._phase_self_improve()

        assert handlers._tick_metrics["self_improve_gaps"] == 0


# ---------------------------------------------------------------------------
# _detect_grinding_patterns
# ---------------------------------------------------------------------------


class TestDetectGrindingPatterns:
    def test_returns_list_from_detector(self, handlers):
        with patch("general_ludd.self_update.grinding_detector.detect_and_create_todos") as mock_detect:
            mock_detect.return_value = [{"type": "grind"}]
            result = handlers._detect_grinding_patterns()
            assert result == [{"type": "grind"}]

    def test_returns_empty_list(self, handlers):
        with patch("general_ludd.self_update.grinding_detector.detect_and_create_todos") as mock_detect:
            mock_detect.return_value = []
            result = handlers._detect_grinding_patterns()
            assert result == []


# ---------------------------------------------------------------------------
# _collect_recurring_failures
# ---------------------------------------------------------------------------


class TestCollectRecurringFailures:
    @pytest.mark.asyncio
    async def test_returns_empty_when_todo_repo_none(self, handlers):
        result = await handlers._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_ingest_disabled(self, handlers):
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()
        handlers.config = {"self_improve": {"ingest_recurring_failures": False}}
        result = await handlers._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_config_not_dict(self, handlers):
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()
        handlers.config = None
        result = await handlers._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_ingests_chronic_blockers(self, handlers):
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()
        handlers.config = {}

        blocker_records = [MagicMock(), MagicMock()]

        with patch("general_ludd.remediation.blocker_detector.BlockerDetector") as mock_bd:
            mock_detector = MagicMock()
            mock_detector.chronic_blockers = AsyncMock(return_value=blocker_records)
            mock_bd.return_value = mock_detector

            result = await handlers._collect_recurring_failures()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_handles_import_failure(self, handlers):
        handlers._todo_repo = AsyncMock()
        handlers._active_session = MagicMock()
        handlers.config = {}

        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector",
            side_effect=ImportError("no module"),
        ):
            result = await handlers._collect_recurring_failures()

        assert result == []


# ---------------------------------------------------------------------------
# _collect_training_data_from_returns
# ---------------------------------------------------------------------------


class TestCollectTrainingDataFromReturns:
    @pytest.mark.asyncio
    async def test_returns_zero_when_session_factory_none(self, handlers):
        handlers._session_factory = None
        result = await handlers._collect_training_data_from_returns()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_general_exception(self, handlers):
        handlers._session_factory = AsyncMock(side_effect=RuntimeError("db down"))
        result = await handlers._collect_training_data_from_returns()
        assert result == 0


# ---------------------------------------------------------------------------
# _apply_self_improvements
# ---------------------------------------------------------------------------


class TestApplySelfImprovements:
    @pytest.mark.asyncio
    async def test_skips_when_session_factory_none(self, handlers):
        handlers._session_factory = None
        await handlers._apply_self_improvements()

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, handlers):
        handlers._session_factory = AsyncMock(side_effect=RuntimeError("db gone"))
        await handlers._apply_self_improvements()


# ---------------------------------------------------------------------------
# _phase_refresh_model_performance
# ---------------------------------------------------------------------------


class TestPhaseRefreshModelPerformance:
    @pytest.mark.asyncio
    async def test_skips_when_interval_zero(self, handlers):
        handlers._model_performance_interval = 0
        handlers._total_ticks = 0
        await handlers._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_when_perf_repo_none(self, handlers):
        handlers._total_ticks = 10
        handlers._model_perf_repo = None
        await handlers._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_when_session_factory_none(self, handlers):
        handlers._total_ticks = 10
        handlers._model_perf_repo = MagicMock()
        handlers._session_factory = None
        await handlers._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_refreshes_stats_on_interval(self, handlers):
        handlers._total_ticks = 10
        handlers._model_perf_repo = MagicMock()
        handlers._model_perf_repo.refresh_recent_stats = AsyncMock(return_value=5)
        handlers._session_factory = MagicMock()
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        handlers._session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        handlers._session_factory.return_value.__aexit__ = AsyncMock()

        await handlers._phase_refresh_model_performance()

        handlers._model_perf_repo.refresh_recent_stats.assert_awaited()

    @pytest.mark.asyncio
    async def test_handles_refresh_exception(self, handlers):
        handlers._total_ticks = 10
        handlers._model_perf_repo = MagicMock()
        handlers._model_perf_repo.refresh_recent_stats = AsyncMock(side_effect=RuntimeError("dead"))
        handlers._session_factory = MagicMock()
        mock_session = AsyncMock()
        handlers._session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        handlers._session_factory.return_value.__aexit__ = AsyncMock()

        await handlers._phase_refresh_model_performance()


# ---------------------------------------------------------------------------
# _phase_poll_issue_sources
# ---------------------------------------------------------------------------


class TestPhasePollIssueSources:
    @pytest.mark.asyncio
    async def test_skips_when_issue_ingestor_none(self, handlers):
        handlers._issue_ingestor = None
        await handlers._phase_poll_issue_sources()

    @pytest.mark.asyncio
    async def test_skips_before_interval(self, handlers):
        handlers._issue_ingestor = MagicMock()
        handlers._issue_poll_tick_counter = 0
        handlers._issue_poll_interval_ticks = 5

        await handlers._phase_poll_issue_sources()

        assert handlers._issue_poll_tick_counter == 1
        handlers._issue_ingestor.poll_issues.assert_not_called()

    @pytest.mark.asyncio
    async def test_polls_when_counter_reaches_interval(self, handlers):
        handlers._issue_ingestor = MagicMock()
        handlers._issue_ingestor.poll_issues = AsyncMock(return_value=[])
        handlers._issue_poll_tick_counter = 5
        handlers._issue_poll_interval_ticks = 5
        handlers._todo_repo = None

        await handlers._phase_poll_issue_sources()

        assert handlers._issue_poll_tick_counter == 0
        handlers._issue_ingestor.poll_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persists_polled_todos(self, handlers):
        handlers._issue_ingestor = MagicMock()
        handlers._todo_repo = AsyncMock()
        todo = {"title": "new issue", "priority": "high"}
        handlers._issue_ingestor.poll_issues = AsyncMock(return_value=[todo])
        handlers._issue_poll_tick_counter = 5
        handlers._issue_poll_interval_ticks = 5

        await handlers._phase_poll_issue_sources()

        handlers._todo_repo.create.assert_awaited_once_with(todo)
        assert handlers._tick_metrics["issues_polled"] == 1

    @pytest.mark.asyncio
    async def test_handles_ingestor_exception(self, handlers):
        handlers._issue_ingestor = MagicMock()
        handlers._issue_ingestor.poll_issues = AsyncMock(side_effect=RuntimeError("boom"))
        handlers._issue_poll_tick_counter = 5
        handlers._issue_poll_interval_ticks = 5

        await handlers._phase_poll_issue_sources()

        assert handlers._issue_poll_tick_counter == 0


# ---------------------------------------------------------------------------
# _phase_service_discovery
# ---------------------------------------------------------------------------


class TestPhaseServiceDiscovery:
    @pytest.mark.asyncio
    async def test_skips_when_discovery_none(self, handlers):
        handlers._service_discovery = None
        await handlers._phase_service_discovery()

    @pytest.mark.asyncio
    async def test_skips_when_disabled_in_config(self, handlers):
        handlers._service_discovery = MagicMock()
        handlers.config = {"service_discovery_enabled": False}
        await handlers._phase_service_discovery()

    @pytest.mark.asyncio
    async def test_skips_within_cooldown(self, handlers):
        import time

        handlers._service_discovery = MagicMock()
        handlers._service_discovery_last_run = time.monotonic() + 99999
        handlers.config = {}

        await handlers._phase_service_discovery()

    @pytest.mark.asyncio
    async def test_runs_discovery_and_logs_report(self, handlers):
        handlers._service_discovery = MagicMock()
        handlers._service_discovery.run_discovery_pipeline = MagicMock(
            return_value=MagicMock(
                new_services=["a"],
                changed_services=["b"],
                retired_services=[],
                total_discovered=3,
                errors=[],
            )
        )
        handlers.config = {"service_discovery_interval_seconds": 0}
        handlers._service_discovery_last_run = 0.0

        await handlers._phase_service_discovery()

        handlers._bounded_to_thread.assert_awaited()


# ---------------------------------------------------------------------------
# _phase_reap_expired_sts_tokens
# ---------------------------------------------------------------------------


class TestPhaseReapExpiredStsTokens:
    @pytest.mark.asyncio
    async def test_skips_when_daemon_state_none(self, handlers):
        handlers._daemon_state = None
        await handlers._phase_reap_expired_sts_tokens()

    @pytest.mark.asyncio
    async def test_skips_when_reaper_none(self, handlers):
        handlers._daemon_state = {}
        await handlers._phase_reap_expired_sts_tokens()

    @pytest.mark.asyncio
    async def test_reaps_expired_tokens_on_interval(self, handlers):
        mock_reaper = MagicMock()
        mock_reaper.reap_expired = AsyncMock(return_value=3)
        handlers._daemon_state = {"_sts_reaper": mock_reaper}
        handlers._total_ticks = 60
        handlers.config = {"sts_reap_interval_ticks": 60}

        await handlers._phase_reap_expired_sts_tokens()

        assert handlers._tick_metrics["sts_tokens_reaped"] == 3

    @pytest.mark.asyncio
    async def test_handles_reap_exception(self, handlers):
        mock_reaper = MagicMock()
        mock_reaper.reap_expired = AsyncMock(side_effect=RuntimeError("dead"))
        handlers._daemon_state = {"_sts_reaper": mock_reaper}
        handlers._total_ticks = 60
        handlers.config = {"sts_reap_interval_ticks": 60}

        await handlers._phase_reap_expired_sts_tokens()


# ---------------------------------------------------------------------------
# _phase_purge_old_task_decisions
# ---------------------------------------------------------------------------


class TestPhasePurgeOldTaskDecisions:
    @pytest.mark.asyncio
    async def test_skips_when_active_session_none(self, handlers):
        handlers._active_session = None
        await handlers._phase_purge_old_task_decisions()

    @pytest.mark.asyncio
    async def test_purges_on_interval(self, handlers):
        handlers._active_session = MagicMock()
        handlers._total_ticks = 3600
        handlers.config = {"task_decisions_retention_interval_ticks": 3600}

        with patch("general_ludd.db.task_decisions_retention.cleanup_old_task_decisions") as mock_cleanup:
            mock_cleanup.return_value = 42
            await handlers._phase_purge_old_task_decisions()

        assert handlers._tick_metrics["task_decisions_purged"] == 42


# ---------------------------------------------------------------------------
# _phase_emit_tick_metrics
# ---------------------------------------------------------------------------


class TestPhaseEmitTickMetrics:
    @pytest.mark.asyncio
    async def test_logs_tick_metrics(self, handlers):
        handlers._tick_metrics = {"foo": 1}
        with patch("general_ludd.event_loop.loop_handlers.logger") as mock_logger:
            await handlers._phase_emit_tick_metrics()
            mock_logger.info.assert_called_once()


# ---------------------------------------------------------------------------
# _maybe_cleanup_ephemeral
# ---------------------------------------------------------------------------


class TestMaybeCleanupEphemeral:
    @pytest.mark.asyncio
    async def test_skips_when_manager_none(self, handlers):
        handlers._ephemeral_account_manager = None
        await handlers._maybe_cleanup_ephemeral(MagicMock())

    @pytest.mark.asyncio
    async def test_cleans_up_ephemeral_account(self, handlers):
        handlers._ephemeral_account_manager = MagicMock()

        todo = MagicMock(tags={"ephemeral_account_id": "ep-1"}, todo_id="T1")

        with patch("general_ludd.account.ephemeral.maybe_delete_ephemeral_after_task") as mock_maybe:
            mock_maybe.return_value = {"deleted": True, "account_id": "ep-1"}
            await handlers._maybe_cleanup_ephemeral(todo)

            mock_maybe.assert_called_once_with(
                manager=handlers._ephemeral_account_manager,
                metadata={"ephemeral_account_id": "ep-1"},
            )

    @pytest.mark.asyncio
    async def test_handles_none_result(self, handlers):
        handlers._ephemeral_account_manager = MagicMock()
        todo = MagicMock(tags={}, todo_id="T2")

        with patch("general_ludd.account.ephemeral.maybe_delete_ephemeral_after_task") as mock_maybe:
            mock_maybe.return_value = None
            await handlers._maybe_cleanup_ephemeral(todo)


# ---------------------------------------------------------------------------
# _auto_record_episode
# ---------------------------------------------------------------------------


class TestAutoRecordEpisode:
    @pytest.mark.asyncio
    async def test_skips_when_memory_repo_none(self, handlers):
        handlers._memory_repo = None
        await handlers._auto_record_episode(MagicMock(), MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_records_completed_episode(self, handlers):
        handlers._memory_repo = MagicMock()

        todo = MagicMock(
            assigned_agent="worker-1",
            work_type="code",
            todo_id="T1",
            title="fix bug",
            project_id="proj-1",
            priority="high",
            task_type="fix",
        )
        new_status = MagicMock()
        new_status.value = "COMPLETE"
        decision = MagicMock(decision="complete", summary="done well")

        with patch("general_ludd.memory.episodic.EpisodicMemoryRecorder") as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder

            await handlers._auto_record_episode(todo, new_status, decision)

            mock_recorder.record_completion.assert_awaited_once()
            call_kwargs = mock_recorder.record_completion.call_args
            assert call_kwargs[1]["outcome"] == "success"
            assert handlers._tick_metrics["episodes_recorded"] == 1

    @pytest.mark.asyncio
    async def test_records_failed_episode(self, handlers):
        handlers._memory_repo = MagicMock()

        todo = MagicMock(
            assigned_agent=None,
            work_type=None,
            todo_id="T2",
            title=None,
            project_id=None,
            priority="low",
            task_type="bug",
            last_error="something broke",
        )
        new_status = MagicMock()
        new_status.value = "FAILED"
        decision = MagicMock(decision="fail", summary=None, failure_reason="timeout")

        with patch("general_ludd.memory.episodic.EpisodicMemoryRecorder") as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder

            await handlers._auto_record_episode(todo, new_status, decision)

            call_kwargs = mock_recorder.record_completion.call_args
            assert call_kwargs[1]["outcome"] == "failure"
            assert call_kwargs[1]["error_message"] == "timeout"

    @pytest.mark.asyncio
    async def test_handles_recorder_exception(self, handlers):
        handlers._memory_repo = MagicMock()

        todo = MagicMock(work_type="code", todo_id="T3")
        new_status = MagicMock()
        new_status.value = "UNKNOWN"
        decision = MagicMock()

        with patch("general_ludd.memory.episodic.EpisodicMemoryRecorder") as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock(side_effect=RuntimeError("bug"))
            mock_recorder_cls.return_value = mock_recorder

            await handlers._auto_record_episode(todo, new_status, decision)


# ---------------------------------------------------------------------------
# _auto_consolidate_memory
# ---------------------------------------------------------------------------


class TestAutoConsolidateMemory:
    @pytest.mark.asyncio
    async def test_skips_when_memory_repo_none(self, handlers):
        handlers._memory_repo = None
        await handlers._auto_consolidate_memory()

    @pytest.mark.asyncio
    async def test_consolidates_and_updates_metrics(self, handlers):
        handlers._memory_repo = MagicMock()

        with patch("general_ludd.memory.consolidation.MemoryConsolidator") as mock_mc:
            mock_consolidator = MagicMock()
            mock_consolidator.consolidate = AsyncMock(return_value={"consolidated": 3, "episodes_consolidated": 12})
            mock_mc.return_value = mock_consolidator

            await handlers._auto_consolidate_memory()

        assert handlers._tick_metrics["memory_consolidated"] == 3


# ---------------------------------------------------------------------------
# _auto_cross_task_learn
# ---------------------------------------------------------------------------


class TestAutoCrossTaskLearn:
    @pytest.mark.asyncio
    async def test_skips_when_memory_repo_none(self, handlers):
        handlers._memory_repo = None
        await handlers._auto_cross_task_learn()

    @pytest.mark.asyncio
    async def test_learns_and_persists_improvements(self, handlers):
        handlers._memory_repo = MagicMock()
        handlers._tick_project_id = "proj-1"

        with patch("general_ludd.memory.cross_task.CrossTaskLearner") as mock_ctl:
            mock_learner = MagicMock()
            mock_learner.generate_improvement_report = AsyncMock(
                return_value={
                    "improvements_needed": [
                        {"suggested_action": "fix antiloop"},
                    ],
                    "total_episodes": 20,
                }
            )
            mock_ctl.return_value = mock_learner

            await handlers._auto_cross_task_learn()

        assert handlers._tick_metrics["cross_task_improvements"] == 1
        handlers._persist_self_improve_todos.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_persist_when_no_improvements(self, handlers):
        handlers._memory_repo = MagicMock()

        with patch("general_ludd.memory.cross_task.CrossTaskLearner") as mock_ctl:
            mock_learner = MagicMock()
            mock_learner.generate_improvement_report = AsyncMock(
                return_value={"improvements_needed": [], "total_episodes": 5}
            )
            mock_ctl.return_value = mock_learner

            await handlers._auto_cross_task_learn()

        assert "cross_task_improvements" not in handlers._tick_metrics


# ---------------------------------------------------------------------------
# _phase_consolidate_memory
# ---------------------------------------------------------------------------


class TestPhaseConsolidateMemory:
    @pytest.mark.asyncio
    async def test_skips_before_interval(self, handlers):
        handlers._consolidation_interval_ticks = 600
        handlers._consolidation_tick_counter = 0

        await handlers._phase_consolidate_memory()

        assert handlers._consolidation_tick_counter == 1

    @pytest.mark.asyncio
    async def test_skips_when_memory_repo_none(self, handlers):
        handlers._consolidation_interval_ticks = 1
        handlers._consolidation_tick_counter = 1
        handlers._memory_repo = None

        await handlers._phase_consolidate_memory()

        assert handlers._consolidation_tick_counter == 0

    @pytest.mark.asyncio
    async def test_consolidates_procedural_and_semantic(self, handlers):
        handlers._consolidation_interval_ticks = 1
        handlers._consolidation_tick_counter = 1
        handlers._memory_repo = MagicMock()
        handlers._procedural_memory = None
        handlers._semantic_memory = None

        with (
            patch("general_ludd.memory.procedural.ProceduralMemoryStore") as mock_proc_cls,
            patch("general_ludd.memory.semantic.SemanticMemoryStore") as mock_sem_cls,
            patch("general_ludd.memory.consolidation.MemoryConsolidator"),
        ):
            mock_proc = MagicMock()
            mock_proc.consolidate_from_episodes = AsyncMock(return_value=4)
            mock_proc_cls.return_value = mock_proc

            mock_sem = MagicMock()
            mock_sem.consolidate_from_consolidated = AsyncMock(return_value=2)
            mock_sem_cls.return_value = mock_sem

            await handlers._phase_consolidate_memory()

        assert handlers._tick_metrics["memory_consolidated_procedures"] == 4
        assert handlers._tick_metrics["memory_consolidated_facts"] == 2

    @pytest.mark.asyncio
    async def test_handles_procedural_consolidation_exception(self, handlers):
        handlers._consolidation_interval_ticks = 1
        handlers._consolidation_tick_counter = 1
        handlers._memory_repo = MagicMock()

        with (
            patch("general_ludd.memory.procedural.ProceduralMemoryStore") as mock_proc_cls,
            patch("general_ludd.memory.semantic.SemanticMemoryStore") as mock_sem_cls,
            patch("general_ludd.memory.consolidation.MemoryConsolidator"),
        ):
            mock_proc = MagicMock()
            mock_proc.consolidate_from_episodes = AsyncMock(side_effect=RuntimeError("bad proc"))
            mock_proc_cls.return_value = mock_proc

            mock_sem = MagicMock()
            mock_sem.consolidate_from_consolidated = AsyncMock(return_value=1)
            mock_sem_cls.return_value = mock_sem

            await handlers._phase_consolidate_memory()

        assert handlers._tick_metrics["memory_consolidated_facts"] == 1
