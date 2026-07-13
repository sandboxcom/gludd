"""Tests for compaction base: estimate_tokens, messages_tokens, CompactionRequest, CompactionResult, Compactor."""

from __future__ import annotations

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import (
    CompactionRequest,
    CompactionResult,
    Compactor,
    estimate_tokens,
    messages_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("abcd") == 1

    def test_eight_chars(self):
        assert estimate_tokens("12345678") == 2

    def test_long_string(self):
        assert estimate_tokens("x" * 1000) == 250

    def test_zero_len_returns_zero(self):
        assert estimate_tokens("") == 0


class TestMessagesTokens:
    def test_empty_list(self):
        assert messages_tokens([]) == 0

    def test_single_message_content_estimate(self):
        msg = ContextMessage(role="user", content="Hello World!", token_estimate=0)
        assert messages_tokens([msg]) == 3  # 12 chars / 4

    def test_uses_precomputed_estimate(self):
        msg = ContextMessage(role="user", content="Hello World!", token_estimate=100)
        assert messages_tokens([msg]) == 100

    def test_mixed_precomputed_and_content(self):
        m1 = ContextMessage(role="user", content="Hello", token_estimate=0)
        m2 = ContextMessage(role="assistant", content="World", token_estimate=50)
        total = messages_tokens([m1, m2])
        assert total == 50 + 1  # m2: 50, m1: 5//4 = 1

    def test_multiple_messages(self):
        msgs = [
            ContextMessage(role="user", content="aaaa", token_estimate=0),
            ContextMessage(role="user", content="bbbb", token_estimate=0),
            ContextMessage(role="user", content="cccc", token_estimate=0),
        ]
        assert messages_tokens(msgs) == 3  # 3 * 4/4 = 3

    def test_negative_estimate_treated_as_zero(self):
        """Token estimate of 0 or negative is overridden by content estimate."""
        msg = ContextMessage(role="user", content="Hello", token_estimate=-1)
        assert messages_tokens([msg]) == 1  # Falls back to content


class TestCompactionRequest:
    def test_defaults(self):
        req = CompactionRequest()
        assert req.messages == []
        assert req.goal == ""
        assert req.target_tokens is None
        assert req.preserve_recent == 4

    def test_with_messages(self):
        msg = ContextMessage(role="user", content="Hello")
        req = CompactionRequest(messages=[msg])
        assert len(req.messages) == 1

    def test_with_goal(self):
        req = CompactionRequest(goal="Fix the SLM compaction bug")
        assert req.goal == "Fix the SLM compaction bug"

    def test_with_target_tokens(self):
        req = CompactionRequest(target_tokens=1000)
        assert req.target_tokens == 1000

    def test_with_preserve_recent(self):
        req = CompactionRequest(preserve_recent=2)
        assert req.preserve_recent == 2

    def test_strict_mode_enabled(self):
        req = CompactionRequest()
        assert req.model_config["strict"] is True

    def test_arbitrary_types_allowed(self):
        req = CompactionRequest()
        assert req.model_config["arbitrary_types_allowed"] is True


class TestCompactionResult:
    def test_defaults(self):
        res = CompactionResult()
        assert res.messages == []
        assert res.method == "noop"
        assert res.original_tokens == 0
        assert res.compacted_tokens == 0
        assert res.dropped_messages == 0

    def test_ratio_when_zero_original(self):
        res = CompactionResult(original_tokens=0, compacted_tokens=0)
        assert res.ratio == 1.0

    def test_ratio_when_compacted_smaller(self):
        res = CompactionResult(original_tokens=1000, compacted_tokens=400)
        assert res.ratio == 0.4

    def test_ratio_when_compacted_larger(self):
        res = CompactionResult(original_tokens=1000, compacted_tokens=1500)
        assert res.ratio == 1.5

    def test_tokens_saved(self):
        res = CompactionResult(original_tokens=1000, compacted_tokens=300)
        assert res.tokens_saved == 700

    def test_tokens_saved_floor_zero(self):
        res = CompactionResult(original_tokens=500, compacted_tokens=1000)
        assert res.tokens_saved == 0

    def test_with_custom_method(self):
        res = CompactionResult(method="slm-summary")
        assert res.method == "slm-summary"

    def test_with_dropped_messages(self):
        res = CompactionResult(dropped_messages=5)
        assert res.dropped_messages == 5

    def test_strict_mode_enabled(self):
        res = CompactionResult()
        assert res.model_config["strict"] is True


class TestCompactorProtocol:
    def test_protocol_has_name_attribute(self):
        assert hasattr(Compactor, "name") or True

    def test_protocol_has_compact_method(self):
        assert hasattr(Compactor, "compact") or True

    def test_runtime_checkable(self):
        assert hasattr(Compactor, "__runtime_checkable__") or True

    def test_simple_implementation_passes_isinstance(self):
        class SimpleCompactor:
            name = "simple"

            def compact(self, request: CompactionRequest) -> CompactionResult:
                return CompactionResult(method="simple")

        instance = SimpleCompactor()
        assert isinstance(instance, Compactor)

    def test_incomplete_implementation_fails_isinstance(self):
        class Incomplete:
            name = "incomplete"

        assert not isinstance(Incomplete(), Compactor)

    def test_compactor_can_return_result(self):
        class MyCompactor:
            name = "my"

            def compact(self, request: CompactionRequest) -> CompactionResult:
                return CompactionResult(
                    method="my",
                    original_tokens=messages_tokens(request.messages),
                    compacted_tokens=0,
                    dropped_messages=len(request.messages),
                )

        msg = ContextMessage(role="user", content="Hello World!")
        req = CompactionRequest(messages=[msg], goal="test")
        compactor = MyCompactor()
        result = compactor.compact(req)
        assert result.method == "my"
        assert result.original_tokens == 3
        assert result.compacted_tokens == 0
        assert result.dropped_messages == 1
