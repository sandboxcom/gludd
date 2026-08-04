"""Deep graph algorithm tests: BFS/DFS traversal, topological sort,
Dijkstra shortest path, cycle detection, connected components.

Each algorithm is pure-Python (stdlib only) — no external graph library.
"""

from __future__ import annotations

from collections import deque

import pytest

# ── Graph types ──────────────────────────────────────────────────────

Graph = dict[str, set[str]]
WeightedGraph = dict[str, dict[str, int]]


# ── BFS ──────────────────────────────────────────────────────────────


def bfs(graph: Graph, start: str) -> list[str]:
    visited: set[str] = set()
    order: list[str] = []
    queue: deque[str] = deque([start])
    visited.add(start)
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph.get(node, set()) - visited):
            visited.add(neighbor)
            queue.append(neighbor)
    return order


def bfs_distances(graph: Graph, start: str) -> dict[str, int]:
    dist: dict[str, int] = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor not in dist:
                dist[neighbor] = dist[node] + 1
                queue.append(neighbor)
    return dist


# ── DFS ──────────────────────────────────────────────────────────────


def dfs_iterative(graph: Graph, start: str) -> list[str]:
    visited: set[str] = set()
    order: list[str] = []
    stack: list[str] = [start]
    while stack:
        node = stack.pop()
        if node not in visited:
            visited.add(node)
            order.append(node)
            for neighbor in sorted(graph.get(node, set()), reverse=True):
                if neighbor not in visited:
                    stack.append(neighbor)
    return order


def dfs_recursive(graph: Graph, start: str) -> list[str]:
    visited: set[str] = set()
    order: list[str] = []

    def _dfs(node: str) -> None:
        visited.add(node)
        order.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                _dfs(neighbor)

    _dfs(start)
    return order


# ── Topological sort (Kahn's algorithm) ──────────────────────────────


def topological_sort(graph: Graph) -> list[str]:
    in_degree: dict[str, int] = {node: 0 for node in graph}
    for node in graph:
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, set()):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(graph):
        raise ValueError("Graph contains a cycle; topological sort impossible")
    return order


# ── Cycle detection ──────────────────────────────────────────────────


def has_cycle(graph: Graph) -> bool:
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def _dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                if _dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(node)
        return False

    return any(_dfs(node) for node in graph if node not in visited)


def find_cycle(graph: Graph) -> list[str] | None:
    visited: set[str] = set()
    rec_stack: set[str] = set()
    path: list[str] = []

    def _dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                if _dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                cycle_start = path.index(neighbor)
                path.append(neighbor)
                nonlocal_cycle[:] = path[cycle_start:]
                return True
        path.pop()
        rec_stack.discard(node)
        return False

    nonlocal_cycle: list[str] = []
    for node in graph:
        if node not in visited and _dfs(node):
            return nonlocal_cycle
    return None


# ── Dijkstra ─────────────────────────────────────────────────────────


def dijkstra(graph: WeightedGraph, start: str) -> dict[str, int]:
    import heapq

    dist: dict[str, int] = {start: 0}
    pq: list[tuple[int, str]] = [(0, start)]

    while pq:
        cur_dist, node = heapq.heappop(pq)
        if cur_dist > dist.get(node, float("inf")):
            continue
        for neighbor, weight in graph.get(node, {}).items():
            new_dist = cur_dist + weight
            if new_dist < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_dist
                heapq.heappush(pq, (new_dist, neighbor))

    return dist


def dijkstra_path(graph: WeightedGraph, start: str, end: str) -> tuple[list[str], int]:
    import heapq

    dist: dict[str, int] = {start: 0}
    prev: dict[str, str | None] = {start: None}
    pq: list[tuple[int, str]] = [(0, start)]

    while pq:
        cur_dist, node = heapq.heappop(pq)
        if cur_dist > dist.get(node, float("inf")):
            continue
        if node == end:
            break
        for neighbor, weight in graph.get(node, {}).items():
            new_dist = cur_dist + weight
            if new_dist < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_dist
                prev[neighbor] = node
                heapq.heappush(pq, (new_dist, neighbor))

    if end not in prev:
        return [], -1

    path: list[str] = []
    cur: str | None = end
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return path, dist[end]


# ── Connected components (undirected) ────────────────────────────────


def connected_components(graph: Graph) -> list[list[str]]:
    visited: set[str] = set()
    components: list[list[str]] = []

    for node in sorted(graph):
        if node in visited:
            continue
        component: list[str] = []
        stack = [node]
        while stack:
            v = stack.pop()
            if v not in visited:
                visited.add(v)
                component.append(v)
                stack.extend(sorted(graph.get(v, set()), reverse=True))
        components.append(sorted(component))

    return components


# ── Utilities ────────────────────────────────────────────────────────


def _undirected(edges: list[tuple[str, str]]) -> Graph:
    g: Graph = {}
    for u, v in edges:
        g.setdefault(u, set()).add(v)
        g.setdefault(v, set()).add(u)
    return g


def _directed(edges: list[tuple[str, str]]) -> Graph:
    g: Graph = {}
    for u, v in edges:
        g.setdefault(u, set()).add(v)
        g.setdefault(v, set())
    return g


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestBFS:
    def test_simple_tree(self) -> None:
        g = _undirected([("A", "B"), ("A", "C"), ("B", "D"), ("B", "E")])
        assert bfs(g, "A") == ["A", "B", "C", "D", "E"]

    def test_disconnected_graph(self) -> None:
        g = _undirected([("A", "B"), ("C", "D")])
        result = bfs(g, "A")
        assert result == ["A", "B"]

    def test_distances(self) -> None:
        g = _undirected([("A", "B"), ("B", "C"), ("A", "D")])
        dist = bfs_distances(g, "A")
        assert dist == {"A": 0, "B": 1, "D": 1, "C": 2}


class TestDFS:
    def test_iterative_linear(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "D")])
        result = dfs_iterative(g, "A")
        assert result[0] == "A"
        assert set(result) == {"A", "B", "C", "D"}

    def test_recursive_branching(self) -> None:
        g = _undirected([("A", "B"), ("A", "C"), ("B", "D"), ("C", "E")])
        result = dfs_recursive(g, "A")
        assert result[0] == "A"
        assert len(result) == 5

    def test_iterative_and_recursive_same_set(self) -> None:
        g = _undirected([("X", "Y"), ("Y", "Z"), ("X", "W")])
        it = set(dfs_iterative(g, "X"))
        rec = set(dfs_recursive(g, "X"))
        assert it == rec == {"X", "Y", "Z", "W"}


class TestTopologicalSort:
    def test_dag(self) -> None:
        g = _directed([("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
        order = topological_sort(g)
        for u, v in [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]:
            assert order.index(u) < order.index(v)

    def test_linear_chain(self) -> None:
        g = _directed([("1", "2"), ("2", "3"), ("3", "4")])
        assert topological_sort(g) == ["1", "2", "3", "4"]

    def test_cycle_raises(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        with pytest.raises(ValueError, match="cycle"):
            topological_sort(g)


class TestCycleDetection:
    def test_no_cycle_dag(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("A", "C")])
        assert not has_cycle(g)

    def test_simple_cycle(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A")])
        assert has_cycle(g)

    def test_self_loop(self) -> None:
        g: Graph = {"A": {"A"}}
        assert has_cycle(g)

    def test_find_cycle_returns_path(self) -> None:
        g = _directed([("A", "B"), ("B", "C"), ("C", "A"), ("C", "D")])
        cycle = find_cycle(g)
        assert cycle is not None
        assert cycle[0] == cycle[-1]
        assert len(cycle) == 4


class TestDijkstra:
    def test_shortest_path_simple(self) -> None:
        g: WeightedGraph = {"A": {"B": 1, "C": 4}, "B": {"C": 2, "D": 5}, "C": {"D": 1}, "D": {}}
        dist = dijkstra(g, "A")
        assert dist == {"A": 0, "B": 1, "C": 3, "D": 4}

    def test_path_retrieval(self) -> None:
        g: WeightedGraph = {"A": {"B": 2, "C": 6}, "B": {"C": 3, "D": 1}, "C": {"D": 1}, "D": {}}
        path, cost = dijkstra_path(g, "A", "D")
        assert cost == 3
        assert path == ["A", "B", "D"]

    def test_unreachable_node(self) -> None:
        g: WeightedGraph = {"A": {"B": 1}, "B": {}, "C": {}}
        path, cost = dijkstra_path(g, "A", "C")
        assert cost == -1
        assert path == []


class TestConnectedComponents:
    def test_single_component(self) -> None:
        g = _undirected([("A", "B"), ("B", "C"), ("A", "C")])
        comps = connected_components(g)
        assert comps == [["A", "B", "C"]]

    def test_two_disconnected(self) -> None:
        g = _undirected([("A", "B"), ("C", "D"), ("C", "E")])
        comps = connected_components(g)
        assert comps == [["A", "B"], ["C", "D", "E"]]

    def test_isolated_nodes(self) -> None:
        g: Graph = {"A": set(), "B": set(), "C": {"D"}, "D": {"C"}}
        comps = connected_components(g)
        assert sorted(tuple(c) for c in comps) == [("A",), ("B",), ("C", "D")]


# ═══════════════════════════════════════════════════════════════════════
# Edge-case / property tests
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_bfs_single_node(self) -> None:
        assert bfs({"A": set()}, "A") == ["A"]

    def test_dfs_single_node(self) -> None:
        assert dfs_iterative({"A": set()}, "A") == ["A"]

    def test_topo_sort_single_node(self) -> None:
        assert topological_sort({"A": set()}) == ["A"]

    def test_dijkstra_start_to_self(self) -> None:
        g: WeightedGraph = {"A": {"B": 1}, "B": {}}
        assert dijkstra(g, "A")["A"] == 0

    def test_empty_graph_components(self) -> None:
        assert connected_components({}) == []
