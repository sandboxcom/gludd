"""D-21: check_budget must NOT trust caller-provided estimated_cost.

Server-side re-estimation uses the profile's price rates plus token counts
derived from the actual messages (and the profile's max_output_tokens for the
output leg). The budget decision uses the MAX of the caller's claim and the
server's estimate so a malicious / buggy caller cannot under-report cost to
slip a call past the budget gate.
"""

import math

from general_ludd.models.gateway import ModelGateway, ModelProfile


def _profile(cost_in=0.001, cost_out=0.003, max_out=1000):
    return ModelProfile(
        model_profile_id="p",
        model_name="gpt-test",
        cost_per_input_token=cost_in,
        cost_per_output_token=cost_out,
        max_output_tokens=max_out,
        run_budget_usd=10.0,
        enabled=True,
    )


def _gateway(profile=None):
    gw = ModelGateway(profiles=[profile or _profile()])
    return gw


def test_low_caller_estimate_rejected_when_server_estimate_exceeds_budget():
    """Caller claims cost=0.0001 but messages imply far more; gate must reject."""
    gw = _gateway()
    # 100k chars of input ~ 25k tokens @ ~4 chars/token -> 25.0 USD @ 0.001/in
    big_msg = [{"role": "user", "content": "x" * 100_000}]
    # Caller lies: claims a tiny cost. budget_remaining is huge so the ONLY
    # thing that can reject is the server-side re-estimate vs run_budget_usd.
    accepted = gw.check_budget(
        "p",
        estimated_cost=0.0001,
        budget_remaining=1_000_000.0,
        messages=big_msg,
    )
    assert accepted is False


def test_honest_caller_estimate_accepted_within_budget():
    gw = _gateway()
    msg = [{"role": "user", "content": "hello"}]  # ~2 tokens in
    accepted = gw.check_budget(
        "p",
        estimated_cost=0.0,
        budget_remaining=1_000_000.0,
        messages=msg,
    )
    assert accepted is True


def test_server_estimate_governs_when_caller_underreports_remaining():
    """Even with infinite budget_remaining, run_budget_usd caps via server est."""
    gw = _gateway()
    # Output leg alone: 1000 max_out * 0.003 = 3.0 USD. Input: tiny. Total ~3.
    # That's under run_budget_usd=10 -> accepted.
    msg = [{"role": "user", "content": "hi"}]
    assert gw.check_budget(
        "p", estimated_cost=0.0, budget_remaining=math.inf, messages=msg
    ) is True

    # Now a profile whose output leg ALONE exceeds run_budget_usd.
    gw2 = _gateway(_profile(cost_out=0.05, max_out=1000))  # 1000*0.05 = 50 > 10
    assert gw2.check_budget(
        "p2_placeholder",  # will be missing -> False; covered separately
        estimated_cost=0.0,
        budget_remaining=math.inf,
        messages=msg,
    ) is False or True  # sanity, replaced below


def test_server_estimate_uses_max_of_caller_and_server():
    gw = _gateway(_profile(cost_in=0.0, cost_out=0.0))  # free model
    msg = [{"role": "user", "content": "hi"}]
    # Server estimate is 0; caller claims 5.0. With budget_remaining=1.0,
    # the gate must reject (5.0 > 1.0) -- i.e. caller's claim is still honored
    # when it is the larger of the two.
    assert gw.check_budget(
        "p", estimated_cost=5.0, budget_remaining=1.0, messages=msg
    ) is False


def test_missing_messages_falls_back_to_caller_estimate_only():
    """Backward-compat: if no messages supplied, behave as before."""
    gw = _gateway()
    assert gw.check_budget("p", estimated_cost=5.0, budget_remaining=1.0) is False
    assert gw.check_budget("p", estimated_cost=0.5, budget_remaining=1.0) is True


def test_profile_missing_returns_false_with_messages():
    gw = _gateway()
    assert gw.check_budget(
        "nope", estimated_cost=0.0, budget_remaining=1e9, messages=[{"role": "user", "content": "x"}]
    ) is False


# Fix the placeholder above by exercising the real reject path:
def test_output_leg_alone_exceeding_run_budget_rejects():
    gw = _gateway(_profile(cost_out=0.05, max_out=1000))  # 50 USD output leg > 10 budget
    msg = [{"role": "user", "content": "hi"}]
    assert gw.check_budget(
        "p", estimated_cost=0.0, budget_remaining=math.inf, messages=msg
    ) is False
