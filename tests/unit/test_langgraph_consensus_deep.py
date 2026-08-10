"""Deep tests for langgraph_consensus — pure functions, AgentVerdict model, edge cases."""

from __future__ import annotations

import pytest

from general_ludd.review.langgraph_consensus import (
    AgentVerdict,
    _build_agent_prompt,
    _build_judge_prompt,
    _build_transcript,
    _check_unanimity,
    _compute_confidence,
    _empty_question_result,
    _no_reviewer_result,
    _parse_judge_output,
    _parse_to_verdict,
)


class TestAgentVerdict:
    def test_construction_minimal(self):
        v = AgentVerdict(agent_index=0, verdict="approve", rationale="ok", round_num=1)
        assert v.agent_index == 0
        assert v.verdict == "approve"
        assert v.rationale == "ok"
        assert v.round_num == 1

    def test_model_dump_shape(self):
        v = AgentVerdict(agent_index=2, verdict="reject", rationale="bad code", round_num=3)
        d = v.model_dump()
        assert d == {
            "agent_index": 2,
            "verdict": "reject",
            "rationale": "bad code",
            "round_num": 3,
        }

    def test_empty_rationale(self):
        v = AgentVerdict(agent_index=0, verdict="approve", rationale="", round_num=1)
        assert v.rationale == ""

    def test_high_agent_index(self):
        v = AgentVerdict(agent_index=99, verdict="needs_changes", rationale="r", round_num=10)
        assert v.agent_index == 99


class TestParseToVerdict:
    def test_approve_verdict(self):
        raw = "approve\nLooks great, no issues found."
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "approve"
        assert v.agent_index == 0
        assert v.round_num == 1
        assert v.rationale == "Looks great, no issues found."

    def test_reject_verdict(self):
        raw = "reject\nMultiple bugs in the code."
        v = _parse_to_verdict(raw, agent_index=1, round_num=2)
        assert v.verdict == "reject"
        assert v.rationale == "Multiple bugs in the code."

    def test_needs_changes_verdict(self):
        raw = "needs_changes\nMissing edge case handling."
        v = _parse_to_verdict(raw, agent_index=2, round_num=1)
        assert v.verdict == "needs_changes"
        assert v.rationale == "Missing edge case handling."

    def test_defaults_to_needs_changes_when_unrecognized(self):
        raw = "maybe_later\nI am not sure about this."
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "needs_changes"

    def test_ignores_whitespace_around_verdict(self):
        raw = "  approve  \nRationale here."
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "approve"

    def test_case_insensitive_verdict(self):
        raw = "APPROVE\nLooks good."
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "approve"

    def test_verdict_on_second_line_found(self):
        raw = "intro\napprove\ngood"
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "approve"

    def test_single_line_no_rationale(self):
        raw = "approve"
        v = _parse_to_verdict(raw, agent_index=3, round_num=5)
        assert v.verdict == "approve"
        assert v.rationale == ""

    def test_multiline_rationale(self):
        raw = "reject\nLine 1.\nLine 2.\nLine 3."
        v = _parse_to_verdict(raw, agent_index=0, round_num=1)
        assert v.verdict == "reject"
        assert v.rationale == "Line 1.\nLine 2.\nLine 3."

    def test_empty_string(self):
        v = _parse_to_verdict("", agent_index=0, round_num=1)
        assert v.verdict == "needs_changes"
        assert v.rationale == ""

    def test_whitespace_only(self):
        v = _parse_to_verdict("   \n  \n  ", agent_index=0, round_num=1)
        assert v.verdict == "needs_changes"


class TestBuildAgentPrompt:
    def test_basic_structure(self):
        prompt = _build_agent_prompt("review this?", "some context", 0, 3, 1, "")
        assert "reviewer agent 1 of 3" in prompt
        assert "some context" in prompt
        assert "review this?" in prompt
        assert "EXACTLY one verdict" in prompt

    def test_excludes_dissent_on_first_round(self):
        prompt = _build_agent_prompt("q", "ctx", 1, 3, 1, "Agent 1: reject")
        assert "NOT unanimous" not in prompt

    def test_includes_dissent_after_first_round(self):
        prompt = _build_agent_prompt("q", "ctx", 0, 3, 2, "Agent 1: reject")
        assert "NOT unanimous" in prompt
        assert "Agent 1: reject" in prompt

    def test_no_dissent_summary_skips_dissent_block(self):
        prompt = _build_agent_prompt("q", "ctx", 0, 3, 2, "")
        assert "NOT unanimous" not in prompt

    def test_empty_context_omitted(self):
        prompt = _build_agent_prompt("q", "", 0, 2, 1, "")
        assert "Context:" not in prompt

    def test_agent_index_starts_at_zero(self):
        prompt = _build_agent_prompt("q", "ctx", 3, 5, 1, "")
        assert "agent 4 of 5" in prompt


class TestCheckUnanimity:
    def test_all_agree_returns_verdict(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
        ]
        assert _check_unanimity(verdicts) == "approve"

    def test_single_agent_is_unanimous(self):
        assert _check_unanimity([{"verdict": "reject"}]) == "reject"

    def test_dissent_returns_none(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "reject"},
            {"verdict": "approve"},
        ]
        assert _check_unanimity(verdicts) is None

    def test_many_same_one_differ_returns_none(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "needs_changes"},
        ]
        assert _check_unanimity(verdicts) is None

    def test_empty_list_returns_none(self):
        assert _check_unanimity([]) is None

    def test_all_needs_changes_is_unanimous(self):
        verdicts = [{"verdict": "needs_changes"}, {"verdict": "needs_changes"}]
        assert _check_unanimity(verdicts) == "needs_changes"

    def test_mixed_many_differ(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "reject"},
            {"verdict": "needs_changes"},
        ]
        assert _check_unanimity(verdicts) is None


class TestComputeConfidence:
    def test_unanimous_is_1_0(self):
        verdicts = [{"verdict": "approve"}, {"verdict": "approve"}, {"verdict": "approve"}]
        assert _compute_confidence(verdicts) == pytest.approx(1.0)

    def test_two_of_three(self):
        verdicts = [{"verdict": "approve"}, {"verdict": "approve"}, {"verdict": "reject"}]
        assert _compute_confidence(verdicts) == pytest.approx(2.0 / 3.0)

    def test_three_way_tie(self):
        verdicts = [{"verdict": "approve"}, {"verdict": "reject"}, {"verdict": "needs_changes"}]
        assert _compute_confidence(verdicts) == pytest.approx(1.0 / 3.0)

    def test_single_agent(self):
        assert _compute_confidence([{"verdict": "approve"}]) == pytest.approx(1.0)

    def test_empty_list_returns_zero(self):
        assert _compute_confidence([]) == 0.0

    def test_five_agents_majority_three(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "reject"},
            {"verdict": "needs_changes"},
        ]
        assert _compute_confidence(verdicts) == pytest.approx(0.6)

    def test_many_with_one_outlier(self):
        verdicts = [
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "reject"},
        ]
        assert _compute_confidence(verdicts) == pytest.approx(5.0 / 6.0)


class TestBuildTranscript:
    def test_empty_verdicts(self):
        assert _build_transcript([]) == []

    def test_single_round_three_agents(self):
        verdicts = [
            {"agent_index": 0, "verdict": "approve", "round_num": 1},
            {"agent_index": 1, "verdict": "approve", "round_num": 1},
            {"agent_index": 2, "verdict": "approve", "round_num": 1},
        ]
        result = _build_transcript(verdicts)
        assert len(result) == 1
        assert result[0]["round"] == 1
        assert len(result[0]["votes"]) == 3
        assert result[0]["votes"][0]["agent_index"] == 0
        assert result[0]["votes"][0]["verdict"] == "approve"

    def test_multi_round_grouping(self):
        verdicts = [
            {"agent_index": 0, "verdict": "approve", "round_num": 1},
            {"agent_index": 1, "verdict": "reject", "round_num": 1},
            {"agent_index": 0, "verdict": "needs_changes", "round_num": 2},
            {"agent_index": 1, "verdict": "needs_changes", "round_num": 2},
        ]
        result = _build_transcript(verdicts)
        assert len(result) == 2
        assert result[0]["round"] == 1
        assert result[1]["round"] == 2
        assert len(result[0]["votes"]) == 2
        assert len(result[1]["votes"]) == 2

    def test_rounds_sorted(self):
        verdicts = [
            {"agent_index": 0, "verdict": "reject", "round_num": 3},
            {"agent_index": 0, "verdict": "approve", "round_num": 1},
            {"agent_index": 0, "verdict": "needs_changes", "round_num": 2},
        ]
        result = _build_transcript(verdicts)
        assert [e["round"] for e in result] == [1, 2, 3]

    def test_missing_round_num_defaults_zero(self):
        verdicts = [{"agent_index": 0, "verdict": "approve"}]
        result = _build_transcript(verdicts)
        assert result[0]["round"] == 0

    def test_missing_verdict_defaults_empty(self):
        verdicts = [{"agent_index": 0, "round_num": 1}]
        result = _build_transcript(verdicts)
        assert result[0]["votes"][0]["verdict"] == ""


class TestBuildJudgePrompt:
    def test_includes_question(self):
        prompt = _build_judge_prompt({"question": "is this good?"}, [])  # pyright: ignore[reportArgumentType]
        assert "is this good?" in prompt

    def test_includes_context_when_present(self):
        prompt = _build_judge_prompt({"question": "q", "context": "ctx"}, [])  # pyright: ignore[reportArgumentType]
        assert "ctx" in prompt

    def test_empty_context_omitted(self):
        prompt = _build_judge_prompt({"question": "q", "context": ""}, [])  # pyright: ignore[reportArgumentType]
        assert "Context:" not in prompt

    def test_missing_context_omitted(self):
        prompt = _build_judge_prompt({"question": "q"}, [])  # pyright: ignore[reportArgumentType]
        assert "Context:" not in prompt

    def test_includes_agent_votes(self):
        verdicts = [
            {"agent_index": 0, "verdict": "approve", "rationale": "good"},
            {"agent_index": 1, "verdict": "reject", "rationale": "bad"},
        ]
        prompt = _build_judge_prompt({"question": "q"}, verdicts)  # pyright: ignore[reportArgumentType]
        assert "Agent 1: approve — good" in prompt
        assert "Agent 2: reject — bad" in prompt

    def test_judge_instructions_present(self):
        prompt = _build_judge_prompt({"question": "q"}, [])  # pyright: ignore[reportArgumentType]
        assert "tie-breaking judge" in prompt
        assert "EXACTLY one verdict" in prompt

    def test_vote_missing_rationale_defaults_empty(self):
        verdicts = [{"agent_index": 0, "verdict": "approve"}]
        prompt = _build_judge_prompt({"question": "q"}, verdicts)  # pyright: ignore[reportArgumentType]
        assert "Agent 1: approve — " in prompt


class TestParseJudgeOutput:
    def test_approve_output(self):
        verdict, rationale = _parse_judge_output("approve\nAfter careful review.")
        assert verdict == "approve"
        assert rationale == "After careful review."

    def test_reject_output(self):
        verdict, _rationale = _parse_judge_output("reject\nNot satisfactory.")
        assert verdict == "reject"

    def test_defaults_to_needs_changes(self):
        verdict, _rationale = _parse_judge_output("unclear\nWhat is this?")
        assert verdict == "needs_changes"

    def test_case_insensitive(self):
        verdict, _ = _parse_judge_output("APPROVE\nOK.")
        assert verdict == "approve"

    def test_single_line_no_rationale(self):
        verdict, rationale = _parse_judge_output("reject")
        assert verdict == "reject"
        assert rationale == ""

    def test_whitespace_around_verdict(self):
        verdict, _ = _parse_judge_output("  needs_changes  \nMore work.")
        assert verdict == "needs_changes"


class TestErrorResults:
    def test_no_reviewer_shape(self):
        r = _no_reviewer_result()
        assert r["consensus"] is False
        assert r["verdict"] == "error"
        assert r["confidence"] == 0.0
        assert r["rounds"] == 0
        assert r["transcript"] == []
        assert r["agent_votes"] == []
        assert "No reviewer configured" in r["error"]

    def test_empty_question_shape(self):
        r = _empty_question_result()
        assert r["consensus"] is False
        assert r["verdict"] == "error"
        assert r["confidence"] == 0.0
        assert r["rounds"] == 0
        assert "Question must not be empty" in r["error"]


class TestLangGraphConsensusEngineNoReviewer:
    def test_no_reviewer_returns_error(self):
        from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine

        engine = LangGraphConsensusEngine(reviewer_callable=None)
        result = engine.run_debate(question="Is this good?")
        assert result["verdict"] == "error"
        assert result["confidence"] == 0.0

    def test_empty_question_returns_error(self, mock_reviewer):
        from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine

        engine = LangGraphConsensusEngine(reviewer_callable=mock_reviewer)
        result = engine.run_debate(question="   ")
        assert result["verdict"] == "error"
        assert "Question must not be empty" in result["error"]

    def test_clamps_negative_num_agents(self, mock_reviewer):
        from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine

        engine = LangGraphConsensusEngine(reviewer_callable=mock_reviewer)
        result = engine.run_debate(question="ok?", num_agents=-1)
        assert result["verdict"] != "error"

    def test_clamps_zero_max_rounds(self, mock_reviewer):
        from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine

        engine = LangGraphConsensusEngine(reviewer_callable=mock_reviewer)
        result = engine.run_debate(question="ok?", max_rounds=0)
        assert result["verdict"] != "error"


@pytest.fixture
def mock_reviewer():
    def _call(prompt: str) -> str:
        return "approve\nLooks good."

    return _call
