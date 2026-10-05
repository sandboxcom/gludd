"""Deep tests for Manacher's algorithm — odd/even radii, longest palindrome,
palindrome count, is_palindrome O(1) queries, and unified computation.
Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.manacher import (
    _manacher_unified,
    count_palindromes,
    is_palindrome,
    longest_palindrome,
    manacher_even,
    manacher_odd,
)

# ── manacher_odd ──────────────────────────────────────────────────────


class TestManacherOdd:
    def test_empty(self) -> None:
        assert manacher_odd("") == []

    def test_single_char(self) -> None:
        assert manacher_odd("a") == [0]

    def test_all_same(self) -> None:
        assert manacher_odd("aaaaa") == [0, 1, 2, 1, 0]

    def test_no_palindrome_beyond_one(self) -> None:
        assert manacher_odd("abcdef") == [0, 0, 0, 0, 0, 0]

    def test_classic_abacaba(self) -> None:
        assert manacher_odd("abacaba") == [0, 1, 0, 3, 0, 1, 0]

    def test_symmetric_around_center(self) -> None:
        assert manacher_odd("xyzzyx") == [0, 0, 0, 0, 0, 0]


# ── manacher_even ─────────────────────────────────────────────────────


class TestManacherEven:
    def test_empty(self) -> None:
        assert manacher_even("") == []

    def test_single_char(self) -> None:
        assert manacher_even("a") == [0]

    def test_double_char_same(self) -> None:
        assert manacher_even("aa") == [1, 0]

    def test_classic_abba(self) -> None:
        assert manacher_even("abba") == [0, 2, 0, 0]

    def test_all_same_even_length(self) -> None:
        assert manacher_even("aaaa") == [1, 2, 1, 0]

    def test_alternating(self) -> None:
        assert manacher_even("ababab") == [0, 0, 0, 0, 0, 0]


# ── longest_palindrome ───────────────────────────────────────────────


class TestLongestPalindrome:
    def test_empty(self) -> None:
        assert longest_palindrome("") == ""

    def test_single_char(self) -> None:
        assert longest_palindrome("x") == "x"

    def test_all_same(self) -> None:
        assert longest_palindrome("aaaaa") == "aaaaa"

    def test_odd_center_longest(self) -> None:
        assert longest_palindrome("abacaba") == "abacaba"

    def test_even_center_longest(self) -> None:
        assert longest_palindrome("cbbd") == "bb"

    def test_multiple_same_length_returns_first(self) -> None:
        result = longest_palindrome("aacbbd")
        assert len(result) == 2
        assert result in ("aa", "bb")

    def test_unicode(self) -> None:
        assert longest_palindrome("αβαγαβα") == "αβαγαβα"

    def test_long_input(self) -> None:
        s = "a" * 5000 + "b" + "a" * 5000
        assert len(longest_palindrome(s)) == 10001


# ── count_palindromes ────────────────────────────────────────────────


class TestCountPalindromes:
    def test_empty(self) -> None:
        assert count_palindromes("") == 0

    def test_single_char(self) -> None:
        assert count_palindromes("a") == 1

    def test_all_same_three(self) -> None:
        assert count_palindromes("aaa") == 6

    def test_no_repeated(self) -> None:
        assert count_palindromes("abcdef") == 6

    def test_mixed(self) -> None:
        assert count_palindromes("abba") == 6


# ── is_palindrome ────────────────────────────────────────────────────


class TestIsPalindrome:
    def test_empty_slice(self) -> None:
        assert is_palindrome("abc", 1, 1) is True

    def test_single_char_slice(self) -> None:
        assert is_palindrome("abc", 1, 2) is True

    def test_palindromic_slice(self) -> None:
        assert is_palindrome("abacaba", 0, 7) is True

    def test_non_palindromic_slice(self) -> None:
        assert is_palindrome("abcdef", 0, 3) is False

    def test_even_length_palindrome_slice(self) -> None:
        assert is_palindrome("abba", 0, 4) is True

    def test_even_length_non_palindrome_slice(self) -> None:
        assert is_palindrome("abcd", 0, 4) is False


# ── _manacher_unified ────────────────────────────────────────────────


class TestManacherUnified:
    def test_empty(self) -> None:
        d1, d2 = _manacher_unified("")
        assert d1 == [] and d2 == []

    def test_single_char(self) -> None:
        d1, d2 = _manacher_unified("a")
        assert d1 == [0] and d2 == [0]

    def test_agrees_with_individual(self) -> None:
        s = "abacabadefed"
        d1_u, d2_u = _manacher_unified(s)
        assert d1_u == manacher_odd(s)
        assert d2_u == manacher_even(s)

    def test_long_agrees_with_individual(self) -> None:
        s = "x" * 2000 + "aba" + "y" * 2000
        d1_u, d2_u = _manacher_unified(s)
        assert d1_u == manacher_odd(s)
        assert d2_u == manacher_even(s)


# ── cross-consistency ────────────────────────────────────────────────


class TestCrossConsistency:
    def test_longest_is_actually_palindrome(self) -> None:
        s = "forgeeksskeegfor"
        lp = longest_palindrome(s)
        assert lp == lp[::-1]
        assert lp in s

    def test_count_matches_brute_force_small(self) -> None:
        s = "abacab"
        expected = 0
        n = len(s)
        for i in range(n):
            for j in range(i + 1, n + 1):
                sub = s[i:j]
                if sub == sub[::-1]:
                    expected += 1
        assert count_palindromes(s) == expected

    def test_longest_against_brute_force(self) -> None:
        s = "abaxyzzyxf"
        expected = ""
        n = len(s)
        for i in range(n):
            for j in range(i + 1, n + 1):
                sub = s[i:j]
                if sub == sub[::-1] and len(sub) > len(expected):
                    expected = sub
        assert longest_palindrome(s) == expected
