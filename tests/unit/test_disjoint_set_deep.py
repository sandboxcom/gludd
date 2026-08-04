"""Deep disjoint-set (union-find) tests: union/find, path compression,
rank/size union heuristics, connected components, set count, edge cases,
and performance invariants.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

# ── Inline DisjointSet implementation (self-contained test subject) ─────


class DisjointSet:
    """Union-Find / Disjoint-Set with union-by-rank and path compression."""

    def __init__(self, n: int = 0) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self._parent: list[int] = list(range(n))
        self._rank: list[int] = [0] * n
        self._size: list[int] = [1] * n
        self._count: int = n

    # ── queries ─────────────────────────────────────────────────────────

    @property
    def set_count(self) -> int:
        """Number of disjoint sets currently tracked."""
        return self._count

    @property
    def element_count(self) -> int:
        """Total number of elements (initial n)."""
        return len(self._parent)

    def find(self, x: int) -> int:
        """Find the root / set representative of *x* with path compression."""
        self._check_bounds(x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def connected(self, a: int, b: int) -> bool:
        """Return True iff *a* and *b* belong to the same set."""
        return self.find(a) == self.find(b)

    def size_of(self, x: int) -> int:
        """Number of elements in the set containing *x*."""
        return self._size[self.find(x)]

    def components(self) -> list[list[int]]:
        """Return every component as a list of elements, sorted per component."""
        buckets: dict[int, list[int]] = {}
        for i in range(len(self._parent)):
            root = self.find(i)
            buckets.setdefault(root, []).append(i)
        return sorted((sorted(v) for v in buckets.values()), key=lambda g: g[0])

    @classmethod
    def from_unions(cls, n: int, pairs: Iterable[tuple[int, int]]) -> DisjointSet:
        """Factory: create *n* elements then union each (a, b) pair."""
        ds = cls(n)
        for a, b in pairs:
            ds.union(a, b)
        return ds

    # ── mutations ───────────────────────────────────────────────────────

    def add(self) -> int:
        """Add a new singleton element, return its index."""
        idx = len(self._parent)
        self._parent.append(idx)
        self._rank.append(0)
        self._size.append(1)
        self._count += 1
        return idx

    def union(self, a: int, b: int) -> bool:
        """Merge the sets containing *a* and *b*.  Returns True when a
        merge actually occurred (i.e. the elements were in different sets)."""
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return False

        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._size[ra] += self._size[rb]
        self._count -= 1
        return True

    # ── helpers ─────────────────────────────────────────────────────────

    def _check_bounds(self, x: int) -> None:
        if x < 0 or x >= len(self._parent):
            raise IndexError(f"element {x} out of range [0, {len(self._parent)})")

    def __len__(self) -> int:
        return len(self._parent)

    def __repr__(self) -> str:
        return f"DisjointSet(elements={len(self._parent)}, sets={self._count})"


# ── Tests ───────────────────────────────────────────────────────────────


class TestConstruction:
    """Construction, zero-size edge cases."""

    def test_empty_set(self):
        ds = DisjointSet(0)
        assert ds.element_count == 0
        assert ds.set_count == 0

    def test_single_element(self):
        ds = DisjointSet(1)
        assert ds.element_count == 1
        assert ds.set_count == 1
        assert ds.find(0) == 0

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            DisjointSet(-1)


class TestFind:
    """Basic find behaviour."""

    @pytest.mark.parametrize("n", [1, 3, 10])
    def test_initial_self_root(self, n: int):
        ds = DisjointSet(n)
        for i in range(n):
            assert ds.find(i) == i

    def test_find_after_union_returns_root(self):
        ds = DisjointSet(4)
        ds.union(0, 1)
        r0 = ds.find(0)
        r1 = ds.find(1)
        assert r0 == r1
        assert r0 in (0, 1)

    def test_find_out_of_bounds_raises(self):
        ds = DisjointSet(3)
        with pytest.raises(IndexError):
            ds.find(-1)
        with pytest.raises(IndexError):
            ds.find(3)


class TestUnion:
    """Union operations — correctness and return value."""

    def test_union_returns_true_on_merge(self):
        ds = DisjointSet(5)
        assert ds.union(0, 1) is True

    def test_union_returns_false_on_self_union(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        assert ds.union(0, 1) is False  # already same set

    def test_union_returns_false_on_already_connected(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        ds.union(1, 2)
        assert ds.union(0, 2) is False

    def test_chained_union_reduces_count(self):
        ds = DisjointSet(5)
        assert ds.set_count == 5
        ds.union(0, 1)
        assert ds.set_count == 4
        ds.union(1, 2)
        assert ds.set_count == 3
        ds.union(3, 4)
        assert ds.set_count == 2
        ds.union(0, 3)
        assert ds.set_count == 1

    def test_union_out_of_bounds_raises(self):
        ds = DisjointSet(3)
        with pytest.raises(IndexError):
            ds.union(-1, 2)
        with pytest.raises(IndexError):
            ds.union(0, 3)


class TestConnected:
    """connected(a, b) predicate."""

    def test_initial_disconnected(self):
        ds = DisjointSet(5)
        for i in range(5):
            for j in range(i + 1, 5):
                assert not ds.connected(i, j)

    def test_connected_after_union(self):
        ds = DisjointSet(5)
        ds.union(0, 2)
        assert ds.connected(0, 2)
        assert not ds.connected(0, 1)
        assert not ds.connected(2, 3)

    def test_connected_transitive(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        ds.union(1, 2)
        assert ds.connected(0, 2)  # transitivity through 1


class TestPathCompression:
    """Path compression is applied during find; verify depth reduction."""

    def test_linear_chain_compressed(self):
        n = 200
        ds = DisjointSet(n)
        for i in range(n - 1):
            ds._parent[i + 1] = i  # bypass union-heuristic for linear chain
        assert ds.find(n - 1) == 0
        # halving compression: each node now points to its grandparent;
        # repeated finds converge to the root
        depths_before = [self._depth(ds, i) for i in range(n)]
        for _ in range(4):
            ds.find(n - 1)
        depths_after = [self._depth(ds, i) for i in range(n)]
        assert sum(depths_after) <= sum(depths_before)

    @staticmethod
    def _depth(ds: DisjointSet, x: int) -> int:
        d = 0
        while ds._parent[x] != x:
            x = ds._parent[x]
            d += 1
        return d

    def test_find_on_root_is_noop(self):
        ds = DisjointSet(10)
        r = ds.find(5)
        assert r == 5
        assert ds._parent[5] == 5


class TestUnionByRank:
    """Union-by-rank ensures the shallower tree attaches to the deeper one."""

    def test_smaller_rank_attaches_to_larger(self):
        ds = DisjointSet(6)
        ds.union(0, 1)  # rank[0]=1
        ds.union(2, 3)  # rank[2]=1
        ds.union(0, 2)  # equal rank, 0 stays root, rank[0] -> 2
        assert ds.find(2) == 0
        assert ds._rank[0] == 2

    def test_rank_never_decreases(self):
        ds = DisjointSet(100)
        for i in range(0, 99, 2):
            ds.union(i, i + 1)
        ranks_before = list(ds._rank)
        for i in range(0, 50, 2):
            ds.union(i, i + 2)
        # ranks should never decrease
        for i in range(100):
            assert ds._rank[i] >= ranks_before[i]


class TestUnionBySize:
    """size_of reports correct component sizes."""

    def test_initial_all_size_one(self):
        ds = DisjointSet(7)
        for i in range(7):
            assert ds.size_of(i) == 1

    def test_size_accumulates_on_union(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        assert ds.size_of(0) == 2
        assert ds.size_of(1) == 2
        ds.union(2, 3)
        ds.union(0, 2)
        assert ds.size_of(0) == 4
        assert ds.size_of(3) == 4
        assert ds.size_of(4) == 1

    def test_size_after_self_union_unchanged(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        sz = ds.size_of(0)
        ds.union(0, 1)  # no-op
        assert ds.size_of(0) == sz

    def test_size_on_large_component(self):
        n = 1000
        ds = DisjointSet(n)
        for i in range(1, n):
            ds.union(0, i)
        assert ds.size_of(0) == n
        assert ds.size_of(n - 1) == n


class TestComponents:
    """components() — grouping and enumeration."""

    def test_initial_each_singleton(self):
        ds = DisjointSet(3)
        comps = ds.components()
        assert comps == [[0], [1], [2]]

    def test_two_components_after_union(self):
        ds = DisjointSet(4)
        ds.union(0, 1)
        ds.union(2, 3)
        assert ds.components() == [[0, 1], [2, 3]]

    def test_single_component_fully_connected(self):
        ds = DisjointSet(4)
        ds.union(0, 1)
        ds.union(1, 2)
        ds.union(2, 3)
        assert ds.components() == [[0, 1, 2, 3]]

    def test_components_sorted_per_group(self):
        ds = DisjointSet(6)
        ds.union(3, 5)
        ds.union(0, 2)
        ds.union(2, 4)
        comps = ds.components()
        assert comps == [[0, 2, 4], [1], [3, 5]]


class TestAdd:
    """Dynamic element addition."""

    def test_add_increases_count(self):
        ds = DisjointSet(3)
        idx = ds.add()
        assert idx == 3
        assert ds.element_count == 4
        assert ds.set_count == 4

    def test_added_element_self_root(self):
        ds = DisjointSet(0)
        idx = ds.add()
        assert ds.find(idx) == idx
        assert ds.size_of(idx) == 1

    def test_add_then_union(self):
        ds = DisjointSet(2)
        ds.union(0, 1)
        a = ds.add()
        b = ds.add()
        assert ds.set_count == 3  # one big + two singletons
        ds.union(a, b)
        assert ds.set_count == 2


class TestFromUnions:
    """Factory method."""

    def test_from_unions_empty(self):
        ds = DisjointSet.from_unions(5, [])
        assert ds.set_count == 5

    def test_from_unions_creates_correct_components(self):
        ds = DisjointSet.from_unions(6, [(0, 1), (1, 2), (3, 4)])
        assert ds.set_count == 3
        assert ds.connected(0, 2)
        assert ds.connected(3, 4)
        assert not ds.connected(0, 3)


class TestStress:
    """Larger-scale correctness invariants."""

    N = 2000

    def test_all_union_set_count_reaches_one(self):
        ds = DisjointSet(self.N)
        for i in range(1, self.N):
            ds.union(0, i)
        assert ds.set_count == 1
        assert ds.size_of(0) == self.N

    def test_pairwise_disjoint_remaining(self):
        ds = DisjointSet(self.N)
        for i in range(0, self.N - 1, 2):
            ds.union(i, i + 1)
        assert ds.set_count == self.N // 2
        # no cross-pair connections
        for i in range(0, self.N - 2, 2):
            assert not ds.connected(i, i + 2)

    def test_find_always_idempotent(self):
        ds = DisjointSet(200)
        for i in range(0, 199, 2):
            ds.union(i, i + 1)
        for i in range(0, 190, 4):
            ds.union(i, i + 2)
        for _ in range(3):
            for i in range(200):
                assert ds.find(ds.find(i)) == ds.find(i)

    def test_random_ops_consistent(self):
        ds = DisjointSet(500)
        expected: dict[int, set[int]] = {i: {i} for i in range(500)}

        import random

        rng = random.Random(42)
        for _ in range(2000):
            a = rng.randrange(500)
            b = rng.randrange(500)
            merged = ds.union(a, b)
            if a == b or merged:
                # build expected ground truth
                sa = expected[a]
                sb = expected[b]
                union_set = sa | sb
                for e in union_set:
                    expected[e] = union_set

        # verify every element's component matches ground truth
        for i in range(500):
            ds_component = set([j for j in range(500) if ds.connected(i, j)])
            assert ds_component == expected[i]

    def test_len_matches_element_count(self):
        ds = DisjointSet(10)
        assert len(ds) == 10
        ds.union(0, 1)
        assert len(ds) == 10

    def test_repr(self):
        ds = DisjointSet(5)
        ds.union(0, 1)
        r = repr(ds)
        assert "elements=5" in r
        assert "sets=4" in r


class TestEdgeCases:
    """Boundary and edge-case behaviours."""

    def test_index_error_messages(self):
        ds = DisjointSet(2)
        with pytest.raises(IndexError, match="element -1"):
            ds.find(-1)
        with pytest.raises(IndexError, match="element 5"):
            ds.find(5)

    def test_size_of_isolated_element(self):
        ds = DisjointSet(1)
        assert ds.size_of(0) == 1

    def test_size_of_after_full_cycle(self):
        ds = DisjointSet(3)
        ds.union(0, 1)
        ds.union(1, 2)
        ds.union(2, 0)  # no-op, all already connected
        assert ds.size_of(0) == 3

    def test_set_count_never_negative(self):
        ds = DisjointSet(100)
        for i in range(99):
            ds.union(i, i + 1)
        assert ds.set_count == 1
        # further unions should not drop below 1
        for _ in range(51):
            ds.union(0, 50)
        assert ds.set_count == 1

    def test_symmetry_of_connected(self):
        ds = DisjointSet(10)
        pairs = [(0, 2), (3, 5), (6, 7), (2, 7), (8, 9)]
        for a, b in pairs:
            ds.union(a, b)
        for a in range(10):
            for b in range(10):
                assert ds.connected(a, b) == ds.connected(b, a)

    def test_rank_bound_log_n(self):
        """Rank is bounded by log2(n)."""
        import math

        n = 1024
        ds = DisjointSet(n)
        for i in range(0, n - 1, 2):
            ds.union(i, i + 1)
        for i in range(0, n - 3, 4):
            ds.union(i, i + 2)
        max_rank = max(ds._rank)
        assert max_rank <= math.ceil(math.log2(n))

    def test_union_out_of_bounds_both(self):
        ds = DisjointSet(5)
        with pytest.raises(IndexError):
            ds.union(5, 6)
        with pytest.raises(IndexError):
            ds.union(0, 10)
