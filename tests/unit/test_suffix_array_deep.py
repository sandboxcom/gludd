"""Deep suffix array and LCP array tests.
SA-IS construction, Kasai LCP, binary search, edge cases.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.suffix_array import (
    build_lcp,
    build_sa,
    sa_contains,
    sa_find_all,
    sa_lower_bound,
    sa_upper_bound,
)


class TestBuildSA:
    def test_empty(self) -> None:
        assert build_sa("") == []

    def test_single_char(self) -> None:
        assert build_sa("a") == [0]

    def test_identical_chars(self) -> None:
        assert build_sa("aaaa") == [3, 2, 1, 0]

    def test_two_chars(self) -> None:
        assert build_sa("ba") == [1, 0]

    def test_two_chars_same(self) -> None:
        assert build_sa("aa") == [1, 0]

    def test_banana(self) -> None:
        sa = build_sa("banana")
        expected = [5, 3, 1, 0, 4, 2]
        assert sa == expected

    def test_mississippi(self) -> None:
        sa = build_sa("mississippi")
        expected = [10, 7, 4, 1, 0, 9, 8, 6, 3, 5, 2]
        assert sa == expected

    def test_long_distinct(self) -> None:
        s = "abcdefghijklmnopqrstuvwxyz"
        sa = build_sa(s)
        assert sa == list(range(len(s)))

    def test_numeric_list(self) -> None:
        sa = build_sa([2, 1, 2, 1])
        assert sa == [3, 1, 2, 0]

    def test_abaababa(self) -> None:
        sa = build_sa("abaababa")
        expected = [7, 2, 5, 0, 3, 6, 1, 4]
        assert sa == expected

    def test_repeated_pattern(self) -> None:
        sa = build_sa("abcabcabc")
        n = len("abcabcabc")
        assert len(sa) == n
        assert set(sa) == set(range(n))


class TestBuildLCP:
    def test_empty(self) -> None:
        sa = build_sa("")
        assert build_lcp("", sa) == []

    def test_identical_chars_lcp(self) -> None:
        s = "aaaa"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        assert lcp == [0, 1, 2, 3]

    def test_banana_lcp(self) -> None:
        s = "banana"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        assert lcp == [0, 1, 3, 0, 0, 2]

    def test_mississippi_lcp(self) -> None:
        s = "mississippi"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        expected = [0, 1, 1, 4, 0, 0, 1, 0, 2, 1, 3]
        assert lcp == expected

    def test_lcp_distinct_chars(self) -> None:
        s = "abcdefg"
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        assert lcp == [0] * len(sa)


class TestBinarySearch:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.text = "banana"
        self.sa = build_sa(self.text)

    def test_lower_bound_exact_match(self) -> None:
        lo = sa_lower_bound(self.sa, self.text, "ana")
        assert lo == 1  # sa[1]=3 -> "ana", sa[2]=1 -> "anana"

    def test_upper_bound_exact_match(self) -> None:
        hi = sa_upper_bound(self.sa, self.text, "ana")
        assert hi == 3

    def test_lower_bound_no_match(self) -> None:
        lo = sa_lower_bound(self.sa, self.text, "z")
        assert lo == len(self.sa)

    def test_lower_bound_prefix(self) -> None:
        lo = sa_lower_bound(self.sa, self.text, "ba")
        assert lo == 3  # sa[3]=0 -> "banana"

    def test_upper_bound_no_match(self) -> None:
        hi = sa_upper_bound(self.sa, self.text, "z")
        assert hi == len(self.sa)

    def test_find_all_exact(self) -> None:
        lo, hi = sa_find_all(self.sa, self.text, "ana")
        assert (lo, hi) == (1, 3)
        matches = [self.sa[i] for i in range(lo, hi)]
        assert all(self.text[p:].startswith("ana") for p in matches)

    def test_find_all_no_match(self) -> None:
        lo, hi = sa_find_all(self.sa, self.text, "xyz")
        assert lo == hi

    def test_contains_true(self) -> None:
        assert sa_contains(self.sa, self.text, "ban")

    def test_contains_false(self) -> None:
        assert not sa_contains(self.sa, self.text, "zzz")

    def test_contains_empty_pattern(self) -> None:
        assert sa_contains(self.sa, self.text, "")


class TestConsistency:
    def test_sa_length_matches_input(self) -> None:
        for s in ["a", "ab", "abc", "mississippi", "banana", "aaaa", ""]:
            sa = build_sa(s)
            assert len(sa) == len(s)

    def test_sa_is_permutation(self) -> None:
        for s in ["a", "ab", "abc", "mississippi", "banana", "aaaa"]:
            sa = build_sa(s)
            assert sorted(sa) == list(range(len(s)))

    def test_sa_sorted(self) -> None:
        for s in ["abracadabra", "mississippi", "banana"]:
            sa = build_sa(s)
            for i in range(len(sa) - 1):
                assert s[sa[i] :] <= s[sa[i + 1] :]

    def test_lcp_consistent_with_sa(self) -> None:
        for s in ["abracadabra", "mississippi", "banana", "a", "ab"]:
            sa = build_sa(s)
            lcp = build_lcp(s, sa)
            for i in range(1, len(sa)):
                a = s[sa[i - 1] :]
                b = s[sa[i] :]
                k = 0
                while k < len(a) and k < len(b) and a[k] == b[k]:
                    k += 1
                assert lcp[i] == k

    def test_lcp_length_matches_sa(self) -> None:
        for s in ["banana", "mississippi", "abcabc", ""]:
            sa = build_sa(s)
            lcp = build_lcp(s, sa)
            assert len(lcp) == len(sa)

    def test_large_random_consistency(self) -> None:
        import random

        random.seed(42)
        s = "".join(random.choice("acgt") for _ in range(200))
        sa = build_sa(s)
        lcp = build_lcp(s, sa)
        assert sorted(sa) == list(range(len(s)))
        for i in range(1, len(sa)):
            a = s[sa[i - 1] :]
            b = s[sa[i] :]
            k = 0
            while k < len(a) and k < len(b) and a[k] == b[k]:
                k += 1
            assert lcp[i] == k

    def test_find_all_consistent(self) -> None:
        s = "abracadabra"
        sa = build_sa(s)
        for pat in ["a", "bra", "cad", "ab", "ra", "x", ""]:
            lo, hi = sa_find_all(sa, s, pat)
            for i in range(lo):
                assert not s[sa[i] :].startswith(pat)
            for i in range(lo, hi):
                assert s[sa[i] :].startswith(pat)
            for i in range(hi, len(sa)):
                assert not s[sa[i] :].startswith(pat)

    def test_single_char_all_occurrences(self) -> None:
        s = "mississippi"
        sa = build_sa(s)
        lo, hi = sa_find_all(sa, s, "s")
        assert lo < hi
        matches = sorted(sa[lo:hi])
        expected = [i for i, c in enumerate(s) if c == "s"]
        assert matches == expected
