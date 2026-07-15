"""Font knowledge module: formats, metrics, system font stacks.

Covers spec section 4.6 (Fonts):
- Font format identification via magic bytes (TTF, OTF, WOFF, WOFF2, TTC)
- OpenType/TrueType table enumeration (head, hhea, OS/2, GSUB, GPOS, kern, fvar)
- Font metric extraction (em-square size, ascent, descent, line gap)
- System font stacks per OS (macOS, Windows, Linux, iOS, Android)
- Web font validation (MIME types, compression, subset requirements)
- Variable font axes (weight, width, italic, optical size)
- Font subsetting strategies and unicode-range coverage

Pure-Python knowledge module; no external dependencies (no fonttools/freetype).
"""

from __future__ import annotations

import os
import struct
from typing import TypedDict


class FontTableRecord(TypedDict):
    tag: str
    offset: int
    length: int


class FontMetrics(TypedDict):
    em_units: int
    ascent: int
    descent: int
    line_gap: int


class FontMetricsResult(TypedDict):
    em_units: int
    ascent: int
    descent: int
    line_gap: int
    format: str


class ErrorResult(TypedDict):
    error: str


FONT_FORMAT_SPECS: dict[str, dict[str, str | bytes]] = {
    "ttf": {
        "magic": b"\x00\x01\x00\x00",
        "mime": "font/ttf",
        "description": "TrueType Font; original Apple/Microsoft raster format",
    },
    "otf": {
        "magic": b"OTTO",
        "mime": "font/otf",
        "description": "OpenType Font with PostScript CFF outlines",
    },
    "woff": {
        "magic": b"wOFF",
        "mime": "font/woff",
        "description": "Web Open Font Format 1.0; zlib-compressed OpenType",
    },
    "woff2": {
        "magic": b"wOF2",
        "mime": "font/woff2",
        "description": "Web Open Font Format 2.0; brotli-compressed OpenType",
    },
    "ttc": {
        "magic": b"ttcf",
        "mime": "font/collection",
        "description": "TrueType Collection; bundles multiple TTF/OTF in one file",
    },
}


SYSTEM_FONT_STACKS: dict[str, dict[str, str]] = {
    "macos": {
        "sans-serif": (
            "-apple-system, BlinkMacSystemFont, 'Helvetica Neue', "
            "'Helvetica', 'Arial', sans-serif"
        ),
        "serif": (
            "'New York', 'Times New Roman', 'Times', serif"
        ),
        "monospace": (
            "Menlo, Monaco, 'Courier New', monospace"
        ),
    },
    "windows": {
        "sans-serif": (
            "'Segoe UI', Tahoma, Arial, sans-serif"
        ),
        "serif": (
            "'Times New Roman', Times, serif"
        ),
        "monospace": (
            "Consolas, 'Courier New', monospace"
        ),
    },
    "linux": {
        "sans-serif": (
            "system-ui, 'DejaVu Sans', 'Liberation Sans', Arial, sans-serif"
        ),
        "serif": (
            "'DejaVu Serif', 'Liberation Serif', 'Times New Roman', serif"
        ),
        "monospace": (
            "'DejaVu Sans Mono', 'Liberation Mono', 'Courier New', monospace"
        ),
    },
    "ios": {
        "sans-serif": (
            "-apple-system, 'Helvetica Neue', 'Helvetica', 'Arial', sans-serif"
        ),
        "serif": (
            "'New York', 'Times New Roman', serif"
        ),
        "monospace": (
            "'SF Mono', Menlo, Monaco, 'Courier New', monospace"
        ),
    },
    "android": {
        "sans-serif": (
            "'Roboto', 'Helvetica Neue', 'Helvetica', 'Arial', sans-serif"
        ),
        "serif": (
            "'Noto Serif', 'Times New Roman', serif"
        ),
        "monospace": (
            "'Roboto Mono', 'Courier New', monospace"
        ),
    },
}


OPENTYPE_REQUIRED_TABLES: list[str] = [
    "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post",
]


OPENTYPE_OPTIONAL_TABLES: list[str] = [
    "glyf", "loca", "CFF ", "CFF2", "GSUB", "GPOS", "GDEF",
    "kern", "fvar", "gvar", "avar", "hvar", "vhea", "vmtx",
    "DSIG", "meta", "STAT", "MERG", "morx", "mort",
]


VARIABLE_FONT_AXES: dict[str, dict[str, str | float]] = {
    "wght": {"name": "Weight", "min": 1.0, "max": 1000.0, "default": 400.0},
    "wdth": {"name": "Width", "min": 50.0, "max": 200.0, "default": 100.0},
    "ital": {"name": "Italic", "min": 0.0, "max": 1.0, "default": 0.0},
    "slnt": {"name": "Slant", "min": -90.0, "max": 90.0, "default": 0.0},
    "opsz": {
        "name": "Optical Size", "min": 8.0, "max": 144.0, "default": 12.0,
    },
}


def identify_font_format(header: bytes) -> str:
    """Identify a font format from its magic bytes.

    Accepts either a full file's bytes or just the leading header (>=4 bytes).
    Returns one of: 'ttf', 'otf', 'woff', 'woff2', 'ttc', 'unknown'.
    """
    if len(header) < 4:
        return "unknown"

    magic_4 = header[:4]

    if magic_4 == b"\x00\x01\x00\x00":
        return "ttf"
    if magic_4 == b"OTTO":
        return "otf"
    if magic_4 == b"wOFF":
        return "woff"
    if magic_4 == b"wOF2":
        return "woff2"
    if magic_4 == b"ttcf":
        return "ttc"

    if magic_4 in (b"true", b"typ1"):
        return "ttf"

    return "unknown"


def list_font_tables(font_path: str) -> list[FontTableRecord]:
    """Parse the OpenType/TrueType table directory.

    Returns a list of {'tag', 'offset', 'length'} for each table record.
    Returns an empty list if the file is missing, unreadable, or not a
    valid SFNT-wrapped font.
    """
    if not font_path or not os.path.isfile(font_path):
        return []

    try:
        with open(font_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return []

            sfversion_bytes = header[:4]
            if sfversion_bytes not in (
                b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1",
            ):
                return []

            num_tables = struct.unpack(">H", header[4:6])[0]

            tables: list[FontTableRecord] = []
            for _i in range(num_tables):
                record = f.read(16)
                if len(record) < 16:
                    break
                tag_bytes, _checksum, offset, length = struct.unpack(
                    ">4sIII", record
                )
                tag = tag_bytes.decode("ascii", errors="replace").rstrip(
                    "\x00"
                )
                tables.append(
                    {"tag": tag, "offset": offset, "length": length}
                )
            return tables
    except (OSError, struct.error):
        return []


def get_font_metrics(
    font_path: str,
) -> FontMetricsResult | ErrorResult:
    """Extract font metrics from the 'head' and 'hhea' tables.

    Returns a dict with keys: em_units, ascent, descent, line_gap, format.
    On error (missing file, not a font, parse failure), returns
    {'error': str} so callers can branch without catching.
    """
    if not font_path or not os.path.isfile(font_path):
        return {"error": f"Font file not found: {font_path}"}

    try:
        with open(font_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return {"error": "File too short to be a valid font"}

            fmt = identify_font_format(header)
            if fmt == "unknown":
                return {"error": f"Unrecognized font format (header={header[:4]!r})"}

            if fmt not in ("ttf", "otf", "ttc"):
                return {"error": f"Metrics extraction not supported for {fmt}"}

            num_tables = struct.unpack(">H", header[4:6])[0]

            table_offsets: dict[str, tuple[int, int]] = {}
            for _i in range(num_tables):
                record = f.read(16)
                if len(record) < 16:
                    break
                tag_bytes, _checksum, offset, length = struct.unpack(
                    ">4sIII", record
                )
                tag = tag_bytes.decode("ascii", errors="replace").rstrip(
                    "\x00"
                )
                table_offsets[tag] = (offset, length)

            em_units = 1000
            ascent = 0
            descent = 0
            line_gap = 0

            if "head" in table_offsets:
                offset, length = table_offsets["head"]
                f.seek(offset)
                head_data = f.read(max(length, 20))
                if len(head_data) >= 20:
                    em_units = struct.unpack(">H", head_data[18:20])[0]

            if "hhea" in table_offsets:
                offset, length = table_offsets["hhea"]
                f.seek(offset)
                hhea_data = f.read(max(length, 10))
                if len(hhea_data) >= 10:
                    ascent = struct.unpack(">h", hhea_data[4:6])[0]
                    descent = struct.unpack(">h", hhea_data[6:8])[0]
                    line_gap = struct.unpack(">h", hhea_data[8:10])[0]

            result: FontMetricsResult = {
                "em_units": em_units,
                "ascent": ascent,
                "descent": descent,
                "line_gap": line_gap,
                "format": fmt,
            }
            return result
    except (OSError, struct.error) as exc:
        return {"error": f"Failed to read font metrics: {exc}"}


def is_web_font_format(font_path: str) -> bool:
    """Return True if the font is in a web-safe format (woff/woff2)."""
    if not font_path or not os.path.isfile(font_path):
        return False
    try:
        with open(font_path, "rb") as f:
            header = f.read(4)
    except OSError:
        return False
    fmt = identify_font_format(header)
    return fmt in ("woff", "woff2")


def has_variable_axes(font_path: str) -> bool:
    """Return True if the font has an 'fvar' table (variable font)."""
    tables = list_font_tables(font_path)
    return any(t["tag"] == "fvar" for t in tables)


def has_kerning(font_path: str) -> bool:
    """Return True if the font has 'kern' or 'GPOS' kerning tables."""
    tables = list_font_tables(font_path)
    tags = {t["tag"] for t in tables}
    return "kern" in tags or "GPOS" in tags
