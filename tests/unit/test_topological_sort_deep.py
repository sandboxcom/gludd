"""Deep topological sort tests: Kahn, DFS-based, cycle detection,
multiple valid orders, disconnected components.

Each algorithm is pure-Python (stdlib only).  The implementations below are
the subjects under test — they mirror the patterns used in
``src/general_ludd/scheduling/scheduler.py`` and in the existing
``tests/unit/test_graph_algo_deep.py``.
"""

from __future__ import annotations

from collections import deque

import pytest

Graph = dict[str, set[str]]


# ── Helpers ───────────────────────────────────────────────────────────


def _directed(edges: list[tuple[str, str]]) -> Graph:
    g: Graph = {}
    for u, v in edges:
        g.setdefault(u, set()).add(v)
        g.setdefault(v, set())
    return g


def _edges_obey_order(order: list[str], edges: list[tuple[str, str]]) -> bool:
    """True when every (u,v) edge has u before v in *order*."""
    idx = {n: i for i, n in enumerate(order)}
    return all(idx[u] < idx[v] for u, v in edges if u in idx and v in idx)


# ── Kahn's algorithm ──────────────────────────────────────────────────


def kahn_sort(graph: Graph) -> list[str]:
    in_degree: dict[str, int] = {node: 0 for node in graph}
    for node in graph:
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1

    ready: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    order: list[str] = []

    while ready:
        node = ready.popleft()
        order.append(node)
        for neighbor in graph.get(node, set()):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                ready.append(neighbor)

    if len(order) != len(graph):
        cyclic = {n for n in graph} - set(order)
        raise ValueError(f"Graph contains a cycle; topological sort impossible (nodes: {sorted(cyclic)})")
    return order


def kahn_has_cycle(graph: Graph) -> bool:
    try:
        kahn_sort(graph)
    except ValueError:
        return True
    return False


# ── DFS-based topological sort ────────────────────────────────────────


def dfs_topo_sort(graph: Graph) -> list[str]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    order: deque[str] = deque()

    def _visit(node: str) -> None:
        color[node] = GRAY
        for neighbor in graph.get(node, set()):
            if color.get(neighbor, WHITE) == GRAY:
                raise ValueError(f"Graph contains a cycle involving {node!r} → {neighbor!r}")
            if color.get(neighbor, WHITE) == WHITE:
                _visit(neighbor)
        color[node] = BLACK
        order.appendleft(node)

    for node in graph:
        if color[node] == WHITE:
            _visit(node)

    return list(order)


def dfs_has_cycle(graph: Graph) -> bool:
    try:
        dfs_topo_sort(graph)
    except ValueError:
        return True
    return False


# ── All valid topological orders (backtracking) ───────────────────────


def all_topo_orders(graph: Graph) -> list[list[str]]:
    in_degree: dict[str, int] = {node: 0 for node in graph}
    for node in graph:
        for neighbor in graph[node]:
            in_degree[neighbor] += 1

    results: list[list[str]] = []

    def _backtrack(path: list[str], indeg: dict[str, int]) -> None:
        if len(path) == len(graph):
            results.append(list(path))
            return
        ready = sorted(n for n, d in indeg.items() if d == 0 and n not in path)
        for node in ready:
            path.append(node)
            for neighbor in graph.get(node, set()):
                indeg[neighbor] -= 1
            _backtrack(path, indeg)
            path.pop()
            for neighbor in graph.get(node, set()):
                indeg[neighbor] += 1

    _backtrack([], dict(in_degree))
    return results


def count_topo_orders(graph: Graph) -> int:
    return len(all_topo_orders(graph))


# ═══════════════════════════════════════════════════════════════════════
# Kahn's algorithm tests
# ═══════════════════════════════════════════════════════════════════════


class TestKahnSort:
    def test_simple_dag(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)

    def test_linear_chain(self) -> None:
        g = _directed([("1", "2"), ("2", "3"), ("3", "4")])
        assert kahn_sort(g) == ["1", "2", "3", "4"]

    def test_diamond_dag(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E")]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert len(order) == 5

    def test_multiple_sources(self) -> None:
        edges = [("A", "C"), ("B", "C"), ("C", "D"), ("C", "E")]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("C")

    def test_single_node(self) -> None:
        assert kahn_sort({"X": set()}) == ["X"]

    def test_empty_graph(self) -> None:
        assert kahn_sort({}) == []

    def test_all_independent(self) -> None:
        g: Graph = {"A": set(), "B": set(), "C": set()}
        order = kahn_sort(g)
        assert set(order) == {"A", "B", "C"}

    def test_branching_fan_out(self) -> None:
        edges = [("root", "a"), ("root", "b"), ("root", "c"), ("a", "a1"), ("b", "b1"), ("c", "c1")]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert order[0] == "root"

    def test_complex_dag(self) -> None:
        edges = [
            ("A", "B"),
            ("A", "C"),
            ("B", "D"),
            ("C", "D"),
            ("D", "E"),
            ("E", "F"),
            ("C", "G"),
            ("G", "F"),
            ("H", "I"),
            ("I", "J"),
            ("J", "F"),
        ]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert len(order) == 10


# ── Kahn cycle detection ─────────────────────────────────────────────


class TestKahnCycleDetection:
    def test_simple_cycle_raises(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        with pytest.raises(ValueError, match="cycle"):
            kahn_sort(g)

    def test_self_loop_raises(self) -> None:
        g: Graph = {"A": {"A"}}
        with pytest.raises(ValueError, match="cycle"):
            kahn_sort(g)

    def test_two_node_cycle(self) -> None:
        g = _directed([("X", "Y"), ("Y", "X")])
        with pytest.raises(ValueError, match="cycle"):
            kahn_sort(g)

    def test_embed_cycle_in_dag(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "D"), ("D", "B")])
        with pytest.raises(ValueError, match="cycle"):
            kahn_sort(g)

    def test_kahn_has_cycle_true(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        assert kahn_has_cycle(g) is True

    def test_kahn_has_cycle_false(self) -> None:
        g = _directed([("A", "B"), ("B", "C")])
        assert kahn_has_cycle(g) is False


# ═══════════════════════════════════════════════════════════════════════
# DFS-based topological sort tests
# ═══════════════════════════════════════════════════════════════════════


class TestDFSBasedTopoSort:
    def test_simple_dag(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        order = dfs_topo_sort(_directed(edges))
        assert _edges_obey_order(order, edges)

    def test_linear_chain(self) -> None:
        g = _directed([("1", "2"), ("2", "3"), ("3", "4")])
        order = dfs_topo_sort(g)
        assert _edges_obey_order(order, [("1", "2"), ("2", "3"), ("3", "4")])
        assert len(order) == 4

    def test_kahn_and_dfs_agree_on_dag(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        g = _directed(edges)
        kahn_order = kahn_sort(g)
        dfs_order = dfs_topo_sort(g)
        assert _edges_obey_order(kahn_order, edges)
        assert _edges_obey_order(dfs_order, edges)

    def test_dfs_cycle_raises(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        with pytest.raises(ValueError, match="cycle"):
            dfs_topo_sort(g)

    def test_dfs_has_cycle_true(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        assert dfs_has_cycle(g) is True

    def test_dfs_has_cycle_false(self) -> None:
        g = _directed([("A", "B"), ("B", "C")])
        assert dfs_has_cycle(g) is False

    def test_dfs_single_node(self) -> None:
        assert dfs_topo_sort({"X": set()}) == ["X"]

    def test_dfs_empty_graph(self) -> None:
        assert dfs_topo_sort({}) == []


# ═══════════════════════════════════════════════════════════════════════
# Multiple valid orders
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleValidOrders:
    def test_diamond_two_valid_paths(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        orders = all_topo_orders(_directed(edges))
        assert len(orders) == 2
        for order in orders:
            assert _edges_obey_order(order, edges)

    def test_disconnected_dag_multiple_orders(self) -> None:
        edges = [("A", "B"), ("C", "D")]
        orders = all_topo_orders(_directed(edges))
        assert len(orders) == 6  # A-B, C-D and C-D, A-B with mix
        for order in orders:
            assert _edges_obey_order(order, edges)

    def test_three_independent_chains(self) -> None:
        edges = [("A1", "A2"), ("B1", "B2"), ("C1", "C2")]
        orders = all_topo_orders(_directed(edges))
        assert len(orders) == 90  # 6! / (2! * 2! * 2!)
        for order in orders:
            assert _edges_obey_order(order, edges)

    def test_count_single_node(self) -> None:
        assert count_topo_orders({"X": set()}) == 1

    def test_count_empty(self) -> None:
        assert count_topo_orders({}) == 1  # single empty ordering

    def test_kahn_result_is_among_valid_orders(self) -> None:
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        g = _directed(edges)
        valid = all_topo_orders(g)
        assert kahn_sort(g) in valid


# ═══════════════════════════════════════════════════════════════════════
# Disconnected components
# ═══════════════════════════════════════════════════════════════════════


class TestDisconnectedComponents:
    def test_two_disconnected_dags(self) -> None:
        edges = [("A", "B"), ("C", "D")]
        order = kahn_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert set(order) == {"A", "B", "C", "D"}

    def test_disconnected_with_isolated_nodes(self) -> None:
        g: Graph = {"A": {"B"}, "B": set(), "C": set(), "D": {"E"}, "E": set()}
        order = kahn_sort(g)
        assert _edges_obey_order(order, [("A", "B"), ("D", "E")])
        assert set(order) == {"A", "B", "C", "D", "E"}

    def test_one_component_cyclic_other_dag(self) -> None:
        g: Graph = {"A": {"B"}, "B": {"A"}, "C": {"D"}, "D": set()}
        with pytest.raises(ValueError, match="cycle"):
            kahn_sort(g)

    def test_dfs_disconnected_dag(self) -> None:
        edges = [("X", "Y"), ("P", "Q")]
        order = dfs_topo_sort(_directed(edges))
        assert _edges_obey_order(order, edges)
        assert set(order) == {"X", "Y", "P", "Q"}

    def test_many_isolated_nodes(self) -> None:
        g: Graph = {str(i): set() for i in range(100)}
        order = kahn_sort(g)
        assert len(order) == 100
        assert set(order) == {str(i) for i in range(100)}

    def test_disconnected_dfs_cycle_in_one_component(self) -> None:
        g: Graph = {"A": {"B"}, "B": {"C"}, "C": {"A"}, "D": {"E"}, "E": set()}
        with pytest.raises(ValueError, match="cycle"):
            dfs_topo_sort(g)


# ═══════════════════════════════════════════════════════════════════════
# Property / edge-case tests
# ═══════════════════════════════════════════════════════════════════════


class TestTopoSortProperties:
    def test_same_node_set_kahn_dfs(self) -> None:
        edges = [("A", "B"), ("B", "C"), ("A", "D"), ("D", "E"), ("E", "C")]
        g = _directed(edges)
        assert set(kahn_sort(g)) == set(dfs_topo_sort(g))

    def test_stable_output_repeated(self) -> None:
        g = _directed([("A", "B"), ("B", "C")])
        first = kahn_sort(g)
        for _ in range(10):
            assert kahn_sort(g) == first

    def test_no_spurious_nodes(self) -> None:
        edges = [("A", "B"), ("B", "C")]
        order = kahn_sort(_directed(edges))
        assert len(order) == 3
        assert sorted(order) == ["A", "B", "C"]

    def test_transitive_closure_respected(self) -> None:
        edges = [("A", "B"), ("B", "C"), ("C", "D")]
        g = _directed(edges)
        order = kahn_sort(g)
        assert order.index("A") < order.index("D")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")

    def test_bfs_postorder_vs_kahn(self) -> None:
        def _bfs_postorder(graph: Graph, start: str) -> list[str]:
            result: list[str] = []
            visited: set[str] = set()
            queue: deque[str] = deque([start])
            visited.add(start)
            while queue:
                node = queue.popleft()
                result.append(node)
                for neighbor in sorted(graph.get(node, set()) - visited):
                    visited.add(neighbor)
                    queue.append(neighbor)
            return result

        edges = [("A", "B"), ("B", "C")]
        g = _directed(edges)
        bfs_order = _bfs_postorder(g, "A")
        kahn_order = kahn_sort(g)
        assert bfs_order == ["A", "B", "C"]
        assert kahn_order == ["A", "B", "C"]


# ═══════════════════════════════════════════════════════════════════════
# Deep cycle-finding (beyond true/false)
# ═══════════════════════════════════════════════════════════════════════


def kahn_find_cycle_nodes(graph: Graph) -> set[str] | None:
    """Return the set of nodes participating in a cycle, or None."""
    in_degree: dict[str, int] = {node: 0 for node in graph}
    for node in graph:
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1

    ready: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    visited: set[str] = set()

    while ready:
        node = ready.popleft()
        visited.add(node)
        for neighbor in graph.get(node, set()):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                ready.append(neighbor)

    remaining = {n for n in graph} - visited
    return remaining if remaining else None


class TestDeepCycleFinding:
    def test_cycle_nodes_identified(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "D"), ("D", "B")])
        cyclic = kahn_find_cycle_nodes(g)
        assert cyclic == {"B", "C", "D"}

    def test_no_cycle_yields_none(self) -> None:
        g = _directed([("A", "B"), ("B", "C")])
        assert kahn_find_cycle_nodes(g) is None

    def test_self_loop_node_identified(self) -> None:
        g: Graph = {"A": {"A", "B"}, "B": set()}
        cyclic = kahn_find_cycle_nodes(g)
        assert cyclic == {"A", "B"}  # B depends on A; both remain unsorted

    def test_full_graph_cycle(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        cyclic = kahn_find_cycle_nodes(g)
        assert cyclic == {"A", "B", "C"}


# ═══════════════════════════════════════════════════════════════════════
# Large / stress tests
# ═══════════════════════════════════════════════════════════════════════


class TestLargeGraph:
    def test_long_chain(self) -> None:
        g: Graph = {str(i): {str(i + 1)} for i in range(500)}
        g[str(500)] = set()
        order = kahn_sort(g)
        assert order == [str(i) for i in range(501)]

    def test_wide_dag(self) -> None:
        """N independent sources → one sink."""
        n = 200
        g: Graph = {}
        for i in range(n):
            src = f"src_{i}"
            g.setdefault(src, set()).add("sink")
        g["sink"] = set()
        order = kahn_sort(g)
        assert _edges_obey_order(order, [(f"src_{i}", "sink") for i in range(n)])
        assert order[-1] == "sink"
        assert len(order) == n + 1
