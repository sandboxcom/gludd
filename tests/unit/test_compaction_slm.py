"""Unit tests for compaction/slm.py — SLMCompactor, extractive fallback, summarizer wiring."""

from __future__ import annotations

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import CompactionRequest
from general_ludd.compaction.slm import (
    SLMCompactor,
    _build_summary_prompt,
    _extractive_fallback,
    make_slm_summarize_fn,
)


def _msg(role: str = "user", content: str = "hello", is_system: bool = False) -> ContextMessage:
    return ContextMessage(role=role, content=content, is_system=is_system)


def _messages(*specs: tuple[str, str, bool]) -> list[ContextMessage]:
    return [_msg(r, c, s) for r, c, s in specs]


class TestBuildSummaryPrompt:
    def test_with_goal(self):
        result = _build_summary_prompt("fix bug", "prior context here")
        assert "GOAL: fix bug" in result
        assert "prior context here" in result
        assert "Summarize the prior context" in result

    def test_empty_goal_omits_goal_line(self):
        result = _build_summary_prompt("", "prior context here")
        assert "GOAL:" not in result
        assert "prior context here" in result

    def test_whitespace_goal_omits_goal_line(self):
        result = _build_summary_prompt("   ", "prior context here")
        assert "GOAL:" not in result
        assert "prior context here" in result


class TestExtractiveFallback:
    def test_within_limit_returns_full_text(self):
        text = "short text"
        assert _extractive_fallback(text, 100) == text

    def test_exceeds_limit_returns_head_and_tail(self):
        text = "a" * 200
        max_chars = 100
        result = _extractive_fallback(text, max_chars)
        half = max_chars // 2
        assert result.startswith("a" * half)
        assert result.endswith("a" * half)
        assert "...[trimmed]..." in result

    def test_empty_text(self):
        assert _extractive_fallback("", 100) == ""

    def test_exact_limit(self):
        text = "x" * 10
        assert _extractive_fallback(text, 10) == text


class TestSLMCompactorInit:
    def test_defaults(self):
        c = SLMCompactor()
        assert c.name == "slm"
        assert c.model_name == "compactor"

    def test_custom_name(self):
        c = SLMCompactor(name="slm_r2")
        assert c.name == "slm_r2"

    def test_none_summarize_fn(self):
        c = SLMCompactor(summarize_fn=None)
        assert c._summarize_fn is None

    def test_custom_summarize_fn(self):
        def fn(g, t):
            return "summary"
        c = SLMCompactor(summarize_fn=fn)
        assert c._summarize_fn is fn


class TestSLMCompactorSummarize:
    def test_no_fn_uses_extractive(self):
        c = SLMCompactor(summarize_fn=None, fallback_max_chars=20)
        result = c._summarize("goal", "x" * 50)
        assert "...[trimmed]..." in result

    def test_fn_returns_valid_string(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "  summarized  ")
        assert c._summarize("goal", "text") == "summarized"

    def test_fn_raises_uses_fallback(self):
        def bad_fn(g, t):
            raise RuntimeError("boom")
        c = SLMCompactor(summarize_fn=bad_fn, fallback_max_chars=20)
        result = c._summarize("goal", "x" * 50)
        assert "...[trimmed]..." in result

    def test_fn_returns_empty_string_uses_fallback(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "", fallback_max_chars=20)
        result = c._summarize("goal", "x" * 50)
        assert "...[trimmed]..." in result

    def test_fn_returns_whitespace_uses_fallback(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "   ", fallback_max_chars=20)
        result = c._summarize("goal", "x" * 50)
        assert "...[trimmed]..." in result

    def test_fn_returns_non_string_uses_fallback(self):
        c = SLMCompactor(summarize_fn=lambda g, t: 42, fallback_max_chars=20)
        result = c._summarize("goal", "x" * 50)
        assert "...[trimmed]..." in result


class TestSLMCompactorCompact:
    def test_no_old_messages_returns_identity(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "summary")
        msgs = [_msg("user", "a"), _msg("assistant", "b")]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=4))
        assert result.messages == msgs
        assert result.dropped_messages == 0

    def test_compacts_old_messages(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "compacted summary")
        msgs = [
            _msg("system", "sys", is_system=True),
            _msg("user", "old1"),
            _msg("assistant", "old2"),
            _msg("user", "recent1"),
            _msg("assistant", "recent2"),
        ]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=2, goal="test"))
        assert result.dropped_messages == 2
        assert "compacted summary" in result.messages[1].content
        assert result.messages[1].is_system is True
        assert result.messages[2].role == "user"
        assert result.messages[2].content == "recent1"
        assert result.messages[3].content == "recent2"

    def test_preserves_system_messages(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "summary")
        msgs = [
            _msg("system", "sys1", is_system=True),
            _msg("system", "sys2", is_system=True),
            _msg("user", "old1"),
            _msg("user", "recent1"),
        ]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=1, goal="test"))
        assert result.messages[0].content == "sys1"
        assert result.messages[1].content == "sys2"

    def test_preserve_recent_zero(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "summary")
        msgs = [
            _msg("system", "sys", is_system=True),
            _msg("user", "a"),
            _msg("user", "b"),
        ]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=0, goal="test"))
        assert result.dropped_messages == 2

    def test_target_tokens_trims_summary(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "a" * 200)
        msgs = [
            _msg("system", "sys", is_system=True),
            _msg("user", "old"),
            _msg("user", "recent"),
        ]
        result = c.compact(CompactionRequest(
            messages=msgs, preserve_recent=1, target_tokens=30
        ))
        assert len(result.messages[1].content) < 200

    def test_method_set_from_name(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "s", name="slm_r4")
        msgs = [_msg("user", "a"), _msg("user", "b")]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=1))
        assert result.method == "slm_r4"

    def test_original_and_compacted_tokens_set(self):
        c = SLMCompactor(summarize_fn=lambda g, t: "summary")
        msgs = [_msg("user", "hello world"), _msg("user", "recent")]
        result = c.compact(CompactionRequest(messages=msgs, preserve_recent=1))
        assert result.original_tokens > 0
        assert result.compacted_tokens > 0


class TestMakeSLMSummarizeFn:
    def test_returns_callable(self):
        fn = make_slm_summarize_fn(None, "compactor")
        assert callable(fn)

    def test_returns_empty_string_on_error(self):
        fn = make_slm_summarize_fn(None, "compactor")
        assert fn("goal", "text") == ""

    def test_delegates_to_gateway(self):
        class FakeResp:
            content = "summarized output"
        class FakeGateway:
            def call_model(self, profile_id, messages, requested_max_output_tokens, work_type):
                assert profile_id == "compactor"
                assert work_type == "compaction"
                return FakeResp()
        fn = make_slm_summarize_fn(FakeGateway(), "compactor")
        assert fn("goal", "text") == "summarized output"

    def test_gateway_exception_returns_empty(self):
        class BadGateway:
            def call_model(self, *a, **kw):
                raise ConnectionError("down")
        fn = make_slm_summarize_fn(BadGateway(), "compactor")
        assert fn("goal", "text") == ""

    def test_resp_without_content_returns_empty(self):
        class FakeResp:
            pass
        class FakeGateway:
            def call_model(self, *a, **kw):
                return FakeResp()
        fn = make_slm_summarize_fn(FakeGateway(), "compactor")
        assert fn("goal", "text") == ""

    def test_resp_content_none_returns_empty(self):
        class FakeResp:
            content = None
        class FakeGateway:
            def call_model(self, *a, **kw):
                return FakeResp()
        fn = make_slm_summarize_fn(FakeGateway(), "compactor")
        assert fn("goal", "text") == ""
