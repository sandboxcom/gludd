"""Tests for LangGraphConsensusEngine (parallel multi-agent debate via Send API)."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.review.langgraph_consensus import (
    AgentVerdict,
    LangGraphConsensusEngine,
)


def _make_reviewer(*responses: str):
    """Return a MagicMock that returns each response in order (wrapping)."""
    idx = 0

    def side_effect(prompt: str) -> str:
        nonlocal idx
        r = responses[idx % len(responses)]
        idx += 1
        return r

    mock = MagicMock()
    mock.side_effect = side_effect
    return mock


class TestLangGraphConsensusEngine:
    def test_engine_is_constructable(self) -> None:
        """LangGraphConsensusEngine instantiates without error."""
        reviewer = _make_reviewer("approve\nok")
        engine = LangGraphConsensusEngine(
            reviewer_callable=reviewer,
        )
        assert engine is not None
        assert engine._graph is not None

    def test_all_agree_returns_consensus_round_1(self) -> None:
        """When all agents return approve, consensus is reached in round 1."""
        reviewer = _make_reviewer("approve\nlooks good")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["confidence"] == 1.0
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 3

    def test_all_reject_consensus(self) -> None:
        """When all agents reject, consensus is reached with reject verdict."""
        reviewer = _make_reviewer("reject\nnot acceptable")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "reject"
        assert result["confidence"] == 1.0

    def test_parallel_fan_out_all_agents_called_in_same_round(self) -> None:
        """Verify that all agents in a round are called (parallel fan-out).

        In the langgraph Send API, all agents in a round receive prompts
        with the same current_round. Each should be called exactly once
        per round.
        """
        reviewer = _make_reviewer("approve\nok")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        engine.run_debate("Is this correct?", num_agents=3)

        call_args = [c[0][0] for c in reviewer.call_args_list]
        assert len(call_args) == 3
        assert any("reviewer agent 1 of 3" in a for a in call_args)
        assert any("reviewer agent 2 of 3" in a for a in call_args)
        assert any("reviewer agent 3 of 3" in a for a in call_args)

    def test_structured_output_parsing(self) -> None:
        """Verdicts are parsed into AgentVerdict structured models.

        Each agent_votes entry should contain agent_index, verdict (typed),
        rationale, and round_num. No raw string splitting artifacts.
        """
        reviewer = _make_reviewer("approve\nrationale text here")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", num_agents=2)

        votes = result["agent_votes"]
        assert len(votes) == 2
        for v in votes:
            assert isinstance(v, dict)
            assert "agent_index" in v
            assert v["verdict"] in ("approve", "reject", "needs_changes")
            assert "rationale" in v
            assert isinstance(v["rationale"], str)
            assert "round_num" in v
            assert v["round_num"] == 1

        parsed = [AgentVerdict(**v) for v in votes]
        assert all(v.verdict == "approve" for v in parsed)
        assert all(v.rationale == "rationale text here" for v in parsed)

    def test_consensus_after_dissent_rounds(self) -> None:
        """Agents that disagree in round 1 can converge in round 2.

        Round 1: 3 agents with different verdicts (no consensus).
        Round 2: all 3 converge on approve (consensus).
        """
        reviewer = _make_reviewer(
            "approve\nround1 agent0",
            "reject\nround1 agent1",
            "needs_changes\nround1 agent2",
            "approve\nround2 agent0",
            "approve\nround2 agent1",
            "approve\nround2 agent2",
        )

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=5)

        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["rounds"] == 2
        assert result["confidence"] == 1.0
        assert len(result["agent_votes"]) == 6

    def test_dissent_shows_in_round_2_prompts(self) -> None:
        """Round 2+ prompts include dissenting opinions from the prior round."""
        reviewer = _make_reviewer(
            "approve\nr1a0",
            "reject\nr1a1",
            "needs_changes\nr1a2",
            "approve\nr2a0",
            "approve\nr2a1",
            "approve\nr2a2",
        )

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        engine.run_debate("Is this correct?", max_rounds=5)

        round2_calls = reviewer.call_args_list[3:]
        for call_obj in round2_calls:
            prompt = call_obj[0][0]
            assert "NOT unanimous" in prompt
            assert any(a in prompt for a in ("Agent 1:", "Agent 2:", "Agent 3:"))

    def test_max_rounds_exhausted_goes_to_judge(self) -> None:
        """When agents never agree and max_rounds is hit, judge tie-breaks."""
        reviewer = _make_reviewer(
            "approve\nalways approve",
            "reject\nalways reject",
            "needs_changes\nalways changes",
            "approve\nalways approve",
            "reject\nalways reject",
            "needs_changes\nalways changes",
        )

        def judge(prompt: str) -> str:
            return "approve\njudge rules approve"

        engine = LangGraphConsensusEngine(
            reviewer_callable=reviewer,
            judge_callable=judge,
        )
        result = engine.run_debate("Is this correct?", max_rounds=2)

        assert result["consensus"] is False
        assert result["judge_ruling"] is True
        assert result["judge_verdict"] == "approve"
        assert result["verdict"] == "approve"
        assert result["rounds"] == 2

    def test_max_rounds_exhausted_no_judge_returns_tie(self) -> None:
        """When agents never agree, no judge configured, returns tie."""
        reviewer = _make_reviewer(
            "approve\nalways",
            "reject\nalways",
            "approve\nalways",
            "reject\nalways",
            "approve\nalways",
            "reject\nalways",
        )

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=2)

        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["judge_ruling"] is False

    def test_context_included_in_prompt(self) -> None:
        """The context string is passed through to agent prompts."""
        reviewer = _make_reviewer("approve\nok")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        engine.run_debate("Is this correct?", context="Background info")

        for call_args in reviewer.call_args_list:
            prompt = call_args[0][0]
            assert "Background info" in prompt

    def test_empty_question(self) -> None:
        """Empty question returns error result."""
        reviewer = MagicMock()
        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)

        result = engine.run_debate("   ")
        assert result["verdict"] == "error"
        assert "empty" in result["error"].lower()
        reviewer.assert_not_called()

    def test_no_reviewer_returns_error(self) -> None:
        """Engine without a reviewer returns error result."""
        engine = LangGraphConsensusEngine()
        result = engine.run_debate("Is this correct?")

        assert result["verdict"] == "error"
        assert result["rounds"] == 0

    def test_single_agent_always_consensus(self) -> None:
        """Single agent trivially reaches consensus in round 1."""
        reviewer = _make_reviewer("needs_changes\nadd tests")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", num_agents=1)

        assert result["consensus"] is True
        assert result["verdict"] == "needs_changes"
        assert result["confidence"] == 1.0
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 1

    def test_invalid_num_agents_clamped(self) -> None:
        """num_agents < 1 is clamped to 1."""
        reviewer = _make_reviewer("approve\nok")
        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", num_agents=0)

        assert result["consensus"] is True
        assert len(result["agent_votes"]) == 1

    def test_invalid_max_rounds_clamped(self) -> None:
        """max_rounds < 1 is clamped to 1."""
        reviewer = _make_reviewer("approve\nok")
        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=0)

        assert result["consensus"] is True
        assert result["rounds"] == 1

    def test_transcript_records_all_rounds(self) -> None:
        """Transcript includes vote data organized by round."""
        reviewer = _make_reviewer("approve\ngood")
        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?")

        assert len(result["transcript"]) == 1
        assert result["transcript"][0]["round"] == 1
        assert len(result["transcript"][0]["votes"]) == 3

    def test_verdict_defaults_to_needs_changes(self) -> None:
        """Unparseable output defaults to needs_changes verdict."""
        reviewer = _make_reviewer("some rambling text with no clear verdict")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "needs_changes"

    def test_verdict_multiline_handling(self) -> None:
        """Verdict on non-first line is still detected correctly."""
        reviewer = _make_reviewer("\n\napprove\nrationale here")

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "approve"

    def test_partial_agreement_confidence(self) -> None:
        """2-of-3 agents agree provides correct confidence in tie result."""
        reviewer = _make_reviewer(
            "approve\nok",
            "approve\nalso ok",
            "reject\nno",
        )

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=1)

        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["confidence"] == 2 / 3

    def test_consensus_reviewer_with_langgraph_flag(self) -> None:
        """ConsensusReviewer with use_langgraph=True constructs LangGraphConsensusEngine."""
        from general_ludd.review.consensus_reviewer import ConsensusReviewer

        gateway = MagicMock()

        def call_model(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> str:
            return "approve\nlooks good"

        gateway.call_model.side_effect = call_model

        reviewer = ConsensusReviewer(gateway, num_agents=2, max_rounds=1, use_langgraph=True)
        assert isinstance(reviewer._engine, LangGraphConsensusEngine)

        from general_ludd.schemas.task_return import TaskReturn
        tr = TaskReturn(
            return_id="R1",
            todo_id="T1",
            job_id="J1",
            playbook="test.yml",
            queue="model",
            work_type="code",
            exit_code=0,
            result_summary="All good.",
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "complete"
        assert decision.confidence == 1.0
