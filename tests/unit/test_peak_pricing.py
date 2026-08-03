"""Tests for budget/peak_pricing and peak_pricing."""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from general_ludd.budget.peak_pricing import (
    PeakPricingSchedule,
    PeakPricingTracker,
    RateTier,
    apply_builtin_schedules,
    current_rate_multiplier,
    default_schedule,
    get_current_rate,
    is_off_peak,
    is_peak,
    list_rate_tiers,
    next_off_peak_window,
    peak_rate_for_model,
)
from general_ludd.peak_pricing import (
    ComputeInstance,
    ProviderPricing,
    ProviderRate,
    build_provider_billing_table,
    load_compute_instances,
    load_provider_rates,
    provider_rate_dict,
)


class TestRateTier:
    def test_creation(self):
        tier = RateTier(
            model_id="gpt-4o",
            provider="openai",
            rate=15.00,
            label="peak",
            days=frozenset(range(7)),
            start_hour=8,
            end_hour=18,
        )
        assert tier.model_id == "gpt-4o"
        assert tier.provider == "openai"
        assert tier.rate == 15.00
        assert tier.label == "peak"

    def test_eq_same(self):
        a = RateTier("m1", "openai", 10.0, "peak", frozenset([0, 1]), 8, 18)
        b = RateTier("m1", "openai", 10.0, "peak", frozenset([0, 1]), 8, 18)
        assert a == b

    def test_eq_different(self):
        a = RateTier("m1", "openai", 10.0, "peak", frozenset([0, 1]), 8, 18)
        b = RateTier("m2", "openai", 10.0, "peak", frozenset([0, 1]), 8, 18)
        assert a != b

    def test_hash_stable(self):
        tier = RateTier("m1", "openai", 10.0, "peak", frozenset([0, 1]), 8, 18)
        assert hash(tier) == hash(tier)

    def test_covers_in_window(self):
        tier = RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 18)
        dt = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        assert tier.covers(dt) is True

    def test_covers_outside_window(self):
        tier = RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 18)
        dt = datetime.datetime(2026, 8, 3, 5, 0, 0, tzinfo=datetime.UTC)
        assert tier.covers(dt) is False

    def test_covers_weekend_not_in_days(self):
        tier = RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 18)
        dt = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.UTC)
        assert tier.covers(dt) is False

    def test_covers_overnight(self):
        tier = RateTier("m1", "openai", 5.0, "off-peak", frozenset(range(7)), 19, 7)
        dt = datetime.datetime(2026, 8, 3, 3, 0, 0, tzinfo=datetime.UTC)
        assert tier.covers(dt) is True

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            RateTier("m1", "openai", -5.0, "peak", frozenset(range(5)), 8, 18)

    def test_bad_start_hour_rejected(self):
        with pytest.raises(ValueError):
            RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 25, 18)

    def test_bad_end_hour_rejected(self):
        with pytest.raises(ValueError):
            RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 25)


class TestPeakPricingSchedule:
    def test_add_and_lookup(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        tiers = sched.tiers_for("gpt-4o", "openai")
        assert len(tiers) == 2

    def test_tiers_for_nonexistent(self):
        sched = PeakPricingSchedule()
        assert sched.tiers_for("nonexistent", "openai") == []

    def test_remove_tier(self):
        sched = PeakPricingSchedule()
        tier = RateTier(
            "gpt-4o",
            "openai",
            15.00,
            "peak",
            frozenset(range(5)),
            8,
            18,
        )
        sched.add_tier(tier)
        sched.remove_tier(tier)
        assert sched.tiers_for("gpt-4o", "openai") == []

    def test_duplicate_add(self):
        sched = PeakPricingSchedule()
        tier = RateTier("gpt-4o", "openai", 15.00, "peak", frozenset(range(5)), 8, 18)
        sched.add_tier(tier)
        sched.add_tier(tier)
        assert len(sched.tiers_for("gpt-4o", "openai")) == 1

    def test_all_providers(self):
        sched = PeakPricingSchedule()
        sched.add_tier(RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 18))
        sched.add_tier(RateTier("m2", "anthropic", 20.0, "peak", frozenset(range(5)), 8, 18))
        assert set(sched.all_providers()) == {"openai", "anthropic"}

    def test_all_model_ids(self):
        sched = PeakPricingSchedule()
        sched.add_tier(RateTier("m1", "openai", 10.0, "peak", frozenset(range(5)), 8, 18))
        sched.add_tier(RateTier("m2", "openai", 20.0, "peak", frozenset(range(5)), 8, 18))
        assert set(sched.all_model_ids()) == {"m1", "m2"}

    def test_matching_tier(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        dt = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        tier = sched.matching_tier("gpt-4o", "openai", dt)
        assert tier is not None
        assert tier.label == "peak"

    def test_matching_tier_none(self):
        sched = PeakPricingSchedule()
        dt = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        assert sched.matching_tier("gpt-4o", "openai", dt) is None


class TestGetCurrentRate:
    def test_peak_rate_weekday(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                7,
                19,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                19,
                7,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                3,
                12,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            assert get_current_rate(sched, "gpt-4o", "openai") == 15.00

    def test_off_peak_weekend(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                7,
                19,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                19,
                7,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                2,
                12,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            assert get_current_rate(sched, "gpt-4o", "openai") == 7.50

    def test_no_tiers_returns_zero(self):
        sched = PeakPricingSchedule()
        assert get_current_rate(sched, "gpt-4o", "openai") == 0.0


class TestListRateTiers:
    def test_returns_tiers(self):
        sched = PeakPricingSchedule()
        tier = RateTier("gpt-4o", "openai", 15.00, "peak", frozenset(range(5)), 8, 20)
        sched.add_tier(tier)
        result = list_rate_tiers(sched, "gpt-4o", "openai")
        assert len(result) == 1

    def test_empty_for_nonexistent(self):
        sched = PeakPricingSchedule()
        assert list_rate_tiers(sched, "nonexistent", "openai") == []


class TestIsOffPeak:
    def test_is_off_peak_false_during_peak(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                3,
                12,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            assert is_off_peak(sched, "gpt-4o", "openai") is False

    def test_is_off_peak_true_weekend(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                2,
                12,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            assert is_off_peak(sched, "gpt-4o", "openai") is True


class TestNextOffPeakWindow:
    def test_from_peak_returns_future(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                3,
                12,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            result = next_off_peak_window(sched, "gpt-4o", "openai")
            assert result is not None
            assert result > mock_now.return_value

    def test_already_off_peak_returns_current(self):
        sched = PeakPricingSchedule()
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                15.00,
                "peak",
                frozenset(range(5)),
                8,
                18,
            )
        )
        sched.add_tier(
            RateTier(
                "gpt-4o",
                "openai",
                7.50,
                "off-peak",
                frozenset(range(7)),
                18,
                8,
            )
        )
        with patch("general_ludd.budget.peak_pricing._utcnow") as mock_now:
            mock_now.return_value = datetime.datetime(
                2026,
                8,
                3,
                4,
                0,
                0,
                tzinfo=datetime.UTC,
            )
            result = next_off_peak_window(sched, "gpt-4o", "openai")
            assert result is not None
            assert result <= mock_now.return_value

    def test_no_tiers_returns_none(self):
        sched = PeakPricingSchedule()
        assert next_off_peak_window(sched, "gpt-4o", "openai") is None


class TestBuiltinSchedules:
    def test_openai_has_tiers(self):
        sched = PeakPricingSchedule()
        apply_builtin_schedules(sched)
        assert sched.tiers_for("gpt-4o", "openai")

    def test_anthropic_has_tiers(self):
        sched = PeakPricingSchedule()
        apply_builtin_schedules(sched)
        assert sched.tiers_for("claude-sonnet-4-20250514", "anthropic")

    def test_peak_rate_higher_than_off_peak(self):
        sched = PeakPricingSchedule()
        apply_builtin_schedules(sched)
        tiers = sched.tiers_for("gpt-4o", "openai")
        rates = {}
        for t in tiers:
            rates.setdefault(t.label, []).append(t.rate)
        if "peak" in rates and "off-peak" in rates:
            assert all(p > o for p in rates["peak"] for o in rates["off-peak"])

    def test_default_schedule_has_openai_and_anthropic(self):
        sched = default_schedule()
        providers = sched.all_providers()
        assert "openai" in providers
        assert "anthropic" in providers


class TestBackwardCompatIsPeak:
    def test_weekday_noon_is_peak(self):
        now = datetime.datetime(2026, 7, 14, 12, 0, 0, tzinfo=datetime.UTC)
        assert is_peak(now) is True

    def test_weekday_early_morning_is_off_peak(self):
        now = datetime.datetime(2026, 7, 14, 5, 0, 0, tzinfo=datetime.UTC)
        assert is_peak(now) is False

    def test_saturday_is_off_peak(self):
        now = datetime.datetime(2026, 7, 11, 12, 0, 0, tzinfo=datetime.UTC)
        assert is_peak(now) is False

    def test_none_defaults_to_now(self):
        assert isinstance(is_peak(), bool)


class TestBackwardCompatMultiplier:
    def test_peak_multiplier_is_1(self):
        now = datetime.datetime(2026, 7, 14, 12, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(now) == 1.0

    def test_off_peak_multiplier_is_discount(self):
        now = datetime.datetime(2026, 7, 14, 5, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(now) == 0.75


class TestBackwardCompatPeakRate:
    def test_peak_rate_for_model(self):
        now = datetime.datetime(2026, 7, 14, 12, 0, 0, tzinfo=datetime.UTC)
        eff_in, eff_out = peak_rate_for_model("m1", 0.01, 0.03, now=now)
        assert eff_in == 0.01
        assert eff_out == 0.03

    def test_off_peak_rate_for_model(self):
        now = datetime.datetime(2026, 7, 14, 5, 0, 0, tzinfo=datetime.UTC)
        eff_in, eff_out = peak_rate_for_model("m1", 0.01, 0.03, now=now)
        assert eff_in == 0.0075
        assert eff_out == 0.0225


class TestBackwardCompatTracker:
    def test_tracker_initial(self):
        t = PeakPricingTracker()
        assert t.cumulative_savings == 0.0
        assert t.cumulative_full_cost == 0.0

    def test_tracker_record_savings(self):
        t = PeakPricingTracker()
        t.record_call(10.0, 7.5)
        assert t.cumulative_savings == 2.5
        assert t.cumulative_full_cost == 10.0
        assert t.cumulative_discounted_cost == 7.5

    def test_tracker_singleton(self):
        a = PeakPricingTracker.singleton()
        b = PeakPricingTracker.singleton()
        assert a is b

    def test_tracker_no_savings_if_no_discount(self):
        t = PeakPricingTracker()
        t.record_call(10.0, 15.0)
        assert t.cumulative_savings == 0.0
        assert t.cumulative_full_cost == 0.0


# ---------------------------------------------------------------------------
# Tests for general_ludd.peak_pricing (standalone provider rate module)
# ---------------------------------------------------------------------------


class TestProviderRate:
    def test_basic_creation(self):
        rate = ProviderRate(
            provider="openai",
            model_id="gpt-4o",
            input_usd_per_1k=5.00,
            output_usd_per_1k_peak=15.00,
            output_usd_per_1k_offpeak=7.50,
        )
        assert rate.provider == "openai"
        assert rate.model_id == "gpt-4o"
        assert rate.input_usd_per_1k == 5.00
        assert rate.output_usd_per_1k_peak == 15.00
        assert rate.output_usd_per_1k_offpeak == 7.50
        assert rate.context_window is None
        assert rate.flat is False

    def test_with_context_window(self):
        rate = ProviderRate("openai", "gpt-4o", 5.0, 15.0, 7.5, context_window=128000)
        assert rate.context_window == 128000

    def test_flat_pricing(self):
        rate = ProviderRate("openai", "gpt-4o", 5.0, 15.0, 7.5, flat=True)
        assert rate.flat is True


class TestProviderPricing:
    def test_basic_creation(self):
        pp = ProviderPricing(
            provider="openai",
            display_name="OpenAI",
            billing="token",
            source="https://openai.com/pricing",
            flat=False,
        )
        assert pp.provider == "openai"
        assert pp.display_name == "OpenAI"
        assert pp.billing == "token"
        assert pp.source == "https://openai.com/pricing"
        assert pp.flat is False
        assert pp.rates == []
        assert pp.off_peak_windows == []

    def test_with_rates(self):
        pp = ProviderPricing(
            provider="openai",
            display_name="OpenAI",
            billing="token",
            source="",
            flat=False,
            rates=[
                ProviderRate("openai", "gpt-4o", 5.0, 15.0, 7.5),
                ProviderRate("openai", "gpt-4o-mini", 0.15, 0.60, 0.30),
            ],
        )
        assert len(pp.rates) == 2

    def test_with_off_peak_windows(self):
        pp = ProviderPricing(
            provider="openai",
            display_name="OpenAI",
            billing="token",
            source="",
            flat=False,
            off_peak_windows=[{"days": "mon-fri", "start": "18:00", "end": "08:00"}],
        )
        assert len(pp.off_peak_windows) == 1
        assert pp.off_peak_windows[0]["days"] == "mon-fri"


class TestComputeInstance:
    def test_basic_creation(self):
        ci = ComputeInstance(
            provider="aws",
            key="p4d.24xlarge",
            gpu="A100",
            gpu_count=8,
            on_demand_usd_hr=32.77,
        )
        assert ci.provider == "aws"
        assert ci.key == "p4d.24xlarge"
        assert ci.gpu == "A100"
        assert ci.gpu_count == 8
        assert ci.on_demand_usd_hr == 32.77
        assert ci.vcpus is None
        assert ci.memory_gb is None
        assert ci.spot_discount is None

    def test_with_optionals(self):
        ci = ComputeInstance("aws", "p4d.24xlarge", "A100", 8, 32.77, vcpus=96, memory_gb=1152, spot_discount=0.7)
        assert ci.vcpus == 96
        assert ci.memory_gb == 1152
        assert ci.spot_discount == 0.7


class TestLoadProviderRates:
    def test_missing_file_returns_empty_list(self):
        with patch("general_ludd.peak_pricing._config_dir") as mock_dir:
            mock_dir.return_value = Path("/nonexistent/path")
            result = load_provider_rates()
            assert result == []

    def test_loads_providers_yml(self):
        temp_dir = Path(tempfile.mkdtemp())
        providers_data = {
            "pricing": {
                "openai": {
                    "display_name": "OpenAI",
                    "billing": "token",
                    "source": "https://openai.com",
                    "flat": False,
                    "off_peak_windows": [
                        {"days": "mon-fri", "start": "18:00", "end": "08:00"},
                    ],
                    "rates": [
                        {
                            "model_id": "gpt-4o",
                            "input_usd_per_1k": 5.00,
                            "output_usd_per_1k_peak": 15.00,
                            "output_usd_per_1k_offpeak": 7.50,
                        },
                    ],
                },
                "anthropic": {
                    "display_name": "Anthropic",
                    "billing": "token",
                    "source": "https://anthropic.com",
                    "flat": True,
                    "rates": [
                        {
                            "model_id": "claude-sonnet",
                            "input_usd_per_1k": 3.00,
                            "output_usd_per_1k_peak": 15.00,
                            "output_usd_per_1k_offpeak": 7.50,
                            "context_window": 200000,
                        },
                    ],
                },
            }
        }
        (temp_dir / "providers.yml").write_text(yaml.dump(providers_data))
        with patch("general_ludd.peak_pricing._config_dir", return_value=temp_dir):
            results = load_provider_rates()
        assert len(results) == 2
        openai = next(pp for pp in results if pp.provider == "openai")
        assert openai.display_name == "OpenAI"
        assert openai.flat is False
        assert len(openai.rates) == 1
        assert len(openai.off_peak_windows) == 1
        anthropic = next(pp for pp in results if pp.provider == "anthropic")
        assert anthropic.flat is True
        assert anthropic.rates[0].context_window == 200000

    def test_loads_empty_pricing_section(self):
        temp_dir = Path(tempfile.mkdtemp())
        (temp_dir / "providers.yml").write_text("pricing: {}\n")
        with patch("general_ludd.peak_pricing._config_dir", return_value=temp_dir):
            results = load_provider_rates()
        assert results == []


class TestLoadComputeInstances:
    def test_missing_file_returns_empty_list(self):
        with patch("general_ludd.peak_pricing._config_dir") as mock_dir:
            mock_dir.return_value = Path("/nonexistent/path")
            result = load_compute_instances()
            assert result == []

    def test_loads_compute_yml(self):
        temp_dir = Path(tempfile.mkdtemp())
        compute_data = {
            "instances": {
                "aws": {
                    "entries": [
                        {
                            "key": "p4d.24xlarge",
                            "gpu": "A100",
                            "gpu_count": 8,
                            "on_demand_usd_hr": 32.77,
                            "vcpus": 96,
                            "memory_gb": 1152,
                            "spot_discount": 0.7,
                        },
                    ],
                },
                "gcp": {
                    "entries": [
                        {
                            "key": "a2-highgpu-8g",
                            "gpu": "A100",
                            "gpu_count": 8,
                            "on_demand_usd_hr": 29.45,
                        },
                    ],
                },
            }
        }
        (temp_dir / "compute.yml").write_text(yaml.dump(compute_data))
        with patch("general_ludd.peak_pricing._config_dir", return_value=temp_dir):
            results = load_compute_instances()
        assert len(results) == 2
        aws = [ci for ci in results if ci.provider == "aws"]
        assert len(aws) == 1
        assert aws[0].vcpus == 96
        assert aws[0].spot_discount == 0.7
        gcp = [ci for ci in results if ci.provider == "gcp"]
        assert len(gcp) == 1
        assert gcp[0].vcpus is None
        assert gcp[0].spot_discount is None


class TestProviderRateDict:
    def test_builds_lookup(self):
        pp = ProviderPricing(
            provider="openai",
            display_name="OpenAI",
            billing="token",
            source="",
            flat=False,
            rates=[
                ProviderRate("openai", "gpt-4o", 5.0, 15.0, 7.5),
                ProviderRate("openai", "gpt-4o-mini", 0.15, 0.60, 0.30),
            ],
        )
        lookup = provider_rate_dict([pp])
        assert len(lookup) == 2
        assert ("openai", "gpt-4o") in lookup
        assert lookup[("openai", "gpt-4o")].input_usd_per_1k == 5.0

    def test_handles_multiple_providers(self):
        pp1 = ProviderPricing(
            provider="openai",
            display_name="",
            billing="",
            source="",
            flat=False,
            rates=[ProviderRate("openai", "gpt-4o", 5.0, 15.0, 7.5)],
        )
        pp2 = ProviderPricing(
            provider="anthropic",
            display_name="",
            billing="",
            source="",
            flat=False,
            rates=[ProviderRate("anthropic", "claude-sonnet", 3.0, 15.0, 7.5)],
        )
        lookup = provider_rate_dict([pp1, pp2])
        assert len(lookup) == 2
        assert ("anthropic", "claude-sonnet") in lookup

    def test_empty_list(self):
        assert provider_rate_dict([]) == {}


class TestBuildProviderBillingTable:
    def test_builds_table(self):
        pp = ProviderPricing(
            provider="openai",
            display_name="OpenAI",
            billing="token",
            source="https://openai.com",
            flat=False,
        )
        table = build_provider_billing_table([pp])
        assert "openai" in table
        assert table["openai"]["display_name"] == "OpenAI"
        assert table["openai"]["billing"] == "token"
        assert table["openai"]["source"] == "https://openai.com"
        assert table["openai"]["flat"] is False

    def test_multiple_providers(self):
        pp1 = ProviderPricing("openai", "OpenAI", "token", "https://oai.com", False)
        pp2 = ProviderPricing("anthropic", "Anthropic", "token", "https://anthro.com", True)
        table = build_provider_billing_table([pp1, pp2])
        assert len(table) == 2
        assert table["anthropic"]["flat"] is True

    def test_empty_list(self):
        assert build_provider_billing_table([]) == {}
