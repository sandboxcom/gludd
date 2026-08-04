"""Deep tests for polynomial rolling hash, double hash, and
Rabin-Karp matcher — slide correctness, collision avoidance,
find all occurrences, and edge cases.
"""

from __future__ import annotations

import random
import string

import pytest

from general_ludd.algorithms.rolling_hash import (
    ENGINES,
    DoubleHash,
    PolynomialRollingHash,
    build_rolling_hash,
    rabin_karp_search,
)


def _naive_find_all(text: str, pattern: str) -> list[int]:
    if not pattern:
        return list(range(len(text) + 1))
    result: list[int] = []
    m = len(pattern)
    for i in range(len(text) - m + 1):
        if text[i : i + m] == pattern:
            result.append(i)
    return result


# ---------------------------------------------------------------------------
# PolynomialRollingHash — construction and window management
# ---------------------------------------------------------------------------


def test_rh_empty_construction():
    h = PolynomialRollingHash()
    assert h.value == 0
    assert len(h) == 0


def test_rh_push_builds_window():
    h = PolynomialRollingHash()
    h.push("a")
    h.push("b")
    h.push("c")
    assert h.window == "abc"


def test_rh_set_window_resets():
    h = PolynomialRollingHash(data="hello")
    assert h.value != 0
    h.set_window("bye")
    assert h.window == "bye"


def test_rh_same_string_same_hash():
    h1 = PolynomialRollingHash(data="test")
    h2 = PolynomialRollingHash(data="test")
    assert h1.value == h2.value


def test_rh_different_string_different_hash():
    h1 = PolynomialRollingHash(data="abc")
    h2 = PolynomialRollingHash(data="abd")
    assert h1.value != h2.value


def test_rh_hash_reproducible_across_instances():
    for s in ["", "a", "hello world", "x" * 100]:
        assert PolynomialRollingHash(data=s).value == PolynomialRollingHash(data=s).value


# ---------------------------------------------------------------------------
# Slide correctness
# ---------------------------------------------------------------------------


def test_slide_vs_recompute_small():
    data = "abcdefghijklmnop"
    window_len = 5
    h = PolynomialRollingHash(data=data[:window_len])
    for i in range(len(data) - window_len):
        expected = PolynomialRollingHash(data=data[i + 1 : i + 1 + window_len])
        h.slide(data[i], data[i + window_len])
        assert h.value == expected.value, (
            f"slide mismatch at i={i}: "
            f"got={h.value} expected={expected.value} "
            f"window={data[i + 1 : i + 1 + window_len]}"
        )


def test_slide_long_repeated():
    text = "a" * 500 + "b" * 500 + "c" * 500
    k = 7
    h = PolynomialRollingHash(data=text[:k])
    for i in range(1, len(text) - k + 1):
        expected = PolynomialRollingHash(data=text[i : i + k])
        h.slide(text[i - 1], text[i + k - 1])
        assert h.value == expected.value, f"slide mismatch at i={i}"


def test_slide_single_char():
    h = PolynomialRollingHash(data="x")
    h.slide("x", "y")
    assert h.window == "y"
    assert h.value == PolynomialRollingHash(data="y").value


# ---------------------------------------------------------------------------
# DoubleHash — collision avoidance
# ---------------------------------------------------------------------------


def test_double_hash_push_and_slide():
    dh = DoubleHash()
    dh.set_window("abc")
    v1 = dh.value
    dh.slide("a", "d")
    v2 = dh.value
    assert v1 != v2
    expected = DoubleHash()
    expected.set_window("bcd")
    assert dh == expected


def test_double_hash_avoid_collision():
    """Double hash (911,1e9+7) and (1597,1e9+9) — different values."""
    dh = DoubleHash()
    dh.set_window("hello")
    h1 = dh._h1.value
    h2 = dh._h2.value
    assert h1 != h2


# ---------------------------------------------------------------------------
# Rabin-Karp correctness (parametrized across single + double)
# ---------------------------------------------------------------------------

_RK_VARIANTS = [
    ("single", lambda t, p: rabin_karp_search(t, p)),
    ("double", lambda t, p: rabin_karp_search(t, p, double_hash=True)),
]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_empty_pattern(variant_name: str, rk):
    assert rk("abc", "") == [0, 1, 2, 3]
    assert rk("", "") == [0]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_pattern_longer_than_text(variant_name: str, rk):
    assert rk("ab", "abc") == []
    assert rk("", "x") == []


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_exact_match(variant_name: str, rk):
    assert rk("hello", "hello") == [0]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_no_match(variant_name: str, rk):
    assert rk("abcdef", "xyz") == []


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_match_at_start(variant_name: str, rk):
    assert rk("abcxxxyyy", "abc") == [0]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_match_at_end(variant_name: str, rk):
    assert rk("xxxyyyabc", "abc") == [6]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_multiple_matches(variant_name: str, rk):
    assert rk("aaa", "aa") == [0, 1]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_overlapping_matches(variant_name: str, rk):
    assert rk("ababa", "aba") == [0, 2]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_all_same_chars(variant_name: str, rk):
    assert rk("aaaaa", "aa") == [0, 1, 2, 3]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_unicode_text(variant_name: str, rk):
    assert rk("café café", "fé") == [2, 7]


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_repeated_pattern(variant_name: str, rk):
    needle = "needle"
    haystack = ("x" * 100 + needle) * 6
    assert rk(haystack, needle) == _naive_find_all(haystack, needle)


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_random_vs_naive(variant_name: str, rk):
    random.seed(42)
    haystack = "".join(random.choices(string.ascii_lowercase, k=500))
    for _ in range(30):
        needle_len = random.randint(1, 12)
        needle = "".join(random.choices(string.ascii_lowercase, k=needle_len))
        expected = _naive_find_all(haystack, needle)
        got = rk(haystack, needle)
        assert got == expected, f"variant={variant_name} needle={needle!r}"


# ---------------------------------------------------------------------------
# Collision avoidance — single vs. double on adversarial inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant_name,rk", _RK_VARIANTS)
def test_rk_large_alphabet(variant_name: str, rk):
    random.seed(99)
    alphabet = "".join(chr(i) for i in range(256, 512))
    haystack = "".join(random.choices(alphabet, k=300))
    for _ in range(20):
        needle_len = random.randint(1, 8)
        needle = "".join(random.choices(alphabet, k=needle_len))
        expected = _naive_find_all(haystack, needle)
        got = rk(haystack, needle)
        assert got == expected, f"variant={variant_name} needle={needle!r}"


def test_double_hash_collision_resistant():
    """Build many strings with same single-hash value, verify double-hash
    distinguishes them."""
    base = 911
    mod_small = 1009
    seen_single: set[int] = set()
    seen_double: set[tuple[int, int]] = set()
    collisions_single = 0
    collisions_double = 0
    for i in range(2000):
        s = f"collision-test-{i}"
        sh = build_rolling_hash(s, base, mod_small)
        dh = DoubleHash()
        dh.set_window(s)
        dv = dh.value
        if sh in seen_single:
            collisions_single += 1
        if dv in seen_double:
            collisions_double += 1
        seen_single.add(sh)
        seen_double.add(dv)
    assert collisions_single > 0, f"expected at least one single-hash collision (mod={mod_small})"
    assert collisions_double == 0, f"double hash had {collisions_double} collisions on 2000 strings"


# ---------------------------------------------------------------------------
# Build rolling hash utility
# ---------------------------------------------------------------------------


def test_build_rolling_hash_deterministic():
    assert build_rolling_hash("hello") == build_rolling_hash("hello")
    assert build_rolling_hash("hello") != build_rolling_hash("hellp")


def test_build_rolling_hash_equal_length_diff_content():
    h1 = build_rolling_hash("abc")
    h2 = build_rolling_hash("abd")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Edge / regression
# ---------------------------------------------------------------------------


def test_rk_single_char_text():
    assert rabin_karp_search("a", "a") == [0]
    assert rabin_karp_search("a", "b") == []


def test_rk_pattern_equals_text_length():
    assert rabin_karp_search("abc", "abc") == [0]


def test_rk_long_repeated_performance():
    text = "a" * 2000 + "b"
    pattern = "a" * 50 + "b"
    assert rabin_karp_search(text, pattern) == [1950]


def test_rk_emoji():
    assert rabin_karp_search("hello 🌍 world 🌍", "🌍") == [6, 14]


def test_double_hash_set_window_sync():
    dh = DoubleHash()
    dh.set_window("hello")
    h1_only = PolynomialRollingHash(data="hello")
    assert dh._h1.value == h1_only.value


# ---------------------------------------------------------------------------
# ENGINES dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ENGINES))
def test_engines_dispatch(name: str):
    engine = ENGINES[name]
    assert engine("hello world hello", "hello") == [0, 12]
