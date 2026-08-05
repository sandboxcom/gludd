"""Burrows-Wheeler transform tests.
Forward encode, inverse decode, round-trip, edge cases.
"""

from __future__ import annotations

from collections import Counter

from general_ludd.algorithms.burrows_wheeler import bwt_decode, bwt_encode


class TestBWTEncode:
    def test_empty(self) -> None:
        result, idx = bwt_encode("")
        assert result == "\x00"
        assert idx == 0

    def test_single_char(self) -> None:
        result, idx = bwt_encode("a")
        assert result == "a\x00"
        assert idx == 1

    def test_two_chars(self) -> None:
        result, idx = bwt_encode("ab")
        assert result == "b\x00a"
        assert idx == 1

    def test_banana(self) -> None:
        result, idx = bwt_encode("banana")
        assert result == "annb\x00aa"
        assert idx == 4

    def test_mississippi(self) -> None:
        result, idx = bwt_encode("mississippi")
        assert result == "ipssm\x00pissii"
        assert idx == 5

    def test_repeated_char(self) -> None:
        result, idx = bwt_encode("aaaa")
        assert result == "aaaa\x00"
        assert idx == 4

    def test_abc_sentinel_position(self) -> None:
        result, idx = bwt_encode("abc")
        assert result == "c\x00ab"
        assert idx == 1

    def test_long_distinct(self) -> None:
        s = "abcdefghijklmnopqrstuvwxyz"
        result, _idx = bwt_encode(s)
        assert len(result) == len(s) + 1
        assert result[0] == "z"

    def test_returns_string_type(self) -> None:
        result, idx = bwt_encode("test")
        assert isinstance(result, str)
        assert isinstance(idx, int)

    def test_result_length(self) -> None:
        s = "hello"
        result, _idx = bwt_encode(s)
        assert len(result) == len(s) + 1

    def test_sentinel_present(self) -> None:
        for s in ["x", "hello", "banana"]:
            result, _idx = bwt_encode(s)
            assert "\x00" in result


class TestBWTDecode:
    def test_empty(self) -> None:
        assert bwt_decode("\x00", 0) == ""

    def test_single_char(self) -> None:
        assert bwt_decode("a\x00", 1) == "a"

    def test_banana(self) -> None:
        assert bwt_decode("annb\x00aa", 4) == "banana"

    def test_mississippi(self) -> None:
        assert bwt_decode("ipssm\x00pissii", 5) == "mississippi"

    def test_repeated_char(self) -> None:
        assert bwt_decode("aaaa\x00", 4) == "aaaa"

    def test_two_chars(self) -> None:
        assert bwt_decode("b\x00a", 1) == "ab"

    def test_abc(self) -> None:
        assert bwt_decode("c\x00ab", 1) == "abc"

    def test_uses_encode_for_idx(self) -> None:
        for s in ["abracadabra", "suffix", "trees"]:
            encoded, idx = bwt_encode(s)
            assert bwt_decode(encoded, idx) == s


class TestBWTRoundTrip:
    def test_empty(self) -> None:
        assert bwt_decode(*bwt_encode("")) == ""

    def test_single(self) -> None:
        assert bwt_decode(*bwt_encode("x")) == "x"

    def test_alphabet(self) -> None:
        for s in ["abc", "hello", "world", "gludd", "python"]:
            assert bwt_decode(*bwt_encode(s)) == s

    def test_repeated(self) -> None:
        for s in ["aaaa", "ababab", "aabbcc", "xxyyzz"]:
            assert bwt_decode(*bwt_encode(s)) == s

    def test_palindrome(self) -> None:
        for s in ["racecar", "abba", "madamimadam"]:
            assert bwt_decode(*bwt_encode(s)) == s

    def test_numeric_like(self) -> None:
        for s in ["12345", "00110011"]:
            assert bwt_decode(*bwt_encode(s)) == s

    def test_unicode(self) -> None:
        s = "caf\xe9 r\xe9sum\xe9 na\xefve"
        assert bwt_decode(*bwt_encode(s)) == s

    def test_sentinel_in_input(self) -> None:
        s = "a\x00b"
        encoded, idx = bwt_encode(s)
        assert bwt_decode(encoded, idx) == s

    def test_long_string(self) -> None:
        s = "the quick brown fox jumps over the lazy dog"
        assert bwt_decode(*bwt_encode(s)) == s

    def test_pangram(self) -> None:
        s = "the quick brown fox jumps over the lazy dog"
        assert bwt_decode(*bwt_encode(s)) == s


class TestBWTPreservesMultiplicity:
    def test_character_counts(self) -> None:
        for s in ["mississippi", "banana", "abracadabra"]:
            encoded, _ = bwt_encode(s)
            no_sentinel = encoded.replace("\x00", "")
            assert Counter(no_sentinel) == Counter(s)


class TestBWTIndexRange:
    def test_index_bounds(self) -> None:
        for s in ["hello world", "abc", "xx"]:
            encoded, idx = bwt_encode(s)
            assert 0 <= idx < len(encoded)
