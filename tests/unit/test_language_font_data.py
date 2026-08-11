"""Deep tests for language/font_data.py — full coverage of all functions and data."""

from __future__ import annotations

import struct
from pathlib import Path

from general_ludd.language.font_data import (
    FONT_FORMAT_SPECS,
    OPENTYPE_OPTIONAL_TABLES,
    OPENTYPE_REQUIRED_TABLES,
    SYSTEM_FONT_STACKS,
    VARIABLE_FONT_AXES,
    get_font_metrics,
    has_kerning,
    has_variable_axes,
    identify_font_format,
    is_web_font_format,
    list_font_tables,
)

# ── Helpers for building binary font fixtures ────────────────────────────


def _make_sfnt_header(
    sfversion: bytes = b"\x00\x01\x00\x00",
    num_tables: int = 0,
) -> bytes:
    return sfversion + struct.pack(">HHHH", num_tables, 0, 0, 0)


def _make_table_record(
    tag: str,
    offset: int,
    length: int,
    checksum: int = 0,
) -> bytes:
    tag_bytes = tag.encode("ascii").ljust(4, b"\x00")
    return struct.pack(">4sIII", tag_bytes, checksum, offset, length)


def _make_head_table(em_units: int = 2048) -> bytes:
    """Minimal 'head' table with emUnits at bytes 18-19."""
    data = bytearray(20)
    data[0:4] = struct.pack(">I", 0x00010000)  # version
    data[4:8] = struct.pack(">I", 0x00010000)  # fontRevision
    data[18:20] = struct.pack(">H", em_units)
    return bytes(data)


def _make_hhea_table(ascent: int = 1000, descent: int = -200, line_gap: int = 0) -> bytes:
    """Minimal 'hhea' table with metrics at bytes 4-9."""
    data = bytearray(10)
    data[0:4] = struct.pack(">I", 0x00010000)  # version
    data[4:6] = struct.pack(">h", ascent)
    data[6:8] = struct.pack(">h", descent)
    data[8:10] = struct.pack(">h", line_gap)
    return bytes(data)


def _write_font_with_tables(
    path: Path,
    sfversion: bytes = b"\x00\x01\x00\x00",
    tables: list[tuple[str, int, bytes]] | None = None,
    extra_bytes: bytes = b"",
) -> None:
    """Write an SFNT-wrapped font file to *path*.

    Each table is (tag, checksum, data). offset/length computed automatically.
    """
    if tables is None:
        tables = []
    num_tables = len(tables)

    header_size = 12
    record_size = num_tables * 16
    data_offset = header_size + record_size

    header = _make_sfnt_header(sfversion, num_tables)
    records = bytearray()
    data_block = bytearray()

    for tag, checksum, tdata in tables:
        offset = data_offset + len(data_block)
        length = len(tdata)
        records += _make_table_record(tag, offset, length, checksum)
        data_block += tdata

    font_bytes = header + bytes(records) + bytes(data_block) + extra_bytes
    path.write_bytes(font_bytes)


# ── FONT_FORMAT_SPECS ────────────────────────────────────────────────────


class TestFontFormatSpecs:
    def test_five_formats(self):
        assert set(FONT_FORMAT_SPECS.keys()) == {"ttf", "otf", "woff", "woff2", "ttc"}

    def test_all_have_required_keys(self):
        required = {"magic", "mime", "description"}
        for fmt, spec in FONT_FORMAT_SPECS.items():
            assert required <= set(spec.keys()), f"{fmt} missing keys: {required - set(spec.keys())}"

    def test_all_magic_is_bytes(self):
        for fmt, spec in FONT_FORMAT_SPECS.items():
            assert isinstance(spec["magic"], bytes), f"{fmt} magic is not bytes"
            assert len(spec["magic"]) == 4, f"{fmt} magic length != 4"

    def test_all_mime_are_non_empty_strings(self):
        for _fmt, spec in FONT_FORMAT_SPECS.items():
            assert isinstance(spec["mime"], str) and len(spec["mime"]) > 0

    def test_all_descriptions_non_empty(self):
        for _fmt, spec in FONT_FORMAT_SPECS.items():
            assert isinstance(spec["description"], str) and len(spec["description"]) > 0

    def test_magic_bytes_unique(self):
        magics = [spec["magic"] for spec in FONT_FORMAT_SPECS.values()]
        assert len(magics) == len(set(magics)), "FONT_FORMAT_SPECS magic bytes collide"

    def test_identify_matches_all_spec_magic_bytes(self):
        for fmt, spec in FONT_FORMAT_SPECS.items():
            assert identify_font_format(spec["magic"] + b"padding") == fmt


# ── identify_font_format ─────────────────────────────────────────────────


class TestIdentifyFontFormat:
    def test_ttf_magic(self):
        assert identify_font_format(b"\x00\x01\x00\x00rest") == "ttf"

    def test_otf_magic(self):
        assert identify_font_format(b"OTTOrest") == "otf"

    def test_woff_magic(self):
        assert identify_font_format(b"wOFFrest") == "woff"

    def test_woff2_magic(self):
        assert identify_font_format(b"wOF2rest") == "woff2"

    def test_ttc_magic(self):
        assert identify_font_format(b"ttcfrest") == "ttc"

    def test_apple_true_magic_maps_to_ttf(self):
        assert identify_font_format(b"truerest") == "ttf"

    def test_typ1_magic_maps_to_ttf(self):
        assert identify_font_format(b"typ1rest") == "ttf"

    def test_unknown_magic(self):
        assert identify_font_format(b"XXXXrest") == "unknown"

    def test_short_header_is_unknown(self):
        assert identify_font_format(b"ab") == "unknown"

    def test_exactly_four_bytes(self):
        assert identify_font_format(b"\x00\x01\x00\x00") == "ttf"
        assert identify_font_format(b"OTTO") == "otf"

    def test_empty_header_is_unknown(self):
        assert identify_font_format(b"") == "unknown"


# ── SYSTEM_FONT_STACKS ───────────────────────────────────────────────────


class TestSystemFontStacks:
    def test_all_five_platforms(self):
        assert set(SYSTEM_FONT_STACKS.keys()) == {"macos", "windows", "linux", "ios", "android"}

    def test_each_platform_has_three_categories(self):
        for platform in SYSTEM_FONT_STACKS:
            assert set(SYSTEM_FONT_STACKS[platform].keys()) == {"sans-serif", "serif", "monospace"}, (
                f"{platform} missing stack categories"
            )

    def test_all_stacks_are_non_empty_strings(self):
        for platform, categories in SYSTEM_FONT_STACKS.items():
            for cat, stack in categories.items():
                assert isinstance(stack, str) and len(stack) > 0, f"{platform}.{cat} empty"

    def test_sans_serif_includes_sans_serif_keyword(self):
        for platform, categories in SYSTEM_FONT_STACKS.items():
            assert "sans-serif" in categories["sans-serif"], f"{platform} sans-serif stack missing fallback"

    def test_serif_includes_serif_keyword(self):
        for platform, categories in SYSTEM_FONT_STACKS.items():
            assert "serif" in categories["serif"], f"{platform} serif stack missing fallback"

    def test_monospace_includes_monospace_keyword(self):
        for platform, categories in SYSTEM_FONT_STACKS.items():
            assert "monospace" in categories["monospace"], f"{platform} monospace stack missing fallback"

    def test_macos_uses_apple_system(self):
        assert "-apple-system" in SYSTEM_FONT_STACKS["macos"]["sans-serif"]

    def test_windows_uses_segoe_ui(self):
        assert "Segoe UI" in SYSTEM_FONT_STACKS["windows"]["sans-serif"]

    def test_android_uses_roboto(self):
        assert "Roboto" in SYSTEM_FONT_STACKS["android"]["sans-serif"]


# ── OPENTYPE_REQUIRED_TABLES ─────────────────────────────────────────────


class TestOpenTypeRequiredTables:
    def test_non_empty_list(self):
        assert isinstance(OPENTYPE_REQUIRED_TABLES, list)
        assert len(OPENTYPE_REQUIRED_TABLES) > 0

    def test_all_are_strings(self):
        for table in OPENTYPE_REQUIRED_TABLES:
            assert isinstance(table, str)

    def test_core_tables_present(self):
        assert "cmap" in OPENTYPE_REQUIRED_TABLES
        assert "head" in OPENTYPE_REQUIRED_TABLES
        assert "hhea" in OPENTYPE_REQUIRED_TABLES
        assert "hmtx" in OPENTYPE_REQUIRED_TABLES
        assert "maxp" in OPENTYPE_REQUIRED_TABLES
        assert "name" in OPENTYPE_REQUIRED_TABLES
        assert "OS/2" in OPENTYPE_REQUIRED_TABLES
        assert "post" in OPENTYPE_REQUIRED_TABLES

    def test_all_lowercase_or_mixed(self):
        for table in OPENTYPE_REQUIRED_TABLES:
            assert table == table.strip()
            assert len(table) >= 2


class TestOpenTypeOptionalTables:
    def test_non_empty(self):
        assert len(OPENTYPE_OPTIONAL_TABLES) > 5

    def test_all_strings(self):
        for table in OPENTYPE_OPTIONAL_TABLES:
            assert isinstance(table, str)

    def test_gsub_gpos_glyf_present(self):
        assert "GSUB" in OPENTYPE_OPTIONAL_TABLES
        assert "GPOS" in OPENTYPE_OPTIONAL_TABLES
        assert "glyf" in OPENTYPE_OPTIONAL_TABLES

    def test_no_overlap_with_required(self):
        overlap = set(OPENTYPE_REQUIRED_TABLES) & set(OPENTYPE_OPTIONAL_TABLES)
        assert not overlap, f"Tables in both required and optional: {overlap}"


# ── VARIABLE_FONT_AXES ───────────────────────────────────────────────────


class TestVariableFontAxes:
    def test_five_axes(self):
        assert set(VARIABLE_FONT_AXES.keys()) == {"wght", "wdth", "ital", "slnt", "opsz"}

    def test_all_have_required_keys(self):
        required = {"name", "min", "max", "default"}
        for axis, spec in VARIABLE_FONT_AXES.items():
            assert required <= set(spec.keys()), f"{axis} missing keys: {required - set(spec.keys())}"

    def test_all_names_are_non_empty_strings(self):
        for _axis, spec in VARIABLE_FONT_AXES.items():
            assert isinstance(spec["name"], str) and len(spec["name"]) > 0

    def test_default_within_min_max(self):
        for axis, spec in VARIABLE_FONT_AXES.items():
            assert spec["min"] <= spec["default"] <= spec["max"], (
                f"{axis}: default {spec['default']} not in [{spec['min']}, {spec['max']}]"
            )

    def test_weight_axis_sensible(self):
        wght = VARIABLE_FONT_AXES["wght"]
        assert wght["min"] <= 200
        assert wght["max"] >= 900

    def test_italic_is_zero_one_range(self):
        ital = VARIABLE_FONT_AXES["ital"]
        assert ital["min"] == 0.0
        assert ital["max"] == 1.0


# ── list_font_tables ─────────────────────────────────────────────────────


class TestListFontTables:
    def test_missing_file_returns_empty(self):
        assert list_font_tables("/nonexistent/font.ttf") == []

    def test_empty_path_returns_empty(self):
        assert list_font_tables("") == []

    def test_non_sfnt_file_returns_empty(self, tmp_path: Path):
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"not a font at all, just text padding")
        assert list_font_tables(str(bogus)) == []

    def test_short_header_returns_empty(self, tmp_path: Path):
        short = tmp_path / "short.ttf"
        short.write_bytes(b"ab")
        assert list_font_tables(str(short)) == []

    def test_parses_ttf_with_one_table(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "mini.ttf",
            tables=[
                ("cmap", 0, b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
            ],
        )
        tables = list_font_tables(str(tmp_path / "mini.ttf"))
        assert len(tables) == 1
        assert tables[0]["tag"] == "cmap"
        assert tables[0]["length"] == 10

    def test_parses_ttf_with_multiple_tables(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "multi.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 8),
                ("head", 0, b"\x00" * 20),
                ("hhea", 0, b"\x00" * 16),
            ],
        )
        tables = list_font_tables(str(tmp_path / "multi.ttf"))
        assert len(tables) == 3
        tags = {t["tag"] for t in tables}
        assert tags == {"cmap", "head", "hhea"}

    def test_accepts_otf_sfversion(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "otf_font.otf",
            sfversion=b"OTTO",
            tables=[("GSUB", 0, b"\x00" * 4)],
        )
        tables = list_font_tables(str(tmp_path / "otf_font.otf"))
        assert len(tables) == 1
        assert tables[0]["tag"] == "GSUB"

    def test_accepts_true_sfversion(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "apple.ttf",
            sfversion=b"true",
            tables=[("kern", 0, b"\x00" * 4)],
        )
        tables = list_font_tables(str(tmp_path / "apple.ttf"))
        assert len(tables) == 1
        assert tables[0]["tag"] == "kern"

    def test_accepts_typ1_sfversion(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "ps1.ttf",
            sfversion=b"typ1",
            tables=[("CFF ", 0, b"\x00" * 4)],
        )
        tables = list_font_tables(str(tmp_path / "ps1.ttf"))
        assert len(tables) == 1

    def test_record_padding_handled(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "pad.ttf",
            tables=[
                ("cmap", 0, b"\x01" * 5),
            ],
            extra_bytes=b"\x00" * 32,
        )
        tables = list_font_tables(str(tmp_path / "pad.ttf"))
        assert len(tables) == 1
        assert tables[0]["length"] == 5

    def test_record_returns_type_dict(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "typed.ttf",
            tables=[
                ("OS/2", 0, b"\x00" * 12),
            ],
        )
        tables = list_font_tables(str(tmp_path / "typed.ttf"))
        assert len(tables) == 1
        t = tables[0]
        assert isinstance(t, dict)
        assert isinstance(t["tag"], str)
        assert isinstance(t["offset"], int)
        assert isinstance(t["length"], int)


# ── get_font_metrics ─────────────────────────────────────────────────────


class TestGetFontMetrics:
    def test_missing_file_returns_error(self):
        result = get_font_metrics("/nonexistent/font.ttf")
        assert "error" in result
        assert result["error"]

    def test_empty_path_returns_error(self):
        result = get_font_metrics("")
        assert "error" in result

    def test_short_file_returns_error(self, tmp_path: Path):
        short = tmp_path / "short.ttf"
        short.write_bytes(b"x")
        result = get_font_metrics(str(short))
        assert "error" in result

    def test_non_sfnt_returns_error(self, tmp_path: Path):
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"not a valid font header here")
        result = get_font_metrics(str(bogus))
        assert "error" in result

    def test_woff_returns_unsupported_error(self, tmp_path: Path):
        woff = tmp_path / "web.woff"
        woff.write_bytes(b"wOFF" + b"\x00" * 8)
        result = get_font_metrics(str(woff))
        assert "error" in result
        assert "not supported" in result["error"].lower() or "woff" in result["error"].lower()

    def test_extracts_head_em_units(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "headonly.ttf",
            tables=[
                ("head", 0, _make_head_table(2048)),
            ],
        )
        result = get_font_metrics(str(tmp_path / "headonly.ttf"))
        assert "error" not in result
        assert result["em_units"] == 2048
        assert result["format"] == "ttf"

    def test_extracts_hhea_ascent_descent_gap(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "hhea.ttf",
            tables=[
                ("head", 0, _make_head_table(1000)),
                ("hhea", 0, _make_hhea_table(ascent=1200, descent=-300, line_gap=50)),
            ],
        )
        result = get_font_metrics(str(tmp_path / "hhea.ttf"))
        assert "error" not in result
        assert result["ascent"] == 1200
        assert result["descent"] == -300
        assert result["line_gap"] == 50

    def test_default_em_units_when_head_short(self, tmp_path: Path):
        """If head table exists but is too short for emUnits, stays at init value 0."""
        _write_font_with_tables(
            tmp_path / "shorthead.ttf",
            tables=[
                ("head", 0, b"\x00" * 10),  # too short for emUnits at bytes 18-20
                ("hhea", 0, _make_hhea_table(ascent=800, descent=-200, line_gap=0)),
            ],
        )
        result = get_font_metrics(str(tmp_path / "shorthead.ttf"))
        assert "error" not in result
        assert result["em_units"] == 0  # stays at zero-initialized value
        assert result["ascent"] == 800

    def test_default_metrics_when_no_hhea(self, tmp_path: Path):
        """When hhea is absent, ascent/descent/line_gap default to 0."""
        _write_font_with_tables(
            tmp_path / "no_hhea.ttf",
            tables=[
                ("head", 0, _make_head_table(2048)),
            ],
        )
        result = get_font_metrics(str(tmp_path / "no_hhea.ttf"))
        assert "error" not in result
        assert result["ascent"] == 0
        assert result["descent"] == 0
        assert result["line_gap"] == 0

    def test_format_is_ttf_for_ttf_header(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "ttf_font.ttf",
            tables=[
                ("head", 0, _make_head_table()),
            ],
        )
        result = get_font_metrics(str(tmp_path / "ttf_font.ttf"))
        assert result["format"] == "ttf"

    def test_format_is_otf_for_otto_header(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "otf_font.otf",
            sfversion=b"OTTO",
            tables=[("head", 0, _make_head_table())],
        )
        result = get_font_metrics(str(tmp_path / "otf_font.otf"))
        assert "error" not in result
        assert result["format"] == "otf"

    def test_ttc_format_works_with_head_only(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "col.ttc",
            sfversion=b"ttcf",
            tables=[("head", 0, _make_head_table(1024))],
        )
        result = get_font_metrics(str(tmp_path / "col.ttc"))
        assert "error" not in result
        assert result["format"] == "ttc"
        assert result["em_units"] == 1024

    def test_zero_tables_still_returns_metrics(self, tmp_path: Path):
        """Font with no tables: format detected but all values default."""
        font = tmp_path / "empty.ttf"
        font.write_bytes(_make_sfnt_header(b"\x00\x01\x00\x00", 0))
        result = get_font_metrics(str(font))
        assert "error" not in result
        assert result["em_units"] == 1000
        assert result["ascent"] == 0
        assert result["format"] == "ttf"


# ── has_variable_axes ────────────────────────────────────────────────────


class TestHasVariableAxes:
    def test_missing_file_returns_false(self):
        assert has_variable_axes("/nonexistent/font.ttf") is False

    def test_font_without_fvar_returns_false(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "nofvar.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 4),
                ("head", 0, b"\x00" * 12),
            ],
        )
        assert has_variable_axes(str(tmp_path / "nofvar.ttf")) is False

    def test_font_with_fvar_returns_true(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "var.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 4),
                ("head", 0, b"\x00" * 12),
                ("fvar", 0, b"\x00" * 8),
            ],
        )
        assert has_variable_axes(str(tmp_path / "var.ttf")) is True

    def test_non_sfnt_returns_false(self, tmp_path: Path):
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"invalid")
        assert has_variable_axes(str(bogus)) is False


# ── has_kerning ──────────────────────────────────────────────────────────


class TestHasKerning:
    def test_missing_file_returns_false(self):
        assert has_kerning("/nonexistent/font.ttf") is False

    def test_font_without_kerning_tables_returns_false(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "nokern.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 4),
            ],
        )
        assert has_kerning(str(tmp_path / "nokern.ttf")) is False

    def test_font_with_kern_returns_true(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "kern.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 4),
                ("kern", 0, b"\x00" * 8),
            ],
        )
        assert has_kerning(str(tmp_path / "kern.ttf")) is True

    def test_font_with_gpos_returns_true(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "gpos.ttf",
            tables=[
                ("cmap", 0, b"\x00" * 4),
                ("GPOS", 0, b"\x00" * 6),
            ],
        )
        assert has_kerning(str(tmp_path / "gpos.ttf")) is True

    def test_font_with_both_kern_and_gpos_returns_true(self, tmp_path: Path):
        _write_font_with_tables(
            tmp_path / "both.ttf",
            tables=[
                ("kern", 0, b"\x00" * 4),
                ("GPOS", 0, b"\x00" * 4),
            ],
        )
        assert has_kerning(str(tmp_path / "both.ttf")) is True

    def test_non_sfnt_returns_false(self, tmp_path: Path):
        bogus = tmp_path / "bogus.ttf"
        bogus.write_bytes(b"junk data")
        assert has_kerning(str(bogus)) is False


# ── is_web_font_format ───────────────────────────────────────────────────


class TestIsWebFontFormat:
    def test_missing_file_returns_false(self):
        assert is_web_font_format("/nonexistent/font.woff") is False

    def test_empty_path_returns_false(self):
        assert is_web_font_format("") is False

    def test_woff_header_returns_true(self, tmp_path: Path):
        woff = tmp_path / "web.woff"
        woff.write_bytes(b"wOFF" + b"\x00" * 8)
        assert is_web_font_format(str(woff)) is True

    def test_woff2_header_returns_true(self, tmp_path: Path):
        woff2 = tmp_path / "web.woff2"
        woff2.write_bytes(b"wOF2" + b"\x00" * 8)
        assert is_web_font_format(str(woff2)) is True

    def test_ttf_returns_false(self, tmp_path: Path):
        ttf = tmp_path / "notweb.ttf"
        ttf.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 8)
        assert is_web_font_format(str(ttf)) is False

    def test_otf_returns_false(self, tmp_path: Path):
        otf = tmp_path / "notweb.otf"
        otf.write_bytes(b"OTTO" + b"\x00" * 8)
        assert is_web_font_format(str(otf)) is False

    def test_short_file_returns_false(self, tmp_path: Path):
        short = tmp_path / "short.woff"
        short.write_bytes(b"wO")
        assert is_web_font_format(str(short)) is False

    def test_ttc_returns_false(self, tmp_path: Path):
        ttc = tmp_path / "col.ttc"
        ttc.write_bytes(b"ttcf" + b"\x00" * 8)
        assert is_web_font_format(str(ttc)) is False
