#!/usr/bin/env python3
"""Clean massive /tmp/gludd-* disk usage.

Truncates large log files, deletes stale state files, keeps active ones.
"""

import glob
import os
import sys

TMP = "/tmp"

ACTIVE_KEEP = {
    "gludd-floor-override",
    "gludd-tool-streak.json",
    "gludd-watchdog-heartbeat.json",
    "gludd-watchdog-disengage.json",
    "gludd-watchdog-ci.json",
    "gludd-enhancement-ratio.json",
    "gludd-ci-check-state.json",
    "gludd-task-deadlines.json",
    "gludd-task-killed.json",
    "gludd-mainthread-streak.json",
    "gludd-block-counter.json",
    "gludd-orchestrator-state.json",
    "gludd-health-score.json",
    "gludd-auto-reset.log",
    "gludd-continue.txt",
    "gludd-force-dispatch.json",
    "gludd-false-done-blocks.json",
    "gludd-false-done-maxout.json",
    "gludd-continue-directive.json",
    "gludd-stop-state.json",
}

def _size(path: str) -> int:
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0

def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    else:
        return f"{n / (1024 * 1024):.1f}MB"

def main() -> int:
    freed = 0
    deleted_count = 0
    truncated_count = 0

    # ── Truncate large log files (>1MB) ────────────────────────────────────
    truncate_files = [
        "gludd-task-deadlines.warnings.log",
        "gludd-watchdog.log",
        "gludd-task-watchdog.log",
        "gludd-secrets-fresh.json",
    ]
    for fn in truncate_files:
        fp = os.path.join(TMP, fn)
        if os.path.isfile(fp):
            sz = _size(fp)
            if sz > 1024 * 1024:
                os.truncate(fp, 0)
                freed += sz
                truncated_count += 1
                print(f"  truncated {fp} ({format_bytes(sz)})")

    # ── Delete live-count files except cache.json ──────────────────────────
    for fp in glob.glob(os.path.join(TMP, "gludd-live-count-*.json")):
        if os.path.basename(fp) == "gludd-live-count-cache.json":
            continue
        freed += _size(fp)
        os.remove(fp)
        deleted_count += 1

    # ── Delete stale patterns ──────────────────────────────────────────────
    patterns = [
        "gludd-gate-test-lock-*",
        "gludd-ship-gate-rc.*",
        "gludd-test-tasks-*",
        "gludd-task-stale-*.json",
    ]
    for pat in patterns:
        for fp in glob.glob(os.path.join(TMP, pat)):
            freed += _size(fp)
            os.remove(fp)
            deleted_count += 1

    # ── Delete old todowrite-state files for stale PIDs ────────────────────
    todowrite_files = glob.glob(os.path.join(TMP, "gludd-todowrite-state-*.json"))
    if todowrite_files:
        for fp in todowrite_files:
            freed += _size(fp)
            os.remove(fp)
            deleted_count += 1

    # ── Delete old session-start files, keep most recent ───────────────────
    session_files = sorted(glob.glob(os.path.join(TMP, "gludd-session-start-*.json")))
    if len(session_files) > 1:
        for fp in session_files[:-1]:
            freed += _size(fp)
            os.remove(fp)
            deleted_count += 1

    # ── Delete stale enhancement-ratio wave files ──────────────────────────
    for fp in glob.glob(os.path.join(TMP, "gludd-enhancement-ratio-wave-*.json")):
        freed += _size(fp)
        os.remove(fp)
        deleted_count += 1

    # ── Delete stale task state files ──────────────────────────────────────
    for pat in ["gludd-task-state-snapshot.json", "gludd-task-state.json",
                "gludd-task-history.json", "gludd-task-timings.json",
                "gludd-task-anomalies.json", "gludd-stalled-tasks.txt",
                "gludd-watchdog-anomaly-count.json",
                "gludd-watchdog-durations.json",
                "gludd-watchdog-timing.json",
                "gludd-watchdog-last-activity.json",
                "gludd-watchdog-last-flag.json",
                "gludd-watchdog-check-cooldowns.json",
                "gludd-watchdog-liveness-backoff.json",
                "gludd-watchdog-push-timestamps.json",
                "gludd-watchdog-stop-count.json",
                "gludd-push-in-progress",
    ]:
        fp = os.path.join(TMP, pat)
        if os.path.isfile(fp):
            freed += _size(fp)
            os.remove(fp)
            deleted_count += 1

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"  truncated: {truncated_count} file(s)")
    print(f"  deleted:   {deleted_count} file(s)")
    print(f"  freed:     {format_bytes(freed)}")
    if freed == 0 and deleted_count == 0 and truncated_count == 0:
        print("  (no stale files found)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
