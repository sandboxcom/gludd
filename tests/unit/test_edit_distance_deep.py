"""Deep tests for edit-distance and sequence-alignment algorithms.

Covers Levenshtein, Damerau-Levenshtein (OSA), Needleman-Wunsch,
Smith-Waterman, Hamming, and Jaro-Winkler.  ≥15 tests per algorithm.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.edit_distance import (
    damerau_levenshtein,
    hamming,
    jaro_similarity,
    jaro_winkler,
    levenshtein,
    needleman_wunsch,
    smith_waterman,
)

# ── Levenshtein ──────────────────────────────────────────────────────────


class TestLevenshtein:
    def test_both_empty(self) -> None:
        assert levenshtein("", "") == 0

    def test_first_empty(self) -> None:
        assert levenshtein("", "abc") == 3

    def test_second_empty(self) -> None:
        assert levenshtein("abc", "") == 3

    def test_single_insert(self) -> None:
        assert levenshtein("a", "ab") == 1

    def test_single_delete(self) -> None:
        assert levenshtein("ab", "a") == 1

    def test_single_substitute(self) -> None:
        assert levenshtein("a", "b") == 1

    def test_kitten_sitting(self) -> None:
        assert levenshtein("kitten", "sitting") == 3

    def test_sunday_saturday(self) -> None:
        assert levenshtein("sunday", "saturday") == 3

    def test_identical(self) -> None:
        assert levenshtein("algorithm", "algorithm") == 0

    def test_completely_different(self) -> None:
        assert levenshtein("abc", "xyz") == 3

    def test_symmetry(self) -> None:
        a, b = "intention", "execution"
        assert levenshtein(a, b) == levenshtein(b, a)

    def test_symmetry_random(self) -> None:
        pairs = [("hello", "world"), ("python", "pytorch"), ("abc", "cba")]
        for a, b in pairs:
            assert levenshtein(a, b) == levenshtein(b, a)

    def test_triangle_inequality(self) -> None:
        a, b, c = "abc", "adc", "adx"
        assert levenshtein(a, c) <= levenshtein(a, b) + levenshtein(b, c)

    def test_subsequence(self) -> None:
        assert levenshtein("abc", "abcdef") == 3

    def test_one_char_substitution_end(self) -> None:
        assert levenshtein("flaw", "flat") == 1

    def test_unicode(self) -> None:
        assert levenshtein("café", "cafe") == 1

    def test_repeated_chars(self) -> None:
        assert levenshtein("aaaa", "aaab") == 1

    def test_long_input(self) -> None:
        a = "a" * 5000
        b = "b" * 5000
        assert levenshtein(a, b) == 5000

    def test_max_dist_is_length(self) -> None:
        assert levenshtein("hello", "world") <= max(len("hello"), len("world"))


# ── Damerau-Levenshtein ──────────────────────────────────────────────────


class TestDamerauLevenshtein:
    def test_both_empty(self) -> None:
        assert damerau_levenshtein("", "") == 0

    def test_first_empty(self) -> None:
        assert damerau_levenshtein("", "abc") == 3

    def test_second_empty(self) -> None:
        assert damerau_levenshtein("abc", "") == 3

    def test_single_transposition(self) -> None:
        assert damerau_levenshtein("ab", "ba") == 1

    def test_transposition_levenshtein_2(self) -> None:
        assert levenshtein("ab", "ba") == 2
        assert damerau_levenshtein("ab", "ba") == 1

    def test_ca_ac(self) -> None:
        assert damerau_levenshtein("ca", "ac") == 1

    def test_no_transposition_needed(self) -> None:
        assert damerau_levenshtein("kitten", "sitting") == 3

    def test_transposition_at_start(self) -> None:
        assert damerau_levenshtein("abcd", "bacd") == 1

    def test_transposition_at_end(self) -> None:
        assert damerau_levenshtein("abcd", "abdc") == 1

    def test_multiple_transpositions(self) -> None:
        assert damerau_levenshtein("abcdef", "badcfe") == 3

    def test_identical(self) -> None:
        assert damerau_levenshtein("hello", "hello") == 0

    def test_symmetry(self) -> None:
        a, b = "teh", "the"
        assert damerau_levenshtein(a, b) == damerau_levenshtein(b, a)

    def test_osa_no_double_swap(self) -> None:
        assert damerau_levenshtein("abc", "cba") == 2

    def test_long_transposition_chain(self) -> None:
        assert damerau_levenshtein("abcde", "bacde") == 1

    def test_substitution_vs_transposition(self) -> None:
        assert damerau_levenshtein("cat", "bat") == 1

    def test_unicode_transposition(self) -> None:
        assert damerau_levenshtein("café", "acfé") == 1  # single transposition c<->a

    def test_large_input(self) -> None:
        a = "ab" * 2500
        b = "ba" * 2500
        d = damerau_levenshtein(a, b)
        assert 0 <= d <= len(a)


# ── Needleman-Wunsch ─────────────────────────────────────────────────────


class TestNeedlemanWunsch:
    def test_both_empty(self) -> None:
        score, aa, ab = needleman_wunsch("", "")
        assert score == 0
        assert aa == ""
        assert ab == ""

    def test_first_empty(self) -> None:
        score, aa, ab = needleman_wunsch("", "ABC")
        assert score == -6
        assert aa == "---"
        assert ab == "ABC"

    def test_second_empty(self) -> None:
        score, aa, ab = needleman_wunsch("ABC", "")
        assert score == -6
        assert aa == "ABC"
        assert ab == "---"

    def test_identical(self) -> None:
        score, aa, ab = needleman_wunsch("ACGT", "ACGT")
        assert score == 4
        assert aa == "ACGT"
        assert ab == "ACGT"

    def test_single_mismatch(self) -> None:
        score, _aa, _ab = needleman_wunsch("ACGT", "ACGA")
        assert score == 2

    def test_gap_opening(self) -> None:
        _score, aa, ab = needleman_wunsch("GCAT", "GAT")
        assert "-" in aa or "-" in ab

    def test_traceback_no_gaps(self) -> None:
        _, aa, ab = needleman_wunsch("ABC", "ABC")
        assert "-" not in aa
        assert "-" not in ab

    def test_traceback_has_gaps(self) -> None:
        _, aa, ab = needleman_wunsch("GCAT", "GAT")
        assert len(aa) == len(ab)

    def test_alignment_length(self) -> None:
        for a, b in [("ABC", "ABD"), ("HELLO", "HELPO"), ("XYZ", "XZ")]:
            _, aa, ab = needleman_wunsch(a, b)
            assert len(aa) == len(ab)

    def test_custom_match_score(self) -> None:
        score, _, _ = needleman_wunsch("CAT", "CAT", match=5, mismatch=-2, gap=-1)
        assert score == 15

    def test_custom_gap_penalty(self) -> None:
        score_high, _, _ = needleman_wunsch("GCAT", "GCT", gap=-1)
        score_low, _, _ = needleman_wunsch("GCAT", "GCT", gap=-5)
        assert score_high > score_low

    def test_score_is_int(self) -> None:
        score, _, _ = needleman_wunsch("HELLO", "HALLO")
        assert isinstance(score, int)

    def test_dna_alignment(self) -> None:
        score, aa, ab = needleman_wunsch("GATTACA", "GCATGCU")
        assert isinstance(score, int)
        assert len(aa) == len(ab)

    def test_leading_gap(self) -> None:
        _, aa, ab = needleman_wunsch("XYZ", "ABCXYZ")
        assert len(aa) == len(ab)

    def test_trailing_gap(self) -> None:
        _, aa, ab = needleman_wunsch("ABCXYZ", "ABC")
        assert len(aa) == len(ab)

    def test_unicode_alignment(self) -> None:
        score, aa, ab = needleman_wunsch("café", "cafe")
        assert isinstance(score, int)
        assert len(aa) == len(ab)

    def test_pure_gap(self) -> None:
        score, aa, ab = needleman_wunsch("A", "G")
        assert score == -1  # mismatch
        assert aa == "A" or aa == "-"
        assert ab == "G" or ab == "-"


# ── Smith-Waterman ───────────────────────────────────────────────────────


class TestSmithWaterman:
    def test_both_empty(self) -> None:
        score, aa, ab = smith_waterman("", "")
        assert score == 0
        assert aa == ""
        assert ab == ""

    def test_first_empty(self) -> None:
        score, aa, ab = smith_waterman("", "ABC")
        assert score == 0
        assert aa == ""
        assert ab == ""

    def test_second_empty(self) -> None:
        score, aa, ab = smith_waterman("ABC", "")
        assert score == 0
        assert aa == ""
        assert ab == ""

    def test_identical_segment(self) -> None:
        score, aa, ab = smith_waterman("ACGT", "ACGT")
        assert score == 8
        assert aa == "ACGT"
        assert ab == "ACGT"

    def test_local_match_embedded(self) -> None:
        score, aa, ab = smith_waterman("GGGACGTCCC", "ACGT")
        assert score == 8
        assert aa == "ACGT"
        assert ab == "ACGT"

    def test_traceback_no_gaps(self) -> None:
        _, aa, ab = smith_waterman("ACGT", "ACGT")
        assert "-" not in aa
        assert "-" not in ab

    def test_traceback_length_match(self) -> None:
        _, aa, ab = smith_waterman("XXHELLOYY", "HELLO")
        assert len(aa) == len(ab)

    def test_custom_scoring(self) -> None:
        score, _, _ = smith_waterman("CAT", "CAT", match=5, mismatch=-2, gap=-1)
        assert score == 15

    def test_score_non_negative(self) -> None:
        for a, b in [("ACGT", "TTTT"), ("XXX", "YYY"), ("ABC", "DEF")]:
            score, _, _ = smith_waterman(a, b)
            assert score >= 0

    def test_local_traceback_is_substring(self) -> None:
        a = "PREFIXHELLOWORLDSUFFIX"
        b = "HELLOWORLD"
        _, aa, ab = smith_waterman(a, b)
        assert aa in a or aa == ""
        assert ab in b or ab == ""

    def test_no_common_chars(self) -> None:
        score, aa, ab = smith_waterman("AAA", "TTT")
        assert score == 0
        assert aa == ""
        assert ab == ""

    def test_score_is_int(self) -> None:
        score, _, _ = smith_waterman("GATTACA", "GCATGCU")
        assert isinstance(score, int)

    def test_partial_overlap(self) -> None:
        score, _aa, _ab = smith_waterman("ABCDEF", "CDEFGH")
        assert score >= 6  # at least "CDEF" with match=2

    def test_unicode(self) -> None:
        score, aa, ab = smith_waterman("café", "cafe")
        assert isinstance(score, int)
        assert len(aa) == len(ab)

    def test_long_identical_substring(self) -> None:
        prefix = "x" * 100
        a = prefix + "TARGET" + prefix
        b = "TARGET"
        score, aa, ab = smith_waterman(a, b)
        assert aa == "TARGET"
        assert ab == "TARGET"
        assert score == 12  # 6 * match=2


# ── Hamming ──────────────────────────────────────────────────────────────


class TestHamming:
    def test_both_empty(self) -> None:
        assert hamming("", "") == 0

    def test_identical(self) -> None:
        assert hamming("karolin", "karolin") == 0

    def test_single_diff(self) -> None:
        assert hamming("karolin", "kathrin") == 3

    def test_all_different(self) -> None:
        assert hamming("1010", "0101") == 4

    def test_known_vector_1(self) -> None:
        assert hamming("toned", "roses") == 3

    def test_known_vector_2(self) -> None:
        assert hamming("2173896", "2233796") == 3

    def test_raises_on_unequal_length(self) -> None:
        with pytest.raises(ValueError, match="equal-length"):
            hamming("abc", "ab")

    def test_raises_on_second_longer(self) -> None:
        with pytest.raises(ValueError, match="equal-length"):
            hamming("ab", "abc")

    def test_raises_empty_vs_nonempty(self) -> None:
        with pytest.raises(ValueError, match="equal-length"):
            hamming("", "a")

    def test_single_char_equal(self) -> None:
        assert hamming("a", "a") == 0

    def test_single_char_diff(self) -> None:
        assert hamming("a", "b") == 1

    def test_unicode(self) -> None:
        assert hamming("café", "cafe") == 1

    def test_long_string(self) -> None:
        a = "a" * 10000
        b = "b" * 10000
        assert hamming(a, b) == 10000

    def test_symmetry(self) -> None:
        a, b = "1011001", "1001001"
        assert hamming(a, b) == hamming(b, a)

    def test_dna_strings(self) -> None:
        assert hamming("GAGCCTACTAACGGGAT", "CATCGTAATGACGGCCT") == 7


# ── Jaro similarity ──────────────────────────────────────────────────────


class TestJaroSimilarity:
    def test_both_empty(self) -> None:
        assert jaro_similarity("", "") == 1.0

    def test_first_empty(self) -> None:
        assert jaro_similarity("", "abc") == 0.0

    def test_second_empty(self) -> None:
        assert jaro_similarity("abc", "") == 0.0

    def test_identical(self) -> None:
        assert jaro_similarity("hello", "hello") == 1.0

    def test_completely_different(self) -> None:
        assert jaro_similarity("abc", "xyz") == 0.0

    def test_known_martha_marhta(self) -> None:
        assert jaro_similarity("MARTHA", "MARHTA") == pytest.approx(0.94444, abs=1e-4)

    def test_known_dwayne_duane(self) -> None:
        assert jaro_similarity("DWAYNE", "DUANE") == pytest.approx(0.82222, abs=1e-4)

    def test_known_dixon_dicksonx(self) -> None:
        assert jaro_similarity("DIXON", "DICKSONX") == pytest.approx(0.76667, abs=1e-4)

    def test_symmetry(self) -> None:
        a, b = "CRATE", "TRACE"
        assert jaro_similarity(a, b) == pytest.approx(jaro_similarity(b, a), abs=1e-10)

    def test_range_zero_to_one(self) -> None:
        pairs = [("hello", "hallo"), ("test", "tent"), ("abc", "cba")]
        for a, b in pairs:
            assert 0.0 <= jaro_similarity(a, b) <= 1.0

    def test_single_char_match(self) -> None:
        assert jaro_similarity("a", "a") == 1.0

    def test_single_char_mismatch(self) -> None:
        assert jaro_similarity("a", "b") == 0.0

    def test_shared_prefix_no_effect(self) -> None:
        s1 = jaro_similarity("abcdef", "abcxyz")
        assert 0.0 <= s1 <= 1.0

    def test_transposition_matters(self) -> None:
        assert jaro_similarity("abcde", "abced") < 1.0

    def test_long_strings(self) -> None:
        a = "a" * 1000 + "b"
        b = "a" * 1000 + "c"
        s = jaro_similarity(a, b)
        assert 0.0 <= s <= 1.0

    def test_unicode(self) -> None:
        s = jaro_similarity("café", "cafe")
        assert 0.0 <= s <= 1.0


# ── Jaro-Winkler ─────────────────────────────────────────────────────────


class TestJaroWinkler:
    def test_both_empty(self) -> None:
        assert jaro_winkler("", "") == 1.0

    def test_first_empty(self) -> None:
        assert jaro_winkler("", "abc") == 0.0

    def test_second_empty(self) -> None:
        assert jaro_winkler("abc", "") == 0.0

    def test_identical(self) -> None:
        assert jaro_winkler("hello", "hello") == 1.0

    def test_known_vector(self) -> None:
        assert jaro_winkler("MARTHA", "MARHTA") == pytest.approx(0.96111, abs=1e-4)

    def test_prefix_boost(self) -> None:
        jw = jaro_winkler("abcdef", "abcxyz")
        j = jaro_similarity("abcdef", "abcxyz")
        assert jw >= j

    def test_boost_capped_at_one(self) -> None:
        s = jaro_winkler("verylongprefix", "verylongprefix")
        assert s <= 1.0

    def test_custom_scaling(self) -> None:
        s_default = jaro_winkler("abcdefg", "abcdxyz", scaling=0.1)
        s_high = jaro_winkler("abcdefg", "abcdxyz", scaling=0.2)
        assert s_high > s_default

    def test_custom_prefix_len(self) -> None:
        s_4 = jaro_winkler("abcde", "abcfg", prefix_len=4)
        s_2 = jaro_winkler("abcde", "abcfg", prefix_len=2)
        assert s_4 >= s_2

    def test_no_boost_below_0_7(self) -> None:
        s = jaro_winkler("abc", "xyz")
        assert s < 0.7

    def test_jaro_winkler_range(self) -> None:
        pairs = [("hello", "hallo"), ("test", "tent"), ("crate", "trace")]
        for a, b in pairs:
            assert 0.0 <= jaro_winkler(a, b) <= 1.0

    def test_known_dwayne_duane(self) -> None:
        assert jaro_winkler("DWAYNE", "DUANE") == pytest.approx(0.84000, abs=1e-4)

    def test_known_dixon_dicksonx(self) -> None:
        assert jaro_winkler("DIXON", "DICKSONX") == pytest.approx(0.81333, abs=1e-4)

    def test_max_boost(self) -> None:
        s = jaro_winkler("abcd", "abef", scaling=0.25, prefix_len=2)
        assert s <= 1.0

    def test_unicode_with_prefix(self) -> None:
        s = jaro_winkler("café", "cafe")
        assert 0.0 <= s <= 1.0

    def test_long_prefix_boost(self) -> None:
        a = "abcdefghXXXX"
        b = "abcdefghYYYY"
        s = jaro_winkler(a, b)
        assert s > 0.8
