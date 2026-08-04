"""Deep Aho-Corasick automaton tests: trie, failure links, output links,
streaming match with overlapping / shared-prefix / substring patterns.
"""

from __future__ import annotations

from typing import ClassVar

from general_ludd.algorithms.aho_corasick import AhoCorasick


def matches(ac: AhoCorasick, text: str) -> list[tuple[int, str]]:
    return list(ac.search(text))


class TestTrieConstruction:
    def test_empty_patterns(self) -> None:
        ac = AhoCorasick([])
        assert ac.state_count == 1
        assert ac.failure == {0: 0}

    def test_single_pattern(self) -> None:
        ac = AhoCorasick(["abc"])
        assert ac.state_count == 4
        assert ac.failure[1] == 0
        assert ac.failure[2] == 0
        assert ac.failure[3] == 0

    def test_shared_prefix(self) -> None:
        ac = AhoCorasick(["ab", "abc", "abd"])
        assert ac.state_count == 5
        assert set(ac._goto[2].keys()) == {"c", "d"}

    def test_empty_string_pattern_ignored(self) -> None:
        ac = AhoCorasick(["", "a", ""])
        assert matches(ac, "a") == [(0, "a")]
        assert ac.state_count == 2

    def test_single_char_patterns(self) -> None:
        ac = AhoCorasick(["a", "b", "c"])
        assert ac.state_count == 4
        assert list(ac.failure.values()).count(0) == 4

    def test_output_at_intermediate_state(self) -> None:
        ac = AhoCorasick(["ab", "abc"])
        assert "ab" in ac.output_map.get(2, [])
        assert "abc" in ac.output_map.get(3, [])


class TestFailureLinks:
    def test_single_char_overlap(self) -> None:
        ac = AhoCorasick(["a", "ab"])
        assert ac.failure[1] == 0
        assert ac.failure[2] == 0

    def test_failure_to_non_root(self) -> None:
        ac = AhoCorasick(["he", "she", "his", "hers"])
        assert ac.failure[4] == 1
        assert ac.failure[5] == 2

    def test_failure_chain_resolves(self) -> None:
        ac = AhoCorasick(["c", "bc", "abc"])
        assert ac.failure[5] == 2
        assert ac.failure[6] == 3
        assert ac.failure[3] == 1

    def test_all_fail_to_root(self) -> None:
        ac = AhoCorasick(["xa", "yb", "zc"])
        for state, f in ac.failure.items():
            if state == 0:
                continue
            assert f == 0, f"state {state} should fail to root, got {f}"


class TestOutputLinks:
    def test_subpattern_output_propagates(self) -> None:
        ac = AhoCorasick(["ab", "bcab"])
        result = matches(ac, "abcab")
        assert (1, "ab") in result
        assert (4, "ab") in result
        assert (4, "bcab") in result

    def test_output_at_proper_suffix(self) -> None:
        ac = AhoCorasick(["a", "ba", "cba"])
        result = matches(ac, "dcba")
        assert (3, "a") in result
        assert (3, "ba") in result
        assert (3, "cba") in result


class TestSearch:
    def test_no_patterns(self) -> None:
        assert matches(AhoCorasick([]), "hello") == []

    def test_no_match(self) -> None:
        assert matches(AhoCorasick(["xyz", "abc"]), "hello world") == []

    def test_single_exact_match(self) -> None:
        assert matches(AhoCorasick(["needle"]), "haystack needle more") == [(14, "needle")]

    def test_multiple_non_overlapping(self) -> None:
        assert matches(AhoCorasick(["foo", "bar"]), "foo bar") == [(2, "foo"), (6, "bar")]

    def test_overlapping_matches(self) -> None:
        result = matches(AhoCorasick(["aa", "aaa"]), "aaaa")
        assert sorted(result) == [
            (1, "aa"),
            (2, "aa"),
            (2, "aaa"),
            (3, "aa"),
            (3, "aaa"),
        ]

    def test_end_position_correct(self) -> None:
        result = matches(AhoCorasick(["test", "st"]), "testing")
        assert (3, "st") in result
        assert (3, "test") in result

    def test_repeated_characters(self) -> None:
        assert matches(AhoCorasick(["xx"]), "xxxxx") == [
            (1, "xx"),
            (2, "xx"),
            (3, "xx"),
            (4, "xx"),
        ]

    def test_unicode_patterns(self) -> None:
        result = matches(AhoCorasick(["café", "fée"]), "un café et une fée")
        assert (6, "café") in result
        assert (17, "fée") in result

    def test_greek_alphabet(self) -> None:
        result = matches(AhoCorasick(["αβ", "βγ"]), "αβγ")
        assert (1, "αβ") in result
        assert (2, "βγ") in result

    def test_long_text_many_matches(self) -> None:
        result = matches(AhoCorasick(["a", "aa", "aaa"]), "a" * 100)
        assert len(result) == 297
        assert result[0] == (0, "a")

    def test_empty_text(self) -> None:
        assert matches(AhoCorasick(["a", "ab"]), "") == []


class TestWikipediaExample:
    PATTERNS: ClassVar[list[str]] = ["a", "ab", "bab", "bc", "bca", "c", "caa"]
    TEXT: ClassVar[str] = "abccab"
    EXPECTED: ClassVar[list[tuple[int, str]]] = [
        (0, "a"),
        (1, "ab"),
        (2, "bc"),
        (2, "c"),
        (3, "c"),
        (4, "a"),
        (5, "ab"),
    ]

    def test_wikipedia_example(self) -> None:
        assert matches(AhoCorasick(self.PATTERNS), self.TEXT) == self.EXPECTED


class TestEdgeCases:
    def test_pattern_longer_than_text(self) -> None:
        assert matches(AhoCorasick(["hello world"]), "hi") == []

    def test_same_pattern_multiple_times_in_input(self) -> None:
        result = matches(AhoCorasick(["test", "test"]), "test test")
        assert result == [(3, "test"), (3, "test"), (8, "test"), (8, "test")]

    def test_all_same_char(self) -> None:
        result = matches(AhoCorasick(["z", "zz", "zzz"]), "zzz")
        assert len(result) == 6
        assert (0, "z") in result
        assert (2, "zzz") in result

    def test_case_sensitivity(self) -> None:
        ac = AhoCorasick(["Hello"])
        assert matches(ac, "hello") == []
        assert matches(ac, "Hello world") == [(4, "Hello")]


class TestAdversarial:
    def test_many_overlapping(self) -> None:
        patterns = [chr(97 + i) for i in range(26)]
        result = matches(AhoCorasick(patterns), "abcdefghijklmnopqrstuvwxyz")
        assert len(result) == 26

    def test_ladder_patterns(self) -> None:
        patterns = ["a" * i for i in range(1, 11)]
        result = matches(AhoCorasick(patterns), "a" * 20)
        assert len(result) > 100

    def test_arabic_text(self) -> None:
        result = matches(AhoCorasick(["مرحباً", "العالم"]), "مرحباً بالعالم")
        assert (5, "مرحباً") in result
        assert (13, "العالم") in result

    def test_state_count_matches_expected(self) -> None:
        assert AhoCorasick(["abc", "abd", "abe", "abf"]).state_count == 7


class TestNoFalsePositives:
    def test_substring_not_a_match(self) -> None:
        assert matches(AhoCorasick(["abc", "def"]), "abd") == []

    def test_prefix_not_a_match(self) -> None:
        assert matches(AhoCorasick(["abc"]), "ab") == []

    def test_suffix_not_a_match(self) -> None:
        assert matches(AhoCorasick(["abc"]), "bc") == []
