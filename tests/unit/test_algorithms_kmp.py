"""Tests for src/general_ludd/algorithms/kmp.py"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.kmp import kmp_search, prefix_function, z_array, z_search


class TestPrefixFunction:
    def test_empty_string(self):
        # arrange
        s = ""
        # act
        result = prefix_function(s)
        # assert
        assert result == []

    def test_single_char(self):
        # arrange
        s = "a"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0]

    def test_no_repeats(self):
        # arrange
        s = "abcdef"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0, 0, 0, 0, 0, 0]

    def test_all_same(self):
        # arrange
        s = "aaaaa"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0, 1, 2, 3, 4]

    def test_known_abacaba(self):
        # arrange
        s = "abacaba"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0, 0, 1, 0, 1, 2, 3]

    def test_repeated_pattern(self):
        # arrange
        s = "ababab"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0, 0, 1, 2, 3, 4]

    def test_mixed_self_overlap(self):
        # arrange
        s = "aabaaab"
        # act
        result = prefix_function(s)
        # assert
        assert result == [0, 1, 0, 1, 2, 2, 3]


class TestKmpSearch:
    def test_empty_pattern(self):
        # arrange
        text, pattern = "hello", ""
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == []

    def test_pattern_longer_than_text(self):
        # arrange
        text, pattern = "ab", "abcd"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == []

    def test_single_match_start(self):
        # arrange
        text, pattern = "banana", "ban"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [0]

    def test_single_match_middle(self):
        # arrange
        text, pattern = "xxneedlexx", "needle"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [2]

    def test_multiple_matches(self):
        # arrange
        text, pattern = "ababa", "aba"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [0, 2]

    def test_overlapping_matches(self):
        # arrange
        text, pattern = "aaaaa", "aa"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [0, 1, 2, 3]

    def test_no_match(self):
        # arrange
        text, pattern = "hello world", "xyz"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == []

    def test_match_at_end(self):
        # arrange
        text, pattern = "aaab", "ab"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [2]

    def test_full_text_match(self):
        # arrange
        text, pattern = "abc", "abc"
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == [0]


class TestZArray:
    def test_empty_string(self):
        # arrange
        s = ""
        # act
        result = z_array(s)
        # assert
        assert result == []

    def test_single_char(self):
        # arrange
        s = "a"
        # act
        result = z_array(s)
        # assert
        assert result == [0]

    def test_all_same(self):
        # arrange
        s = "aaaa"
        # act
        result = z_array(s)
        # assert
        assert result == [0, 3, 2, 1]

    def test_no_repeats(self):
        # arrange
        s = "abcde"
        # act
        result = z_array(s)
        # assert
        assert result == [0, 0, 0, 0, 0]

    def test_known_abacaba(self):
        # arrange
        s = "abacaba"
        # act
        result = z_array(s)
        # assert
        assert result == [0, 0, 1, 0, 3, 0, 1]


class TestZSearch:
    def test_empty_pattern(self):
        # arrange
        text, pattern = "hello", ""
        # act
        result = z_search(text, pattern)
        # assert
        assert result == []

    def test_pattern_longer_than_text(self):
        # arrange
        text, pattern = "ab", "abcd"
        # act
        result = z_search(text, pattern)
        # assert
        assert result == []

    def test_single_match(self):
        # arrange
        text, pattern = "abcab", "cab"
        # act
        result = z_search(text, pattern)
        # assert
        assert result == [2]

    def test_multiple_and_overlapping(self):
        # arrange
        text, pattern = "aaaaa", "aa"
        # act
        result = z_search(text, pattern)
        # assert
        assert result == [0, 1, 2, 3]

    def test_no_match(self):
        # arrange
        text, pattern = "abcdef", "xyz"
        # act
        result = z_search(text, pattern)
        # assert
        assert result == []


class TestKmpZAgreement:
    @pytest.mark.parametrize(
        "text,pattern",
        [
            ("", ""),
            ("a", ""),
            ("", "a"),
            ("banana", "ana"),
            ("aaaaaaaa", "aa"),
            ("abcabcabc", "abc"),
            ("mississippi", "ssi"),
        ],
    )
    def test_search_agreement(self, text: str, pattern: str):
        # arrange
        expected = kmp_search(text, pattern)
        # act
        actual = z_search(text, pattern)
        # assert
        assert actual == expected

    def test_naive_agreement(self):
        # arrange
        text = "ababababacaba"
        pattern = "aba"
        naive = [i for i in range(len(text) - len(pattern) + 1) if text[i : i + len(pattern)] == pattern]
        # act
        result = kmp_search(text, pattern)
        # assert
        assert result == naive
