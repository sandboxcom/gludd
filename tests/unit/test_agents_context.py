"""Unit + functional tests for src/general_ludd/agents/context.py.

Coverage targets:
  - ContextMessage: construction, defaults, strict-mode rejection
  - ContextCompactor.__init__: default and custom params
  - ContextCompactor.estimate_tokens: character-to-token ratio
  - ContextCompactor.get_compaction_ratio: zero-token, normal, over-budget
  - ContextCompactor.needs_compaction: at/above/below threshold
  - ContextCompactor.compact: empty, below-threshold passthrough, system-msg
    preservation, too-few-non-system passthrough, default truncation summary,
    custom summary_fn injection, prior-context key injection in summary message
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.agents.context import ContextCompactor, ContextMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_msg(
    role: str = "user",
    content: str = "hello",
    token_estimate: int = 0,
    is_system: bool = False,
    timestamp: float = 0.0,
) -> ContextMessage:
    return ContextMessage(
        role=role,
        content=content,
        token_estimate=token_estimate,
        is_system=is_system,
        timestamp=timestamp,
    )


def msgs_with_tokens(*token_counts: int, is_system: bool = False) -> list[ContextMessage]:
    """Build a list of non-system messages with the given token estimates."""
    return [
        make_msg(
            content=f"message {i}",
            token_estimate=t,
            is_system=is_system,
        )
        for i, t in enumerate(token_counts)
    ]


# ---------------------------------------------------------------------------
# ContextMessage
# ---------------------------------------------------------------------------

class TestContextMessage:
    def test_defaults(self) -> None:
        msg = ContextMessage(role="user", content="hi")
        assert msg.token_estimate == 0
        assert msg.is_system is False
        assert msg.timestamp == 0.0

    def test_explicit_fields(self) -> None:
        msg = ContextMessage(
            role="assistant",
            content="response",
            token_estimate=42,
            is_system=True,
            timestamp=1234.5,
        )
        assert msg.role == "assistant"
        assert msg.content == "response"
        assert msg.token_estimate == 42
        assert msg.is_system is True
        assert msg.timestamp == 1234.5

    def test_strict_mode_rejects_extra_fields(self) -> None:
        """Strict config should reject unexpected keyword arguments."""
        with pytest.raises((ValidationError, TypeError)):
            ContextMessage(role="user", content="hi", unknown_field="x")  # type: ignore[call-arg]

    def test_strict_mode_rejects_wrong_type_for_token_estimate(self) -> None:
        """In strict mode Pydantic must not coerce '5' (str) to int."""
        with pytest.raises(ValidationError):
            ContextMessage(role="user", content="hi", token_estimate="5")  # type: ignore[arg-type]

    def test_strict_mode_rejects_wrong_type_for_is_system(self) -> None:
        with pytest.raises(ValidationError):
            ContextMessage(role="user", content="hi", is_system="yes")  # type: ignore[arg-type]

    def test_role_and_content_required(self) -> None:
        with pytest.raises(ValidationError):
            ContextMessage(role="user")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ContextCompactor — __init__ / defaults
# ---------------------------------------------------------------------------

class TestContextCompactorInit:
    def test_defaults(self) -> None:
        cc = ContextCompactor()
        assert cc._max_tokens == 128_000
        assert cc._compaction_threshold == 0.8
        assert cc._preserve_recent_count == 4

    def test_custom_params(self) -> None:
        cc = ContextCompactor(
            max_tokens=4096,
            compaction_threshold=0.5,
            preserve_recent_count=2,
        )
        assert cc._max_tokens == 4096
        assert cc._compaction_threshold == 0.5
        assert cc._preserve_recent_count == 2


# ---------------------------------------------------------------------------
# ContextCompactor.estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self) -> None:
        cc = ContextCompactor()
        assert cc.estimate_tokens("") == 0

    def test_four_chars_equals_one_token(self) -> None:
        cc = ContextCompactor()
        assert cc.estimate_tokens("abcd") == 1

    def test_eight_chars_equals_two_tokens(self) -> None:
        cc = ContextCompactor()
        assert cc.estimate_tokens("abcdefgh") == 2

    def test_three_chars_rounds_down(self) -> None:
        cc = ContextCompactor()
        assert cc.estimate_tokens("abc") == 0

    def test_large_text(self) -> None:
        cc = ContextCompactor()
        text = "a" * 400
        assert cc.estimate_tokens(text) == 100


# ---------------------------------------------------------------------------
# ContextCompactor.get_compaction_ratio
# ---------------------------------------------------------------------------

class TestGetCompactionRatio:
    def test_empty_list(self) -> None:
        cc = ContextCompactor(max_tokens=1000)
        assert cc.get_compaction_ratio([]) == 0.0

    def test_zero_max_tokens_returns_zero(self) -> None:
        cc = ContextCompactor(max_tokens=0)
        messages = [make_msg(token_estimate=100)]
        assert cc.get_compaction_ratio(messages) == 0.0

    def test_ratio_below_one(self) -> None:
        cc = ContextCompactor(max_tokens=1000)
        messages = [make_msg(token_estimate=500)]
        assert cc.get_compaction_ratio(messages) == pytest.approx(0.5)

    def test_ratio_at_one(self) -> None:
        cc = ContextCompactor(max_tokens=200)
        messages = [make_msg(token_estimate=200)]
        assert cc.get_compaction_ratio(messages) == pytest.approx(1.0)

    def test_ratio_above_one(self) -> None:
        cc = ContextCompactor(max_tokens=100)
        messages = [make_msg(token_estimate=300)]
        assert cc.get_compaction_ratio(messages) == pytest.approx(3.0)

    def test_sums_all_messages(self) -> None:
        cc = ContextCompactor(max_tokens=1000)
        messages = [
            make_msg(token_estimate=200),
            make_msg(token_estimate=300),
            make_msg(token_estimate=100),
        ]
        assert cc.get_compaction_ratio(messages) == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# ContextCompactor.needs_compaction
# ---------------------------------------------------------------------------

class TestNeedsCompaction:
    def test_below_threshold_false(self) -> None:
        cc = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        messages = [make_msg(token_estimate=500)]
        assert cc.needs_compaction(messages) is False

    def test_exactly_at_threshold_true(self) -> None:
        cc = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        messages = [make_msg(token_estimate=800)]
        assert cc.needs_compaction(messages) is True

    def test_above_threshold_true(self) -> None:
        cc = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        messages = [make_msg(token_estimate=900)]
        assert cc.needs_compaction(messages) is True

    def test_empty_messages_false(self) -> None:
        cc = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        assert cc.needs_compaction([]) is False


# ---------------------------------------------------------------------------
# ContextCompactor.compact — passthrough cases
# ---------------------------------------------------------------------------

class TestCompactPassthrough:
    def test_empty_list_returns_empty(self) -> None:
        cc = ContextCompactor()
        assert cc.compact([]) == []

    def test_below_threshold_returns_original_list(self) -> None:
        cc = ContextCompactor(max_tokens=10_000, compaction_threshold=0.8)
        messages = [make_msg(token_estimate=100), make_msg(token_estimate=200)]
        result = cc.compact(messages)
        assert result is messages  # exact same object — no copy needed

    def test_at_threshold_but_too_few_non_system_returns_all(self) -> None:
        """When non-system count <= preserve_recent_count, return all unchanged."""
        cc = ContextCompactor(
            max_tokens=100,
            compaction_threshold=0.8,
            preserve_recent_count=4,
        )
        # 4 non-system messages at 25 tokens each = 100 tokens = ratio 1.0 → needs compaction
        messages = [make_msg(token_estimate=25) for _ in range(4)]
        result = cc.compact(messages)
        assert result is messages


# ---------------------------------------------------------------------------
# ContextCompactor.compact — default summary (truncation)
# ---------------------------------------------------------------------------

class TestCompactDefaultSummary:
    def _build_compactable(
        self,
        num_old: int = 5,
        num_recent: int = 4,
        preserve: int = 4,
        max_tokens: int = 100,
        token_per_msg: int = 10,
    ) -> tuple[ContextCompactor, list[ContextMessage]]:
        cc = ContextCompactor(
            max_tokens=max_tokens,
            compaction_threshold=0.8,
            preserve_recent_count=preserve,
        )
        messages = [
            make_msg(content=f"old-{i}", token_estimate=token_per_msg)
            for i in range(num_old)
        ]
        messages += [
            make_msg(content=f"recent-{i}", token_estimate=token_per_msg)
            for i in range(num_recent)
        ]
        return cc, messages

    def test_compact_reduces_message_count(self) -> None:
        cc, messages = self._build_compactable(num_old=5, num_recent=4, preserve=4, max_tokens=50)
        result = cc.compact(messages)
        # system summary msg + 4 recent = 5 total (less than original 9)
        assert len(result) < len(messages)

    def test_recent_messages_preserved_at_end(self) -> None:
        cc, messages = self._build_compactable(num_old=5, num_recent=4, preserve=4, max_tokens=50)
        result = cc.compact(messages)
        # Last 4 should be the 4 recent messages
        recent_expected = [m.content for m in messages[-4:]]
        recent_actual = [m.content for m in result[-4:]]
        assert recent_actual == recent_expected

    def test_summary_message_marked_system(self) -> None:
        cc, messages = self._build_compactable(num_old=5, num_recent=4, preserve=4, max_tokens=50)
        result = cc.compact(messages)
        # Find the summary message (not one of the recent ones)
        summary_msgs = [m for m in result if m.is_system]
        assert len(summary_msgs) >= 1

    def test_summary_message_has_prior_context_key(self) -> None:
        """The summary content must start with '[prior context]'."""
        cc, messages = self._build_compactable(num_old=5, num_recent=4, preserve=4, max_tokens=50)
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1

    def test_default_truncation_at_500_chars(self) -> None:
        """When no summary_fn is given, content > 500 chars is truncated with '...'."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        long_content = "x" * 600
        messages = [
            make_msg(content=long_content, token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        # The summarized body must end with "..."
        assert summary_msgs[0].content.endswith("...")
        # The inner truncated text should be exactly 500 chars + "..."
        inner = summary_msgs[0].content[len("[prior context] "):]
        assert len(inner) == 503  # 500 + "..."

    def test_default_no_truncation_when_content_under_500(self) -> None:
        """When old content is <= 500 chars, no ellipsis appended."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        short_content = "short"
        messages = [
            make_msg(content=short_content, token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        assert not summary_msgs[0].content.endswith("...")

    def test_summary_token_estimate_set(self) -> None:
        """Summary message token_estimate must be > 0 when content is non-empty."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        messages = [
            make_msg(content="old content here", token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert summary_msgs[0].token_estimate > 0


# ---------------------------------------------------------------------------
# ContextCompactor.compact — custom summary_fn injection
# ---------------------------------------------------------------------------

class TestCompactCustomSummaryFn:
    def test_custom_summary_fn_called_with_concatenated_old_content(self) -> None:
        """summary_fn receives newline-joined content of old messages."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        captured: list[str] = []

        def my_summary(text: str) -> str:
            captured.append(text)
            return "SUMMARY"

        messages = [
            make_msg(content="alpha", token_estimate=5),
            make_msg(content="beta", token_estimate=4),
            make_msg(content="recent", token_estimate=1),
        ]
        cc.compact(messages, summary_fn=my_summary)
        assert len(captured) == 1
        assert captured[0] == "alpha\nbeta"

    def test_custom_summary_fn_output_embedded_in_prior_context(self) -> None:
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)

        def my_summary(text: str) -> str:
            return "MY-CUSTOM-SUMMARY"

        messages = [
            make_msg(content="old", token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages, summary_fn=my_summary)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        assert "MY-CUSTOM-SUMMARY" in summary_msgs[0].content

    def test_custom_summary_fn_none_uses_default_path(self) -> None:
        """Passing summary_fn=None explicitly triggers the default truncation path."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        messages = [
            make_msg(content="old", token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages, summary_fn=None)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1


# ---------------------------------------------------------------------------
# ContextCompactor.compact — system message preservation
# ---------------------------------------------------------------------------

class TestCompactSystemMessagePreservation:
    def test_system_messages_preserved_at_front(self) -> None:
        """System messages must appear before the summary and recent messages."""
        cc = ContextCompactor(max_tokens=50, compaction_threshold=0.8, preserve_recent_count=2)
        system_msg = make_msg(role="system", content="SYSTEM PROMPT", is_system=True, token_estimate=5)
        non_system = [make_msg(content=f"m{i}", token_estimate=10) for i in range(5)]
        messages = [system_msg, *non_system]
        result = cc.compact(messages)
        assert result[0].is_system is True
        assert result[0].content == "SYSTEM PROMPT"

    def test_multiple_system_messages_all_preserved(self) -> None:
        cc = ContextCompactor(max_tokens=50, compaction_threshold=0.8, preserve_recent_count=2)
        sys1 = make_msg(role="system", content="SYS1", is_system=True, token_estimate=2)
        sys2 = make_msg(role="system", content="SYS2", is_system=True, token_estimate=2)
        non_system = [make_msg(content=f"m{i}", token_estimate=10) for i in range(5)]
        messages = [sys1, sys2, *non_system]
        result = cc.compact(messages)
        system_in_result = [m for m in result if m.content in ("SYS1", "SYS2")]
        assert len(system_in_result) == 2

    def test_result_order_system_then_summary_then_recent(self) -> None:
        """Output order must be: [system...] [summary] [recent...]."""
        cc = ContextCompactor(max_tokens=50, compaction_threshold=0.8, preserve_recent_count=2)
        system_msg = make_msg(role="system", content="SYSTEM", is_system=True, token_estimate=1)
        non_system = [make_msg(content=f"m{i}", token_estimate=10) for i in range(5)]
        messages = [system_msg, *non_system]

        result = cc.compact(messages)
        # First element: original system message
        assert result[0].content == "SYSTEM"
        # Second element: the generated prior-context summary
        assert "[prior context]" in result[1].content
        # Last 2 elements: the 2 most recent non-system messages
        assert result[-2].content == "m3"
        assert result[-1].content == "m4"


# ---------------------------------------------------------------------------
# ContextCompactor.compact — edge cases
# ---------------------------------------------------------------------------

class TestCompactEdgeCases:
    def test_preserve_recent_count_zero(self) -> None:
        """With preserve_recent_count=0, ALL non-system go into the summary."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=0)
        messages = [make_msg(content=f"m{i}", token_estimate=4) for i in range(4)]
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        # No recent messages — only the summary
        non_summary = [m for m in result if "[prior context]" not in m.content]
        assert non_summary == []

    def test_only_system_messages(self) -> None:
        """A context that is all system messages.

        needs_compaction may be False; returned as-is or compacted but preserved.
        """
        cc = ContextCompactor(max_tokens=5, compaction_threshold=0.8, preserve_recent_count=2)
        messages = [
            make_msg(role="system", content="SYS", is_system=True, token_estimate=10),
        ]
        # needs_compaction is True, but non_system is empty so split=0 and old=[]
        # The function should return [system_msg, summary_of_nothing, ...recent=[]]
        # i.e. it won't crash — just verify it runs cleanly
        result = cc.compact(messages)
        assert isinstance(result, list)

    def test_exactly_preserve_plus_one_non_system(self) -> None:
        """One message over preserve_recent_count should trigger compaction."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=2)
        # 3 non-system messages each at 4 tokens = 12 > 10*0.8=8
        messages = [make_msg(content=f"m{i}", token_estimate=4) for i in range(3)]
        result = cc.compact(messages)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        # The single "old" message content should appear in summary
        assert "m0" in summary_msgs[0].content

    def test_compact_result_is_new_list(self) -> None:
        """When compaction occurs the returned list must be a new object."""
        cc = ContextCompactor(max_tokens=10, compaction_threshold=0.8, preserve_recent_count=1)
        messages = [
            make_msg(content="old", token_estimate=9),
            make_msg(content="recent", token_estimate=1),
        ]
        result = cc.compact(messages)
        assert result is not messages
