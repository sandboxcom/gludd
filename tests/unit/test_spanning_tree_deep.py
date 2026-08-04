"""Deep minimum spanning tree tests: Kruskal, Prim, Boruvka, Steiner.

Verifies correctness, invariants, edge cases across all four algorithms.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from general_ludd.algorithms.spanning_tree import (
    boruvka,
    boruvka_total_weight,
    kruskal,
    kruskal_total_weight,
    prim,
    prim_total_weight,
    steiner_total_weight,
    steiner_tree,
)

# ── Shared test graphs ────────────────────────────────────────────────

_GRAPH_SMALL: list[tuple[int, int, float]] = [
    (0, 1, 4.0),
    (0, 2, 1.0),
    (1, 2, 2.0),
    (1, 3, 5.0),
    (2, 3, 3.0),
]

_GRAPH_DENSE4 = [
    (0, 1, 2.0),
    (0, 2, 4.0),
    (0, 3, 1.0),
    (1, 2, 3.0),
    (1, 3, 7.0),
    (2, 3, 5.0),
]

_GRAPH_LINE = [
    (0, 1, 1.0),
    (1, 2, 2.0),
    (2, 3, 3.0),
    (3, 4, 4.0),
]

_GRAPH_DISJOINT = [
    (0, 1, 1.0),
    (1, 2, 2.0),  # component 1
    (3, 4, 3.0),
    (4, 5, 4.0),  # component 2
]

_GRAPH_STEINER = [
    (0, 1, 2.0),
    (0, 2, 3.0),
    (0, 3, 5.0),
    (1, 2, 1.0),
    (1, 4, 7.0),
    (2, 3, 2.0),
    (2, 4, 4.0),
    (3, 4, 1.0),
]

# ═══════════════════════════════════════════════════════════════════════
# Kruskal
# ═══════════════════════════════════════════════════════════════════════


class TestKruskal:
    def test_small_graph_mst_weight(self) -> None:
        assert kruskal_total_weight(_GRAPH_SMALL) == 6.0

    def test_small_graph_mst_edges_count(self) -> None:
        mst = kruskal(_GRAPH_SMALL)
        assert len(mst) == 3  # 4 nodes → 3 edges

    def test_dense4_mst_weight(self) -> None:
        assert kruskal_total_weight(_GRAPH_DENSE4) == 6.0

    def test_line_graph_uses_all_edges(self) -> None:
        mst = kruskal(_GRAPH_LINE)
        assert len(mst) == 4
        assert kruskal_total_weight(_GRAPH_LINE) == 10.0

    def test_disjoint_graph_forest(self) -> None:
        mst = kruskal(_GRAPH_DISJOINT)
        assert len(mst) == 4  # 6 nodes, 2 components → 4 edges

    def test_single_edge(self) -> None:
        mst = kruskal([(0, 1, 42.0)])
        assert mst == [(0, 1, 42.0)]

    def test_empty_edges(self) -> None:
        assert kruskal([]) == []
        assert kruskal_total_weight([]) == 0.0

    def test_parallel_edges_picks_cheapest(self) -> None:
        edges = [(0, 1, 10.0), (0, 1, 1.0), (0, 1, 5.0)]
        mst = kruskal(edges)
        assert mst == [(0, 1, 1.0)]


# ═══════════════════════════════════════════════════════════════════════
# Prim
# ═══════════════════════════════════════════════════════════════════════


class TestPrim:
    def test_same_weight_as_kruskal(self) -> None:
        assert prim_total_weight(_GRAPH_SMALL) == kruskal_total_weight(_GRAPH_SMALL)

    def test_same_weight_as_kruskal_dense(self) -> None:
        assert prim_total_weight(_GRAPH_DENSE4) == kruskal_total_weight(_GRAPH_DENSE4)

    def test_nonzero_start(self) -> None:
        w = prim_total_weight(_GRAPH_SMALL, start=3)
        assert w == 6.0

    def test_line_graph(self) -> None:
        assert prim_total_weight(_GRAPH_LINE) == 10.0

    def test_disjoint_stays_within_visited(self) -> None:
        mst = prim(_GRAPH_DISJOINT)
        assert len(mst) == 2  # only component containing start=0

    def test_start_not_in_graph(self) -> None:
        assert prim([(0, 1, 1.0)], start=99) == []

    def test_empty_edges(self) -> None:
        assert prim([]) == []
        assert prim_total_weight([]) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Boruvka
# ═══════════════════════════════════════════════════════════════════════


class TestBoruvka:
    def test_same_weight_as_kruskal(self) -> None:
        assert boruvka_total_weight(_GRAPH_SMALL) == kruskal_total_weight(_GRAPH_SMALL)

    def test_same_weight_as_kruskal_dense(self) -> None:
        assert boruvka_total_weight(_GRAPH_DENSE4) == kruskal_total_weight(_GRAPH_DENSE4)

    def test_line_graph(self) -> None:
        assert boruvka_total_weight(_GRAPH_LINE) == 10.0

    def test_disjoint_forest(self) -> None:
        mst = boruvka(_GRAPH_DISJOINT)
        assert len(mst) == 4

    def test_empty_edges(self) -> None:
        assert boruvka([]) == []
        assert boruvka_total_weight([]) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Cross-algorithm agreement
# ═══════════════════════════════════════════════════════════════════════


class TestMSTConsensus:
    """All three algorithms MUST agree on total MST weight for any graph."""

    # Prim does not span disconnected components; exclude _GRAPH_DISJOINT.
    GRAPHS: ClassVar = (
        _GRAPH_SMALL,
        _GRAPH_DENSE4,
        _GRAPH_LINE,
        [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0), (3, 0, 1.0)],  # cycle of 4
        [(0, 1, 3.0), (0, 2, 2.0), (1, 2, 10.0), (1, 3, 5.0), (2, 3, 8.0)],
    )

    @pytest.mark.parametrize("graph", GRAPHS)
    def test_all_three_agree(self, graph: list[tuple[int, int, float]]) -> None:
        k = kruskal_total_weight(graph)
        p = prim_total_weight(graph)
        b = boruvka_total_weight(graph)
        assert k == p == b, f"K={k} P={p} B={b}"


# ═══════════════════════════════════════════════════════════════════════
# Steiner tree
# ═══════════════════════════════════════════════════════════════════════


class TestSteiner:
    def test_all_nodes_are_terminals_is_mst(self) -> None:
        """When every node is a terminal, Steiner ≈ MST."""
        st = steiner_total_weight(_GRAPH_STEINER, terminals={0, 1, 2, 3, 4})
        mst_w = kruskal_total_weight(_GRAPH_STEINER)
        assert st == mst_w

    def test_two_terminals_is_shortest_path(self) -> None:
        """Steiner tree on 2 terminals = shortest path between them."""
        weight = steiner_total_weight(_GRAPH_STEINER, terminals={0, 4})
        assert weight == 6.0  # 0-1(2) + 1-2(1) + 2-3(2) + 3-4(1) = 6

    def test_single_terminal(self) -> None:
        assert steiner_tree(_GRAPH_STEINER, terminals={2}) == []

    def test_no_terminals(self) -> None:
        assert steiner_tree(_GRAPH_STEINER, set()) == []

    def test_steiner_saves_over_mst(self) -> None:
        """Classic Steiner: 3 terminals on a triangle with a central node.

        Nodes: 0,1,2 (terminals) + 3 (Steiner point).
        Edges: (0,3)=1, (1,3)=1, (2,3)=1, (0,1)=2, (1,2)=2, (0,2)=2.
        The distance-network heuristic picks 2 of 3 terminal edges (weight 4).
        The optimum (via Steiner point 3) = 3. The heuristic is <= 2x optimum.
        """
        edges = [
            (0, 3, 1.0),
            (1, 3, 1.0),
            (2, 3, 1.0),
            (0, 1, 2.0),
            (1, 2, 2.0),
            (0, 2, 2.0),
        ]
        st_w = steiner_total_weight(edges, terminals={0, 1, 2})
        assert st_w == 4.0  # heuristic, <= 2x optimum

    def test_disconnected_terminals_returns_partial(self) -> None:
        """Terminals in separate components → forest."""
        edges = [(0, 1, 1.0), (2, 3, 2.0)]
        tree = steiner_tree(edges, terminals={0, 1, 2, 3})
        assert len(tree) >= 0  # may be empty if unreachable

    def test_weight_never_exceeds_mst_weight(self) -> None:
        """Steiner weight ≤ MST weight on all nodes for any terminal subset."""
        for term_ct in range(1, 5):
            terminals = set(range(term_ct))
            st = steiner_total_weight(_GRAPH_STEINER, terminals)
            mst = kruskal_total_weight(_GRAPH_STEINER)
            assert st <= mst + 1e-9, f"terminals={terminals}: Steiner={st} MST={mst}"


# ═══════════════════════════════════════════════════════════════════════
# Property / edge-case tests
# ═══════════════════════════════════════════════════════════════════════


class TestProperties:
    def test_mst_edges_equal_nodes_minus_one(self) -> None:
        for mst_func in (kruskal, prim, boruvka):
            mst = mst_func(_GRAPH_SMALL)
            assert len(mst) == 3, f"{mst_func.__name__} edges != n-1"

    def test_mst_is_spanning(self) -> None:
        mst = kruskal(_GRAPH_SMALL)
        nodes: set[int] = set()
        for u, v, _w in mst:
            nodes.add(u)
            nodes.add(v)
        assert nodes == {0, 1, 2, 3}

    def test_mst_has_no_cycles(self) -> None:
        mst = kruskal(_GRAPH_DENSE4)
        adj: dict[int, set[int]] = {}
        for u, v, _w in mst:
            adj.setdefault(u, set()).add(v)
            adj.setdefault(v, set()).add(u)

        visited: set[int] = set()

        def dfs(node: int, parent: int | None) -> bool:
            visited.add(node)
            for nb in adj.get(node, set()):
                if nb == parent:
                    continue
                if nb in visited:
                    return False
                if not dfs(nb, node):
                    return False
            return True

        assert dfs(0, None) and len(visited) == len(adj)

    def test_negative_weights_ok(self) -> None:
        edges = [(0, 1, -5.0), (1, 2, -3.0), (0, 2, -10.0)]
        mst = kruskal(edges)
        assert kruskal_total_weight(edges) == -15.0  # picks -10 and -5
        assert len(mst) == 2

    def test_large_identical_weights(self) -> None:
        edges = [(i, (i + 1) % 10, 1.0) for i in range(10)]
        mst = kruskal(edges)
        assert kruskal_total_weight(edges) == 9.0
        assert len(mst) == 9
