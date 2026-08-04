"""Deep wildcard / glob pattern matching tests.

Covers ``src/general_ludd/algorithms/wildcard_match.py``:
``match_backtrack``, ``match_dp``, ``match_regex``, ``match``.
"""

from __future__ import annotations

from general_ludd.algorithms.wildcard_match import (
    _to_regex,
    match,
    match_backtrack,
    match_dp,
    match_regex,
)

_MATCHERS = [match_backtrack, match_dp, match_regex]


def _check(pattern: str, text: str, expected: bool) -> None:
    for impl in _MATCHERS:
        assert impl(pattern, text) == expected, f"{impl.__name__}({pattern!r}, {text!r}) != {expected}"


# ── literal (no wildcards) ─────────────────────────────────────────────────


def test_literal_exact_match() -> None:
    _check("", "", True)
    _check("a", "a", True)
    _check("abc", "abc", True)
    _check("hello", "hello", True)


def test_literal_mismatch() -> None:
    _check("a", "b", False)
    _check("abc", "abd", False)
    _check("hello", "Hello", False)


def test_literal_different_lengths() -> None:
    _check("a", "", False)
    _check("", "a", False)
    _check("abc", "ab", False)
    _check("ab", "abc", False)


# ── ``?`` wildcard ──────────────────────────────────────────────────────────


def test_question_matches_one_char() -> None:
    _check("?", "a", True)
    _check("?", "X", True)
    _check("?", "", False)
    _check("?", "ab", False)


def test_question_with_literals() -> None:
    _check("a?c", "abc", True)
    _check("a?c", "aXc", True)
    _check("a?c", "ac", False)
    _check("a?c", "abbc", False)


def test_multiple_questions() -> None:
    _check("???", "abc", True)
    _check("???", "ab", False)
    _check("???", "abcd", False)
    _check("a?b?c", "aXbYc", True)


# ── ``*`` wildcard ──────────────────────────────────────────────────────────


def test_star_matches_anything() -> None:
    _check("*", "", True)
    _check("*", "a", True)
    _check("*", "abcdef", True)


def test_star_with_literals() -> None:
    _check("a*b", "ab", True)
    _check("a*b", "aXb", True)
    _check("a*b", "aXYZb", True)
    _check("a*b", "abX", False)
    _check("a*b", "Xab", False)


def test_multiple_stars() -> None:
    _check("**", "abc", True)
    _check("***", "abc", True)
    _check("***", "", True)


def test_star_bookends() -> None:
    _check("*a*b*", "XaYbZ", True)
    _check("*a*b*", "ab", True)
    _check("*a*b*", "abX", True)
    _check("*a*b*", "XaYb", True)


# ── mixed ``*`` and ``?`` ──────────────────────────────────────────────────


def test_mixed_wildcards() -> None:
    _check("a?*b", "aXb", True)
    _check("a?*b", "aXYb", True)
    _check("a?*b", "ab", False)
    _check("*?", "a", True)
    _check("*?", "", False)


def test_complex_mixed() -> None:
    _check("a?c*d?f", "aXcYYdZf", True)
    _check("a?c*d?f", "abcdef", True)
    _check("?*?", "a", False)
    _check("?*?", "ab", True)
    _check("?*?", "abc", True)


# ── backtracking edge cases ────────────────────────────────────────────────


def test_star_greedy_backtrack() -> None:
    _check("*a*b", "aaaab", True)
    _check("*a*b", "aaaac", False)


# ── real-world glob patterns ───────────────────────────────────────────────


def test_file_globs() -> None:
    _check("*.py", "foo.py", True)
    _check("*.py", "foo.txt", False)
    _check("test_*.py", "test_wildcard.py", True)
    _check("test_*.py", "production.py", False)


def test_deep_globstar() -> None:
    _check("src/**/*.py", "src/a/b/c/foo.py", True)
    _check("src/**/*.py", "src/foo.py", False)
    _check("src/**/*.py", "test/foo.py", False)


# ── _to_regex ──────────────────────────────────────────────────────────────


def test_to_regex_literal() -> None:
    assert _to_regex("abc") == "^abc$"


def test_to_regex_special_chars_escaped() -> None:
    assert _to_regex("a.b") == r"^a\.b$"


def test_to_regex_star_question() -> None:
    assert _to_regex("a*b?c") == "^a.*b.c$"


def test_to_regex_multiple_specials() -> None:
    assert _to_regex("*.py") == r"^.*\.py$"
    assert _to_regex("a?b*c.d") == r"^a.b.*c\.d$"


# ── DP vs backtrack vs regex consistency ───────────────────────────────────


def test_all_three_agree_on_extensive_suite() -> None:
    cases = [
        ("", "", True),
        ("*", "", True),
        ("*", "a", True),
        ("?", "", False),
        ("?", "a", True),
        ("a", "a", True),
        ("a", "", False),
        ("a*b*c", "aXbYc", True),
        ("a*b*c", "aXbYcZ", False),
        ("*a*b*c*", "XYZaXbYcABC", True),
        ("?*?", "a", False),
        ("?*?", "ab", True),
        ("?*?", "abc", True),
        ("*****", "", True),
        ("*****", "xyz", True),
        ("a?c*d?f", "abcdef", True),
        ("a?c*d?f", "aXcYYdZf", True),
        ("a?c*d?f", "abcde", False),
        ("*a*b", "aaaab", True),
        ("*a*b", "aaaac", False),
    ]
    for pat, txt, exp in cases:
        r1 = match_backtrack(pat, txt)
        r2 = match_dp(pat, txt)
        r3 = match_regex(pat, txt)
        assert r1 == r2 == r3 == exp, (
            f"mismatch: pattern={pat!r} text={txt!r} backtrack={r1} dp={r2} regex={r3} expected={exp}"
        )


# ── convenience match ──────────────────────────────────────────────────────


def test_convenience_match_delegates() -> None:
    assert match("a*b", "aXb")
    assert not match("a*b", "abX")
    assert match("*", "")
    assert not match("?", "")


# ── long strings ───────────────────────────────────────────────────────────


def test_long_string_dp() -> None:
    p = "*a" * 50 + "*"
    t = "a" * 50
    assert match_dp(p, t)


def test_long_string_backtrack() -> None:
    p = "*a" * 50 + "*"
    t = "a" * 50
    assert match_backtrack(p, t)


# ── edge: pattern longer than text with no stars ────────────────────────────


def test_no_wildcards_longer_pattern() -> None:
    _check("abcd", "abc", False)
    _check("abc", "abcd", False)
    _check("a?c?", "abc", False)
    _check("a?c?", "abcd", True)


# ── edge: only stars ───────────────────────────────────────────────────────


def test_only_stars() -> None:
    for i in range(1, 6):
        assert match_backtrack("*" * i, "") is True
        assert match_backtrack("*" * i, "hello") is True


# ── edge: only questions ───────────────────────────────────────────────────


def test_only_questions() -> None:
    _check("?", "x", True)
    _check("?", "", False)
    _check("??", "ab", True)
    _check("??", "a", False)
    _check("??", "abc", False)
