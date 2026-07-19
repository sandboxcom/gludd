"""TDD tests for ContextWindow: per-turn token tracking, sliding window, summarization trigger."""

from __future__ import annotations

import pytest

from general_ludd.chat.context_window import ContextWindow
from general_ludd.chat.session import ChatSession


class TestEstimateTokens:
    def test_estimate_proportional_to_length(self) -> None:
        assert ContextWindow.estimate_tokens("") == 1
        assert ContextWindow.estimate_tokens("a" * 4) == 1
        assert ContextWindow.estimate_tokens("a" * 8) == 2
        assert ContextWindow.estimate_tokens("a" * 100) == 25

    def test_estimate_minimum_one(self) -> None:
        assert ContextWindow.estimate_tokens("") == 1
        assert ContextWindow.estimate_tokens("ab") == 1


class TestRecordAndTotal:
    def test_record_single_turn(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        cw.record_turn(50)
        assert cw.total_tokens() == 50

    def test_record_accumulates(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        cw.record_turn(10)
        cw.record_turn(20)
        cw.record_turn(30)
        assert cw.total_tokens() == 60

    def test_record_zero_allowed(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        cw.record_turn(0)
        assert cw.total_tokens() == 0

    def test_record_negative_raises(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        with pytest.raises(ValueError):
            cw.record_turn(-1)

    def test_per_turn_history(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        cw.record_turn(5)
        cw.record_turn(7)
        assert cw.per_turn_tokens() == [5, 7]


class TestRemaining:
    def test_remaining_full_budget(self) -> None:
        cw = ContextWindow(max_tokens=1000, reserve_tokens=0)
        assert cw.remaining() == 1000

    def test_remaining_subtracts_used_and_reserve(self) -> None:
        cw = ContextWindow(max_tokens=1000, reserve_tokens=100)
        cw.record_turn(200)
        assert cw.remaining() == 700

    def test_remaining_floors_at_zero(self) -> None:
        cw = ContextWindow(max_tokens=100, reserve_tokens=50)
        cw.record_turn(200)
        assert cw.remaining() == 0


class TestNeedsSummarization:
    def test_below_threshold_no_summarization(self) -> None:
        cw = ContextWindow(max_tokens=1000, summarization_threshold=0.8)
        cw.record_turn(700)
        assert cw.needs_summarization() is False

    def test_at_threshold_triggers(self) -> None:
        cw = ContextWindow(max_tokens=1000, summarization_threshold=0.8)
        cw.record_turn(800)
        assert cw.needs_summarization() is True

    def test_above_threshold_triggers(self) -> None:
        cw = ContextWindow(max_tokens=1000, summarization_threshold=0.8)
        cw.record_turn(950)
        assert cw.needs_summarization() is True


class TestSlidingWindow:
    def test_keeps_system_and_recent(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "ans1"},
            {"role": "user", "content": "old2"},
            {"role": "assistant", "content": "ans2"},
            {"role": "user", "content": "recent"},
        ]
        out = cw.sliding_window_messages(messages, keep_recent=2)
        assert out[0]["role"] == "system"
        assert out[-1]["content"] == "recent"
        assert len(out) == 3

    def test_keep_all_when_fewer_than_window(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "only"},
        ]
        out = cw.sliding_window_messages(messages, keep_recent=4)
        assert out == messages

    def test_empty_messages(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        assert cw.sliding_window_messages([], keep_recent=4) == []

    def test_no_system_message_keeps_recent(self) -> None:
        cw = ContextWindow(max_tokens=1000)
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        out = cw.sliding_window_messages(messages, keep_recent=2)
        assert [m["content"] for m in out] == ["b", "c"]


class TestSummarizeTrigger:
    def test_summarize_returns_placeholder_dropping_old(self) -> None:
        cw = ContextWindow(max_tokens=1000, summarization_threshold=0.8)
        cw.record_turn(900)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "ans1"},
            {"role": "user", "content": "old2"},
            {"role": "assistant", "content": "ans2"},
            {"role": "user", "content": "recent"},
        ]
        out = cw.summarize_if_needed(messages, keep_recent=2)
        assert out is not None
        assert any(m["role"] == "system" for m in out)
        summary_msg = next(m for m in out if m["role"] == "system" and "summary" in m["content"].lower())
        assert "old1" in summary_msg["content"]
        assert out[-1]["content"] == "recent"

    def test_summarize_returns_none_when_under_threshold(self) -> None:
        cw = ContextWindow(max_tokens=10000, summarization_threshold=0.8)
        cw.record_turn(10)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        assert cw.summarize_if_needed(messages, keep_recent=2) is None


class TestChatSessionIntegration:
    def test_max_context_param_stored(self) -> None:
        session = ChatSession(max_context=4096)
        assert session._context_window is not None
        assert session._context_window._max_tokens == 4096

    def test_default_max_context(self) -> None:
        session = ChatSession()
        assert session._context_window._max_tokens > 0

    def test_tokens_recorded_after_turn(self) -> None:
        session = ChatSession(max_context=10000)
        before = session._context_window.total_tokens()
        session._record_turn_tokens("test prompt", "hello there")
        after = session._context_window.total_tokens()
        assert after > before
        assert session._context_window.per_turn_tokens()[-1] > 0

    def test_messages_for_api_uses_full_history_under_threshold(self) -> None:
        session = ChatSession(max_context=10000)
        session.history.append({"role": "user", "content": "hi"})
        assert session._messages_for_api() is session.history

    def test_messages_for_api_compacts_when_over_threshold(self) -> None:
        session = ChatSession(max_context=100)
        for i in range(10):
            session.history.append({"role": "user", "content": f"message number {i} " * 5})
            session._context_window.record_turn(20)
        api_msgs = session._messages_for_api()
        assert api_msgs is not session.history
        assert len(api_msgs) < len(session.history)


class TestCLIMaxContextFlag:
    def test_max_context_flag_present(self) -> None:
        from general_ludd.cli import build_parser
        parser = build_parser()[0]
        args = parser.parse_args(["chat", "--max-context", "8192", "--eval", "hi"])
        assert args.max_context == 8192

    def test_max_context_default(self) -> None:
        from general_ludd.cli import build_parser
        parser = build_parser()[0]
        args = parser.parse_args(["chat", "--eval", "hi"])
        assert args.max_context is None
