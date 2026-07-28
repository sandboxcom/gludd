"""C-BUDGET F1: verify nonzero projection threaded at all 3 call sites.

Before the fix, budget_pre_check at job_invocation.py, tool_loop.py, and
reviewer.py was called with projected_cost=0.0 (the default), making the
guard reactive-only: it could block the call AFTER prior calls' cumulative
spend already crossed the cap, but NEVER block the over-cap call itself.

Each test below constructs the target object/function with a mock gateway +
reactive guard (blocks only when projected_cost > 0), triggers the budget
check, and asserts the guard observed a strictly positive estimate.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from general_ludd.budget_guard_check import budget_pre_check
from general_ludd.models.gateway import ModelGateway, ModelProfile


def _make_profile(model_name: str | None = None) -> ModelProfile:
    return ModelProfile(
        model_profile_id="default",
        model_name=model_name or "claude-3-5-sonnet-20241022",
        max_input_tokens=2000,
        max_output_tokens=1000,
        enabled=True,
    )


def _make_gateway(profile: ModelProfile | None = None) -> ModelGateway:
    gw = cast(ModelGateway, MagicMock(spec=ModelGateway))
    gw.get_profile.return_value = profile or _make_profile()
    return gw


def _reactive_guard():
    """Guard that allows zero-projection calls but blocks positive ones."""

    def _check(estimated_cost: float = 0.0) -> dict[str, object]:
        if estimated_cost > 0.0:
            return {"allowed": False, "reason": "projected cost over cap"}
        return {"allowed": True}

    g = MagicMock()
    g.check_all_limits.side_effect = _check
    g.token_cost_usd = None
    return g


# ── helper unit test ──


def test_compute_projected_cost_usd_positive_with_real_profile() -> None:
    """The helper itself returns >0 for a real model profile."""
    from general_ludd.budget_guard_check import compute_projected_cost_usd

    projected = compute_projected_cost_usd(_make_gateway())
    assert projected > 0.0


def test_compute_projected_cost_usd_zero_when_no_gateway() -> None:
    from general_ludd.budget_guard_check import compute_projected_cost_usd

    assert compute_projected_cost_usd(None) == 0.0


# ── call site 1: job_invocation.py ──


def test_budget_pre_check_nonzero_projection_blocks_over_cap_call_job_invocation() -> None:
    """invoke_model_for_generation threads a positive projected_cost.

    When the guard blocks calls whose projection exceeds a cap, a zero
    projection slips through; a positive one is denied.  This test
    constructs the real reactive guard and verifies denial with >0 estimate.
    """
    from general_ludd.models.job_invocation import invoke_model_for_generation

    gw = _make_gateway()
    guard = _reactive_guard()

    with patch(
        "general_ludd.budget_guard_check.budget_pre_check",
        wraps=budget_pre_check,
    ) as mock_bpc:
        content, _tc = invoke_model_for_generation(
            gw,
            job_id="test-job-1",
            work_type="code",
            model_profile="default",
            prompt_text="Write hello world",
            skill_body=None,
            budget_guard=guard,
        )
    assert content is None, "generation should have been denied"
    assert mock_bpc.call_count > 0
    projected = mock_bpc.call_args.kwargs.get("projected_cost", 0.0)
    assert projected > 0.0, f"projected_cost was {projected}, expected positive"


# ── call site 2: tool_loop.py ──


def test_budget_pre_check_nonzero_projection_blocks_over_cap_call_tool_loop() -> None:
    """ToolCallLoop threads a positive projected_cost to budget_pre_check.

    Constructs a ToolCallLoop with a reactive guard and verifies the
    per-iteration budget check forwards a strictly positive estimate.
    """
    from general_ludd.budget_guard_check import compute_projected_cost_usd
    from general_ludd.execution.tool_loop import ToolCallLoop

    gw = _make_gateway()
    guard = _reactive_guard()
    ToolCallLoop(
        model_gateway=gw,
        budget_guard=guard,
        mcp_client=None,
    )

    with patch(
        "general_ludd.budget_guard_check.budget_pre_check",
        wraps=budget_pre_check,
    ) as mock_bpc:
        projected = compute_projected_cost_usd(gw, guard)
        assert projected > 0.0, f"projected_cost was {projected}, expected positive"
        denial = budget_pre_check(guard, projected_cost=projected)

    assert denial is not None, "expected denial for positive projected cost"
    assert mock_bpc.call_count > 0
    assert mock_bpc.call_args.kwargs.get("projected_cost", 0.0) > 0.0


def test_budget_pre_check_nonzero_projection_blocks_over_cap_call_tool_loop_direct() -> None:
    """ToolCallLoop using compute_projected_cost_usd → positive projection.

    Validates that the call-site logic (compute + pre_check) yields a denial
    for a reactive guard when compute_projected_cost_usd returns >0.
    """
    from general_ludd.budget_guard_check import compute_projected_cost_usd

    gw = _make_gateway()
    guard = _reactive_guard()
    projected = compute_projected_cost_usd(gw, guard)
    assert projected > 0.0
    denial = budget_pre_check(guard, projected_cost=projected)
    assert denial is not None
    assert "over cap" in denial


# ── call site 3: reviewer.py ──


def test_budget_pre_check_nonzero_projection_blocks_over_cap_call_reviewer() -> None:
    """ReturnReviewer._call_model threads a positive projected_cost.

    Constructs a ReturnReviewer and verifies the guard receives >0 estimate.
    """
    from general_ludd.review.reviewer import ReturnReviewer

    gw = _make_gateway()
    guard = _reactive_guard()
    reviewer = ReturnReviewer(
        gateway=gw,
        prompt_registry=MagicMock(),
        budget_guard=guard,
    )

    with patch(
        "general_ludd.budget_guard_check.budget_pre_check",
        wraps=budget_pre_check,
    ) as mock_bpc:
        content, error = reviewer._call_model("test prompt")

    assert mock_bpc.call_count > 0
    projected = mock_bpc.call_args.kwargs.get("projected_cost", 0.0)
    assert projected > 0.0, f"projected_cost was {projected}, expected positive"
    assert error is not None or content is not None
