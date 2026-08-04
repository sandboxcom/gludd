"""Deep LCP array tests: Kasai, sparse-table RMQ, longest repeated substring.

Covers empty/edge cases, classic strings, large random consistency,
and all exported LCP utilities.
"""

from __future__ import annotations

import random

import pytest

from general_ludd.algorithms.suffix_array import (
    _floor_log2,
    build_lcp,
    build_lcp_sparse_table,
    build_sa,
    build_sa_lcp_rmq,
    lcp_of_suffixes,
    lcp_query,
    longest_repeated_substring,
)


class TestFloorLog2:
    def test_powers_of_two(self) -> None:
        assert _floor_log2(1) == 0
        assert _floor_log2(2) == 1
        assert _floor_log2(4) == 2
        assert _floor_log2(8) == 3
        assert _floor_log2(1024) == 10

    def test_non_powers(self) -> None:
        assert _floor_log2(3) == 1
        assert _floor_log2(5) == 2
        assert _floor_log2(7) == 2
        assert _floor_log2(1000) == 9


class TestLongestRepeatedSubstring:
    def test_empty(self) -> None:
        sa = build_sa("")
        lcp = build_lcp("", sa)
        assert longest_repeated_substring("", sa, lcp) == (0, 0, 0)

    def test_single_char(self) -> None:
        for s in ["a", "x"]:
            sa = build_sa(s)
            lcp = build_lcp(s, sa)
            assert longest_repeated_substring(s, sa, lcp) == (0, 0, 0)

    def test_all_distinct(self) -> None:
        s = "abcdefgh"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        assert longest_repeated_substring(s, sa, lcp) == (0, 0, 0)

    def test_all_same(self) -> None:
        s = "aaaaa"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 4
        assert s[a : a + length] == s[b : b + length]
        assert a != b

    def test_banana(self) -> None:
        s = "banana"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 3
        assert a != b
        assert s[a : a + length] == s[b : b + length]

    def test_mississippi(self) -> None:
        s = "mississippi"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 4
        assert a != b
        assert s[a : a + length] == s[b : b + length]

    def test_abracadabra(self) -> None:
        s = "abracadabra"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 4
        assert a != b
        assert s[a : a + length] == s[b : b + length]

    def test_repeated_abc(self) -> None:
        s = "abcabcabc"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 6
        assert s[a : a + length] == "abcabc"
        assert s[b : b + length] == "abcabc"

    def test_numeric_list_lrm(self) -> None:
        s = [1, 2, 3, 1, 2, 3, 1, 2, 3]
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 6
        assert a != b

    def test_brute_force_consistency(self) -> None:
        random.seed(123)
        for _ in range(50):
            n = random.randint(2, 60)
            s = "".join(random.choice("acgt") for _ in range(n))
            sa = build_sa(s)
            lcp = build_lcp(s, sa)
            length, a, b = longest_repeated_substring(s, sa, lcp)
            if length == 0:
                for i in range(n):
                    for j in range(i + 1, n):
                        k = 0
                        while i + k < n and j + k < n and s[i + k] == s[j + k]:
                            k += 1
                        assert k == 0, f"missed repeat in {s!r}: {i}:{j} len {k}"
            else:
                assert s[a : a + length] == s[b : b + length]
                _a_min, _b_min = min(a, b), max(a, b)
                for i in range(n):
                    for j in range(i + 1, n):
                        k = 0
                        while i + k < n and j + k < n and s[i + k] == s[j + k]:
                            k += 1
                        assert k <= length, f"longer repeat missed ({k} > {length}) in {s!r}"


class TestBuildLcpSparseTable:
    def test_empty_lcp(self) -> None:
        st = build_lcp_sparse_table([])
        assert st == [[0]]

    def test_single_element_lcp(self) -> None:
        st = build_lcp_sparse_table([0])
        assert st[0] == [0]

    def test_sparse_level_count(self) -> None:
        lcp = [0] * 15
        st = build_lcp_sparse_table(lcp)
        assert len(st) == 4  # floor(log2(15)) + 1 = 3 + 1

    def test_level_count_power_of_two(self) -> None:
        lcp = [0] * 8
        st = build_lcp_sparse_table(lcp)
        assert len(st) == 4  # floor(log2(8)) + 1 = 3 + 1


class TestLcpQueryRMQ:
    def test_single_element_range(self) -> None:
        st = build_lcp_sparse_table([5])
        assert lcp_query(st, 0, 1) == 5

    def test_exact_range(self) -> None:
        lcp = [0, 1, 3, 0, 0, 2]
        st = build_lcp_sparse_table(lcp)
        assert lcp_query(st, 1, 3) == 1
        assert lcp_query(st, 2, 4) == 0
        assert lcp_query(st, 1, 4) == 0
        assert lcp_query(st, 0, 6) == 0

    def test_empty_range_returns_sentinel(self) -> None:
        st = build_lcp_sparse_table([1, 2, 3])
        v = lcp_query(st, 2, 2)
        assert v >= 1_000_000_000

    def test_identical_values(self) -> None:
        lcp = [7] * 20
        st = build_lcp_sparse_table(lcp)
        assert lcp_query(st, 3, 17) == 7
        assert lcp_query(st, 5, 5) >= 1_000_000_000

    def test_rmq_vs_brute_force(self) -> None:
        random.seed(456)
        for _ in range(30):
            n = random.randint(5, 100)
            a = [random.randint(0, 200) for _ in range(n)]
            st = build_lcp_sparse_table(a)
            left = random.randint(0, n - 1)
            r = random.randint(left + 1, n)
            rmq = lcp_query(st, left, r)
            brute = min(a[left:r])
            assert rmq == brute, f"RMQ mismatch: left={left} r={r} rmq={rmq} brute={brute}"


class TestLcpOfSuffixes:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.s = "banana"
        self.sa = build_sa(self.s)
        self.lcp = build_lcp(self.s, self.sa)
        self.st = build_lcp_sparse_table(self.lcp)
        self.rank = [0] * len(self.s)
        for i, v in enumerate(self.sa):
            self.rank[v] = i
        self.n = len(self.s)

    def _lcp_brute(self, a: int, b: int) -> int:
        k = 0
        s = self.s
        while a + k < self.n and b + k < self.n and s[a + k] == s[b + k]:
            k += 1
        return k

    def test_between_neighbor_suffixes(self) -> None:
        actual = lcp_of_suffixes(self.sa, self.rank, self.st, 1, 3)
        assert actual == 3

    def test_between_nonadjacent_suffixes(self) -> None:
        actual = lcp_of_suffixes(self.sa, self.rank, self.st, 0, 3)
        expected = self._lcp_brute(0, 3)
        assert actual == expected

    def test_same_position_sentinel(self) -> None:
        v = lcp_of_suffixes(self.sa, self.rank, self.st, 2, 2)
        assert v >= 1_000_000_000

    def test_brute_force_consistency(self) -> None:
        random.seed(789)
        for _ in range(30):
            n = random.randint(2, 80)
            s = "".join(random.choice("acgt") for _ in range(n))
            sa = build_sa(s)
            lcp = build_lcp(s, sa)
            st = build_lcp_sparse_table(lcp)
            rank = [0] * n
            for i, v in enumerate(sa):
                rank[v] = i
            for __ in range(10):
                a = random.randint(0, n - 1)
                b = random.randint(0, n - 1)
                rmq_val = lcp_of_suffixes(sa, rank, st, a, b)
                brute_val = 0
                k = 0
                if a != b:
                    while a + k < n and b + k < n and s[a + k] == s[b + k]:
                        k += 1
                    brute_val = k
                if a == b:
                    assert rmq_val >= 1_000_000_000
                else:
                    assert rmq_val == brute_val, f"LCP mismatch a={a} b={b} rmq={rmq_val} brute={brute_val} s={s!r}"


class TestBuildSaLcpRmq:
    def test_convenience_empty(self) -> None:
        sa, lcp, st = build_sa_lcp_rmq("")
        assert sa == []
        assert lcp == []
        assert st == [[0]]

    def test_convenience_banana(self) -> None:
        sa, lcp, st = build_sa_lcp_rmq("banana")
        assert sa == [5, 3, 1, 0, 4, 2]
        assert lcp == [0, 1, 3, 0, 0, 2]
        assert lcp_query(st, 1, 3) == 1

    def test_roundtrip_lrm_via_convenience(self) -> None:
        s = "mississippi"
        sa, lcp, _st = build_sa_lcp_rmq(s)
        length, a, b = longest_repeated_substring(s, sa, lcp)
        assert length == 4
        assert a != b
        assert s[a : a + length] == s[b : b + length]
