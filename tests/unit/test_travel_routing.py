"""Unit tests for travel routing.py module_utils."""

from __future__ import annotations

from datetime import datetime

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    MultiStopRoute,
    RouteStop,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.routing import (
    DijkstraRouter,
    MultiStopOptimizer,
    TimeBudgetAllocator,
)


def _make_stop(index: int, city: str, country: str = "USA") -> RouteStop:
    return RouteStop(stop_index=index, city=city, country=country)


class TestMultiStopOptimizer:
    def test_optimize_two_stops(self):
        stops = [_make_stop(0, "JFK"), _make_stop(1, "LHR")]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize()
        assert isinstance(route, MultiStopRoute)
        assert route.optimized is True
        assert len(route.segments) == 1
        assert route.total_cost > 0

    def test_optimize_single_stop_unoptimized(self):
        stops = [_make_stop(0, "JFK")]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize()
        assert route.optimized is False
        assert route.name == "unoptimized"

    def test_optimize_three_stops_nearest_neighbor(self):
        stops = [_make_stop(0, "JFK"), _make_stop(1, "LHR"), _make_stop(2, "CDG")]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize(strategy="nearest_neighbor")
        assert route.optimized is True
        assert len(route.segments) >= 2
        assert route.total_cost > 0
        assert route.segments[0].from_location != route.segments[0].to_location

    def test_optimize_five_stops(self):
        stops = [
            _make_stop(0, "JFK"),
            _make_stop(1, "LHR"),
            _make_stop(2, "CDG"),
            _make_stop(3, "FRA"),
            _make_stop(4, "LAX"),
        ]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize(strategy="nearest_neighbor")
        assert route.optimized is True
        assert len(route.segments) == len(stops) - 1

    def test_route_name_is_arrow_separated(self):
        stops = [_make_stop(0, "JFK"), _make_stop(1, "LHR"), _make_stop(2, "CDG")]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize()
        assert "JFK" in route.name
        assert "LHR" in route.name

    def test_unoptimized_has_validation_fail(self):
        stops = [_make_stop(0, "JFK")]
        opt = MultiStopOptimizer(stops)
        route = opt.optimize()
        assert len(route.validation) == 1
        assert route.validation[0].status.value == "fail"
        assert "too_few_stops" in route.validation[0].check


class TestDijkstraRouter:
    def test_shortest_path_direct(self):
        router = DijkstraRouter()
        path, cost = router.shortest_path("JFK", "LHR")
        assert path[0] == "JFK"
        assert path[-1] == "LHR"
        assert cost > 0

    def test_shortest_path_same_node(self):
        router = DijkstraRouter()
        path, cost = router.shortest_path("JFK", "JFK")
        assert path == ["JFK"]
        assert cost == 0.0

    def test_shortest_path_unknown_origin_raises(self):
        import pytest

        router = DijkstraRouter()
        with pytest.raises(ValueError, match="not found in route graph"):
            router.shortest_path("MARS", "JFK")

    def test_shortest_path_unknown_destination_raises(self):
        import pytest

        router = DijkstraRouter()
        with pytest.raises(ValueError, match="not found in route graph"):
            router.shortest_path("JFK", "MARS")

    def test_shortest_path_multi_stop(self):
        router = DijkstraRouter()
        path, cost = router.shortest_path("JFK", "CDG")
        assert path[0] == "JFK"
        assert path[-1] == "CDG"
        assert cost > 0

    def test_all_paths_returns_paths(self):
        router = DijkstraRouter()
        paths = router.all_paths("JFK")
        assert len(paths) >= 1
        for tgt, (path, _cost) in paths.items():
            assert path[0] == "JFK"
            assert path[-1] == tgt

    def test_all_paths_unknown_node_returns_empty(self):
        router = DijkstraRouter()
        paths = router.all_paths("MARS")
        assert paths == {}

    def test_all_paths_excludes_source(self):
        router = DijkstraRouter()
        paths = router.all_paths("JFK")
        assert "JFK" not in paths

    def test_graph_property_returns_dict(self):
        router = DijkstraRouter()
        g = router.graph
        assert isinstance(g, dict)
        assert len(g) >= 1
        assert "JFK" in g
        assert isinstance(g["JFK"], dict)

    def test_unreachable_node_raises(self):
        import pytest

        router = DijkstraRouter()
        # Remove edges to isolate a node
        with pytest.raises(ValueError):
            router.shortest_path("JFK", "ZZZ")


class TestTimeBudgetAllocator:
    def test_allocate_splits_time(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=3)
        assert len(result) == 3
        for entry in result:
            assert entry["dwell_hours"] > 0

    def test_allocate_zero_stops_returns_empty(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=0)
        assert result == []

    def test_allocate_with_transit_time(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=2, transit_hours=6.0)
        assert len(result) == 2
        assert result[0]["transit_hours"] == 6.0  # per stop transit for 2 stops
        assert result[0]["dwell_hours"] < 12.0

    def test_allocate_transit_exceeds_time(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 12, 0)
        result = alloc.allocate(start, end, stop_count=2, transit_hours=10.0)
        for entry in result:
            assert entry["dwell_hours"] == 0.0

    def test_allocate_with_custom_weights(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=2, dwell_weights=[0.7, 0.3])
        assert result[0]["dwell_hours"] > result[1]["dwell_hours"]

    def test_allocate_default_weights_equal(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=3)
        dwells = [e["dwell_hours"] for e in result]
        assert max(dwells) - min(dwells) < 0.1

    def test_allocate_single_stop_no_transit(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 8, 0)
        end = datetime(2026, 9, 1, 20, 0)
        result = alloc.allocate(start, end, stop_count=1)
        assert len(result) == 1
        assert result[0]["stop_index"] == 0
        assert result[0]["transit_hours"] == 0.0

    def test_allocate_end_before_start(self):
        alloc = TimeBudgetAllocator()
        start = datetime(2026, 9, 1, 20, 0)
        end = datetime(2026, 9, 1, 8, 0)
        result = alloc.allocate(start, end, stop_count=2)
        for entry in result:
            assert entry["dwell_hours"] == 0.0
