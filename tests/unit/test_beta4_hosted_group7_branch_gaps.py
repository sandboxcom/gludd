"""Close hosted-only regex-engine branch gaps."""

from __future__ import annotations

import pytest

from general_ludd.regex_engine import (
    _analyze_backtracking_dangers,
    _contains_quantified_branch,
    _find_close,
    _overlaps,
    _skip_char_class,
    _skip_group,
)


class TestRegexHostedBranches:
    @pytest.mark.parametrize(
        ("group", "expected"),
        [
            ("(?:a+)", True),
            ("[ab]+", True),
            (r"a\+", False),
            ("plain", False),
        ],
    )
    def test_quantified_branch_scanner(self, group: str, expected: bool) -> None:
        assert _contains_quantified_branch(group) is expected

    def test_character_class_and_group_scanners_cover_escapes(self) -> None:
        assert _skip_char_class(r"[^\]a]tail", 0) == 6
        assert _skip_group(r"(a(?:b[)])c)tail", 0) == 12
        assert _find_close(r"(a(?:b[)])c)tail", 0) == 12

    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [("", "a", False), ("ab", "ac", True), ("a", "ab", True), ("x", "y", False)],
    )
    def test_overlap_totality(self, left: str, right: str, expected: bool) -> None:
        assert _overlaps(left, right) is expected

    @pytest.mark.parametrize(
        "pattern",
        [
            "(a+",
            r"\(a+\)",
            "(?=a|ab)+",
            "([ab]+)+",
            "(a|ab)+",
            "(a|b)+",
        ],
    )
    def test_analyzer_handles_incomplete_escaped_and_alternating_patterns(self, pattern: str) -> None:
        assert isinstance(_analyze_backtracking_dangers(pattern), list)
