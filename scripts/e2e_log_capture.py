"""E2E log capture — persists terraform, pytest, and deployment output to
.gate-logs/e2e-azure/ for auditability. Never loses error output.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, TypedDict, cast

LOG_DIR = Path(".gate-logs/e2e-azure")
LOG_DIR.mkdir(parents=True, exist_ok=True)


class CaptureResult(TypedDict):
    label: str
    timestamp: str
    command: str
    exit_code: int | None
    log_file: str
    error_summary: list[str] | None
    timeout_seconds: float


class RunSummary(TypedDict):
    label: str
    timestamp: str
    exit_code: int


def _tee_with_timeout(
    proc: subprocess.Popen[str],
    log_file: TextIO,
    timeout_seconds: float,
) -> tuple[int, str, bool]:
    """Stream child output while retaining a wall-clock timeout."""
    stdout = proc.stdout
    assert stdout is not None
    lines: list[str] = []
    line_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for line in stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)

    reader = threading.Thread(target=read_output, name="gludd-e2e-log-reader", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False

    def record(line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()
        log_file.write(line)
        log_file.flush()
        lines.append(line)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if proc.poll() is None:
                proc.kill()
                timed_out = True
            break
        try:
            line = line_queue.get(timeout=min(0.1, remaining))
        except queue.Empty:
            if proc.poll() is not None and not reader.is_alive():
                break
            continue
        if line is None:
            break
        record(line)

    if proc.poll() is None:
        remaining = max(0.001, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            timed_out = True
    proc.wait()
    reader.join(timeout=1.0)
    while True:
        try:
            line = line_queue.get_nowait()
        except queue.Empty:
            break
        if line is not None:
            record(line)

    return (124 if timed_out else int(proc.returncode), "".join(lines), timed_out)


def capture(
    cmd: list[str],
    *,
    label: str,
    env: dict[str, str] | None = None,
    tee: bool = False,
    timeout_seconds: float = 3600,
) -> CaptureResult:
    """Run a command, optionally tee output to console + timestamped log, return result."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{label}-{ts}.log"
    result_path = LOG_DIR / f"{label}-{ts}.json"

    result: CaptureResult = {
        "label": label,
        "timestamp": ts,
        "command": " ".join(cmd),
        "exit_code": None,
        "log_file": str(log_path),
        "error_summary": None,
        "timeout_seconds": timeout_seconds,
    }

    header = f"=== {label} started at {ts} ===\nCommand: {' '.join(cmd)}\n\n"
    sys.stdout.write(header)
    sys.stdout.flush()

    with open(log_path, "w") as f:
        f.write(header)

        timed_out = False
        try:
            if tee:
                with subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env={**os.environ, **(env or {})},
                ) as stream_proc:
                    exit_code, full_output, timed_out = _tee_with_timeout(
                        stream_proc, f, timeout_seconds
                    )
            else:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env={**os.environ, **(env or {})},
                    timeout=timeout_seconds,
                )
                exit_code = completed.returncode
                full_output = completed.stdout
                f.write("=== STDOUT ===\n")
                f.write(completed.stdout)
                f.write("\n=== STDERR ===\n")
                f.write(completed.stderr)

        except subprocess.TimeoutExpired:
            timed_out = True

        if timed_out:
            timeout_label = f"{timeout_seconds:g}s"
            f.write(f"=== TIMEOUT after {timeout_label} ===\n")
            result["exit_code"] = 124
            result["error_summary"] = [f"TIMEOUT after {timeout_label}"]
            with open(result_path, "w") as rf:
                json.dump(result, rf, indent=2)
            return result

        f.write(f"\n=== Exit code: {exit_code} ===\n")

    result["exit_code"] = exit_code
    if exit_code != 0:
        result["error_summary"] = _extract_errors(full_output)

    with open(result_path, "w") as rf:
        json.dump(result, rf, indent=2)

    return result


def _extract_errors(stderr: str) -> list[str]:
    """Extract key error lines from terraform/pytest stderr."""
    errors: list[str] = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(kw in line for kw in ("Error:", "FAILED", "Traceback", "RuntimeError", "FATAL")):
            errors.append(line)
    return errors


def latest_log(label: str) -> Path | None:
    """Return the most recent log file for a label."""
    logs = sorted(LOG_DIR.glob(f"{label}-*.log"))
    return logs[-1] if logs else None


def latest_result(label: str) -> CaptureResult | None:
    """Return the most recent JSON result for a label."""
    results = sorted(LOG_DIR.glob(f"{label}-*.json"))
    if not results:
        return None
    return cast(CaptureResult, json.loads(results[-1].read_text(encoding="utf-8")))


def list_runs() -> list[RunSummary]:
    """List all E2E runs with summary."""
    runs: list[RunSummary] = []
    for f in sorted(LOG_DIR.glob("*.json")):
        try:
            result = cast(CaptureResult, json.loads(f.read_text(encoding="utf-8")))
            runs.append(
                {
                    "label": result["label"],
                    "timestamp": result["timestamp"],
                    "exit_code": result["exit_code"] if result["exit_code"] is not None else -1,
                }
            )
        except Exception:
            runs.append({"label": f.stem, "timestamp": "", "exit_code": -1})
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E log capture — wrap a command and persist output")
    parser.add_argument("--cmd", help="Command to run (will be split on spaces)")
    parser.add_argument("--label", help="Label for log files (e.g. azure-provision)")
    parser.add_argument("--tee", action="store_true", help="Stream command output to console while capturing to file")
    parser.add_argument("--timeout", type=float, default=3600, help="Wall-clock timeout in seconds")
    parser.add_argument("--audit", action="store_true", help="List all E2E runs with PASS/FAIL/RUNNING status")
    parser.add_argument("--latest", metavar="LABEL", help="Show exit code and error summary for latest run")
    args = parser.parse_args()

    if args.audit:
        for run in list_runs():
            status = "PASS" if run["exit_code"] == 0 else ("FAIL" if run["exit_code"] else "RUNNING")
            print(f"{run['timestamp']:20s} {run['label']:25s} {status}")
        sys.exit(0)

    if args.latest:
        latest = latest_result(args.latest)
        if latest:
            print(f"Exit code: {latest['exit_code']}")
            if latest["error_summary"]:
                for e in latest["error_summary"]:
                    print(f"  {e}")
            log = latest_log(args.latest)
            if log:
                print(f"\nFull log: {log}")
                print(log.read_text()[-2000:])
        else:
            print("No E2E logs found")
        sys.exit(latest["exit_code"] if latest and latest["exit_code"] else 0)

    if not args.cmd or not args.label:
        parser.error("--cmd and --label are required for capture mode")

    result = capture(args.cmd.split(), label=args.label, tee=args.tee, timeout_seconds=args.timeout)
    print(json.dumps({k: v for k, v in result.items() if k != "error_summary"}, indent=2))
    if result["error_summary"]:
        print("\n=== ERROR SUMMARY ===")
        for e in result["error_summary"]:
            print(f"  {e}")
    sys.exit(result["exit_code"] or 0)


if __name__ == "__main__":
    main()
