#!/usr/bin/env python3
"""CLI wrapper for prompt_injection_detector — used by prompt_injection_scan role.

Scans files or text for prompt-injection payloads across encoding formats,
returns structured findings with severity scoring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_collection_root() -> str:
    script_dir = Path(__file__).resolve().parent
    collection_root = script_dir.parent.parent.parent
    if (collection_root / "plugins" / "module_utils").is_dir():
        return str(collection_root)
    alt = script_dir.parent.parent.parent.parent
    if (alt / "plugins" / "module_utils" / "prompt_injection_detector.py").is_file():
        return str(alt)
    env_root = os.environ.get("GLUDD_BINARY_RE_ROOT", "")
    if env_root and Path(env_root).is_dir():
        return env_root
    return str(collection_root)


def _patch_sys_path() -> None:
    root = _resolve_collection_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    mod_utils = os.path.join(root, "plugins", "module_utils")
    if mod_utils not in sys.path:
        sys.path.insert(0, mod_utils)


_patch_sys_path()

from plugins.module_utils.obfuscation_techniques import (  # noqa: E402
    detect_techniques as detect_obfuscation,
)
from plugins.module_utils.prompt_injection_detector import (  # noqa: E402
    ScanReport,
    scan_file,
    scan_text,
)

SEVERITY_ORDER = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _format_text(report: ScanReport) -> str:
    lines: list[str] = []
    if not report.findings:
        lines.append("No findings.")
    else:
        for f in report.findings:
            lines.append(
                f"[{f.severity.value.upper()}] [{f.category.value}] "
                f"pos={f.position} match={f.match!r}"
            )
    lines.append("")
    lines.append(
        f"Overall severity: {report.overall_severity.value} | "
        f"Findings: {len(report.findings)} | "
        f"Duration: {report.scan_duration_ms:.1f}ms"
    )
    return "\n".join(lines)


def _run_obfuscation_scan(file_path: str) -> list[dict]:
    results: list[dict] = []
    try:
        detections = detect_obfuscation(file_path)
        for technique, confidence, evidence in detections:
            results.append({
                "technique": technique.value,
                "confidence": confidence.value,
                "evidence": list(evidence),
            })
    except Exception:
        pass
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan for prompt-injection payloads in text or files"
    )
    parser.add_argument(
        "--file", type=str, default="",
        help="File path to scan"
    )
    parser.add_argument(
        "--text", type=str, default="",
        help="Text string to scan"
    )
    parser.add_argument(
        "--output", type=str, default="-",
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--format", choices=["json", "text"], default="json",
        help="Output format"
    )
    parser.add_argument(
        "--min-severity", type=str, default="medium",
        choices=["info", "low", "medium", "high", "critical"],
        help="Minimum severity to report"
    )
    parser.add_argument(
        "--scan-obfuscation", action="store_true", default=False,
        help="Also scan for obfuscation techniques"
    )
    args = parser.parse_args()

    if not args.file and not args.text:
        print("ERROR: --file or --text is required", file=sys.stderr)
        sys.exit(1)

    report: ScanReport
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"ERROR: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        if p.stat().st_size == 0:
            report = ScanReport()
        else:
            report = scan_file(str(p))
    else:
        report = scan_text(
            args.text,
            check_encodings=True,
            check_js=True,
            check_python=True,
        )

    threshold = SEVERITY_ORDER.get(args.min_severity, 2)
    report.findings = [
        f for f in report.findings
        if SEVERITY_ORDER.get(f.severity.value, 0) >= threshold
    ]

    obfuscation_results: list[dict] = []
    if args.scan_obfuscation and args.file:
        obfuscation_results = _run_obfuscation_scan(args.file)

    if args.format == "json":
        output_data: dict = {
            "scan": report.to_dict(),
            "obfuscation_techniques": obfuscation_results,
        }
        output = json.dumps(output_data, indent=2, default=str)
    else:
        text_lines = [_format_text(report)]
        if obfuscation_results:
            text_lines.append("\nObfuscation techniques detected:")
            for r in obfuscation_results:
                text_lines.append(
                    f"  [{r['confidence']}] {r['technique']}: "
                    f"{'; '.join(r['evidence'])}"
                )
        output = "\n".join(text_lines)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
