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


# ── Learned token-cost classification refines the static taxonomy ───────────


def test_learned_light_flips_quality_worktype_to_weak() -> None:
    from general_ludd.observability.token_cost import TokenCostTracker

    t = TokenCostTracker(min_samples=3)
    for _ in range(3):
        t.record("review", 60, 40)            # total 100   -> light
        t.record("architecture", 6000, 4000)  # total 10000 -> heavy
        t.record("design", 600, 400)          # total 1000  -> moderate
    rec = build_optimization_hints(
        routing={"default_profile": "flagship", "weak_model_profile": "weak"},
        token_tracker=t,
    )["recommended_profile_for"]
    assert rec["review"] == "weak"            # quality type observed light -> cheap
    assert rec["architecture"] == "flagship"  # heavy stays quality
    assert rec["design"] == "flagship"        # moderate -> static (quality)


def test_learned_heavy_flips_cheap_worktype_to_quality() -> None:
    from general_ludd.observability.token_cost import TokenCostTracker

    t = TokenCostTracker(min_samples=3)
    for _ in range(3):
        t.record("summarize", 6000, 4000)  # heavy
        t.record("classify", 60, 40)       # light
        t.record("extract", 600, 400)      # moderate
    rec = build_optimization_hints(
        routing={"default_profile": "flagship", "weak_model_profile": "weak"},
        token_tracker=t,
    )["recommended_profile_for"]
    assert rec["summarize"] == "flagship"  # cheap type observed heavy -> quality
    assert rec["classify"] == "weak"       # light stays cheap


def test_empty_tracker_preserves_static_mapping() -> None:
    from general_ludd.observability.token_cost import TokenCostTracker

    routing = {"default_profile": "flagship", "weak_model_profile": "weak"}
    with_empty = build_optimization_hints(
        routing=routing, token_tracker=TokenCostTracker()
    )["recommended_profile_for"]
    no_tracker = build_optimization_hints(routing=routing)["recommended_profile_for"]
    assert with_empty == no_tracker
    assert with_empty["mechanical"] == "weak"
    assert with_empty["architecture"] == "flagship"


def test_learned_classification_defensive_on_raising_tracker() -> None:
    class _Boom:
        def classify(self, *a: object, **k: object) -> str:
            raise RuntimeError("boom")

    rec = build_optimization_hints(
        routing={"default_profile": "flagship", "weak_model_profile": "weak"},
        token_tracker=_Boom(),
    )["recommended_profile_for"]
    assert rec["mechanical"] == "weak"
    assert rec["architecture"] == "flagship"


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
# burn_rate — budget burn-velocity / time-to-exhaustion hint
# ---------------------------------------------------------------------------


def _burn_hints(out: dict) -> list[dict]:
    return [h for h in out["hints"] if h["signal"] == "burn_rate"]


def test_burn_rate_hint_emitted_when_spend_and_elapsed_positive() -> None:
    # spent 5.0 over 100s -> velocity 0.05 USD/s; remaining 50 -> ~1000s left.
    out = build_optimization_hints(
        budget={
            "run_spent_usd": 5.0,
            "run_remaining_usd": 50.0,
            "elapsed_seconds": 100.0,
            "run_limit_usd": 55.0,
        },
    )
    burn = _burn_hints(out)
    assert len(burn) == 1
    assert burn[0]["velocity_usd_per_sec"] == 0.05
    assert burn[0]["time_remaining_sec"] == 1000.0
    # Plenty of headroom -> info severity, not warning.
    assert burn[0]["severity"] == "info"


def test_burn_rate_near_exhaustion_is_warning() -> None:
    # spent 9.0 over 90s -> velocity 0.1 USD/s; remaining 1.0 -> 10s left < 120.
    out = build_optimization_hints(
        budget={
            "run_spent_usd": 9.0,
            "run_remaining_usd": 1.0,
            "elapsed_seconds": 90.0,
            "run_limit_usd": 10.0,
        },
    )
    burn = _burn_hints(out)
    assert burn and burn[0]["severity"] == "warning"
    assert burn[0]["time_remaining_sec"] is not None
    assert burn[0]["time_remaining_sec"] < 120.0


def test_burn_rate_omitted_when_no_elapsed() -> None:
    out = build_optimization_hints(
        budget={"run_spent_usd": 5.0, "run_remaining_usd": 50.0},
    )
    assert _burn_hints(out) == []


def test_burn_rate_omitted_when_zero_spend() -> None:
    out = build_optimization_hints(
        budget={
            "run_spent_usd": 0.0,
            "run_remaining_usd": 50.0,
            "elapsed_seconds": 100.0,
        },
    )
    assert _burn_hints(out) == []


def test_burn_rate_time_remaining_none_when_remaining_unknown() -> None:
    # velocity is computable but no run_remaining -> time_remaining is None and
    # severity falls back to info (never warning on an unknown horizon).
    out = build_optimization_hints(
        budget={"run_spent_usd": 5.0, "elapsed_seconds": 100.0},
    )
    burn = _burn_hints(out)
    assert burn and burn[0]["time_remaining_sec"] is None
    assert burn[0]["severity"] == "info"


# ---------------------------------------------------------------------------
# model_flaky — flakiness hint sourced from a (stubbed) MetricsCollector
# ---------------------------------------------------------------------------


class _StubCollector:
    """Minimal MetricsCollector duck-type for advisor flakiness tests."""

    def __init__(self, flaky: set[str], failures: dict[str, list[str]] | None = None):
        self._flaky = flaky
        self._failures = failures or {}

    def is_flaky(self, model_profile_id: str, *a: object, **k: object) -> bool:
        return model_profile_id in self._flaky

    def get_recent_failures(
        self, model_profile_id: str, *a: object, **k: object
    ) -> list[str]:
        return list(self._failures.get(model_profile_id, []))


def _flaky_hints(out: dict) -> list[dict]:
    return [h for h in out["hints"] if h["signal"] == "model_flaky"]


def test_model_flaky_hint_emitted_when_collector_reports_flaky() -> None:
    collector = _StubCollector(
        flaky={"flagship"},
        failures={"flagship": ["timeout", "500 err", "rate-limit", "extra"]},
    )
    out = build_optimization_hints(
        models=[
            {"profile_id": "flagship", "enabled": True},
            {"profile_id": "weak", "enabled": True},
        ],
        metrics_collector=collector,
    )
    flaky = _flaky_hints(out)
    assert len(flaky) == 1
    assert flaky[0]["model"] == "flagship"
    assert flaky[0]["severity"] == "warning"
    assert flaky[0]["recommendation"] == "prefer fallback profile"
    # recent_failures is capped at 3.
    assert flaky[0]["recent_failures"] == ["timeout", "500 err", "rate-limit"]


def test_model_flaky_hint_absent_when_collector_missing() -> None:
    out = build_optimization_hints(
        models=[{"profile_id": "flagship", "enabled": True}],
        metrics_collector=None,
    )
    assert _flaky_hints(out) == []


def test_model_flaky_skips_disabled_profiles() -> None:
    collector = _StubCollector(flaky={"flagship"})
    out = build_optimization_hints(
        models=[{"profile_id": "flagship", "enabled": False}],
        metrics_collector=collector,
    )
    assert _flaky_hints(out) == []


def test_model_flaky_defensive_on_raising_collector() -> None:
    class _Boom:
        def is_flaky(self, *a: object, **k: object) -> bool:
            raise RuntimeError("collector exploded")

    out = build_optimization_hints(
        models=[{"profile_id": "flagship", "enabled": True}],
        metrics_collector=_Boom(),
    )
    # No hint, no exception.
    assert _flaky_hints(out) == []


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
