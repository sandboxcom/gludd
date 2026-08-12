"""Tests for src/general_ludd/encoding_converter.py"""

from __future__ import annotations

from general_ludd.encoding_converter import (
    convert,
    decode_all,
    decode_with_bom,
    detect_bom,
    guess_encoding,
    roundtrip,
)


class TestConvert:
    def test_str_utf8_to_latin1(self):
        result = convert("caf\u00e9", "utf-8", "iso-8859-1")
        assert isinstance(result, bytes)
        assert result == b"caf\xe9"

    def test_bytes_between_encodings(self):
        raw = b"hello"
        result = convert(raw, "ascii", "utf-8")
        assert result == "hello"

    def test_str_utf8_to_utf8(self):
        result = convert("hello", "utf-8", "utf-8")
        assert result == "hello"


class TestDetectBOM:
    def test_utf8_bom(self):
        data = b"\xef\xbb\xbfhello"
        assert detect_bom(data) == "utf-8"

    def test_utf16_le_bom(self):
        data = b"\xff\xfeh\x00e\x00"
        assert detect_bom(data) == "utf-16-le"

    def test_utf16_be_bom(self):
        data = b"\xfe\xff\x00h\x00e"
        assert detect_bom(data) == "utf-16-be"

    def test_utf32_le_bom(self):
        data = b"\xff\xfe\x00\x00hello"
        assert detect_bom(data) == "utf-32-le"

    def test_utf32_be_bom(self):
        data = b"\x00\x00\xfe\xffhello"
        assert detect_bom(data) == "utf-32-be"

    def test_no_bom(self):
        assert detect_bom(b"hello") is None

    def test_empty(self):
        assert detect_bom(b"") is None


class TestDecodeWithBOM:
    def test_utf8_bom_decoded(self):
        data = b"\xef\xbb\xbfhello"
        assert decode_with_bom(data) == "hello"

    def test_no_bom_falls_through(self):
        assert decode_with_bom(b"hello") == "hello"

    def test_utf16_le_bom_decoded(self):
        data = b"\xff\xfeh\x00e\x00"
        assert decode_with_bom(data) == "he"


class TestDecodeAll:
    def test_valid_utf8(self):
        assert decode_all(b"hello") == "hello"

    def test_fallback_to_latin1(self):
        data = bytes(range(128, 256))
        result = decode_all(data)
        assert isinstance(result, str)


class TestGuessEncoding:
    def test_ascii(self):
        assert guess_encoding(b"hello") == "ascii"

    def test_utf8(self):
        assert guess_encoding("\u20ac".encode("utf-8")) == "utf-8"

    def test_utf16_le_from_zeros(self):
        data = b"\x00\xc0\x00\xc1\x00\xc2"
        assert guess_encoding(data) == "utf-16-le"

    def test_utf16_be_from_zeros(self):
        data = b"\xc0\x00\xc1\x00\xc2\x00"
        assert guess_encoding(data) == "utf-16-be"

    def test_none_when_unknown(self):
        assert guess_encoding(b"\xff\xff\xff\xff") is None


class TestRoundtrip:
    def test_utf8_roundtrip(self):
        text = "Hello, \u2603"
        assert roundtrip(text, "utf-8") == text

    def test_latin1_roundtrip(self):
        text = "".join(chr(i) for i in range(256))
        result = roundtrip(text, "iso-8859-1")
        assert result == text
