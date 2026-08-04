"""Deep tests for union-find v2: DSU, persistent, rollback, dynamic connectivity, offline queries.

Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.union_find_v2 import (
    DynamicConnectivity,
    GenericDSU,
    PersistentUnionFind,
    RollbackUnionFind,
    UnionFind,
    UnionFindBySize,
    kruskal_mst,
)

# ── UnionFind (standard DSU) ─────────────────────────────────────────────


class TestUnionFind:
    def test_initial_state_disconnected(self) -> None:
        uf = UnionFind(5)
        for i in range(5):
            for j in range(i + 1, 5):
                assert not uf.connected(i, j)
        assert uf.components == 5

    def test_basic_union_connectivity(self) -> None:
        uf = UnionFind(6)
        uf.union(0, 1)
        uf.union(1, 2)
        assert uf.connected(0, 2)
        assert uf.connected(0, 1)
        assert uf.connected(1, 2)
        assert not uf.connected(0, 3)
        assert uf.components == 4

    def test_union_returns_true_false(self) -> None:
        uf = UnionFind(4)
        assert uf.union(0, 1)
        assert uf.union(1, 2)
        assert not uf.union(0, 2)

    def test_component_size(self) -> None:
        uf = UnionFind(8)
        uf.union(0, 1)
        uf.union(1, 2)
        uf.union(3, 4)
        uf.union(0, 4)
        assert uf.component_size(0) == 5
        assert uf.component_size(5) == 1

    def test_path_compression_flattens(self) -> None:
        uf = UnionFind(10)
        uf.union(0, 1)
        uf.union(1, 2)
        uf.union(2, 3)
        uf.union(3, 4)
        root = uf.find(0)
        for i in range(5):
            assert uf.find(i) == root

    def test_groups_returns_partitions(self) -> None:
        uf = UnionFind(6)
        uf.union(0, 1)
        uf.union(2, 3)
        uf.union(4, 5)
        uf.union(0, 2)
        groups = uf.groups()
        assert len(groups) == 2
        assert set(groups[uf.find(0)]) == {0, 1, 2, 3}
        assert set(groups[uf.find(4)]) == {4, 5}

    def test_n_equals_constructor_arg(self) -> None:
        uf = UnionFind(12)
        assert uf.n == 12


# ── UnionFindBySize ──────────────────────────────────────────────────────


class TestUnionFindBySize:
    def test_small_tree_attaches_to_large(self) -> None:
        uf = UnionFindBySize(5)
        uf.union(0, 1)
        uf.union(1, 2)
        uf.union(3, 4)
        assert uf.component_size(0) == 3
        assert uf.component_size(3) == 2
        uf.union(3, 0)
        assert uf.component_size(0) == 5

    def test_union_by_size_duplicate(self) -> None:
        uf = UnionFindBySize(4)
        uf.union(0, 1)
        uf.union(2, 3)
        assert not uf.union(0, 1)
        assert uf.components == 2


# ── RollbackUnionFind ────────────────────────────────────────────────────


class TestRollbackUnionFind:
    def test_snapshot_and_rollback_full(self) -> None:
        ruf = RollbackUnionFind(6)
        ruf.union(0, 1)
        ruf.union(2, 3)
        cp = ruf.snapshot()
        ruf.union(0, 2)
        assert ruf.connected(0, 2)
        ruf.rollback(cp)
        assert not ruf.connected(0, 2)
        assert ruf.connected(0, 1)

    def test_rollback_restores_components(self) -> None:
        ruf = RollbackUnionFind(5)
        assert ruf.components == 5
        ruf.union(0, 1)
        assert ruf.components == 4
        cp = ruf.snapshot()
        ruf.union(1, 2)
        assert ruf.components == 3
        ruf.rollback(cp)
        assert ruf.components == 4

    def test_partial_rollback_to_midpoint(self) -> None:
        ruf = RollbackUnionFind(6)
        ruf.union(0, 1)
        cp1 = ruf.snapshot()
        ruf.union(2, 3)
        ruf.union(3, 4)
        cp2 = ruf.snapshot()
        ruf.union(0, 2)
        assert ruf.connected(0, 3)
        ruf.rollback(cp2)
        assert not ruf.connected(0, 2)
        assert ruf.connected(2, 4)
        ruf.rollback(cp1)
        assert ruf.components == 5

    def test_rollback_restores_sizes(self) -> None:
        ruf = RollbackUnionFind(5)
        ruf.union(0, 1)
        cp = ruf.snapshot()
        ruf.union(0, 2)
        assert ruf.component_size(0) == 3
        ruf.rollback(cp)
        assert ruf.component_size(0) == 2
        assert ruf.component_size(2) == 1

    def test_rollback_to_zero_fully_disconnects(self) -> None:
        ruf = RollbackUnionFind(4)
        ruf.union(0, 1)
        ruf.union(1, 2)
        ruf.union(2, 3)
        assert ruf.components == 1
        ruf.rollback(0)
        assert ruf.components == 4
        for i in range(4):
            for j in range(i + 1, 4):
                assert not ruf.connected(i, j)


# ── PersistentUnionFind ──────────────────────────────────────────────────


class TestPersistentUnionFind:
    def test_union_returns_new_version(self) -> None:
        v0 = PersistentUnionFind(4)
        v1 = v0.union(0, 1)
        assert v1 is not None
        assert v0.components == 4
        assert v1.components == 3

    def test_prior_version_unchanged_after_union(self) -> None:
        v0 = PersistentUnionFind(5)
        v1 = v0.union(0, 1)
        v2 = v1.union(1, 2)
        assert v2 is not None
        assert not v0.connected(0, 1)
        assert v0.connected(0, 0)
        assert v1.connected(0, 1)
        assert not v1.connected(0, 2)
        assert v2.connected(0, 2)

    def test_union_same_component_returns_none(self) -> None:
        v0 = PersistentUnionFind(3)
        v1 = v0.union(0, 1)
        assert v1 is not None
        v2 = v1.union(0, 1)
        assert v2 is None
        assert v1.connected(0, 1)

    def test_version_id_increments(self) -> None:
        v0 = PersistentUnionFind(5)
        assert v0.version_id == 0
        v1 = v0.union(0, 1)
        assert v1 is not None
        assert v1.version_id == 1
        v2 = v1.union(2, 3)
        assert v2 is not None
        assert v2.version_id == 2

    def test_component_size_persists(self) -> None:
        v0 = PersistentUnionFind(6)
        v1 = v0.union(0, 1)
        v2 = v1.union(1, 2)
        assert v2 is not None
        assert v2.component_size(0) == 3
        assert v0.component_size(0) == 1

    def test_long_chain_stays_connected(self) -> None:
        v = PersistentUnionFind(10)
        for i in range(9):
            v2 = v.union(i, i + 1)
            assert v2 is not None
            v = v2
        assert v.connected(0, 9)


# ── DynamicConnectivity ──────────────────────────────────────────────────


class TestDynamicConnectivity:
    def test_connectivity_through_time(self) -> None:
        dc = DynamicConnectivity(4)
        dc.add_edge(0, 1, 0)
        dc.add_query(0, 1, 1)
        dc.add_query(0, 2, 2)
        assert dc.solve() == [True, False]

    def test_remove_edge_disconnects(self) -> None:
        dc = DynamicConnectivity(4)
        dc.add_edge(0, 1, 0)
        dc.remove_edge(0, 1, 1)
        dc.add_query(0, 1, 0)
        dc.add_query(0, 1, 2)
        assert dc.solve() == [True, False]

    def test_re_add_edge_reconnects(self) -> None:
        dc = DynamicConnectivity(3)
        dc.add_edge(0, 1, 0)
        dc.remove_edge(0, 1, 1)
        dc.add_edge(0, 1, 2)
        dc.add_query(0, 1, 0)
        dc.add_query(0, 1, 1)
        dc.add_query(0, 1, 3)
        assert dc.solve() == [True, False, True]

    def test_empty_returns_empty_list(self) -> None:
        dc = DynamicConnectivity(3)
        assert dc.solve() == []


# ── Kruskal MST ──────────────────────────────────────────────────────────


class TestKruskalMST:
    def test_triangle_graph(self) -> None:
        edges = [(0, 1, 3), (1, 2, 4), (0, 2, 5)]
        mst = kruskal_mst(3, edges)
        assert len(mst) == 2
        weights = {w for _, _, w in mst}
        assert 3 in weights
        assert 4 in weights

    def test_disconnected_graph(self) -> None:
        edges = [(0, 1, 1), (2, 3, 2)]
        mst = kruskal_mst(4, edges)
        assert len(mst) == 2

    def test_single_node_returns_empty(self) -> None:
        mst = kruskal_mst(1, [])
        assert mst == []

    def test_parallel_edges_choose_cheapest(self) -> None:
        edges = [(0, 1, 5), (0, 1, 2), (1, 2, 1)]
        mst = kruskal_mst(3, edges)
        total = sum(w for _, _, w in mst)
        assert total == 3
        assert len(mst) == 2


# ── GenericDSU ───────────────────────────────────────────────────────────


class TestGenericDSU:
    def test_string_elements(self) -> None:
        dsu = GenericDSU[str]()
        dsu.add("a")
        dsu.add("b")
        dsu.add("c")
        dsu.union("a", "b")
        assert dsu.connected("a", "b")
        assert not dsu.connected("a", "c")

    def test_union_on_non_added_auto_adds(self) -> None:
        dsu = GenericDSU[int]()
        dsu.union(10, 20)
        assert dsu.connected(10, 20)
        assert dsu.component_size(10) == 2

    def test_constructor_from_list(self) -> None:
        dsu = GenericDSU[str](["x", "y", "z"])
        assert dsu.components == 3
        dsu.union("x", "y")
        assert dsu.components == 2

    def test_find_auto_creates(self) -> None:
        dsu = GenericDSU[int]()
        root = dsu.find(42)
        assert root == 42
        assert dsu.components == 1

    def test_manual_add_idempotent(self) -> None:
        dsu = GenericDSU[str]()
        dsu.add("k")
        dsu.add("k")
        assert dsu.components == 1
