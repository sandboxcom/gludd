"""Tests for src/general_ludd/algorithms/rolling_hash.py"""

from __future__ import annotations

from general_ludd.algorithms.rolling_hash import (
    ENGINES,
    DoubleHash,
    PolynomialRollingHash,
    _pow_mod,
    build_rolling_hash,
    rabin_karp_search,
)

MOD = 1_000_000_007


class TestPolynomialRollingHashBasics:
    def test_empty_hash_value_zero(self):
        h = PolynomialRollingHash()
        assert h.value == 0
        assert len(h) == 0
        assert h.window == ""

    def test_init_with_data_matches_sequential_push(self):
        a = PolynomialRollingHash(data="abc")
        b = PolynomialRollingHash()
        for ch in "abc":
            b.push(ch)
        assert a.value == b.value
        assert a.window == b.window == "abc"

    def test_value_stays_below_modulus(self):
        h = PolynomialRollingHash(data="a" * 500)
        assert 0 <= h.value < MOD

    def test_single_char_value_is_ord(self):
        h = PolynomialRollingHash(data="a")
        assert h.value == ord("a")

    def test_known_value_for_two_chars(self):
        h = PolynomialRollingHash(data="ab")
        expected = (ord("a") * 911 + ord("b")) % MOD
        assert h.value == expected

    def test_window_mirrors_pushes(self):
        h = PolynomialRollingHash()
        for ch in "hello":
            h.push(ch)
        assert h.window == "hello"
        assert len(h) == 5

    def test_push_is_incremental(self):
        h = PolynomialRollingHash()
        h.push("x")
        first = h.value
        h.push("y")
        second = h.value
        expected = (first * 911 + ord("y")) % MOD
        assert second == expected


class TestPolynomialRollingHashSlide:
    def test_slide_on_empty_window_pushes(self):
        h = PolynomialRollingHash()
        h.slide("a", "b")
        assert h.window == "b"
        assert h.value == ord("b")

    def test_slide_matches_fresh_window(self):
        h = PolynomialRollingHash(data="abcd")
        h.slide("a", "e")
        fresh = PolynomialRollingHash(data="bcde")
        assert h.value == fresh.value
        assert h.window == "bcde"
        assert len(h) == 4

    def test_slide_preserves_length(self):
        h = PolynomialRollingHash(data="abcdef")
        h.slide("a", "z")
        assert len(h) == 6

    def test_multiple_slides_walk_across_text(self):
        h = PolynomialRollingHash(data="window")
        h.slide("w", "!")
        h.slide("i", "@")
        assert h.window == "ndow!@"
        fresh = PolynomialRollingHash(data="ndow!@")
        assert h.value == fresh.value

    def test_slide_single_char_window(self):
        h = PolynomialRollingHash(data="q")
        h.slide("q", "r")
        assert h.window == "r"
        assert h.value == ord("r")


class TestPolynomialRollingHashResetAndDunder:
    def test_set_window_resets_state(self):
        h = PolynomialRollingHash(data="stale-data")
        h.set_window("new")
        assert h.window == "new"
        assert h.value == PolynomialRollingHash(data="new").value

    def test_set_window_empty(self):
        h = PolynomialRollingHash(data="junk")
        h.set_window("")
        assert h.value == 0
        assert len(h) == 0
        assert h.window == ""

    def test_equal_hashes_are_equal(self):
        assert PolynomialRollingHash(data="abc") == PolynomialRollingHash(data="abc")

    def test_different_windows_unequal(self):
        assert PolynomialRollingHash(data="abc") != PolynomialRollingHash(data="abd")

    def test_eq_other_type_returns_false(self):
        h = PolynomialRollingHash(data="abc")
        assert (h == "abc") is False
        assert (h == 42) is False

    def test_repr_contains_value_and_base(self):
        h = PolynomialRollingHash(data="abc")
        r = repr(h)
        assert "PolynomialRollingHash" in r
        assert str(h.value) in r
        assert "911" in r


class TestPowMod:
    def test_exp_zero(self):
        assert _pow_mod(911, 0, MOD) == 1

    def test_exp_one(self):
        assert _pow_mod(911, 1, MOD) == 911

    def test_mod_one_returns_zero(self):
        assert _pow_mod(911, 10, 1) == 0

    def test_matches_builtin_pow(self):
        for exp in (2, 3, 7, 16, 31):
            assert _pow_mod(911, exp, MOD) == pow(911, exp, MOD)

    def test_base_larger_than_mod(self):
        assert _pow_mod(1_000_000_099, 3, MOD) == pow(1_000_000_099, 3, MOD)


class TestDoubleHash:
    def test_default_value_is_pair(self):
        d = DoubleHash()
        assert d.value == (0, 0)

    def test_push_propagates_to_both(self):
        d = DoubleHash()
        for ch in "abc":
            d.push(ch)
        h1 = PolynomialRollingHash(data="abc")
        h2 = PolynomialRollingHash(base=1597, mod=1_000_000_009, data="abc")
        assert d.value == (h1.value, h2.value)

    def test_custom_bases_and_mods(self):
        d = DoubleHash(bases=(7, 13), mods=(101, 103))
        for ch in "xy":
            d.push(ch)
        h1 = PolynomialRollingHash(base=7, mod=101, data="xy")
        h2 = PolynomialRollingHash(base=13, mod=103, data="xy")
        assert d.value == (h1.value, h2.value)

    def test_slide_and_set_window_propagate(self):
        d = DoubleHash()
        d.set_window("abcd")
        d.slide("a", "e")
        fresh = DoubleHash()
        fresh.set_window("bcde")
        assert d.value == fresh.value

    def test_equal_by_value(self):
        a = DoubleHash()
        b = DoubleHash()
        a.set_window("same")
        b.set_window("same")
        assert a == b
        b.push("!")
        assert a != b

    def test_eq_other_type_returns_false(self):
        d = DoubleHash()
        assert (d == (0, 0)) is False

    def test_repr_contains_value(self):
        d = DoubleHash()
        d.push("z")
        assert str(d.value) in repr(d)


class TestRabinKarpSearch:
    def test_empty_pattern_returns_all_positions(self):
        assert rabin_karp_search("abc", "") == [0, 1, 2, 3]

    def test_pattern_longer_than_text(self):
        assert rabin_karp_search("ab", "abc") == []

    def test_single_char_pattern(self):
        assert rabin_karp_search("banana", "n") == [2, 4]

    def test_match_at_start_middle_end(self):
        assert rabin_karp_search("aXbXcX", "X") == [1, 3, 5]

    def test_whole_text_matches(self):
        assert rabin_karp_search("needle", "needle") == [0]

    def test_overlapping_matches(self):
        assert rabin_karp_search("aaaaa", "aaa") == [0, 1, 2]

    def test_no_match(self):
        assert rabin_karp_search("hello", "xyz") == []

    def test_single_char_text_and_pattern(self):
        assert rabin_karp_search("a", "a") == [0]

    def test_double_hash_matches_single(self):
        text = "the quick brown fox jumps over the lazy dog"
        pattern = "the"
        single = rabin_karp_search(text, pattern)
        double = rabin_karp_search(text, pattern, double_hash=True)
        assert double == single == [0, 31]

    def test_double_hash_no_match(self):
        assert rabin_karp_search("abcdef", "xyz", double_hash=True) == []

    def test_unicode_text(self):
        assert rabin_karp_search("héllo wörld héllo", "héllo") == [0, 12]


class TestBuildRollingHash:
    def test_empty_pattern(self):
        assert build_rolling_hash("") == 0

    def test_matches_polynomial_value(self):
        assert build_rolling_hash("abc") == PolynomialRollingHash(data="abc").value

    def test_custom_base_and_mod(self):
        expected = PolynomialRollingHash(base=31, mod=97, data="xyz").value
        assert build_rolling_hash("xyz", base=31, mod=97) == expected


class TestEngines:
    def test_known_keys(self):
        assert set(ENGINES) == {"rabin_karp", "rabin_karp_double"}

    def test_engines_agree(self):
        for engine in ENGINES.values():
            assert engine("abcab", "ab") == [0, 3]
