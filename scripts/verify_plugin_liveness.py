#!/usr/bin/env python3
"""Verify runtime liveness of the core enforcement plugins.

This complements `scripts/check_plugin_liveness.py` (which structurally
audits enforce-stop.ts source + counter files) with a per-plugin heartbeat
read that answers the P0 audit question directly: are enforce-floor,
enforce-delegate, and enforce-stop ACTUALLY executing their tool.execute.before
hook, or are they registered-but-inert?

Each plugin writes `/tmp/gludd-plugin-heartbeat-<name>.json` at the top of
its tool.execute.before hook (fail-open) and appends a LOADED line to
`/tmp/gludd-plugin-loaded.log` when opencode invokes its factory. This script
reads those artifacts and reports:

  - LOADED:   did the factory ever run for this plugin?
  - HEARTBEAT: did tool.execute.before fire recently (within STALE_SECS)?
  - PID:      the process that wrote the heartbeat (a stale PID implies a
              restart is needed — the live opencode process is not the one
              that wrote the heartbeat).

Exit code:
  0  all plugins are LOADED + have a fresh heartbeat
  1  any plugin is missing LOADED evidence OR has a stale/absent heartbeat

Tunables:
  STALE_SECS   heartbeat age threshold (default 60)
  PLUGINS      comma-separated plugin names to check (default: the three core
               enforcement plugins)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

STALE_SECS = int(os.environ.get("GLUDD_HEARTBEAT_STALE_SECS", "60"))
HEARTBEAT_DIR = Path(os.environ.get("GLUDD_HEARTBEAT_DIR", "/tmp"))
LOADED_LOG = Path(os.environ.get("GLUDD_PLUGIN_LOADED_LOG", "/tmp/gludd-plugin-loaded.log"))

DEFAULT_PLUGINS = ["enforce-floor", "enforce-delegate", "enforce-stop"]


def _plugins() -> list[str]:
    raw = os.environ.get("PLUGINS")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return DEFAULT_PLUGINS


def _read_heartbeat(name: str) -> Optional[dict]:
    path = HEARTBEAT_DIR / f"gludd-plugin-heartbeat-{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _loaded_entries() -> dict[str, str]:
    """Map plugin name -> last LOADED timestamp string seen in the log."""
    out: dict[str, str] = {}
    if not LOADED_LOG.exists():
        return out
    try:
        for line in LOADED_LOG.read_text(encoding="utf-8").splitlines():
            # shape: "<iso> LOADED <name> <surfaces...> pid=<pid>"
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "LOADED":
                out[parts[2]] = parts[0]
    except Exception:
        pass
    return out


def check_plugin(name: str, loaded_map: dict[str, str], now: float) -> dict:
    hb = _read_heartbeat(name)
    loaded_ts = loaded_map.get(name)

    if hb is None:
        return {
            "plugin": name,
            "loaded": bool(loaded_ts),
            "loaded_ts": loaded_ts,
            "heartbeat": False,
            "heartbeat_age_secs": None,
            "pid": None,
            "status": "MISSING" if not loaded_ts else "INERT",
            "detail": (
                "no heartbeat file — tool.execute.before has never fired"
                if loaded_ts
                else "no LOADED log entry AND no heartbeat — plugin is not registered"
            ),
        }

    hb_ts = hb.get("ts")
    if not isinstance(hb_ts, (int, float)) or hb_ts <= 0:
        return {
            "plugin": name, "loaded": bool(loaded_ts), "loaded_ts": loaded_ts,
            "heartbeat": False, "heartbeat_age_secs": None,
            "pid": hb.get("pid"), "status": "MALFORMED",
            "detail": f"heartbeat file present but ts field invalid: {hb!r}",
        }

    age = now - (hb_ts / 1000.0) if hb_ts > 1e11 else now - hb_ts
    fresh = age <= STALE_SECS
    return {
        "plugin": name,
        "loaded": bool(loaded_ts),
        "loaded_ts": loaded_ts,
        "heartbeat": fresh,
        "heartbeat_age_secs": round(age, 1),
        "pid": hb.get("pid"),
        "status": "ACTIVE" if fresh else "STALE",
        "detail": (
            f"tool.execute.before fired {age:.1f}s ago (<= {STALE_SECS}s threshold)"
            if fresh
            else f"last fired {age:.1f}s ago (> {STALE_SECS}s threshold) — "
                 "plugin may be inert or session idle; a stale PID also implies "
                 "opencode needs a restart to pick up plugin changes"
        ),
    }


def main() -> int:
    plugins = _plugins()
    now = time.time()
    loaded_map = _loaded_entries()
    results = [check_plugin(p, loaded_map, now) for p in plugins]

    print("=== Plugin Heartbeat Liveness Check ===")
    print(f"stale threshold: {STALE_SECS}s   loaded log: {LOADED_LOG}")
    print()
    print(f"{'PLUGIN':<22} {'STATUS':<10} {'LOADED':<8} {'HB_AGE':<10} {'PID':<10} DETAIL")
    print("-" * 100)

    failed = False
    for r in results:
        marker = "OK" if r["status"] == "ACTIVE" else "FAIL"
        if r["status"] != "ACTIVE":
            failed = True
        hb_age = r["heartbeat_age_secs"]
        hb_str = f"{hb_age}s" if hb_age is not None else "-"
        pid_str = str(r["pid"]) if r["pid"] is not None else "-"
        loaded_str = "yes" if r["loaded"] else "NO"
        print(f"{r['plugin']:<22} [{marker}] {r['status']:<8} {loaded_str:<8} {hb_str:<10} {pid_str:<10} {r['detail']}")

    print()
    if failed:
        inert = [r["plugin"] for r in results if r["status"] in ("MISSING", "INERT", "MALFORMED")]
        stale = [r["plugin"] for r in results if r["status"] == "STALE"]
        print("RESULT: FAIL — runtime evidence shows plugins are NOT all firing.")
        if inert:
            print(f"  REGISTERED-BUT-INERT (no heartbeat ever written): {inert}")
            print("  These plugins loaded but their tool.execute.before hook never ran.")
            print("  Most likely cause: opencode has not been restarted since the .ts")
            print("  edits (BUGS.md:427 — no hot-reload). The heartbeat write requires")
            print("  a restart to take effect, AND a fresh opencode process to confirm")
            print("  the hook actually fires.")
        if stale:
            print(f"  STALE (heartbeat older than {STALE_SECS}s): {stale}")
            print("  Either the session is idle (no tool calls → no heartbeat) or the")
            print("  plugin hook is silently failing. Make a tool call, then re-run.")
        return 1

    print("RESULT: PASS — all checked plugins fired their hook within the threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
