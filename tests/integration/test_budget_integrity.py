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

  ModelProfile.cost_per_input_token  defaults to 0.0
  ModelProfile.cost_per_output_token defaults to 0.0
  => cost == 0.0 for every call under the shipped/default profile config
  => record_spend(0.0) => the rolling window / daily ledger never grows
  => the cap never trips no matter how many calls are made.

The tests below drive a mock gateway that returns a KNOWN NON-TRIVIAL usage
(input/output tokens) through the REAL _invoke_and_bill billing path, then
assert the guard actually blocks a subsequent over-budget dispatch.

Per the audit, the "shipped-default profile" test is expected to demonstrate
the INERT behaviour (cost 0.0, cap never fires) while the "explicit non-zero
rates" test demonstrates the cap CAN fire only when the operator manually sets
per-token rates that the default config does not ship with.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.daemon_wiring import make_spend_guarded_executor
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers — build a gateway whose provider returns a KNOWN usage payload, so
# the REAL _invoke_and_bill billing path computes a real (non-injected) cost.
# ---------------------------------------------------------------------------


def _make_gateway(
    *,
    budget_guard: RunBudgetGuard | None,
    cost_per_input_token: float,
    cost_per_output_token: float,
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
    )

    gw = ModelGateway(
        profiles=[profile],
        provider_registry=reg,
        budget_guard=budget_guard,
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
# 1. The shipped-default config: profiles carry ZERO per-token rates, so the
#    real billing path records $0.0 and the cap NEVER fires.  This is the
#    CA-T12 inertness, demonstrated through the real gateway billing path.
# ---------------------------------------------------------------------------


class TestProdPathCostIsZeroWithDefaultProfile:
    def test_default_profile_bills_zero_into_run_budget_guard(self):
        """With shipped defaults (cost_per_*_token == 0.0) the guard records 0.0."""
        guard = RunBudgetGuard(run_budget_usd=0.01)  # tiny cap

        gw, patches = _make_gateway(
            budget_guard=guard,
            cost_per_input_token=0.0,   # the SHIPPED default
            cost_per_output_token=0.0,  # the SHIPPED default
            input_tokens=1_000_000,     # huge usage...
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
        assert status["paused"] is False, (
            "INERT: daily cap never pauses because the recorded cost is $0.0."
        )
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
            cost_per_input_token=0.00003,   # operator-configured non-zero rate
            cost_per_output_token=0.00006,
            input_tokens=1000,
            output_tokens=1000,
        )
        # cost per call = 1000*0.00003 + 1000*0.00006 = 0.09 USD

        with patches[0], patches[1]:
            resp = gw.call_model("default", [{"role": "user", "content": "hi"}])
            assert resp.cost_estimate == pytest.approx(0.09)

        assert guard.get_total_spend() == pytest.approx(0.09)
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
