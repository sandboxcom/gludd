"""Tests for src/general_ludd/compression/mtf.py"""

from __future__ import annotations

import pytest

from general_ludd.compression.mtf import mtf_decode, mtf_encode


class TestMTFEncode:
    def test_encode_known_case(self):
        alphabet = list(range(256))
        result = mtf_encode(b"abc", alphabet)
        assert result == [97, 98, 99]

    def test_encode_repeating_symbol(self):
        alphabet = list(range(256))
        result = mtf_encode(b"aaaa", alphabet)
        assert result == [97, 0, 0, 0]

    def test_encode_single_byte(self):
        alphabet = list(range(256))
        result = mtf_encode(b"x", alphabet)
        assert len(result) == 1

    def test_encode_empty_data(self):
        alphabet = list(range(256))
        result = mtf_encode(b"", alphabet)
        assert result == []

    def test_encode_small_alphabet(self):
        alphabet = [65, 66, 67]
        result = mtf_encode(b"BCA", alphabet)
        assert result == [1, 2, 2]

    def test_encode_symbol_not_in_alphabet(self):
        alphabet = [1, 2, 3]
        with pytest.raises(ValueError, match="not in alphabet"):
            mtf_encode(b"X", alphabet)

    def test_encode_empty_alphabet_raises(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            mtf_encode(b"a", [])


class TestMTFDecode:
    def test_decode_known_case(self):
        alphabet = list(range(256))
        result = mtf_decode([97, 98, 99], alphabet)
        assert result == b"abc"

    def test_decode_empty(self):
        alphabet = list(range(256))
        result = mtf_decode([], alphabet)
        assert result == b""

    def test_decode_empty_alphabet_raises(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            mtf_decode([0], [])

    def test_decode_index_out_of_range(self):
        alphabet = [1, 2]
        with pytest.raises(ValueError, match="out of range"):
            mtf_decode([5], alphabet)

    def test_decode_negative_index(self):
        alphabet = [1, 2]
        with pytest.raises(ValueError, match="out of range"):
            mtf_decode([-1], alphabet)


class TestMTFRoundtrip:
    def test_roundtrip_ascii(self):
        alphabet = list(range(256))
        original = b"Hello, world!"
        encoded = mtf_encode(original, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == original

    def test_roundtrip_all_bytes(self):
        alphabet = list(range(256))
        original = bytes(range(256))
        encoded = mtf_encode(original, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == original

    def test_roundtrip_repeating(self):
        alphabet = list(range(256))
        original = b"aaaaabbbbbcccc"
        encoded = mtf_encode(original, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == original
