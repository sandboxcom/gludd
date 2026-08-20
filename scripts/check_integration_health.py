#!/usr/bin/env python3
"""
check_integration_health.py — integration test health checker.

Runs all integration test files with --tb=short -q, streams output live,
writes incremental failure data to /tmp/gludd-integration-failures.json,
and exits non-zero if any failures exist.

Output:
  - stdout: live pytest output + periodic progress markers
  - /tmp/gludd-integration-failures.json: structured failure list
    (written incrementally every 30s while running, finalized on completion)
  - Exit 0: no failures
  - Exit 1: one or more failures
  - Exit 2: internal error (timeout, subprocess failure)
"""

from __future__ import annotations

import json
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests" / "integration"
OUTPUT_FILE = Path("/tmp/gludd-integration-failures.json")

TIMEOUT_SEC = 900
INTERMEDIATE_INTERVAL_SEC = 30
PROGRESS_INTERVAL_FILES = 5

XDIST_FAILURE_RE = re.compile(
    r"^(?:\[[^\]\n]*\]\s+)*FAILED\s+"
    r"(?P<test>\S+?\.py(?:::\S+)*)"
    r"(?:\s+-\s+(?P<reason>.*))?\s*$",
    re.MULTILINE,
)

FILE_ERROR_RE = re.compile(
    r"^ERROR\s+(\S+)",
    re.MULTILINE,
)

COLLECT_ERROR_RE = re.compile(
    r"^ERROR collecting\s+(\S+)",
    re.MULTILINE,
)

RESULT_LINE_RE = re.compile(
    r"(?:PASSED|FAILED|ERROR)\s+\S*tests/integration/",
)


def _find_integration_test_files() -> list[Path]:
    files: list[Path] = []
    for py_file in sorted(TESTS_DIR.rglob("test_*.py")):
        if "__pycache__" in py_file.parts:
            continue
        files.append(py_file)
    return files


def _parse_failures(output: str) -> list[dict[str, Any]]:
    failures = _parse_short_summary_failures(output)

    if not failures:
        for match in COLLECT_ERROR_RE.finditer(output):
            failures.append(
                {
                    "raw": f"ERROR collecting {match.group(1)}",
                    "test": match.group(1),
                }
            )

    if not failures:
        for match in FILE_ERROR_RE.finditer(output):
            failures.append(
                {
                    "raw": f"ERROR {match.group(1)} (collection or import error)",
                    "test": match.group(1),
                }
            )

    for f in failures:
        test = str(f.get("test", ""))
        f.setdefault("file", test.split("::", 1)[0] if test else "")
        f.setdefault("line", "")
        f.setdefault("reason", "")

    return failures


def _parse_short_summary_failures(output: str) -> list[dict[str, Any]]:
    failures_by_test: dict[str, dict[str, Any]] = {}

    for match in XDIST_FAILURE_RE.finditer(output):
        test = match.group("test")
        reason = (match.group("reason") or "").strip()
        failure = {
            "raw": match.group(0).strip(),
            "test": test,
            "file": test.split("::", 1)[0],
            "line": "",
            "reason": reason,
        }
        previous = failures_by_test.get(test)
        if previous is None or (reason and len(reason) > len(previous["reason"])):
            failures_by_test[test] = failure

    return list(failures_by_test.values())


def _write_output(data: dict[str, Any]) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUTPUT_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.rename(OUTPUT_FILE)


_accumulated_lines: list[str] = []
_accumulated_lock = threading.Lock()
_test_file_count: int = 0
_start_time: float = 0.0


def _signal_handler(signum: int, frame: object) -> None:
    with _accumulated_lock:
        accumulated = "".join(_accumulated_lines)
    failures = _parse_failures(accumulated)
    failed_files_set = {f.get("file", "") for f in failures if f.get("file")}
    data: dict[str, object] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_sec": round(time.time() - _start_time, 1),
        "status": "interrupted",
        "returncode": None,
        "total_files": _test_file_count,
        "failed_files": len(failed_files_set),
        "total_failures": len(failures),
        "failures": failures,
    }
    _write_output(data)
    raise SystemExit(1)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


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
        "-n",
        "auto",
        "--dist",
        "loadgroup",
        "--tb=short",
        "-q",
        "--no-header",
    ]

    start = time.time()
    global _test_file_count, _start_time
    _test_file_count = len(test_files)
    _start_time = start
    last_write = start
    test_count = 0
    error_msg: str | None = None

    def _reader(proc: subprocess.Popen[str]) -> None:
        nonlocal test_count, last_write, error_msg
        prev_failure_count = 0
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                with _accumulated_lock:
                    _accumulated_lines.append(line)

                is_result = RESULT_LINE_RE.search(line)
                if is_result:
                    with _accumulated_lock:
                        test_count += 1
                        if test_count % PROGRESS_INTERVAL_FILES == 0:
                            failures_so_far = _parse_failures("".join(_accumulated_lines))
                            print(
                                f"\n--- Progress: ~{test_count} results, "
                                f"{len(failures_so_far)} failures "
                                f"({time.time() - _start_time:.1f}s) ---\n"
                            )

                    with _accumulated_lock:
                        failures_now = _parse_failures("".join(_accumulated_lines))
                        if len(failures_now) > prev_failure_count:
                            prev_failure_count = len(failures_now)
                            now = time.time()
                            data = {
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "elapsed_sec": round(now - _start_time, 1),
                                "status": "running",
                                "returncode": None,
                                "total_files": _test_file_count,
                                "failures_so_far": len(failures_now),
                                "failures": failures_now,
                            }
                            _write_output(data)
                            last_write = now

                now = time.time()
                if now - last_write >= INTERMEDIATE_INTERVAL_SEC:
                    with _accumulated_lock:
                        failures_now = _parse_failures("".join(_accumulated_lines))
                        if len(failures_now) > prev_failure_count:
                            prev_failure_count = len(failures_now)
                        data = {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "elapsed_sec": round(now - _start_time, 1),
                            "status": "running",
                            "returncode": None,
                            "total_files": _test_file_count,
                            "failures_so_far": len(failures_now),
                            "failures": failures_now,
                        }
                        _write_output(data)
                        last_write = now
        except Exception as exc:
            with _accumulated_lock:
                error_msg = str(exc)

    print(f"Running {len(test_files)} integration test files (timeout {TIMEOUT_SEC}s)...")
    print(f"Command: {' '.join(cmd)}\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=PROJECT_ROOT,
            bufsize=1,
        )
    except OSError as exc:
        print(f"ERROR: failed to start pytest: {exc}", file=sys.stderr)
        return 2

    reader_thread = threading.Thread(target=_reader, args=(proc,), daemon=True)
    reader_thread.start()

    timed_out = False
    try:
        try:
            returncode = proc.wait(timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            returncode = proc.wait(timeout=5)
            timed_out = True
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        reader_thread.join(timeout=10)

    elapsed = time.time() - start

    with _accumulated_lock:
        combined = "".join(_accumulated_lines)

    if timed_out:
        print(f"\nTIMEOUT: integration tests exceeded {TIMEOUT_SEC}s")
        failures_timeout = _parse_failures(combined)
        output_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": round(elapsed, 1),
            "status": "timeout",
            "returncode": returncode,
            "total_files": len(test_files),
            "failed_files": len({f["file"] for f in failures_timeout if f.get("file")}),
            "total_failures": len(failures_timeout),
            "failures": failures_timeout,
        }
        _write_output(output_data)
        print(f"Partial results: {len(failures_timeout)} failures captured")
        return 2

    if error_msg is not None:
        print(f"ERROR reading pytest output: {error_msg}", file=sys.stderr)

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
        "status": "complete",
        "returncode": returncode,
        "total_files": len(test_files),
        "failed_files": len(failed_files_set),
        "total_failures": len(failures),
        "failures": failures,
    }

    _write_output(output_data)

    print(f"\nIntegration Health — {len(test_files)} test files, {elapsed:.1f}s")
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
