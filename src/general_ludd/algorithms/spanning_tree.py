"""Minimum spanning tree algorithms: Kruskal, Prim, Boruvka, and a
Steiner-tree heuristic (distance-network / MST-based).

All implementations are pure-Python, stdlib only.
"""

from __future__ import annotations

import heapq
from collections import defaultdict

# ── Union-Find (helper) ────────────────────────────────────────────────


class _UnionFind:
    """Disjoint-set with path compression and union by rank."""

    def __init__(self, elements: int) -> None:
        self.parent = list(range(elements))
        self.rank = [0] * elements
        self.components = elements

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> bool:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.components -= 1
        return True


# ── Kruskal ─────────────────────────────────────────────────────────────


def kruskal(edges: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Return the edges of a minimum spanning forest using Kruskal's algorithm.

    ``edges`` is a list of ``(u, v, weight)`` tuples.  Vertex indices are
    assumed contiguous from 0.  O(|E| log |E|) after sorting.

    Returns the MST edges in ascending weight order.
    """
    if not edges:
        return []
    max_node = max(max(u, v) for u, v, _w in edges)
    uf = _UnionFind(max_node + 1)
    mst: list[tuple[int, int, float]] = []
    for u, v, w in sorted(edges, key=lambda e: e[2]):
        if uf.union(u, v):
            mst.append((u, v, w))
    return mst


def kruskal_total_weight(edges: list[tuple[int, int, float]]) -> float:
    """Return the total weight of the MST computed by Kruskal."""
    return sum(w for _u, _v, w in kruskal(edges))


# ── Prim ────────────────────────────────────────────────────────────────


def prim(edges: list[tuple[int, int, float]], start: int = 0) -> list[tuple[int, int, float]]:
    """Return the edges of a minimum spanning tree using Prim's algorithm.

    ``edges`` is a list of ``(u, v, weight)`` undirected edges.
    Builds an adjacency list internally.  O(|E| log |V|) via a min-heap.

    Returns MST edges in the order they were added to the tree.
    """
    if not edges:
        return []
    adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
    nodes: set[int] = set()
    for u, v, w in edges:
        adj[u].append((v, w))
        adj[v].append((u, w))
        nodes.add(u)
        nodes.add(v)

    if start not in nodes:
        return []

    visited: set[int] = {start}
    mst: list[tuple[int, int, float]] = []
    heap: list[tuple[float, int, int]] = []
    for nb, w in adj[start]:
        heapq.heappush(heap, (w, start, nb))

    while heap and len(mst) < len(nodes) - 1:
        w, u, v = heapq.heappop(heap)
        if v in visited:
            continue
        visited.add(v)
        mst.append((u, v, w))
        for nb, nw in adj[v]:
            if nb not in visited:
                heapq.heappush(heap, (nw, v, nb))

    return mst


def prim_total_weight(edges: list[tuple[int, int, float]], start: int = 0) -> float:
    """Return the total weight of the MST computed by Prim."""
    return sum(w for _u, _v, w in prim(edges, start))


# ── Boruvka ─────────────────────────────────────────────────────────────


def boruvka(edges: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Return the edges of a minimum spanning forest using Boruvka's algorithm.

    O(|E| log |V|) — each iteration at least halves the number of components.

    Returns MST edges (order is iteration-dependent).
    """
    if not edges:
        return []
    max_node = max(max(u, v) for u, v, _w in edges)
    uf = _UnionFind(max_node + 1)
    mst: list[tuple[int, int, float]] = []

    # Build adjacency per-component cheapest outgoing edge
    while uf.components > 1:
        cheapest: dict[int, tuple[int, int, float]] = {}
        for u, v, w in edges:
            ru, rv = uf.find(u), uf.find(v)
            if ru == rv:
                continue
            if ru not in cheapest or w < cheapest[ru][2]:
                cheapest[ru] = (u, v, w)
            if rv not in cheapest or w < cheapest[rv][2]:
                cheapest[rv] = (v, u, w)

        if not cheapest:
            break

        added = False
        for _comp, (u, v, w) in cheapest.items():
            if uf.union(u, v):
                mst.append((u, v, w))
                added = True
        if not added:
            break

    return mst


def boruvka_total_weight(edges: list[tuple[int, int, float]]) -> float:
    """Return the total weight of the MST computed by Boruvka."""
    return sum(w for _u, _v, w in boruvka(edges))


# ── Steiner tree heuristic (MST-based) ──────────────────────────────────


def _steiner_mst_heuristic(edges: list[tuple[int, int, float]], terminals: set[int]) -> list[tuple[int, int, float]]:
    """Build a Steiner tree via the distance-network heuristic.

    1. Compute all-pairs shortest paths between terminals (Floyd-Warshall).
    2. Build a complete graph on terminals where edge weight = shortest-path dist.
    3. Run Kruskal on the terminal graph → core Steiner edges.
    4. Expand each core edge back to its shortest-path route.
    5. Combine all expanded edges and run Kruskal to remove cycles.

    This is a 2-approximation for metric Steiner tree.
    """
    if len(terminals) <= 1:
        return []

    nodes: set[int] = set()
    for u, v, _w in edges:
        nodes.add(u)
        nodes.add(v)
    n = max(nodes) + 1

    # Floyd-Warshall all-pairs shortest paths
    INF = float("inf")
    dist = [[INF] * n for _ in range(n)]
    nxt: list[list[int | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        dist[i][i] = 0
    for u, v, w in edges:
        if w < dist[u][v]:
            dist[u][v] = dist[v][u] = w
            nxt[u][v] = v
            nxt[v][u] = u

    for k in range(n):
        for i in range(n):
            if dist[i][k] == INF:
                continue
            dik = dist[i][k]
            for j in range(n):
                nd = dik + dist[k][j]
                if nd < dist[i][j]:
                    dist[i][j] = nd
                    nxt[i][j] = nxt[i][k]

    # Build complete graph on terminals
    term_list = sorted(terminals)
    complete_edges: list[tuple[int, int, float]] = []
    for i in range(len(term_list)):
        for j in range(i + 1, len(term_list)):
            ti, tj = term_list[i], term_list[j]
            if dist[ti][tj] != INF:
                complete_edges.append((ti, tj, dist[ti][tj]))

    if not complete_edges:
        return []

    # Kruskal on the terminal complete graph
    steiner_core = kruskal(complete_edges)

    # Expand core edges to shortest-path routes
    raw_edges: list[tuple[int, int, float]] = []
    for u, v, _w in steiner_core:
        cur = u
        while cur != v:
            nxt_node = nxt[cur][v]
            assert nxt_node is not None
            w = dist[cur][nxt_node]
            raw_edges.append((cur, nxt_node, w))
            cur = nxt_node

    # Deduplicate and run Kruskal to remove cycles
    return kruskal(raw_edges)


def steiner_tree(edges: list[tuple[int, int, float]], terminals: set[int]) -> list[tuple[int, int, float]]:
    """Return the edges of an approximate Steiner tree connecting *terminals*.

    Uses the distance-network (MST-based) 2-approximation.
    Returns edges ``(u, v, weight)`` sorted by weight.
    """
    return sorted(_steiner_mst_heuristic(edges, terminals), key=lambda e: e[2])


def steiner_total_weight(edges: list[tuple[int, int, float]], terminals: set[int]) -> float:
    """Return the total weight of the approximate Steiner tree."""
    return sum(w for _u, _v, w in steiner_tree(edges, terminals))
