"""Deep tests for the regex engine: compilation, matching, groups,
lookahead/behind, backreferences, and catastrophic-backtracking detection."""

from __future__ import annotations

import re

import pytest

from general_ludd.regex_engine import (
    BacktrackingDanger,
    CatastrophicBacktrackingError,
    CompiledPattern,
    RegexEngine,
)


class TestRegexCompilation:
    def test_compile_simple_literal(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"hello")
        assert isinstance(cp, CompiledPattern)
        assert cp.source == r"hello"
        assert cp.groups == 0

    def test_compile_with_flags_ignorecase(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"hello", flags=re.IGNORECASE)
        assert cp.match("HELLO").matched
        assert cp.match("Hello").matched

    def test_compile_escaped_special_chars(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\.\*\+\[\]")
        assert cp.match(".*+[]").matched is True
        assert cp.match("abc").matched is False

    def test_compile_char_class(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"[aeiou]")
        result = cp.search("rhythm")
        assert not result.matched

    def test_compile_invalid_pattern_raises_re_error(self) -> None:
        engine = RegexEngine()
        with pytest.raises(re.error):
            engine.compile(r"[unclosed")


class TestRegexMatching:
    def test_match_anchored_start(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"foo")
        assert cp.match("foobar").matched
        assert not cp.match("barfoo").matched

    def test_search_finds_anywhere(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"foo")
        result = cp.search("barfoobaz")
        assert result.matched
        assert result.start == 3
        assert result.end == 6

    def test_match_quantifier_star(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"a*")
        result = cp.match("aaaa")
        assert result.matched
        assert result.span == (0, 4)

    def test_match_quantifier_plus(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"a+")
        assert cp.match("aaaa").matched
        assert not cp.match("bbbb").matched

    def test_match_quantifier_range(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"a{2,4}")
        assert cp.match("aa").matched
        assert cp.match("aaaa").matched
        assert not cp.match("a").matched
        assert cp.match("aaaaa").matched  # match() grabs first 4, still matches
        assert cp.match("aaaaa").group(0) == "aaaa"

    def test_match_alternation(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"cat|dog|bird")
        assert cp.search("I have a dog").matched
        assert cp.search("I have a fish").matched is False

    def test_match_start_end_anchors(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"^hello$")
        assert cp.match("hello").matched
        assert not cp.match("hello world").matched

    def test_match_word_boundary(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\bcat\b")
        assert cp.search("the cat sat").matched
        assert not cp.search("concatenate").matched


class TestRegexGroups:
    def test_numbered_groups(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(hello) (world)")
        result = cp.match("hello world")
        assert result.groups == ("hello", "world")
        assert result.group(1) == "hello"
        assert result.group(2) == "world"

    def test_named_groups(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?P<first>\w+) (?P<last>\w+)")
        result = cp.match("Jane Doe")
        assert result.group("first") == "Jane"
        assert result.group("last") == "Doe"
        assert result.groupdict == {"first": "Jane", "last": "Doe"}

    def test_non_capturing_group(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?:hello) (world)")
        result = cp.match("hello world")
        assert result.groups == ("world",)
        assert cp.groups == 1

    def test_empty_group_match(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(a)?(b)")
        result = cp.match("b")
        assert result.matched
        assert result.group(1) is None
        assert result.group(2) == "b"

    def test_groupindex_returns_named_group_map(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?P<x>\d+)-(?P<y>\d+)")
        assert cp.groupindex == {"x": 1, "y": 2}


class TestLookaheadLookbehind:
    def test_positive_lookahead(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+(?=px)")
        result = cp.search("width: 100px")
        assert result.matched
        assert result.group(0) == "100"

    def test_negative_lookahead(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+(?!px)")
        result = cp.search("width: 100em")
        assert result.matched
        assert result.group(0) == "100"
        result2 = cp.search("width: 100px")
        assert result2.matched
        assert result2.group(0) == "10"  # backtracked past "0px"

    def test_positive_lookbehind(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?<=\$)\d+")
        result = cp.search("price: $42 total")
        assert result.matched
        assert result.group(0) == "42"

    def test_negative_lookbehind(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?<!\$)\d+")
        result = cp.search("count: 42 items")
        assert result.matched
        assert result.group(0) == "42"
        result2 = cp.search("price: $42")
        assert result2.matched
        assert result2.group(0) == "2"  # backtracked past "$4"

    def test_lookahead_not_consumed(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\w+(?=\s*world)")
        result = cp.match("hello world")
        assert result.group(0) == "hello"
        assert result.end == 5  # lookahead not consumed


class TestBackreferences:
    def test_numbered_backreference(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(\w+)\s+\1")
        assert cp.match("hello hello").matched
        assert not cp.match("hello world").matched

    def test_named_backreference(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?P<word>\w+)\s+(?P=word)")
        assert cp.match("bye bye").matched
        assert not cp.match("bye hello").matched

    def test_multiple_backreferences(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(\w+)-(\d+)\s+\1-\2")
        result = cp.match("abc-123 abc-123")
        assert result.matched
        result2 = cp.match("abc-123 def-456")
        assert not result2.matched

    def test_backreference_with_quantifier(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(\w)\1+")
        result = cp.search("abbbbc")
        assert result.matched
        assert result.group(0) == "bbbb"


class TestMatchResult:
    def test_result_string_property(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"world")
        result = cp.search("hello world")
        assert result.string == "hello world"

    def test_result_span_when_no_match(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"xyz")
        result = cp.search("abc")
        assert not result.matched
        assert result.span == (-1, -1)
        assert result.start == -1
        assert result.end == -1

    def test_result_bool(self) -> None:
        engine = RegexEngine()
        assert bool(engine.match(r"a", "a"))
        assert not bool(engine.match(r"a", "b"))

    def test_result_repr(self) -> None:
        engine = RegexEngine()
        result = engine.match(r"hi", "hi")
        r = repr(result)
        assert "matched" in r and "span" in r


class TestFindallFinditer:
    def test_findall_returns_list(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+")
        assert cp.findall("a1 b22 c333") == ["1", "22", "333"]

    def test_finditer_yields_results(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+")
        results = list(cp.finditer("a1 b22"))
        assert len(results) == 2
        assert results[0].group(0) == "1"
        assert results[1].group(0) == "22"

    def test_split(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\s*,\s*")
        assert cp.split("a, b, c") == ["a", "b", "c"]

    def test_sub(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+")
        assert cp.sub("X", "a1 b22") == "aX bX"

    def test_subn(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"\d+")
        result, count = cp.subn("X", "a1 b22")
        assert result == "aX bX"
        assert count == 2


class TestCatastrophicBacktracking:
    def test_nested_plus_detected(self) -> None:
        engine = RegexEngine()
        dangers = engine.check_pattern(r"(a+)+")
        assert len(dangers) >= 1
        assert any("(a+)+" in d.reason or "nested" in d.reason.lower() for d in dangers)

    def test_nested_star_detected(self) -> None:
        engine = RegexEngine()
        dangers = engine.check_pattern(r"(a*)*")
        assert len(dangers) >= 1
        assert any("empty string" in d.reason for d in dangers)

    def test_alternation_overlap_detected(self) -> None:
        engine = RegexEngine()
        dangers = engine.check_pattern(r"(a|ab)+")
        has_overlap = any("overlap" in d.reason.lower() or "overlapping" in d.reason.lower() for d in dangers)
        assert has_overlap or len(dangers) >= 1

    def test_safe_pattern_no_dangers(self) -> None:
        engine = RegexEngine()
        dangers = engine.check_pattern(r"hello \w+")
        assert len(dangers) == 0

    def test_strict_mode_raises_on_dangerous(self) -> None:
        engine = RegexEngine(strict=True)
        with pytest.raises(CatastrophicBacktrackingError):
            engine.compile(r"(a+)+")

    def test_strict_mode_allows_safe(self) -> None:
        engine = RegexEngine(strict=True)
        cp = engine.compile(r"\d{3}-\d{4}")
        assert cp.match("123-4567").matched

    def test_is_safe_helper(self) -> None:
        engine = RegexEngine()
        assert engine.is_safe(r"hello")
        assert not engine.is_safe(r"(a+)+")

    def test_danger_location_tracks_position(self) -> None:
        engine = RegexEngine()
        dangers = engine.check_pattern(r"prefix (a+)+ suffix")
        assert len(dangers) >= 1
        d = dangers[0]
        assert d.location > 0
        assert d.construct is not None
        assert isinstance(d, BacktrackingDanger)

    def test_compiled_pattern_exposes_dangers(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(a+)+")
        assert cp.has_dangers
        assert len(cp.dangers) >= 1

    def test_no_false_positive_for_non_capturing_group(self) -> None:
        engine = RegexEngine()
        cp = engine.compile(r"(?:hello)+")
        assert not cp.has_dangers


class TestRegexEngineHelpers:
    def test_engine_match_shorthand(self) -> None:
        engine = RegexEngine()
        result = engine.match(r"\d+", "42 things")
        assert result.matched

    def test_engine_search_shorthand(self) -> None:
        engine = RegexEngine()
        result = engine.search(r"\d+", "item 99 sold")
        assert result.matched
        assert result.group(0) == "99"
