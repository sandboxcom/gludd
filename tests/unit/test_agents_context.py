"""Structural tests for agents/context.py — context compaction for token budgets."""

from general_ludd.agents.context import ContextCompactor, ContextMessage


class TestContextMessage:
    def test_construct_minimal(self):
        msg = ContextMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.token_estimate == 0

    def test_construct_full(self):
        msg = ContextMessage(
            role="system",
            content="you are a helpful assistant",
            token_estimate=100,
            is_system=True,
            timestamp=12345.6,
        )
        assert msg.is_system is True
        assert msg.token_estimate == 100
        assert msg.timestamp == 12345.6

    def test_default_values(self):
        msg = ContextMessage(role="assistant", content="ok")
        assert msg.is_system is False
        assert msg.timestamp == 0.0
        assert msg.token_estimate == 0


class TestContextCompactor:
    def test_construct_defaults(self):
        compactor = ContextCompactor()
        assert compactor._max_tokens == 128000
        assert compactor._compaction_threshold == 0.8
        assert compactor._preserve_recent_count == 4

    def test_construct_custom(self):
        compactor = ContextCompactor(
            max_tokens=32000,
            compaction_threshold=0.5,
            preserve_recent_count=2,
        )
        assert compactor._max_tokens == 32000

    def test_estimate_tokens(self):
        compactor = ContextCompactor()
        est = compactor.estimate_tokens("hello world " * 100)
        assert est > 0
        assert est == len("hello world " * 100) // 4

    def test_get_compaction_ratio_empty(self):
        compactor = ContextCompactor()
        assert compactor.get_compaction_ratio([]) == 0.0

    def test_get_compaction_ratio_below_threshold(self):
        compactor = ContextCompactor(max_tokens=10000)
        messages = [ContextMessage(role="user", content="hi", token_estimate=100)]
        ratio = compactor.get_compaction_ratio(messages)
        assert ratio < 1.0

    def test_needs_compaction_false(self):
        compactor = ContextCompactor(max_tokens=10000, compaction_threshold=0.8)
        messages = [ContextMessage(role="user", content="hi", token_estimate=100)]
        assert compactor.needs_compaction(messages) is False

    def test_needs_compaction_true(self):
        compactor = ContextCompactor(max_tokens=1000, compaction_threshold=0.1)
        messages = [ContextMessage(role="user", content="x" * 100, token_estimate=500)]
        assert compactor.needs_compaction(messages) is True

    def test_compact_empty(self):
        compactor = ContextCompactor()
        assert compactor.compact([]) == []

    def test_compact_no_need(self):
        compactor = ContextCompactor(max_tokens=10000, compaction_threshold=0.9)
        messages = [ContextMessage(role="user", content="hi", token_estimate=100)]
        result = compactor.compact(messages)
        assert result == messages

    def test_compact_few_messages_no_compaction(self):
        compactor = ContextCompactor(
            max_tokens=100, compaction_threshold=0.1,
            preserve_recent_count=5,
        )
        messages = [ContextMessage(role="user", content="x", token_estimate=50)]
        result = compactor.compact(messages)
        assert result == messages

    def test_compact_preserves_recent(self):
        compactor = ContextCompactor(
            max_tokens=100, compaction_threshold=0.01,
            preserve_recent_count=2,
        )
        messages = []
        for i in range(10):
            messages.append(ContextMessage(
                role="user", content=f"msg{i}", token_estimate=50,
            ))
        result = compactor.compact(messages)
        # Should have compacted old + preserved recent 2
        assert len(result) == 3  # 1 summary + 2 recent

    def test_compact_preserves_system_messages(self):
        compactor = ContextCompactor(
            max_tokens=100, compaction_threshold=0.01,
            preserve_recent_count=1,
        )
        messages = [
            ContextMessage(role="system", content="sys", is_system=True, token_estimate=10),
            ContextMessage(role="user", content="old1", token_estimate=50),
            ContextMessage(role="user", content="old2", token_estimate=50),
            ContextMessage(role="user", content="recent", token_estimate=50),
        ]
        result = compactor.compact(messages)
        assert result[0].is_system is True
        assert result[0].content == "sys"

    def test_compact_with_summary_fn(self):
        compactor = ContextCompactor(
            max_tokens=100, compaction_threshold=0.01,
            preserve_recent_count=1,
        )
        messages = [
            ContextMessage(role="user", content="old1", token_estimate=50),
            ContextMessage(role="user", content="old2", token_estimate=50),
            ContextMessage(role="user", content="recent", token_estimate=50),
        ]

        def summary_fn(text):
            return "CUSTOM SUMMARY"

        result = compactor.compact(messages, summary_fn=summary_fn)
        assert any("CUSTOM SUMMARY" in m.content for m in result)

    def test_compact_without_summary_fn_truncates(self):
        compactor = ContextCompactor(
            max_tokens=100, compaction_threshold=0.01,
            preserve_recent_count=1,
        )
        long_content = "X" * 600
        messages = [
            ContextMessage(role="user", content=long_content, token_estimate=50),
            ContextMessage(role="user", content=long_content, token_estimate=50),
            ContextMessage(role="user", content="recent", token_estimate=50),
        ]
        result = compactor.compact(messages)
        # Default summary should be truncated to 500 chars + "..."
        summary_content = result[0].content
        assert len(summary_content) <= 600

    def test_ratio_zero_when_max_tokens_zero(self):
        compactor = ContextCompactor(max_tokens=0)
        messages = [ContextMessage(role="user", content="hi", token_estimate=100)]
        assert compactor.get_compaction_ratio(messages) == 0.0
