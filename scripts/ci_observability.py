#!/usr/bin/env python3
"""Single-page CI pipeline health summary for operators.

Reads CI state files maintained by the watchdog daemon and ci-check-cooldown
infrastructure, prints a human-readable summary, and exits 0 if CI is healthy
(GREEN or no data), non-zero if CI is RED.

Files read (all in /tmp, all optional):
  /tmp/gludd-watchdog-ci.json        — CI verdict cache from agent_watchdog
  /tmp/gludd-ci-check-state.json     — cooldown state from ci_check_cooldown
  /tmp/gludd-watchdog-push-timestamps.json — push history for loop detection
  /tmp/gludd-orchestrator-state.json  — aggregated orchestrator health

Usage::

    python3 scripts/ci_observability.py [BRANCH]

    make ci-observability
    make ci-observability BRANCH=development
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _now_epoch() -> float:
    return time.time()


def _normalize_ts(value: float) -> float:
    """Normalize a timestamp that may be in seconds or milliseconds."""
    if value > 1_000_000_000_000:
        return value / 1000.0
    return value


def _fmt_ago(ts_epoch: float) -> str:
    delta = _now_epoch() - ts_epoch
    if delta < 0:
        return "future"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    hours = delta / 3600
    if hours < 24:
        return f"{hours:.1f}h ago"
    return f"{hours / 24:.1f}d ago"


def _fmt_ts(ts_epoch: float) -> str:
    if ts_epoch <= 0:
        return "never"
    dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _load_json(path: str) -> dict | list | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _ci_status_icon(status: str) -> str:
    s = status.upper()
    if s == "SUCCESS":
        return "GREEN"
    if s in ("FAILURE", "CANCELLED", "TIMED_OUT"):
        return "RED"
    if s in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING"):
        return "PENDING"
    return "UNKNOWN"


def _ci_status_healthy(status: str) -> bool:
    s = status.upper()
    return s in ("SUCCESS", "")


def main() -> int:
    now = _now_epoch()
    branch = sys.argv[1] if len(sys.argv) > 1 else "master"

    ci_cache = _load_json("/tmp/gludd-watchdog-ci.json") or {}
    cooldown_state = _load_json("/tmp/gludd-ci-check-state.json") or {}
    push_timestamps = _load_json("/tmp/gludd-watchdog-push-timestamps.json") or []
    orch_state = _load_json("/tmp/gludd-orchestrator-state.json") or {}

    ci_status = ci_cache.get("last_ci_status", "NO DATA")
    ci_run_id = ci_cache.get("last_ci_run_id", "—")
    ci_last_check = _normalize_ts(ci_cache.get("last_ci_check", 0.0))
    pending_first_seen = _normalize_ts(ci_cache.get("pending_first_seen", 0.0))
    ci_output = ci_cache.get("last_output", "")

    last_push_epoch = cooldown_state.get("last_push_epoch", 0.0)
    last_check_epoch = cooldown_state.get("last_check_epoch", 0.0)
    check_count = cooldown_state.get("check_count", 0)
    last_head_sha = cooldown_state.get("last_head_sha", "—")

    ci_loop_detected = orch_state.get("ci_loop_detected", False)
    ci_stall = orch_state.get("ci_true_stall", False)

    header = f"=== CI OBSERVABILITY — {branch} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ==="
    print(header)
    print()

    print("── CI Status")
    icon = _ci_status_icon(ci_status)
    print(f"  Verdict:       {icon} ({ci_status})")
    print(f"  Run ID:        {ci_run_id}")
    if ci_last_check > 0:
        stale_warning = ""
        if now - ci_last_check > 1800:
            stale_warning = " *** STALE (>30m old) ***"
        print(f"  Last checked:  {_fmt_ts(ci_last_check)} ({_fmt_ago(ci_last_check)}){stale_warning}")
    else:
        print("  Last checked:  never — no CI data cached")

    if pending_first_seen > 0 and icon == "PENDING":
        pending_mins = (now - pending_first_seen) / 60.0
        print(f"  Pending for:   {pending_mins:.1f} min")

    if ci_output:
        snippet = ci_output.strip()[:120]
        print(f"  Last output:   {snippet}")

    print()
    print("── Cooldown")
    if last_push_epoch > 0:
        print(f"  Last push:     {_fmt_ts(last_push_epoch)} ({_fmt_ago(last_push_epoch)})")
        print(f"  Push SHA:      {last_head_sha[:12]}")
    else:
        print("  Last push:     never")
    if last_check_epoch > 0:
        print(f"  Last CI check: {_fmt_ago(last_check_epoch)} (check #{check_count})")
        cooldown_sec = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))
        remaining = max(0.0, cooldown_sec - (now - last_check_epoch))
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            print(f"  Cooldown:      {mins}m{secs}s remaining (cooldown={cooldown_sec}s)")
        else:
            print("  Cooldown:      EXPIRED — next check allowed")
    else:
        print("  Last CI check: never")

    print()
    print("── Push Rate")
    if isinstance(push_timestamps, list) and push_timestamps:
        ten_min_window = now - 600
        fifteen_min_window = now - 900
        thirty_min_window = now - 1800
        recent_10m = [t for t in push_timestamps if t > ten_min_window]
        recent_15m = [t for t in push_timestamps if t > fifteen_min_window]
        recent_30m = [t for t in push_timestamps if t > thirty_min_window]
        all_recent = len(recent_30m)
        print(f"  Total pushes (last 30m):  {len(recent_30m)}")
        print(f"  Pushes in last 15m:       {len(recent_15m)}")
        print(f"  Pushes in last 10m:       {len(recent_10m)}")
        if ci_loop_detected:
            print("  WARNING: CI push loop detected (>=3 pushes in 10 min window)")
        if ci_stall:
            print("  WARNING: CI true stall — pending >45 min with no new pushes")
        if len(recent_30m) >= 5 and not ci_loop_detected:
            print("  WARNING: high push frequency — consider batching")
    else:
        print("  No push history recorded")

    print()
    print("── Health")
    warnings: list[str] = []
    if ci_last_check > 0 and (now - ci_last_check) > 1800:
        warnings.append("CI cache is STALE (>30 minutes old). CI status may not reflect reality.")
    if ci_loop_detected:
        warnings.append("Push loop detected — CI runs being cancelled by rapid pushes.")
    if ci_stall:
        warnings.append("CI stalled — pending >45 minutes with no pushes.")
    icon = _ci_status_icon(ci_status)
    if icon == "RED":
        warnings.append(f"CI is RED (status={ci_status}). Fix the pipeline.")
    if icon == "PENDING" and pending_first_seen > 0 and (now - pending_first_seen) > 600:
        warnings.append(f"CI pending for >10 min (run {ci_run_id}). Check GitHub for queued jobs.")
    if not ci_cache and not cooldown_state:
        warnings.append("No CI state files found at all — is the watchdog running?")

    if warnings:
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}")
    else:
        print("  No issues detected. Pipeline appears healthy.")

    print()
    print(f"--- {header[4:]} ---")

    healthy = _ci_status_healthy(ci_status)
    if ci_last_check > 0 and (now - ci_last_check) > 1800 and ci_status == "NO DATA":
        healthy = False
    if icon == "RED":
        return 1
    return 0 if healthy else 0


if __name__ == "__main__":
    sys.exit(main())
