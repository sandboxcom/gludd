"""Structural tests for src/general_ludd/log_analysis/prompt_evaluator.py."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from general_ludd.log_analysis.prompt_evaluator import (
    _build_fallback_entry,
    _estimate_tokens,
    _parse_fallback,
    _try_parse_json,
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

# ---------------------------------------------------------------------------
# parse_conversation_log
# ---------------------------------------------------------------------------


class TestParseConversationLog:
    def test_parses_basic_user_assistant_exchange(self) -> None:
        raw = "<user>Hello, world!</user><assistant>Hi there!</assistant>"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False
        ) as f:
            f.write(raw)
            f.flush()
            entries = parse_conversation_log(f.name)
        Path(f.name).unlink()

        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "Hello, world!"
        assert entries[1]["role"] == "assistant"
        assert entries[1]["content"] == "Hi there!"

    def test_parses_xml_string_directly(self) -> None:
        raw = "<user>How do I fix this?</user>\n"
        entries = parse_conversation_log(raw)
        assert len(entries) == 1
        assert entries[0]["role"] == "user"

    def test_parses_single_line_xml_string_directly(self) -> None:
        raw = "<user>Hello</user><assistant>Hi</assistant>"
        entries = parse_conversation_log(raw)
        assert [entry["role"] for entry in entries] == ["user", "assistant"]

    def test_parses_single_line_fallback_string_directly(self) -> None:
        entries = parse_conversation_log("User: Hello")
        assert len(entries) == 1
        assert entries[0]["role"] == "user"

    def test_extracts_tool_calls(self) -> None:
        raw = "<assistant><tool_call>{\"name\":\"read\"}</tool_call>\n</assistant>\n"
        entries = parse_conversation_log(raw)
        assert len(entries[0]["tool_calls"]) == 1
        assert entries[0]["tool_calls"][0]["name"] == "read"

    def test_extracts_malformed_tool_call_as_raw(self) -> None:
        raw = "<assistant><tool_call>not json</tool_call>\n</assistant>\n"
        entries = parse_conversation_log(raw)
        assert len(entries[0]["tool_calls"]) == 1
        assert entries[0]["tool_calls"][0] == {"raw": "not json"}

    def test_extracts_cot(self) -> None:
        raw = "<assistant><cot>I should use a regex here</cot>some text\n</assistant>\n"
        entries = parse_conversation_log(raw)
        assert entries[0]["cot"] == "I should use a regex here"

    def test_extracts_timestamp(self) -> None:
        raw = '<assistant>Timestamp: "2024-01-15T10:30:00Z" message\n</assistant>\n'
        entries = parse_conversation_log(raw)
        assert entries[0]["timestamp"] == "2024-01-15T10:30:00Z"

    def test_stores_estimated_tokens(self) -> None:
        raw = "<user>one two three four five\n</user>\n"
        entries = parse_conversation_log(raw)
        assert entries[0]["tokens"] >= 5

    def test_falls_back_on_no_xml_tags(self) -> None:
        raw = "User: Hello\nAssistant: Hi there\n"
        entries = parse_conversation_log(raw)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_empty_input_returns_empty_list(self) -> None:
        entries = parse_conversation_log("")
        assert entries == []

    def test_missing_file_returns_empty_list(self) -> None:
        entries = parse_conversation_log("/nonexistent/path/no/such/file.xml")
        assert entries == []

    def test_system_role_parsed(self) -> None:
        raw = "<system>You are a helpful coding assistant.\n</system>\n"
        entries = parse_conversation_log(raw)
        assert len(entries) == 1
        assert entries[0]["role"] == "system"

    def test_entry_has_all_default_fields(self) -> None:
        raw = "<user>test\n</user>\n"
        entries = parse_conversation_log(raw)
        entry = entries[0]
        assert set(entry.keys()) == {
            "role", "content", "tool_calls", "cot", "timestamp", "tokens",
        }
        assert entry["tool_calls"] == []
        assert entry["cot"] == ""
        assert entry["timestamp"] is None

    def test_multiple_tool_calls_in_one_entry(self) -> None:
        raw = (
            "<assistant>\n"
            "<tool_call>{\"name\":\"read\"}</tool_call>"
            "<tool_call>{\"name\":\"grep\"}</tool_call>"
            "\n</assistant>\n"
        )
        entries = parse_conversation_log(raw)
        assert len(entries[0]["tool_calls"]) == 2

    def test_content_with_xml_characters(self) -> None:
        raw = "<user>Is 2 &lt; 3 true?\n</user>\n"
        entries = parse_conversation_log(raw)
        assert len(entries) == 1
        assert "Is 2" in entries[0]["content"]
        assert entries[0]["role"] == "user"


# ---------------------------------------------------------------------------
# _try_parse_json
# ---------------------------------------------------------------------------


class TestTryParseJson:
    def test_valid_json_dict_returns_dict(self) -> None:
        result = _try_parse_json('{"a": 1, "b": "two"}')
        assert result == {"a": 1, "b": "two"}

    def test_valid_json_list_returns_none(self) -> None:
        result = _try_parse_json("[1, 2, 3]")
        assert result is None

    def test_valid_json_string_returns_none(self) -> None:
        result = _try_parse_json('"just a string"')
        assert result is None

    def test_valid_json_number_returns_none(self) -> None:
        result = _try_parse_json("42")
        assert result is None

    def test_invalid_json_returns_none(self) -> None:
        result = _try_parse_json("not json at all")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = _try_parse_json("")
        assert result is None

    def test_none_input_returns_none(self) -> None:
        result = _try_parse_json(None)  # type: ignore[arg-type]
        assert result is None

    def test_nested_json_returns_dict(self) -> None:
        result = _try_parse_json('{"outer": {"inner": [1, 2]}}')
        assert isinstance(result, dict)
        assert result["outer"]["inner"] == [1, 2]

    def test_trailing_comma_json_returns_none(self) -> None:
        result = _try_parse_json('{"a": 1,}')
        assert result is None


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_text_returns_zero(self) -> None:
        assert _estimate_tokens("") == 0

    def test_single_word_returns_one(self) -> None:
        assert _estimate_tokens("hello") == 1

    def test_multiple_words(self) -> None:
        assert _estimate_tokens("hello world again") == 3

    def test_punctuation_not_counted_as_words(self) -> None:
        text = "hello, world! this: \"is\" a test."
        assert _estimate_tokens(text) == 6

    def test_code_snippet_counts_identifiers(self) -> None:
        text = "def foo(x: int) -> str:\n    return str(x)"
        assert _estimate_tokens(text) == 8  # def foo x int str return str x

    def test_underscore_words_counted(self) -> None:
        assert _estimate_tokens("some_long_identifier_name") == 1

    def test_hyphenated_words(self) -> None:
        assert _estimate_tokens("pre-commit-hook") == 3  # pre commit hook

    def test_numbers_counted_as_tokens(self) -> None:
        assert _estimate_tokens("123 456 789") == 3

    def test_newlines_and_whitespace(self) -> None:
        text = "\n\n  word1  \n  word2  \n\n"
        assert _estimate_tokens(text) == 2

    def test_code_block_with_backticks(self) -> None:
        text = "```python\nprint('hello')\n```"
        assert _estimate_tokens(text) == 3  # python print hello


# ---------------------------------------------------------------------------
# _parse_fallback
# ---------------------------------------------------------------------------


class TestParseFallback:
    def test_parses_user_assistant_prefixes(self) -> None:
        raw = "User: Hello there\nAssistant: Hi back\n"
        entries = _parse_fallback(raw)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_case_insensitive_prefixes(self) -> None:
        raw = "USER: cmd line\nASSISTANT: response\n"
        entries = _parse_fallback(raw)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_maps_human_to_user(self) -> None:
        raw = "Human: What is this?\n"
        entries = _parse_fallback(raw)
        assert entries[0]["role"] == "user"

    def test_maps_ai_to_assistant(self) -> None:
        raw = "AI: This is a test.\n"
        entries = _parse_fallback(raw)
        assert entries[0]["role"] == "assistant"

    def test_system_prefix(self) -> None:
        raw = "System: You are a helpful bot.\n"
        entries = _parse_fallback(raw)
        assert entries[0]["role"] == "system"

    def test_multiline_content_preserved(self) -> None:
        raw = "User: Line one\nLine two\nLine three\nAssistant: Response\n"
        entries = _parse_fallback(raw)
        assert len(entries) == 2
        assert "Line one" in entries[0]["content"]
        assert "Line two" in entries[0]["content"]

    def test_no_prefix_yields_unknown_role(self) -> None:
        raw = "some plain text without prefixes\n"
        entries = _parse_fallback(raw)
        assert len(entries) == 1
        assert entries[0]["role"] == "unknown"

    def test_colon_prefix_variant(self) -> None:
        raw = "User> First message\nAssistant> Response\n"
        entries = _parse_fallback(raw)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"

    def test_empty_raw_returns_empty(self) -> None:
        assert _parse_fallback("") == []

    def test_fallback_entry_has_required_keys(self) -> None:
        raw = "User: test\n"
        entries = _parse_fallback(raw)
        entry = entries[0]
        assert set(entry.keys()) == {
            "role", "content", "tool_calls", "cot", "timestamp", "tokens",
        }


# ---------------------------------------------------------------------------
# extract_prompts
# ---------------------------------------------------------------------------


class TestExtractPrompts:
    def test_filters_out_unknown_role(self) -> None:
        conv: list[dict[str, Any]] = [
            {"role": "user", "content": "hi"},
            {"role": "unknown", "content": "???"},
            {"role": "assistant", "content": "ok"},
        ]
        result = extract_prompts(conv)
        assert len(result) == 2
        roles = {e["role"] for e in result}
        assert roles == {"user", "assistant"}

    def test_preserves_system_role(self) -> None:
        conv = [{"role": "system", "content": "sys msg"}]
        result = extract_prompts(conv)
        assert len(result) == 1

    def test_empty_conv_returns_empty(self) -> None:
        assert extract_prompts([]) == []


# ---------------------------------------------------------------------------
# classify_prompt
# ---------------------------------------------------------------------------


class TestClassifyPrompt:
    def test_planning_prompt(self) -> None:
        assert classify_prompt("write a plan for the new feature") == "planning"

    def test_coding_prompt(self) -> None:
        assert classify_prompt("write a function that implements sorting") == "coding"

    def test_research_prompt(self) -> None:
        result = classify_prompt("research the codebase for all endpoint patterns")
        assert result == "research"

    def test_debugging_prompt(self) -> None:
        assert classify_prompt("debug the crash in the daemon") == "debugging"

    def test_configuration_prompt(self) -> None:
        assert classify_prompt("setup the CI pipeline config") == "configuration"

    def test_unknown_returns_other(self) -> None:
        assert classify_prompt("xyzzy flurbo wibble") == "other"

    def test_empty_string_returns_other(self) -> None:
        assert classify_prompt("") == "other"

    def test_whitespace_only_returns_other(self) -> None:
        assert classify_prompt("   ") == "other"


# ---------------------------------------------------------------------------
# measure_prompt_efficiency
# ---------------------------------------------------------------------------


class TestMeasurePromptEfficiency:
    def test_basic_measurement(self) -> None:
        prompt = "Implement a function to sort a list"
        response: dict[str, Any] = {
            "content": "I have completed the implementation. All tests pass.",
            "tool_calls": [{"name": "edit"}, {"name": "bash"}],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["tokens_in"] == 7
        assert result["tokens_out"] == 8
        assert result["tools_called"] == 2
        assert result["task_completed"] is True

    def test_task_not_completed(self) -> None:
        prompt = "do stuff"
        response: dict[str, Any] = {
            "content": "I am not sure how to proceed.",
            "tool_calls": [],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["task_completed"] is False

    def test_detects_errors(self) -> None:
        prompt = "fix the error"
        response: dict[str, Any] = {
            "content": "There was an error and a failure during execution.",
            "tool_calls": [],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["errors"] >= 2

    def test_checkbox_pattern_completion(self) -> None:
        prompt = "task"
        response: dict[str, Any] = {"content": "[x] completed item", "tool_calls": []}
        result = measure_prompt_efficiency(prompt, response)
        assert result["task_completed"] is True

    def test_tool_results_in_error_count(self) -> None:
        prompt = "run test"
        response: dict[str, Any] = {
            "content": "running",
            "tool_calls": [],
            "tool_results": [{"output": "Error: permission denied"}],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["errors"] >= 2  # error + denied

    def test_content_as_list_with_tool_use(self) -> None:
        prompt = "edit file"
        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "name": "edit"},
                {"type": "text", "text": "done"},
            ],
            "tool_calls": [],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["steps_taken"] >= 1


# ---------------------------------------------------------------------------
# detect_context_waste
# ---------------------------------------------------------------------------


class TestDetectContextWaste:
    def test_detects_repeated_sentences(self) -> None:
        conv: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "content": "This is a repeated sentence. Another sentence.",
                "tokens": 10,
            },
            {
                "role": "assistant",
                "content": "This is a repeated sentence. Yet another.",
                "tokens": 10,
            },
        ]
        findings = detect_context_waste(conv)
        assert any(f["type"] == "repeated_fact" for f in findings)

    def test_no_waste_in_short_conv(self) -> None:
        conv: list[dict[str, Any]] = [
            {"role": "user", "content": "hi", "tokens": 1},
            {"role": "assistant", "content": "hello", "tokens": 1},
        ]
        findings = detect_context_waste(conv)
        assert len(findings) == 0

    def test_overly_broad_request_detected(self) -> None:
        long_prompt = "word " * 600
        conv: list[dict[str, Any]] = [
            {"role": "user", "content": long_prompt, "tokens": 600},
            {"role": "assistant", "content": "ok", "tokens": 1},
        ]
        findings = detect_context_waste(conv)
        assert any(f["type"] == "overly_broad_request" for f in findings)

    def test_high_response_overhead_detected(self) -> None:
        conv: list[dict[str, Any]] = [
            {"role": "user", "content": "short", "tokens": 1},
            {
                "role": "assistant",
                "content": "verbose " * 200,
                "tokens": 200,
            },
        ]
        findings = detect_context_waste(conv)
        assert any(f["type"] == "high_response_overhead" for f in findings)

    def test_empty_conv_returns_empty(self) -> None:
        assert detect_context_waste([]) == []


# ---------------------------------------------------------------------------
# analyze_cot_quality
# ---------------------------------------------------------------------------


class TestAnalyzeCotQuality:
    def test_empty_cot_returns_zeroes(self) -> None:
        result = analyze_cot_quality("")
        assert result["score"] == 0
        assert result["reasoning_depth"] == 0

    def test_none_returns_zeroes(self) -> None:
        analyze_cot_quality("")
        result2 = analyze_cot_quality("   ")
        assert result2["score"] == 0

    def test_deep_reasoning_scores_high(self) -> None:
        cot = (
            "Because the test failed, therefore we need to refactor. "
            "However, there is an alternative approach. "
            "The assumption was wrong, as shown by the test evidence. "
            "If we fix the bug then the gate should pass. "
            "The trade-off is increased complexity."
        )
        result = analyze_cot_quality(cot)
        assert result["reasoning_depth"] > 1
        assert result["score"] > 0

    def test_good_decisions_score_high(self) -> None:
        cot = (
            "I chose the regex approach because it is the best option. "
            "We should use this method since it is clearly the optimal path. "
            "I decided to use pytest because it is preferable."
        )
        result = analyze_cot_quality(cot)
        assert result["decision_quality"] > 1

    def test_dead_ends_detected(self) -> None:
        cot = (
            "I hit a dead-end with approach A and abandoned it. "
            "Back to the drawing board. I pivoted to approach B. "
            "I made a wrong assumption about the code structure."
        )
        result = analyze_cot_quality(cot)
        assert result["dead_ends"] >= 3

    def test_iteration_efficiency_lowers_with_many_retries(self) -> None:
        cot = (
            "First try failed. Second attempt also failed. "
            "Try number three. Again, try again. Yet another different approach."
            "Iteration 1 failed. Iteration 2 failed."
        )
        result = analyze_cot_quality(cot)
        assert result["iteration_efficiency"] < 10

    def test_score_bounded_below_10(self) -> None:
        cotton = analyze_cot_quality("because " * 100)
        assert 0 <= cotton["score"] <= 10


# ---------------------------------------------------------------------------
# recommend_improvements
# ---------------------------------------------------------------------------


class TestRecommendImprovements:
    def test_shallow_reasoning_recommendation(self) -> None:
        analysis = {"cot_quality": {"reasoning_depth": 1}}
        recs = recommend_improvements(analysis)
        assert any("Deepen reasoning" in r for r in recs)

    def test_low_decision_quality_recommendation(self) -> None:
        analysis = {"cot_quality": {"decision_quality": 1}}
        recs = recommend_improvements(analysis)
        assert any("decision clarity" in r for r in recs)

    def test_many_dead_ends_recommendation(self) -> None:
        analysis = {"cot_quality": {"dead_ends": 5}}
        recs = recommend_improvements(analysis)
        assert any("dead-ends" in r for r in recs)

    def test_large_prompt_warning(self) -> None:
        analysis = {"efficiency": {"tokens_in": 2000}}
        recs = recommend_improvements(analysis)
        assert any("very large" in r for r in recs)

    def test_incomplete_task_with_many_steps(self) -> None:
        analysis = {
            "efficiency": {"task_completed": False, "steps_taken": 10},
        }
        recs = recommend_improvements(analysis)
        assert any("incomplete" in r.lower() for r in recs)

    def test_high_error_count_warning(self) -> None:
        analysis = {"efficiency": {"errors": 5}}
        recs = recommend_improvements(analysis)
        assert any("error" in r.lower() for r in recs)

    def test_context_waste_recommendation(self) -> None:
        analysis = {"context_waste": ["a", "b", "c", "d"]}
        recs = recommend_improvements(analysis)
        assert any("waste" in r.lower() for r in recs)

    def test_debugging_with_many_dead_ends(self) -> None:
        analysis = {
            "classification": "debugging",
            "cot_quality": {"dead_ends": 5},
        }
        recs = recommend_improvements(analysis)
        assert any("debugging" in r.lower() for r in recs)

    def test_research_prompt_recommendation(self) -> None:
        analysis = {"classification": "research"}
        recs = recommend_improvements(analysis)
        assert any("research" in r.lower() for r in recs)

    def test_well_structured_prompt_no_issues(self) -> None:
        analysis = {
            "cot_quality": {
                "reasoning_depth": 5,
                "decision_quality": 5,
                "dead_ends": 1,
                "score": 7,
            },
            "efficiency": {"tokens_in": 100, "task_completed": True, "errors": 0},
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("well-structured" in r for r in recs)

    def test_low_cot_score_recommendation(self) -> None:
        analysis = {"cot_quality": {"score": 2}}
        recs = recommend_improvements(analysis)
        assert any("CoT" in r for r in recs)


# ---------------------------------------------------------------------------
# ab_compare
# ---------------------------------------------------------------------------


class TestAbCompare:
    def test_variant_a_wins_with_higher_completion_rate(self) -> None:
        a: list[dict[str, Any]] = [
            {"role": "user", "content": "task", "tokens": 10},
            {
                "role": "assistant",
                "content": "I have done the thing.",
                "tokens": 5,
                "tool_calls": [],
            },
        ]
        b: list[dict[str, Any]] = [
            {"role": "user", "content": "task", "tokens": 10},
            {
                "role": "assistant",
                "content": "still working...",
                "tokens": 50,
                "tool_calls": [{"a": 1}],
            },
        ]
        result = ab_compare(a, b)
        assert result["winner"] == "A"

    def test_tie_when_equal(self) -> None:
        conv: list[dict[str, Any]] = [
            {"role": "user", "content": "x", "tokens": 1},
            {"role": "assistant", "content": "done", "tokens": 1, "tool_calls": []},
        ]
        result = ab_compare(conv, conv)
        assert result["winner"] == "tie"

    def test_recommendation_field_present(self) -> None:
        a: list[dict[str, Any]] = [
            {"role": "user", "content": "task", "tokens": 10},
            {
                "role": "assistant",
                "content": "task done and complete. [x]",
                "tokens": 5,
                "tool_calls": [],
            },
        ]
        b: list[dict[str, Any]] = [
            {"role": "user", "content": "task", "tokens": 10},
            {"role": "assistant", "content": "...", "tokens": 1, "tool_calls": []},
        ]
        result = ab_compare(a, b)
        assert "recommendation" in result
        assert "a_metrics" in result
        assert "b_metrics" in result


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_markdown_format(self) -> None:
        analyses: list[dict[str, Any]] = [
            {
                "prompt_id": "test-1",
                "classification": "coding",
                "efficiency": {
                    "tokens_in": 42,
                    "tokens_out": 10,
                    "task_completed": True,
                    "steps_taken": 3,
                    "errors": 0,
                },
                "cot_quality": {
                    "reasoning_depth": 5,
                    "decision_quality": 4,
                    "dead_ends": 1,
                    "score": 6,
                },
                "context_waste": [{"type": "repeated_fact"}],
                "recommendations": ["Do better"],
            },
        ]
        markdown = generate_report(analyses, format="markdown")
        assert "# Prompt Evaluation Report" in markdown
        assert "## test-1" in markdown
        assert "coding" in markdown

    def test_json_format(self) -> None:
        analyses: list[dict[str, Any]] = [
            {"prompt_id": "j1", "classification": "research"}
        ]
        output = generate_report(analyses, format="json")
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["classification"] == "research"

    def test_defaults_to_markdown(self) -> None:
        analyses = [{"prompt_id": "p1"}]
        result = generate_report(analyses)
        assert result.startswith("#")


# ---------------------------------------------------------------------------
# _build_fallback_entry (internal helper)
# ---------------------------------------------------------------------------


class TestBuildFallbackEntry:
    def test_builds_correct_structure(self) -> None:
        entry = _build_fallback_entry("user", "hello world")
        assert entry["role"] == "user"
        assert entry["content"] == "hello world"
        assert entry["tokens"] == 2
        assert entry["tool_calls"] == []
        assert entry["cot"] == ""
        assert entry["timestamp"] is None
