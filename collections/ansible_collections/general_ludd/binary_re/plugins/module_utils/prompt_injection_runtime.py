#!/usr/bin/env python3
"""CLI wrapper for prompt_injection_detector — used by prompt_injection_scan role.

Scans files or text for prompt-injection payloads across encoding formats,
returns structured findings with severity scoring.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ansible_collections.general_ludd.binary_re.plugins.module_utils.obfuscation_techniques import (
    detect_techniques as detect_obfuscation,
)
from ansible_collections.general_ludd.binary_re.plugins.module_utils.prompt_injection_detector import (
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


def _run_obfuscation_scan(file_path: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
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


def run_scan(
    *,
    file_path: str = "",
    input_text: str = "",
    min_severity: str = "medium",
    scan_obfuscation: bool = False,
) -> tuple[ScanReport, list[dict[str, Any]]]:
    """Scan one source and return the filtered report plus obfuscation results."""
    if not file_path and not input_text:
        raise ValueError("file_path or input_text is required")
    if min_severity not in SEVERITY_ORDER:
        raise ValueError(f"Unsupported minimum severity: {min_severity}")

    report: ScanReport
    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(file_path)
        report = ScanReport() if path.stat().st_size == 0 else scan_file(str(path))
    else:
        report = scan_text(
            input_text,
            check_encodings=True,
            check_js=True,
            check_python=True,
        )

    threshold = SEVERITY_ORDER[min_severity]
    report.findings = [
        finding
        for finding in report.findings
        if SEVERITY_ORDER.get(finding.severity.value, 0) >= threshold
    ]
    obfuscation = (
        _run_obfuscation_scan(file_path)
        if scan_obfuscation and file_path
        else []
    )
    return report, obfuscation


def scan_payload(
    report: ScanReport,
    obfuscation: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the stable JSON-compatible role result."""
    return {
        "scan": report.to_dict(),
        "obfuscation_techniques": obfuscation,
    }


def render_scan(
    report: ScanReport,
    obfuscation: list[dict[str, Any]],
    output_format: str,
) -> str:
    """Render a scan without writing the destination artifact."""
    if output_format == "json":
        return json.dumps(scan_payload(report, obfuscation), indent=2, default=str)
    if output_format != "text":
        raise ValueError(f"Unsupported output format: {output_format}")

    text_lines = [_format_text(report)]
    if obfuscation:
        text_lines.append("\nObfuscation techniques detected:")
        for result in obfuscation:
            text_lines.append(
                f"  [{result['confidence']}] {result['technique']}: "
                f"{'; '.join(result['evidence'])}"
            )
    return "\n".join(text_lines)


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

    try:
        report, obfuscation_results = run_scan(
            file_path=args.file,
            input_text=args.text,
            min_severity=args.min_severity,
            scan_obfuscation=args.scan_obfuscation,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    output = render_scan(report, obfuscation_results, args.format)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
