"""Deep unit tests for EventLoopHandlers — logic paths beyond guard clauses."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from general_ludd.event_loop.loop_handlers import EventLoopHandlers


def _make_handlers(**overrides):
    handlers = EventLoopHandlers.__new__(EventLoopHandlers)
    defaults = {
        "_self_improve_interval": 10,
        "_total_ticks": 10,
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
        "_consolidation_tick_counter": 10,
        "_consolidation_interval_ticks": 10,
        "_procedural_memory": None,
        "_semantic_memory": None,
        "_model_perf_repo": None,
        "_model_performance_interval": 10,
        "_adaptive_router": None,
        "_bounded_to_thread": AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw)),
        "_resolve_repo_root": MagicMock(return_value="/tmp/repo"),
        "_persist_self_improve_todos": AsyncMock(return_value=3),
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(handlers, k, v)
    if "config" not in overrides:
        handlers.config = {}
    return handlers


# ── _phase_self_improve deep paths ──────────────────────────────────────


class TestPhaseSelfImproveDeep:
    @pytest.mark.asyncio
    async def test_persists_findings_and_grinding_todos(self):
        h = _make_handlers(_self_improve_interval=2, _total_ticks=2)
        findings = [{"gap": "missing_type"}, {"gap": "no_test"}]
        grinding = [{"title": "reduce grinding"}]
        fake_harness = MagicMock()
        fake_harness.generate_fix_todos.return_value = [{"title": "fix types"}]
        fake_harness.run_gap_analysis = MagicMock(return_value=findings)

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=grinding),
            patch.object(h, "_collect_training_data_from_returns", new=AsyncMock(return_value=5)),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock()),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock()),
            patch.object(h, "_apply_self_improvements", new=AsyncMock()),
            patch(
                "general_ludd.event_loop.loop_handlers.SelfImprovementHarness",
                return_value=fake_harness,
            ),
        ):
            await h._phase_self_improve()

        assert h._tick_metrics["self_improve_gaps"] == 2
        assert h._tick_metrics["self_improve_todos_persisted"] == 3
        assert h._tick_metrics["self_improve_training_recorded"] == 5
        assert h._daemon_state["self_improve_last_analysis"]["findings_count"] == 2
        assert h._daemon_state["self_improve_last_analysis"]["grinding_todos"] == 1
        assert h._daemon_state["self_improve_last_analysis"]["todos_enqueued"] == 3

    @pytest.mark.asyncio
    async def test_sets_zero_gaps_when_no_findings(self):
        h = _make_handlers(_self_improve_interval=3, _total_ticks=3)
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis = MagicMock(return_value=[])
        fake_harness.generate_fix_todos.return_value = []

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
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

    @pytest.mark.asyncio
    async def test_handles_training_data_failure_gracefully(self):
        h = _make_handlers(_self_improve_interval=2, _total_ticks=2)

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
            patch.object(
                h,
                "_collect_training_data_from_returns",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock()),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock()),
            patch.object(h, "_apply_self_improvements", new=AsyncMock()),
        ):
            await h._phase_self_improve()

        assert h._tick_metrics["self_improve_training_recorded"] == 0

    @pytest.mark.asyncio
    async def test_handles_consolidation_failure_gracefully(self):
        h = _make_handlers(_self_improve_interval=2, _total_ticks=2)
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis = MagicMock(return_value=[])

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
            patch.object(h, "_collect_training_data_from_returns", new=AsyncMock(return_value=0)),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock(side_effect=OSError("oom"))),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock()),
            patch.object(h, "_apply_self_improvements", new=AsyncMock()),
            patch(
                "general_ludd.event_loop.loop_handlers.SelfImprovementHarness",
                return_value=fake_harness,
            ),
        ):
            await h._phase_self_improve()

        assert h._tick_metrics["self_improve_gaps"] == 0

    @pytest.mark.asyncio
    async def test_handles_cross_task_learn_failure_gracefully(self):
        h = _make_handlers(_self_improve_interval=2, _total_ticks=2)
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis = MagicMock(return_value=[])

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
            patch.object(h, "_collect_training_data_from_returns", new=AsyncMock(return_value=0)),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock()),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock(side_effect=ValueError("bad config"))),
            patch.object(h, "_apply_self_improvements", new=AsyncMock()),
            patch(
                "general_ludd.event_loop.loop_handlers.SelfImprovementHarness",
                return_value=fake_harness,
            ),
        ):
            await h._phase_self_improve()

        assert h._tick_metrics["self_improve_gaps"] == 0

    @pytest.mark.asyncio
    async def test_handles_apply_improvements_failure_gracefully(self):
        h = _make_handlers(_self_improve_interval=2, _total_ticks=2)
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis = MagicMock(return_value=[])

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
            patch.object(h, "_collect_training_data_from_returns", new=AsyncMock(return_value=0)),
            patch.object(h, "_auto_consolidate_memory", new=AsyncMock()),
            patch.object(h, "_auto_cross_task_learn", new=AsyncMock()),
            patch.object(h, "_apply_self_improvements", new=AsyncMock(side_effect=KeyError("missing"))),
            patch(
                "general_ludd.event_loop.loop_handlers.SelfImprovementHarness",
                return_value=fake_harness,
            ),
        ):
            await h._phase_self_improve()

        assert h._tick_metrics["self_improve_gaps"] == 0

    @pytest.mark.asyncio
    async def test_updates_daemon_state_with_analysis(self):
        h = _make_handlers(
            _self_improve_interval=1,
            _total_ticks=1,
            _daemon_state={"existing": "value"},
        )
        findings = [{"gap": "x"}]
        fake_harness = MagicMock()
        fake_harness.run_gap_analysis = MagicMock(return_value=findings)
        fake_harness.generate_fix_todos.return_value = [{"title": "y"}]

        with (
            patch.object(h, "_collect_recurring_failures", new=AsyncMock(return_value=[])),
            patch.object(h, "_detect_grinding_patterns", return_value=[]),
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

        assert h._daemon_state["existing"] == "value"
        analysis = h._daemon_state["self_improve_last_analysis"]
        assert analysis["findings"] == findings
        assert analysis["findings_count"] == 1
        assert analysis["todos_enqueued"] == 3


# ── _collect_recurring_failures deep paths ──────────────────────────────


class TestCollectRecurringFailuresDeep:
    @pytest.mark.asyncio
    async def test_passes_chronic_config_kwargs(self):
        h = _make_handlers(
            config={
                "self_improve": {
                    "ingest_recurring_failures": True,
                    "chronic_lookback_days": 30,
                    "min_chronic_incidents": 10,
                },
            },
        )
        fake_records = [MagicMock(), MagicMock(), MagicMock()]
        with (
            patch(
                "general_ludd.remediation.blocker_detector.BlockerDetector",
            ) as mock_detector_cls,
            patch(
                "general_ludd.remediation.blocker_detector.RemediationConfig",
            ) as mock_rc,
        ):
            mock_detector = MagicMock()
            mock_detector.chronic_blockers = AsyncMock(return_value=fake_records)
            mock_detector_cls.return_value = mock_detector
            result = await h._collect_recurring_failures()
            mock_rc.assert_called_once_with(
                chronic_lookback_days=30,
                min_chronic_incidents=10,
            )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_handles_non_dict_self_improve_config(self):
        h = _make_handlers(config={"self_improve": "not-a-dict"})
        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector",
        ) as mock_detector_cls:
            mock_detector = MagicMock()
            mock_detector.chronic_blockers = AsyncMock(return_value=[])
            mock_detector_cls.return_value = mock_detector
            result = await h._collect_recurring_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_chronic_blockers_exception(self):
        h = _make_handlers()
        with patch(
            "general_ludd.remediation.blocker_detector.BlockerDetector",
        ) as mock_detector_cls:
            mock_detector = MagicMock()
            mock_detector.chronic_blockers = AsyncMock(side_effect=ValueError("bad"))
            mock_detector_cls.return_value = mock_detector
            result = await h._collect_recurring_failures()
        assert result == []


# ── _auto_record_episode deep paths ─────────────────────────────────────


class TestAutoRecordEpisodeDeep:
    @pytest.mark.asyncio
    async def test_skips_when_no_memory_repo(self):
        h = _make_handlers(_memory_repo=None)
        todo = MagicMock()
        await h._auto_record_episode(todo, MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_records_success_outcome(self):
        h = _make_handlers()
        todo = MagicMock()
        todo.assigned_agent = "agent-7"
        todo.work_type = "refactor"
        todo.title = "Fix types"
        todo.todo_id = "todo-abc"
        todo.task_type = "code_fix"
        todo.priority = "high"
        todo.project_id = "proj-x"
        new_status = MagicMock()
        type(new_status).value = PropertyMock(return_value="COMPLETE")
        decision = MagicMock()
        decision.decision = "complete"
        decision.summary = "All types fixed"

        with patch(
            "general_ludd.memory.episodic.EpisodicMemoryRecorder",
        ) as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder
            await h._auto_record_episode(todo, new_status, decision)

        mock_recorder.record_completion.assert_called_once()
        call_kwargs = mock_recorder.record_completion.call_args.kwargs
        assert call_kwargs["outcome"] == "success"
        assert call_kwargs["agent_id"] == "agent-7"
        assert call_kwargs["work_type"] == "refactor"
        assert call_kwargs["priority"] == "high"
        assert call_kwargs["takeaway"] == "All types fixed"
        assert call_kwargs["project_id"] == "proj-x"
        assert h._tick_metrics["episodes_recorded"] == 1

    @pytest.mark.asyncio
    async def test_records_failure_outcome(self):
        h = _make_handlers()
        todo = MagicMock()
        todo.assigned_agent = None
        todo.work_type = "test"
        todo.todo_id = "todo-fail"
        todo.task_type = None
        todo.priority = "low"
        todo.project_id = None
        todo.last_error = "segfault"
        new_status = MagicMock()
        type(new_status).value = PropertyMock(return_value="FAILED")
        decision = MagicMock()
        decision.decision = "reject"
        decision.summary = None
        decision.failure_reason = "Tests flaked"

        with patch(
            "general_ludd.memory.episodic.EpisodicMemoryRecorder",
        ) as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder
            await h._auto_record_episode(todo, new_status, decision)

        call_kwargs = mock_recorder.record_completion.call_args.kwargs
        assert call_kwargs["outcome"] == "failure"
        assert call_kwargs["agent_id"] == "test"
        assert call_kwargs["error_message"] == "Tests flaked"

    @pytest.mark.asyncio
    async def test_records_partial_outcome_for_unknown_status(self):
        h = _make_handlers()
        todo = MagicMock()
        todo.todo_id = "todo-partial"
        todo.assigned_agent = "agent-x"
        todo.work_type = "docs"
        todo.task_type = "doc"
        todo.priority = "medium"
        todo.project_id = None
        new_status = MagicMock()
        type(new_status).value = PropertyMock(return_value="CANCELLED")
        decision = MagicMock()
        decision.summary = None
        decision.failure_reason = None

        with patch(
            "general_ludd.memory.episodic.EpisodicMemoryRecorder",
        ) as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder
            await h._auto_record_episode(todo, new_status, decision)

        call_kwargs = mock_recorder.record_completion.call_args.kwargs
        assert call_kwargs["outcome"] == "partial"
        assert call_kwargs["priority"] == "medium"

    @pytest.mark.asyncio
    async def test_truncates_long_takeaway(self):
        h = _make_handlers()
        todo = MagicMock()
        todo.assigned_agent = "a"
        todo.work_type = "code"
        todo.todo_id = "t1"
        todo.task_type = "task"
        todo.priority = "medium"
        todo.project_id = None
        long_text = "x" * 600
        todo.title = long_text
        new_status = MagicMock()
        type(new_status).value = PropertyMock(return_value="COMPLETE")
        decision = MagicMock()
        decision.summary = None

        with patch(
            "general_ludd.memory.episodic.EpisodicMemoryRecorder",
        ) as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock()
            mock_recorder_cls.return_value = mock_recorder
            await h._auto_record_episode(todo, new_status, decision)

        call_kwargs = mock_recorder.record_completion.call_args.kwargs
        assert len(call_kwargs["takeaway"]) <= 500

    @pytest.mark.asyncio
    async def test_handles_recording_exception_gracefully(self):
        h = _make_handlers()
        todo = MagicMock()
        todo.assigned_agent = "a"
        todo.work_type = "code"
        todo.todo_id = "t1"
        todo.task_type = "t"
        todo.priority = "low"
        todo.project_id = None
        new_status = MagicMock()
        type(new_status).value = PropertyMock(return_value="COMPLETE")
        decision = MagicMock()
        decision.summary = None

        with patch(
            "general_ludd.memory.episodic.EpisodicMemoryRecorder",
        ) as mock_recorder_cls:
            mock_recorder = MagicMock()
            mock_recorder.record_completion = AsyncMock(side_effect=RuntimeError("db gone"))
            mock_recorder_cls.return_value = mock_recorder
            await h._auto_record_episode(todo, new_status, decision)

        assert h._tick_metrics.get("episodes_recorded", 0) == 0


# ── _auto_consolidate_memory deep paths ─────────────────────────────────


class TestAutoConsolidateMemoryDeep:
    @pytest.mark.asyncio
    async def test_skips_when_no_memory_repo(self):
        h = _make_handlers(_memory_repo=None)
        await h._auto_consolidate_memory()

    @pytest.mark.asyncio
    async def test_records_consolidation_metrics(self):
        h = _make_handlers()
        with patch(
            "general_ludd.memory.consolidation.MemoryConsolidator",
        ) as mock_consolidator_cls:
            mock_consolidator = MagicMock()
            mock_consolidator.consolidate = AsyncMock(return_value={"consolidated": 5, "episodes_consolidated": 42})
            mock_consolidator_cls.return_value = mock_consolidator
            await h._auto_consolidate_memory()

        assert h._tick_metrics["memory_consolidated"] == 5
        assert h._tick_metrics["memory_episodes_consolidated"] == 42

    @pytest.mark.asyncio
    async def test_no_metrics_when_zero_consolidated(self):
        h = _make_handlers()
        with patch(
            "general_ludd.memory.consolidation.MemoryConsolidator",
        ) as mock_consolidator_cls:
            mock_consolidator = MagicMock()
            mock_consolidator.consolidate = AsyncMock(return_value={"consolidated": 0, "episodes_consolidated": 0})
            mock_consolidator_cls.return_value = mock_consolidator
            await h._auto_consolidate_memory()

        assert "memory_consolidated" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_handles_consolidation_exception_gracefully(self):
        h = _make_handlers()
        with patch(
            "general_ludd.memory.consolidation.MemoryConsolidator",
        ) as mock_consolidator_cls:
            mock_consolidator = MagicMock()
            mock_consolidator.consolidate = AsyncMock(side_effect=RuntimeError("db"))
            mock_consolidator_cls.return_value = mock_consolidator
            await h._auto_consolidate_memory()

        assert "memory_consolidated" not in h._tick_metrics


# ── _auto_cross_task_learn deep paths ───────────────────────────────────


class TestAutoCrossTaskLearnDeep:
    @pytest.mark.asyncio
    async def test_skips_when_no_memory_repo(self):
        h = _make_handlers(_memory_repo=None)
        await h._auto_cross_task_learn()

    @pytest.mark.asyncio
    async def test_persists_improvement_todos(self):
        h = _make_handlers()
        h._persist_self_improve_todos = AsyncMock(return_value=2)
        with patch(
            "general_ludd.memory.cross_task.CrossTaskLearner",
        ) as mock_learner_cls:
            mock_learner = MagicMock()
            mock_learner.generate_improvement_report = AsyncMock(
                return_value={
                    "improvements_needed": [
                        {"suggested_action": "Add type hints"},
                        {"suggested_action": "Increase test coverage"},
                    ],
                    "total_episodes": 100,
                }
            )
            mock_learner_cls.return_value = mock_learner
            await h._auto_cross_task_learn()

        assert h._tick_metrics["cross_task_improvements"] == 2
        assert h._tick_metrics["cross_task_todos_persisted"] == 2

    @pytest.mark.asyncio
    async def test_no_metrics_when_no_improvements(self):
        h = _make_handlers()
        with patch(
            "general_ludd.memory.cross_task.CrossTaskLearner",
        ) as mock_learner_cls:
            mock_learner = MagicMock()
            mock_learner.generate_improvement_report = AsyncMock(
                return_value={
                    "improvements_needed": [],
                    "total_episodes": 50,
                }
            )
            mock_learner_cls.return_value = mock_learner
            await h._auto_cross_task_learn()

        assert "cross_task_improvements" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_handles_persistence_failure_gracefully(self):
        h = _make_handlers()
        h._persist_self_improve_todos = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(
            "general_ludd.memory.cross_task.CrossTaskLearner",
        ) as mock_learner_cls:
            mock_learner = MagicMock()
            mock_learner.generate_improvement_report = AsyncMock(
                return_value={
                    "improvements_needed": [{"suggested_action": "Refactor"}],
                    "total_episodes": 10,
                }
            )
            mock_learner_cls.return_value = mock_learner
            await h._auto_cross_task_learn()

        assert h._tick_metrics["cross_task_improvements"] == 1
        assert "cross_task_todos_persisted" not in h._tick_metrics


# ── _phase_consolidate_memory deep paths ────────────────────────────────


class TestPhaseConsolidateMemoryDeep:
    @pytest.mark.asyncio
    async def test_skips_when_counter_below_interval(self):
        h = _make_handlers(_consolidation_tick_counter=3, _consolidation_interval_ticks=10)
        await h._phase_consolidate_memory()
        assert h._consolidation_tick_counter == 4

    @pytest.mark.asyncio
    async def test_skips_when_no_memory_repo(self):
        h = _make_handlers(_memory_repo=None, _consolidation_tick_counter=10)
        await h._phase_consolidate_memory()
        assert h._consolidation_tick_counter == 0

    @pytest.mark.asyncio
    async def test_consolidates_procedural_and_semantic(self):
        h = _make_handlers()
        mock_procedural = MagicMock()
        mock_procedural.consolidate_from_episodes = AsyncMock(return_value=3)
        mock_semantic = MagicMock()
        mock_semantic.consolidate_from_consolidated = AsyncMock(return_value=2)
        h._procedural_memory = mock_procedural
        h._semantic_memory = mock_semantic

        with (
            patch(
                "general_ludd.memory.episodic.EpisodicMemoryRecorder",
            ) as mock_recorder_cls,
            patch(
                "general_ludd.memory.consolidation.MemoryConsolidator",
            ) as mock_consolidator_cls,
        ):
            mock_recorder_cls.return_value = MagicMock()
            mock_consolidator_cls.return_value = MagicMock()
            await h._phase_consolidate_memory()

        assert h._tick_metrics["memory_consolidated_procedures"] == 3
        assert h._tick_metrics["memory_consolidated_facts"] == 2
        assert h._consolidation_tick_counter == 0

    @pytest.mark.asyncio
    async def test_handles_procedural_failure_continues_to_semantic(self):
        h = _make_handlers()
        mock_procedural = MagicMock()
        mock_procedural.consolidate_from_episodes = AsyncMock(side_effect=RuntimeError("proc fail"))
        mock_semantic = MagicMock()
        mock_semantic.consolidate_from_consolidated = AsyncMock(return_value=1)
        h._procedural_memory = mock_procedural
        h._semantic_memory = mock_semantic

        with (
            patch(
                "general_ludd.memory.episodic.EpisodicMemoryRecorder",
            ) as mock_recorder_cls,
            patch(
                "general_ludd.memory.consolidation.MemoryConsolidator",
            ) as mock_consolidator_cls,
        ):
            mock_recorder_cls.return_value = MagicMock()
            mock_consolidator_cls.return_value = MagicMock()
            await h._phase_consolidate_memory()

        assert h._tick_metrics["memory_consolidated_facts"] == 1

    @pytest.mark.asyncio
    async def test_handles_semantic_failure_after_procedural_success(self):
        h = _make_handlers()
        mock_procedural = MagicMock()
        mock_procedural.consolidate_from_episodes = AsyncMock(return_value=5)
        mock_semantic = MagicMock()
        mock_semantic.consolidate_from_consolidated = AsyncMock(side_effect=OSError("disk"))
        h._procedural_memory = mock_procedural
        h._semantic_memory = mock_semantic

        with (
            patch(
                "general_ludd.memory.episodic.EpisodicMemoryRecorder",
            ) as mock_recorder_cls,
            patch(
                "general_ludd.memory.consolidation.MemoryConsolidator",
            ) as mock_consolidator_cls,
        ):
            mock_recorder_cls.return_value = MagicMock()
            mock_consolidator_cls.return_value = MagicMock()
            await h._phase_consolidate_memory()

        assert h._tick_metrics["memory_consolidated_procedures"] == 5

    @pytest.mark.asyncio
    async def test_no_metrics_when_zero_consolidated(self):
        h = _make_handlers()
        mock_procedural = MagicMock()
        mock_procedural.consolidate_from_episodes = AsyncMock(return_value=0)
        mock_semantic = MagicMock()
        mock_semantic.consolidate_from_consolidated = AsyncMock(return_value=0)
        h._procedural_memory = mock_procedural
        h._semantic_memory = mock_semantic

        with (
            patch(
                "general_ludd.memory.episodic.EpisodicMemoryRecorder",
            ) as mock_recorder_cls,
            patch(
                "general_ludd.memory.consolidation.MemoryConsolidator",
            ) as mock_consolidator_cls,
        ):
            mock_recorder_cls.return_value = MagicMock()
            mock_consolidator_cls.return_value = MagicMock()
            await h._phase_consolidate_memory()

        assert "memory_consolidated_procedures" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_falls_back_to_default_stores(self):
        h = _make_handlers(_procedural_memory=None, _semantic_memory=None)
        with (
            patch(
                "general_ludd.memory.procedural.ProceduralMemoryStore",
            ) as mock_proc_store_cls,
            patch(
                "general_ludd.memory.semantic.SemanticMemoryStore",
            ) as mock_sem_store_cls,
            patch(
                "general_ludd.memory.episodic.EpisodicMemoryRecorder",
            ) as mock_recorder_cls,
            patch(
                "general_ludd.memory.consolidation.MemoryConsolidator",
            ) as mock_consolidator_cls,
        ):
            mock_proc_store = MagicMock()
            mock_proc_store.consolidate_from_episodes = AsyncMock(return_value=1)
            mock_proc_store_cls.return_value = mock_proc_store
            mock_sem_store = MagicMock()
            mock_sem_store.consolidate_from_consolidated = AsyncMock(return_value=0)
            mock_sem_store_cls.return_value = mock_sem_store
            mock_recorder_cls.return_value = MagicMock()
            mock_consolidator_cls.return_value = MagicMock()
            await h._phase_consolidate_memory()

        mock_proc_store_cls.assert_called_once_with(memory_repo=h._memory_repo)
        mock_sem_store_cls.assert_called_once_with(memory_repo=h._memory_repo)


# ── _phase_refresh_model_performance deep paths ─────────────────────────


class TestPhaseRefreshModelPerformanceDeep:
    @pytest.mark.asyncio
    async def test_skips_when_no_session_factory(self):
        h = _make_handlers(
            _model_performance_interval=5,
            _total_ticks=5,
            _session_factory=None,
            _model_perf_repo=MagicMock(),
        )
        await h._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_refreshes_and_captures_routing_decisions(self):
        mock_repo = MagicMock()
        mock_repo.refresh_recent_stats = AsyncMock(return_value=7)
        mock_router = MagicMock()
        mock_router.current_routing_decisions = AsyncMock(return_value=[MagicMock()] * 3)
        h = _make_handlers(
            _model_performance_interval=3,
            _total_ticks=3,
            _model_perf_repo=mock_repo,
            _adaptive_router=mock_router,
        )
        await h._phase_refresh_model_performance()
        assert h._tick_metrics["model_routing_decisions"] == 3

    @pytest.mark.asyncio
    async def test_handles_router_exception_gracefully(self):
        mock_repo = MagicMock()
        mock_repo.refresh_recent_stats = AsyncMock(return_value=1)
        mock_router = MagicMock()
        mock_router.current_routing_decisions = AsyncMock(side_effect=RuntimeError("gone"))
        h = _make_handlers(
            _model_performance_interval=2,
            _total_ticks=2,
            _model_perf_repo=mock_repo,
            _adaptive_router=mock_router,
        )
        await h._phase_refresh_model_performance()
        assert "model_routing_decisions" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_handles_perf_repo_exception_gracefully(self):
        mock_repo = MagicMock()
        mock_repo.refresh_recent_stats = AsyncMock(side_effect=RuntimeError("db"))
        h = _make_handlers(
            _model_performance_interval=2,
            _total_ticks=2,
            _model_perf_repo=mock_repo,
        )
        await h._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_routing_when_router_lacks_method(self):
        mock_repo = MagicMock()
        mock_repo.refresh_recent_stats = AsyncMock(return_value=1)
        mock_router = MagicMock(spec=[])
        h = _make_handlers(
            _model_performance_interval=2,
            _total_ticks=2,
            _model_perf_repo=mock_repo,
            _adaptive_router=mock_router,
        )
        await h._phase_refresh_model_performance()
        assert "model_routing_decisions" not in h._tick_metrics


# ── _phase_poll_issue_sources deep paths ────────────────────────────────


class TestPhasePollIssueSourcesDeep:
    @pytest.mark.asyncio
    async def test_skips_when_tick_counter_below_interval(self):
        ingestor = AsyncMock()
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=3,
            _issue_poll_interval_ticks=10,
        )
        await h._phase_poll_issue_sources()
        assert h._issue_poll_tick_counter == 4
        ingestor.poll_issues.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_polled_issues(self):
        ingestor = AsyncMock()
        ingestor.poll_issues.return_value = [
            {"title": "issue-1", "work_type": "bug"},
            {"title": "issue-2", "work_type": "feature"},
        ]
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=10,
            _issue_poll_interval_ticks=10,
        )
        await h._phase_poll_issue_sources()
        assert h._todo_repo.create.call_count == 2
        assert h._tick_metrics["issues_polled"] == 2
        assert h._issue_poll_tick_counter == 0

    @pytest.mark.asyncio
    async def test_no_metrics_when_no_new_todos(self):
        ingestor = AsyncMock()
        ingestor.poll_issues.return_value = []
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=10,
            _issue_poll_interval_ticks=10,
        )
        await h._phase_poll_issue_sources()
        assert "issues_polled" not in h._tick_metrics

    @pytest.mark.asyncio
    async def test_handles_persistence_failure_per_todo(self):
        ingestor = AsyncMock()
        ingestor.poll_issues.return_value = [
            {"title": "good"},
            {"title": "bad"},
            {"title": "also-good"},
        ]
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=10,
            _issue_poll_interval_ticks=10,
        )
        h._todo_repo.create.side_effect = [
            None,
            RuntimeError("duplicate"),
            None,
        ]
        await h._phase_poll_issue_sources()
        assert h._tick_metrics["issues_polled"] == 2

    @pytest.mark.asyncio
    async def test_handles_poll_exception_gracefully(self):
        ingestor = AsyncMock()
        ingestor.poll_issues.side_effect = RuntimeError("api down")
        h = _make_handlers(
            _issue_ingestor=ingestor,
            _issue_poll_tick_counter=10,
            _issue_poll_interval_ticks=10,
        )
        await h._phase_poll_issue_sources()
        assert "issues_polled" not in h._tick_metrics


# ── _phase_service_discovery deep paths ─────────────────────────────────


class TestPhaseServiceDiscoveryDeep:
    @pytest.mark.asyncio
    async def test_runs_discovery_when_interval_elapsed(self):
        mock_sd = MagicMock()
        mock_report = MagicMock()
        mock_report.new_services = ["s1"]
        mock_report.changed_services = ["s2", "s3"]
        mock_report.retired_services = []
        mock_report.total_discovered = 10
        mock_report.errors = []
        mock_sd.run_discovery_pipeline = MagicMock(return_value=mock_report)
        h = _make_handlers(
            _service_discovery=mock_sd,
            _service_discovery_last_run=0.0,
            # interval 0 means "already elapsed" — without this the default
            # 86400s interval makes the phase return before running discovery.
            config={"service_discovery_interval_seconds": 0},
        )
        await h._phase_service_discovery()
        # Flush the event loop so any executor-scheduled work from the mocked
        # bounded-to-thread wrapper is fully drained before asserting. On CI
        # runners the discovery call can still be in flight when the coroutine
        # returns, making assert_called_once race the thread handoff.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_sd.run_discovery_pipeline.assert_called_once()
        assert h._service_discovery_last_run > 0

    @pytest.mark.asyncio
    async def test_handles_discovery_exception_gracefully(self):
        mock_sd = MagicMock()
        mock_sd.run_discovery_pipeline = MagicMock(side_effect=RuntimeError("timeout"))
        h = _make_handlers(
            _service_discovery=mock_sd,
            _service_discovery_last_run=0.0,
            config={"service_discovery_interval_seconds": 0},
        )
        await h._phase_service_discovery()


# ── _phase_reap_expired_sts_tokens deep paths ───────────────────────────


class TestPhaseReapExpiredStsTokensDeep:
    @pytest.mark.asyncio
    async def test_reaps_tokens_on_interval(self):
        reaper = AsyncMock()
        reaper.reap_expired.return_value = 12
        h = _make_handlers(
            _daemon_state={"_sts_reaper": reaper},
            _total_ticks=60,
            config={"sts_reap_interval_ticks": 60},
        )
        await h._phase_reap_expired_sts_tokens()
        assert h._tick_metrics["sts_tokens_reaped"] == 12

    @pytest.mark.asyncio
    async def test_handles_reaper_exception_gracefully(self):
        reaper = AsyncMock()
        reaper.reap_expired.side_effect = RuntimeError("vault down")
        h = _make_handlers(
            _daemon_state={"_sts_reaper": reaper},
            _total_ticks=30,
            config={"sts_reap_interval_ticks": 30},
        )
        await h._phase_reap_expired_sts_tokens()
        assert "sts_tokens_reaped" not in h._tick_metrics


# ── _phase_purge_old_task_decisions deep paths ──────────────────────────


class TestPhasePurgeOldTaskDecisionsDeep:
    @pytest.mark.asyncio
    async def test_purges_on_interval(self):
        h = _make_handlers(
            _total_ticks=3600,
            config={
                "task_decisions_retention_interval_ticks": 3600,
                "task_decisions_retention_days": 90,
            },
        )
        with patch(
            "general_ludd.db.task_decisions_retention.cleanup_old_task_decisions",
            new=AsyncMock(return_value=42),
        ) as mock_cleanup:
            await h._phase_purge_old_task_decisions()
            mock_cleanup.assert_called_once_with(h._active_session, retention_days=90)
        assert h._tick_metrics["task_decisions_purged"] == 42

    @pytest.mark.asyncio
    async def test_handles_cleanup_exception_gracefully(self):
        h = _make_handlers(
            _total_ticks=100,
            config={"task_decisions_retention_interval_ticks": 100},
        )
        with patch(
            "general_ludd.db.task_decisions_retention.cleanup_old_task_decisions",
            new=AsyncMock(side_effect=RuntimeError("table missing")),
        ):
            await h._phase_purge_old_task_decisions()
        assert "task_decisions_purged" not in h._tick_metrics


# ── _maybe_cleanup_ephemeral deep paths ─────────────────────────────────


class TestMaybeCleanupEphemeralDeep:
    @pytest.mark.asyncio
    async def test_deletes_ephemeral_account(self):
        mock_mgr = MagicMock()
        todo = MagicMock()
        todo.todo_id = "todo-ephem"
        todo.tags = {"ephemeral_account_id": "acc-123"}
        h = _make_handlers(_ephemeral_account_manager=mock_mgr)
        with patch(
            "general_ludd.account.ephemeral.maybe_delete_ephemeral_after_task",
            return_value={"deleted": True, "account_id": "acc-123"},
        ):
            await h._maybe_cleanup_ephemeral(todo)

    @pytest.mark.asyncio
    async def test_no_deletion_when_result_is_none(self):
        mock_mgr = MagicMock()
        todo = MagicMock()
        todo.todo_id = "todo-noop"
        todo.tags = {}
        h = _make_handlers(_ephemeral_account_manager=mock_mgr)
        with patch(
            "general_ludd.account.ephemeral.maybe_delete_ephemeral_after_task",
            return_value=None,
        ):
            await h._maybe_cleanup_ephemeral(todo)

    @pytest.mark.asyncio
    async def test_handles_non_dict_tags(self):
        mock_mgr = MagicMock()
        todo = MagicMock()
        todo.todo_id = "todo-list-tags"
        todo.tags = ["not-a-dict"]
        h = _make_handlers(_ephemeral_account_manager=mock_mgr)
        with patch(
            "general_ludd.account.ephemeral.maybe_delete_ephemeral_after_task",
            return_value=None,
        ) as mock_maybe:
            await h._maybe_cleanup_ephemeral(todo)
            mock_maybe.assert_called_once()


# ── _collect_training_data_from_returns deep paths ──────────────────────


class TestCollectTrainingDataFromReturnsDeep:
    @pytest.mark.asyncio
    async def test_handles_collector_exception_gracefully(self):
        h = _make_handlers()
        with patch(
            "general_ludd.ornith.training_data.TrainingDataCollector",
            side_effect=RuntimeError("import failed"),
        ):
            result = await h._collect_training_data_from_returns()
        assert result == 0

    @pytest.mark.asyncio
    async def test_handles_session_factory_exception(self):
        h = _make_handlers()
        h._session_factory.side_effect = RuntimeError("connection refused")
        result = await h._collect_training_data_from_returns()
        assert result == 0


# ── _apply_self_improvements deep paths ─────────────────────────────────


class TestApplySelfImprovementsDeep:
    @pytest.mark.asyncio
    async def test_logs_quality_report_and_detects_patterns(self):
        h = _make_handlers(_daemon_state={})
        mock_collector = MagicMock()
        mock_collector.quality_report = AsyncMock(
            return_value={
                "total_pairs": 100,
                "resolved": 80,
                "positive_examples": 60,
                "negative_examples": 20,
            }
        )
        mock_rejected = MagicMock()
        mock_rejected.instruction = "stop premature halt abort"
        mock_collector.list_by_statuses = AsyncMock(return_value=[mock_rejected])

        mock_outcome_analyzer = MagicMock()
        mock_outcome_analyzer.analyze = MagicMock(return_value={"suggestions": ["use sonnet"]})

        with (
            patch(
                "general_ludd.ornith.training_data.TrainingDataCollector",
                return_value=mock_collector,
            ),
            patch(
                "general_ludd.self_improve.outcomes.OutcomeAnalyzer",
                return_value=mock_outcome_analyzer,
            ),
        ):
            await h._apply_self_improvements()

        patterns = h._daemon_state["self_improve_error_patterns"]
        assert patterns["patterns"]["premature_stop"] == 1

    @pytest.mark.asyncio
    async def test_detects_grind_pattern(self):
        h = _make_handlers(_daemon_state={})
        mock_collector = MagicMock()
        mock_collector.quality_report = AsyncMock(
            return_value={
                "total_pairs": 10,
                "resolved": 5,
                "positive_examples": 3,
                "negative_examples": 2,
            }
        )
        mock_rejected = MagicMock()
        mock_rejected.instruction = "inline grind token main_thread"
        mock_collector.list_by_statuses = AsyncMock(return_value=[mock_rejected])

        mock_outcome_analyzer = MagicMock()
        mock_outcome_analyzer.analyze = MagicMock(return_value={"suggestions": []})

        with (
            patch(
                "general_ludd.ornith.training_data.TrainingDataCollector",
                return_value=mock_collector,
            ),
            patch(
                "general_ludd.self_improve.outcomes.OutcomeAnalyzer",
                return_value=mock_outcome_analyzer,
            ),
        ):
            await h._apply_self_improvements()

        patterns = h._daemon_state["self_improve_error_patterns"]
        assert patterns["patterns"]["grind_failure"] == 1

    @pytest.mark.asyncio
    async def test_no_daemon_state_update_when_no_patterns(self):
        h = _make_handlers(_daemon_state={})
        mock_collector = MagicMock()
        mock_collector.quality_report = AsyncMock(
            return_value={
                "total_pairs": 1,
                "resolved": 1,
                "positive_examples": 1,
                "negative_examples": 0,
            }
        )
        mock_collector.list_by_statuses = AsyncMock(return_value=[])

        mock_outcome_analyzer = MagicMock()
        mock_outcome_analyzer.analyze = MagicMock(return_value={"suggestions": []})

        with (
            patch(
                "general_ludd.ornith.training_data.TrainingDataCollector",
                return_value=mock_collector,
            ),
            patch(
                "general_ludd.self_improve.outcomes.OutcomeAnalyzer",
                return_value=mock_outcome_analyzer,
            ),
        ):
            await h._apply_self_improvements()

        assert "self_improve_error_patterns" not in h._daemon_state


# ── phase handler method existence ──────────────────────────────────────


class TestPhaseHandlerExistence:
    def test_all_phase_methods_exist(self):
        h = _make_handlers()
        phases = [
            "_phase_self_improve",
            "_phase_refresh_model_performance",
            "_phase_poll_issue_sources",
            "_phase_service_discovery",
            "_phase_reap_expired_sts_tokens",
            "_phase_purge_old_task_decisions",
            "_phase_emit_tick_metrics",
            "_phase_consolidate_memory",
        ]
        for phase_name in phases:
            assert callable(getattr(h, phase_name, None)), f"Missing {phase_name}"

    def test_discoverable_helper_methods(self):
        h = _make_handlers()
        helpers = [
            "_detect_grinding_patterns",
            "_collect_recurring_failures",
            "_collect_training_data_from_returns",
            "_apply_self_improvements",
            "_maybe_cleanup_ephemeral",
            "_auto_record_episode",
            "_auto_consolidate_memory",
            "_auto_cross_task_learn",
        ]
        for helper in helpers:
            assert callable(getattr(h, helper, None)), f"Missing {helper}"
