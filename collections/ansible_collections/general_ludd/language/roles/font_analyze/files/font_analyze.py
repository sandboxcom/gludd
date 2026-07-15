#!/usr/bin/env python3
"""font_analyze — Analyze OpenType/TrueType/WOFF/WOFF2 font files.

Parses font tables, extracts metrics, checks kerning/ligature features,
detects variable font axes, and validates web font formats.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys


WEB_FONT_FORMATS: frozenset[str] = frozenset({"woff", "woff2"})


def _read_table_data(font_file: str, tables: list[dict[str, object]]) -> dict[str, bytes]:
    table_data: dict[str, bytes] = {}
    with open(font_file, "rb") as f:
        for tbl in tables:
            try:
                offset_val = int(tbl["offset"])
                length_val = int(tbl["length"])
                f.seek(offset_val)
                table_data[str(tbl["tag"])] = f.read(length_val)
            except (OSError, struct.error):
                pass
    return table_data


def _parse_tables(font_file: str) -> tuple[list[dict[str, object]], int]:
    tables: list[dict[str, object]] = []
    with open(font_file, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            return tables, 0
        num_tables = struct.unpack(">H", header[4:6])[0]
        for _ in range(num_tables):
            chunk = f.read(16)
            if len(chunk) < 16:
                break
            tag_bytes = chunk[0:4]
            offset_val = struct.unpack(">I", chunk[8:12])[0]
            length_val = struct.unpack(">I", chunk[12:16])[0]
            tables.append({
                "tag": tag_bytes.decode("ascii", errors="replace").strip(),
                "offset": offset_val,
                "length": length_val,
            })
    return tables, len(tables)


def _detect_format(font_file: str) -> str:
    with open(font_file, "rb") as f:
        header = f.read(4)
    if header.startswith(b"wOFF"):
        return "woff"
    if header.startswith(b"wOF2"):
        return "woff2"
    if header[:4] == b"ttcf":
        return "ttc"
    if header[:4] == b"\x00\x01\x00\x00":
        return "ttf"
    if header[:4] == b"OTTO":
        return "otf"
    return "unknown"


def _extract_metrics(font_file: str, tables: list[dict[str, object]]) -> dict[str, int]:
    metrics: dict[str, int] = {
        "em_units": 2048,
        "ascent": 0,
        "descent": 0,
        "line_gap": 0,
        "cap_height": 0,
        "x_height": 0,
    }

    table_data = _read_table_data(font_file, tables)

    if "head" in table_data and len(table_data["head"]) >= 54:
        h = table_data["head"]
        metrics["em_units"] = struct.unpack(">H", h[18:20])[0]

    if "hhea" in table_data and len(table_data["hhea"]) >= 36:
        h = table_data["hhea"]
        metrics["ascent"] = struct.unpack(">h", h[4:6])[0]
        metrics["descent"] = struct.unpack(">h", h[6:8])[0]
        metrics["line_gap"] = struct.unpack(">h", h[8:10])[0]

    if "OS/2" in table_data and len(table_data["OS/2"]) >= 90:
        os2 = table_data["OS/2"]
        if len(os2) >= 90:
            metrics["cap_height"] = struct.unpack(">h", os2[88:90])[0]
        xh_off = 86
        try:
            metrics["x_height"] = struct.unpack(">h", os2[xh_off:xh_off + 2])[0]
        except struct.error:
            pass

    return metrics


def _check_features(tables: list[dict[str, object]]) -> dict[str, bool]:
    tags = {t["tag"] for t in tables}
    return {
        "has_GSUB": "GSUB" in tags,
        "has_GPOS": "GPOS" in tags,
        "has_kern": "kern" in tags,
    }


def _check_variable_axes(font_file: str, tables: list[dict[str, object]]) -> list[dict[str, object]]:
    var_axes: list[dict[str, object]] = []
    for tbl in tables:
        if tbl["tag"] == "fvar":
            try:
                with open(font_file, "rb") as f:
                    f.seek(int(tbl["offset"]))
                    fvar_data = f.read(int(tbl["length"]))
                if len(fvar_data) >= 16:
                    axis_count = struct.unpack(">H", fvar_data[8:10])[0]
                    off = 16
                    for _ in range(axis_count):
                        if off + 20 <= len(fvar_data):
                            tag_str = fvar_data[off:off + 4].decode("ascii", errors="replace")
                            min_val = struct.unpack(">f", fvar_data[off + 4:off + 8])[0]
                            def_val = struct.unpack(">f", fvar_data[off + 8:off + 12])[0]
                            max_val = struct.unpack(">f", fvar_data[off + 12:off + 16])[0]
                            var_axes.append({
                                "tag": tag_str.strip(),
                                "min": round(min_val, 4),
                                "default": round(def_val, 4),
                                "max": round(max_val, 4),
                            })
                            off += 20
            except (OSError, struct.error):
                pass
            break
    return var_axes


def analyze(args: argparse.Namespace) -> dict[str, object]:
    font_file = args.input

    if not font_file or not os.path.isfile(font_file):
        return {"file": font_file, "error": f"Font file not found: {font_file}"}

    result: dict[str, object] = {"file": font_file}

    result["size_bytes"] = os.path.getsize(font_file)
    result["format"] = _detect_format(font_file)
    fmt = str(result["format"])

    if args.check_tables and fmt in ("ttf", "otf", "ttc"):
        try:
            tables, count = _parse_tables(font_file)
            result["tables"] = tables
            result["table_count"] = count
        except (OSError, struct.error) as exc:
            result["table_error"] = str(exc)

    if args.check_metrics:
        try:
            tables = result.get("tables", [])
            if not isinstance(tables, list):
                tables = []
            result["metrics"] = _extract_metrics(font_file, tables)
        except (OSError, struct.error) as exc:
            result["metrics_error"] = str(exc)

    if args.check_features and result.get("tables"):
        result["features"] = _check_features(result["tables"])  # type: ignore[arg-type]

    if args.check_variable and result.get("tables"):
        result["variable_axes"] = _check_variable_axes(
            font_file, result["tables"]  # type: ignore[arg-type]
        )

    if args.check_monospace and result.get("tables"):
        raw_tables = result["tables"]  # type: ignore[index]
        if isinstance(raw_tables, list):
            tags = {t["tag"] for t in raw_tables}
            result["is_monospace"] = "post" not in tags if tags else None

    if args.check_web_font:
        result["web_font_valid"] = fmt in WEB_FONT_FORMATS
        if fmt == "ttf":
            result["web_font_note"] = "TrueType can be used as web font but not compressed"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze font files (OpenType/TrueType/WOFF/WOFF2)"
    )
    parser.add_argument("--input", default="", help="Font file path to analyze")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--check-tables", action="store_true", default=True)
    parser.add_argument("--check-metrics", action="store_true", default=True)
    parser.add_argument("--check-features", action="store_true", default=True)
    parser.add_argument("--check-variable", action="store_true", default=True)
    parser.add_argument("--check-monospace", action="store_true", default=True)
    parser.add_argument("--check-web-font", action="store_true", default=True)
    parser.add_argument("--no-tables", dest="check_tables", action="store_false")
    parser.add_argument("--no-metrics", dest="check_metrics", action="store_false")
    parser.add_argument("--no-features", dest="check_features", action="store_false")
    parser.add_argument("--no-variable", dest="check_variable", action="store_false")
    parser.add_argument("--no-monospace", dest="check_monospace", action="store_false")
    parser.add_argument("--no-web-font", dest="check_web_font", action="store_false")

    args = parser.parse_args()

    try:
        result = analyze(args)
    except Exception as exc:
        result = {"file": args.input, "error": str(exc)}

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output: {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
