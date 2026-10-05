"""Deep token budget and allocation tests — envelope system, credit tracking,
peak pricing, off-peak scheduling, and combined cost tracking."""

from __future__ import annotations

import datetime
import time
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.budget.combined_cost import CombinedCostTracker
from general_ludd.budget.credit_tracker import (
    DEFAULT_THRESHOLDS,
    CreditTracker,
    _parse_deepseek,
    _parse_openrouter,
    _parse_zai,
)
from general_ludd.budget.envelope import (
    BudgetCheckResult,
    BudgetEnvelope,
    BudgetManager,
    PerAgentEnvelope,
    PerTaskEnvelope,
    PerToolEnvelope,
)
from general_ludd.budget.off_peak_scheduler import (
    OffPeakScheduler,
    OffPeakTicket,
    SavingsTracker,
)
from general_ludd.budget.peak_pricing import (
    PeakPricingSchedule,
    PeakPricingTracker,
    RateTier,
    current_rate_multiplier,
)


def _local_wall_time(hour: int) -> float:
    """Return a stable timestamp whose local-time hour is ``hour``."""
    return datetime.datetime(2026, 1, 15, hour).timestamp()


# ---------------------------------------------------------------------------
# BudgetEnvelope
# ---------------------------------------------------------------------------


class TestBudgetEnvelope:
    """BudgetEnvelope: atomic allocation, deny, invalid inputs, reset."""

    def test_initial_state(self) -> None:
        env = BudgetEnvelope("test", limit=10.0)
        assert env.name == "test"
        assert env.limit == 10.0
        assert env.spent == 0.0
        assert env.remaining == 10.0
        assert not env.is_exhausted

    def test_spend_within_budget(self) -> None:
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(5.0)
        assert result["allowed"] is True
        assert env.spent == 5.0
        assert env.remaining == 5.0

    def test_spend_up_to_limit(self) -> None:
        env = BudgetEnvelope("exact", limit=3.0)
        assert env.try_spend(3.0)["allowed"] is True
        assert env.is_exhausted
        assert env.remaining == 0.0

    def test_deny_when_budget_exceeded(self) -> None:
        env = BudgetEnvelope("tight", limit=2.0)
        env.try_spend(1.5)
        result = env.try_spend(1.0)
        assert result["allowed"] is False
        assert "exceeded" in str(result["reason"])
        assert env.spent == 1.5  # unchanged

    def test_infinite_limit_always_allows(self) -> None:
        env = BudgetEnvelope("infinite")
        assert env.limit == float("inf")
        result = env.try_spend(1e9)
        assert result["allowed"] is True

    def test_negative_amount_denied(self) -> None:
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(-5.0)
        assert result["allowed"] is False
        assert "negative" in str(result["reason"])

    def test_nan_amount_denied(self) -> None:
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(float("nan"))
        assert result["allowed"] is False

    def test_inf_amount_denied(self) -> None:
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(float("inf"))
        assert result["allowed"] is False

    def test_record_spend_outside_gate(self) -> None:
        env = BudgetEnvelope("external", limit=100.0)
        env.record_spend(30.0)
        assert env.spent == 30.0
        env.record_spend(70.0)
        assert env.is_exhausted

    def test_record_spend_rejects_invalid(self) -> None:
        env = BudgetEnvelope("test", limit=50.0)
        with pytest.raises(ValueError):
            env.record_spend(-1.0)
        with pytest.raises(ValueError):
            env.record_spend(float("nan"))

    def test_reset(self) -> None:
        env = BudgetEnvelope("reset-me", limit=10.0)
        env.try_spend(8.0)
        env.reset()
        assert env.spent == 0.0
        assert env.remaining == 10.0
        assert not env.is_exhausted

    def test_get_status_full(self) -> None:
        env = BudgetEnvelope("status", limit=5.0)
        env.try_spend(3.0)
        status = env.get_status()
        assert status["name"] == "status"
        assert status["limit"] == 5.0
        assert status["spent"] == 3.0
        assert status["remaining"] == 2.0
        assert status["exhausted"] is False

    def test_negative_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            BudgetEnvelope("bad", limit=-1.0)

    def test_nan_limit_raises(self) -> None:
        with pytest.raises(ValueError):
            BudgetEnvelope("bad", limit=float("nan"))


# ---------------------------------------------------------------------------
# PerAgentEnvelope
# ---------------------------------------------------------------------------


class TestPerAgentEnvelope:
    """Per-model budget — no-limit fallback, per-model caps, aggregation."""

    def test_no_limit_configured_allows_all(self) -> None:
        pa = PerAgentEnvelope()
        result = pa.try_spend("sonnet", 50.0)
        assert result["allowed"] is True
        assert result["remaining"] == float("inf")

    def test_set_and_enforce_model_limit(self) -> None:
        pa = PerAgentEnvelope()
        pa.set_limit("opus", 10.0)
        assert pa.try_spend("opus", 6.0)["allowed"] is True
        assert pa.try_spend("opus", 5.0)["allowed"] is False

    def test_update_existing_limit(self) -> None:
        pa = PerAgentEnvelope()
        pa.set_limit("opus", 10.0)
        pa.try_spend("opus", 9.0)
        pa.set_limit("opus", 20.0)
        result = pa.try_spend("opus", 5.0)
        assert result["allowed"] is True

    def test_total_spent_aggregation(self) -> None:
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 10.0)
        pa.set_limit("haiku", 5.0)
        pa.try_spend("sonnet", 4.0)
        pa.try_spend("haiku", 3.0)
        assert pa.total_spent() == 7.0

    def test_reset_all(self) -> None:
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 10.0)
        pa.try_spend("sonnet", 4.0)
        pa.reset_all()
        assert pa.total_spent() == 0.0


# ---------------------------------------------------------------------------
# PerTaskEnvelope
# ---------------------------------------------------------------------------


class TestPerTaskEnvelope:
    """Per-task budget — default limit, auto-envelope creation, reset."""

    def test_auto_create_envelope_with_default_limit(self) -> None:
        pt = PerTaskEnvelope(default_limit=5.0)
        result = pt.try_spend("task-1", 3.0)
        assert result["allowed"] is True
        result2 = pt.try_spend("task-1", 3.0)
        assert result2["allowed"] is False

    def test_no_default_limit_tasks_unlimited(self) -> None:
        pt = PerTaskEnvelope()
        result = pt.try_spend("any-task", 1e6)
        assert result["allowed"] is True

    def test_explicit_limit_overrides_default(self) -> None:
        pt = PerTaskEnvelope(default_limit=5.0)
        pt.set_limit("critical", 100.0)
        assert pt.try_spend("critical", 80.0)["allowed"] is True
        assert pt.try_spend("critical", 30.0)["allowed"] is False

    def test_reset_all_clears_tasks(self) -> None:
        pt = PerTaskEnvelope(default_limit=10.0)
        pt.try_spend("t1", 5.0)
        pt.reset_all()
        assert pt.total_spent() == 0.0
        result = pt.try_spend("t1", 9.0)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# PerToolEnvelope
# ---------------------------------------------------------------------------


class TestPerToolEnvelope:
    """Per-tool budget — individual tool caps, independent spend tracking."""

    def test_tool_budget_enforcement(self) -> None:
        ptool = PerToolEnvelope()
        ptool.set_limit("bash", 3.0)
        assert ptool.try_spend("bash", 2.0)["allowed"] is True
        assert ptool.try_spend("bash", 2.0)["allowed"] is False
        assert ptool.try_spend("write", 100.0)["allowed"] is True

    def test_unconfigured_tool_allows_all(self) -> None:
        ptool = PerToolEnvelope()
        result = ptool.try_spend("unknown_tool", 9999.0)
        assert result["allowed"] is True

    def test_tool_total_spent(self) -> None:
        ptool = PerToolEnvelope()
        ptool.set_limit("bash", 10.0)
        ptool.set_limit("edit", 10.0)
        ptool.try_spend("bash", 3.0)
        ptool.try_spend("edit", 2.0)
        assert ptool.total_spent() == 5.0


# ---------------------------------------------------------------------------
# BudgetManager
# ---------------------------------------------------------------------------


class TestBudgetManager:
    """Layered budget coordinator — tool→task→agent ordering, aggregation."""

    def test_tool_layer_blocks_first(self) -> None:
        mgr = BudgetManager()
        mgr.per_tool.set_limit("bash", 1.0)
        mgr.per_tool.try_spend("bash", 0.8)
        result = mgr.check_all(tool_type="bash", amount=0.5)
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_task_layer_blocks_second(self) -> None:
        mgr = BudgetManager()
        mgr.per_task.set_limit("task-42", 5.0)
        mgr.per_task.try_spend("task-42", 5.0)
        result = mgr.check_all(task_id="task-42", amount=1.0)
        assert result.allowed is False
        assert result.details["layer"] == "task"

    def test_agent_layer_blocks_third(self) -> None:
        mgr = BudgetManager()
        mgr.per_agent.set_limit("opus", 1.0)
        mgr.per_agent.try_spend("opus", 0.9)
        result = mgr.check_all(agent_type="opus", amount=0.5)
        assert result.allowed is False
        assert result.details["layer"] == "agent"

    def test_all_layers_pass(self) -> None:
        mgr = BudgetManager()
        mgr.per_tool.set_limit("bash", 100.0)
        mgr.per_task.set_limit("task-1", 100.0)
        mgr.per_agent.set_limit("sonnet", 100.0)
        result = mgr.check_all(tool_type="bash", task_id="task-1", agent_type="sonnet", amount=5.0)
        assert result.allowed is True

    def test_total_spent_aggregates_all_layers(self) -> None:
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 10.0)
        mgr.per_task.set_limit("t1", 10.0)
        mgr.per_tool.set_limit("bash", 10.0)
        mgr.check_all(agent_type="sonnet", amount=2.0)
        mgr.check_all(task_id="t1", amount=3.0)
        mgr.check_all(tool_type="bash", amount=1.0)
        assert mgr.total_spent() > 0

    def test_reset_all_layers(self) -> None:
        mgr = BudgetManager()
        mgr.per_agent.set_limit("opus", 10.0)
        mgr.check_all(agent_type="opus", amount=7.0)
        mgr.reset_all()
        assert mgr.total_spent() == 0.0

    def test_check_all_with_only_agent(self) -> None:
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 5.0)
        assert mgr.check_all(agent_type="sonnet", amount=3.0).allowed
        assert not mgr.check_all(agent_type="sonnet", amount=3.0).allowed

    def test_check_all_with_none_arguments(self) -> None:
        mgr = BudgetManager()
        result = mgr.check_all(amount=100.0)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# RateTier / PeakPricingSchedule / PeakPricingTracker
# ---------------------------------------------------------------------------


class TestRateTier:
    """RateTier — time-window coverage, equality, validation."""

    _MONDAY = frozenset({0})
    _WEEKDAY = frozenset({0, 1, 2, 3, 4})

    def test_covers_within_window(self) -> None:
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", self._WEEKDAY, 9, 17)
        dt = datetime.datetime(2026, 8, 3, 12, 0, tzinfo=datetime.UTC)  # Monday
        assert tier.covers(dt) is True

    def test_covers_outside_window(self) -> None:
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", self._WEEKDAY, 9, 17)
        dt = datetime.datetime(2026, 8, 3, 6, 0, tzinfo=datetime.UTC)  # Monday 6am
        assert tier.covers(dt) is False

    def test_covers_wrong_day(self) -> None:
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", self._WEEKDAY, 9, 17)
        dt = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)  # Saturday
        assert tier.covers(dt) is False

    def test_covers_overnight_window(self) -> None:
        tier = RateTier("gpt-4o", "openai", 1.25, "off-peak", frozenset(range(7)), 17, 9)
        assert tier.covers(datetime.datetime(2026, 8, 3, 20, 0, tzinfo=datetime.UTC)) is True
        assert tier.covers(datetime.datetime(2026, 8, 3, 3, 0, tzinfo=datetime.UTC)) is True
        assert tier.covers(datetime.datetime(2026, 8, 3, 12, 0, tzinfo=datetime.UTC)) is False

    def test_equality_semantics(self) -> None:
        a = RateTier("m", "p", 1.0, "peak", self._WEEKDAY, 9, 17)
        b = RateTier("m", "p", 1.0, "peak", self._WEEKDAY, 9, 17)
        c = RateTier("m2", "p", 1.0, "peak", self._WEEKDAY, 9, 17)
        assert a == b
        assert hash(a) == hash(b)
        assert a != c

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            RateTier("m", "p", -1.0, "peak", self._WEEKDAY, 9, 17)

    def test_invalid_hours_raises(self) -> None:
        with pytest.raises(ValueError):
            RateTier("m", "p", 1.0, "peak", self._WEEKDAY, 24, 17)
        with pytest.raises(ValueError):
            RateTier("m", "p", 1.0, "peak", self._WEEKDAY, 9, -1)


class TestPeakPricingSchedule:
    """PeakPricingSchedule — add/remove tiers, matching, enumeration."""

    def test_add_and_match_tier(self) -> None:
        schedule = PeakPricingSchedule()
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", frozenset(range(7)), 0, 23)
        schedule.add_tier(tier)
        match = schedule.matching_tier("gpt-4o", "openai", _utc_monday_noon())
        assert match is not None
        assert match.rate == 2.50

    def test_no_tier_returns_none(self) -> None:
        schedule = PeakPricingSchedule()
        assert schedule.matching_tier("unknown", "openai", _utc_monday_noon()) is None

    def test_remove_tier(self) -> None:
        schedule = PeakPricingSchedule()
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", frozenset(range(7)), 0, 23)
        schedule.add_tier(tier)
        schedule.remove_tier(tier)
        assert schedule.matching_tier("gpt-4o", "openai", _utc_monday_noon()) is None

    def test_duplicate_tier_not_added(self) -> None:
        schedule = PeakPricingSchedule()
        tier = RateTier("gpt-4o", "openai", 2.50, "peak", frozenset(range(7)), 0, 23)
        schedule.add_tier(tier)
        schedule.add_tier(tier)
        assert len(schedule.tiers_for("gpt-4o", "openai")) == 1

    def test_all_providers_and_models(self) -> None:
        schedule = PeakPricingSchedule()
        schedule.add_tier(RateTier("m1", "p1", 1.0, "peak", frozenset(range(7)), 0, 23))
        schedule.add_tier(RateTier("m2", "p2", 1.0, "peak", frozenset(range(7)), 0, 23))
        assert set(schedule.all_providers()) == {"p1", "p2"}
        assert set(schedule.all_model_ids()) == {"m1", "m2"}

    def test_clear(self) -> None:
        schedule = PeakPricingSchedule()
        schedule.add_tier(RateTier("m1", "p1", 1.0, "peak", frozenset(range(7)), 0, 23))
        schedule.clear()
        assert schedule.matching_tier("m1", "p1", _utc_monday_noon()) is None


class TestPeakPricingTracker:
    """PeakPricingTracker — savings accumulation, singleton."""

    def test_record_savings(self) -> None:
        tracker = PeakPricingTracker()
        tracker.record_call(base_cost=10.0, effective_cost=6.0)
        assert tracker.cumulative_savings == 4.0
        assert tracker.cumulative_full_cost == 10.0
        assert tracker.cumulative_discounted_cost == 6.0

    def test_no_savings_when_effective_higher(self) -> None:
        tracker = PeakPricingTracker()
        tracker.record_call(base_cost=5.0, effective_cost=10.0)
        assert tracker.cumulative_savings == 0.0

    def test_multiple_calls_accumulate(self) -> None:
        tracker = PeakPricingTracker()
        tracker.record_call(10.0, 7.0)
        tracker.record_call(20.0, 15.0)
        assert tracker.cumulative_savings == 8.0

    def test_singleton_returns_same_instance(self) -> None:
        a = PeakPricingTracker.singleton()
        b = PeakPricingTracker.singleton()
        assert a is b


# ---------------------------------------------------------------------------
# current_rate_multiplier
# ---------------------------------------------------------------------------


class TestCurrentRateMultiplier:
    """current_rate_multiplier — peak/off-peak/weekend logic."""

    def test_weekday_peak(self) -> None:
        dt = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)  # Tue noon
        assert current_rate_multiplier(dt) == 1.0

    def test_weekend_off_peak(self) -> None:
        dt = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)  # Sat noon
        assert current_rate_multiplier(dt) == 0.75


# ---------------------------------------------------------------------------
# CreditTracker — parser functions
# ---------------------------------------------------------------------------


class TestCreditTrackerParsers:
    """Per-provider balance response parsers."""

    def test_parse_deepseek_usd_wallet(self) -> None:
        data = {"wallets": [{"balance": 42.50, "currency": "USD"}]}
        balance, currency = _parse_deepseek(data)
        assert balance == 42.50
        assert currency == "USD"

    def test_parse_deepseek_no_usd_fallback(self) -> None:
        data = {"wallets": [{"balance": 100.0, "currency": "CNY"}]}
        balance, currency = _parse_deepseek(data)
        assert balance == 100.0
        assert currency == "CNY"

    def test_parse_deepseek_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_deepseek({"wallets": []})

    def test_parse_zai_valid(self) -> None:
        data = {"data": {"balance": 15.75, "currency": "USD"}}
        balance, currency = _parse_zai(data)
        assert balance == 15.75
        assert currency == "USD"

    def test_parse_zai_missing_balance_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_zai({"data": {}})

    def test_parse_openrouter_valid(self) -> None:
        data = {"data": {"total_credits": 50.0, "total_usage": 30.0}}
        balance, currency = _parse_openrouter(data)
        assert balance == 20.0
        assert currency == "USD"

    def test_parse_openrouter_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_openrouter({"data": {"total_credits": 50.0}})


# ---------------------------------------------------------------------------
# CreditTracker — should_refill / recommend_refill_amount
# ---------------------------------------------------------------------------


class TestCreditTrackerRefill:
    """CreditTracker refill logic — threshold checks, amount recommendations."""

    def test_should_refill_below_threshold(self) -> None:
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": {"balance": 0.50, "currency": "USD"},
        }
        fake_client.get.return_value = fake_response

        tracker = CreditTracker(
            api_keys={"zai": "test-key"},
            http_client=fake_client,
        )
        assert tracker.should_refill("zai") is True

    def test_should_refill_above_threshold(self) -> None:
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": {"balance": 10.0, "currency": "USD"},
        }
        fake_client.get.return_value = fake_response

        tracker = CreditTracker(
            api_keys={"zai": "test-key"},
            http_client=fake_client,
        )
        assert tracker.should_refill("zai") is False

    def test_should_refill_unknown_balance_returns_true(self) -> None:
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.status_code = 403
        fake_client.get.return_value = fake_response

        tracker = CreditTracker(
            api_keys={"openrouter": "test-key"},
            http_client=fake_client,
        )
        assert tracker.should_refill("openrouter") is True

    def test_recommend_refill_without_history(self) -> None:
        tracker = CreditTracker()
        amount = tracker.recommend_refill_amount("deepseek")
        assert amount == DEFAULT_THRESHOLDS["deepseek"] * 2.0

    def test_recommend_refill_with_spend_rate(self) -> None:
        tracker = CreditTracker(historical_spend_rates={"openai": 3.0})
        amount = tracker.recommend_refill_amount("openai")
        assert amount == 3.0 * 7.0  # DEFAULT_REFILL_DAYS

    def test_recommend_refill_zero_rate_falls_back(self) -> None:
        tracker = CreditTracker(historical_spend_rates={"deepseek": 0.0})
        amount = tracker.recommend_refill_amount("deepseek")
        assert amount == DEFAULT_THRESHOLDS["deepseek"] * 2.0


# ---------------------------------------------------------------------------
# CreditTracker — general
# ---------------------------------------------------------------------------


class TestCreditTrackerGeneral:
    """CreditTracker — unsupported service, set_spend_limit, defaults."""

    def test_unsupported_service_raises(self) -> None:
        tracker = CreditTracker()
        with pytest.raises(ValueError, match="Unsupported"):
            tracker.check_balance("unknown_provider")

    def test_set_spend_limit_unsupported(self) -> None:
        tracker = CreditTracker()
        result = tracker.set_spend_limit("deepseek", 50.0)
        assert result["supported"] is False
        assert result["applied"] is False

    def test_set_spend_limit_supported(self) -> None:
        tracker = CreditTracker()
        result = tracker.set_spend_limit("openrouter", 100.0)
        assert result["supported"] is True

    def test_set_spend_limit_negative_raises(self) -> None:
        tracker = CreditTracker()
        with pytest.raises(ValueError):
            tracker.set_spend_limit("openrouter", -10.0)

    def test_check_balance_missing_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            tracker = CreditTracker()
            result = tracker.check_balance("deepseek")
            assert result["error"] == "missing_api_key"
            assert result["balance_usd"] is None

    def test_default_thresholds_loaded(self) -> None:
        tracker = CreditTracker()
        for service in ["deepseek", "openai", "zai", "openrouter"]:
            assert tracker.get_balance_threshold(service) > 0


# ---------------------------------------------------------------------------
# SavingsTracker / OffPeakTicket
# ---------------------------------------------------------------------------


class TestSavingsTracker:
    """SavingsTracker — record, ignore invalid, snapshot."""

    def test_record_and_accumulate(self) -> None:
        st = SavingsTracker()
        st.record(5.0)
        st.record(3.0)
        assert st.total_savings == 8.0
        assert st.total_deferred == 2

    def test_ignore_negative_savings(self) -> None:
        st = SavingsTracker()
        st.record(-1.0)
        assert st.total_savings == 0.0
        assert st.total_deferred == 0

    def test_ignore_nan_savings(self) -> None:
        st = SavingsTracker()
        st.record(float("nan"))
        assert st.total_savings == 0.0

    def test_snapshot_structure(self) -> None:
        st = SavingsTracker()
        st.record(10.0)
        snap = st.snapshot()
        assert snap["total_deferred"] == 1
        assert snap["total_savings"] == 10.0


class TestOffPeakTicket:
    """OffPeakTicket — readiness, derived fields."""

    def test_is_ready_when_runnable_after_past(self) -> None:
        ticket = OffPeakTicket(
            task_id="t1",
            task_spec={},
            deadline=time.time() + 3600,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
            savings=5.0,
            scheduled_at=time.time(),
            runnable_after=time.time() - 100,
        )
        assert ticket.is_ready is True

    def test_is_not_ready_when_runnable_future(self) -> None:
        ticket = OffPeakTicket(
            task_id="t2",
            task_spec={},
            deadline=time.time() + 3600,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
            savings=5.0,
            scheduled_at=time.time(),
            runnable_after=time.time() + 3600,
        )
        assert ticket.is_ready is False


# ---------------------------------------------------------------------------
# OffPeakScheduler
# ---------------------------------------------------------------------------


class TestOffPeakScheduler:
    """OffPeakScheduler — deferral, ready-tasks, pruning, status."""

    def test_schedule_uses_injected_clock_consistently(self) -> None:
        now = _local_wall_time(12)
        scheduler = OffPeakScheduler(
            off_peak_start=0,
            off_peak_end=6,
            clock=lambda: now,
        )

        ticket = scheduler.schedule(
            {"action": "sweep"},
            deadline=now + 7200,
            estimated_cost_now=100.0,
            estimated_cost_off_peak=50.0,
        )

        assert ticket is not None
        assert ticket.scheduled_at == now
        assert ticket.runnable_after > now

    def test_schedule_returns_none_during_off_peak(self) -> None:
        scheduler = OffPeakScheduler(off_peak_start=0, off_peak_end=23)
        with patch.object(scheduler, "_is_off_peak", return_value=True):
            ticket = scheduler.schedule(
                {"action": "train"},
                deadline=time.time() + 7200,
                estimated_cost_now=100.0,
            )
        assert ticket is None

    def test_schedule_returns_none_below_savings_ratio(self) -> None:
        scheduler = OffPeakScheduler(
            min_savings_ratio=0.50,
            clock=lambda: _local_wall_time(12),
        )
        ticket = scheduler.schedule(
            {"action": "eval"},
            deadline=time.time() + 7200,
            estimated_cost_now=100.0,
            estimated_cost_off_peak=80.0,
        )
        assert ticket is None

    def test_schedule_defers_when_savings_justify(self) -> None:
        scheduler = OffPeakScheduler(
            off_peak_start=0,
            off_peak_end=6,
            min_savings_ratio=0.20,
            clock=lambda: _local_wall_time(12),
        )
        ticket = scheduler.schedule(
            {"action": "sweep"},
            deadline=time.time() + 7200,
            estimated_cost_now=100.0,
            estimated_cost_off_peak=50.0,
        )
        assert ticket is not None
        assert ticket.savings == 50.0
        assert ticket.task_spec == {"action": "sweep"}

    def test_schedule_uses_cost_tracker_fallback(self) -> None:
        fake_combined = MagicMock(spec=CombinedCostTracker)
        fake_combined.model_spend.return_value = 42.0
        scheduler = OffPeakScheduler(
            cost_tracker=fake_combined,
            off_peak_start=0,
            off_peak_end=6,
            clock=lambda: _local_wall_time(12),
        )
        ticket = scheduler.schedule(
            {"action": "infer"},
            deadline=time.time() + 7200,
        )
        assert ticket is not None
        assert ticket.estimated_cost_now == 42.0

    def test_prune_uses_injected_clock(self) -> None:
        current = [_local_wall_time(12) + 86400 * 3650]
        scheduler = OffPeakScheduler(
            off_peak_start=0,
            off_peak_end=6,
            ticket_ttl=10.0,
            clock=lambda: current[0],
        )
        ticket = scheduler.schedule(
            {"action": "sweep"},
            deadline=current[0] + 10.0,
            estimated_cost_now=100.0,
            estimated_cost_off_peak=50.0,
        )
        assert ticket is not None

        current[0] += 21.0

        assert scheduler._prune_expired() == 1
        assert scheduler.pending_count == 0

    def test_get_ready_tasks_filters_correctly(self) -> None:
        scheduler = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        past = time.time() - 100
        future = time.time() + 3600
        with scheduler._lock:
            scheduler._tickets["ready"] = OffPeakTicket(
                "ready",
                {},
                future,
                10,
                5,
                5,
                past,
                past,
            )
            scheduler._tickets["future"] = OffPeakTicket(
                "future",
                {},
                future,
                10,
                5,
                5,
                past,
                future,
            )
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "ready"

    def test_pending_count(self) -> None:
        scheduler = OffPeakScheduler(off_peak_start=0, off_peak_end=6)
        assert scheduler.pending_count == 0
        with scheduler._lock:
            scheduler._tickets["t1"] = OffPeakTicket(
                "t1",
                {},
                time.time() + 3600,
                10,
                5,
                5,
                time.time(),
                time.time(),
            )
        assert scheduler.pending_count == 1

    def test_get_status(self) -> None:
        scheduler = OffPeakScheduler(off_peak_start=2, off_peak_end=6)
        status = scheduler.get_status()
        assert "pending_count" in status
        assert "savings" in status
        assert "off_peak_start" in status
        assert "off_peak_end" in status
        assert "off_peak_active" in status

    def test_invalid_params_raise(self) -> None:
        with pytest.raises(ValueError):
            OffPeakScheduler(off_peak_start=25)
        with pytest.raises(ValueError):
            OffPeakScheduler(cost_multiplier_peak=0.5)
        with pytest.raises(ValueError):
            OffPeakScheduler(min_savings_ratio=1.5)
        with pytest.raises(ValueError):
            OffPeakScheduler(cost_multiplier_off=-1.0)


# ---------------------------------------------------------------------------
# CombinedCostTracker
# ---------------------------------------------------------------------------


class TestCombinedCostTracker:
    """CombinedCostTracker — probes, record errors, missing-side zero-cost."""

    def test_has_model_and_has_infra_when_none(self) -> None:
        ct = CombinedCostTracker()
        assert ct.has_model is False
        assert ct.has_infra is False

    def test_model_spend_returns_zero_when_no_limiter(self) -> None:
        ct = CombinedCostTracker()
        assert ct.model_spend() == 0.0

    def test_infra_spend_returns_zero_when_no_tracker(self) -> None:
        ct = CombinedCostTracker()
        assert ct.infra_spend() == 0.0

    def test_get_total_spend_zero_when_none_configured(self) -> None:
        ct = CombinedCostTracker()
        assert ct.get_total_spend() == 0.0

    def test_record_model_cost_raises_when_no_limiter(self) -> None:
        ct = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no SpendLimiter"):
            ct.record_model_cost(5.0)

    def test_record_infra_cost_raises_when_no_tracker(self) -> None:
        ct = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no InfraCostTracker"):
            ct.record_infra_cost("aws", "compute", "i-123", 10.0)

    def test_remaining_model_budget_inf_when_no_limiter(self) -> None:
        ct = CombinedCostTracker()
        assert ct.remaining_model_budget() == float("inf")

    def test_would_exceed_combined_false_when_no_limiter(self) -> None:
        ct = CombinedCostTracker()
        assert ct.would_exceed_combined(1000.0) is False

    def test_snapshot_empty(self) -> None:
        ct = CombinedCostTracker()
        snap = ct.snapshot()
        assert snap["model_records"] == []
        assert snap["infra"] == {}


# ---------------------------------------------------------------------------
# BudgetCheckResult
# ---------------------------------------------------------------------------


class TestBudgetCheckResult:
    """BudgetCheckResult — dataclass defaults and field access."""

    def test_default_allowed_false(self) -> None:
        result = BudgetCheckResult(allowed=False, reason="budget exhausted")
        assert result.allowed is False
        assert result.reason == "budget exhausted"
        assert result.details == {}

    def test_with_details(self) -> None:
        result = BudgetCheckResult(allowed=True, reason="ok", details={"layer": "tool", "remaining": 5.0})
        assert result.allowed is True
        assert result.details["layer"] == "tool"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_monday_noon() -> datetime.datetime:
    return datetime.datetime(2026, 8, 3, 12, 0, tzinfo=datetime.UTC)
