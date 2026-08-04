"""Deep tests for segment tree v2: lazy sum/min/max, persistent, 2D."""

from __future__ import annotations

from general_ludd.algorithms.segment_tree_v2 import (
    LazySegTree2D,
    PersistentSegTree,
    SegTree2D,
    lazy_max_tree,
    lazy_min_tree,
    lazy_sum_tree,
)

# ── Lazy sum tree ──────────────────────────────────────────────────────


class TestLazySumTree:
    def test_build_and_full_sum(self) -> None:
        st = lazy_sum_tree([1, 2, 3, 4, 5])
        assert st.range_query(0, 5) == 15

    def test_build_empty(self) -> None:
        st = lazy_sum_tree([])
        assert len(st) == 0

    def test_range_add_full(self) -> None:
        st = lazy_sum_tree([1, 2, 3, 4, 5])
        st.range_update(0, 5, 10)
        assert st.range_query(0, 5) == 65

    def test_range_add_prefix(self) -> None:
        st = lazy_sum_tree([1, 2, 3, 4, 5])
        st.range_update(0, 2, 5)
        assert st.range_query(0, 2) == 13
        assert st.range_query(2, 5) == 12

    def test_range_add_suffix(self) -> None:
        st = lazy_sum_tree([10, 20, 30, 40])
        st.range_update(2, 4, 100)
        assert st.range_query(2, 4) == 270

    def test_chained_range_updates(self) -> None:
        st = lazy_sum_tree([0] * 10)
        st.range_update(0, 10, 1)
        st.range_update(3, 7, 2)
        st.range_update(5, 6, 5)
        assert st.range_query(0, 10) == 10 + 8 + 5
        assert st.range_query(4, 5) == 3
        assert st.range_query(5, 6) == 8

    def test_overlapping_updates(self) -> None:
        st = lazy_sum_tree([0] * 8)
        st.range_update(0, 4, 3)
        st.range_update(2, 6, 4)
        st.range_update(1, 7, 1)
        expected = [0] * 8
        for lo, r, v in [(0, 4, 3), (2, 6, 4), (1, 7, 1)]:
            for i in range(lo, r):
                expected[i] += v
        for i in range(8):
            assert st.range_query(i, i + 1) == expected[i]

    def test_single_element_range(self) -> None:
        st = lazy_sum_tree([5, 8, 13])
        assert st.range_query(1, 2) == 8

    def test_large_array(self) -> None:
        n = 200
        st = lazy_sum_tree([i for i in range(n)])
        for lo in range(0, n, 17):
            r = min(lo + 7, n)
            st.range_update(lo, r, 3)
        base = [i for i in range(n)]
        for lo in range(0, n, 17):
            for i in range(lo, min(lo + 7, n)):
                base[i] += 3
        assert st.range_query(0, n) == sum(base)


# ── Lazy min tree ──────────────────────────────────────────────────────


class TestLazyMinTree:
    def test_build_min(self) -> None:
        st = lazy_min_tree([5, 2, 8, 1, 9])
        assert st.range_query(0, 5) == 1
        assert st.range_query(0, 2) == 2

    def test_range_add_decreases_min(self) -> None:
        st = lazy_min_tree([10, 20, 30, 40])
        st.range_update(1, 3, -15)
        assert st.range_query(0, 4) == 5

    def test_chained_min_updates(self) -> None:
        st = lazy_min_tree([10, 20, 30, 40, 50])
        st.range_update(0, 3, -5)
        assert st.range_query(0, 3) == 5
        st.range_update(2, 5, -20)
        assert st.range_query(2, 5) == 5

    def test_min_with_positive_add(self) -> None:
        st = lazy_min_tree([0, 100, 200, 300])
        st.range_update(0, 2, 50)
        assert st.range_query(0, 4) == 50


# ── Lazy max tree ──────────────────────────────────────────────────────


class TestLazyMaxTree:
    def test_build_max(self) -> None:
        st = lazy_max_tree([5, 2, 8, 1, 9])
        assert st.range_query(0, 5) == 9
        assert st.range_query(1, 4) == 8

    def test_range_add_increases_max(self) -> None:
        st = lazy_max_tree([10, 20, 30, 40])
        st.range_update(0, 2, 50)
        assert st.range_query(0, 4) == 70

    def test_chained_max_updates(self) -> None:
        st = lazy_max_tree([10, 20, 30, 40, 50])
        st.range_update(1, 4, 100)
        assert st.range_query(0, 5) == 140


# ── Persistent segment tree ────────────────────────────────────────────


class TestPersistentSegTree:
    def test_build_and_query(self) -> None:
        pst = PersistentSegTree([3, 1, 4, 1, 5])
        assert pst.query(0, 0, 5) == 14
        assert pst.query(0, 1, 3) == 5

    def test_empty(self) -> None:
        pst = PersistentSegTree([])
        assert pst.n == 0

    def test_single_element(self) -> None:
        pst = PersistentSegTree([42])
        assert pst.query(0, 0, 1) == 42

    def test_update_creates_new_version(self) -> None:
        pst = PersistentSegTree([1, 2, 3, 4, 5])
        assert pst.versions == 1
        pst.update(2, 99)
        assert pst.versions == 2
        assert pst.query(0, 0, 5) == 15
        assert pst.query(1, 0, 5) == 111

    def test_multiple_versions_retain_history(self) -> None:
        pst = PersistentSegTree([10, 20, 30])
        pst.update(1, 50)
        pst.update(0, 5)
        pst.update(2, 100)
        assert pst.query(0, 0, 3) == 60
        assert pst.query(1, 0, 3) == 90
        assert pst.query(2, 0, 3) == 85
        assert pst.query(3, 0, 3) == 155

    def test_partial_query_across_versions(self) -> None:
        pst = PersistentSegTree([1, 2, 3, 4, 5, 6, 7, 8])
        pst.update(3, 0)
        assert pst.query(0, 2, 6) == 18
        assert pst.query(1, 2, 6) == 14


# ── 2D segment tree (point update, submatrix sum) ──────────────────────


class TestSegTree2D:
    def test_single_cell(self) -> None:
        st = SegTree2D(3, 3)
        st.update(0, 0, 5)
        assert st.query(0, 1, 0, 1) == 5

    def test_row_sum(self) -> None:
        st = SegTree2D(4, 4)
        for c in range(4):
            st.update(0, c, c + 1)
        assert st.query(0, 1, 0, 4) == 10

    def test_full_matrix_sum(self) -> None:
        st = SegTree2D(4, 4)
        for r in range(4):
            for c in range(4):
                st.update(r, c, 1)
        assert st.query(0, 4, 0, 4) == 16

    def test_submatrix(self) -> None:
        st = SegTree2D(5, 5)
        for r in range(5):
            for c in range(5):
                st.update(r, c, r * 5 + c + 1)
        assert st.query(1, 3, 1, 3) == sum(r * 5 + c + 1 for r in range(1, 3) for c in range(1, 3))

    def test_update_overwrite(self) -> None:
        st = SegTree2D(3, 3)
        st.update(1, 1, 10)
        st.update(1, 1, -3)
        assert st.query(0, 3, 0, 3) == 7


# ── LazySegTree2D (row assign, column queries) ─────────────────────────


class TestLazySegTree2D:
    def test_assign_row_and_query(self) -> None:
        st = LazySegTree2D(4, 3)
        st.assign_row(0, [1, 2, 3])
        st.assign_row(1, [4, 5, 6])
        st.assign_row(2, [7, 8, 9])
        st.assign_row(3, [10, 11, 12])
        assert st.col_query(0, 4, 1) == 2 + 5 + 8 + 11

    def test_partial_row_range(self) -> None:
        st = LazySegTree2D(5, 2)
        for r in range(5):
            st.assign_row(r, [r, r * 2])
        assert st.col_query(1, 4, 0) == 1 + 2 + 3

    def test_single_row(self) -> None:
        st = LazySegTree2D(3, 4)
        st.assign_row(1, [10, 20, 30, 40])
        assert st.col_query(1, 2, 2) == 30

    def test_update_existing_row(self) -> None:
        st = LazySegTree2D(3, 3)
        st.assign_row(0, [1, 1, 1])
        st.assign_row(0, [5, 5, 5])
        assert st.col_query(0, 1, 1) == 5

    def test_col_query_ignores_unassigned(self) -> None:
        st = LazySegTree2D(5, 3)
        st.assign_row(2, [7, 8, 9])
        assert st.col_query(0, 5, 0) == 7


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_large_array_lazy_sum(self) -> None:
        n = 512
        data = [i % 7 for i in range(n)]
        st = lazy_sum_tree(data)
        for start in range(0, n, 32):
            st.range_update(start, min(start + 16, n), 3)
        total = st.range_query(0, n)
        expected = sum(data)
        for start in range(0, n, 32):
            for _i in range(start, min(start + 16, n)):
                expected += 3
        assert total == expected

    def test_lazy_sum_power_of_two_size(self) -> None:
        st = lazy_sum_tree([1] * 16)
        st.range_update(0, 16, 2)
        assert st.range_query(0, 16) == 48

    def test_lazy_sum_non_power_of_two_size(self) -> None:
        st = lazy_sum_tree([1] * 13)
        st.range_update(0, 13, 1)
        assert st.range_query(0, 13) == 26

    def test_2d_empty_rows(self) -> None:
        st = SegTree2D(0, 5)
        assert st.query(0, 0, 0, 0) == 0

    def test_persistent_version_count(self) -> None:
        pst = PersistentSegTree([1, 1, 1, 1, 1])
        for i in range(5):
            pst.update(i, i * 2)
        assert pst.versions == 6
