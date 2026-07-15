#!/usr/bin/env python3
"""encoding_detect — Detect character encoding and convert between encodings.

Produces JSON artifact with: detected_encoding, confidence, confidence_level,
converted preview, mojibake detection, and supported encodings list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _add_src_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "..", "..", "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def detect(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.charset_map import (  # type: ignore[import-not-at-top-of-file]
        ALL_ENCODINGS,
        CHARDET_CONFIDENCE_THRESHOLDS,
        MOJIBAKE_SIGNATURES,
    )

    def _confidence_level(conf: float) -> str:
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("trusted", 0.95):
            return "trusted"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("reliable", 0.80):
            return "reliable"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("usable", 0.50):
            return "usable"
        return "entry"

    result: dict[str, object] = {}
    data: bytes | None = None

    if args.input_file:
        with open(args.input_file, "rb") as f:
            data = f.read()
    elif args.input_bytes:
        data = bytes.fromhex(args.input_bytes.replace(" ", ""))

    if data is None:
        return {"error": "No input provided"}

    result["byte_length"] = len(data)

    try:
        import chardet  # type: ignore[import-not-at-top-of-file]

        detected = chardet.detect(data)
        result["detected_encoding"] = detected.get("encoding", "unknown")
        result["confidence"] = round(detected.get("confidence", 0.0), 4)
    except ImportError:
        result["detected_encoding"] = "utf-8"
        result["confidence"] = 0.5
        result["fallback"] = "chardet not available, defaulting to utf-8"

    conf = float(result.get("confidence", 0))
    result["confidence_level"] = _confidence_level(conf)

    detected_enc = str(result.get("detected_encoding", "utf-8") or "utf-8")
    preview = ""

    try:
        decoded = data.decode(detected_enc)
        preview = decoded[:200]
        result["char_length"] = len(decoded)
    except (UnicodeDecodeError, LookupError):
        decoded = data.decode("utf-8", errors="replace")
        preview = decoded[:200]
        result["decode_error"] = True
        result["char_length"] = len(decoded)

    target_enc = args.target_encoding
    if target_enc:
        try:
            encoded_target = decoded.encode(target_enc)
            result["target_byte_length"] = len(encoded_target)
        except (UnicodeEncodeError, LookupError):
            result["target_encoding_error"] = True

    result["converted_preview"] = preview

    if args.detect_mojibake:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        found_pat = None
        for sig_name, patterns in MOJIBAKE_SIGNATURES.items():
            for pat in patterns:
                if pat and pat in text:
                    found_pat = sig_name
                    break
            if found_pat:
                break
        result["mojibake_detected"] = found_pat is not None
        result["mojibake_pattern"] = found_pat

    result["supported_encodings"] = [e["name"] for e in ALL_ENCODINGS]
    result["supported_count"] = len(ALL_ENCODINGS)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect and convert character encodings")
    parser.add_argument("--input-file", default="", help="File to read raw bytes from")
    parser.add_argument("--input-bytes", default="", help="Hex-encoded byte string")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--min-confidence", type=float, default=0.20)
    parser.add_argument("--target-encoding", default="", help="Target encoding for conversion")
    parser.add_argument("--detect-mojibake", action="store_true", default=False)

    args = parser.parse_args()

    try:
        result = detect(args)
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
