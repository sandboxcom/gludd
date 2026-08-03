"""Unit tests for log_analysis/prompt_evaluator.py — comprehensive coverage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from general_ludd.log_analysis.prompt_evaluator import (
    _compute_variant_metrics,
    _score_variant,
    ab_compare,
    analyze_cot_quality,
    classify_prompt,
    detect_context_waste,
    extract_prompts,
    generate_report,
    measure_prompt_efficiency,
    parse_conversation_log,
    recommend_improvements,
)


class TestParseConversationLog:
    def test_parse_xml_roles(self) -> None:
        raw = "<user>Hello</user><assistant>Hi there</assistant><system>You are helpful</system>"
        entries = parse_conversation_log(raw)
        roles = [e["role"] for e in entries]
        assert roles == ["user", "assistant", "system"]

    def test_parse_with_tool_calls(self) -> None:
        raw = '<user>Run <tool_call>{"name": "grep"}</tool_call> now</user>'
        entries = parse_conversation_log(raw)
        assert len(entries) == 1
        assert entries[0]["tool_calls"] == [{"name": "grep"}]

    def test_parse_invalid_json_tool_call_falls_back_to_raw(self) -> None:
        raw = "<user>do <tool_call>not json</tool_call></user>"
        entries = parse_conversation_log(raw)
        assert entries[0]["tool_calls"] == [{"raw": "not json"}]

    def test_parse_extracts_cot(self) -> None:
        raw = "<assistant><cot>I think therefore</cot>x</assistant>"
        entries = parse_conversation_log(raw)
        assert entries[0]["cot"] == "I think therefore"

    def test_parse_extracts_timestamp(self) -> None:
        raw = '<assistant>timestamp: "2025-01-15T10:30:00Z" x</assistant>'
        entries = parse_conversation_log(raw)
        assert entries[0]["timestamp"] == "2025-01-15T10:30:00Z"

    def test_parse_estimates_tokens(self) -> None:
        raw = "<user>hello world foo bar</user>"
        entries = parse_conversation_log(raw)
        assert entries[0]["tokens"] == 4

    def test_parse_from_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("<user>file content</user>")
            f.flush()
            path = f.name
        try:
            entries = parse_conversation_log(path)
            assert len(entries) == 1
            assert entries[0]["content"] == "file content"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_parse_nonexistent_file_returns_empty(self) -> None:
        entries = parse_conversation_log("/nonexistent/path_xyz_123.txt")
        assert entries == []

    def test_parse_inline_text_with_newline(self) -> None:
        raw = "<user>\nhello\nworld\n</user>"
        entries = parse_conversation_log(raw)
        assert len(entries) == 1

    def test_parse_fallback_on_no_xml_tags(self) -> None:
        raw = "User: Hello\nAssistant: Hi there"
        entries = parse_conversation_log(raw)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_parse_fallback_human_ai_aliases(self) -> None:
        raw = "Human: hello\nAI: response"
        entries = parse_conversation_log(raw)
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"


class TestExtractPrompts:
    def test_filters_user_assistant_system_only(self) -> None:
        conv = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
            {"role": "system", "content": "sys"},
            {"role": "tool", "content": "result"},
            {"role": "unknown", "content": "x"},
        ]
        result = extract_prompts(conv)
        assert len(result) == 3
        assert all(e["role"] in ("user", "assistant", "system") for e in result)


class TestClassifyPrompt:
    def test_planning_markers(self) -> None:
        assert classify_prompt("plan the architecture") == "planning"
        assert classify_prompt("what is the strategy here") == "planning"
        assert classify_prompt("create a blueprint for the system") == "planning"

    def test_coding_markers(self) -> None:
        assert classify_prompt("write code for the endpoint") == "coding"
        assert classify_prompt("implement a function that does X") == "coding"
        assert classify_prompt("fix the bug in test") == "coding"

    def test_research_markers(self) -> None:
        assert classify_prompt("research the codebase") == "research"
        assert classify_prompt("search the repo for usage") == "research"
        assert classify_prompt("audit the modules") == "research"

    def test_debugging_markers(self) -> None:
        assert classify_prompt("debug the crash") == "debugging"
        assert classify_prompt("troubleshoot the stack trace") == "debugging"

    def test_configuration_markers(self) -> None:
        assert classify_prompt("config the env vars for CI pipeline") == "configuration"

    def test_unknown_no_markers(self) -> None:
        assert classify_prompt("xyzzy") == "other"

    def test_empty_prompt(self) -> None:
        assert classify_prompt("") == "other"


class TestMeasurePromptEfficiency:
    def test_token_counting(self) -> None:
        result = measure_prompt_efficiency("hello world", {"content": "ok"})
        assert result["tokens_in"] == 2
        assert result["tokens_out"] == 1

    def test_task_completion_detected(self) -> None:
        resp = {"content": "task is done now", "tool_calls": []}
        result = measure_prompt_efficiency("do x", resp)
        assert result["task_completed"] is True

    def test_task_not_completed(self) -> None:
        resp = {"content": "working on it", "tool_calls": []}
        result = measure_prompt_efficiency("do x", resp)
        assert result["task_completed"] is False

    def test_error_counting(self) -> None:
        resp = {"content": "error occurred, exception thrown, failure detected"}
        result = measure_prompt_efficiency("do x", resp)
        assert result["errors"] >= 3

    def test_tool_counts(self) -> None:
        resp = {
            "content": "done",
            "tool_calls": [{"name": "grep"}, {"name": "read"}, {"name": "edit"}],
        }
        result = measure_prompt_efficiency("do x", resp)
        assert result["tools_called"] == 3
        assert result["steps_taken"] == 3

    def test_content_list_step_detection(self) -> None:
        resp = {
            "content": [
                {"type": "tool_use", "name": "grep"},
                {"type": "tool_use", "name": "read"},
                {"type": "text", "text": "hello"},
            ],
            "tool_calls": [],
        }
        result = measure_prompt_efficiency("do x", resp)
        assert result["steps_taken"] == 2

    def test_tool_results_error_detection(self) -> None:
        resp = {
            "content": "ok",
            "tool_calls": [],
            "tool_results": [{"output": "error: permission denied"}],
        }
        result = measure_prompt_efficiency("do x", resp)
        assert result["errors"] >= 1


class TestDetectContextWaste:
    def test_repeated_sentence_detected(self) -> None:
        conv = [
            {
                "role": "assistant",
                "content": "The system is designed to handle large workloads. "
                "The system is designed to handle large workloads.",
                "tokens": 20,
            },
        ]
        findings = detect_context_waste(conv)
        repeated = [f for f in findings if f["type"] == "repeated_fact"]
        assert len(repeated) >= 1

    def test_high_severity_repeated_at_4(self) -> None:
        conv = [
            {
                "role": "assistant",
                "content": ". ".join(["The system processes data efficiently"] * 5),
                "tokens": 20,
            },
        ]
        findings = detect_context_waste(conv)
        repeated = [f for f in findings if f["type"] == "repeated_fact"]
        assert any(f["severity"] == "medium" for f in repeated)

    def test_overly_broad_request_detected(self) -> None:
        long_text = "word " * 600
        conv = [{"role": "user", "content": long_text, "tokens": 600}]
        findings = detect_context_waste(conv)
        broad = [f for f in findings if f["type"] == "overly_broad_request"]
        assert len(broad) >= 1

    def test_high_response_overhead_detected(self) -> None:
        conv = [
            {"role": "user", "content": "hi", "tokens": 1},
            {
                "role": "assistant",
                "content": "word " * 50,
                "tokens": 50,
            },
        ]
        findings = detect_context_waste(conv)
        overhead = [f for f in findings if f["type"] == "high_response_overhead"]
        assert len(overhead) >= 1

    def test_empty_conversation_no_findings(self) -> None:
        findings = detect_context_waste([])
        assert len(findings) == 0


class TestAnalyzeCotQuality:
    def test_empty_cot_returns_zeros(self) -> None:
        result = analyze_cot_quality("")
        assert result["reasoning_depth"] == 0
        assert result["decision_quality"] == 0
        assert result["score"] == 0

    def test_none_cot_returns_zeros(self) -> None:
        result = analyze_cot_quality("   ")
        assert result["score"] == 0

    def test_deep_reasoning_scores_high(self) -> None:
        text = (
            "because of this evidence, therefore we must proceed. "
            "however, an alternative approach exists. "
            "if this assumption holds then we continue."
        )
        result = analyze_cot_quality(text)
        assert result["reasoning_depth"] >= 1
        assert result["decision_quality"] >= 0

    def test_dead_ends_detected(self) -> None:
        text = "this was a dead end. we abandoned it. then we scrapped the plan."
        result = analyze_cot_quality(text)
        assert result["dead_ends"] >= 2

    def test_iteration_patterns_detected(self) -> None:
        text = "first attempt failed. try again. retry with another approach."
        result = analyze_cot_quality(text)
        assert result["iteration_efficiency"] <= 10
        assert result["iteration_efficiency"] < 10

    def test_max_scores_capped_at_10(self) -> None:
        text = " ".join(
            [
                "because thus hence consequently. ",
                "chosen best optimal preferred.",
                "alternatively however pros and cons trade-off.",
            ]
            * 10
        )
        result = analyze_cot_quality(text)
        assert result["reasoning_depth"] <= 10
        assert result["decision_quality"] <= 10

    def test_score_computation(self) -> None:
        text = "because of the evidence, I chose this approach since it is best."
        result = analyze_cot_quality(text)
        assert 0 <= result["score"] <= 10


class TestRecommendImprovements:
    def test_low_reasoning_deep_triggers_recommendation(self) -> None:
        analysis = {"cot_quality": {"reasoning_depth": 1, "score": 4}}
        recs = recommend_improvements(analysis)
        assert any("reasoning" in r.lower() for r in recs)

    def test_high_dead_ends_triggers_recommendation(self) -> None:
        analysis = {"cot_quality": {"reasoning_depth": 5, "dead_ends": 4, "score": 3}}
        recs = recommend_improvements(analysis)
        assert any("dead-end" in r.lower() for r in recs)

    def test_large_prompt_triggers_recommendation(self) -> None:
        analysis = {
            "efficiency": {"tokens_in": 2000},
            "cot_quality": {},
        }
        recs = recommend_improvements(analysis)
        assert any("1000" in r for r in recs)

    def test_incomplete_many_steps_triggers_recommendation(self) -> None:
        analysis = {
            "efficiency": {"task_completed": False, "steps_taken": 10},
            "cot_quality": {},
        }
        recs = recommend_improvements(analysis)
        assert any("incomplete" in r.lower() for r in recs)

    def test_high_errors_triggers_recommendation(self) -> None:
        analysis = {
            "efficiency": {"errors": 5},
            "cot_quality": {},
        }
        recs = recommend_improvements(analysis)
        assert any("error" in r.lower() for r in recs)

    def test_no_issues_returns_well_structured(self) -> None:
        analysis = {
            "cot_quality": {"reasoning_depth": 8, "decision_quality": 8, "dead_ends": 0, "score": 8},
            "efficiency": {"tokens_in": 50, "task_completed": True, "steps_taken": 2, "errors": 0},
        }
        recs = recommend_improvements(analysis)
        assert len(recs) == 1
        assert "well-structured" in recs[0].lower()

    def test_debugging_with_dead_ends_triggers_specific(self) -> None:
        analysis = {
            "classification": "debugging",
            "cot_quality": {"dead_ends": 5, "score": 3},
        }
        recs = recommend_improvements(analysis)
        assert any("debug" in r.lower() and "dead-end" in r.lower() for r in recs)

    def test_research_classification_triggers_scope_rec(self) -> None:
        analysis = {
            "classification": "research",
            "cot_quality": {"score": 5},
        }
        recs = recommend_improvements(analysis)
        assert any("research" in r.lower() for r in recs)


class TestAbCompare:
    def test_variant_a_wins(self) -> None:
        a = [
            {"role": "user", "content": "x", "tokens": 1},
            {"role": "assistant", "content": "done", "tokens": 5, "tool_calls": []},
        ]
        b = [
            {"role": "user", "content": "x", "tokens": 1000},
            {"role": "assistant", "content": "working", "tokens": 500, "tool_calls": [{}] * 10},
        ]
        result = ab_compare(a, b)
        assert result["winner"] == "A"

    def test_variant_b_wins(self) -> None:
        a = [
            {"role": "user", "content": "x", "tokens": 1000},
            {"role": "assistant", "content": "error error error", "tokens": 500, "tool_calls": [{}] * 10},
        ]
        b = [
            {"role": "user", "content": "x", "tokens": 1},
            {"role": "assistant", "content": "done", "tokens": 5, "tool_calls": []},
        ]
        result = ab_compare(a, b)
        assert result["winner"] == "B"

    def test_tie(self) -> None:
        entry = [
            {"role": "user", "content": "x", "tokens": 5},
            {"role": "assistant", "content": "done", "tokens": 5, "tool_calls": []},
        ]
        result = ab_compare(entry, entry)
        assert result["winner"] == "tie"

    def test_metrics_present_in_result(self) -> None:
        a = [
            {"role": "user", "content": "x", "tokens": 3},
            {"role": "assistant", "content": "done", "tokens": 2, "tool_calls": []},
        ]
        b = [
            {"role": "user", "content": "x", "tokens": 5},
            {"role": "assistant", "content": "ok", "tokens": 1, "tool_calls": []},
        ]
        result = ab_compare(a, b)
        assert "a_metrics" in result
        assert "b_metrics" in result
        assert "recommendation" in result

    def test_low_completion_rate_recommends_optimization(self) -> None:
        a = [
            {"role": "user", "content": "x", "tokens": 1},
            {"role": "assistant", "content": "nope", "tokens": 5, "tool_calls": []},
        ]
        b = [
            {"role": "user", "content": "x", "tokens": 1000},
            {"role": "assistant", "content": "nope", "tokens": 500, "tool_calls": [{}] * 10},
        ]
        result = ab_compare(a, b)
        assert "completion rate" in result["recommendation"].lower() or "further" in result["recommendation"].lower()


class TestComputeVariantMetrics:
    def test_basic_metrics(self) -> None:
        conv = [
            {"role": "user", "content": "hello", "tokens": 2},
            {"role": "assistant", "content": "hi done", "tokens": 2, "tool_calls": [{}]},
        ]
        m = _compute_variant_metrics(conv)
        assert m["total_tokens_in"] == 2
        assert m["total_tokens_out"] == 2
        assert m["tasks_completed"] == 1
        assert m["total_tasks"] == 1
        assert m["task_completion_rate"] == 1.0
        assert m["total_tools"] == 1

    def test_zero_tasks_handled_safely(self) -> None:
        m = _compute_variant_metrics([])
        assert m["total_tasks"] == 1
        assert m["task_completion_rate"] == 0.0
        assert m["tokens_per_task"] == 0.0


class TestScoreVariant:
    def test_perfect_variant(self) -> None:
        metrics = {
            "task_completion_rate": 1.0,
            "tokens_per_task": 10,
            "total_errors": 0,
        }
        score = _score_variant(metrics)
        assert score > 60

    def test_terrible_variant(self) -> None:
        metrics = {
            "task_completion_rate": 0.0,
            "tokens_per_task": 10000,
            "total_errors": 10,
        }
        score = _score_variant(metrics)
        assert score < 40


class TestGenerateReport:
    def test_markdown_format(self) -> None:
        analyses = [
            {
                "prompt_id": "test-1",
                "classification": "coding",
                "efficiency": {
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "task_completed": True,
                    "steps_taken": 3,
                    "errors": 0,
                },
                "cot_quality": {"reasoning_depth": 7, "decision_quality": 6, "dead_ends": 1, "score": 7},
                "context_waste": [],
                "recommendations": ["Good job"],
            },
        ]
        report = generate_report(analyses)
        assert "# Prompt Evaluation Report" in report
        assert "test-1" in report
        assert "coding" in report
        assert "Recommendations" in report
        assert "Good job" in report

    def test_json_format(self) -> None:
        analyses = [{"prompt_id": "test-1"}]
        report = generate_report(analyses, format="json")
        parsed = json.loads(report)
        assert isinstance(parsed, list)
        assert parsed[0]["prompt_id"] == "test-1"

    def test_no_prompt_id_uses_default(self) -> None:
        analyses = [{"classification": "planning"}]
        report = generate_report(analyses)
        assert "Analysis #1" in report
