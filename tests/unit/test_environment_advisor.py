"""Unit tests for the pure optimization advisor (build_optimization_hints)."""

from __future__ import annotations

from general_ludd.controllers.environment_advisor import (
    build_advice,
    build_optimization_hints,
)


def test_budget_near_limit_yields_budget_hint() -> None:
    out = build_optimization_hints(
        models=[],
        routing={"weak_model_profile": "weak"},
        budget={"run_spent_usd": 9.5, "run_limit_usd": 10.0},
    )
    budget_hints = [h for h in out["hints"] if h["signal"] == "budget"]
    assert len(budget_hints) == 1
    assert budget_hints[0]["severity"] == "warning"
    assert "weak" in budget_hints[0]["recommendation"]


def test_budget_over_limit_is_critical() -> None:
    out = build_optimization_hints(
        budget={"run_spent_usd": 12.0, "run_limit_usd": 10.0},
        routing={"weak_model_profile": "weak"},
    )
    budget_hints = [h for h in out["hints"] if h["signal"] == "budget"]
    assert budget_hints and budget_hints[0]["severity"] == "critical"


def test_budget_well_under_limit_yields_no_budget_hint() -> None:
    out = build_optimization_hints(
        budget={"run_spent_usd": 1.0, "run_limit_usd": 10.0},
        routing={"weak_model_profile": "weak"},
    )
    assert not [h for h in out["hints"] if h["signal"] == "budget"]


def test_mechanical_work_maps_to_weak_profile() -> None:
    out = build_optimization_hints(
        routing={"default_profile": "flagship", "weak_model_profile": "weak"},
    )
    rec = out["recommended_profile_for"]
    assert rec["mechanical"] == "weak"
    # High-quality work routes to the default profile.
    assert rec["architecture"] == "flagship"


def test_role_routing_overrides_default_for_quality_work() -> None:
    out = build_optimization_hints(
        routing={
            "default_profile": "flagship",
            "weak_model_profile": "weak",
            "roles": {"review": "reviewer-pro"},
        },
    )
    assert out["recommended_profile_for"]["review"] == "reviewer-pro"


def test_metered_model_with_fallback_yields_hint() -> None:
    out = build_optimization_hints(
        models=[
            {
                "profile_id": "flagship",
                "enabled": True,
                "api_metered": True,
                "fallback_profiles": ["weak"],
            },
            {"profile_id": "weak", "enabled": True, "api_metered": False},
        ],
        routing={},
    )
    metered = [h for h in out["hints"] if h["signal"] == "metered_model"]
    assert metered and "weak" in metered[0]["recommendation"]


def test_advisor_is_defensive_on_garbage_input() -> None:
    # None/garbage must not raise and must yield safe empty defaults.
    out = build_optimization_hints(
        models=[None, 42, {"profile_id": "x"}],  # type: ignore[list-item]
        routing=None,
        budget={"run_spent_usd": "nan", "run_limit_usd": None},
    )
    assert out["hints"] == []
    assert out["recommended_profile_for"] == {}


# ---------------------------------------------------------------------------
# build_advice — per-task advice surface (pure logic)
# ---------------------------------------------------------------------------


def _rec(**kw: object) -> dict[str, object]:
    base = {
        "selected_model_profile_id": "flagship",
        "selected_prompt_profile_id": "pp1",
        "composite_score": 0.7,
        "estimated_cost_usd": 0.02,
        "sample_count": 5,
        "fallback": False,
        "reason": "best_historical_score",
    }
    base.update(kw)
    return base


def test_build_advice_contract_keys() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"model_name": "gpt-4o", "context_window": 128000,
                 "max_output_tokens": 8000},
        est_cost_usd=0.02,
        gateway_present=True,
    )
    for key in (
        "task_type",
        "recommendation",
        "route",
        "est_cost_usd",
        "use_workflow",
        "workflow_reason",
        "resource_hints",
    ):
        assert key in out
    assert out["recommendation"]["model_profile"] == "flagship"
    assert out["route"]["selected_prompt_profile_id"] == "pp1"
    assert isinstance(out["est_cost_usd"], float)


def test_build_advice_use_workflow_true_for_feature_with_gateway() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        gateway_present=True,
        est_cost_usd=0.01,
        budget_ok=True,
    )
    assert out["use_workflow"] is True


def test_build_advice_use_workflow_false_for_docs() -> None:
    out = build_advice(
        work_type="docs",
        recommendation=_rec(),
        gateway_present=True,
        est_cost_usd=0.01,
    )
    assert out["use_workflow"] is False
    assert "docs" in out["workflow_reason"]


def test_build_advice_use_workflow_false_without_gateway() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        gateway_present=False,
    )
    assert out["use_workflow"] is False
    assert "gateway" in out["workflow_reason"]


def test_build_advice_use_workflow_false_when_budget_tight() -> None:
    # est_cost 0.50, retries default 2 -> needs 1.50 headroom; only 0.10 left.
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        gateway_present=True,
        est_cost_usd=0.50,
        budget_ok=True,
        budget_remaining_usd=0.10,
    )
    assert out["use_workflow"] is False
    assert "headroom" in out["workflow_reason"]


def test_build_advice_use_workflow_false_when_budget_not_ok() -> None:
    # gateway present + feature, but the budget guard has tripped.
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        gateway_present=True,
        est_cost_usd=0.01,
        budget_ok=False,
    )
    assert out["use_workflow"] is False
    assert out["resource_hints"]["budget_ok"] is False


def test_build_advice_priority_latency_does_not_prefer_local() -> None:
    # priority=latency (not cost) -> never prefer_local even with free profile.
    out = build_advice(
        work_type="feature",
        priority="latency",
        recommendation=_rec(),
        gateway_present=True,
        local_model_allowed=True,
        has_local_or_free_profile=True,
    )
    assert out["resource_hints"]["prefer_local"] is False


def test_build_advice_prefer_local_only_when_cost_priority_and_free_profile() -> None:
    # priority=cost + local allowed + a free profile exists -> prefer_local.
    cost = build_advice(
        work_type="feature",
        priority="cost",
        recommendation=_rec(),
        gateway_present=True,
        local_model_allowed=True,
        has_local_or_free_profile=True,
    )
    assert cost["resource_hints"]["prefer_local"] is True
    # priority=quality with the same hardware -> NOT prefer_local. This is the
    # tiebreak difference between cost and quality priority.
    quality = build_advice(
        work_type="feature",
        priority="quality",
        recommendation=_rec(),
        gateway_present=True,
        local_model_allowed=True,
        has_local_or_free_profile=True,
    )
    assert quality["resource_hints"]["prefer_local"] is False


def test_build_advice_context_fits_flag() -> None:
    fits = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"context_window": 1000},
        prompt_tokens=500,
        gateway_present=True,
    )
    assert fits["resource_hints"]["context_fits"] is True
    overflow = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"context_window": 1000},
        prompt_tokens=5000,
        gateway_present=True,
    )
    assert overflow["resource_hints"]["context_fits"] is False


def test_build_advice_latency_class_reasoning_is_slow() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"model_name": "glm-4.5-air", "max_output_tokens": 8000},
        gateway_present=True,
    )
    assert out["recommendation"]["latency_class"] == "slow"


def test_build_advice_latency_class_unhealthy_is_degraded() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"model_name": "gpt-4o", "max_output_tokens": 8000},
        gateway_present=True,
        healthy=False,
    )
    assert out["recommendation"]["latency_class"] == "degraded"


def test_build_advice_latency_class_small_output_is_fast() -> None:
    out = build_advice(
        work_type="feature",
        recommendation=_rec(),
        profile={"model_name": "gpt-4o-mini", "max_output_tokens": 1000},
        gateway_present=True,
    )
    assert out["recommendation"]["latency_class"] == "fast"


def test_build_advice_never_raises_on_garbage() -> None:
    out = build_advice(
        work_type="feature",
        recommendation={"composite_score": "nan"},  # type: ignore[dict-item]
        profile=None,
        est_cost_usd=float("inf"),
    )
    # Falls through to a safe contract, never raises.
    assert out["task_type"] == "feature"
    assert isinstance(out["est_cost_usd"], float)
    assert out["use_workflow"] in (True, False)
