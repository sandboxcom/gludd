"""Integration tests for peak/off-peak pricing endpoints and gateway wiring."""

from __future__ import annotations

import datetime as _dt

import pytest


@pytest.fixture(autouse=True)
def _reset_tracker() -> None:
    from general_ludd.budget.peak_pricing import PeakPricingTracker

    tracker = PeakPricingTracker.singleton()
    with tracker._lock:
        tracker._cumulative_full_cost = 0.0
        tracker._cumulative_discounted_cost = 0.0


class TestAdminBudgetRatesEndpoint:
    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_rates_endpoint_registered(self, test_client):
        resp = test_client.get("/admin/budget/rates")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_period" in data
        assert "rate_multiplier" in data
        assert "peak_start_utc" in data
        assert "peak_end_utc" in data
        assert "models" in data

    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_rates_has_correct_period(self, test_client):
        now = _dt.datetime.now(tz=_dt.UTC)
        is_weekday = now.weekday() < 5
        in_peak_window = 9 <= now.hour < 17
        expected_period = "peak" if (is_weekday and in_peak_window) else "off-peak"

        resp = test_client.get("/admin/budget/rates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_period"] == expected_period

    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_rates_multiplier_range(self, test_client):
        resp = test_client.get("/admin/budget/rates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rate_multiplier"] in (0.75, 1.0)

    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_rates_has_peak_hour_constants(self, test_client):
        resp = test_client.get("/admin/budget/rates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["peak_start_utc"] == 9
        assert data["peak_end_utc"] == 17


class TestAdminBudgetSavingsEndpoint:
    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_savings_endpoint_registered(self, test_client):
        resp = test_client.get("/admin/budget/savings")
        assert resp.status_code == 200
        data = resp.json()
        assert "cumulative_full_cost" in data
        assert "cumulative_discounted_cost" in data
        assert "cumulative_savings" in data
        assert "savings_percentage" in data

    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_savings_starts_at_zero(self, test_client):
        resp = test_client.get("/admin/budget/savings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cumulative_savings"] == 0.0
        assert data["cumulative_full_cost"] == 0.0
        assert data["savings_percentage"] == 0.0

    @pytest.mark.skip(reason="needs test_client fixture")
    def test_budget_savings_reflects_accumulated(self, test_client):
        from general_ludd.budget.peak_pricing import PeakPricingTracker

        tracker = PeakPricingTracker.singleton()
        tracker.record_call(10.0, 7.5)

        resp = test_client.get("/admin/budget/savings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cumulative_savings"] == 2.5
        assert data["cumulative_full_cost"] == 10.0
        assert data["cumulative_discounted_cost"] == 7.5
        assert data["savings_percentage"] == 25.0


class TestPeakPricingGatewayIntegration:
    def test_peak_tracker_accumulates_off_peak_savings(self):
        from general_ludd.budget.peak_pricing import PeakPricingTracker

        tracker = PeakPricingTracker.singleton()
        tracker.record_call(100.0, 75.0)
        tracker.record_call(200.0, 150.0)
        assert tracker.cumulative_savings == 75.0
        assert tracker.cumulative_full_cost == 300.0

    def test_rate_multiplier_during_peak_is_one(self):
        from general_ludd.budget.peak_pricing import current_rate_multiplier

        now = _dt.datetime(2026, 7, 14, 12, 0, 0, tzinfo=_dt.UTC)
        assert current_rate_multiplier(now) == 1.0

    def test_rate_multiplier_during_off_peak_is_discount(self):
        from general_ludd.budget.peak_pricing import current_rate_multiplier

        now = _dt.datetime(2026, 7, 14, 3, 0, 0, tzinfo=_dt.UTC)
        assert current_rate_multiplier(now) == 0.75
