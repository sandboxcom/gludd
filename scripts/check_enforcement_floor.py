#!/usr/bin/env python3
"""Diagnose 10-agent floor enforcement state. Read-only, exit 0 on clean."""

import json
import os
import sys
from pathlib import Path

_STATE_FILES = {
    "multitask": "/tmp/gludd-multitask-state.json",
    "streak": "/tmp/gludd-tool-streak.json",
    "read_grind": "/tmp/gludd-read-grind.json",
    "session_start": "/tmp/gludd-session-start.json",
    "deadlines": "/tmp/gludd-task-deadlines.json",
    "enhancement": "/tmp/gludd-enhancement-ratio.json",
}
_OVERRIDE_FILE = "/tmp/gludd-floor-override"


def _read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _check_env() -> dict[str, bool]:
    # Canonical truthy pattern: enabled when the value lower-cases to one of
    # the accepted truthy tokens (matches the plugin-side env checks).
    return {
        "GLUDD_MULTITASK_FLOOR_ENFORCE": os.environ.get("GLUDD_MULTITASK_FLOOR_ENFORCE", "1").lower().strip()
        in {"1", "true", "yes", "on"},
        "GLUDD_FLOOR_ENFORCE": os.environ.get("GLUDD_FLOOR_ENFORCE", "1").lower().strip() in {"1", "true", "yes", "on"},
        "GLUDD_MAINTHREAD_STREAK_ENFORCE": os.environ.get("GLUDD_MAINTHREAD_STREAK_ENFORCE", "1").lower().strip()
        in {"1", "true", "yes", "on"},
        "GLUDD_SESSION_START_ENFORCE": os.environ.get("GLUDD_SESSION_START_ENFORCE", "1").lower().strip()
        in {"1", "true", "yes", "on"},
        "GLUDD_ENHANCEMENT_RATIO_ENFORCE": os.environ.get("GLUDD_ENHANCEMENT_RATIO_ENFORCE", "1").lower().strip()
        in {"1", "true", "yes", "on"},
    }


def main() -> int:
    violations: list[str] = []
    floor = 10
    try:
        with open(_OVERRIDE_FILE) as f:
            floor = int(f.read().strip())
    except Exception:
        pass

    env = _check_env()

    print("=" * 60)
    print("ENFORCEMENT FLOOR CHECK")
    print("=" * 60)

    print(f"\nFloor override: {floor}")
    for name, enabled in env.items():
        status = "ON" if enabled else "OFF"
        print(f"  {name}: {status}")
        if not enabled:
            violations.append(f"ENV {name} is OFF")

    for label, path in _STATE_FILES.items():
        data = _read_json(path)
        if data is None:
            print(f"\n{label}: no state file")
            continue

        print(f"\n{label} ({path}):")
        for k, v in sorted(data.items()):
            if isinstance(v, list) and len(str(v)) > 60:
                print(f"  {k}: [...] ({len(v)} items)")
            else:
                print(f"  {k}: {v}")

        _check_multitask_state(label, data, floor, violations)

    if violations:
        print("\n" + "=" * 60)
        print(f"VIOLATIONS FOUND ({len(violations)}):")
        for v in violations:
            print(f"  - {v}")
        print("=" * 60)
        return 1

    print("\n" + "=" * 60)
    print("ENFORCEMENT FLOOR: CLEAN")
    print("=" * 60)
    return 0


def _check_multitask_state(label: str, data: dict, floor: int, violations: list[str]) -> None:
    if label != "multitask":
        return

    under_floor = data.get("underFloorCount", 0)
    zero_streak = data.get("zeroStreak", 0)
    prev_disp = data.get("prevMessageDispatches", 0)
    this_disp = data.get("thisMessageDispatches", 0)
    consecutive = data.get("consecutiveNonDispatch", 0)
    session_total = data.get("sessionDispatchTotal", 0)
    streak = data.get("streak", 0) if label == "streak" else 0

    if under_floor >= 3:
        violations.append(
            f"MULTITASK underFloorCount={under_floor} — {under_floor} consecutive "
            f"under-floor waves; 10-agent floor requirement violated"
        )

    if zero_streak >= 2:
        violations.append(f"MULTITASK zeroStreak={zero_streak} — {zero_streak} consecutive zero-dispatch messages")

    if prev_disp > 0 and prev_disp < floor:
        violations.append(f"MULTITASK prevMessageDispatches={prev_disp} — last wave was below floor {floor}")

    if this_disp > 0 and this_disp < floor:
        violations.append(f"MULTITASK thisMessageDispatches={this_disp} — current wave is below floor {floor}")

    if session_total > 0:
        thin_pct = (under_floor / max(session_total, 1)) * 100
        if thin_pct > 50:
            violations.append(
                f"MULTITASK underFloorCount={under_floor} with "
                f"sessionDispatchTotal={session_total} — "
                f"{thin_pct:.0f}% of messages are under floor"
            )


if __name__ == "__main__":
    sys.exit(main())
