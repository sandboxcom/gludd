"""Deep tests for encoding_converter — UTF-8/16/32, ISO-8859-1, CP-1252,
BOM detection, roundtrip, invalid byte handling.
"""

from __future__ import annotations

import pytest

from general_ludd.encoding_converter import (
    convert,
    decode_all,
    decode_with_bom,
    detect_bom,
    guess_encoding,
    roundtrip,
)


def _bom(encoding: str) -> bytes:
    mapping = {
        "utf-8": b"\xef\xbb\xbf",
        "utf-16-le": b"\xff\xfe",
        "utf-16-be": b"\xfe\xff",
        "utf-32-le": b"\xff\xfe\x00\x00",
        "utf-32-be": b"\x00\x00\xfe\xff",
    }
    return mapping[encoding]


# ── UTF-8 ──────────────────────────────────────────────────────────


class TestUtf8:
    def test_encode_basic_ascii(self) -> None:
        result = convert("hello", "utf-8", "utf-8")
        assert result == "hello"
        le_bytes = convert("hello", "utf-8", "utf-16-le")
        assert "hello".encode("utf-16-le") == le_bytes

    def test_encode_multilingual(self) -> None:
        text = "Hello, 世界! ñoño — café"
        result = convert(text, "utf-8", "utf-8")
        assert result == text
        decoded = convert(text.encode("utf-8"), "utf-8", "utf-8")
        assert decoded == text

    def test_decode_bom_prefixed(self) -> None:
        result = decode_with_bom(b"\xef\xbb\xbfhello")
        assert result == "hello"


# ── UTF-16 ─────────────────────────────────────────────────────────


class TestUtf16:
    def test_encode_decode_le(self) -> None:
        text = "Hello, 世界!"
        encoded = convert(text, "utf-8", "utf-16-le")
        decoded = convert(encoded, "utf-16-le", "utf-8")
        assert decoded == text

    def test_encode_decode_be(self) -> None:
        text = "Hello, 世界!"
        encoded = convert(text, "utf-8", "utf-16-be")
        decoded = convert(encoded, "utf-16-be", "utf-8")
        assert decoded == text

    def test_bom_present_on_le(self) -> None:
        text = "hello"
        encoded = convert(text, "utf-8", "utf-16")
        assert encoded[:2] in (b"\xff\xfe", b"\xfe\xff")

    def test_surrogate_pairs(self) -> None:
        text = "\U0001f600\U0001f4a9"
        encoded = convert(text, "utf-8", "utf-16-le")
        decoded = convert(encoded, "utf-16-le", "utf-8")
        assert decoded == text


# ── UTF-32 ─────────────────────────────────────────────────────────


class TestUtf32:
    def test_encode_decode_le(self) -> None:
        text = "Hello, 世界!"
        encoded = convert(text, "utf-8", "utf-32-le")
        decoded = convert(encoded, "utf-32-le", "utf-8")
        assert decoded == text

    def test_encode_decode_be(self) -> None:
        text = "Hello, 世界!"
        encoded = convert(text, "utf-8", "utf-32-be")
        decoded = convert(encoded, "utf-32-be", "utf-8")
        assert decoded == text

    def test_wide_characters(self) -> None:
        text = "\U0001f600\U0001f4a9"
        encoded = convert(text, "utf-8", "utf-32-le")
        decoded = convert(encoded, "utf-32-le", "utf-8")
        assert decoded == text


# ── ISO-8859-1 (Latin-1) ──────────────────────────────────────────


class TestIso88591:
    def test_encode_decode_all_bytes(self) -> None:
        for i in range(256):
            raw = bytes([i])
            try:
                decoded = convert(raw, "iso-8859-1", "utf-8")
                reencoded = convert(decoded, "utf-8", "iso-8859-1")
                round_trip = convert(reencoded, "iso-8859-1", "utf-8")
                assert round_trip == decoded
            except UnicodeError:
                pass

    def test_accented_characters(self) -> None:
        text = "café naïve über"
        encoded = convert(text, "utf-8", "iso-8859-1")
        decoded = convert(encoded, "iso-8859-1", "utf-8")
        assert decoded == text

    def test_invalid_utf8_to_latin1(self) -> None:
        raw = b"\xff\xfe"
        with pytest.raises((UnicodeDecodeError, UnicodeEncodeError, ValueError)):
            convert(raw, "utf-8", "iso-8859-1")


# ── CP-1252 (Windows-1252) ────────────────────────────────────────


class TestCp1252:
    def test_encode_decode_common(self) -> None:
        text = "café naïve — résumé"
        encoded = convert(text, "utf-8", "cp1252")
        decoded = convert(encoded, "cp1252", "utf-8")
        assert decoded == text

    def test_smart_quotes_roundtrip(self) -> None:
        text = "\u201cHello\u201d \u2018world\u2019"
        encoded = convert(text, "utf-8", "cp1252")
        decoded = convert(encoded, "cp1252", "utf-8")
        assert decoded == text

    def test_euro_sign(self) -> None:
        text = "\u20ac100"
        encoded = convert(text, "utf-8", "cp1252")
        decoded = convert(encoded, "cp1252", "utf-8")
        assert decoded == text


# ── BOM Detection ─────────────────────────────────────────────────


class TestBomDetection:
    def test_detect_utf8_bom(self) -> None:
        assert detect_bom(b"\xef\xbb\xbfhello") == "utf-8"

    def test_detect_utf16_le_bom(self) -> None:
        assert detect_bom(b"\xff\xfeh\x00e\x00") == "utf-16-le"

    def test_detect_utf16_be_bom(self) -> None:
        assert detect_bom(b"\xfe\xff\x00h\x00e") == "utf-16-be"

    def test_detect_utf32_le_bom(self) -> None:
        assert detect_bom(b"\xff\xfe\x00\x00h\x00\x00\x00") == "utf-32-le"

    def test_detect_utf32_be_bom(self) -> None:
        assert detect_bom(b"\x00\x00\xfe\xff\x00\x00\x00h") == "utf-32-be"

    def test_no_bom_returns_none(self) -> None:
        assert detect_bom(b"hello") is None
        assert detect_bom(b"") is None

    def test_decode_with_bom_utf8(self) -> None:
        result = decode_with_bom(b"\xef\xbb\xbfhello")
        assert result == "hello"

    def test_decode_with_bom_utf16(self) -> None:
        result = decode_with_bom(b"\xff\xfeh\x00e\x00l\x00l\x00o\x00")
        assert result == "hello"


# ── Roundtrip ─────────────────────────────────────────────────────


class TestRoundtrip:
    @pytest.mark.parametrize(
        "text,encoding",
        [
            ("Hello, World!", "utf-16-le"),
            ("Hello, 世界!", "utf-16-le"),
            ("café — résumé", "cp1252"),
            ("naïve über", "iso-8859-1"),
            ("\U0001f600", "utf-32-le"),
            ("", "utf-16-le"),
            ("a" * 1000, "utf-32-le"),
        ],
    )
    def test_roundtrip_function(self, text: str, encoding: str) -> None:
        result = roundtrip(text, encoding)
        assert result == text

    def test_roundtrip_utf16_to_latin1_to_utf16(self) -> None:
        text = "café"
        assert roundtrip(text, "utf-16-le") == text
        assert roundtrip(text, "iso-8859-1") == text


# ── Invalid Bytes / Error Handling ────────────────────────────────


class TestInvalidBytes:
    def test_utf8_decode_invalid_surrogate(self) -> None:
        raw = b"\xed\xa0\x80"
        with pytest.raises((UnicodeDecodeError, ValueError)):
            result = convert(raw, "utf-8", "utf-8")
            if isinstance(result, str):
                result.encode("utf-8")

    def test_decode_all_fallback(self) -> None:
        result = decode_all(b"\xff\xfe\x00\x00", encodings=["utf-8", "utf-16-le"])
        assert isinstance(result, str)

    def test_guess_encoding_utf16(self) -> None:
        raw = b"\xff\xfeh\x00e\x00l\x00l\x00o\x00"
        result = guess_encoding(raw)
        assert result in ("utf-16-le", "utf-16")

    def test_guess_encoding_utf8(self) -> None:
        raw = "Hello, 世界!".encode()
        result = guess_encoding(raw)
        assert "utf" in result.lower()

    def test_guess_encoding_ascii(self) -> None:
        result = guess_encoding(b"plain ascii text")
        assert result in ("ascii", "utf-8", None)


# ── Edge Cases ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_input(self) -> None:
        assert convert("", "utf-8", "utf-16-le") == b""
        assert convert(b"", "utf-16-le", "utf-8") == ""

    def test_convert_identity(self) -> None:
        assert convert("hello", "utf-8", "utf-8") == "hello"

    def test_error_strict_mode(self) -> None:
        with pytest.raises((UnicodeDecodeError, UnicodeEncodeError, ValueError)):
            convert(b"\x80", "utf-8", "utf-8")

    def test_escape_unicode_null(self) -> None:
        text = "hello\x00world"
        encoded = convert(text, "utf-8", "utf-16-le")
        decoded = convert(encoded, "utf-16-le", "utf-8")
        assert decoded == text
