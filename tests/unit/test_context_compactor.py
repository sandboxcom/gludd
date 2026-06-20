"""Unit tests for ContextCompactor and ContextMessage.

Covers:
- compact() with a summary_fn (custom summarization path)
- compact() with mixed system + non-system messages
- needs_compaction=False passthrough (compact returns messages unchanged)
- check_budget when max_tokens < remaining (TokenWindowManager)
- compact() when non_system <= preserve_recent_count (early return)
- compact() empty list
- estimate_tokens
- get_compaction_ratio
- ContextMessage field defaults
"""

from __future__ import annotations

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.agents.token_window import TokenWindowManager

# ---------------------------------------------------------------------------
# ContextMessage
# ---------------------------------------------------------------------------

class TestContextMessage:
    def test_defaults(self) -> None:
        msg = ContextMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.token_estimate == 0
        assert msg.is_system is False
        assert msg.timestamp == 0.0

    def test_system_message(self) -> None:
        msg = ContextMessage(role="system", content="sys prompt", is_system=True, token_estimate=50)
        assert msg.is_system is True
        assert msg.token_estimate == 50


# ---------------------------------------------------------------------------
# ContextCompactor.estimate_tokens
# ---------------------------------------------------------------------------

class TestContextCompactorEstimateTokens:
    def test_estimate_tokens_basic(self) -> None:
        c = ContextCompactor()
        text = "a" * 400
        assert c.estimate_tokens(text) == 100

    def test_estimate_tokens_empty(self) -> None:
        c = ContextCompactor()
        assert c.estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# ContextCompactor.get_compaction_ratio
# ---------------------------------------------------------------------------

class TestGetCompactionRatio:
    def test_ratio_zero_when_no_messages(self) -> None:
        c = ContextCompactor(max_tokens=1000)
        assert c.get_compaction_ratio([]) == 0.0

    def test_ratio_correct(self) -> None:
        c = ContextCompactor(max_tokens=1000)
        msgs = [
            ContextMessage(role="user", content="hi", token_estimate=400),
            ContextMessage(role="assistant", content="ok", token_estimate=200),
        ]
        ratio = c.get_compaction_ratio(msgs)
        assert abs(ratio - 0.6) < 1e-9

    def test_ratio_zero_when_max_tokens_zero(self) -> None:
        c = ContextCompactor(max_tokens=0)
        msgs = [ContextMessage(role="user", content="hi", token_estimate=100)]
        assert c.get_compaction_ratio(msgs) == 0.0


# ---------------------------------------------------------------------------
# ContextCompactor.needs_compaction
# ---------------------------------------------------------------------------

class TestNeedsCompaction:
    def test_needs_compaction_true_at_threshold(self) -> None:
        c = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        msgs = [ContextMessage(role="user", content="x", token_estimate=800)]
        assert c.needs_compaction(msgs) is True

    def test_needs_compaction_false_below_threshold(self) -> None:
        c = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        msgs = [ContextMessage(role="user", content="x", token_estimate=500)]
        assert c.needs_compaction(msgs) is False


# ---------------------------------------------------------------------------
# ContextCompactor.compact — needs_compaction=False passthrough
# ---------------------------------------------------------------------------

class TestCompactPassthrough:
    def test_no_compaction_needed_returns_same_list(self) -> None:
        """When needs_compaction=False, compact returns the original list unchanged."""
        c = ContextCompactor(max_tokens=10000, compaction_threshold=0.8)
        msgs = [
            ContextMessage(role="user", content="hello", token_estimate=10),
            ContextMessage(role="assistant", content="world", token_estimate=10),
        ]
        result = c.compact(msgs)
        assert result is msgs  # same object — no copy made

    def test_empty_list_returns_empty(self) -> None:
        c = ContextCompactor()
        result = c.compact([])
        assert result == []


# ---------------------------------------------------------------------------
# ContextCompactor.compact — custom summary_fn
# ---------------------------------------------------------------------------

class TestCompactWithSummaryFn:
    def _overloaded_messages(
        self,
        count: int = 10,
        tokens_each: int = 200,
    ) -> list[ContextMessage]:
        return [
            ContextMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i}",
                token_estimate=tokens_each,
            )
            for i in range(count)
        ]

    def test_summary_fn_is_called(self) -> None:
        """compact() calls summary_fn with the concatenated old messages."""
        c = ContextCompactor(
            max_tokens=1000,
            compaction_threshold=0.5,
            preserve_recent_count=2,
        )
        # 10 messages x 100 tokens = 1000, ratio=1.0 >= 0.5 -> needs compaction
        msgs = self._overloaded_messages(count=10, tokens_each=100)

        called_with: list[str] = []

        def fake_summary(text: str) -> str:
            called_with.append(text)
            return "SUMMARY"

        c.compact(msgs, summary_fn=fake_summary)
        assert len(called_with) == 1
        assert "message 0" in called_with[0]

    def test_summary_fn_result_in_output(self) -> None:
        """The summary_fn output appears in the compacted result."""
        c = ContextCompactor(
            max_tokens=1000,
            compaction_threshold=0.5,
            preserve_recent_count=2,
        )
        msgs = self._overloaded_messages(count=10, tokens_each=100)

        result = c.compact(msgs, summary_fn=lambda _: "MY_SUMMARY")

        # There should be a summary system message at position right after system msgs
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        assert "MY_SUMMARY" in summary_msgs[0].content

    def test_preserve_recent_count_honoured(self) -> None:
        """The last preserve_recent_count non-system messages are kept verbatim."""
        c = ContextCompactor(
            max_tokens=1000,
            compaction_threshold=0.5,
            preserve_recent_count=3,
        )
        msgs = [
            ContextMessage(role="user", content=f"msg{i}", token_estimate=100)
            for i in range(8)
        ]

        result = c.compact(msgs)

        # The last 3 messages should be present in order
        recent_content = [m.content for m in result if not m.is_system]
        assert recent_content == ["msg5", "msg6", "msg7"]

    def test_no_summary_fn_uses_truncation(self) -> None:
        """Without summary_fn, the first 500 chars of old content are used."""
        c = ContextCompactor(
            max_tokens=100,
            compaction_threshold=0.5,
            preserve_recent_count=1,
        )
        # 3 non-system messages each with a large token estimate
        long_content = "A" * 600
        msgs = [
            ContextMessage(role="user", content=long_content, token_estimate=40),
            ContextMessage(role="user", content="second", token_estimate=40),
            ContextMessage(role="user", content="keep_me", token_estimate=40),
        ]
        result = c.compact(msgs)
        summary_msgs = [m for m in result if "[prior context]" in m.content]
        assert len(summary_msgs) == 1
        # truncated at 500 chars + "..."
        assert "..." in summary_msgs[0].content


# ---------------------------------------------------------------------------
# ContextCompactor.compact — mixed system + non-system messages
# ---------------------------------------------------------------------------

class TestCompactMixedSystemMessages:
    def test_system_messages_preserved_at_front(self) -> None:
        """System messages are always placed before the summary in the output."""
        c = ContextCompactor(
            max_tokens=1000,
            compaction_threshold=0.4,
            preserve_recent_count=2,
        )
        msgs = [
            ContextMessage(role="system", content="sys1", is_system=True, token_estimate=50),
            ContextMessage(role="system", content="sys2", is_system=True, token_estimate=50),
            ContextMessage(role="user", content="u1", token_estimate=100),
            ContextMessage(role="user", content="u2", token_estimate=100),
            ContextMessage(role="user", content="u3", token_estimate=100),
            ContextMessage(role="user", content="u4", token_estimate=100),
        ]
        # total=500, ratio=0.5 >= 0.4 → needs compaction
        result = c.compact(msgs)

        # First two must be the system messages
        assert result[0].is_system is True
        assert result[0].content == "sys1"
        assert result[1].is_system is True
        assert result[1].content == "sys2"

    def test_non_system_le_preserve_recent_returns_all(self) -> None:
        """When non-system count <= preserve_recent_count, compact returns the full list."""
        c = ContextCompactor(
            max_tokens=100,
            compaction_threshold=0.1,  # guaranteed to need compaction
            preserve_recent_count=5,
        )
        msgs = [
            ContextMessage(role="system", content="sys", is_system=True, token_estimate=50),
            ContextMessage(role="user", content="u1", token_estimate=50),
            ContextMessage(role="user", content="u2", token_estimate=50),
        ]
        # non_system count = 2 <= preserve_recent_count=5 → early return
        result = c.compact(msgs)
        assert result is msgs


# ---------------------------------------------------------------------------
# TokenWindowManager.check_budget — max_tokens < remaining
# ---------------------------------------------------------------------------

class TestTokenWindowCheckBudgetMaxTokensLessThanRemaining:
    def test_max_tokens_less_than_remaining_limits_cap(self) -> None:
        """When max_tokens < remaining budget, the effective cap is max_tokens."""
        mgr = TokenWindowManager(default_budget=10000)
        # remaining = 10000 (no usage recorded), max_tokens = 50
        # A prompt longer than 50*4=200 chars should fail the budget check
        long_prompt = "x" * 300  # estimate = 75 tokens > 50 cap
        result = mgr.check_budget("agent_x", long_prompt, max_tokens=50)
        assert result is False

    def test_max_tokens_less_than_remaining_short_prompt_passes(self) -> None:
        """Short prompt fits even when max_tokens is the binding constraint."""
        mgr = TokenWindowManager(default_budget=10000)
        short_prompt = "hi"  # estimate = 0 tokens (len=2 // 4 = 0)
        result = mgr.check_budget("agent_x", short_prompt, max_tokens=50)
        assert result is True

    def test_max_tokens_zero_rejects_all_nonempty_prompts(self) -> None:
        """max_tokens=0 means budget_cap=0 so any prompt with tokens>0 fails."""
        mgr = TokenWindowManager(default_budget=10000)
        prompt = "x" * 8  # estimate = 2 tokens
        result = mgr.check_budget("agent_x", prompt, max_tokens=0)
        assert result is False
