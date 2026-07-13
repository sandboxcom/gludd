"""Unit tests for compaction/baselines.py — NoOp, Truncation, ContextCompactorAdapter."""

from __future__ import annotations

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import CompactionRequest
from general_ludd.compaction.baselines import (
    ContextCompactorAdapter,
    NoOpCompactor,
    TruncationCompactor,
    _split_system,
)


def _msg(role: str = "user", content: str = "hello", is_system: bool = False) -> ContextMessage:
    return ContextMessage(role=role, content=content, is_system=is_system)


def _messages(*specs: tuple[str, str, bool]) -> list[ContextMessage]:
    return [_msg(r, c, s) for r, c, s in specs]


class TestSplitSystem:
    def test_empty(self):
        system, rest = _split_system([])
        assert system == []
        assert rest == []

    def test_all_system(self):
        system, rest = _split_system([_msg(is_system=True), _msg(is_system=True)])
        assert len(system) == 2
        assert rest == []

    def test_all_non_system(self):
        system, rest = _split_system([_msg(), _msg()])
        assert system == []
        assert len(rest) == 2

    def test_mixed(self):
        msgs = [
            _msg(role="system", content="sys", is_system=True),
            _msg(role="user", content="a"),
            _msg(role="assistant", content="b"),
            _msg(role="system", content="sys2", is_system=True),
        ]
        system, rest = _split_system(msgs)
        assert len(system) == 2
        assert len(rest) == 2
        assert all(m.is_system for m in system)
        assert all(not m.is_system for m in rest)


class TestNoOpCompactor:
    def test_name(self):
        assert NoOpCompactor().name == "noop"

    def test_compact_identity(self):
        msgs = [_msg("user", "a"), _msg("assistant", "b")]
        result = NoOpCompactor().compact(CompactionRequest(messages=msgs))
        assert result.messages == msgs
        assert result.method == "noop"

    def test_compact_empty(self):
        result = NoOpCompactor().compact(CompactionRequest(messages=[]))
        assert result.messages == []

    def test_compact_counts_tokens(self):
        result = NoOpCompactor().compact(CompactionRequest(messages=[_msg("user", "hello world")]))
        assert result.original_tokens > 0
        assert result.compacted_tokens > 0
        assert result.ratio == 1.0
        assert result.dropped_messages == 0


class TestTruncationCompactor:
    def test_name(self):
        assert TruncationCompactor().name == "truncate"

    def test_keeps_system_messages(self):
        msgs = [
            _msg(role="system", content="sys", is_system=True),
            _msg(role="user", content="u1"),
            _msg(role="user", content="u2"),
        ]
        result = TruncationCompactor().compact(CompactionRequest(messages=msgs, preserve_recent=1))
        compacted = result.messages
        assert len([m for m in compacted if m.is_system]) == 1
        assert compacted[0].content == "sys"

    def test_preserve_recent_keeps_last_n(self):
        msgs = [
            _msg(role="user", content="old1"),
            _msg(role="user", content="old2"),
            _msg(role="user", content="recent1"),
            _msg(role="user", content="recent2"),
        ]
        result = TruncationCompactor().compact(CompactionRequest(messages=msgs, preserve_recent=2))
        assert len(result.messages) == 2
        assert result.messages[0].content == "recent1"
        assert result.messages[1].content == "recent2"

    def test_preserve_recent_zero_drops_all(self):
        msgs = [_msg("user", "a"), _msg("user", "b")]
        result = TruncationCompactor().compact(CompactionRequest(messages=msgs, preserve_recent=0))
        assert result.messages == []

    def test_mixed_system_and_recent(self):
        msgs = [
            _msg(role="system", content="sys", is_system=True),
            _msg(role="user", content="old"),
            _msg(role="user", content="recent"),
        ]
        result = TruncationCompactor().compact(CompactionRequest(messages=msgs, preserve_recent=1))
        contents = [m.content for m in result.messages]
        assert "sys" in contents
        assert "recent" in contents
        assert "old" not in contents

    def test_target_tokens_limits_budget(self):
        msgs = [
            _msg(role="user", content="x" * 100),
            _msg(role="user", content="y" * 100),
            _msg(role="user", content="z" * 100),
        ]
        result = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=1, target_tokens=60)
        )
        assert result.compacted_tokens <= 60

    def test_target_tokens_more_than_all_keeps_all(self):
        msgs = [_msg(role="user", content="x" * 40), _msg(role="user", content="y" * 40)]
        result = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=1, target_tokens=1000)
        )
        assert len(result.messages) == 2

    def test_dropped_messages_count(self):
        msgs = [_msg("user", str(i)) for i in range(10)]
        result = TruncationCompactor().compact(CompactionRequest(messages=msgs, preserve_recent=3))
        assert result.dropped_messages == 7


class TestContextCompactorAdapter:
    def test_name(self):
        adapter = ContextCompactorAdapter()
        assert adapter.name == "context_compactor"

    def test_custom_name(self):
        adapter = ContextCompactorAdapter(name="my_strategy")
        assert adapter.name == "my_strategy"

    def test_compact_returns_result(self):
        msgs = [_msg("user", "a"), _msg("user", "b"), _msg("user", "c")]
        adapter = ContextCompactorAdapter(max_tokens=1000, compaction_threshold=0.0)
        result = adapter.compact(CompactionRequest(messages=msgs))
        assert hasattr(result, "method")
        assert hasattr(result, "ratio")
        assert hasattr(result, "tokens_saved")

    def test_compact_with_empty_messages(self):
        adapter = ContextCompactorAdapter()
        result = adapter.compact(CompactionRequest(messages=[]))
        assert result.messages == []

    def test_compact_with_system_messages(self):
        msgs = [
            _msg(role="system", content="sys", is_system=True),
            _msg(role="user", content="u1"),
            _msg(role="user", content="u2"),
        ]
        adapter = ContextCompactorAdapter(max_tokens=1000, compaction_threshold=0.0)
        result = adapter.compact(CompactionRequest(messages=msgs))
        assert len([m for m in result.messages if m.is_system]) >= 1

    def test_summary_fn_called(self):
        msgs = [
            _msg("user", "old " + "x" * 600),
            _msg("user", "old " + "x" * 600),
            _msg("user", "recent a"),
            _msg("user", "recent b"),
        ]
        calls: list[str] = []

        def summary_fn(text: str) -> str:
            calls.append(text)
            return "[summary]"

        adapter = ContextCompactorAdapter(
            max_tokens=1000,
            compaction_threshold=0.0,
            preserve_recent_count=2,
            summary_fn=summary_fn,
        )
        result = adapter.compact(CompactionRequest(messages=msgs))
        assert len(calls) >= 1
        assert any("[summary]" in m.content for m in result.messages)
