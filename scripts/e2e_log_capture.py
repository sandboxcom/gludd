"""E2E log capture — persists terraform, pytest, and deployment output to
.gate-logs/e2e-azure/ for auditability. Never loses error output.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


LOG_DIR = Path(".gate-logs/e2e-azure")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def capture(cmd: list[str], *, label: str, env: dict | None = None) -> dict:
    """Run a command, tee output to a timestamped log file, return result dict."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{label}-{ts}.log"
    result_path = LOG_DIR / f"{label}-{ts}.json"

    result = {
        "label": label,
        "timestamp": ts,
        "command": " ".join(cmd),
        "exit_code": None,
        "log_file": str(log_path),
        "error_summary": None,
    }

    with open(log_path, "w") as f:
        f.write(f"=== {label} started at {ts} ===\n")
        f.write(f"Command: {' '.join(cmd)}\n")
        sanitized_env = {k: "***" if "SECRET" in k or "KEY" in k else v for k, v in (env or {}).items()}
        f.write(f"Env: {json.dumps(sanitized_env, indent=2)}\n\n")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=3600)
        except subprocess.TimeoutExpired:
            f.write("=== TIMEOUT after 3600s ===\n")
            result["exit_code"] = 124
            result["error_summary"] = ["TIMEOUT after 3600s"]
            with open(result_path, "w") as rf:
                json.dump(result, rf, indent=2)
            return result

        f.write("=== STDOUT ===\n")
        f.write(proc.stdout)
        f.write("\n=== STDERR ===\n")
        f.write(proc.stderr)
        f.write(f"\n=== Exit code: {proc.returncode} ===\n")

    result["exit_code"] = proc.returncode
    if proc.returncode != 0:
        result["error_summary"] = _extract_errors(proc.stderr)

    with open(result_path, "w") as rf:
        json.dump(result, rf, indent=2)

    return result


def _extract_errors(stderr: str) -> list[str]:
    """Extract key error lines from terraform/pytest stderr."""
    errors = []
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


def latest_result(label: str) -> dict | None:
    """Return the most recent JSON result for a label."""
    results = sorted(LOG_DIR.glob(f"{label}-*.json"))
    if not results:
        return None
    return json.loads(results[-1].read_text())


def list_runs() -> list[dict]:
    """List all E2E runs with summary."""
    runs = []
    for f in sorted(LOG_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
            runs.append({"label": d["label"], "timestamp": d["timestamp"], "exit_code": d["exit_code"]})
        except Exception:
            runs.append({"label": f.stem, "timestamp": "", "exit_code": -1})
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E log capture — wrap a command and persist output")
    parser.add_argument("--cmd", help="Command to run (will be split on spaces)")
    parser.add_argument("--label", help="Label for log files (e.g. azure-provision)")
    parser.add_argument("--audit", action="store_true", help="List all E2E runs with PASS/FAIL/RUNNING status")
    parser.add_argument("--latest", metavar="LABEL", help="Show exit code and error summary for latest run")
    args = parser.parse_args()

    if args.audit:
        for r in list_runs():
            status = "PASS" if r["exit_code"] == 0 else ("FAIL" if r["exit_code"] else "RUNNING")
            print(f"{r['timestamp']:20s} {r['label']:25s} {status}")
        sys.exit(0)

    if args.latest:
        r = latest_result(args.latest)
        if r:
            print(f"Exit code: {r['exit_code']}")
            if r.get("error_summary"):
                for e in r["error_summary"]:
                    print(f"  {e}")
            log = latest_log(args.latest)
            if log:
                print(f"\nFull log: {log}")
                print(log.read_text()[-2000:])
        else:
            print("No E2E logs found")
        sys.exit(r["exit_code"] if r and r["exit_code"] else 0)

    if not args.cmd or not args.label:
        parser.error("--cmd and --label are required for capture mode")

    result = capture(args.cmd.split(), label=args.label)
    print(json.dumps({k: v for k, v in result.items() if k != "error_summary"}, indent=2))
    if result["error_summary"]:
        print("\n=== ERROR SUMMARY ===")
        for e in result["error_summary"]:
            print(f"  {e}")
    sys.exit(result["exit_code"] or 0)


if __name__ == "__main__":
    main()
