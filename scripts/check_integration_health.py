#!/usr/bin/env python3
"""
check_integration_health.py — integration test health checker.

Runs all integration test files with --tb=short -q, captures failures with
file:line:reason, writes structured failure data to
/tmp/gludd-integration-failures.json, and exits non-zero if any failures exist.

Output:
  - stdout: summary of failed files + total failures
  - /tmp/gludd-integration-failures.json: structured failure list
  - Exit 0: no failures
  - Exit 1: one or more failures
  - Exit 2: internal error
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests" / "integration"
OUTPUT_FILE = Path("/tmp/gludd-integration-failures.json")

FAILURE_RE = re.compile(
    r"^(FAILED\s+.*?\n.*?test_.*?\n(?:.*?\n)*?E\s+.*)",
    re.MULTILINE,
)

FAILURE_LINE_RE = re.compile(
    r"^FAILED\s+(\S+)::(\S+):(\d+):\s+(.*)",
    re.MULTILINE,
)

SHORT_FAILURE_RE = re.compile(
    r"^FAILED\s+(\S+)\s*$",
    re.MULTILINE,
)

TB_FAILURE_RE = re.compile(
    r"^FAILED\s+(\S+).*?\n(.*?Error.*)",
    re.MULTILINE | re.DOTALL,
)

FILE_ERROR_RE = re.compile(
    r"^ERROR\s+(\S+)",
    re.MULTILINE,
)

COLLECT_ERROR_RE = re.compile(
    r"^ERROR collecting\s+(\S+)",
    re.MULTILINE,
)


def _find_integration_test_files() -> list[Path]:
    files: list[Path] = []
    for py_file in sorted(TESTS_DIR.rglob("test_*.py")):
        if "__pycache__" in py_file.parts:
            continue
        files.append(py_file)
    return files


def _parse_failures(output: str) -> list[dict]:
    failures: list[dict] = []

    for match in TB_FAILURE_RE.finditer(output):
        block = output[match.start() : match.end()]
        failures.append(
            {
                "raw": block.strip(),
                "test": match.group(1),
            }
        )

    if not failures:
        parsed = _parse_short_summary_failures(output)
        failures.extend(parsed)

    if not failures:
        for match in FILE_ERROR_RE.finditer(output):
            failures.append(
                {
                    "raw": f"ERROR {match.group(1)} (collection or import error)",
                    "test": match.group(1),
                }
            )

    if not failures:
        for match in COLLECT_ERROR_RE.finditer(output):
            failures.append(
                {
                    "raw": f"ERROR collecting {match.group(1)}",
                    "test": match.group(1),
                }
            )

    for f in failures:
        line_match = FAILURE_LINE_RE.search(f.get("raw", ""))
        if line_match:
            f["file"] = line_match.group(1)
            f["line"] = line_match.group(3)
            f["reason"] = line_match.group(4)
        else:
            f["file"] = ""
            f["line"] = ""
            f["reason"] = ""

    return failures


def _parse_short_summary_failures(output: str) -> list[dict]:
    failures: list[dict] = []
    in_failures_section = False

    for line in output.split("\n"):
        if line.startswith("FAILURES") or line.startswith("== FAILURES =="):
            in_failures_section = True
            continue
        if (
            in_failures_section
            and line.startswith("==")
            or in_failures_section
            and line.startswith("short test summary")
        ):
            break
        if in_failures_section and line.strip().startswith("FAILED"):
            match = SHORT_FAILURE_RE.match(line.strip())
            if match:
                failures.append(
                    {
                        "raw": line.strip(),
                        "test": match.group(1),
                    }
                )

    return failures


def main() -> int:
    test_files = _find_integration_test_files()
    if not test_files:
        print("No integration test files found.")
        return 0

    file_paths = [str(f) for f in test_files]
    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "pytest",
        *file_paths,
        "--tb=short",
        "-q",
        "--no-header",
    ]

    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=600,
    )
    elapsed = time.time() - start

    stdout = result.stdout
    stderr = result.stderr
    combined = stdout + "\n" + stderr
    returncode = result.returncode

    failures = _parse_failures(combined)

    if not failures and returncode != 0:
        failures.append(
            {
                "raw": f"Exit code {returncode} with no parseable failures.",
                "test": "",
            }
        )

    failed_files_set: set[str] = set()
    for f in failures:
        if f.get("file"):
            failed_files_set.add(f["file"])

    output_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_sec": round(elapsed, 1),
        "returncode": returncode,
        "total_files": len(test_files),
        "failed_files": len(failed_files_set),
        "total_failures": len(failures),
        "failures": failures,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output_data, indent=2))

    print(f"Integration Health — {len(test_files)} test files, {elapsed:.1f}s")
    if failures:
        print(f"FAIL: {len(failed_files_set)} failed files, {len(failures)} total failures")
        for f in failures:
            loc = f.get("file", "") or f.get("test", "") or "unknown"
            reason = f.get("reason", "") or f.get("raw", "")[:120]
            print(f"  {loc}: {reason}")
        return 1

    print("PASS: 0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
