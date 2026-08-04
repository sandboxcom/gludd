"""Union-find v2: DSU with path compression, persistent, rollback, dynamic connectivity, offline queries.

Pure-Python, stdlib only. 0-indexed.
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


# ── Standard DSU (path compression + union by rank / size) ──────────────


class UnionFind:
    """Disjoint-set union with path compression and union by rank."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n
        self._size = [1] * n
        self._components = n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def find_recursive(self, x: int) -> int:
        if self._parent[x] != x:
            self._parent[x] = self.find_recursive(self._parent[x])
        return self._parent[x]

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._components -= 1
        return True

    def connected(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)

    @property
    def n(self) -> int:
        return len(self._parent)

    @property
    def components(self) -> int:
        return self._components

    def component_size(self, x: int) -> int:
        return self._size[self.find(x)]

    def groups(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = {}
        for i in range(len(self._parent)):
            root = self.find(i)
            result.setdefault(root, []).append(i)
        return result


class UnionFindBySize(UnionFind):
    """DSU with union by size instead of rank."""

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        self._components -= 1
        return True


# ── Rollback DSU (checkpoint-based undo) ─────────────────────────────────


class RollbackUnionFind:
    """DSU that supports snapshot() and rollback() — undo to a prior state.

    Stores a history stack of (child, old_parent, parent_rank_delta, size_delta).
    """

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n
        self._size = [1] * n
        self._components = n
        self._history: list[tuple[int, int, int, int]] = []

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._history.append((rb, self._parent[rb], ra, self._size[ra]))
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._components -= 1
        return True

    def connected(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)

    @property
    def n(self) -> int:
        return len(self._parent)

    @property
    def components(self) -> int:
        return self._components

    def component_size(self, x: int) -> int:
        return self._size[self.find(x)]

    def snapshot(self) -> int:
        return len(self._history)

    def rollback(self, checkpoint: int = 0) -> None:
        while len(self._history) > checkpoint:
            child, old_parent, parent, old_size = self._history.pop()
            self._parent[child] = old_parent
            self._size[parent] = old_size
            if self._rank[parent] > 0:
                self._rank[parent] -= 1
            self._components += 1


# ── Persistent DSU (immutable copy-on-write) ─────────────────────────────


class PersistentUnionFind:
    """DSU where every union returns a NEW version — prior versions stay valid.

    Each node stores (parent, rank). A version is a list of nodes copied on write.
    """

    def __init__(self, n: int) -> None:
        self._n = n
        self._parent = list(range(n))
        self._rank = [0] * n
        self._size = [1] * n
        self._components = n
        self._version_id = 0

    def _copy(self) -> PersistentUnionFind:
        clone = PersistentUnionFind.__new__(PersistentUnionFind)
        clone._n = self._n
        clone._parent = list(self._parent)
        clone._rank = list(self._rank)
        clone._size = list(self._size)
        clone._components = self._components
        clone._version_id = self._version_id + 1
        return clone

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> PersistentUnionFind | None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return None
        new = self._copy()
        if new._rank[ra] < new._rank[rb]:
            ra, rb = rb, ra
        new._parent[rb] = ra
        new._size[ra] += new._size[rb]
        if new._rank[ra] == new._rank[rb]:
            new._rank[ra] += 1
        new._components -= 1
        return new

    def connected(self, a: int, b: int) -> bool:
        return self.find(a) == self.find(b)

    @property
    def n(self) -> int:
        return self._n

    @property
    def components(self) -> int:
        return self._components

    @property
    def version_id(self) -> int:
        return self._version_id

    def component_size(self, x: int) -> int:
        return self._size[self.find(x)]


# ── Dynamic Connectivity (offline queries with time windows) ─────────────


class DynamicConnectivity:
    """Answer connectivity queries offline given a list of edge add/remove operations.

    Uses a segment-tree-of-time approach: each edge exists over a contiguous
    interval of query indices.  The segment tree is built over the timeline,
    and a DFS with a rollback DSU answers all queries.
    """

    def __init__(self, n: int) -> None:
        self._n = n
        self._events: list[tuple[str, int, int]] = []
        self._edge_intervals: dict[tuple[int, int], list[list[int]]] = {}
        self._query_indices: list[int] = []

    def add_edge(self, u: int, v: int, time: int) -> None:
        key = (min(u, v), max(u, v))
        if key not in self._edge_intervals:
            self._edge_intervals[key] = []
        if not self._edge_intervals[key] or self._edge_intervals[key][-1][1] != -1:
            self._edge_intervals[key].append([time, -1])

    def remove_edge(self, u: int, v: int, time: int) -> None:
        key = (min(u, v), max(u, v))
        if key in self._edge_intervals and self._edge_intervals[key] and self._edge_intervals[key][-1][1] == -1:
            self._edge_intervals[key][-1][1] = time

    def add_query(self, u: int, v: int, time: int) -> None:
        self._query_indices.append(len(self._query_indices))
        self._events.append(("query", u, v))

    def solve(self) -> list[bool]:
        max_time = len(self._events)
        if max_time == 0:
            return []

        seg_size = 1 << (max_time - 1).bit_length() if max_time else 1
        seg: list[list[tuple[int, int]]] = [[] for _ in range(2 * seg_size)]

        for (u, v), intervals in self._edge_intervals.items():
            for lo, hi in intervals:
                if hi == -1:
                    hi = max_time
                if lo >= hi:
                    continue
                pos = lo + seg_size
                r = hi + seg_size
                while pos < r:
                    if pos & 1:
                        seg[pos].append((u, v))
                        pos += 1
                    if r & 1:
                        r -= 1
                        seg[r].append((u, v))
                    pos >>= 1
                    r >>= 1

        dsu = RollbackUnionFind(self._n)
        result = [False] * len(self._query_indices)
        query_map: dict[int, int] = {}
        for i, (kind, _u, _v) in enumerate(self._events):
            if kind == "query":
                query_map[i] = len(query_map)

        def _dfs(pos: int) -> None:
            checkpoint = dsu.snapshot()
            for u_edge, v_edge in seg[pos]:
                dsu.union(u_edge, v_edge)
            if pos >= seg_size:
                time = pos - seg_size
                if time < max_time and self._events[time][0] == "query":
                    _, u_q, v_q = self._events[time]
                    qi = query_map.get(time)
                    if qi is not None:
                        result[qi] = dsu.connected(u_q, v_q)
            else:
                _dfs(2 * pos)
                _dfs(2 * pos + 1)
            dsu.rollback(checkpoint)

        _dfs(1)
        return result


# ── Offline MST (Kruskal's algorithm, path compression DSU) ──────────────


def kruskal_mst(n: int, edges: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Return MST edges using Kruskal's algorithm. edges = [(u, v, weight), ...]."""
    edges_sorted = sorted(edges, key=lambda e: e[2])
    dsu = UnionFind(n)
    mst: list[tuple[int, int, int]] = []
    for u, v, w in edges_sorted:
        if dsu.union(u, v):
            mst.append((u, v, w))
    return mst


# ── Generic DSU — any hashable element type ──────────────────────────────


class GenericDSU(Generic[T]):
    """Disjoint-set union for arbitrary hashable elements."""

    def __init__(self, elements: list[T] | None = None) -> None:
        self._parent: dict[T, T] = {}
        self._rank: dict[T, int] = {}
        self._size: dict[T, int] = {}
        self._components = 0
        if elements:
            for e in elements:
                self.add(e)

    def add(self, x: T) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
            self._size[x] = 1
            self._components += 1

    def find(self, x: T) -> T:
        if x not in self._parent:
            self.add(x)
            return x
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: T, b: T) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._components -= 1
        return True

    def connected(self, a: T, b: T) -> bool:
        return self.find(a) == self.find(b)

    @property
    def components(self) -> int:
        return self._components

    def component_size(self, x: T) -> int:
        return self._size[self.find(x)]
