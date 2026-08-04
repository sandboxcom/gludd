"""Deep fuzzy matching and similarity tests.

Covers:
- Levenshtein distance (character-level edit distance)
- Jaro-Winkler similarity (prefix-weighted string similarity)
- Trigram similarity (character trigram overlap)
- Soundex matching (phonetic hashing identical codes)
- Metaphone matching (phonetic hashing approximate equality)
- Token sort ratio (bag-of-words intersection / union)

All implementations are stdlib-only or use existing project modules
(phonetic_data). No external fuzzy-matching dependencies required.
"""

from __future__ import annotations

from collections import Counter

import pytest

# ── Pure stdlib algorithm implementations ──────────────────────────────────


def _levenshtein_distance(a: str, b: str) -> int:
    """Classic Wagner-Fischer Levenshtein edit distance."""
    if not a:
        return len(b)
    if not b:
        return len(a)

    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + cost,
            )
        prev = curr
    return prev[m]


def _levenshtein_similarity(a: str, b: str) -> float:
    """Normalised Levenshtein similarity in [0.0, 1.0]."""
    if not a and not b:
        return 1.0
    dist = _levenshtein_distance(a, b)
    return 1.0 - (dist / max(len(a), len(b)))


def _jaro_similarity(a: str, b: str) -> float:
    """Jaro similarity between two strings."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    la, lb = len(a), len(b)
    match_distance = max(la, lb) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    a_matches = [False] * la
    b_matches = [False] * lb
    matches = 0

    for i in range(la):
        start = max(0, i - match_distance)
        end = min(lb, i + match_distance + 1)
        for j in range(start, end):
            if b_matches[j]:
                continue
            if a[i] == b[j]:
                a_matches[i] = True
                b_matches[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(la):
        if not a_matches[i]:
            continue
        while not b_matches[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1

    return (matches / la + matches / lb + (matches - transpositions // 2) / matches) / 3.0


def _jaro_winkler_similarity(a: str, b: str, scaling: float = 0.1) -> float:
    """Jaro-Winkler similarity with prefix bonus."""
    jaro = _jaro_similarity(a, b)
    if jaro < 0.7:
        return jaro

    prefix = 0
    for ca, cb in zip(a.lower(), b.lower(), strict=False):
        if ca == cb:
            prefix += 1
        else:
            break
    prefix = min(prefix, 4)
    return jaro + prefix * scaling * (1.0 - jaro)


def _trigram_set(s: str) -> set[tuple[str, str, str]]:
    """All unique character trigrams from a string, including an extra
    leading-space and trailing-space pad character."""
    p = " " + s.lower() + " "
    n = len(p)
    return {(p[i], p[i + 1], p[i + 2]) for i in range(n - 2)}


def _trigram_similarity(a: str, b: str) -> float:
    """Dice coefficient over character trigram sets."""
    ta = _trigram_set(a)
    tb = _trigram_set(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    return (2.0 * intersection) / (len(ta) + len(tb))


def _soundex_match(a: str, b: str) -> bool:
    """True when two words share the same Soundex code."""
    from general_ludd.language.phonetic_data import compute_soundex

    return compute_soundex(a) == compute_soundex(b)


def _metaphone_match(a: str, b: str) -> bool:
    """True when the primary Metaphone codes of two words agree."""
    from general_ludd.language.phonetic_data import compute_metaphone

    return compute_metaphone(a) == compute_metaphone(b)


def _token_sort_ratio(a: str, b: str) -> float:
    """Case-insensitive token-bag similarity.

    Splits on whitespace, sorts, then computes Jaccard on the bigram
    token union.  Returns 0.0..1.0.
    """

    ta = Counter(tok.lower() for tok in a.split() if tok)
    tb = Counter(tok.lower() for tok in b.split() if tok)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    numerator = sum((ta & tb).values())
    denominator = sum((ta | tb).values())
    return numerator / denominator


# ── 1. Levenshtein distance ────────────────────────────────────────────────


class TestLevenshteinDistance:
    def test_identical_strings(self) -> None:
        assert _levenshtein_distance("hello", "hello") == 0

    def test_single_substitution(self) -> None:
        assert _levenshtein_distance("cat", "bat") == 1

    def test_single_deletion(self) -> None:
        assert _levenshtein_distance("cats", "cat") == 1

    def test_single_insertion(self) -> None:
        assert _levenshtein_distance("cat", "cats") == 1

    def test_complete_mismatch(self) -> None:
        assert _levenshtein_distance("abc", "xyz") == 3

    def test_empty_a(self) -> None:
        assert _levenshtein_distance("", "hello") == 5

    def test_empty_b(self) -> None:
        assert _levenshtein_distance("hello", "") == 5

    def test_both_empty(self) -> None:
        assert _levenshtein_distance("", "") == 0

    def test_kitten_sitting(self) -> None:
        assert _levenshtein_distance("kitten", "sitting") == 3

    def test_similarity_metric(self) -> None:
        assert _levenshtein_similarity("hello", "hello") == 1.0
        assert _levenshtein_similarity("hello", "helo") == pytest.approx(0.8)
        assert _levenshtein_similarity("abc", "xyz") == pytest.approx(0.0)
        assert _levenshtein_similarity("", "") == 1.0
        assert _levenshtein_similarity("a", "") == 0.0


# ── 2. Jaro-Winkler similarity ─────────────────────────────────────────────


class TestJaroWinkler:
    def test_identical(self) -> None:
        assert _jaro_winkler_similarity("hello", "hello") == 1.0

    def test_martha_marhta(self) -> None:
        assert _jaro_similarity("MARTHA", "MARHTA") == pytest.approx(0.9444, abs=1e-3)

    def test_dwayne_duane(self) -> None:
        assert _jaro_similarity("DWAYNE", "DUANE") == pytest.approx(0.8222, abs=1e-3)

    def test_jones_johnson(self) -> None:
        s = _jaro_winkler_similarity("Jones", "Johnson")
        assert 0.6 < s < 0.9

    def test_prefix_bonus_short(self) -> None:
        jw = _jaro_winkler_similarity("martha", "marhta")
        j = _jaro_similarity("martha", "marhta")
        assert jw > j

    def test_no_prefix_bonus_below_threshold(self) -> None:
        a, b = "abcdefgh", "zyxwvuts"
        j = _jaro_similarity(a, b)
        jw = _jaro_winkler_similarity(a, b)
        assert jw == j

    def test_both_empty(self) -> None:
        assert _jaro_winkler_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert _jaro_winkler_similarity("hello", "") == 0.0


# ── 3. Trigram similarity ──────────────────────────────────────────────────


class TestTrigramSimilarity:
    def test_identical(self) -> None:
        assert _trigram_similarity("hello", "hello") == 1.0

    def test_near_miss(self) -> None:
        s = _trigram_similarity("hello", "hallo")
        assert s > 0.3

    def test_complete_mismatch(self) -> None:
        s = _trigram_similarity("abcde", "vwxyz")
        assert s < 0.4

    def test_anagram_low_similarity(self) -> None:
        s = _trigram_similarity("silent", "listen")
        assert s < 0.3

    def test_both_empty(self) -> None:
        assert _trigram_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert _trigram_similarity("hello", "") == 0.0

    def test_case_insensitive(self) -> None:
        assert _trigram_similarity("Hello", "HELLO") == 1.0


# ── 4. Soundex matching ────────────────────────────────────────────────────


class TestSoundexMatching:
    def test_robert_rupert_match(self) -> None:
        assert _soundex_match("Robert", "Rupert")

    def test_washington_code(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex

        assert compute_soundex("Washington") == "W252"

    def test_ashcraft_tymezak_different(self) -> None:
        assert not _soundex_match("Ashcraft", "Tymczak")

    def test_empty_strings(self) -> None:
        assert _soundex_match("", "")

    def test_lee_code_padded(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex

        assert len(compute_soundex("Lee")) == 4
        assert compute_soundex("Lee") == "L000"


# ── 5. Metaphone matching ──────────────────────────────────────────────────


class TestMetaphoneMatching:
    def test_smith_knight_different(self) -> None:
        assert not _metaphone_match("Smith", "Knight")

    def test_initial_gn_dropped(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone

        assert compute_metaphone("Gnome") == "NM"

    def test_initial_kn_dropped(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone

        assert compute_metaphone("Knight") == "NGHT"

    def test_empty_input(self) -> None:
        assert _metaphone_match("", "")

    def test_metaphone_length_capped(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone

        for word in ("Washington", "Philadelphia"):
            assert len(compute_metaphone(word)) <= 4

    def test_double_metaphone_returns_pair(self) -> None:
        from general_ludd.language.phonetic_data import compute_double_metaphone

        primary, alternate = compute_double_metaphone("Smith")
        assert isinstance(primary, str)
        assert isinstance(alternate, str)

    def test_sch_prefix(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone

        assert compute_metaphone("School") == "SKL"


# ── 6. Token sort ratio ────────────────────────────────────────────────────


class TestTokenSortRatio:
    def test_identical(self) -> None:
        assert _token_sort_ratio("hello world", "hello world") == 1.0

    def test_reversed_order(self) -> None:
        assert _token_sort_ratio("hello world", "world hello") == 1.0

    def test_partial_overlap(self) -> None:
        s = _token_sort_ratio("hello world", "hello foo")
        assert s == pytest.approx(0.333, abs=0.1)

    def test_no_overlap(self) -> None:
        assert _token_sort_ratio("hello world", "foo bar") == 0.0

    def test_case_insensitive(self) -> None:
        assert _token_sort_ratio("Hello World", "HELLO WORLD") == 1.0

    def test_extra_whitespace(self) -> None:
        assert _token_sort_ratio("hello   world", "hello world") == 1.0

    def test_multi_word_partial_overlap(self) -> None:
        s = _token_sort_ratio(
            "the quick brown fox",
            "the quick brown dog",
        )
        assert s == pytest.approx(0.6, abs=0.1)

    def test_both_empty(self) -> None:
        assert _token_sort_ratio("", "") == 1.0

    def test_one_empty(self) -> None:
        assert _token_sort_ratio("hello world", "") == 0.0

    def test_duplicate_tokens(self) -> None:
        s = _token_sort_ratio("hello hello world", "hello world")
        assert s > 0.5 and s < 1.0


# ── 7. Cross-algorithm consistency checks ──────────────────────────────────


class TestCrossAlgorithmConsistency:
    TAG = "cross"

    def test_identical_max(self) -> None:
        for name in ("hello", "abc", "test", "python", "fuzzy"):
            assert _levenshtein_similarity(name, name) == 1.0
            assert _jaro_winkler_similarity(name, name) == 1.0
            assert _trigram_similarity(name, name) == 1.0

    def test_order_symmetry(self) -> None:
        a, b = "algorithm", "alogrithm"
        assert _levenshtein_similarity(a, b) == _levenshtein_similarity(b, a)
        assert _jaro_winkler_similarity(a, b) == _jaro_winkler_similarity(b, a)
        assert _trigram_similarity(a, b) == _trigram_similarity(b, a)

    def test_phonetic_agreement(self) -> None:
        pairs = [
            ("Robert", "Rupert"),
            ("Ashcraft", "Ashcroft"),
            ("Catherine", "Katherine"),
        ]
        for a, b in pairs:
            if _soundex_match(a, b) or _metaphone_match(a, b):
                assert _trigram_similarity(a, b) > 0.0
