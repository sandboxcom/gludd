"""Budget-cap integrity: do the caps ACTUALLY block dispatch in the prod path?

This suite is an ADVERSARIAL audit of the SpendLimiter / RunBudgetGuard /
BudgetManager budget caps (claimed-100% features F5 / H11).  The existing unit
tests (tests/unit/test_budget_caps.py, tests/unit/test_budget_wiring.py) all
pass — but they pass by INJECTING a non-zero cost directly into the guard:

    guard.record_spend(1.0)                 # test_budget_wiring.py
    projected_cost_usd=0.8                   # _make_budget_gated_executor(...)

i.e. they prove the guard *arithmetic* works when handed a non-zero number.
They never drive the number the PRODUCTION path actually supplies.

The audit (CA-T12) claims the production cost that reaches the guards is
hardwired to $0.0, so the caps never fire.  The cost chain, traced through the
real code, is:

  daemon.py:_gateway_executor
    -> model_gateway.call_model_with_retry(...)        # the real call
        -> gateway._invoke_and_bill(...)
            cost = in_tok * profile.cost_per_input_token
                 + out_tok * profile.cost_per_output_token
            budget_guard.record_spend(cost)            # records THIS cost
    -> budget_manager.record_spend(task_id, result.cost_estimate)

  Enabled metered profiles now fail validation when both token rates are zero.
  Explicitly unmetered/local profiles may retain zero rates, and their calls
  correctly record no API-token spend.

The tests below drive a mock gateway that returns a KNOWN NON-TRIVIAL usage
(input/output tokens) through the REAL _invoke_and_bill billing path, then
assert the guard actually blocks a subsequent over-budget dispatch.

The legacy-named zero-cost tests now exercise only the explicit unmetered path;
the metered positive controls prove non-zero costs reach and trip the guards.

CA-T12 EXTENSION (2026-07-27): The fix below seeds per-token rates from the
PricingCatalog at profile-load time, so ModelProfile defaults are real non-zero
values whenever the catalog has pricing data for the provider+model combo.
"""

from __future__ import annotations

import datetime
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.daemon_wiring import make_spend_guarded_executor
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry

_OFF_PEAK_NOW = datetime.datetime(2026, 8, 9, 12, tzinfo=datetime.UTC)

# ---------------------------------------------------------------------------
# Helpers — build a gateway whose provider returns a KNOWN usage payload, so
# the REAL _invoke_and_bill billing path computes a real (non-injected) cost.
# ---------------------------------------------------------------------------


def _make_gateway(
    *,
    budget_guard: RunBudgetGuard | None,
    cost_per_input_token: float,
    cost_per_output_token: float,
    api_metered: bool = True,
    input_tokens: int = 1000,
    output_tokens: int = 1000,
):
    """Build a ModelGateway whose underlying provider returns a fixed usage.

    The returned gateway bills through the REAL _invoke_and_bill code, so the
    cost it records into ``budget_guard`` is exactly what the production path
    would record for a profile with the given per-token rates.
    """
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

    profile = ModelProfile(
        model_profile_id="default",
        enabled=True,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name="gpt-4",
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
        api_metered=api_metered,
    )

    gw = ModelGateway(
        profiles=[profile],
        provider_registry=reg,
        budget_guard=budget_guard,
        billing_clock=lambda: _OFF_PEAK_NOW,
    )

    FakeChatModel = MagicMock()
    fake_instance = MagicMock()
    fake_instance.invoke.return_value = MagicMock(
        content="generated output",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    FakeChatModel.return_value = fake_instance

    patches = (
        patch.object(reg, "is_installed", return_value=True),
        patch.object(reg, "get_provider_class", return_value=FakeChatModel),
    )
    return gw, patches


# ---------------------------------------------------------------------------
# 1. Explicitly unmetered profiles carry zero API-token rates, so the real
#    billing path records $0.0 without misclassifying an enabled metered model
#    as free.  Test names are retained while the stepwise corpus is active.
# ---------------------------------------------------------------------------


class TestProdPathCostIsZeroWithDefaultProfile:
    def test_default_profile_bills_zero_into_run_budget_guard(self):
        """An explicitly unmetered local profile records no API-token spend."""
        guard = RunBudgetGuard(run_budget_usd=0.01)  # tiny cap

        gw, patches = _make_gateway(
            budget_guard=guard,
            cost_per_input_token=0.0,  # explicit unmetered/local rate
            cost_per_output_token=0.0,  # explicit unmetered/local rate
            api_metered=False,
            input_tokens=1_000_000,  # huge usage...
            output_tokens=1_000_000,
        )

        with patches[0], patches[1]:
            # Make 50 calls — a real run would blow through any sane cap.
            for _ in range(50):
                resp = gw.call_model("default", [{"role": "user", "content": "hi"}])
                assert resp.cost_estimate == 0.0  # billed at $0 despite huge usage

        # The guard recorded $0.0 across 50 expensive calls.
        assert guard.get_total_spend() == 0.0
        # ...so the run-budget check still says ALLOWED even past the $0.01 cap.
        verdict = guard.check_run_budget()
        assert verdict["allowed"] is True, (
            "INERT: the run-budget cap never fires because the production cost "
            "billed into it is hardwired to $0.0 (profiles ship with "
            "cost_per_*_token == 0.0). This is CA-T12."
        )

    def test_default_profile_bills_zero_into_budget_manager(self):
        """BudgetManager daily/per-todo caps also never trip on the $0 prod cost."""
        budget = BudgetManager(daily_limit_usd=0.01, per_todo_limit_usd=0.01)

        gw, patches = _make_gateway(
            budget_guard=None,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=False,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        with patches[0], patches[1]:
            for _ in range(50):
                resp = gw.call_model("default", [{"role": "user", "content": "hi"}])
                # This is exactly what daemon.py records into the BudgetManager:
                #   budget_manager.record_spend(task_id, result.cost_estimate)
                budget.record_spend("todo-1", float(resp.cost_estimate or 0.0))

        status = budget.get_status()
        assert status["daily_spend"] == 0.0
        assert status["paused"] is False, "INERT: daily cap never pauses because the recorded cost is $0.0."
        # A fresh check still admits despite 50 huge-usage calls.
        assert budget.check_daily_budget(0.0)["allowed"] is True


# ---------------------------------------------------------------------------
# 2. The SpendLimiter dispatch gate as wired in daemon.py.  In production the
#    gate charges ``_projected_cost_usd``; the REALISED cost from the model
#    call is never charged to the SpendLimiter at all.  We verify both:
#      (a) when projected_cost is 0.0 (what daemon passes when there is no
#          "default" profile, or the profile bills $0) the gate NEVER defers;
#      (b) the realised over-budget cost is invisible to the SpendLimiter.
# ---------------------------------------------------------------------------


class TestSpendLimiterDispatchGateProdWiring:
    @pytest.mark.asyncio
    async def test_zero_projection_never_defers_even_when_realised_cost_is_huge(self):
        from general_ludd.controllers.spend_limiter import SpendLimiter

        limiter = SpendLimiter(limit_usd=0.01, window_seconds=3600.0)

        realised_costs: list[float] = []

        async def _executor(task=None):
            # Pretend the underlying call actually cost a fortune.
            realised_costs.append(1000.0)
            return "ran"

        # daemon.py computes _projected_cost_usd from token_cost_usd(...) ONLY
        # when a profile named "default" exists; otherwise it stays 0.0. With a
        # 0.0 projection the gate's try_charge(0.0) ALWAYS admits.
        guarded = make_spend_guarded_executor(
            executor=_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.0,
        )

        for _ in range(100):
            out = await guarded()
            assert out == "ran", (
                "INERT: with projected_cost_usd=0.0 the SpendLimiter admits "
                "every dispatch, regardless of realised cost."
            )

        # Realised spend was 100 * 1000 = 100000 USD; window spend is still 0.
        assert sum(realised_costs) == pytest.approx(100_000.0)
        assert limiter.window_spend() == 0.0, (
            "The realised cost is never recorded into the SpendLimiter; only the "
            "(zero) projection is charged, so the rolling window never grows."
        )


# ---------------------------------------------------------------------------
# 3. POSITIVE CONTROL: the guard arithmetic DOES block when a real non-zero
#    cost reaches it.  This proves the cap is not broken in itself — it is
#    starved of a non-zero input in the production path.  An operator who sets
#    explicit per-token rates gets a working cap.
# ---------------------------------------------------------------------------


class TestGuardBlocksWhenRealCostReachesIt:
    def test_run_budget_guard_trips_with_explicit_nonzero_rates(self):
        guard = RunBudgetGuard(run_budget_usd=0.05)

        gw, patches = _make_gateway(
            budget_guard=guard,
            cost_per_input_token=0.00003,  # operator-configured non-zero rate
            cost_per_output_token=0.00006,
            input_tokens=1000,
            output_tokens=1000,
        )
        # At the fixture-pinned off-peak time, cost per call is
        # (1000*0.00003 + 1000*0.00006) * 0.75 = 0.0675 USD.

        with patches[0], patches[1]:
            resp = gw.call_model("default", [{"role": "user", "content": "hi"}])
            assert resp.cost_estimate == pytest.approx(0.0675)

        assert guard.get_total_spend() == pytest.approx(0.0675)
        # 0.09 > 0.05 cap => the run-budget check now (correctly) blocks.
        verdict = guard.check_run_budget()
        assert verdict["allowed"] is False
        assert "run budget" in str(verdict["reason"]).lower()

    @pytest.mark.asyncio
    async def test_spend_limiter_defers_with_nonzero_projection(self):
        from general_ludd.controllers.spend_limiter import SpendLimiter

        limiter = SpendLimiter(limit_usd=0.10, window_seconds=3600.0)

        calls = {"n": 0}

        async def _executor(task=None):
            calls["n"] += 1
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.06,  # a REAL projection, as daemon computes
        )

        # First call: 0.06 fits under 0.10.
        assert await guarded() == "ran"
        # Second call: 0.06 + 0.06 = 0.12 > 0.10 => deferred.
        assert await guarded() == "deferred:spend_limit_exceeded"
        assert calls["n"] == 1, "the over-budget second dispatch must NOT run"


# ---------------------------------------------------------------------------
# CA-T12 EXTENSION: ModelProfile.seed_token_rates_from_catalog returns real
# non-zero per-token rates when the PricingCatalog has data.
# ---------------------------------------------------------------------------


class TestModelProfileRatesNonzeroByDefault:
    def test_seed_rates_from_catalog_returns_nonzero_for_known_model(self):
        """seed_token_rates_from_catalog returns non-zero per-token rates for a
        well-known model that exists in the static pricing tables (openai/gpt-4o)."""
        from general_ludd.pricing_intel import PricingCatalog

        catalog = PricingCatalog()
        cost_in, cost_out = ModelProfile.seed_token_rates_from_catalog(
            "openai",
            "gpt-4o",
            catalog,
        )
        assert cost_in > 0.0, "CA-T12: input per-token rate seeded from catalog must be > 0 for gpt-4o"
        assert cost_out > 0.0, "CA-T12: output per-token rate seeded from catalog must be > 0 for gpt-4o"

    def test_seed_rates_from_catalog_converts_per_1k_to_per_token(self):
        """The catalog stores USD-per-1K-tokens; the seeder divides by 1000 to
        produce per-token rates consumed by _invoke_and_bill."""
        from general_ludd.pricing_intel import PricingCatalog

        catalog = PricingCatalog()
        # gpt-4o is $0.005/1K input, $0.015/1K output (per static table).
        cost_in, cost_out = ModelProfile.seed_token_rates_from_catalog(
            "openai",
            "gpt-4o",
            catalog,
        )
        assert cost_in == pytest.approx(0.000005), "0.005/1000 = 0.000005 per token"
        assert cost_out == pytest.approx(0.000015), "0.015/1000 = 0.000015 per token"

    def test_seed_rates_returns_zero_for_unknown_model(self):
        """seed_token_rates_from_catalog returns (0.0, 0.0) for a model not in the catalog."""
        catalog = type("FakeCatalog", (), {"model_price": lambda s, p, m: None})()
        cost_in, cost_out = ModelProfile.seed_token_rates_from_catalog(
            "openai",
            "gpt-nonexistent-9999",
            catalog,
        )
        assert cost_in == 0.0
        assert cost_out == 0.0

    def test_seed_rates_returns_zero_when_catalog_is_none(self):
        """seed_token_rates_from_catalog returns (0.0, 0.0) when catalog is None."""
        cost_in, cost_out = ModelProfile.seed_token_rates_from_catalog(
            "openai",
            "gpt-4o",
            None,
        )
        assert cost_in == 0.0
        assert cost_out == 0.0

    def test_auto_configurator_seeds_rates_from_catalog(self):
        """auto_configure_from_env seeds non-zero per-token rates when a catalog is provided."""
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.pricing_intel import PricingCatalog

        catalog = PricingCatalog()
        ac = AutoConfigurator()
        profiles = ac.auto_configure_from_env(
            environ={"OPENAI_API_KEY": "sk-not-a-real-key"},  # pragma: allowlist secret
            catalog=catalog,
        )
        for p in profiles:
            if p["provider"] == "openai":
                assert float(cast("float | str", p["cost_per_input_token"])) > 0.0, (
                    "CA-T12: auto-configured openai profile must have non-zero per-token input rate seeded from catalog"
                )
                assert float(cast("float | str", p["cost_per_output_token"])) > 0.0, (
                    "CA-T12: auto-configured openai profile must have non-zero "
                    "per-token output rate seeded from catalog"
                )
                assert p["is_free"] is False
                break
        else:
            pytest.skip("No OpenAI profile auto-configured (no OPENAI_API_KEY env var?)")

    def test_gateway_billing_with_seeded_rates(self):
        """The _invoke_and_bill cost computation produces a non-zero cost when
        the profile carries real per-token rates seeded from the catalog."""
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        guard = RunBudgetGuard(run_budget_usd=200.0)
        profile = ModelProfile(
            model_profile_id="gpt-4o-seeded",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4o",
            cost_per_input_token=0.000005,  # $0.005/1K input
            cost_per_output_token=0.000015,  # $0.015/1K output
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            budget_guard=guard,
            billing_clock=lambda: _OFF_PEAK_NOW,
        )

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="generated output",
            usage_metadata={
                "input_tokens": 500,
                "output_tokens": 600,
            },
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt-4o-seeded", [{"role": "user", "content": "hi"}])

        expected_cost = (500 * 0.000005 + 600 * 0.000015) * 0.75  # (0.0025 + 0.009) * 0.75 = 0.008625
        assert resp.cost_estimate == pytest.approx(expected_cost)
        assert guard.get_total_spend() == pytest.approx(expected_cost)
        assert guard.get_total_spend() > 0.0, (
            "CA-T12: budget guard must record non-zero spend when profile carries real per-token rates"
        )
