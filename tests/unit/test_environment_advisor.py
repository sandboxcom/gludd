"""Unit tests for the pure optimization advisor (build_optimization_hints)."""

from __future__ import annotations

from general_ludd.controllers.environment_advisor import build_optimization_hints


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
