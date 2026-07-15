#!/usr/bin/env python3
"""verify_coverage — run pytest-cov on generated test files, verify thresholds.

Usage:
    python verify_coverage.py --test-dir <dir> --source-module <path> --output <json>
    python verify_coverage.py --test-dir <dir> --source-module <path> --output <json> --threshold 85

Runs pytest --cov on generated test files against the source module, checks
coverage threshold, and writes a structured coverage report.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _find_test_files(test_dir: Path, prefix: str) -> list[str]:
    if not test_dir.is_dir():
        return []
    return sorted(
        str(p) for p in test_dir.glob(f"{prefix}*.py") if p.is_file()
    )


def _parse_coverage_json(cov_path: Path) -> dict:
    if not cov_path.is_file():
        return {"totals": {"percent_covered": 0.0}}
    with open(cov_path) as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pytest-cov and verify code coverage thresholds"
    )
    parser.add_argument("--test-dir", required=True, help="Directory with generated test files")
    parser.add_argument("--source-module", required=True, help="Source module to measure coverage for")
    parser.add_argument("--output", required=True, help="Path for coverage_report.json")
    parser.add_argument("--threshold", type=int, default=85, help="Coverage threshold percentage")
    parser.add_argument("--timeout", type=int, default=300, help="Pytest timeout per test")
    parser.add_argument("--test-file-prefix", default="test_e2e_generated_", help="Prefix for test file glob")

    args = parser.parse_args()

    test_dir = Path(args.test_dir)
    source = Path(args.source_module)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    test_files = _find_test_files(test_dir, args.test_file_prefix)
    if not test_files:
        report = {
            "module": str(source),
            "test_output_dir": str(test_dir),
            "coverage_percent": 0.0,
            "threshold": args.threshold,
            "verdict": "skip",
            "verdict_reason": "No generated test files found",
            "pytest_exit_code": -1,
            "coverage_targets": [],
            "status": "completed",
        }
        with open(output, "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report))
        sys.exit(0)

    cov_json = output.parent / ".coverage_raw.json"
    cmd = [
        sys.executable, "-m", "pytest",
        *test_files,
        f"--cov={source}",
        f"--cov-report=json:{cov_json}",
        "--cov-report=term",
        f"--cov-fail-under={args.threshold}",
        f"--timeout={args.timeout}",
        "-v",
        "--tb=short",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout + 60)
    pytest_passed = result.returncode == 0

    cov_data = _parse_coverage_json(cov_json)
    coverage_pct = round(cov_data.get("totals", {}).get("percent_covered", 0.0), 1)

    verdict = "pass" if pytest_passed else "fail"
    reason = (
        f"All tests pass. Coverage {coverage_pct}% meets threshold {args.threshold}%."
        if pytest_passed
        else f"pytest failed with rc={result.returncode}. Coverage {coverage_pct}% vs threshold {args.threshold}%."
    )

    report = {
        "module": str(source),
        "test_output_dir": str(test_dir),
        "coverage_percent": coverage_pct,
        "threshold": args.threshold,
        "verdict": verdict,
        "verdict_reason": reason,
        "pytest_exit_code": result.returncode,
        "coverage_targets": [str(tf) for tf in test_files],
        "pytest_output_tail": result.stdout[-2000:] if result.stdout else "",
        "status": "completed",
    }

    with open(output, "w") as f:
        json.dump(report, f, indent=2)

    pytest_log = output.parent / "pytest_output.log"
    with open(pytest_log, "w") as f:
        f.write(result.stdout or "")
        f.write("\n")
        f.write(result.stderr or "")

    print(json.dumps(report))


if __name__ == "__main__":
    main()
