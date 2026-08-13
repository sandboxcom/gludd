"""Deep edge-case tests for TokenWindowManager in token_window.py."""

from __future__ import annotations

from general_ludd.agents.token_window import TokenWindowManager


class TestEstimateTokens:
    def test_empty_string(self):
        assert TokenWindowManager().estimate_tokens("") == 0

    def test_single_char(self):
        assert TokenWindowManager().estimate_tokens("x") == 0

    def test_four_chars_exactly_one_token(self):
        assert TokenWindowManager().estimate_tokens("1234") == 1

    def test_five_chars_rounded_down(self):
        assert TokenWindowManager().estimate_tokens("12345") == 1

    def test_eight_chars(self):
        assert TokenWindowManager().estimate_tokens("12345678") == 2

    def test_unicode_multibyte(self):
        assert TokenWindowManager().estimate_tokens("\U0001f600\U0001f600\U0001f600\U0001f600") == 1

    def test_unicode_multibyte_five(self):
        assert TokenWindowManager().estimate_tokens("\U0001f600" * 5) == 1

    def test_huge_string(self):
        assert TokenWindowManager().estimate_tokens("x" * 10_000) == 2500


class TestConstructEdgeCases:
    def test_default_budget_zero(self):
        mgr = TokenWindowManager(default_budget=0)
        assert mgr._default_budget == 0
        assert mgr.get_remaining_budget("a") == 0

    def test_default_budget_one(self):
        mgr = TokenWindowManager(default_budget=1)
        assert mgr._default_budget == 1
        assert mgr.get_remaining_budget("a") == 1

    def test_negative_default_budget(self):
        mgr = TokenWindowManager(default_budget=-100)
        assert mgr.get_remaining_budget("a") == 0

    def test_very_large_default_budget(self):
        mgr = TokenWindowManager(default_budget=10_000_000)
        assert mgr.get_remaining_budget("a") == 10_000_000

    def test_negative_default_budget_check_behavior(self):
        mgr = TokenWindowManager(default_budget=-50)
        assert mgr.check_budget("a", "hello", max_tokens=10) is False

    def test_negative_budget_compact_context(self):
        mgr = TokenWindowManager(default_budget=-100)
        result = mgr.compact_context("a")
        assert result == ""

    def test_budget_zero_compact_context(self):
        mgr = TokenWindowManager(default_budget=0)
        result = mgr.compact_context("a")
        assert result != ""


class TestRecordUsage:
    def test_single_agent(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 300)
        assert mgr.get_remaining_budget("a") == 700

    def test_multiple_calls_accumulate(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 300)
        mgr.record_usage("a", 200)
        mgr.record_usage("a", 100)
        assert mgr.get_remaining_budget("a") == 400

    def test_multiple_agents_independent(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 600)
        mgr.record_usage("b", 200)
        assert mgr.get_remaining_budget("a") == 400
        assert mgr.get_remaining_budget("b") == 800

    def test_record_zero_tokens(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", 0)
        assert mgr.get_remaining_budget("a") == 100

    def test_record_negative_tokens(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", -50)
        assert mgr.get_remaining_budget("a") == 150

    def test_record_exactly_budget(self):
        mgr = TokenWindowManager(default_budget=500)
        mgr.record_usage("a", 500)
        assert mgr.get_remaining_budget("a") == 0

    def test_record_exceeds_budget(self):
        mgr = TokenWindowManager(default_budget=500)
        mgr.record_usage("a", 700)
        assert mgr.get_remaining_budget("a") == 0

    def test_record_negative_exceeds_default(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", -200)
        assert mgr.get_remaining_budget("a") == 300

    def test_agent_not_yet_recorded(self):
        mgr = TokenWindowManager(default_budget=1000)
        assert mgr.get_remaining_budget("nonexistent") == 1000

    def test_record_after_exhaustion(self):
        mgr = TokenWindowManager(default_budget=200)
        mgr.record_usage("a", 200)
        mgr.record_usage("a", 50)
        assert mgr.get_remaining_budget("a") == 0
        mgr.record_usage("a", -75)
        assert mgr.get_remaining_budget("a") == 25


class TestGetRemainingBudget:
    def test_exactly_zero_remaining(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", 100)
        assert mgr.get_remaining_budget("a") == 0

    def test_one_remaining(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", 99)
        assert mgr.get_remaining_budget("a") == 1

    def test_negative_default_returns_zero(self):
        mgr = TokenWindowManager(default_budget=-10)
        assert mgr.get_remaining_budget("a") == 0

    def test_empty_string_agent_name(self):
        mgr = TokenWindowManager(default_budget=50)
        assert mgr.get_remaining_budget("") == 50
        mgr.record_usage("", 30)
        assert mgr.get_remaining_budget("") == 20


class TestCheckBudget:
    def test_under_budget(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "x" * 40, max_tokens=50) is True

    def test_exact_fit_at_budget_cap(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "x" * 40, max_tokens=10) is True

    def test_one_token_over_max_tokens(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "x" * 44, max_tokens=10) is False

    def test_max_tokens_zero(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "", max_tokens=0) is True
        assert mgr.check_budget("a", "xxxx", max_tokens=0) is False

    def test_max_tokens_negative(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "x", max_tokens=-10) is False

    def test_empty_prompt(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("a", "", max_tokens=10) is True

    def test_unknown_agent_gets_full_budget(self):
        mgr = TokenWindowManager(default_budget=100)
        assert mgr.check_budget("newcomer", "x" * 40, max_tokens=20) is True

    def test_exhausted_agent(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", 100)
        assert mgr.check_budget("a", "", max_tokens=10) is True
        assert mgr.check_budget("a", "xxxx", max_tokens=10) is False

    def test_budget_caps_max_tokens(self):
        mgr = TokenWindowManager(default_budget=20)
        assert mgr.check_budget("a", "x" * 40, max_tokens=100) is True

    def test_prompt_exceeds_budget_but_under_max(self):
        mgr = TokenWindowManager(default_budget=10)
        assert mgr.check_budget("a", "x" * 80, max_tokens=100) is False

    def test_exactly_at_remaining_budget(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("a", 80)
        assert mgr.check_budget("a", "x" * 80, max_tokens=100) is True


class TestCompactContext:
    def test_above_threshold_returns_empty(self):
        mgr = TokenWindowManager(default_budget=1000)
        assert mgr.compact_context("a") == ""

    def test_exactly_at_threshold_returns_nonempty(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 800)
        result = mgr.compact_context("a")
        assert result != ""
        assert "200 tokens remaining" in result

    def test_one_below_threshold_returns_nonempty(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 801)
        result = mgr.compact_context("a")
        assert result != ""

    def test_one_above_threshold_returns_empty(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 799)
        result = mgr.compact_context("a")
        assert result == ""

    def test_exhausted_budget_returns_nonempty(self):
        mgr = TokenWindowManager(default_budget=500)
        mgr.record_usage("a", 500)
        result = mgr.compact_context("a")
        assert result != ""
        assert "0 tokens remaining" in result

    def test_over_exhausted_returns_nonempty(self):
        mgr = TokenWindowManager(default_budget=500)
        mgr.record_usage("a", 600)
        result = mgr.compact_context("a")
        assert result != ""

    def test_default_budget_one_threshold(self):
        mgr = TokenWindowManager(default_budget=1)
        assert mgr.compact_context("a") == ""
        mgr.record_usage("a", 1)
        result = mgr.compact_context("a")
        assert result != ""

    def test_default_budget_two_threshold(self):
        mgr = TokenWindowManager(default_budget=2)
        assert mgr.compact_context("a") == ""
        mgr.record_usage("a", 1)
        assert mgr.compact_context("a") == ""
        mgr.record_usage("a", 1)
        result = mgr.compact_context("a")
        assert result != ""

    def test_compact_context_includes_agent_name(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("agent-42", 900)
        result = mgr.compact_context("agent-42")
        assert "agent-42" in result

    def test_fractional_threshold_floor_behavior(self):
        mgr = TokenWindowManager(default_budget=7)
        mgr.record_usage("a", 6)
        result = mgr.compact_context("a")
        assert result != ""

    def test_two_agents_one_compacted_one_not(self):
        mgr = TokenWindowManager(default_budget=1000)
        mgr.record_usage("a", 900)
        mgr.record_usage("b", 100)
        assert mgr.compact_context("a") != ""
        assert mgr.compact_context("b") == ""

    def test_compact_negative_budget(self):
        mgr = TokenWindowManager(default_budget=-10)
        result = mgr.compact_context("a")
        assert result == ""

    def test_compact_zero_budget(self):
        mgr = TokenWindowManager(default_budget=0)
        result = mgr.compact_context("a")
        assert result != ""


class TestIntegratedScenarios:
    def test_full_lifecycle_then_compact(self):
        mgr = TokenWindowManager(default_budget=200)
        assert mgr.check_budget("a", "x" * 400, max_tokens=200) is True
        mgr.record_usage("a", 100)
        assert mgr.get_remaining_budget("a") == 100
        mgr.record_usage("a", 30)
        assert mgr.get_remaining_budget("a") == 70
        assert mgr.check_budget("a", "x" * 200, max_tokens=100) is True
        mgr.record_usage("a", 50)
        assert mgr.compact_context("a") != ""

    def test_multi_agent_no_cross_contamination(self):
        mgr = TokenWindowManager(default_budget=500)
        mgr.record_usage("a", 400)
        mgr.record_usage("b", 50)
        assert mgr.compact_context("a") != ""
        assert mgr.compact_context("b") == ""
        assert mgr.check_budget("b", "x" * 200, max_tokens=500) is True

    def test_check_against_max_tokens_stricter_than_budget(self):
        mgr = TokenWindowManager(default_budget=10_000)
        assert mgr.check_budget("a", "x" * 200, max_tokens=10) is False

    def test_check_against_budget_stricter_than_max_tokens(self):
        mgr = TokenWindowManager(default_budget=10)
        assert mgr.check_budget("a", "x" * 200, max_tokens=10_000) is False

    def test_estimate_tokens_idempotent(self):
        mgr = TokenWindowManager()
        assert mgr.estimate_tokens("hello world") == mgr.estimate_tokens("hello world")

    def test_estimate_tokens_deterministic(self):
        mgr = TokenWindowManager()
        results = [mgr.estimate_tokens("testing " * i) for i in range(100)]
        assert all(r >= 0 for r in results)
        assert results == sorted(results)
