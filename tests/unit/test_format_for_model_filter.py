from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).parent.parent.parent
FILTER_PATH = ROOT / "plugins" / "filter" / "format_for_model.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("format_for_model", str(FILTER_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module()

SAMPLE_RESULTS: list[dict[str, Any]] = [
    {"title": "Test Page", "url": "https://example.com", "snippet": "A test result page."},
    {"title": "Another", "url": "https://example.org", "snippet": "Second result."},
]

SAMPLE_LOGS: list[dict[str, Any]] = [
    {"tool": "read_file", "args": {"path": "/tmp/x"}, "result": "file contents", "timestamp": "2026-01-01T00:00:00Z"},
    {"tool": "run_bash", "args": {"cmd": "ls"}, "result": "file1\nfile2", "timestamp": "2026-01-01T00:00:01Z"},
]

SAMPLE_CONTEXT: dict[str, Any] = {
    "agent_state": {"mode": "coding", "project": "gludd"},
    "memory": [{"key": "fact1", "value": "remembered"}],
    "tasks": [{"id": "1", "status": "pending"}],
    "history": [{"role": "user", "content": "hello"}],
}


class TestFormatSearchResults:
    def test_claude_xml_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "claude")
        assert "<search_results>" in result
        assert "</search_results>" in result
        assert "<result>" in result
        assert "<title>Test Page</title>" in result
        assert "<url>https://example.com</url>" in result
        assert "<snippet>A test result page.</snippet>" in result

    def test_gpt4_markdown_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "gpt4")
        assert "### Search Results" in result
        assert "**Test Page**" in result
        assert "https://example.com" in result
        assert "A test result page." in result

    def test_deepseek_json_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "deepseek")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["title"] == "Test Page"

    def test_qwen_markdown_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "qwen")
        assert "### Search Results" in result
        assert "**Test Page**" in result

    def test_llama_plaintext_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "llama")
        assert "Search Results:" in result
        assert "Test Page" in result

    def test_glm_structured_format(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "glm")
        assert "[Search Results]" in result
        assert "TITLE: Test Page" in result
        assert "URL: https://example.com" in result
        assert "SNIPPET: A test result page." in result

    def test_empty_results_handled(self) -> None:
        results_claude = MOD.format_search_results([], "claude")
        assert results_claude == "<search_results></search_results>"

        results_gpt = MOD.format_search_results([], "gpt4")
        assert "*No results found.*" in results_gpt

        results_deepseek = MOD.format_search_results([], "deepseek")
        assert results_deepseek == "[]"

        results_llama = MOD.format_search_results([], "llama")
        assert "No results found." in results_llama

    def test_unknown_model_falls_back_to_claude(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "unknown-model-xyz")
        assert "<search_results>" in result

    def test_partial_match_model(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "openai-gpt4-turbo")
        assert "### Search Results" in result

    def test_case_insensitive_model(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "CLAUDE")
        assert "<search_results>" in result

    def test_results_with_missing_fields(self) -> None:
        sparse = [{"title": "Only Title"}]
        result = MOD.format_search_results(sparse, "claude")
        assert "<title>Only Title</title>" in result
        assert "<url></url>" in result
        assert "<snippet></snippet>" in result

    def test_uses_content_fallback_for_snippet(self) -> None:
        with_content = [{"title": "T", "url": "U", "content": "Body text."}]
        result = MOD.format_search_results(with_content, "claude")
        assert "Body text." in result

    def test_uses_summary_fallback_for_snippet(self) -> None:
        with_summary = [{"title": "T", "url": "U", "summary": "Summary text."}]
        result = MOD.format_search_results(with_summary, "gpt4")
        assert "Summary text." in result

    def test_large_results_not_truncated_by_default(self) -> None:
        large = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "snippet": f"Snippet {i}"}
            for i in range(100)
        ]
        result = MOD.format_search_results(large, "claude")
        assert "<result>" in result
        assert all(f"Result {i}" in result for i in range(100))


class TestFormatLogCalls:
    def test_claude_prefers_structured_xml(self) -> None:
        result = MOD.format_log_calls(SAMPLE_LOGS, "claude")
        assert "<tool_call>" in result
        assert "<name>read_file</name>" in result
        assert "<result>file contents</result>" in result

    def test_gpt4_prefers_structured_xml(self) -> None:
        result = MOD.format_log_calls(SAMPLE_LOGS, "gpt4")
        assert "<tool_call>" in result

    def test_deepseek_json_log_format(self) -> None:
        result = MOD.format_log_calls([SAMPLE_LOGS[0]], "deepseek")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert parsed["tool"] == "read_file"

    def test_llama_plaintext_log_format(self) -> None:
        result = MOD.format_log_calls(SAMPLE_LOGS, "llama")
        assert "read_file" in result
        assert "file contents" in result
        assert "<tool_call>" not in result

    def test_empty_logs(self) -> None:
        result = MOD.format_log_calls([], "claude")
        assert result == "No log entries."

    def test_single_log_entry(self) -> None:
        result = MOD.format_log_calls([SAMPLE_LOGS[0]], "claude")
        assert "<name>read_file</name>" in result
        assert "<name>run_bash</name>" not in result

    def test_missing_fields_handled(self) -> None:
        sparse = [{"tool": "test"}]
        result = MOD.format_log_calls(sparse, "claude")
        assert "<name>test</name>" in result

    def test_log_separator_respects_model_preference(self) -> None:
        result = MOD.format_log_calls(SAMPLE_LOGS, "claude")
        assert result.count("<tool_call>") == 2


class TestFormatContextWindow:
    def test_formats_agent_state(self) -> None:
        result = MOD.format_context_window({"agent_state": {"mode": "coding"}}, "claude")
        assert "<agent_state>" in result
        assert "coding" in result

    def test_formats_memory(self) -> None:
        result = MOD.format_context_window({"memory": ["m1", "m2"]}, "claude")
        assert "<memory>" in result
        assert "m1" in result

    def test_formats_tasks(self) -> None:
        result = MOD.format_context_window({"tasks": [{"id": "1"}]}, "claude")
        assert "<tasks>" in result

    def test_formats_history(self) -> None:
        result = MOD.format_context_window({"history": ["h1"]}, "claude")
        assert "<history>" in result

    def test_full_context(self) -> None:
        result = MOD.format_context_window(SAMPLE_CONTEXT, "claude")
        assert "<agent_state>" in result
        assert "<memory>" in result
        assert "<tasks>" in result
        assert "<history>" in result

    def test_empty_context(self) -> None:
        result = MOD.format_context_window({}, "claude")
        assert result == "{}"

    def test_max_tokens_truncation(self) -> None:
        big = {"memory": [{"k": "x" * 1000}]}
        result = MOD.format_context_window(big, "claude", max_tokens=10)
        assert "... [truncated]" in result

    def test_max_tokens_no_truncation_when_small(self) -> None:
        small = {"agent_state": {"mode": "test"}}
        result = MOD.format_context_window(small, "claude", max_tokens=10000)
        assert "... [truncated]" not in result
        assert "test" in result

    def test_hard_truncation_for_llama(self) -> None:
        big = {"memory": [{"k": "x" * 1000}]}
        result = MOD.format_context_window(big, "llama", max_tokens=10)
        assert len(result) <= 50

    def test_gpt4_separator_preference(self) -> None:
        ctx = {"agent_state": {"a": 1}, "memory": ["m"]}
        result = MOD.format_context_window(ctx, "gpt4")
        assert "---" in result


class TestFormatErrorTrace:
    def test_string_error_claude(self) -> None:
        result = MOD.format_for_model("Something went wrong", "claude", "error_trace")
        assert "<error>" in result
        assert "Something went wrong" in result
        assert "</error>" in result

    def test_string_error_llama(self) -> None:
        result = MOD.format_for_model("Something went wrong", "llama", "error_trace")
        assert "Something went wrong" in result

    def test_dict_error(self) -> None:
        error = {"message": "OOM", "traceback": "line1\nline2"}
        result = MOD.format_for_model(error, "claude", "error_trace")
        assert "Error: OOM" in result
        assert "line1" in result

    def test_dict_error_no_traceback(self) -> None:
        error = {"message": "OOM"}
        result = MOD.format_for_model(error, "claude", "error_trace")
        assert "Error: OOM" in result

    def test_dict_error_with_error_field(self) -> None:
        error = {"error": "Failed"}
        result = MOD.format_for_model(error, "claude", "error_trace")
        assert "Error: Failed" in result


class TestDetectMisfire:
    def test_empty_output_detected(self) -> None:
        score = MOD.detect_format_misfire("", "claude")
        assert score == 1.0

    def test_whitespace_only_detected(self) -> None:
        score = MOD.detect_format_misfire("   ", "claude")
        assert score == 1.0

    def test_truncated_output_detected(self) -> None:
        score = MOD.detect_format_misfire("some text... [truncated]", "claude")
        assert score > 0.5

    def test_xml_model_receives_json_is_misfire(self) -> None:
        score = MOD.detect_format_misfire('{"key": "value"}', "claude")
        assert score > 0.3

    def test_xml_model_receives_xml_is_ok(self) -> None:
        score = MOD.detect_format_misfire("<result>Hello</result>", "claude")
        assert score == 0.0

    def test_json_model_receives_valid_json_is_ok(self) -> None:
        score = MOD.detect_format_misfire('{"title": "T"}', "deepseek")
        assert score == 0.0

    def test_json_model_receives_invalid_json(self) -> None:
        score = MOD.detect_format_misfire("not json", "deepseek")
        assert score > 0.3

    def test_markdown_model_without_markdown(self) -> None:
        score = MOD.detect_format_misfire("plain text without markdown", "gpt4")
        assert score > 0.2

    def test_unknown_model_falls_back(self) -> None:
        score = MOD.detect_format_misfire("<data>content</data>", "unknown")
        assert score == 0.0


class TestFormatForModel:
    def test_dispatches_to_format_search_results(self) -> None:
        result = MOD.format_for_model(SAMPLE_RESULTS, "claude", "search_results")
        assert "<search_results>" in result

    def test_dispatches_to_format_log_calls(self) -> None:
        result = MOD.format_for_model(SAMPLE_LOGS, "claude", "log_calls")
        assert "<tool_call>" in result

    def test_dispatches_to_format_context_window(self) -> None:
        result = MOD.format_for_model(SAMPLE_CONTEXT, "claude", "model_context")
        assert "<agent_state>" in result

    def test_single_dict_as_search_result(self) -> None:
        result = MOD.format_for_model({"title": "One"}, "claude", "search_results")
        assert "<search_results>" in result
        assert "One" in result

    def test_single_dict_as_log_call(self) -> None:
        result = MOD.format_for_model({"tool": "t"}, "claude", "log_calls")
        assert "<tool_call>" in result

    def test_non_dict_context(self) -> None:
        result = MOD.format_for_model("not a dict", "claude", "model_context")
        assert result == "{}"

    def test_unknown_context_type_returns_string(self) -> None:
        result = MOD.format_for_model({"key": "val"}, "claude", "unknown_type")
        assert isinstance(result, str)

    def test_filter_module_class(self) -> None:
        fm = MOD.FilterModule()
        filters = fm.filters()
        assert "format_for_model" in filters
        assert "format_search_results" in filters
        assert "format_log_calls" in filters
        assert "format_context_window" in filters
        assert "detect_format_misfire" in filters
        assert callable(filters["format_for_model"])
        assert callable(filters["format_search_results"])
        assert callable(filters["format_log_calls"])
        assert callable(filters["format_context_window"])
        assert callable(filters["detect_format_misfire"])


class TestFormattingWeights:
    def test_claude_separator_weight_high(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "claude")
        assert "\n\n" in result

    def test_deepseek_separator_weight_high(self) -> None:
        result = MOD.format_search_results(SAMPLE_RESULTS, "deepseek")
        assert result.startswith("[")

    def test_llama_truncation_weight_high(self) -> None:
        big = {"memory": [{"k": "x" * 1000}]}
        result = MOD.format_context_window(big, "llama", max_tokens=10)
        assert len(result) <= 50

    def test_claude_structure_header_weight(self) -> None:
        result = MOD.format_for_model("error text", "claude", "error_trace")
        assert "<error>" in result
