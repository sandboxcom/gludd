#!/usr/bin/env python3
"""Create a compact, non-source-bearing summary from Bandit JSON output."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

GROUPS = ("by_severity", "by_rule", "by_file")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _clean_label(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip().replace("\\", "/")


def _counts_from_report(payload: Mapping[str, Any]) -> dict[str, Counter[str]]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("Bandit report `results` must be a list")
    counts = {group: Counter[str]() for group in GROUPS}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Bandit report findings must be JSON objects")
        counts["by_severity"][_clean_label(result.get("issue_severity"), "UNKNOWN")] += 1
        counts["by_rule"][_clean_label(result.get("test_id"), "UNKNOWN")] += 1
        counts["by_file"][_clean_label(result.get("filename"), "UNKNOWN")] += 1
    return counts


def _counts_from_summary(payload: Mapping[str, Any]) -> dict[str, Counter[str]]:
    counts = {group: Counter[str]() for group in GROUPS}
    for group in GROUPS:
        values = payload.get(group, {})
        if not isinstance(values, dict):
            raise ValueError(f"summary `{group}` must be a JSON object")
        for label, value in values.items():
            current = value.get("current", 0) if isinstance(value, dict) else value
            if not isinstance(current, int) or current < 0:
                raise ValueError(f"summary count for {group}.{label} must be non-negative")
            counts[group][str(label)] = current
    return counts


def _counts(payload: Mapping[str, Any]) -> dict[str, Counter[str]]:
    return (
        _counts_from_report(payload)
        if "results" in payload
        else _counts_from_summary(payload)
    )


def _delta_group(
    current: Counter[str], baseline: Counter[str]
) -> dict[str, dict[str, int]]:
    return {
        label: {
            "baseline": baseline[label],
            "current": current[label],
            "delta": current[label] - baseline[label],
        }
        for label in sorted(current.keys() | baseline.keys())
    }


def summarize(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    current_counts = _counts(report)
    baseline_counts = (
        _counts(baseline)
        if baseline is not None
        else {group: Counter[str]() for group in GROUPS}
    )
    current_total = sum(current_counts["by_severity"].values())
    baseline_total = sum(baseline_counts["by_severity"].values())
    scanner_errors = report.get("errors", [])
    scanner_error_count = len(scanner_errors) if isinstance(scanner_errors, list) else 1
    return {
        "schema_version": 1,
        "baseline_available": baseline is not None,
        "scanner_error_count": scanner_error_count,
        "totals": {
            "baseline": baseline_total,
            "current": current_total,
            "delta": current_total - baseline_total,
        },
        **{
            group: _delta_group(current_counts[group], baseline_counts[group])
            for group in GROUPS
        },
    }


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--baseline", default="")
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _load(args.report)
    baseline = _load(Path(args.baseline)) if args.baseline else None
    summary = summarize(report, baseline)
    _write(args.output, summary)
    totals = summary["totals"]
    severities = summary["by_severity"]

    def severity_count(name: str) -> int:
        value = severities.get(name, {}).get("current", 0)
        return value if isinstance(value, int) else 0

    print(
        "SAST_SUMMARY "
        f"current={totals['current']} baseline={totals['baseline']} "
        f"delta={totals['delta']:+d} high={severity_count('HIGH')} "
        f"medium={severity_count('MEDIUM')} low={severity_count('LOW')} "
        f"rules={len(summary['by_rule'])} files={len(summary['by_file'])} "
        f"output={args.output}",
        flush=True,
    )
    if summary["scanner_error_count"]:
        print(
            f"SAST_SUMMARY scanner_errors={summary['scanner_error_count']} (details withheld)",
            flush=True,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
