#!/usr/bin/env python3
"""homoglyph_scan — Detect confusable/homoglyph characters in text.

Checks for: UTS #39 confusable characters, invisible characters,
bidi spoofing (Trojan Source / CVE-2021-42574), mixed-script detection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata


SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _add_src_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "..", "..", "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _script_for(ch: str) -> str:
    try:
        return unicodedata.script_name(ch)
    except (ValueError, AttributeError):
        return "Unknown"


def scan(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.homoglyph_data import (  # type: ignore[import-not-at-top-of-file]
        ATTACK_VECTORS,
        HOMOGLYPH_GROUPS,
        INVISIBLE_CHARACTERS,
    )

    text = args.input or ""
    if not text and args.input_domain:
        text = args.input_domain

    if not text:
        return {"input_length": 0, "findings": [], "attack_vectors": [], "safe": True}

    result: dict[str, object] = {"input_length": len(text)}
    min_sev = SEVERITY_RANK.get(args.min_severity, 0)

    cp_to_skeleton: dict[int, str] = {}
    for group in HOMOGLYPH_GROUPS:
        for cp, _name in group["characters"]:
            cp_to_skeleton[cp] = group["skeleton"]

    invisible_map: dict[int, dict[str, object]] = {
        inv["codepoint"]: inv for inv in INVISIBLE_CHARACTERS
    }
    bidi_codepoints: set[int] = {
        inv["codepoint"]
        for inv in INVISIBLE_CHARACTERS
        if inv["category"] == "bidi-control"
    }

    findings: list[dict[str, object]] = []
    script_counts: dict[str, int] = {}

    for ch in text:
        cp = ord(ch)
        script = _script_for(ch)
        script_counts[script] = script_counts.get(script, 0) + 1

        if args.check_confusables and cp in cp_to_skeleton:
            skel = cp_to_skeleton[cp]
            if skel and skel != ch:
                findings.append({
                    "type": "confusable",
                    "severity": "medium",
                    "character": ch,
                    "codepoint": f"U+{cp:04X}",
                    "name": unicodedata.name(ch, ""),
                    "skeleton": skel,
                    "description": (
                        f'Confusable character: looks like "{skel}" '
                        f"but is {unicodedata.name(ch, 'unknown')}"
                    ),
                })

        if args.check_invisible and cp in invisible_map:
            inv = invisible_map[cp]
            sev = "high" if inv.get("category") == "bidi-control" else "medium"
            findings.append({
                "type": "invisible",
                "severity": sev,
                "character": ch,
                "codepoint": f"U+{cp:04X}",
                "name": inv.get("name", ""),
                "short_name": inv.get("short_name", ""),
                "risk": inv.get("risk", ""),
                "cve": inv.get("cve_reference", ""),
            })

        if args.check_bidi and cp in bidi_codepoints:
            is_override = cp in (0x202A, 0x202B, 0x202D, 0x202E)
            findings.append({
                "type": "bidi_spoof",
                "severity": "critical" if is_override else "high",
                "character": ch,
                "codepoint": f"U+{cp:04X}",
                "name": unicodedata.name(ch, ""),
                "cve": "CVE-2021-42574" if 0x202A <= cp <= 0x202E else "",
                "description": "Bidirectional text control character — "
                               "potential Trojan Source attack",
            })

    result["findings"] = [
        f for f in findings
        if SEVERITY_RANK.get(str(f["severity"]), 0) >= min_sev
    ]

    if args.check_mixed_script:
        scripts_present = [
            s for s, count in script_counts.items()
            if count > 0 and s not in ("Common", "Inherited", "Unknown")
        ]
        if len(scripts_present) > 1:
            findings.append({
                "type": "mixed_script",
                "severity": "medium",
                "scripts": scripts_present,
                "description": f"Mixed scripts detected: {', '.join(scripts_present)}",
            })
        result["scripts_detected"] = scripts_present
        result["script_counts"] = {
            k: v for k, v in sorted(script_counts.items(), key=lambda x: -x[1])
        }

    seen_vectors: set[str] = set()
    attack_vectors: list[dict[str, str]] = []
    for f in result["findings"]:
        if f["type"] == "confusable" and "domain_spoofing" not in seen_vectors:
            attack_vectors.append({
                "vector": "domain_spoofing",
                "description": ATTACK_VECTORS.get("domain_spoofing", ""),
                "severity": "high",
            })
            seen_vectors.add("domain_spoofing")
        elif f["type"] == "bidi_spoof":
            attack_vectors.append({
                "vector": "code_injection",
                "description": ATTACK_VECTORS.get("code_injection", ""),
                "severity": "critical",
            })

    if args.check_invisible and any(
        f["type"] == "invisible" for f in result["findings"]
    ):
        for vec_name in ("filename_confusion", "string_comparison_bypass"):
            if vec_name not in seen_vectors:
                attack_vectors.append({
                    "vector": vec_name,
                    "description": ATTACK_VECTORS.get(vec_name, ""),
                    "severity": "high",
                })
                seen_vectors.add(vec_name)

    if args.check_bidi and any(
        f["type"] == "bidi_spoof" for f in result["findings"]
    ):
        if "comment_out_out-of-context" not in seen_vectors:
            attack_vectors.append({
                "vector": "comment_out_out-of-context",
                "description": ATTACK_VECTORS.get("comment_out_out-of-context", ""),
                "severity": "critical",
            })
            seen_vectors.add("comment_out_out-of-context")

    result["attack_vectors"] = attack_vectors

    total = len(result["findings"])
    result["total_findings"] = total
    sev_counts: dict[str, int] = {}
    for f in result["findings"]:
        sev = str(f["severity"])
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    result["severity_counts"] = sev_counts
    result["safe"] = total == 0

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan text for confusable/homoglyph characters"
    )
    parser.add_argument("--input", default="", help="Text to scan")
    parser.add_argument("--input-domain", default="", help="Domain name to scan")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--min-severity", default="low",
                        choices=["low", "medium", "high", "critical"])
    parser.add_argument("--check-confusables", action="store_true", default=True)
    parser.add_argument("--check-invisible", action="store_true", default=True)
    parser.add_argument("--check-bidi", action="store_true", default=True)
    parser.add_argument("--check-mixed-script", action="store_true", default=True)
    parser.add_argument("--no-confusables", dest="check_confusables", action="store_false")
    parser.add_argument("--no-invisible", dest="check_invisible", action="store_false")
    parser.add_argument("--no-bidi", dest="check_bidi", action="store_false")
    parser.add_argument("--no-mixed-script", dest="check_mixed_script", action="store_false")

    args = parser.parse_args()

    try:
        result = scan(args)
    except Exception as exc:
        result = {"input_length": 0, "error": str(exc), "findings": [], "safe": True}

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
