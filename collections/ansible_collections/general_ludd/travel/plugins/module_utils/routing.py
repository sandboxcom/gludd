"""Travel routing module — multi-stop optimization, shortest-path, time-budget.
Moved from src/general_ludd/travel/routing.py.

Classes:
  MultiStopOptimizer  — order stops via nearest-neighbor or 2-opt heuristics
  DijkstraRouter      — shortest-path between airport code nodes in the cost graph
  TimeBudgetAllocator — divide total trip duration into per-stop dwell + transit
"""

from __future__ import annotations

import contextlib
import heapq
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    MultiStopRoute,
    RouteStop,
    SegmentKind,
    TripSegment,
    ValidationEntry,
    ValidationStatus,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.core import (
    _ROUGH_DISTANCES,
    _SUPPORTED_ROUTES,
    _TRANSPORT_COST_PER_MILE,
)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# shared distance / cost helpers
# ---------------------------------------------------------------------------


def _distance_between(a: str, b: str) -> int:
    return _ROUGH_DISTANCES.get((a.upper(), b.upper()), 1500)


def _flight_cost(a: str, b: str) -> float:
    return round(_distance_between(a, b) * _TRANSPORT_COST_PER_MILE["flight"], 2)


def _flight_duration(a: str, b: str) -> int:
    dist = _distance_between(a, b)
    return max(60, dist // 9 + 30)


# ---------------------------------------------------------------------------
# MultiStopOptimizer
# ---------------------------------------------------------------------------


class MultiStopOptimizer:
    """Optimize a list of stops into a cost-efficient route.

    Strategies:
      - ``nearest_neighbor`` (default): greedy — start at the first stop,
        always go to the cheapest unvisited next stop.
    """

    def __init__(self, stops: list[RouteStop]) -> None:
        self._stops = list(stops)

    # ---------------------------------------------------------------
    # public API
    # ---------------------------------------------------------------

    def optimize(self, strategy: str = "nearest_neighbor") -> MultiStopRoute:
        if len(self._stops) < 2:
            return self._unoptimized(f"{len(self._stops)} stops — need at least 2")

        fn = _STRATEGIES.get(strategy, _nearest_neighbor)
        ordered = fn(self._stops)
        segments = self._build_segments(ordered)
        return MultiStopRoute(
            name=self._route_name(ordered),
            segments=segments,
            optimized=True,
            validation=[],
        )

    # ---------------------------------------------------------------
    # internals
    # ---------------------------------------------------------------

    @staticmethod
    def _route_name(stops: list[RouteStop]) -> str:
        return " \u2192 ".join(s.city for s in stops)

    def _unoptimized(self, reason: str) -> MultiStopRoute:
        return MultiStopRoute(
            name="unoptimized",
            segments=[
                TripSegment(
                    segment_type=SegmentKind.transport,
                    from_location="N/A",
                    to_location="N/A",
                    departure=datetime(2000, 1, 1),
                    arrival=datetime(2000, 1, 1),
                    cost=0.0,
                    currency="USD",
                )
            ],
            optimized=False,
            validation=[ValidationEntry(check="too_few_stops", status=ValidationStatus.fail, detail=reason)],
        )

    def _build_segments(self, stops: list[RouteStop]) -> list[TripSegment]:
        segments: list[TripSegment] = []
        for i in range(1, len(stops)):
            a, b = stops[i - 1], stops[i]
            cost = _flight_cost(a.city, b.city)
            dur = _flight_duration(a.city, b.city)
            dep = datetime(2026, 9, 1, 10, 0)
            segments.append(
                TripSegment(
                    segment_type=SegmentKind.transport,
                    from_location=a.city,
                    to_location=b.city,
                    departure=dep,
                    arrival=dep + timedelta(minutes=dur),
                    cost=cost,
                    currency="USD",
                )
            )
        return segments


# -- strategy implementations ------------------------------------------------


def _nearest_neighbor(stops: list[RouteStop]) -> list[RouteStop]:
    ordered: list[RouteStop] = [stops[0]]
    remaining: set[int] = {s.stop_index for s in stops[1:]}
    by_index: dict[int, RouteStop] = {s.stop_index: s for s in stops}
    current = stops[0]

    while remaining:
        best_idx = -1
        best_cost = float("inf")
        for idx in remaining:
            cost = _flight_cost(current.city, by_index[idx].city)
            if cost < best_cost:
                best_cost = cost
                best_idx = idx
        ordered.append(by_index[best_idx])
        remaining.discard(best_idx)
        current = by_index[best_idx]

    return ordered


_STRATEGIES: dict[str, Callable[[list[RouteStop]], list[RouteStop]]] = {"nearest_neighbor": _nearest_neighbor}


# ---------------------------------------------------------------------------
# DijkstraRouter
# ---------------------------------------------------------------------------


class DijkstraRouter:
    """Shortest-path router over the airport-code cost graph."""

    def __init__(self) -> None:
        self._graph: dict[str, dict[str, float]] = self._build_graph()

    # ---------------------------------------------------------------
    # public API
    # ---------------------------------------------------------------

    def shortest_path(self, origin: str, destination: str) -> tuple[list[str], float]:
        """Return (ordered_city_list, total_cost)."""
        src = origin.upper()
        tgt = destination.upper()

        if src not in self._graph:
            raise ValueError(f"origin '{origin}' not found in route graph")
        if tgt not in self._graph:
            raise ValueError(f"destination '{destination}' not found in route graph")

        if src == tgt:
            return [src], 0.0

        dist: dict[str, float] = {}
        prev: dict[str, str | None] = {}
        for node in self._graph:
            dist[node] = float("inf")
            prev[node] = None
        dist[src] = 0.0

        pq: list[tuple[float, str]] = [(0.0, src)]
        visited: set[str] = set()

        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == tgt:
                break
            for v, w in self._graph.get(u, {}).items():
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if dist.get(tgt, float("inf")) == float("inf"):
            raise ValueError(f"'{destination}' is unreachable from '{origin}'")

        path: list[str] = []
        cur: str | None = tgt
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()

        return path, round(dist[tgt], 2)

    def all_paths(self, origin: str) -> dict[str, tuple[list[str], float]]:
        """Return shortest paths to every reachable node from *origin*."""
        src = origin.upper()
        if src not in self._graph:
            return {}
        result: dict[str, tuple[list[str], float]] = {}
        for tgt in self._graph:
            if tgt == src:
                continue
            with contextlib.suppress(ValueError):
                result[tgt] = self.shortest_path(src, tgt)
        return result

    @property
    def graph(self) -> dict[str, dict[str, float]]:
        return dict(self._graph)

    # ---------------------------------------------------------------
    # internals
    # ---------------------------------------------------------------

    def _build_graph(self) -> dict[str, dict[str, float]]:
        g: dict[str, dict[str, float]] = {}
        nodes: set[str] = set()

        for (a, b), _dist in _ROUGH_DISTANCES.items():
            nodes.add(a)
            nodes.add(b)

        for src in nodes:
            g[src] = {}
            for dst in _SUPPORTED_ROUTES.get(src, []):
                cost = _flight_cost(src, dst)
                g[src][dst] = cost

        for src in _SUPPORTED_ROUTES:
            for dst in _SUPPORTED_ROUTES[src]:
                if src in g and dst in g[src]:
                    continue
                if src not in g:
                    g[src] = {}
                g[src][dst] = _flight_cost(src, dst)

        return g


# ---------------------------------------------------------------------------
# TimeBudgetAllocator
# ---------------------------------------------------------------------------


class TimeBudgetAllocator:
    """Divide a total trip window into per-stop dwell and transit allocations."""

    def allocate(
        self,
        start: datetime,
        end: datetime,
        stop_count: int,
        *,
        transit_hours: float = 0.0,
        dwell_weights: list[float] | None = None,
    ) -> list[dict[str, float]]:
        if stop_count <= 0:
            return []

        total_hours = max(0.0, (end - start).total_seconds() / 3600.0)
        available = max(0.0, total_hours - transit_hours)

        if available <= 0:
            return [
                {"stop_index": i, "dwell_hours": 0.0, "transit_hours": transit_hours / max(1, stop_count)}
                for i in range(stop_count)
            ]

        weights = self._normalize_weights(dwell_weights, stop_count)
        per_stop_transit = transit_hours / max(1, stop_count - 1) if stop_count > 1 else 0.0

        result: list[dict[str, float]] = []
        for i in range(stop_count):
            result.append(
                {
                    "stop_index": i,
                    "dwell_hours": round(available * weights[i], 2),
                    "transit_hours": round(per_stop_transit, 2),
                }
            )

        return result

    @staticmethod
    def _normalize_weights(raw: list[float] | None, n: int) -> list[float]:
        if raw is None or len(raw) != n:
            return [1.0 / n] * n
        total = sum(raw)
        if total == 0:
            return [1.0 / n] * n
        return [w / total for w in raw]


__all__ = [
    "DijkstraRouter",
    "MultiStopOptimizer",
    "TimeBudgetAllocator",
]
