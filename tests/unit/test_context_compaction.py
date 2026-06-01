"""Unit tests for context compaction in ContextCompactor."""

from __future__ import annotations

from general_ludd.agents.context import ContextCompactor, ContextMessage


def _make_messages(count: int, tokens_per_msg: int = 100, role: str = "user") -> list[ContextMessage]:
    messages: list[ContextMessage] = []
    for _ in range(count):
        text = "x" * (tokens_per_msg * 4)
        messages.append(ContextMessage(role=role, content=text, token_estimate=tokens_per_msg, is_system=False))
    return messages


class TestEstimateTokensReasonable:
    def test_estimate_tokens_reasonable(self):
        compactor = ContextCompactor()
        text = "Hello world, this is a token estimation test."
        result = compactor.estimate_tokens(text)
        assert result == len(text) // 4

    def test_estimate_tokens_empty(self):
        compactor = ContextCompactor()
        assert compactor.estimate_tokens("") == 0

    def test_estimate_tokens_long(self):
        compactor = ContextCompactor()
        text = "a" * 4000
        assert compactor.estimate_tokens(text) == 1000


class TestGetCompactionRatio:
    def test_get_compaction_ratio(self):
        compactor = ContextCompactor(max_tokens=1000)
        messages = _make_messages(5, tokens_per_msg=200)
        ratio = compactor.get_compaction_ratio(messages)
        assert ratio == 1.0

    def test_get_compaction_ratio_partial(self):
        compactor = ContextCompactor(max_tokens=1000)
        messages = _make_messages(3, tokens_per_msg=200)
        ratio = compactor.get_compaction_ratio(messages)
        assert ratio == 0.6

    def test_get_compaction_ratio_empty(self):
        compactor = ContextCompactor(max_tokens=1000)
        ratio = compactor.get_compaction_ratio([])
        assert ratio == 0.0


class TestCompactContextNoopWhenUnderThreshold:
    def test_compact_context_noop_when_under_threshold(self):
        compactor = ContextCompactor(max_tokens=10000, compaction_threshold=0.8)
        messages = _make_messages(5, tokens_per_msg=100)
        result = compactor.compact(messages)
        assert result == messages

    def test_noop_with_few_messages(self):
        compactor = ContextCompactor(max_tokens=100000, compaction_threshold=0.8)
        messages = _make_messages(2, tokens_per_msg=50)
        result = compactor.compact(messages)
        assert result == messages


class TestCompactContextReturnsSummary:
    def test_compact_context_returns_summary(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.5, preserve_recent_count=2)
        messages = _make_messages(8, tokens_per_msg=100)
        result = compactor.compact(messages)
        summary_msgs = [m for m in result if m.role == "system" and "prior context" in m.content.lower()]
        assert len(summary_msgs) >= 1
        total_content = sum(len(m.content) for m in result)
        original_content = sum(len(m.content) for m in messages)
        assert total_content < original_content


class TestCompactContextKeepsRecentMessages:
    def test_compact_context_keeps_recent_messages(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.5, preserve_recent_count=3)
        messages = _make_messages(10, tokens_per_msg=100)
        result = compactor.compact(messages)
        recent_in_result = [m for m in result if not m.is_system]
        last_original = messages[-3:]
        for orig, kept in zip(last_original, recent_in_result[-3:], strict=True):
            assert kept.content == orig.content

    def test_preserve_recent_count_respected(self):
        compactor = ContextCompactor(max_tokens=200, compaction_threshold=0.3, preserve_recent_count=4)
        messages = _make_messages(10, tokens_per_msg=50)
        result = compactor.compact(messages)
        non_system = [m for m in result if not m.is_system]
        assert len(non_system) >= 4
        recent_contents = {m.content for m in messages[-4:]}
        for m in non_system[-4:]:
            assert m.content in recent_contents


class TestCompactContextSummarizesOldMessages:
    def test_compact_context_summarizes_old_messages(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=2)
        messages = _make_messages(10, tokens_per_msg=100)
        result = compactor.compact(messages)
        summary_msgs = [m for m in result if m.is_system and "prior context" in m.content.lower()]
        assert len(summary_msgs) == 1
        old_content_parts = [m.content[:20] for m in messages[:-2]]
        summary_text = summary_msgs[0].content
        for part in old_content_parts:
            assert part in summary_text


class TestCompactContextPreservesSystemPrompt:
    def test_compact_context_preserves_system_prompt(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=2)
        system_msg = ContextMessage(
            role="system",
            content="You are a helpful assistant.",
            token_estimate=7,
            is_system=True,
        )
        messages = [system_msg, *_make_messages(8, tokens_per_msg=100)]
        result = compactor.compact(messages)
        system_in_result = [m for m in result if m.role == "system" and m.content == "You are a helpful assistant."]
        assert len(system_in_result) == 1
        assert result[0] == system_msg


class TestCompactContextHandlesEmptyHistory:
    def test_compact_context_handles_empty_history(self):
        compactor = ContextCompactor()
        result = compactor.compact([])
        assert result == []


class TestCompactContextTokenCountAccurate:
    def test_compact_context_token_count_accurate(self):
        compactor = ContextCompactor(max_tokens=400, compaction_threshold=0.5, preserve_recent_count=2)
        messages = _make_messages(20, tokens_per_msg=100)
        result = compactor.compact(messages)
        total_tokens = sum(m.token_estimate for m in result)
        assert total_tokens < sum(m.token_estimate for m in messages)
        assert compactor.get_compaction_ratio(result) < compactor.get_compaction_ratio(messages)


class TestCompactContextStripsOldToolOutput:
    def test_compact_context_strips_old_tool_output(self):
        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=2)
        tool_output = ContextMessage(
            role="tool",
            content="y" * 4000,
            token_estimate=1000,
            is_system=False,
        )
        recent_tool = ContextMessage(
            role="tool",
            content="recent tool output",
            token_estimate=5,
            is_system=False,
        )
        messages = [tool_output, *_make_messages(5, tokens_per_msg=100), recent_tool]
        result = compactor.compact(messages)
        tool_msgs = [m for m in result if m.role == "tool"]
        recent_contents = [m.content for m in tool_msgs]
        assert "recent tool output" in recent_contents
        assert tool_output.content not in [m.content for m in result if not m.is_system]
        assert not any(m.role == "tool" and m.content == tool_output.content for m in result)
        summary_msgs = [m for m in result if m.is_system and "prior context" in m.content.lower()]
        assert len(summary_msgs) >= 1


class TestCompactWithSummaryFn:
    def test_compact_with_custom_summary_fn(self):
        def upper_summary(text: str) -> str:
            return text.upper()[:200]

        compactor = ContextCompactor(max_tokens=500, compaction_threshold=0.3, preserve_recent_count=2)
        messages = _make_messages(8, tokens_per_msg=100)
        result = compactor.compact(messages, summary_fn=upper_summary)
        summary_msgs = [m for m in result if m.is_system and "prior context" in m.content.lower()]
        assert len(summary_msgs) == 1
        summary_body = summary_msgs[0].content.replace("[prior context] ", "")
        assert summary_body.isupper()
