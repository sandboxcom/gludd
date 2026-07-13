"""Unit tests for SLMCompactor."""

from __future__ import annotations

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import CompactionRequest
from general_ludd.compaction.slm import (
    SLMCompactor,
    _build_summary_prompt,
    _extractive_fallback,
)


def make_msg(role: str, content: str, is_system: bool = False, tokens: int = 0) -> ContextMessage:
    return ContextMessage(role=role, content=content, is_system=is_system, token_estimate=tokens)


class TestExtractiveFallback:
    def test_short_text_returned_verbatim(self) -> None:
        result = _extractive_fallback("hello", max_chars=100)
        assert result == "hello"

    def test_long_text_trimmed_head_tail(self) -> None:
        text = "x" * 200
        result = _extractive_fallback(text, max_chars=100)
        half = 100 // 2
        max_expected = half * 2 + len("...[trimmed]...") + 2
        assert len(result) <= max_expected
        assert "x" * half in result
        assert "[trimmed]" in result

    def test_exact_boundary(self) -> None:
        text = "a" * 50
        result = _extractive_fallback(text, max_chars=50)
        assert result == text


class TestBuildSummaryPrompt:
    def test_with_goal(self) -> None:
        result = _build_summary_prompt("fix bug", "context here")
        assert "GOAL: fix bug" in result
        assert "context here" in result

    def test_with_empty_goal(self) -> None:
        result = _build_summary_prompt("", "context here")
        assert "GOAL:" not in result
        assert "context here" in result

    def test_with_whitespace_goal(self) -> None:
        result = _build_summary_prompt("   ", "context here")
        assert "GOAL:" not in result
        assert "context here" in result


class TestSLMCompactorInit:
    def test_defaults(self) -> None:
        compactor = SLMCompactor()
        assert compactor.name == "slm"
        assert compactor.model_name == "compactor"

    def test_custom_params(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "summary"

        compactor = SLMCompactor(
            summarize_fn=fn,
            preserve_recent=6,
            fallback_max_chars=4000,
            model_name="custom",
            name="my_compactor",
        )
        assert compactor.name == "my_compactor"
        assert compactor.model_name == "custom"

    def test_no_summarize_fn(self) -> None:
        compactor = SLMCompactor(summarize_fn=None)
        assert compactor._summarize_fn is None


class TestSLMCompactorCompact:
    def test_empty_messages_no_compaction(self) -> None:
        compactor = SLMCompactor()
        request = CompactionRequest(messages=[], goal="test")
        result = compactor.compact(request)
        assert result.original_tokens == 0
        assert result.compacted_tokens == 0
        assert result.dropped_messages == 0

    def test_messages_within_preserve_recent_not_compacted(self) -> None:
        compactor = SLMCompactor()
        msgs = [
            make_msg("user", "hello"),
            make_msg("assistant", "hi"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=4)
        result = compactor.compact(request)
        assert result.dropped_messages == 0
        assert len(result.messages) == 2

    def test_compacts_old_messages_with_summarize_fn(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "summarized context"

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "msg1"),
            make_msg("assistant", "msg2"),
            make_msg("user", "msg3"),
            make_msg("assistant", "msg4"),
            make_msg("user", "msg5"),
            make_msg("assistant", "msg6"),
        ]
        request = CompactionRequest(messages=msgs, goal="fix thing", preserve_recent=2)
        result = compactor.compact(request)
        assert result.dropped_messages == 4
        assert result.method == "slm"
        assert "summarized context" in result.messages[0].content

    def test_preserve_recent_zero_compacts_all(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "all summary"

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "a"),
            make_msg("assistant", "b"),
        ]
        request = CompactionRequest(messages=msgs, goal="goal", preserve_recent=0)
        result = compactor.compact(request)
        assert result.dropped_messages == 2

    def test_no_summarize_fn_uses_extractive_fallback(self) -> None:
        compactor = SLMCompactor(summarize_fn=None)
        msgs = [
            make_msg("user", "x" * 3000),
            make_msg("assistant", "x" * 3000),
            make_msg("user", "recent1"),
            make_msg("assistant", "recent2"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=2)
        result = compactor.compact(request)
        assert result.dropped_messages == 2
        assert result.compacted_tokens < result.original_tokens

    def test_summarize_fn_returns_empty_uses_fallback(self) -> None:
        def fn(goal: str, text: str) -> str:
            return ""

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "old content here"),
            make_msg("assistant", "more old content"),
            make_msg("user", "recent1"),
            make_msg("assistant", "recent2"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=2)
        result = compactor.compact(request)
        assert result.dropped_messages == 2

    def test_summarize_fn_raises_fallback_used(self) -> None:
        def fn(goal: str, text: str) -> str:
            raise RuntimeError("model crash")

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "old data"),
            make_msg("assistant", "response data"),
            make_msg("user", "recent1"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=1)
        result = compactor.compact(request)
        assert result.dropped_messages == 2
        assert result.method == "slm"

    def test_summarize_fn_returns_non_string_fallback(self) -> None:
        def fn(goal: str, text: str) -> int:
            return 42

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "old context"),
            make_msg("assistant", "more context"),
            make_msg("user", "recent"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=1)
        result = compactor.compact(request)
        assert result.dropped_messages == 2

    def test_system_messages_preserved(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "summary"

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            ContextMessage(role="system", content="sys msg", is_system=True, token_estimate=0),
            make_msg("user", "old1"),
            make_msg("assistant", "old2"),
            make_msg("user", "recent1"),
        ]
        request = CompactionRequest(messages=msgs, goal="goal", preserve_recent=1)
        result = compactor.compact(request)
        system_count = sum(1 for m in result.messages if m.role == "system")
        assert system_count >= 2

    def test_compacted_tokens_less_than_original(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "short summary"

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "long " * 100),
            make_msg("assistant", "content " * 100),
            make_msg("user", "recent long " * 50),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=1)
        result = compactor.compact(request)
        assert result.compacted_tokens < result.original_tokens

    def test_target_tokens_budget_respected(self) -> None:
        def fn(goal: str, text: str) -> str:
            return "a" * 5000

        compactor = SLMCompactor(summarize_fn=fn)
        msgs = [
            make_msg("user", "old"),
            make_msg("assistant", "more old"),
            make_msg("user", "recent"),
        ]
        request = CompactionRequest(messages=msgs, goal="test", preserve_recent=1, target_tokens=20)
        result = compactor.compact(request)
        summary_msg = [m for m in result.messages if m.role == "system" and "prior context" in m.content]
        assert len(summary_msg) == 1
