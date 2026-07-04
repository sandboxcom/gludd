"""Tests for G11 multi-agent debate/consensus engine."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.review.consensus import ConsensusEngine


class TestConsensusEngine:
    def test_engine_is_constructable(self) -> None:
        """ConsensusEngine instantiates without error."""
        engine = ConsensusEngine()
        assert engine is not None

    def test_all_agree_returns_consensus(self) -> None:
        """When all agents return the same verdict, consensus is reached immediately."""
        reviewer = MagicMock(return_value="approve\nlooks good to me")

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["confidence"] == 1.0
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 3

    def test_all_reject_consensus(self) -> None:
        """When all agents reject, consensus is reached with reject verdict."""
        reviewer = MagicMock(return_value="reject\nnot acceptable")

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "reject"

    def test_tie_with_judge_model(self) -> None:
        """When agents disagree and max rounds are reached, judge breaks the tie."""
        responses = [
            "approve\nlooks fine",
            "reject\nbad quality",
            "needs_changes\nimprove X",
        ]

        def mixed_reviewer(prompt: str) -> str:
            return responses.pop(0) if responses else "approve"

        def judge(prompt: str) -> str:
            return "approve\njudge rules approve"

        engine = ConsensusEngine(reviewer=mixed_reviewer, judge=judge)
        result = engine.run_debate("Is this correct?", max_rounds=1)

        assert result["consensus"] is False
        assert result["judge_ruling"] is True
        assert result["judge_verdict"] == "approve"
        assert result["verdict"] == "approve"
        assert result["rounds"] == 1

    def test_tie_no_judge(self) -> None:
        """When agents disagree and no judge is configured, returns tie."""
        responses = ["approve\nok", "reject\nno", "approve\nok"]
        response_idx = 0

        def mixed_reviewer(prompt: str) -> str:
            nonlocal response_idx
            r = responses[response_idx % len(responses)]
            response_idx += 1
            return r

        engine = ConsensusEngine(reviewer=mixed_reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=1)

        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["judge_ruling"] is False

    def test_empty_question(self) -> None:
        """Empty question string returns an error result."""
        reviewer = MagicMock()
        engine = ConsensusEngine(reviewer=reviewer)

        result = engine.run_debate("   ")
        assert result["verdict"] == "error"
        assert "empty" in result["error"].lower()
        reviewer.assert_not_called()

    def test_single_agent(self) -> None:
        """A single agent always achieves trivial consensus in round 1."""
        reviewer = MagicMock(return_value="needs_changes\nadd tests")

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?", num_agents=1)

        assert result["consensus"] is True
        assert result["verdict"] == "needs_changes"
        assert result["confidence"] == 1.0
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 1

    def test_no_reviewer_returns_error(self) -> None:
        """Engine without a reviewer returns an error result."""
        engine = ConsensusEngine()
        result = engine.run_debate("Is this correct?")

        assert result["verdict"] == "error"
        assert result["rounds"] == 0

    def test_context_included_in_prompt(self) -> None:
        """The context text is passed through to the reviewer."""
        reviewer = MagicMock(return_value="approve\ngood")
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?", context="Background info here")

        assert result["consensus"] is True
        for call_args in reviewer.call_args_list:
            prompt = call_args[0][0]
            assert "Background info here" in prompt

    def test_consensus_after_dissent_rounds(self) -> None:
        """Agents that disagree in round 1 can converge in round 2."""
        call_count = 0

        def flip_flop(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                verdicts = ["approve", "reject", "needs_changes"]
                return f"{verdicts[(call_count - 1) % 3]}\nround1 vote"
            else:
                return "approve\nconvinced by peer"

        engine = ConsensusEngine(reviewer=flip_flop)
        result = engine.run_debate("Is this correct?", max_rounds=5)

        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["rounds"] == 2
        assert result["confidence"] == 1.0

    def test_transcript_records_all_rounds(self) -> None:
        """The transcript includes vote data for every round."""
        reviewer = MagicMock(return_value="approve\ngood")
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?")

        assert len(result["transcript"]) == 1
        assert result["transcript"][0]["round"] == 1
        assert len(result["transcript"][0]["votes"]) == 3

    def test_multiple_dissent_rounds_tie(self) -> None:
        """Persistent disagreement across multiple rounds results in tie."""

        def stubborn(prompt: str) -> str:
            if "reviewer agent 1" in prompt.lower():
                return "approve\nalways"
            elif "reviewer agent 2" in prompt.lower():
                return "reject\nalways"
            else:
                return "needs_changes\nalways"

        engine = ConsensusEngine(reviewer=stubborn)
        result = engine.run_debate("Is this correct?", max_rounds=3)

        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["rounds"] == 3
        assert result["judge_ruling"] is False

    def test_verdict_parse_handles_multiline(self) -> None:
        """Verdicts on non-first lines are still parsed correctly."""
        reviewer = MagicMock(return_value="\n\napprove\nrationale here")

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "approve"

    def test_verdict_parse_defaults_to_needs_changes(self) -> None:
        """Unparseable output defaults to needs_changes."""
        reviewer = MagicMock(return_value="some rambling text with no clear verdict")

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?")

        assert result["consensus"] is True
        assert result["verdict"] == "needs_changes"

    def test_invalid_num_agents_clamped(self) -> None:
        """num_agents < 1 is clamped to 1."""
        reviewer = MagicMock(return_value="approve\nok")
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?", num_agents=0)

        assert result["consensus"] is True
        assert len(result["agent_votes"]) == 1

    def test_invalid_max_rounds_clamped(self) -> None:
        """max_rounds < 1 is clamped to 1."""
        reviewer = MagicMock(return_value="approve\nok")
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=0)

        assert result["consensus"] is True
        assert result["rounds"] == 1

    def test_partial_agreement_confidence(self) -> None:
        """2 of 3 agents agree but no consensus, tie result has correct confidence."""
        responses = ["approve\nok", "approve\nalso ok", "reject\nno"]
        response_idx = 0

        def two_of_three(prompt: str) -> str:
            nonlocal response_idx
            r = responses[response_idx % len(responses)]
            response_idx += 1
            return r

        engine = ConsensusEngine(reviewer=two_of_three)
        result = engine.run_debate("Is this correct?", max_rounds=1)

        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["confidence"] == 2 / 3

    def test_dissent_prompt_injected_in_round_two(self) -> None:
        """Round 2+ prompts include dissenting opinions from the prior round."""
        reviewer = MagicMock()
        reviewer.side_effect = [
            "approve\ninitial",
            "reject\ninitial",
            "needs_changes\ninitial",
            "approve\nchanged mind",
            "approve\nchanged mind",
            "approve\nchanged mind",
        ]

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Is this correct?", max_rounds=5)

        assert result["consensus"] is True
        assert result["rounds"] == 2
        round2_prompts = [c[0][0] for c in reviewer.call_args_list[3:]]
        for prompt in round2_prompts:
            assert "NOT unanimous" in prompt
