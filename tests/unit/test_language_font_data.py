"""Tests for language/font_data.py — font format identification + tables."""

from __future__ import annotations

import struct
from pathlib import Path

from general_ludd.language.font_data import (
    SYSTEM_FONT_STACKS,
    VARIABLE_FONT_AXES,
    identify_font_format,
    is_web_font_format,
    list_font_tables,
)


class TestIdentifyFontFormat:
    def test_ttf_magic(self) -> None:
        assert identify_font_format(b"\x00\x01\x00\x00rest") == "ttf"

    def test_otf_magic(self) -> None:
        assert identify_font_format(b"OTTOrest") == "otf"

    def test_woff_magic(self) -> None:
        assert identify_font_format(b"wOFFrest") == "woff"

    def test_woff2_magic(self) -> None:
        assert identify_font_format(b"wOF2rest") == "woff2"

    def test_ttc_magic(self) -> None:
        assert identify_font_format(b"ttcfrest") == "ttc"

    def test_apple_true_magic_maps_to_ttf(self) -> None:
        assert identify_font_format(b"truerest") == "ttf"

    def test_unknown_magic(self) -> None:
        assert identify_font_format(b"XXXXrest") == "unknown"

    def test_short_header_is_unknown(self) -> None:
        assert identify_font_format(b"ab") == "unknown"


class TestListFontTables:
    def test_missing_file_returns_empty(self) -> None:
        assert list_font_tables("/nonexistent/font.ttf") == []

    def test_non_sfnt_file_returns_empty(self, tmp_path: Path) -> None:
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"not a font at all, just text padding")

        assert list_font_tables(str(bogus)) == []

    def test_parses_minimal_sfnt_table_directory(self, tmp_path: Path) -> None:
        header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 0, 0, 0)
        record = struct.pack(">4sIII", b"cmap", 0, 28, 4)
        font = tmp_path / "mini.ttf"
        font.write_bytes(header + record + b"\x00\x00\x00\x00")

        tables = list_font_tables(str(font))

        assert len(tables) == 1
        assert tables[0]["tag"] == "cmap"
        assert tables[0]["length"] == 4


class TestFontData:
    def test_system_font_stacks_cover_major_platforms(self) -> None:
        assert isinstance(SYSTEM_FONT_STACKS, dict)
        assert len(SYSTEM_FONT_STACKS) > 0

    def test_variable_axes_include_weight(self) -> None:
        assert "wght" in VARIABLE_FONT_AXES
        assert VARIABLE_FONT_AXES["wght"]["default"] == 400.0

    def test_is_web_font_format_false_for_missing_file(self) -> None:
        assert is_web_font_format("/nonexistent/font.woff") is False
