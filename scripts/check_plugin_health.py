#!/usr/bin/env python3
"""Plugin health dashboard — one-stop liveness + state + hook-fire observability.

Reads the plugin lifecycle artifacts written by enforcement plugins via the
shared.ts `reportAlive` / `writeHeartbeat` / `writeJsonFile` helpers and the
hook-fires JSONL log (if any plugin records per-invocation data), plus the
enforcement state files (disengage, floor-override, streak, deadlines, etc.).

Reports:
  1. Which plugins are alive (recently reported via reportAlive)
  2. Which hooks fired (from /tmp/gludd-hook-fires.jsonl, if exists)
  3. Enforcement state summary (disengaged?, floor override?, streak, etc.)

Exit 0 on success. Exits 1 if alive.json is missing entirely (implies no
plugins loaded). Writes structured result to /tmp/gludd-plugin-health.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, TypedDict

try:
    from scripts import gludd_env_defaults as gludd_env_defaults
except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
    import gludd_env_defaults


WORKSPACE = Path(os.environ.get("GLUDD_WORKSPACE_ROOT", os.getcwd()))
ALIVE_PATH = Path(os.environ.get("GLUDD_ALIVE_PATH", "/tmp/gludd-plugin-alive.json"))
HOOK_FIRES_PATH = Path("/tmp/gludd-hook-fires.jsonl")
DISENGAGE_PATH = Path("/tmp/gludd-watchdog-disengage.json")
FLOOR_OVERRIDE_PATH = Path("/tmp/gludd-floor-override")
STREAK_PATH = Path("/tmp/gludd-tool-streak.json")
SESSION_START_PATH = Path("/tmp/gludd-session-start.json")
TASK_DEADLINES_PATH = Path("/tmp/gludd-task-deadlines.json")
ENHANCEMENT_RATIO_PATH = Path("/tmp/gludd-enhancement-ratio.json")
CI_CHECK_PATH = Path("/tmp/gludd-ci-check-state.json")
HEARTBEAT_DIR = Path("/tmp")
RESULT_PATH = Path("/tmp/gludd-plugin-health.json")
MAX_AGE_SECS = int(os.environ.get("GLUDD_LIVENESS_MAX_AGE", gludd_env_defaults.LIVENESS_MAX_AGE_DEFAULT))


# ── Typed results ────────────────────────────────────────────────────────────


class AliveEntry(TypedDict):
    last_seen_epoch_ms: float
    last_seen_iso: str
    age_secs: float
    status: str  # OK | STALE


class HookFireEntry(TypedDict):
    plugin: str
    hook: str
    ts_epoch_ms: float
    ts_iso: str
    age_secs: float


class EnforcementState(TypedDict):
    disengaged: bool
    disengage_until_iso: str
    floor_override: Optional[int]
    streak: Optional[int]
    session_start_active: bool
    task_deadlines_active: bool
    ci_check_cooldown_remaining_secs: Optional[float]


class HealthResult(TypedDict):
    timestamp: str
    ts_epoch: float
    alive_plugins: list[AliveEntry]
    stale_plugins: list[AliveEntry]
    total_alive: int
    total_loaded_plugins: int
    hook_fires: list[HookFireEntry]
    total_hook_fires: int
    hook_fire_fields: list[str]
    enforcement: EnforcementState


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ts_to_iso(epoch_ms: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_ms / 1000.0))


# ── 1. Plugin aliveness (from alive.json) ────────────────────────────────────


def _check_alive_plugins(now: float) -> tuple[list[AliveEntry], list[AliveEntry]]:
    alive = _read_json(ALIVE_PATH)
    if alive is None:
        return [], []

    ok: list[AliveEntry] = []
    stale: list[AliveEntry] = []
    for name, data in alive.items():
        last_seen = data.get("last_seen", 0) if isinstance(data, dict) else 0
        age = now - (last_seen / 1000.0) if last_seen > 1e11 else now - last_seen
        entry: AliveEntry = {
            "last_seen_epoch_ms": float(last_seen),
            "last_seen_iso": _ts_to_iso(last_seen) if last_seen else "unknown",
            "age_secs": age,
            "status": "STALE" if age > MAX_AGE_SECS else "OK",
        }
        if age > MAX_AGE_SECS:
            stale.append(entry)
        else:
            ok.append(entry)

    return ok, stale


# ── 2. Hook fires (from hook-fires.jsonl) ────────────────────────────────────


def _check_hook_fires(now: float) -> list[HookFireEntry]:
    if not HOOK_FIRES_PATH.exists():
        return []

    entries: list[HookFireEntry] = []
    try:
        for raw_line in HOOK_FIRES_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts", rec.get("timestamp", 0))
            age = now - (ts / 1000.0) if ts > 1e11 else now - ts
            entries.append({
                "plugin": str(rec.get("plugin", rec.get("name", "unknown"))),
                "hook": str(rec.get("hook", rec.get("event", "unknown"))),
                "ts_epoch_ms": float(ts),
                "ts_iso": _ts_to_iso(ts) if ts else "unknown",
                "age_secs": age,
            })
    except Exception:
        pass

    return sorted(entries, key=lambda e: e["ts_epoch_ms"], reverse=True)


# ── 3. Enforcement state ─────────────────────────────────────────────────────


def _check_enforcement_state(now: float) -> EnforcementState:
    state: EnforcementState = {
        "disengaged": False,
        "disengage_until_iso": "",
        "floor_override": None,
        "streak": None,
        "session_start_active": False,
        "task_deadlines_active": False,
        "ci_check_cooldown_remaining_secs": None,
    }

    # Disengage
    disengage = _read_json(DISENGAGE_PATH)
    if disengage is not None:
        until = (
            disengage.get("disengage_until_epoch_ms", 0)
            or disengage.get("disengage_until", 0)
        )
        state["disengaged"] = until > (now * 1000)
        state["disengage_until_iso"] = (
            _ts_to_iso(until) if until else "expired"
        )

    # Floor override
    if FLOOR_OVERRIDE_PATH.exists():
        try:
            val = FLOOR_OVERRIDE_PATH.read_text(encoding="utf-8").strip()
            state["floor_override"] = int(val) if val.isdigit() else None
        except Exception:
            pass

    # Streak
    streak = _read_json(STREAK_PATH)
    if streak is not None:
        state["streak"] = streak.get("streak", 0)

    # Session start
    ss = _read_json(SESSION_START_PATH)
    if ss is not None:
        state["session_start_active"] = bool(
            ss.get("active")
            or (ss.get("dispatches", 0) > 0 and ss.get("min_met", False))
            or os.path.exists(SESSION_START_PATH)
        )

    # Task deadlines
    td = _read_json(TASK_DEADLINES_PATH)
    if td is not None:
        state["task_deadlines_active"] = bool(td) and len(td) > 0

    # CI check cooldown
    cc = _read_json(CI_CHECK_PATH)
    if cc is not None:
        last_check = cc.get("last_check_epoch", 0)
        cooldown = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))
        if last_check > 0:
            elapsed = now - last_check
            remaining = max(0, cooldown - elapsed)
            state["ci_check_cooldown_remaining_secs"] = remaining

    return state


# ── Main ─────────────────────────────────────────────────────────────────────


def run_check() -> HealthResult:
    now = time.time()
    ok_plugins, stale_plugins = _check_alive_plugins(now)
    hook_fires = _check_hook_fires(now)
    enforcement = _check_enforcement_state(now)

    all_plugins = ok_plugins + stale_plugins
    all_names = sorted(set(
        list({p for p in [ok_plugins, stale_plugins] if isinstance(p, dict)})
    ))

    fire_fields: list[str] = []
    for entry in hook_fires[:1]:
        fire_fields = list(entry.keys())

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ts_epoch": now,
        "alive_plugins": ok_plugins,
        "stale_plugins": stale_plugins,
        "total_alive": len(ok_plugins),
        "total_loaded_plugins": len(all_plugins),
        "hook_fires": hook_fires,
        "total_hook_fires": len(hook_fires),
        "hook_fire_fields": fire_fields,
        "enforcement": enforcement,
    }


def main() -> int:
    result = run_check()

    try:
        RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception:
        pass

    print("=== Plugin Health Dashboard ===")
    print(f"Timestamp: {result['timestamp']}")
    print()

    # ── 1. Plugin aliveness ──
    print(f"--- Plugin Aliveness ({result['total_alive']} OK / {result['total_loaded_plugins']} loaded) ---")
    if result["total_loaded_plugins"] == 0:
        print("  MISSING: /tmp/gludd-plugin-alive.json not found")
        print("  No enforcement plugins have reported alive yet.")
        print("  This is normal if opencode just started or plugins haven't loaded.")
    else:
        ok_names = sorted([p.get("last_seen_iso", "?") for p in result["alive_plugins"]])
        if result["alive_plugins"]:
            print(f"  HEARTBEAT OK ({result['total_alive']} plugins):")
            for entry in result["alive_plugins"]:
                # entry keys are the plugin names as top-level keys in alive.json
                pass
        # Iterate over raw alive.json entries instead
        alive_raw = _read_json(ALIVE_PATH) or {}
        for name, data in sorted(alive_raw.items()):
            last_seen = data.get("last_seen", 0) if isinstance(data, dict) else 0
            age = result["ts_epoch"] - (last_seen / 1000.0) if last_seen > 1e11 else result["ts_epoch"] - last_seen
            status = "OK  " if age <= MAX_AGE_SECS else "STALE"
            ts_iso = _ts_to_iso(last_seen) if last_seen else "unknown"
            print(f"  [{status}] {name:40s} last seen: {ts_iso} ({age:.0f}s ago)")

    print()

    # ── 2. Hook fires ──
    print(f"--- Hook Fires ({result['total_hook_fires']} entries) ---")
    if HOOK_FIRES_PATH.exists():
        print(f"  File: {HOOK_FIRES_PATH} ({HOOK_FIRES_PATH.stat().st_size} bytes)")
        for entry in result["hook_fires"][:20]:
            print(f"  [{entry['age_secs']:.0f}s ago] {entry['plugin']}.{entry['hook']}")
        if result["total_hook_fires"] > 20:
            print(f"  ... and {result['total_hook_fires'] - 20} more")
    else:
        print("  No hook-fires log found (/tmp/gludd-hook-fires.jsonl).")
        print("  Hook-fire logging is optional per-plugin; presence here is best-effort.")

    print()

    # ── 3. Enforcement state ──
    print("--- Enforcement State ---")
    e = result["enforcement"]
    print(f"  disengaged:            {'YES' if e['disengaged'] else 'NO'}{' (until ' + e['disengage_until_iso'] + ')' if e['disengaged'] else ''}")
    print(f"  floor-override:        {e['floor_override'] if e['floor_override'] is not None else '(none)'}")
    print(f"  tool-streak:           {e['streak'] if e['streak'] is not None else '(none)'}")
    print(f"  session-start:         {'active' if e['session_start_active'] else 'inactive'}")
    print(f"  task-deadlines:        {'active' if e['task_deadlines_active'] else 'inactive'}")
    cooldown_secs = e.get("ci_check_cooldown_remaining_secs")
    if cooldown_secs is not None and cooldown_secs > 0:
        print(f"  ci-cooldown:           {cooldown_secs:.0f}s remaining")
    else:
        print(f"  ci-cooldown:           clear")
    print()

    # ── Summary ──
    issues = 0
    if result["total_loaded_plugins"] == 0:
        issues += 1
    if result["total_alive"] == 0 and result["total_loaded_plugins"] > 0:
        issues += 1
    for s in os.environ.get("GLUDD_HEALTH_WARN_STALE", "").split(",") if os.environ.get("GLUDD_HEALTH_WARN_STALE") else []:
        pass

    print("--- Summary ---")
    print(f"  Plugins alive: {result['total_alive']}/{result['total_loaded_plugins']}")
    print(f"  Hook-fire entries: {result['total_hook_fires']}")
    print(f"  Enforcement engaged: {'NO (disengaged)' if e['disengaged'] else 'YES'}")
    print()

    exit_code = 0
    if result["total_loaded_plugins"] == 0:
        print("NOTE: No plugin alive data — this is normal for a fresh session.")
    if result["total_alive"] == 0 and result["total_loaded_plugins"] > 0:
        print("WARNING: Plugins loaded but all heartbeats are stale — enforcement may be dead.")
        exit_code = 1

    print(f"Result written to: {RESULT_PATH}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
