"""Unit tests for G8: ParetoRouter — cost/quality Pareto frontier routing."""

from general_ludd.scoring.pareto import ParetoRouter


class TestParetoRouter:
    def test_constructor_defaults(self) -> None:
        router = ParetoRouter()
        assert router._cost_weight == 0.5
        assert router._quality_weight == 0.5

    def test_constructor_custom_weights(self) -> None:
        router = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        assert router._cost_weight == 0.3
        assert router._quality_weight == 0.7

    def test_route_by_pareto_frontier_returns_candidates(self) -> None:
        router = ParetoRouter()
        candidates = [
            {"model": "a", "cost": 0.01, "quality": 0.9},
            {"model": "b", "cost": 0.02, "quality": 0.8},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert result == candidates

    def test_route_by_pareto_frontier_empty_list(self) -> None:
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []
