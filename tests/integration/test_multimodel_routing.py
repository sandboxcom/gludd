"""
STEP 2 — Static task routing: proves ModelRouter correctly differentiates models by
task type/role/pattern/quality/latency and falls back to defaults for unknown roles.

STEP 4 — Adaptive weighting inert: proves AdaptiveRouter falls back to config when
benchmark history is empty (CA-T11). When benchmark_repo=None, no historical data
exists, so the adaptive router must return fallback=True with reason
"insufficient_historical_data" and pass through the caller-supplied default model
profile unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import general_ludd.routing_roles  # noqa: F401 — import cycle warm-up; must precede gateway imports
from general_ludd.config.model_routing import ModelRoutingConfig, build_router_from_config
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# STEP 2: Static routing fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def routing_config() -> ModelRoutingConfig:
    """Multi-profile config that maps roles, patterns, quality, and latency."""
    return ModelRoutingConfig(
        default_profile="glm46_profile",
        weak_model_profile="glm_turbo_profile",
        role_routing={
            "coder": "glm46_profile",
            "planner": "glm_air_profile",
            "fast": "glm_turbo_profile",
        },
        pattern_routing={
            "code_generation": "coder",
            "planning": "planner",
        },
        quality_routing={
            "high": "glm46_profile",
            "medium": "glm_air_profile",
        },
        latency_routing={
            "fast": "glm_turbo_profile",
        },
    )


@pytest.fixture()
def router(routing_config: ModelRoutingConfig):
    return build_router_from_config(routing_config)


# ---------------------------------------------------------------------------
# STEP 2: Role resolution
# ---------------------------------------------------------------------------

class TestStaticRoleRouting:
    def test_coder_resolves_to_glm46(self, router):
        assert router.resolve_role("coder") == "glm46_profile"

    def test_planner_resolves_to_glm_air(self, router):
        assert router.resolve_role("planner") == "glm_air_profile"

    def test_fast_resolves_to_glm_turbo(self, router):
        assert router.resolve_role("fast") == "glm_turbo_profile"

    def test_unknown_role_falls_back_to_default(self, router):
        assert router.resolve_role("unknown") == "glm46_profile"

    def test_weak_sentinel_resolves_to_weak_model(self, router):
        assert router.resolve_role("weak") == "glm_turbo_profile"

    def test_all_three_task_roles_are_distinct(self, router):
        coder_profile = router.resolve_role("coder")
        planner_profile = router.resolve_role("planner")
        fast_profile = router.resolve_role("fast")
        assert coder_profile != planner_profile, "coder and planner must map to different profiles"
        assert coder_profile != fast_profile, "coder and fast must map to different profiles"
        assert planner_profile != fast_profile, "planner and fast must map to different profiles"


# ---------------------------------------------------------------------------
# STEP 2: Pattern resolution (resolves through role)
# ---------------------------------------------------------------------------

class TestStaticPatternRouting:
    def test_code_generation_resolves_via_coder_to_glm46(self, router):
        # code_generation -> role "coder" -> "glm46_profile"
        assert router.resolve_pattern("code_generation") == "glm46_profile"

    def test_planning_resolves_via_planner_to_glm_air(self, router):
        # planning -> role "planner" -> "glm_air_profile"
        assert router.resolve_pattern("planning") == "glm_air_profile"


# ---------------------------------------------------------------------------
# STEP 2: Quality and latency resolution
# ---------------------------------------------------------------------------

class TestStaticQualityLatencyRouting:
    def test_high_quality_resolves_to_glm46(self, router):
        assert router.resolve_by_quality("high") == "glm46_profile"

    def test_medium_quality_resolves_to_glm_air(self, router):
        assert router.resolve_by_quality("medium") == "glm_air_profile"

    def test_fast_latency_resolves_to_glm_turbo(self, router):
        assert router.resolve_by_latency("fast") == "glm_turbo_profile"


# ---------------------------------------------------------------------------
# STEP 4: AdaptiveRouter falls back when history is empty (CA-T11)
# ---------------------------------------------------------------------------

class TestAdaptiveRouterInertWithNoHistory:
    """When benchmark_repo=None no historical data exists; the router must
    always fall back to the caller-supplied default_model_profile."""

    _DEFAULT = "glm46_profile"

    def _route(self, task_type: TaskType) -> object:
        router = AdaptiveRouter(benchmark_repo=None)
        return asyncio.run(router.route(task_type, default_model_profile=self._DEFAULT))

    def test_feature_falls_back(self):
        decision = self._route(TaskType.FEATURE)
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        assert decision.selected_model_profile_id == self._DEFAULT

    def test_bug_fix_falls_back(self):
        decision = self._route(TaskType.BUG_FIX)
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        assert decision.selected_model_profile_id == self._DEFAULT

    def test_code_review_falls_back(self):
        decision = self._route(TaskType.CODE_REVIEW)
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        assert decision.selected_model_profile_id == self._DEFAULT


class TestAdaptiveRouterInertWithWiredButEmptyRepo:
    """CA-T11 (stronger): even with a benchmark_repo WIRED, when it returns no
    usable history the adaptive weighting must NOT override the static default.

    Two inert conditions are proven:
      (a) repo returns an empty aggregate list  -> fall back to static default
      (b) repo returns aggregates BELOW min_samples -> fall back to static default

    In both cases benchmark-history-based selection is inert and the
    caller-supplied default_model_profile (the static/config choice) wins. This
    is the core "adaptive weights are inert until history accrues" guarantee.
    """

    _DEFAULT = "glm46_profile"
    _ALT = "glm_air_profile"  # what history WOULD pick if it were active

    def test_empty_aggregates_keep_static_default(self):
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=[])
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = asyncio.run(
            router.route(TaskType.FEATURE, default_model_profile=self._DEFAULT)
        )
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"
        assert decision.selected_model_profile_id == self._DEFAULT

    def test_below_min_samples_keeps_static_default(self):
        # History EXISTS for the alternative model, but only 1 sample (< min 3),
        # so it must be ignored and the static default must win unchanged.
        agg = [
            {
                "model_profile_id": self._ALT,
                "prompt_profile_id": None,
                "composite_score": 0.99,  # would dominate if it were eligible
                "avg_cost": 0.001,
                "sample_count": 1,
                "task_type": TaskType.FEATURE.value,
            }
        ]
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(return_value=agg)
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)
        decision = asyncio.run(
            router.route(TaskType.FEATURE, default_model_profile=self._DEFAULT)
        )
        assert decision.fallback is True
        assert decision.selected_model_profile_id == self._DEFAULT
        assert decision.selected_model_profile_id != self._ALT
