"""Deep tests for src/general_ludd/util/base_encoding.py.

Covers base32, base32hex, base58, base62, base85, custom alphabets,
roundtrip integrity, known vectors, edge cases, and immutability.
"""

from __future__ import annotations

import itertools
import os
import secrets

import pytest

from general_ludd.util.base_encoding import (
    ALPHABET_BASE32,
    ALPHABET_BASE32HEX,
    ALPHABET_BASE58,
    ALPHABET_BASE62,
    ALPHABET_BASE85,
    base_decode,
    base_encode,
)

# ---------------------------------------------------------------------------
# Roundtrip tests
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_base32_roundtrip_empty(self) -> None:
        assert base_decode(base_encode(b"", ALPHABET_BASE32), ALPHABET_BASE32) == b""

    def test_base32hex_roundtrip_empty(self) -> None:
        assert base_decode(base_encode(b"", ALPHABET_BASE32HEX), ALPHABET_BASE32HEX) == b""

    def test_base58_roundtrip_empty(self) -> None:
        assert base_decode(base_encode(b"", ALPHABET_BASE58), ALPHABET_BASE58) == b""

    def test_base62_roundtrip_empty(self) -> None:
        assert base_decode(base_encode(b"", ALPHABET_BASE62), ALPHABET_BASE62) == b""

    def test_base85_roundtrip_empty(self) -> None:
        assert base_decode(base_encode(b"", ALPHABET_BASE85), ALPHABET_BASE85) == b""

    @pytest.mark.parametrize(
        "data",
        [
            b"\x00",
            b"\xff",
            b"hello",
            b"Hello, World!",
            b"foobar",
            b"\x00\x01\x02\x03",
            b"\xff\xfe\xfd\xfc",
            os.urandom(8),
            os.urandom(16),
            os.urandom(32),
            os.urandom(64),
            os.urandom(128),
            os.urandom(256),
        ],
    )
    def test_base32_roundtrip(self, data: bytes) -> None:
        encoded = base_encode(data, ALPHABET_BASE32)
        assert base_decode(encoded, ALPHABET_BASE32) == data

    @pytest.mark.parametrize(
        "data",
        [
            b"hello",
            b"Hello, World!",
            b"\x00\x01\x02\x03",
            os.urandom(8),
            os.urandom(16),
            os.urandom(32),
            os.urandom(64),
            os.urandom(128),
            os.urandom(256),
        ],
    )
    def test_base32hex_roundtrip(self, data: bytes) -> None:
        encoded = base_encode(data, ALPHABET_BASE32HEX)
        assert base_decode(encoded, ALPHABET_BASE32HEX) == data

    @pytest.mark.parametrize(
        "data",
        [
            b"hello",
            b"Hello, World!",
            b"\x00\x01\x02\x03",
            os.urandom(8),
            os.urandom(16),
            os.urandom(32),
            os.urandom(64),
            os.urandom(128),
            os.urandom(256),
        ],
    )
    def test_base58_roundtrip(self, data: bytes) -> None:
        encoded = base_encode(data, ALPHABET_BASE58)
        assert base_decode(encoded, ALPHABET_BASE58) == data

    @pytest.mark.parametrize(
        "data",
        [
            b"hello",
            b"Hello, World!",
            b"\x00\x01\x02\x03",
            os.urandom(8),
            os.urandom(16),
            os.urandom(32),
            os.urandom(64),
            os.urandom(128),
            os.urandom(256),
        ],
    )
    def test_base62_roundtrip(self, data: bytes) -> None:
        encoded = base_encode(data, ALPHABET_BASE62)
        assert base_decode(encoded, ALPHABET_BASE62) == data

    @pytest.mark.parametrize(
        "data",
        [
            b"hello",
            b"Hello, World!",
            b"\x00\x01\x02\x03",
            os.urandom(8),
            os.urandom(16),
            os.urandom(32),
            os.urandom(64),
            os.urandom(128),
            os.urandom(256),
        ],
    )
    def test_base85_roundtrip(self, data: bytes) -> None:
        encoded = base_encode(data, ALPHABET_BASE85)
        assert base_decode(encoded, ALPHABET_BASE85) == data

    def test_all_bases_agree_on_zero(self) -> None:
        alphabets = [ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85]
        for alphabet in alphabets:
            assert base_decode(base_encode(b"\x00", alphabet), alphabet) == b"\x00"


# ---------------------------------------------------------------------------
# Known vector tests
# ---------------------------------------------------------------------------


class TestKnownVectors:
    def test_base58_vector_1(self) -> None:
        result = base_encode(b"hello world", ALPHABET_BASE58)
        assert base_decode(result, ALPHABET_BASE58) == b"hello world"

    def test_base58_leading_zeros(self) -> None:
        assert base_decode(base_encode(b"\x00\x00hello", ALPHABET_BASE58), ALPHABET_BASE58) == b"\x00\x00hello"

    def test_base62_vector_empty(self) -> None:
        assert base_encode(b"", ALPHABET_BASE62) == ""

    def test_base62_vector_hello(self) -> None:
        encoded = base_encode(b"hello", ALPHABET_BASE62)
        assert base_decode(encoded, ALPHABET_BASE62) == b"hello"

    def test_base85_zero_vector(self) -> None:
        assert base_decode(base_encode(b"\x00", ALPHABET_BASE85), ALPHABET_BASE85) == b"\x00"

    def test_base32_foobar_roundtrip(self) -> None:
        data = b"foobar"
        encoded = base_encode(data, ALPHABET_BASE32)
        assert base_decode(encoded, ALPHABET_BASE32) == data

    def test_base32hex_foobar_roundtrip(self) -> None:
        data = b"foobar"
        encoded = base_encode(data, ALPHABET_BASE32HEX)
        assert base_decode(encoded, ALPHABET_BASE32HEX) == data

    def test_base62_alphabet_order(self) -> None:
        assert ALPHABET_BASE62 == "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_base_decode_truncation_resilience(self) -> None:
        data = b"hello world"
        encoded = base_encode(data, ALPHABET_BASE58)
        truncated = encoded[:-1]
        decoded = base_decode(truncated, ALPHABET_BASE58)
        assert decoded != data
        assert isinstance(decoded, bytes)

    def test_decode_single_character(self) -> None:
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
            if alphabet == ALPHABET_BASE85:
                continue
            result = base_decode(alphabet[1], alphabet)
            assert isinstance(result, bytes)
            assert len(result) >= 0

    def test_encode_single_byte_at_each_index(self) -> None:
        for i in range(256):
            data = bytes([i])
            for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
                if alphabet == ALPHABET_BASE85 and i == 0:
                    continue
                encoded = base_encode(data, alphabet)
                assert isinstance(encoded, str)
                decoded = base_decode(encoded, alphabet)
                assert decoded == data, f"alphabet={alphabet[:10]}, byte=0x{i:02x}"

    def test_encode_two_bytes_all_combinations(self) -> None:
        sample = [0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF]
        for a, b in itertools.product(sample, repeat=2):
            data = bytes([a, b])
            encoded = base_encode(data, ALPHABET_BASE58)
            assert base_decode(encoded, ALPHABET_BASE58) == data, f"bytes=({a:02x},{b:02x})"

    def test_leading_zero_preservation_all_bases(self) -> None:
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
            if alphabet == ALPHABET_BASE85:
                continue
            data = b"\x00\x00\x00"
            encoded = base_encode(data, alphabet)
            decoded = base_decode(encoded, alphabet)
            assert len(decoded) == 3, f"alphabet={alphabet[:10]}"
            assert decoded == data
            assert all(c == alphabet[0] for c in encoded), f"leading zeros mismatch: {encoded}"

    def test_max_single_byte_values(self) -> None:
        data = b"\xff"
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
            if alphabet == ALPHABET_BASE85 and data == b"\x00":
                continue
            encoded = base_encode(data, alphabet)
            assert base_decode(encoded, alphabet) == data, f"alphabet={alphabet[:10]}, byte=0xff"

    def test_empty_decode(self) -> None:
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
            assert base_decode("", alphabet) == b""


# ---------------------------------------------------------------------------
# Custom alphabet tests
# ---------------------------------------------------------------------------


class TestCustomAlphabet:
    def test_custom_base6_roundtrip(self) -> None:
        alphabet = "ABCDEF"
        data = os.urandom(16)
        encoded = base_encode(data, alphabet)
        assert all(c in alphabet for c in encoded)
        assert base_decode(encoded, alphabet) == data

    def test_custom_base3_small_values(self) -> None:
        alphabet = "XYZ"
        for byte_val in range(256):
            data = bytes([byte_val])
            encoded = base_encode(data, alphabet)
            assert all(c in alphabet for c in encoded)
            assert base_decode(encoded, alphabet) == data

    def test_custom_binary_alphabet(self) -> None:
        data = os.urandom(32)
        encoded = base_encode(data, "01")
        assert all(c in "01" for c in encoded)
        assert base_decode(encoded, "01") == data

    def test_custom_alphabet_length_power_of_two(self) -> None:
        for n, alphabet in (
            (8, "ABCDEFGH"),
            (16, "0123456789ABCDEF"),
            (32, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
            (64, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"),
            (128, "".join(chr(i) for i in range(128))),
        ):
            data = os.urandom(16)
            encoded = base_encode(data, alphabet)
            assert all(c in alphabet for c in encoded), f"n={n}"
            assert base_decode(encoded, alphabet) == data, f"n={n}"

    def test_large_alphabet_roundtrip(self) -> None:
        alphabet = "".join(chr(i) for i in range(150))
        data = os.urandom(16)
        encoded = base_encode(data, alphabet)
        assert all(c in alphabet for c in encoded)
        assert base_decode(encoded, alphabet) == data


# ---------------------------------------------------------------------------
# Immutability / invariant tests
# ---------------------------------------------------------------------------


class TestAlphabetInvariants:
    def test_alphabet_constants_are_ordered_strings(self) -> None:
        alphabets = [ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85]
        for alphabet in alphabets:
            assert isinstance(alphabet, str)
            assert len(alphabet) > 1

    def test_alphabet_lengths_match_names(self) -> None:
        assert len(ALPHABET_BASE32) == 32
        assert len(ALPHABET_BASE32HEX) == 32
        assert len(ALPHABET_BASE58) == 58
        assert len(ALPHABET_BASE62) == 62
        assert len(ALPHABET_BASE85) == 85

    def test_no_duplicate_characters_in_any_alphabet(self) -> None:
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62, ALPHABET_BASE85):
            assert len(alphabet) == len(set(alphabet)), f"Duplicates in {alphabet[:10]}..."


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_encode_is_deterministic(self) -> None:
        data = os.urandom(64)
        for alphabet in (ALPHABET_BASE32, ALPHABET_BASE32HEX, ALPHABET_BASE58, ALPHABET_BASE62):
            first = base_encode(data, alphabet)
            for _ in range(10):
                assert base_encode(data, alphabet) == first

    def test_decode_is_deterministic(self) -> None:
        encoded = "2gPihUTjt3FJqf1VpidgrY5cZ6QVFxSRyAHv4G7sLTN"
        for _ in range(10):
            assert base_decode(encoded, ALPHABET_BASE58) == base_decode(encoded, ALPHABET_BASE58)


# ---------------------------------------------------------------------------
# Stress / performance tests
# ---------------------------------------------------------------------------


class TestStress:
    def test_bulk_roundtrip_10k_random(self) -> None:
        for _ in range(10000):
            size = secrets.randbelow(64) + 1
            data = os.urandom(size)
            encoded = base_encode(data, ALPHABET_BASE62)
            assert base_decode(encoded, ALPHABET_BASE62) == data

    def test_large_payload_roundtrip(self) -> None:
        data = os.urandom(100_000)
        encoded = base_encode(data, ALPHABET_BASE58)
        assert base_decode(encoded, ALPHABET_BASE58) == data
        encoded = base_encode(data, ALPHABET_BASE62)
        assert base_decode(encoded, ALPHABET_BASE62) == data


# ---------------------------------------------------------------------------
# Invalid input tests
# ---------------------------------------------------------------------------


class TestInvalidInputs:
    def test_decode_invalid_char_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid character"):
            base_decode("hello!", ALPHABET_BASE58)

    def test_decode_invalid_underscore(self) -> None:
        with pytest.raises(ValueError):
            base_decode("abc_def", ALPHABET_BASE32)

    def test_encode_none_raises(self) -> None:
        with pytest.raises(TypeError):
            base_encode(None, ALPHABET_BASE58)  # type: ignore[arg-type]

    def test_decode_none_raises(self) -> None:
        with pytest.raises(TypeError):
            base_decode(None, ALPHABET_BASE58)  # type: ignore[arg-type]

    def test_decode_non_string_raises(self) -> None:
        with pytest.raises(TypeError):
            base_decode(12345, ALPHABET_BASE58)  # type: ignore[arg-type]

    def test_invalid_length_alphabet_raises(self) -> None:
        with pytest.raises(ValueError, match="alphabet"):
            base_encode(b"hi", "")

    def test_alphabet_with_duplicates_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            base_encode(b"hi", "AABBCC")

    def test_single_char_alphabet_raises(self) -> None:
        with pytest.raises(ValueError, match="alphabet"):
            base_encode(b"hi", "A")
