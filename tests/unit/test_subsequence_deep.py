"""Deep tests for subsequence DP algorithms: LCS, LIS (patience sorting),
LRS, SCS, and Needleman-Wunsch sequence alignment.
Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.subsequence import (
    alignment_score,
    lcs_length,
    lis_length,
    longest_common_subsequence,
    longest_increasing_subsequence,
    longest_repeated_subsequence,
    lrs_length,
    needleman_wunsch,
    scs_length,
    shortest_common_supersequence,
)


def _is_subsequence(sub: list, full: list) -> bool:
    it = iter(full)
    return all(c in it for c in sub)


# ── longest_common_subsequence ───────────────────────────────────────


class TestLCS:
    def test_both_empty(self) -> None:
        assert longest_common_subsequence([], []) == []

    def test_one_empty(self) -> None:
        assert longest_common_subsequence([1, 2, 3], []) == []

    def test_no_common(self) -> None:
        assert longest_common_subsequence("abc", "xyz") == []

    def test_exact_match(self) -> None:
        assert longest_common_subsequence("hello", "hello") == list("hello")

    def test_classic(self) -> None:
        assert longest_common_subsequence("ABCDGH", "AEDFHR") == ["A", "D", "H"]

    def test_repeated_chars(self) -> None:
        result = longest_common_subsequence("AGGTAB", "GXTXAYB")
        assert result == ["G", "T", "A", "B"]

    def test_lcs_length_agrees(self) -> None:
        a, b = "XMJYAUZ", "MZJAWXU"
        assert lcs_length(a, b) == len(longest_common_subsequence(a, b))

    def test_numeric_sequences(self) -> None:
        result = longest_common_subsequence([1, 3, 4, 5, 6, 7, 7, 8], [3, 5, 7, 4, 8, 6, 7, 8, 2])
        assert len(result) == 5


# ── longest_increasing_subsequence ───────────────────────────────────


class TestLIS:
    def test_empty(self) -> None:
        assert longest_increasing_subsequence([]) == []

    def test_single(self) -> None:
        assert longest_increasing_subsequence([7]) == [7]

    def test_all_decreasing(self) -> None:
        result = longest_increasing_subsequence([5, 4, 3, 2, 1])
        assert len(result) == 1

    def test_all_increasing(self) -> None:
        assert longest_increasing_subsequence([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]

    def test_classic(self) -> None:
        result = longest_increasing_subsequence([10, 9, 2, 5, 3, 7, 101, 18])
        assert len(result) == 4
        for i in range(1, len(result)):
            assert result[i - 1] < result[i]

    def test_duplicates_not_strictly_increasing(self) -> None:
        result = longest_increasing_subsequence([2, 2, 2, 2])
        assert result == [2]
        assert len(result) == 1

    def test_lis_length(self) -> None:
        assert lis_length([0, 8, 4, 12, 2, 10, 6, 14, 1, 9]) == 4


# ── longest_repeated_subsequence ─────────────────────────────────────


class TestLRS:
    def test_empty_string(self) -> None:
        assert longest_repeated_subsequence("") == ""

    def test_single_char(self) -> None:
        assert longest_repeated_subsequence("a") == ""

    def test_no_repetition(self) -> None:
        assert longest_repeated_subsequence("abcdef") == ""

    def test_repeated_char(self) -> None:
        assert longest_repeated_subsequence("aab") == "a"

    def test_classic(self) -> None:
        assert longest_repeated_subsequence("AABEBCDD") == "ABD"

    def test_all_same(self) -> None:
        result = longest_repeated_subsequence("aaaa")
        assert len(result) >= 1

    def test_lrs_length(self) -> None:
        assert lrs_length("AABEBCDD") == 3


# ── shortest_common_supersequence ───────────────────────────────────


class TestSCS:
    def test_both_empty(self) -> None:
        assert shortest_common_supersequence([], []) == []

    def test_one_empty(self) -> None:
        assert shortest_common_supersequence([1, 2], []) == [1, 2]

    def test_classic(self) -> None:
        result = shortest_common_supersequence("abac", "cab")
        assert _is_subsequence(list("abac"), result)
        assert _is_subsequence(list("cab"), result)
        assert len(result) == scs_length("abac", "cab")

    def test_disjoint(self) -> None:
        result = shortest_common_supersequence("ab", "cd")
        assert _is_subsequence(list("ab"), result)
        assert _is_subsequence(list("cd"), result)

    def test_scs_length_formula(self) -> None:
        a, b = "AGGTAB", "GXTXAYB"
        assert scs_length(a, b) == len(a) + len(b) - lcs_length(a, b)


# ── needleman_wunsch ─────────────────────────────────────────────────


class TestNeedlemanWunsch:
    def test_empty_sequences(self) -> None:
        score, al_a, al_b = needleman_wunsch("", "")
        assert score == 0
        assert al_a == ""
        assert al_b == ""

    def test_one_empty(self) -> None:
        score, al_a, al_b = needleman_wunsch("ABC", "")
        assert score == -3
        assert al_a == "ABC"
        assert al_b == "---"

    def test_identical(self) -> None:
        score, al_a, al_b = needleman_wunsch("GATTACA", "GATTACA")
        assert score == 7
        assert al_a == "GATTACA"
        assert al_b == "GATTACA"

    def test_single_mismatch(self) -> None:
        score, al_a, al_b = needleman_wunsch("CAT", "CGT")
        assert al_a == "CAT"
        assert al_b == "CGT"
        assert score == 2 * 1 + (-1)  # 2 matches, 1 mismatch

    def test_gap_insertion(self) -> None:
        _score, al_a, al_b = needleman_wunsch("GAATTC", "GATTC")
        assert al_a == "GAATTC"
        assert al_b == "G-ATTC" or al_b == "GA-TTC"

    def test_custom_scoring(self) -> None:
        score, _al_a, _al_b = needleman_wunsch("AAAA", "TTTT", match=2, mismatch=-3, gap=-2)
        assert score == 4 * -3  # all mismatches with custom scoring

    def test_alignment_score_convenience(self) -> None:
        assert alignment_score("GATTACA", "GATTACA") == 7
        assert alignment_score("", "") == 0
