#!/usr/bin/env python3
"""unicode_analyze — Analyze Unicode properties of text.

Produces JSON artifact with: codepoints, categories, normalization forms,
grapheme clusters, plane distribution, surrogate analysis, UTF encodings,
and Unicode version history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata


def _add_src_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "..", "..", "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def analyze(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.unicode_data import (  # type: ignore[import-not-at-top-of-file]
        UNICODE_BLOCK_NAMES,
        UNICODE_CATEGORY_NAMES,
        UNICODE_VERSION_HISTORY,
        is_high_surrogate,
        is_low_surrogate,
        plane_of,
        surrogates_to_codepoint,
    )

    try:
        import regex as regex_mod  # type: ignore[import-not-at-top-of-file]
    except ImportError:
        regex_mod = None

    def _block_for(cp: int) -> str:
        for (lo, hi), name in UNICODE_BLOCK_NAMES.items():
            if lo <= cp <= hi:
                return name
        return "Unknown"

    def _script_for(ch: str) -> str:
        try:
            return unicodedata.script_name(ch)
        except (ValueError, AttributeError):
            return "Unknown"

    def _plane_distribution(text: str) -> dict[str, int]:
        dist: dict[str, int] = {}
        for ch in text:
            pl = plane_of(ord(ch))
            dist[pl] = dist.get(pl, 0) + 1
        return dist

    text = args.input or ""
    if not text and args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            text = f.read()

    if not text:
        return {"error": "No input provided", "input_length": 0}

    result: dict[str, object] = {
        "input_length": len(text),
        "input_byte_length": len(text.encode("utf-8")),
    }

    if args.include_codepoints:
        codepoints = []
        for i, ch in enumerate(text):
            cp = ord(ch)
            cat = unicodedata.category(ch)
            codepoints.append({
                "index": i,
                "char": ch,
                "codepoint": f"U+{cp:04X}",
                "category": cat,
                "category_name": UNICODE_CATEGORY_NAMES.get(cat, ""),
                "block": _block_for(cp),
                "plane": plane_of(cp),
                "script": _script_for(ch),
                "name": unicodedata.name(ch, ""),
            })
        result["codepoints"] = codepoints

    if args.include_normalization:
        result["normalization"] = {
            "NFC": unicodedata.normalize("NFC", text),
            "NFD": unicodedata.normalize("NFD", text),
            "NFKC": unicodedata.normalize("NFKC", text),
            "NFKD": unicodedata.normalize("NFKD", text),
        }

    if args.include_grapheme_clusters:
        clusters = []
        if regex_mod:
            matches = regex_mod.findall(r"\X", text)
            clusters = [{"index": i, "text": c} for i, c in enumerate(matches)]
        else:
            clusters = [{"index": i, "text": ch} for i, ch in enumerate(text)]
        result["grapheme_clusters"] = clusters

    if args.include_surrogates:
        surrogates = []
        text_cps = [ord(ch) for ch in text]
        i = 0
        while i < len(text_cps):
            if (
                is_high_surrogate(text_cps[i])
                and i + 1 < len(text_cps)
                and is_low_surrogate(text_cps[i + 1])
            ):
                decoded = surrogates_to_codepoint(text_cps[i], text_cps[i + 1])
                surrogates.append({
                    "index": i,
                    "high": f"U+{text_cps[i]:04X}",
                    "low": f"U+{text_cps[i + 1]:04X}",
                    "decoded": f"U+{decoded:04X}",
                })
                i += 2
            else:
                i += 1
        result["surrogates"] = surrogates

    if args.include_plane_distribution:
        result["plane_distribution"] = _plane_distribution(text)

    if args.include_utf_encodings:
        result["utf_encodings"] = {
            "UTF-8": text.encode("utf-8").hex(" "),
            "UTF-16-LE": text.encode("utf-16-le").hex(" "),
            "UTF-16-BE": text.encode("utf-16-be").hex(" "),
            "UTF-32-LE": text.encode("utf-32-le").hex(" "),
            "UTF-32-BE": text.encode("utf-32-be").hex(" "),
        }

    if args.include_version_info:
        result["unicode_versions"] = UNICODE_VERSION_HISTORY

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Unicode properties of text")
    parser.add_argument("--input", default="", help="Text to analyze")
    parser.add_argument("--input-file", default="", help="File to read text from")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--include-codepoints", action="store_true", default=True)
    parser.add_argument("--include-normalization", action="store_true", default=True)
    parser.add_argument("--include-grapheme-clusters", action="store_true", default=True)
    parser.add_argument("--include-surrogates", action="store_true", default=True)
    parser.add_argument("--include-plane-distribution", action="store_true", default=True)
    parser.add_argument("--include-utf-encodings", action="store_true", default=True)
    parser.add_argument("--include-version-info", action="store_true", default=True)
    parser.add_argument("--no-codepoints", dest="include_codepoints", action="store_false")
    parser.add_argument("--no-normalization", dest="include_normalization", action="store_false")
    parser.add_argument("--no-grapheme", dest="include_grapheme_clusters", action="store_false")
    parser.add_argument("--no-surrogates", dest="include_surrogates", action="store_false")
    parser.add_argument("--no-plane-distribution", dest="include_plane_distribution", action="store_false")
    parser.add_argument("--no-utf-encodings", dest="include_utf_encodings", action="store_false")
    parser.add_argument("--no-version-info", dest="include_version_info", action="store_false")

    args = parser.parse_args()

    try:
        result = analyze(args)
    except Exception as exc:
        result = {"error": str(exc)}

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
