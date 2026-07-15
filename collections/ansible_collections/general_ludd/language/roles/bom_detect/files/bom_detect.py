#!/usr/bin/env python3
"""bom_detect — Detect, strip, and add Byte Order Marks.

Produces JSON artifact with: bom_found, bom_type, encoding, content preview,
RFC compliance, audit results when scanning directories.
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


def _hex_preview(data: bytes, max_bytes: int = 32) -> str:
    preview = " ".join(f"{b:02X}" for b in data[:max_bytes])
    if len(data) > max_bytes:
        preview += "..."
    return preview


def detect(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.charset_map import (  # type: ignore[import-not-at-top-of-file]
        BOM_OPTIONAL_BY_RFC,
        BOM_REQUIRED_BY_RFC,
        BOM_SIGNATURES,
        BOM_SIZE,
    )

    def _rfc_compliance(encoding: str) -> str:
        rfc_base = encoding.replace("-BE", "BE").replace("-LE", "LE")
        if rfc_base in BOM_REQUIRED_BY_RFC:
            return "required"
        if rfc_base in BOM_OPTIONAL_BY_RFC:
            return "optional"
        return "none"

    result: dict[str, object] = {}
    data: bytes | None = None

    if args.input_file:
        with open(args.input_file, "rb") as f:
            data = f.read()
    elif args.input_bytes:
        data = bytes.fromhex(args.input_bytes.replace(" ", ""))

    if data is None:
        return {"bom_detected": False, "error": "No input provided"}

    result["file_size"] = len(data)

    detected = None
    for name, sig in BOM_SIGNATURES.items():
        if data[:len(sig)] == sig:
            detected = name
            break

    result["bom_detected"] = detected is not None

    if detected:
        size = BOM_SIZE.get(detected, len(BOM_SIGNATURES[detected]))
        result["encoding"] = detected
        result["bom_size"] = size
        result["bom_hex"] = _hex_preview(data[:size], max_bytes=size)
        result["rfc_compliance"] = _rfc_compliance(detected)

        if args.strip:
            stripped = data[size:]
            result["stripped_preview"] = _hex_preview(stripped)
    else:
        result["encoding"] = None
        result["bom_size"] = 0

    if args.add_bom:
        add_enc = args.add_bom_encoding
        if add_enc in BOM_SIGNATURES:
            bom_bytes = BOM_SIGNATURES[add_enc]
            if data[:len(bom_bytes)] == bom_bytes:
                modified = data
            else:
                modified = bom_bytes + data
            result["bom_added"] = add_enc
            result["with_bom_hex"] = _hex_preview(modified, max_bytes=64)

    if args.audit_directory:
        audit_path = args.audit_directory
        if os.path.isdir(audit_path):
            audit_results = []
            for root, _dirs, files in os.walk(audit_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "rb") as f:
                            head = f.read(4)
                        found = None
                        for name, sig in BOM_SIGNATURES.items():
                            if head[:len(sig)] == sig:
                                found = name
                                break
                        audit_results.append({"file": fpath, "bom": found})
                    except OSError:
                        pass
            result["audit_results"] = audit_results
        else:
            result["audit_error"] = f"Directory not found: {audit_path}"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect and handle Byte Order Marks")
    parser.add_argument("--input-file", default="", help="File to read raw bytes from")
    parser.add_argument("--input-bytes", default="", help="Hex-encoded byte string")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--strip", action="store_true", default=False, help="Strip BOM from data")
    parser.add_argument("--add-bom", action="store_true", default=False, help="Add BOM to data")
    parser.add_argument("--add-bom-encoding", default="UTF-8", help="Encoding for add-bom")
    parser.add_argument("--audit-directory", default="", help="Directory to audit for BOMs")

    args = parser.parse_args()

    try:
        result = detect(args)
    except Exception as exc:
        result = {"bom_detected": False, "error": str(exc)}

    output = json.dumps(result, indent=2)
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
