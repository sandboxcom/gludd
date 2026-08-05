"""Move-to-Front transform tests."""

from __future__ import annotations

import pytest

from general_ludd.compression.mtf import (
    mtf_decode,
    mtf_encode,
)


class TestMtfEncode:
    def test_empty_returns_empty(self) -> None:
        assert mtf_encode(b"", list(range(256))) == []

    def test_single_byte_returns_zero(self) -> None:
        assert mtf_encode(b"\x00", list(range(256))) == [0]

    def test_repeated_byte_returns_zeros(self) -> None:
        assert mtf_encode(b"\x41\x41\x41", list(range(256))) == [0x41, 0, 0]

    def test_alternating_two_symbols(self) -> None:
        result = mtf_encode(b"\x41\x42\x41\x42\x41", list(range(256)))
        assert result == [0x41, 0x42, 1, 1, 1]

    def test_consecutive_distinct(self) -> None:
        result = mtf_encode(b"\x00\x01\x02", list(range(256)))
        assert result == [0, 1, 2]

    def test_with_ascii_lowercase(self) -> None:
        alphabet = list(b"abcdefghijklmnopqrstuvwxyz")
        result = mtf_encode(b"banana", alphabet)
        assert result == [1, 1, 13, 1, 1, 1]

    def test_symbol_not_in_alphabet_raises(self) -> None:
        alphabet = list(b"abc")
        with pytest.raises(ValueError, match="not in alphabet"):
            mtf_encode(b"abcx", alphabet)

    def test_alphabet_length_one(self) -> None:
        assert mtf_encode(b"\x00\x00\x00", [0]) == [0, 0, 0]

    def test_long_repeating_run(self) -> None:
        data = b"\x41" * 100
        result = mtf_encode(data, list(range(256)))
        assert result == [0x41] + [0] * 99


class TestMtfDecode:
    def test_empty_returns_empty(self) -> None:
        assert mtf_decode([], list(range(256))) == b""

    def test_single_zero_returns_first_symbol(self) -> None:
        assert mtf_decode([0], list(range(256))) == b"\x00"

    def test_repeated_zeros(self) -> None:
        result = mtf_decode([0, 0, 0], list(range(256)))
        assert result == b"\x00\x00\x00"

    def test_alternating_indices(self) -> None:
        result = mtf_decode([0, 1, 0, 1], list(range(256)))
        assert result == b"\x00\x01\x01\x00"

    def test_ascii_lowercase_decode(self) -> None:
        alphabet = list(b"abcdefghijklmnopqrstuvwxyz")
        result = mtf_decode([1, 1, 13, 1, 1, 1], alphabet)
        assert result == b"banana"

    def test_decode_non_symmetric_indices(self) -> None:
        alphabet = list(b"abcdefghijklmnopqrstuvwxyz")
        result = mtf_decode([1, 1, 13, 1, 1, 13], alphabet)
        assert result == b"bananm"

    def test_index_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            mtf_decode([5], list(b"abc"))

    def test_alphabet_length_one(self) -> None:
        assert mtf_decode([0, 0, 0], [0]) == bytes([0, 0, 0])

    def test_large_alphabet_full_range(self) -> None:
        data = list(range(128))
        decoded = mtf_decode(data, list(range(128)))
        assert decoded == bytes(range(128))


class TestMtfRoundtrip:
    def test_empty(self) -> None:
        alphabet = list(range(256))
        assert mtf_decode(mtf_encode(b"", alphabet), alphabet) == b""

    def test_single_byte(self) -> None:
        alphabet = list(range(256))
        for b in range(256):
            original = bytes([b])
            encoded = mtf_encode(original, alphabet)
            decoded = mtf_decode(encoded, alphabet)
            assert decoded == original, f"failed for byte {b}"

    def test_long_random_sequence(self) -> None:
        alphabet = list(range(256))
        data = bytes(range(256)) * 4
        encoded = mtf_encode(data, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == data

    def test_repeated_pattern(self) -> None:
        alphabet = list(range(256))
        data = b"\x41\x42\x43" * 50
        encoded = mtf_encode(data, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == data

    def test_alphabet_subset(self) -> None:
        alphabet = list(b"ABCDE")
        data = b"ABCDE" * 3
        encoded = mtf_encode(data, alphabet)
        decoded = mtf_decode(encoded, alphabet)
        assert decoded == data

    def test_encode_then_decode_mutates_neither_alphabet(self) -> None:
        alphabet = list(range(256))
        original_alpha = alphabet[:]
        data = bytes(range(10))
        encoded = mtf_encode(data, alphabet)
        assert alphabet == original_alpha
        decoded = mtf_decode(encoded, alphabet)
        assert alphabet == original_alpha
        assert decoded == data


class TestMtfProperties:
    def test_encode_output_len_matches_input_len(self) -> None:
        alphabet = list(range(256))
        for size in [0, 1, 5, 127, 256, 1024]:
            data = bytes(i % 256 for i in range(size))
            encoded = mtf_encode(data, alphabet)
            assert len(encoded) == len(data)

    def test_decode_output_len_matches_input_len(self) -> None:
        alphabet = list(range(256))
        for size in [0, 1, 5, 127, 256, 1024]:
            indices = [i % 256 for i in range(size)]
            decoded = mtf_decode(indices, alphabet)
            assert len(decoded) == len(indices)

    def test_encode_followed_by_decode_is_identity(self) -> None:
        alphabet = list(range(256))
        datasets = [
            b"",
            b"\x00",
            b"\xff",
            b"hello world",
            b"\x00\x01\x02\x03" * 25,
        ]
        for data in datasets:
            assert mtf_decode(mtf_encode(data, alphabet), alphabet) == data

    def test_mtf_reduces_entropy_for_repeated_symbols(self) -> None:
        alphabet = list(range(256))
        data = b"aaaaabbbbbcccccaaaaabbbbb"
        encoded = mtf_encode(data, alphabet)
        zeros = encoded.count(0)
        ones = encoded.count(1)
        assert zeros + ones >= len(encoded) * 0.6


class TestMtfBufferSafety:
    """Verify the internal alphabet list is not mutated across calls."""

    def test_repeated_encode_uses_fresh_state(self) -> None:
        alphabet = list(range(256))
        r1 = mtf_encode(b"\x41", alphabet)
        r2 = mtf_encode(b"\x41", alphabet)
        assert r1 == r2

    def test_repeated_decode_uses_fresh_state(self) -> None:
        alphabet = list(range(256))
        r1 = mtf_decode([0x41], alphabet)
        r2 = mtf_decode([0x41], alphabet)
        assert r1 == r2
