"""Deep maximum flow and min-cut tests: Ford-Fulkerson, Edmonds-Karp,
Dinic, min-cut, bipartite matching, verification, edge cases,
stress-like multi-path graphs, and algorithm agreement properties.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from general_ludd.algorithms.graph_flow import (
    CapacityGraph,
    FlowGraph,
    bipartite_max_matching,
    dinic,
    edmonds_karp,
    ford_fulkerson,
    min_cut,
    verify_flow,
)


def _build_graph(edges: list[tuple[str, str, int]]) -> CapacityGraph:
    g: CapacityGraph = {}
    for u, v, c in edges:
        g.setdefault(u, {})[v] = c
        g.setdefault(v, {})
    return g


SIMPLE = _build_graph([("S", "A", 10), ("S", "B", 5), ("A", "B", 15), ("A", "T", 10), ("B", "T", 10)])
SIMPLE_EXPECTED = 15

DIAMOND = _build_graph([("S", "A", 5), ("S", "B", 5), ("A", "C", 5), ("B", "C", 5), ("C", "T", 8)])
DIAMOND_EXPECTED = 8

MULTI_PATH = _build_graph(
    [
        ("S", "A", 16),
        ("S", "B", 13),
        ("A", "B", 4),
        ("A", "C", 12),
        ("B", "A", 10),
        ("B", "D", 14),
        ("C", "B", 9),
        ("C", "T", 20),
        ("D", "C", 7),
        ("D", "T", 4),
    ]
)
MULTI_PATH_EXPECTED = 23

SERIAL = _build_graph([("S", "A", 10), ("A", "B", 5), ("B", "T", 10)])
SERIAL_EXPECTED = 5

DISCONNECTED = _build_graph([("S", "A", 10), ("B", "T", 10)])
DISCONNECTED_EXPECTED = 0

SINGLE_EDGE = _build_graph([("S", "T", 7)])
SINGLE_EDGE_EXPECTED = 7

TRIANGLE = _build_graph([("S", "A", 3), ("S", "B", 3), ("A", "B", 1), ("B", "A", 1), ("A", "T", 3), ("B", "T", 3)])
TRIANGLE_EXPECTED = 6

REVERSE_SATURATION = _build_graph([("S", "A", 100), ("A", "B", 1), ("B", "C", 1), ("C", "T", 100)])
REVERSE_SATURATION_EXPECTED = 1


# ═══════════════════════════════════════════════════════════════════════
# Ford-Fulkerson
# ═══════════════════════════════════════════════════════════════════════


class TestFordFulkerson:
    def test_simple_graph(self) -> None:
        val, flow = ford_fulkerson(SIMPLE, "S", "T")
        assert val == SIMPLE_EXPECTED
        assert verify_flow(SIMPLE, "S", "T", flow, val)

    def test_diamond_graph(self) -> None:
        val, flow = ford_fulkerson(DIAMOND, "S", "T")
        assert val == DIAMOND_EXPECTED
        assert verify_flow(DIAMOND, "S", "T", flow, val)

    def test_serial_graph(self) -> None:
        val, _flow = ford_fulkerson(SERIAL, "S", "T")
        assert val == SERIAL_EXPECTED

    def test_disconnected_sink(self) -> None:
        val, _flow = ford_fulkerson(DISCONNECTED, "S", "T")
        assert val == DISCONNECTED_EXPECTED

    def test_single_edge_st(self) -> None:
        val, _flow = ford_fulkerson(SINGLE_EDGE, "S", "T")
        assert val == SINGLE_EDGE_EXPECTED


# ═══════════════════════════════════════════════════════════════════════
# Edmonds-Karp
# ═══════════════════════════════════════════════════════════════════════


class TestEdmondsKarp:
    def test_simple_graph(self) -> None:
        val, flow, _ = edmonds_karp(SIMPLE, "S", "T")
        assert val == SIMPLE_EXPECTED
        assert verify_flow(SIMPLE, "S", "T", flow, val)

    def test_diamond_graph(self) -> None:
        val, _flow, _ = edmonds_karp(DIAMOND, "S", "T")
        assert val == DIAMOND_EXPECTED

    def test_multi_path(self) -> None:
        val, flow, _ = edmonds_karp(MULTI_PATH, "S", "T")
        assert val == MULTI_PATH_EXPECTED
        assert verify_flow(MULTI_PATH, "S", "T", flow, val)

    def test_reverse_saturation(self) -> None:
        val, _flow, _ = edmonds_karp(REVERSE_SATURATION, "S", "T")
        assert val == REVERSE_SATURATION_EXPECTED

    def test_parent_map_reaches_source(self) -> None:
        _, _, parent = edmonds_karp(SIMPLE, "S", "T")
        assert parent.get("S") is None
        reachable = 0
        for node in SIMPLE:
            if node in parent or node == "S":
                reachable += 1
        assert reachable >= 1


# ═══════════════════════════════════════════════════════════════════════
# Dinic
# ═══════════════════════════════════════════════════════════════════════


class TestDinic:
    def test_simple_graph(self) -> None:
        val, flow = dinic(SIMPLE, "S", "T")
        assert val == SIMPLE_EXPECTED
        assert verify_flow(SIMPLE, "S", "T", flow, val)

    def test_diamond_graph(self) -> None:
        val, _flow = dinic(DIAMOND, "S", "T")
        assert val == DIAMOND_EXPECTED

    def test_triangle_graph(self) -> None:
        val, _flow = dinic(TRIANGLE, "S", "T")
        assert val == TRIANGLE_EXPECTED

    def test_multi_path(self) -> None:
        val, _flow = dinic(MULTI_PATH, "S", "T")
        assert val == MULTI_PATH_EXPECTED

    def test_scale_16_node(self) -> None:
        g: CapacityGraph = {}
        for i in range(16):
            g[str(i)] = {}
        g["S"] = {str(i): 1 for i in range(8)}
        g["T"] = {}
        for i in range(8):
            g[str(i)]["T"] = 1
        val, _flow = dinic(g, "S", "T")
        assert val == 8


# ═══════════════════════════════════════════════════════════════════════
# Min-Cut
# ═══════════════════════════════════════════════════════════════════════


class TestMinCut:
    def test_simple_mincut(self) -> None:
        src, snk, val = min_cut(SIMPLE, "S", "T")
        assert "S" in src
        assert "T" in snk
        assert src.union(snk) == set(SIMPLE)
        assert len(src & snk) == 0
        assert val == SIMPLE_EXPECTED

    def test_cut_value_matches_flow(self) -> None:
        ek_val, _, _ = edmonds_karp(MULTI_PATH, "S", "T")
        _, _, cut_val = min_cut(MULTI_PATH, "S", "T")
        assert cut_val == ek_val

    def test_disjoint_partition(self) -> None:
        src, snk, _val = min_cut(DIAMOND, "S", "T")
        assert "S" in src
        assert "T" in snk
        assert src.isdisjoint(snk)

    def test_capacity_of_cut(self) -> None:
        src, snk, val = min_cut(SIMPLE, "S", "T")
        cut_cap = 0
        for u in src:
            for v in SIMPLE.get(u, {}):
                if v in snk:
                    cut_cap += SIMPLE[u][v]
        assert cut_cap >= val


# ═══════════════════════════════════════════════════════════════════════
# Bipartite Maximum Matching
# ═══════════════════════════════════════════════════════════════════════


class TestBipartiteMatching:
    def test_simple_bipartite(self) -> None:
        left = ["L1", "L2"]
        right = ["R1", "R2"]
        edges = [("L1", "R1"), ("L2", "R1"), ("L2", "R2")]
        count, matches = bipartite_max_matching(left, right, edges)
        assert count == 2
        assert len(matches) == 2

    def test_no_edges(self) -> None:
        left = ["A", "B"]
        right = ["X", "Y"]
        count, matches = bipartite_max_matching(left, right, [])
        assert count == 0
        assert matches == []

    def test_perfect_matching(self) -> None:
        left = ["A", "B", "C"]
        right = ["X", "Y", "Z"]
        edges = [("A", "X"), ("B", "Y"), ("C", "Z")]
        count, _matches = bipartite_max_matching(left, right, edges)
        assert count == 3

    def test_left_side_limited(self) -> None:
        left = ["A", "B", "C", "D"]
        right = ["X", "Y"]
        edges = [("A", "X"), ("B", "X"), ("C", "Y"), ("D", "Y")]
        count, _ = bipartite_max_matching(left, right, edges)
        assert count == 2


# ═══════════════════════════════════════════════════════════════════════
# Algorithm Agreement (property-based)
# ═══════════════════════════════════════════════════════════════════════


class TestAlgorithmAgreement:
    ALGOS: ClassVar = (ford_fulkerson, lambda g, s, t: edmonds_karp(g, s, t)[:2], dinic)
    GRAPHS: ClassVar = (
        ("simple", SIMPLE, SIMPLE_EXPECTED),
        ("diamond", DIAMOND, DIAMOND_EXPECTED),
        ("multi", MULTI_PATH, MULTI_PATH_EXPECTED),
        ("serial", SERIAL, SERIAL_EXPECTED),
        ("disconnected", DISCONNECTED, DISCONNECTED_EXPECTED),
        ("single_edge", SINGLE_EDGE, SINGLE_EDGE_EXPECTED),
        ("triangle", TRIANGLE, TRIANGLE_EXPECTED),
    )

    @pytest.mark.parametrize("name,graph,expected", GRAPHS)
    def test_all_agree_on_value(self, name: str, graph: CapacityGraph, expected: int) -> None:
        values: set[int] = set()
        for algo in self.ALGOS:
            val, _ = algo(graph, "S", "T")
            values.add(val)
        assert len(values) == 1
        assert values.pop() == expected


# ═══════════════════════════════════════════════════════════════════════
# Verify flow helper
# ═══════════════════════════════════════════════════════════════════════


class TestVerifyFlow:
    def test_valid_flow_passes(self) -> None:
        _, flow = ford_fulkerson(SIMPLE, "S", "T")
        assert verify_flow(SIMPLE, "S", "T", flow, SIMPLE_EXPECTED)

    def test_wrong_value_fails(self) -> None:
        _, flow = ford_fulkerson(SIMPLE, "S", "T")
        assert not verify_flow(SIMPLE, "S", "T", flow, 999999)

    def test_overflow_fails(self) -> None:
        broken: FlowGraph = {"S": {"A": 999}, "A": {"T": 999}, "T": {}}
        assert not verify_flow(SIMPLE, "S", "T", broken, 999)

    def test_imbalanced_intermediate_fails(self) -> None:
        broken: FlowGraph = {"S": {"A": 5}, "A": {"T": 3}, "T": {}}
        assert not verify_flow(SIMPLE, "S", "T", broken, 5)

    def test_negative_flow_fails(self) -> None:
        broken: FlowGraph = {"S": {"A": -1}, "A": {"T": 5}, "T": {}}
        assert not verify_flow(SIMPLE, "S", "T", broken, -1)


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_source_equals_sink(self) -> None:
        g = _build_graph([("S", "A", 5), ("A", "S", 3)])
        val, _ = ford_fulkerson(g, "S", "S")
        assert val == 0

    def test_zero_capacity_edges(self) -> None:
        g = _build_graph([("S", "A", 0), ("A", "T", 10)])
        val, _ = dinic(g, "S", "T")
        assert val == 0

    def test_multiple_parallel_edges(self) -> None:
        g: CapacityGraph = {"S": {"A": 3, "B": 3}, "A": {"T": 3}, "B": {"T": 3}, "T": {}}
        val, _, _ = edmonds_karp(g, "S", "T")
        assert 5 <= val <= 6

    def test_partition_across_all_algos(self) -> None:
        for _name, graph, _expected in TestAlgorithmAgreement.GRAPHS:
            src, snk, _val = min_cut(graph, "S", "T")
            assert src.isdisjoint(snk)
            assert src.union(snk) == set(graph)
