"""Unit tests for general_ludd.agents.token_window.TokenWindowManager.

Coverage targets:
- __init__ (default + custom budget)
- estimate_tokens (happy path, empty string, non-divisible lengths)
- get_remaining_budget (new agent, after usage, exhausted budget)
- record_usage (first use, cumulative, zero tokens)
- check_budget (fits, too large, max_tokens cap, exhausted)
- compact_context (above threshold → empty string, at/below threshold → message)
"""

from __future__ import annotations

import pytest

from general_ludd.agents.token_window import TokenWindowManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mgr() -> TokenWindowManager:
    """Default-budget manager (128 000 tokens)."""
    return TokenWindowManager()


@pytest.fixture()
def small_mgr() -> TokenWindowManager:
    """Small budget manager for overflow/threshold tests."""
    return TokenWindowManager(default_budget=1000)


# ---------------------------------------------------------------------------
# __init__ / window sizing
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_budget(self, mgr: TokenWindowManager) -> None:
        assert mgr._default_budget == 128_000

    def test_custom_budget(self) -> None:
        m = TokenWindowManager(default_budget=4096)
        assert m._default_budget == 4096

    def test_usage_starts_empty(self, mgr: TokenWindowManager) -> None:
        assert mgr._usage == {}

    def test_zero_budget_allowed(self) -> None:
        m = TokenWindowManager(default_budget=0)
        assert m._default_budget == 0

    def test_large_budget(self) -> None:
        m = TokenWindowManager(default_budget=10_000_000)
        assert m._default_budget == 10_000_000


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string(self, mgr: TokenWindowManager) -> None:
        assert mgr.estimate_tokens("") == 0

    def test_four_chars_is_one_token(self, mgr: TokenWindowManager) -> None:
        assert mgr.estimate_tokens("abcd") == 1

    def test_eight_chars_is_two_tokens(self, mgr: TokenWindowManager) -> None:
        assert mgr.estimate_tokens("abcdefgh") == 2

    def test_truncates_not_rounds(self, mgr: TokenWindowManager) -> None:
        # 5 chars → 5 // 4 = 1 (integer division, not rounding)
        assert mgr.estimate_tokens("abcde") == 1

    def test_three_chars_is_zero(self, mgr: TokenWindowManager) -> None:
        assert mgr.estimate_tokens("abc") == 0

    def test_large_text(self, mgr: TokenWindowManager) -> None:
        text = "x" * 4000
        assert mgr.estimate_tokens(text) == 1000

    def test_whitespace_counted(self, mgr: TokenWindowManager) -> None:
        # spaces count as characters
        assert mgr.estimate_tokens("    ") == 1

    def test_unicode_chars_counted_by_len(self, mgr: TokenWindowManager) -> None:
        # len("こんにちは") == 5 → 5 // 4 == 1
        assert mgr.estimate_tokens("こんにちは") == 1


# ---------------------------------------------------------------------------
# get_remaining_budget
# ---------------------------------------------------------------------------


class TestGetRemainingBudget:
    def test_new_agent_full_budget(self, mgr: TokenWindowManager) -> None:
        assert mgr.get_remaining_budget("agent-x") == 128_000

    def test_after_partial_usage(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("agent-x", 1000)
        assert mgr.get_remaining_budget("agent-x") == 127_000

    def test_after_full_usage(self, small_mgr: TokenWindowManager) -> None:
        small_mgr.record_usage("agent-y", 1000)
        assert small_mgr.get_remaining_budget("agent-y") == 0

    def test_never_negative(self, small_mgr: TokenWindowManager) -> None:
        # Over-record beyond budget
        small_mgr.record_usage("agent-z", 2000)
        assert small_mgr.get_remaining_budget("agent-z") == 0

    def test_independent_agents(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("alice", 5000)
        assert mgr.get_remaining_budget("bob") == 128_000

    def test_zero_budget_manager(self) -> None:
        m = TokenWindowManager(default_budget=0)
        assert m.get_remaining_budget("any") == 0


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------


class TestRecordUsage:
    def test_first_record(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("agent-a", 100)
        assert mgr._usage["agent-a"] == 100

    def test_cumulative_records(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("agent-a", 100)
        mgr.record_usage("agent-a", 200)
        assert mgr._usage["agent-a"] == 300

    def test_zero_tokens(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("agent-a", 0)
        assert mgr._usage["agent-a"] == 0

    def test_multiple_agents_isolated(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("alice", 500)
        mgr.record_usage("bob", 300)
        assert mgr._usage["alice"] == 500
        assert mgr._usage["bob"] == 300

    def test_large_usage(self, mgr: TokenWindowManager) -> None:
        mgr.record_usage("big-agent", 128_000)
        assert mgr._usage["big-agent"] == 128_000


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


class TestCheckBudget:
    def test_fits_within_budget(self, mgr: TokenWindowManager) -> None:
        # 40 chars → 10 tokens; remaining=128000; max_tokens=128000
        prompt = "x" * 40
        assert mgr.check_budget("agent", prompt, max_tokens=128_000) is True

    def test_exceeds_max_tokens_cap(self, mgr: TokenWindowManager) -> None:
        # 400 chars → 100 tokens; max_tokens=50 → cap is 50 → 100 > 50
        prompt = "x" * 400
        assert mgr.check_budget("agent", prompt, max_tokens=50) is False

    def test_exceeds_remaining_budget(self, small_mgr: TokenWindowManager) -> None:
        # Use up all budget first
        small_mgr.record_usage("agent", 1000)
        prompt = "x" * 40  # 10 tokens
        assert small_mgr.check_budget("agent", prompt, max_tokens=1000) is False

    def test_exactly_at_cap(self, mgr: TokenWindowManager) -> None:
        # 40 chars → 10 tokens; max_tokens=10; 10 <= 10 → True
        prompt = "x" * 40
        assert mgr.check_budget("agent", prompt, max_tokens=10) is True

    def test_one_over_cap(self, mgr: TokenWindowManager) -> None:
        # 44 chars → 11 tokens; max_tokens=10 → 11 > 10 → False
        prompt = "x" * 44
        assert mgr.check_budget("agent", prompt, max_tokens=10) is False

    def test_empty_prompt_always_fits(self, mgr: TokenWindowManager) -> None:
        assert mgr.check_budget("agent", "", max_tokens=0) is True

    def test_cap_is_min_of_max_tokens_and_remaining(self, small_mgr: TokenWindowManager) -> None:
        # Budget=1000, used=900, remaining=100; max_tokens=500
        # budget_cap = min(500, 100) = 100; 20 tokens (80 chars) ≤ 100 → fits
        small_mgr.record_usage("agent", 900)
        prompt = "x" * 80  # 20 tokens; 20 <= 100 → True
        assert small_mgr.check_budget("agent", prompt, max_tokens=500) is True

    def test_cap_clamps_to_remaining_when_smaller(self, small_mgr: TokenWindowManager) -> None:
        # Budget=1000, used=900, remaining=100; max_tokens=500
        # budget_cap = min(500, 100) = 100; 440 chars = 110 tokens > 100 → False
        small_mgr.record_usage("agent2", 900)
        prompt = "x" * 440  # 110 tokens; 110 > 100 → False
        assert small_mgr.check_budget("agent2", prompt, max_tokens=500) is False

    def test_zero_max_tokens_empty_prompt(self, mgr: TokenWindowManager) -> None:
        # 0 estimated tokens <= 0 budget_cap
        assert mgr.check_budget("agent", "", max_tokens=0) is True

    def test_new_agent_large_prompt(self, mgr: TokenWindowManager) -> None:
        # 4000 chars → 1000 tokens; default budget=128000; max_tokens=2000
        prompt = "y" * 4000
        assert mgr.check_budget("new-agent", prompt, max_tokens=2000) is True


# ---------------------------------------------------------------------------
# compact_context  (priority retention / overflow truncation behaviour)
# ---------------------------------------------------------------------------


class TestCompactContext:
    def test_above_threshold_returns_empty(self, small_mgr: TokenWindowManager) -> None:
        # Budget=1000, threshold=200; remaining=1000 > 200 → ""
        result = small_mgr.compact_context("agent")
        assert result == ""

    def test_at_threshold_triggers_compact(self, small_mgr: TokenWindowManager) -> None:
        # threshold = 1000 * 0.2 = 200; use 800 so remaining=200; 200 > 200 is False → compact
        small_mgr.record_usage("agent", 800)
        result = small_mgr.compact_context("agent")
        assert result != ""
        assert "agent" in result
        assert "200" in result

    def test_below_threshold_triggers_compact(self, small_mgr: TokenWindowManager) -> None:
        # remaining=50 < 200 threshold → compact message
        small_mgr.record_usage("agent", 950)
        result = small_mgr.compact_context("agent")
        assert "agent" in result
        assert "50" in result

    def test_exhausted_budget_triggers_compact(self, small_mgr: TokenWindowManager) -> None:
        small_mgr.record_usage("agent", 1000)
        result = small_mgr.compact_context("agent")
        assert "0" in result
        assert "agent" in result

    def test_compact_message_format(self, small_mgr: TokenWindowManager) -> None:
        small_mgr.record_usage("my-agent", 950)
        result = small_mgr.compact_context("my-agent")
        expected = "[compacted context for my-agent: 50 tokens remaining]"
        assert result == expected

    def test_just_above_threshold_no_compact(self, small_mgr: TokenWindowManager) -> None:
        # threshold=200; use 799 → remaining=201 > 200 → ""
        small_mgr.record_usage("agent", 799)
        result = small_mgr.compact_context("agent")
        assert result == ""

    def test_agent_name_in_compact_message(self, small_mgr: TokenWindowManager) -> None:
        small_mgr.record_usage("special-agent-007", 999)
        result = small_mgr.compact_context("special-agent-007")
        assert "special-agent-007" in result

    def test_default_budget_well_above_threshold(self, mgr: TokenWindowManager) -> None:
        # default budget 128000, threshold=25600; fresh agent → remaining=128000 → ""
        result = mgr.compact_context("fresh-agent")
        assert result == ""

    def test_default_budget_near_exhaustion(self, mgr: TokenWindowManager) -> None:
        # Use 110000 → remaining=18000; threshold=25600; 18000 < 25600 → compact
        mgr.record_usage("tired-agent", 110_000)
        result = mgr.compact_context("tired-agent")
        assert "tired-agent" in result
        assert "18000" in result

    def test_over_budget_remaining_clamped_to_zero(self, small_mgr: TokenWindowManager) -> None:
        # Over-record; remaining=0; threshold=200; 0 > 200 False → compact with 0
        small_mgr.record_usage("agent", 5000)
        result = small_mgr.compact_context("agent")
        assert "0" in result
