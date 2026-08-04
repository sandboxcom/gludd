"""Deep tests for Boyer-Moore, BMH, BMHS, bad-char table,
good-suffix table, and edge cases.
"""

from __future__ import annotations

import random
import string
from collections.abc import Callable

import pytest

from general_ludd.algorithms.boyer_moore import (
    ENGINES,
    boyer_moore_horspool_search,
    boyer_moore_horspool_sunday_search,
    boyer_moore_search,
    boyer_moore_search_with_table,
    build_bad_char_table,
    build_good_suffix_table,
)

ENGINE_NAMES = sorted(ENGINES)

SearchFn = Callable[[str, str], list[int]]


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
# Bad-character table
# ---------------------------------------------------------------------------


def test_bad_char_table_empty():
    assert build_bad_char_table("") == {}


def test_bad_char_table_unique():
    t = build_bad_char_table("abc")
    assert t == {"a": 0, "b": 1, "c": 2}


def test_bad_char_table_duplicates_rightmost():
    t = build_bad_char_table("abca")
    assert t["a"] == 3
    assert t["b"] == 1
    assert t["c"] == 2


def test_bad_char_table_repeated():
    t = build_bad_char_table("aaaa")
    assert t["a"] == 3


# ---------------------------------------------------------------------------
# Good-suffix table
# ---------------------------------------------------------------------------


def test_good_suffix_empty():
    assert build_good_suffix_table("") == []


def test_good_suffix_single_char():
    assert build_good_suffix_table("a") == [1]


def test_good_suffix_no_border():
    gs = build_good_suffix_table("abcde")
    assert all(v > 0 for v in gs)
    assert len(gs) == 5


def test_good_suffix_with_border():
    gs = build_good_suffix_table("aba")
    assert len(gs) == 3
    assert all(v > 0 for v in gs)


def test_good_suffix_repeated():
    gs = build_good_suffix_table("aaaaa")
    assert len(gs) == 5
    assert gs[0] == 1


def test_good_suffix_case_sensitivity():
    gs = build_good_suffix_table("ABa")
    assert len(gs) == 3


# ---------------------------------------------------------------------------
# BM / BMH / BMHS correctness (parametrized across all engines)
# ---------------------------------------------------------------------------

_ENGINE_FUNCS: list[tuple[str, SearchFn]] = [
    ("boyer_moore", boyer_moore_search),
    ("horspool", boyer_moore_horspool_search),
    ("sunday", boyer_moore_horspool_sunday_search),
    (
        "boyer_moore_with_table",
        lambda t, p: boyer_moore_search_with_table(t, build_bad_char_table(p), p),
    ),
]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_empty_pattern(engine_name: str, engine: SearchFn):
    assert engine("", "") == [0]
    assert engine("abc", "") == [0, 1, 2, 3]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_pattern_longer_than_text(engine_name: str, engine: SearchFn):
    assert engine("ab", "abcdef") == []
    assert engine("", "x") == []


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_exact_match(engine_name: str, engine: SearchFn):
    assert engine("hello", "hello") == [0]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_no_match(engine_name: str, engine: SearchFn):
    assert engine("abcdef", "xyz") == []


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_match_at_start(engine_name: str, engine: SearchFn):
    assert engine("abcxxxyyy", "abc") == [0]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_match_at_end(engine_name: str, engine: SearchFn):
    assert engine("xxxyyyabc", "abc") == [6]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_multiple_matches(engine_name: str, engine: SearchFn):
    assert engine("aaa", "aa") == [0, 1]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_overlapping_matches(engine_name: str, engine: SearchFn):
    assert engine("ababa", "aba") == [0, 2]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_all_same_chars(engine_name: str, engine: SearchFn):
    assert engine("aaaaa", "aa") == [0, 1, 2, 3]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_unicode_text(engine_name: str, engine: SearchFn):
    assert engine("café café", "fé") == [2, 7]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_wide_unicode(engine_name: str, engine: SearchFn):
    assert engine("hello 🌍 world", "🌍") == [6]


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_repeated_pattern_in_long_text(engine_name: str, engine: SearchFn):
    needle = "needle"
    haystack = ("x" * 100 + needle) * 6
    got = engine(haystack, needle)
    assert got == _naive_find_all(haystack, needle)


@pytest.mark.parametrize("engine_name,engine", _ENGINE_FUNCS)
def test_random_haystack_vs_naive(engine_name: str, engine: SearchFn):
    random.seed(42)
    haystack = "".join(random.choices(string.ascii_lowercase, k=500))
    for _ in range(20):
        needle_len = random.randint(1, 10)
        needle = "".join(random.choices(string.ascii_lowercase, k=needle_len))
        expected = _naive_find_all(haystack, needle)
        got = engine(haystack, needle)
        assert got == expected, f"engine={engine_name} needle={needle!r}"


# ---------------------------------------------------------------------------
# Good-suffix shift correctness
# ---------------------------------------------------------------------------


def test_good_suffix_pathological_case():
    text = "a" * 2000 + "b"
    pattern = "a" * 20 + "b"
    got_horspool = boyer_moore_horspool_search(text, pattern)
    got_bm = boyer_moore_search(text, pattern)
    assert got_bm == [1980]
    assert got_horspool == [1980]


def test_good_suffix_no_skip_true_match():
    assert boyer_moore_search("xyzhello", "hello") == [3]
    assert boyer_moore_horspool_search("xyzhello", "hello") == [3]


# ---------------------------------------------------------------------------
# Sunday variant specifics
# ---------------------------------------------------------------------------


def test_sunday_next_char_shift():
    assert boyer_moore_horspool_sunday_search("abczdef", "def") == [4]


def test_sunday_chars_not_in_pattern():
    assert boyer_moore_horspool_sunday_search("abcxdef", "def") == [4]


# ---------------------------------------------------------------------------
# Precomputed table reuse
# ---------------------------------------------------------------------------


def test_precomputed_bad_char_reuse():
    bc = build_bad_char_table("abc")
    assert boyer_moore_search_with_table("ababc", bc, "abc") == [2]
    assert boyer_moore_search_with_table("xxxabc", bc, "abc") == [3]


# ---------------------------------------------------------------------------
# ENGINES dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ENGINE_NAMES)
def test_engines_dispatch(name: str):
    engine = ENGINES[name]
    assert engine("hello world hello", "hello") == [0, 12]


# ---------------------------------------------------------------------------
# Edge / regression checks
# ---------------------------------------------------------------------------


def test_single_char_text():
    assert boyer_moore_search("a", "a") == [0]
    assert boyer_moore_horspool_search("a", "a") == [0]
    assert boyer_moore_horspool_sunday_search("a", "a") == [0]
    assert boyer_moore_search("a", "b") == []
    assert boyer_moore_horspool_search("a", "b") == []
    assert boyer_moore_horspool_sunday_search("a", "b") == []


def test_pattern_equals_text_length():
    assert boyer_moore_search("abc", "abc") == [0]
    assert boyer_moore_horspool_search("abc", "abc") == [0]
    assert boyer_moore_horspool_sunday_search("abc", "abc") == [0]


def test_long_repeated_bm_speed():
    text = "a" * 2000 + "b"
    pattern = "a" * 50 + "b"
    assert boyer_moore_search(text, pattern) == [1950]
