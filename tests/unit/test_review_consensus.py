"""Tests for review/consensus.py — ConsensusEngine debate orchestration,
verdict parsing, consensus detection, confidence computation, and tie-breaking."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from general_ludd.review.consensus import (
    ConsensusEngine,
    _build_dissent_prompt,
    _build_judge_prompt,
    _build_prompt,
    _check_consensus,
    _compute_confidence,
    _empty_question_result,
    _no_reviewer_result,
    _parse_verdict,
    _run_round,
    _tie_or_judge,
    _tie_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_reviewer_approve(_prompt: str) -> str:
    return "approve\nLooks good."


def _mock_reviewer_reject(_prompt: str) -> str:
    return "reject\nNeeds work."


def _mock_reviewer_needs_changes(_prompt: str) -> str:
    return "needs_changes\nMinor issues."


def _mock_reviewer_varied(agent_idx: int = 0) -> Callable[[str], str]:
    """Return a reviewer whose verdict depends on the input prompt."""

    def _review(prompt: str) -> str:
        idx = 0
        for line in prompt.splitlines():
            if line.startswith("You are reviewer agent "):
                idx = int(line.split()[4]) - 1
                break
        if idx == agent_idx:
            return "reject\nDissenting."
        return "approve\nLooks good."

    return _review


def _mock_reviewer_mixed() -> Callable[[str], str]:
    """2 agents approve, 1 rejects — no unanimity."""

    def _review(prompt: str) -> str:
        idx = 0
        for line in prompt.splitlines():
            if line.startswith("You are reviewer agent "):
                idx = int(line.split()[4]) - 1
                break
        if idx == 1:
            return "reject\nDissenting."
        return "approve\nLooks good to me."

    return _review


def _make_mock_votes(verdicts: list[str]) -> list[dict[str, Any]]:
    return [
        {"agent_index": i, "verdict": v, "rationale": f"reason {i}", "raw": v, "round": 1}
        for i, v in enumerate(verdicts)
    ]


# ---------------------------------------------------------------------------
# 1. Edge cases — initialization and degenerate inputs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_reviewer_configured(self):
        engine = ConsensusEngine()
        result = engine.run_debate("test question")
        assert result["consensus"] is False
        assert result["verdict"] == "error"
        assert result["confidence"] == 0.0
        assert result["rounds"] == 0
        assert result["transcript"] == []
        assert result["agent_votes"] == []
        assert result["error"] == "No reviewer configured"

    def test_empty_question(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("")
        assert result["consensus"] is False
        assert result["verdict"] == "error"
        assert result["confidence"] == 0.0
        assert result["error"] == "Question must not be empty"

    def test_whitespace_only_question(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("   \t\n  ")
        assert result["verdict"] == "error"

    def test_num_agents_zero_clamped_to_one(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", num_agents=0)
        assert result["rounds"] >= 1

    def test_num_agents_negative_clamped_to_one(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", num_agents=-5)
        assert result["rounds"] >= 1

    def test_max_rounds_zero_clamped_to_one(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", max_rounds=0)
        assert result["rounds"] == 1

    def test_max_rounds_negative_clamped_to_one(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", max_rounds=-3)
        assert result["rounds"] == 1


# ---------------------------------------------------------------------------
# 2. Consensus engine — unanimous verdict
# ---------------------------------------------------------------------------


class TestUnanimousConsensus:
    def test_all_approve_produces_consensus(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("Should we ship?", num_agents=3)
        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["confidence"] == 1.0
        assert result["rounds"] == 1
        assert len(result["agent_votes"]) == 3

    def test_all_reject_produces_consensus(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_reject)
        result = engine.run_debate("Should we ship?", num_agents=3)
        assert result["consensus"] is True
        assert result["verdict"] == "reject"
        assert result["confidence"] == 1.0

    def test_all_needs_changes_produces_consensus(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_needs_changes)
        result = engine.run_debate("Should we ship?", num_agents=3)
        assert result["consensus"] is True
        assert result["verdict"] == "needs_changes"

    def test_agent_votes_shape(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", num_agents=2)
        votes = result["agent_votes"]
        assert len(votes) == 2
        for i, v in enumerate(votes):
            assert v["agent_index"] == i
            assert v["verdict"] == "approve"
            assert "rationale" in v
            assert v["round"] == 1

    def test_transcript_shape(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_approve)
        result = engine.run_debate("q", num_agents=3)
        transcript = result["transcript"]
        assert len(transcript) == 1
        round_entry = transcript[0]
        assert round_entry["round"] == 1
        assert len(round_entry["votes"]) == 3


# ---------------------------------------------------------------------------
# 3. Consensus engine — deadlock and judge
# ---------------------------------------------------------------------------


class TestDeadlockAndJudge:
    def test_mixed_verdicts_no_judge_produces_tie(self):
        engine = ConsensusEngine(reviewer=_mock_reviewer_mixed())
        result = engine.run_debate("q", num_agents=3, max_rounds=5)
        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result.get("judge_ruling") is False

    def test_mixed_verdicts_with_judge_uses_judge(self):
        def _mock_judge(prompt: str) -> str:
            return "approve\nTie broken."

        engine = ConsensusEngine(reviewer=_mock_reviewer_mixed(), judge=_mock_judge)
        result = engine.run_debate("q", num_agents=3, max_rounds=5)
        assert result["consensus"] is False
        assert result["verdict"] in {"approve", "reject", "needs_changes"}
        assert result.get("judge_ruling") is True
        assert "judge_verdict" in result
        assert result["confidence"] == 0.5

    def test_judge_verdict_appears_in_result(self):
        def _mock_judge(prompt: str) -> str:
            return "reject\nUnsafe to ship."

        engine = ConsensusEngine(reviewer=_mock_reviewer_mixed(), judge=_mock_judge)
        result = engine.run_debate("q", num_agents=3, max_rounds=3)
        assert result["judge_verdict"] == "reject"
        assert "Unsafe to ship" in result["judge_rationale"]

    def test_max_rounds_limits_iterations(self):
        call_counts: list[int] = [0]

        def _counting_reviewer(prompt: str) -> str:
            call_counts[0] += 1
            idx = 0
            for line in prompt.splitlines():
                if line.startswith("You are reviewer agent "):
                    idx = int(line.split()[4]) - 1
                    break
            if idx == 0:
                return "approve\n"
            return "reject\n"

        engine = ConsensusEngine(reviewer=_counting_reviewer)
        result = engine.run_debate("q", num_agents=3, max_rounds=2)
        assert result["rounds"] == 2
        assert call_counts[0] == 6


# ---------------------------------------------------------------------------
# 4. _parse_verdict
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_approve_line(self):
        verdict, rationale = _parse_verdict("approve\nShip it.")
        assert verdict == "approve"
        assert rationale == "Ship it."

    def test_reject_line(self):
        verdict, rationale = _parse_verdict("reject\nNot ready.")
        assert verdict == "reject"
        assert rationale == "Not ready."

    def test_needs_changes_line(self):
        verdict, rationale = _parse_verdict("needs_changes\nFix tests.")
        assert verdict == "needs_changes"
        assert rationale == "Fix tests."

    def test_case_insensitive_verdict(self):
        verdict, _ = _parse_verdict("APPROVE\nok")
        assert verdict == "approve"

    def test_leading_whitespace_in_verdict(self):
        verdict, _ = _parse_verdict("  approve  \nok")
        assert verdict == "approve"

    def test_verdict_buried_in_prose_returns_default(self):
        verdict, _ = _parse_verdict("I think we should approve this.\nBecause reasons.")
        assert verdict == "needs_changes"

    def test_no_verdict_returns_needs_changes(self):
        verdict, _ = _parse_verdict("Just some prose\nNo verdict line.")
        assert verdict == "needs_changes"

    def test_empty_string(self):
        verdict, rationale = _parse_verdict("")
        assert verdict == "needs_changes"
        assert rationale == ""

    def test_only_whitespace(self):
        verdict, rationale = _parse_verdict("   ")
        assert verdict == "needs_changes"
        assert rationale == ""

    def test_multiline_with_verdict_not_first(self):
        # First non-verdict line, then "reject" — parser skips first line
        verdict, _ = _parse_verdict("not a verdict\nreject\nBecause.")
        assert verdict == "reject"

    def test_rationale_empty_single_line(self):
        verdict, rationale = _parse_verdict("approve")
        assert verdict == "approve"
        assert rationale == ""


# ---------------------------------------------------------------------------
# 5. _check_consensus
# ---------------------------------------------------------------------------


class TestCheckConsensus:
    def test_empty_votes_returns_none(self):
        assert _check_consensus([]) is None

    def test_single_vote_returns_its_verdict(self):
        votes = [{"verdict": "approve"}]
        assert _check_consensus(votes) == "approve"

    def test_all_same_verdict_returns_it(self):
        votes = _make_mock_votes(["approve", "approve", "approve"])
        assert _check_consensus(votes) == "approve"

    def test_one_differing_returns_none(self):
        votes = _make_mock_votes(["approve", "reject", "approve"])
        assert _check_consensus(votes) is None

    def test_all_different_returns_none(self):
        votes = _make_mock_votes(["approve", "reject", "needs_changes"])
        assert _check_consensus(votes) is None

    def test_verdicts_have_different_keys_ignored(self):
        votes = [
            {"verdict": "approve", "extra": 1},
            {"verdict": "approve", "extra": 2},
        ]
        assert _check_consensus(votes) == "approve"


# ---------------------------------------------------------------------------
# 6. _compute_confidence
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    def test_empty_votes_returns_zero(self):
        assert _compute_confidence([]) == 0.0

    def test_all_same_confidence_one(self):
        votes = _make_mock_votes(["approve", "approve"])
        assert _compute_confidence(votes) == 1.0

    def test_majority_split_2_of_3(self):
        votes = _make_mock_votes(["approve", "approve", "reject"])
        assert _compute_confidence(votes) == pytest.approx(2 / 3)

    def test_equal_split_2_2(self):
        votes = _make_mock_votes(["approve", "approve", "reject", "reject"])
        assert _compute_confidence(votes) == 0.5

    def test_all_unique_verdicts(self):
        votes = _make_mock_votes(["approve", "reject", "needs_changes"])
        assert _compute_confidence(votes) == pytest.approx(1 / 3)

    def test_five_agents_majority(self):
        votes = _make_mock_votes(["approve", "approve", "approve", "reject", "needs_changes"])
        assert _compute_confidence(votes) == pytest.approx(3 / 5)


# ---------------------------------------------------------------------------
# 7. _run_round
# ---------------------------------------------------------------------------


class TestRunRound:
    def test_returns_votes_and_transcript(self):
        votes, transcript = _run_round(_mock_reviewer_approve, "q", "", 3, 1, [])
        assert len(votes) == 3
        assert transcript["round"] == 1
        assert len(transcript["votes"]) == 3

    def test_each_vote_has_required_keys(self):
        votes, _ = _run_round(_mock_reviewer_approve, "q", "", 2, 1, [])
        for v in votes:
            assert "agent_index" in v
            assert "verdict" in v
            assert "rationale" in v
            assert "raw" in v
            assert "round" in v

    def test_agent_indexes_are_sequential(self):
        votes, _ = _run_round(_mock_reviewer_approve, "q", "", 4, 1, [])
        indexes = [v["agent_index"] for v in votes]
        assert indexes == [0, 1, 2, 3]

    def test_dissent_from_previous_round_passed_to_reviewer(self):
        calls: list[str] = []

        def _tracking_reviewer(prompt: str) -> str:
            calls.append(prompt)
            return "approve\nok"

        prev_transcript = {
            "round": 1,
            "votes": [
                {"agent_index": 0, "verdict": "approve"},
                {"agent_index": 1, "verdict": "reject"},
            ],
        }
        _run_round(_tracking_reviewer, "q", "", 3, 2, [prev_transcript])
        for call_text in calls:
            assert "the agents were NOT unanimous" in call_text
            assert "Agent 1: approve" in call_text
            assert "Agent 2: reject" in call_text

    def test_no_dissent_when_no_previous_rounds(self):
        calls: list[str] = []

        def _tracking_reviewer(prompt: str) -> str:
            calls.append(prompt)
            return "approve\nok"

        _run_round(_tracking_reviewer, "q", "", 3, 1, [])
        for call_text in calls:
            assert "the agents were NOT unanimous" not in call_text


# ---------------------------------------------------------------------------
# 8. _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_role_and_question(self):
        prompt = _build_prompt("Ship?", "", 0, 3, 1, "")
        assert "reviewer agent 1 of 3" in prompt
        assert "Ship?" in prompt
        assert "EXACTLY one verdict" in prompt

    def test_contains_context_when_provided(self):
        prompt = _build_prompt("Q", "Some context here", 0, 3, 1, "")
        assert "Context:" in prompt
        assert "Some context here" in prompt

    def test_no_context_section_when_empty(self):
        prompt = _build_prompt("Q", "", 0, 3, 1, "")
        assert "Context:" not in prompt

    def test_contains_dissent_in_later_rounds(self):
        prompt = _build_prompt("Q", "", 0, 3, 2, "Agent 1: reject")
        assert "the agents were NOT unanimous" in prompt
        assert "Agent 1: reject" in prompt

    def test_dissent_not_included_in_first_round(self):
        prompt = _build_prompt("Q", "", 0, 3, 1, "Agent 1: reject")
        assert "the agents were NOT unanimous" not in prompt

    def test_agent_numbering_is_one_based(self):
        prompt = _build_prompt("Q", "", 2, 5, 1, "")
        assert "reviewer agent 3 of 5" in prompt


# ---------------------------------------------------------------------------
# 9. _build_dissent_prompt
# ---------------------------------------------------------------------------


class TestBuildDissentPrompt:
    def test_empty_votes(self):
        result = _build_dissent_prompt({"votes": []})
        assert result == ""

    def test_no_votes_key(self):
        result = _build_dissent_prompt({})
        assert result == ""

    def test_single_agent(self):
        result = _build_dissent_prompt({"votes": [{"agent_index": 0, "verdict": "approve"}]})
        assert result == "Agent 1: approve"

    def test_multiple_agents(self):
        result = _build_dissent_prompt(
            {
                "votes": [
                    {"agent_index": 0, "verdict": "approve"},
                    {"agent_index": 1, "verdict": "reject"},
                    {"agent_index": 2, "verdict": "needs_changes"},
                ]
            }
        )
        lines = result.split("\n")
        assert len(lines) == 3
        assert "Agent 1: approve" in lines[0]
        assert "Agent 2: reject" in lines[1]
        assert "Agent 3: needs_changes" in lines[2]

    def test_missing_agent_index_defaults_to_neg_one(self):
        result = _build_dissent_prompt({"votes": [{"verdict": "approve"}]})
        assert "Agent 0" in result

    def test_missing_verdict_shows_unknown(self):
        result = _build_dissent_prompt({"votes": [{"agent_index": 5}]})
        assert "unknown" in result


# ---------------------------------------------------------------------------
# 10. _build_judge_prompt
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    def test_contains_question_and_votes(self):
        votes = _make_mock_votes(["approve", "reject"])
        prompt = _build_judge_prompt("Ship?", "", votes)
        assert "tie-breaking judge" in prompt
        assert "Ship?" in prompt
        assert "Agent 1: approve" in prompt
        assert "Agent 2: reject" in prompt

    def test_includes_context_when_provided(self):
        votes = _make_mock_votes(["approve"])
        prompt = _build_judge_prompt("Q", "ctx", votes)
        assert "Context:" in prompt
        assert "ctx" in prompt

    def test_no_context_section_when_empty(self):
        votes = _make_mock_votes(["approve"])
        prompt = _build_judge_prompt("Q", "", votes)
        assert "Context:" not in prompt

    def test_includes_rationale_in_vote_lines(self):
        votes = _make_mock_votes(["approve", "reject"])
        prompt = _build_judge_prompt("Q", "", votes)
        assert "reason 0" in prompt
        assert "reason 1" in prompt

    def test_exact_verdict_instruction_present(self):
        prompt = _build_judge_prompt("Q", "", _make_mock_votes(["approve"]))
        assert "EXACTLY one verdict" in prompt


# ---------------------------------------------------------------------------
# 11. _tie_result and _tie_or_judge
# ---------------------------------------------------------------------------


class TestTieResult:
    def test_tie_result_shape(self):
        votes = _make_mock_votes(["approve", "reject"])
        result = _tie_result(votes, [], 3)
        assert result["consensus"] is False
        assert result["verdict"] == "tie"
        assert result["judge_ruling"] is False

    def test_tie_result_confidence(self):
        votes = _make_mock_votes(["approve", "approve", "reject"])
        result = _tie_result(votes, [], 3)
        assert result["confidence"] == pytest.approx(2 / 3)

    def test_tie_result_agent_votes_preserved(self):
        votes = _make_mock_votes(["approve", "reject"])
        result = _tie_result(votes, [], 2)
        assert len(result["agent_votes"]) == 2

    def test_tie_or_judge_without_judge_calls_tie(self):
        votes = _make_mock_votes(["approve", "reject"])
        result = _tie_or_judge(None, votes, "q", "", [], 5)
        assert result["verdict"] == "tie"
        assert result.get("judge_ruling") is False

    def test_tie_or_judge_with_judge_calls_judge(self):
        def _mock_judge(prompt: str) -> str:
            return "approve\nFinal ruling."

        votes = _make_mock_votes(["approve", "reject"])
        result = _tie_or_judge(_mock_judge, votes, "q", "", [], 5)
        assert result["verdict"] == "approve"
        assert result.get("judge_ruling") is True
        assert result["confidence"] == 0.5

    def test_tie_result_empty_votes_confidence_zero(self):
        result = _tie_result([], [], 1)
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# 12. Static result functions
# ---------------------------------------------------------------------------


class TestStaticResults:
    def test_no_reviewer_result_shape(self):
        r = _no_reviewer_result()
        assert r["consensus"] is False
        assert r["verdict"] == "error"
        assert r["confidence"] == 0.0
        assert r["rounds"] == 0
        assert r["transcript"] == []
        assert r["agent_votes"] == []
        assert "error" in r

    def test_empty_question_result_shape(self):
        r = _empty_question_result()
        assert r["consensus"] is False
        assert r["verdict"] == "error"
        assert r["confidence"] == 0.0
        assert r["rounds"] == 0
        assert r["transcript"] == []
        assert r["agent_votes"] == []
        assert "error" in r


# ---------------------------------------------------------------------------
# 13. Context propagation
# ---------------------------------------------------------------------------


class TestContextPropagation:
    def test_context_appears_in_prompt(self):
        calls: list[str] = []

        def _tracking(prompt: str) -> str:
            calls.append(prompt)
            return "approve\nok"

        engine = ConsensusEngine(reviewer=_tracking)
        engine.run_debate("Should we ship?", context="prod environment", num_agents=1)
        assert any("prod environment" in c for c in calls)

    def test_context_passed_to_judge_in_deadlock(self):
        judge_calls: list[str] = []

        def _mock_reviewer(prompt: str) -> str:
            idx = 0
            for line in prompt.splitlines():
                if line.startswith("You are reviewer agent "):
                    idx = int(line.split()[4]) - 1
                    break
            return "approve" if idx == 0 else "reject"

        def _mock_judge(prompt: str) -> str:
            judge_calls.append(prompt)
            return "approve\nok"

        engine = ConsensusEngine(reviewer=_mock_reviewer, judge=_mock_judge)
        engine.run_debate("q", context="critical context", num_agents=2, max_rounds=1)
        assert any("critical context" in c for c in judge_calls)
