"""Focused tests for the scoring-router aggregate-parse helpers.

Covers the de-duplicated aggregate-row parser and the bool-guarded sample-count
coercion extracted during the models dedup refactor:
- ``_coerce_sample_count`` (bool-guard + non-numeric → 0 + negative clamp)
- ``AdaptiveRouter._parse_aggregates_to_candidates`` (shared min-samples +
  health-filter loop, with an optional ``max_cost`` cap)
"""

from __future__ import annotations

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter, _coerce_sample_count


def _agg(model_id: str, *, samples: int = 5, cost: float = 1.0, score: float = 0.7) -> dict:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": f"prompt-{model_id}",
        "sample_count": samples,
        "avg_cost": cost,
        "composite_score": score,
    }


class TestCoerceSampleCount:
    def test_int_passthrough(self) -> None:
        assert _coerce_sample_count(7) == 7

    def test_float_truncated(self) -> None:
        assert _coerce_sample_count(3.9) == 3

    def test_bool_true_counts_as_zero(self) -> None:
        # isinstance(True, int) is True; without the guard this would be 1.
        assert _coerce_sample_count(True) == 0

    def test_bool_false_counts_as_zero(self) -> None:
        assert _coerce_sample_count(False) == 0

    def test_negative_clamped_to_zero(self) -> None:
        assert _coerce_sample_count(-4) == 0

    def test_non_numeric_string_is_zero(self) -> None:
        assert _coerce_sample_count("abc") == 0

    def test_none_is_zero(self) -> None:
        assert _coerce_sample_count(None) == 0


class _Tracker:
    def __init__(self, unhealthy: set[str]) -> None:
        self._unhealthy = unhealthy

    def is_healthy(self, model_id: str, admit_probe: bool = True) -> bool:
        return model_id not in self._unhealthy


class TestParseAggregatesToCandidates:
    def test_basic_parse_all_pass(self) -> None:
        router = AdaptiveRouter(min_samples=3)
        cands = router._parse_aggregates_to_candidates(
            [_agg("a"), _agg("b")], TaskType.FEATURE
        )
        assert {c.model_profile_id for c in cands} == {"a", "b"}
        assert all(c.task_type is TaskType.FEATURE for c in cands)

    def test_min_samples_filter(self) -> None:
        router = AdaptiveRouter(min_samples=5)
        cands = router._parse_aggregates_to_candidates(
            [_agg("a", samples=4), _agg("b", samples=5)], TaskType.BUG_FIX
        )
        assert [c.model_profile_id for c in cands] == ["b"]

    def test_bool_sample_count_filtered_out(self) -> None:
        # sample_count=True would pass a naive int()==1 path but is coerced to 0.
        router = AdaptiveRouter(min_samples=1)
        cands = router._parse_aggregates_to_candidates(
            [_agg("a", samples=True)], TaskType.FEATURE  # type: ignore[arg-type]
        )
        assert cands == []

    def test_health_tracker_drops_unhealthy(self) -> None:
        router = AdaptiveRouter(min_samples=1, health_tracker=_Tracker({"bad"}))
        cands = router._parse_aggregates_to_candidates(
            [_agg("good"), _agg("bad")], TaskType.FEATURE
        )
        assert [c.model_profile_id for c in cands] == ["good"]

    def test_max_cost_cap_drops_over_budget(self) -> None:
        router = AdaptiveRouter(min_samples=1)
        cands = router._parse_aggregates_to_candidates(
            [_agg("cheap", cost=0.5), _agg("pricey", cost=5.0)],
            TaskType.FEATURE,
            max_cost=1.0,
        )
        assert [c.model_profile_id for c in cands] == ["cheap"]

    def test_max_cost_none_keeps_all(self) -> None:
        router = AdaptiveRouter(min_samples=1)
        cands = router._parse_aggregates_to_candidates(
            [_agg("cheap", cost=0.5), _agg("pricey", cost=5.0)],
            TaskType.FEATURE,
            max_cost=None,
        )
        assert {c.model_profile_id for c in cands} == {"cheap", "pricey"}

    def test_non_finite_cost_dropped_under_cap(self) -> None:
        router = AdaptiveRouter(min_samples=1)
        cands = router._parse_aggregates_to_candidates(
            [_agg("nan_cost", cost=float("nan"))],
            TaskType.FEATURE,
            max_cost=10.0,
        )
        assert cands == []
