"""Unit tests for AG.15: Reflexion loops — self-critique and iterative improvement.

Tests the ReflexionLoop orchestrator, EpisodeRecord, ReflexionMemory, and the
full try → evaluate → reflect → retry cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from general_ludd.ag14_reflexion.loop import (
    EpisodeRecord,
    ReflexionLoop,
    ReflexionMemory,
    ReflexionResult,
    create_reflexion_loop,
)


class TestEpisodeRecord:
    def test_construction_minimal(self):
        ep = EpisodeRecord(
            episode_id="ep-1",
            task_description="Summarize a paragraph",
            actor_output="The summary is...",
            evaluation_score=0.85,
        )
        assert ep.episode_id == "ep-1"
        assert ep.evaluation_score == 0.85
        assert ep.reflexion_text == ""
        assert ep.retry_count == 0
        assert ep.created_at is not None

    def test_construction_full(self):
        ep = EpisodeRecord(
            episode_id="ep-2",
            task_description="Write a function",
            actor_output="def foo(): pass",
            evaluation_score=0.5,
            reflexion_text="Missing docstring and type hints",
            retry_count=2,
        )
        assert ep.episode_id == "ep-2"
        assert ep.actor_output == "def foo(): pass"
        assert ep.reflexion_text == "Missing docstring and type hints"
        assert ep.retry_count == 2

    def test_is_success_default_threshold(self):
        ep = EpisodeRecord(
            episode_id="ep-3",
            task_description="Task",
            actor_output="Output",
            evaluation_score=0.8,
        )
        assert ep.is_success() is True

    def test_is_success_below_threshold(self):
        ep = EpisodeRecord(
            episode_id="ep-4",
            task_description="Task",
            actor_output="Output",
            evaluation_score=0.79,
        )
        assert ep.is_success() is False

    def test_is_success_custom_threshold(self):
        ep = EpisodeRecord(
            episode_id="ep-5",
            task_description="Task",
            actor_output="Output",
            evaluation_score=0.6,
        )
        assert ep.is_success(threshold=0.9) is False
        assert ep.is_success(threshold=0.5) is True


class TestReflexionMemory:
    def test_empty_memory(self):
        mem = ReflexionMemory()
        assert mem.episode_count == 0
        assert mem.last_score() is None
        assert mem.recent_feedback() == []

    def test_add_and_retrieve(self):
        mem = ReflexionMemory()
        ep = EpisodeRecord(
            episode_id="ep-1",
            task_description="Task",
            actor_output="Output",
            evaluation_score=0.6,
            reflexion_text="Needs improvement",
        )
        mem.add(ep)
        assert mem.episode_count == 1
        assert mem.last_score() == 0.6
        assert mem.recent_feedback() == ["Needs improvement"]

    def test_recent_feedback_respects_window(self):
        mem = ReflexionMemory(max_window=3)
        for i in range(5):
            mem.add(
                EpisodeRecord(
                    episode_id=f"ep-{i}",
                    task_description="Task",
                    actor_output="Out",
                    evaluation_score=0.5,
                    reflexion_text=f"Feedback {i}",
                )
            )
        assert mem.episode_count == 5
        feedback = mem.recent_feedback(n=2)
        assert feedback == ["Feedback 3", "Feedback 4"]

    def test_recent_feedback_skips_empty_reflexion(self):
        mem = ReflexionMemory()
        mem.add(
            EpisodeRecord(
                episode_id="ep-1",
                task_description="Task",
                actor_output="Out",
                evaluation_score=0.9,
                reflexion_text="",
            )
        )
        mem.add(
            EpisodeRecord(
                episode_id="ep-2",
                task_description="Task",
                actor_output="Out",
                evaluation_score=0.5,
                reflexion_text="Still needs work",
            )
        )
        assert mem.recent_feedback() == ["Still needs work"]

    def test_clear(self):
        mem = ReflexionMemory()
        mem.add(
            EpisodeRecord(
                episode_id="ep-1",
                task_description="Task",
                actor_output="Out",
                evaluation_score=0.5,
            )
        )
        mem.clear()
        assert mem.episode_count == 0

    def test_episodes_is_tuple(self):
        mem = ReflexionMemory()
        mem.add(
            EpisodeRecord(
                episode_id="ep-1",
                task_description="Task",
                actor_output="Out",
                evaluation_score=0.5,
            )
        )
        assert isinstance(mem.episodes, tuple)

    def test_memory_truncates_to_window_doubled(self):
        mem = ReflexionMemory(max_window=2)
        for i in range(5):
            mem.add(
                EpisodeRecord(
                    episode_id=f"ep-{i}",
                    task_description="Task",
                    actor_output="Out",
                    evaluation_score=0.5,
                )
            )
        assert 2 <= mem.episode_count <= 4


def _passthrough_actor(task: str, feedback: Sequence[str]) -> str:
    return f"[{task}] output with feedback: {list(feedback)}"


def _fake_evaluator_increasing() -> tuple[
    object, object
]:
    state: dict[str, float] = {"scores": iter([0.3, 0.6, 0.9])}

    def evaluate(_task: str, _output: str) -> float:
        return float(next(state["scores"]))  # type: ignore[arg-type]

    return state, evaluate


class TestReflexionLoop:
    def test_construction_defaults(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 1.0,
        )
        assert loop.max_retries == 3
        assert loop.score_threshold == 0.8
        assert loop.memory.episode_count == 0

    def test_construction_invalid_max_retries(self):
        with pytest.raises(ValueError, match="max_retries"):
            ReflexionLoop(
                actor=_passthrough_actor,
                evaluator=lambda t, o: 1.0,
                max_retries=-1,
            )

    def test_construction_invalid_threshold_low(self):
        with pytest.raises(ValueError, match="score_threshold"):
            ReflexionLoop(
                actor=_passthrough_actor,
                evaluator=lambda t, o: 1.0,
                score_threshold=-0.1,
            )

    def test_construction_invalid_threshold_high(self):
        with pytest.raises(ValueError, match="score_threshold"):
            ReflexionLoop(
                actor=_passthrough_actor,
                evaluator=lambda t, o: 1.0,
                score_threshold=1.1,
            )

    def test_run_success_first_attempt(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.9,
        )
        result = loop.run("Summarize X")
        assert result.success is True
        assert result.total_retries == 0
        assert len(result.episodes) == 1
        assert result.final_episode.evaluation_score == 0.9

    def test_run_success_after_retries(self):
        _state, evaluator = _fake_evaluator_increasing()
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=evaluator,
            max_retries=5,
        )
        result = loop.run("Complex task")
        assert result.success is True
        assert result.total_retries == 2
        assert len(result.episodes) == 3
        assert result.final_episode.evaluation_score == 0.9

    def test_run_failure_all_retries_exhausted(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.2,
            max_retries=2,
            score_threshold=0.8,
        )
        result = loop.run("Impossible task")
        assert result.success is False
        assert result.total_retries == 2
        assert len(result.episodes) == 3

    def test_run_each_episode_gets_feedback(self):
        call_log: list[list[str]] = []

        def logging_actor(task: str, feedback: Sequence[str]) -> str:
            call_log.append(list(feedback))
            return f"output for {task}"

        loop = ReflexionLoop(
            actor=logging_actor,
            evaluator=lambda t, o: 0.5,
            max_retries=2,
            score_threshold=0.9,
        )
        loop.run("Task with feedback")
        assert len(call_log) == 3
        assert call_log[0] == []
        assert call_log[1] != []
        assert call_log[2] != []

    def test_run_all_episodes_in_memory(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.3,
            max_retries=2,
        )
        loop.run("Task")
        assert loop.memory.episode_count == 3

    def test_run_with_zero_max_retries(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.9,
            max_retries=0,
        )
        result = loop.run("Task")
        assert result.success is True
        assert len(result.episodes) == 1

    def test_run_failure_with_zero_max_retries(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.5,
            max_retries=0,
            score_threshold=0.9,
        )
        result = loop.run("Task")
        assert result.success is False
        assert result.total_retries == 0
        assert len(result.episodes) == 1

    def test_episode_records_have_unique_ids(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.3,
            max_retries=3,
        )
        result = loop.run("Task")
        ids = {ep.episode_id for ep in result.episodes}
        assert len(ids) == len(result.episodes)

    def test_reset_clears_state(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.9,
        )
        loop.run("Task A")
        loop.reset()
        assert loop.memory.episode_count == 0

    def test_multiple_runs_independent(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.9,
        )
        loop.run("Task A")
        count_after_first = loop.memory.episode_count
        loop.run("Task B")
        assert loop.memory.episode_count == count_after_first * 2

    def test_create_reflexion_loop_factory(self):
        loop = create_reflexion_loop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.5,
            max_retries=5,
            score_threshold=0.7,
        )
        assert loop.max_retries == 5
        assert loop.score_threshold == 0.7

    def test_episodes_ordered_by_attempt(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.3,
            max_retries=3,
        )
        result = loop.run("Task")
        retries = [ep.retry_count for ep in result.episodes]
        assert retries == sorted(retries)

    def test_reflexion_text_present_on_low_score(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.3,
            max_retries=2,
            score_threshold=0.8,
        )
        result = loop.run("Task")
        for ep in result.episodes:
            assert ep.reflexion_text != ""
            assert "score" in ep.reflexion_text.lower()

    def test_reflexion_text_absent_on_high_score(self):
        loop = ReflexionLoop(
            actor=_passthrough_actor,
            evaluator=lambda t, o: 0.9,
            max_retries=2,
            score_threshold=0.8,
        )
        result = loop.run("Task")
        assert result.success is True
        assert result.final_episode.reflexion_text == ""

    def test_reflexion_result_termination(self):
        result = ReflexionResult(
            success=True,
            final_episode=EpisodeRecord(
                episode_id="e1",
                task_description="Test",
                actor_output="OK",
                evaluation_score=0.9,
            ),
        )
        assert result.success is True
        assert result.total_retries == 0
