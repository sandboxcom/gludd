"""Deep tests for KMP prefix-function, pattern matching, Z-array,
and Z-based matching. Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.kmp import (
    kmp_search,
    prefix_function,
    z_array,
    z_search,
)

# ── prefix_function ─────────────────────────────────────────────────


class TestPrefixFunction:
    def test_empty_string(self) -> None:
        assert prefix_function("") == []

    def test_single_char(self) -> None:
        assert prefix_function("a") == [0]

    def test_no_prefix_suffix_overlap(self) -> None:
        assert prefix_function("abcde") == [0, 0, 0, 0, 0]

    def test_all_same_char(self) -> None:
        assert prefix_function("aaaaa") == [0, 1, 2, 3, 4]

    def test_partial_overlap(self) -> None:
        assert prefix_function("aabaabaaa") == [0, 1, 0, 1, 2, 3, 4, 5, 2]

    def test_classic_abacab(self) -> None:
        assert prefix_function("abacab") == [0, 0, 1, 0, 1, 2]


# ── kmp_search ──────────────────────────────────────────────────────


class TestKMPSearch:
    def test_empty_text(self) -> None:
        assert kmp_search("", "abc") == []

    def test_empty_pattern(self) -> None:
        assert kmp_search("hello", "") == []

    def test_pattern_longer_than_text(self) -> None:
        assert kmp_search("ab", "abc") == []

    def test_single_char_found(self) -> None:
        assert kmp_search("x", "x") == [0]

    def test_single_char_not_found(self) -> None:
        assert kmp_search("x", "y") == []

    def test_exact_match(self) -> None:
        assert kmp_search("hello", "hello") == [0]

    def test_no_match(self) -> None:
        assert kmp_search("abcabc", "xyz") == []

    def test_multiple_non_overlapping(self) -> None:
        assert kmp_search("abababab", "ab") == [0, 2, 4, 6]

    def test_overlapping_pattern(self) -> None:
        assert kmp_search("aaaaa", "aa") == [0, 1, 2, 3]

    def test_pattern_at_end(self) -> None:
        assert kmp_search("xxabc", "abc") == [2]

    def test_pattern_at_start(self) -> None:
        assert kmp_search("abcxx", "abc") == [0]

    def test_large_input_correctness(self) -> None:
        text = "ab" * 5000 + "target" + "ab" * 5000
        assert kmp_search(text, "target") == [10000]

    def test_repeated_prefix_matches(self) -> None:
        assert kmp_search("abcababcabc", "abcabc") == [5]

    def test_unicode_text(self) -> None:
        assert kmp_search("café☕café", "fé") == [2, 7]

    def test_return_type_is_list(self) -> None:
        result = kmp_search("abcabc", "abc")
        assert isinstance(result, list)
        assert result == [0, 3]


# ── z_array ─────────────────────────────────────────────────────────


class TestZArray:
    def test_empty_string(self) -> None:
        assert z_array("") == []

    def test_single_char(self) -> None:
        assert z_array("a") == [0]

    def test_no_repeated_prefix(self) -> None:
        assert z_array("abcde") == [0, 0, 0, 0, 0]

    def test_all_same_char(self) -> None:
        assert z_array("aaaaa") == [0, 4, 3, 2, 1]

    def test_classic_abacab(self) -> None:
        assert z_array("abacab") == [0, 0, 1, 0, 2, 0]

    def test_aab(self) -> None:
        assert z_array("aab") == [0, 1, 0]

    def test_aaab(self) -> None:
        assert z_array("aaab") == [0, 2, 1, 0]

    def test_ababab(self) -> None:
        assert z_array("ababab") == [0, 0, 4, 0, 2, 0]


# ── z_search ────────────────────────────────────────────────────────


class TestZSearch:
    def test_empty_text(self) -> None:
        assert z_search("", "abc") == []

    def test_empty_pattern(self) -> None:
        assert z_search("hello", "") == []

    def test_pattern_longer_than_text(self) -> None:
        assert z_search("ab", "abc") == []

    def test_single_char_found(self) -> None:
        assert z_search("x", "x") == [0]

    def test_single_char_not_found(self) -> None:
        assert z_search("x", "y") == []

    def test_exact_match(self) -> None:
        assert z_search("hello", "hello") == [0]

    def test_multiple_occurrences(self) -> None:
        assert z_search("ababab", "ab") == [0, 2, 4]

    def test_overlapping_pattern(self) -> None:
        assert z_search("aaaaa", "aa") == [0, 1, 2, 3]

    def test_pattern_with_sentinel_character(self) -> None:
        assert z_search("ab$ab", "ab") == [0, 3]

    def test_large_correctness(self) -> None:
        text = "xy" * 3000 + "needle" + "xy" * 3000
        assert z_search(text, "needle") == [6000]

    def test_unicode(self) -> None:
        assert z_search("αβγδαβ", "αβ") == [0, 4]

    def test_return_type_is_list(self) -> None:
        result = z_search("abcabc", "abc")
        assert isinstance(result, list)
        assert result == [0, 3]


# ── cross-consistency ───────────────────────────────────────────────


class TestCrossConsistency:
    """KMP and Z-search must agree on all findings."""

    def test_agree_empty(self) -> None:
        assert kmp_search("", "a") == z_search("", "a") == []

    def test_agree_simple(self) -> None:
        assert kmp_search("abcabc", "abc") == z_search("abcabc", "abc")

    def test_agree_overlapping(self) -> None:
        assert kmp_search("aaaa", "aa") == z_search("aaaa", "aa")

    def test_agree_large(self) -> None:
        text = ".".join(str(i) for i in range(200))
        assert kmp_search(text, "42") == z_search(text, "42")
