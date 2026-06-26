#!/usr/bin/env python3
"""5-hour token-window monitor -> throttles the subagent floor near the rate-limit window.

SENSOR: sums token usage from the Claude Code session transcript JSONL files over a
rolling 5-hour window (input + output + cache tokens of every assistant message whose
timestamp is within the last 5h).

ACTUATOR: writes the subagent floor to /tmp/gludd-floor-override -- the SAME live
override the enforce-floor hooks read. Floor=1 lets the subagent pool drain (the Stop
hooks stop forcing refills); floor=7 restores full parallelism.

POLICY (hysteresis so it never flaps at the boundary):
  spend >= 95% of budget -> floor = 1  (drain subagents; conserve the rest of the window)
  spend <  90% of budget -> floor = 7  (window has room or has RESET -> resume full work)
  90%..95%               -> hold (no change)
When the 5h window rolls over, old messages age out of the lookback, spend drops below
90%, and the floor is automatically restored to 7 -- keeping work going across windows.

BUDGET (5h token allowance for your plan -- tune it): read from
/tmp/gludd-5h-token-budget if present, else $GLUDD_5H_TOKEN_BUDGET, else DEFAULT_BUDGET.

Usage:
  token_window_monitor.py --once   # one evaluation, print + act, exit (used by status/test)
  token_window_monitor.py          # loop forever (interval $GLUDD_TOKEN_MONITOR_INTERVAL, default 60s)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

FLOOR_OVERRIDE = Path("/tmp/gludd-floor-override")
BUDGET_FILE = Path("/tmp/gludd-5h-token-budget")
LOG_FILE = Path("/tmp/gludd-token-monitor.log")
PID_FILE = Path("/tmp/gludd-token-monitor.pid")

TRANSCRIPT_DIR = Path(
    os.environ.get(
        "GLUDD_TRANSCRIPT_DIR",
        str(Path.home() / ".claude" / "projects" / "-Users-shawnwilson-gludd"),
    )
)

# Default 5h token allowance, in ACTUAL tokens sent (input + output + cache
# creation + cache read — cache_read dominates, ~95% of the count, because every
# turn re-reads the cached context, and all subagent calls count too).
# Calibrated 2026-06-26 against the operator's observed real rate-limit reading:
# a measured 5h total of 247.5M corresponded to 91%, so the plan's true 5h limit
# is ~272M (247.5M / 0.91), not the old 200M placeholder which over-read at 124%.
# Tune via /tmp/gludd-5h-token-budget or $GLUDD_5H_TOKEN_BUDGET if the plan changes.
DEFAULT_BUDGET = int(os.environ.get("GLUDD_5H_TOKEN_BUDGET", "272000000"))
WINDOW = timedelta(hours=5)
HIGH_WATERMARK = 0.95
LOW_WATERMARK = 0.90
FLOOR_THROTTLE = 1
FLOOR_NORMAL = int(os.environ.get("GLUDD_TOKEN_MONITOR_NORMAL_FLOOR", "7"))


def budget() -> int:
    """Live-tunable 5h token budget (file overrides env/default)."""
    try:
        if BUDGET_FILE.exists():
            v = int(BUDGET_FILE.read_text().strip())
            if v > 0:
                return v
    except Exception:
        pass
    return DEFAULT_BUDGET


def _parse_ts(obj: dict) -> datetime | None:
    ts = obj.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _msg_tokens(obj: dict) -> int:
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return 0
    u = msg.get("usage")
    if not isinstance(u, dict):
        return 0

    def _g(k: str) -> int:
        try:
            return int(u.get(k, 0) or 0)
        except Exception:
            return 0

    return (
        _g("input_tokens")
        + _g("output_tokens")
        + _g("cache_creation_input_tokens")
        + _g("cache_read_input_tokens")
    )


def _entry_id(obj: dict) -> str | None:
    """A stable unique id for a transcript entry, used to de-duplicate usage.

    A compacted/resumed session writes a NEW transcript file that re-embeds
    earlier entries, so the SAME assistant turn can appear in more than one
    ``*.jsonl`` in this dir. Summing every file then double-counts those turns
    and overstates 5h spend. We therefore count each entry at most once, keyed
    by (in order of preference) the transcript-entry ``uuid``, the assistant
    ``message.id``, or the ``requestId``.
    """
    uid = obj.get("uuid")
    if isinstance(uid, str) and uid:
        return uid
    msg = obj.get("message")
    if isinstance(msg, dict):
        mid = msg.get("id")
        if isinstance(mid, str) and mid:
            return f"msg:{mid}"
    rid = obj.get("requestId")
    if isinstance(rid, str) and rid:
        return f"req:{rid}"
    return None


def spend_last_5h(now: datetime | None = None) -> int:
    """Sum token usage across transcript JSONL within the rolling 5h window.

    De-duplicates by transcript-entry id so a turn that appears in multiple
    transcript files (compaction/resume re-embeds earlier turns) is counted
    once, not once per file.
    """
    now = now or datetime.now(UTC)
    cutoff = now - WINDOW
    total = 0
    seen: set[str] = set()
    if not TRANSCRIPT_DIR.exists():
        return 0
    for jf in TRANSCRIPT_DIR.glob("*.jsonl"):
        try:
            with jf.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = _parse_ts(obj)
                    if ts is None or ts < cutoff:
                        continue
                    eid = _entry_id(obj)
                    if eid is not None:
                        if eid in seen:
                            continue  # already counted this turn from another file
                        seen.add(eid)
                    total += _msg_tokens(obj)
        except Exception:
            continue
    return total


def spend_breakdown(now: datetime | None = None) -> dict[str, int]:
    """Per-component token sums over the 5h window (deduped by entry id).

    Diagnostic: shows how much of the total is input vs output vs cache so the
    budget definition can be reconciled with the plan's real rate-limit metric.
    """
    now = now or datetime.now(UTC)
    cutoff = now - WINDOW
    comp = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "entries": 0,
    }
    seen: set[str] = set()
    if not TRANSCRIPT_DIR.exists():
        return comp
    for jf in TRANSCRIPT_DIR.glob("*.jsonl"):
        try:
            with jf.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = _parse_ts(obj)
                    if ts is None or ts < cutoff:
                        continue
                    eid = _entry_id(obj)
                    if eid is not None:
                        if eid in seen:
                            continue
                        seen.add(eid)
                    msg = obj.get("message")
                    u = msg.get("usage") if isinstance(msg, dict) else None
                    if not isinstance(u, dict):
                        continue
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    ):
                        try:
                            comp[k] += int(u.get(k, 0) or 0)
                        except Exception:
                            pass
                    comp["entries"] += 1
        except Exception:
            continue
    return comp


def decide_floor(spend: int, budget_total: int, current_floor: int) -> int:
    """Pure policy with hysteresis: throttle at >=95%, restore at <90%, hold between."""
    if budget_total <= 0:
        return current_floor
    pct = spend / budget_total
    if pct >= HIGH_WATERMARK:
        return FLOOR_THROTTLE
    if pct < LOW_WATERMARK:
        return FLOOR_NORMAL
    return current_floor


def current_floor() -> int:
    try:
        return int(FLOOR_OVERRIDE.read_text().strip())
    except Exception:
        return FLOOR_NORMAL


def _set_floor(v: int) -> None:
    FLOOR_OVERRIDE.write_text(str(v))


def _log(msg: str) -> None:
    line = f"{datetime.now(UTC).isoformat()} [token-monitor] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def evaluate_once() -> tuple[int, int, int]:
    """One sense->decide->act cycle. Returns (spend, budget, target_floor)."""
    b = budget()
    spend = spend_last_5h()
    cur = current_floor()
    target = decide_floor(spend, b, cur)
    pct = (spend / b * 100) if b else 0.0
    if target != cur:
        _set_floor(target)
        _log(f"floor {cur} -> {target}  (5h spend {spend:,}/{b:,} = {pct:.1f}%)")
    else:
        _log(f"hold floor {cur}  (5h spend {spend:,}/{b:,} = {pct:.1f}%)")
    return spend, b, target


def main(argv: list[str]) -> int:
    interval = int(os.environ.get("GLUDD_TOKEN_MONITOR_INTERVAL", "60"))
    if "--breakdown" in argv:
        comp = spend_breakdown()
        b = budget()
        non_cache = comp["input_tokens"] + comp["output_tokens"]
        cache = comp["cache_creation_input_tokens"] + comp["cache_read_input_tokens"]
        total = non_cache + cache
        for k, v in comp.items():
            _log(f"breakdown {k} = {v:,}")
        _log(f"breakdown NON_CACHE(input+output) = {non_cache:,} = {non_cache / b * 100:.1f}% of {b:,}")
        _log(f"breakdown CACHE(creation+read)     = {cache:,}")
        _log(f"breakdown TOTAL(all four)          = {total:,} = {total / b * 100:.1f}% of {b:,}")
        return 0
    once = "--once" in argv
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass
    _log(
        f"starting (budget={budget():,}, interval={interval}s, throttle_floor={FLOOR_THROTTLE}, "
        f"normal_floor={FLOOR_NORMAL}, once={once})"
    )
    if once:
        evaluate_once()
        return 0
    while True:
        try:
            evaluate_once()
        except Exception as exc:
            _log(f"evaluate error: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
