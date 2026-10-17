#!/usr/bin/env python3
"""Print gate completion percentage and ETA based on historical logs."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from general_ludd.gate.progress import (
    cooldown_remaining,
    discover_gate_logs,
    estimate_progress,
    failure_summary,
    parse_gate_log,
    record_cooldown,
    render_progress,
)

DEFAULT_COOLDOWN_SECS = 120.0
STATE_FILE = Path("/tmp/gludd-gate-progress-state.json")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _gate_status(log_dir: Path, current: Path) -> str:
    pid_file = log_dir.parent / ".gate-background.pid"
    if pid_file.exists():
        text = pid_file.read_text(encoding="utf-8").strip()
        if text.isdigit() and _pid_alive(int(text)):
            return f"RUNNING (pid={text})"
    run = parse_gate_log(current)
    if run.terminal is not None:
        return f"FINISHED: {run.terminal}"
    return "IDLE (no running gate)"


def _first_fail_phase(log_dir: Path) -> str | None:
    status_file = log_dir.parent / ".gate-status"
    if not status_file.exists():
        return None
    for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if " FAIL" in stripped and not stripped.startswith("==="):
            return stripped
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Estimate running gate completion and ETA.")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(".gate-logs"),
        help="Directory containing gate-*.log files (default: .gate-logs)",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="Path to the current gate log (default: newest gate-*.log)",
    )
    parser.add_argument(
        "--max-historical",
        type=int,
        default=20,
        help="Number of historical logs to use for estimation (default: 20)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the historical template and per-label averages",
    )
    parser.add_argument(
        "--cooldown-secs",
        type=float,
        default=DEFAULT_COOLDOWN_SECS,
        help=f"Minimum seconds between polls (default: {DEFAULT_COOLDOWN_SECS:g})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the poll cooldown",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=STATE_FILE,
        help=f"Cooldown state file (default: {STATE_FILE})",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep this many seconds before printing status (default: 0)",
    )
    args = parser.parse_args(argv)

    cooldown_note = ""
    remaining_val: float | None = None
    if not args.force:
        remaining_val = cooldown_remaining(args.state_file, args.cooldown_secs, time.time())
        if remaining_val > 1.0:
            cooldown_note = f" | COOLDOWN: {remaining_val:.0f}s remaining (GATE_PROGRESS_FORCE=1 skips)"
        else:
            record_cooldown(args.state_file, time.time())
    else:
        record_cooldown(args.state_file, time.time())

    current = args.current
    if current is None:
        logs = discover_gate_logs(args.log_dir, max_historical=args.max_historical + 1)
        if not logs:
            print("No gate logs found.", file=sys.stderr)
            return 1
        current = logs[0]

    historical = discover_gate_logs(args.log_dir, max_historical=args.max_historical)
    historical = [p for p in historical if p != current]

    if args.debug:
        from general_ludd.gate.estimation import _average_durations, _template_sequence

        hist_runs = [parse_gate_log(p) for p in historical]
        template = _template_sequence(hist_runs)
        averages = _average_durations(hist_runs)
        print(f"template ({len(template)} labels):")
        print("  " + " -> ".join(template))
        print("averages (seconds):")
        for label, avg in sorted(averages.items()):
            print(f"  {label}: {avg:.1f}s")

    report = estimate_progress(current, historical)
    status = _gate_status(args.log_dir, current)
    failure: str | None = None
    if status.startswith("FINISHED: FAILED"):
        failure = failure_summary(parse_gate_log(current))
        if not failure:
            phase = _first_fail_phase(args.log_dir)
            if phase:
                failure = f"phase {phase}"
    next_poll_secs: float | None = None
    if status.startswith("RUNNING"):
        if remaining_val is not None and remaining_val > 1.0:
            next_poll_secs = remaining_val
        else:
            next_poll_secs = float(args.cooldown_secs)
    print(
        render_progress(
            report,
            status=status,
            failure=failure,
            next_poll_secs=next_poll_secs,
        )
        + cooldown_note
    )

    if args.sleep > 0:
        time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
