#!/usr/bin/env python3
"""Force-push tracker: limits consecutive GLUDD_FORCE_PUSH bypasses.

Commands:
    check-bypass   Exit 0 if bypass is allowed; exit 1 with message if blocked.
    record-bypass  Record a force-push bypass (called after check-bypass passes).
    record-normal  Reset the bypass counter (called on normal, non-force push).

State is held at .gate-logs/force-push-track.json.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_FILE = ".gate-logs/force-push-track.json"
DEFAULT_MAX_CONSECUTIVE = int(os.environ.get("GLUDD_FORCE_PUSH_MAX_BYPASS", "5"))
DEFAULT_WINDOW_HOURS = float(os.environ.get("GLUDD_FORCE_PUSH_WINDOW_HOURS", "12"))


def _state_path() -> Path:
    return Path(os.environ.get("GLUDD_FORCE_PUSH_TRACK_FILE", DEFAULT_STATE_FILE))


def _load() -> dict:
    sp = _state_path()
    if sp.exists():
        return json.loads(sp.read_text())
    return {
        "bypass_times": [],
        "last_normal_push": None,
        "max_consecutive_bypasses": DEFAULT_MAX_CONSECUTIVE,
        "window_hours": DEFAULT_WINDOW_HOURS,
    }


def _save(state: dict) -> None:
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state))


class ForcePushTracker:
    def __init__(
        self,
        state_file: Optional[Path] = None,
        max_bypasses: int = DEFAULT_MAX_CONSECUTIVE,
        max_consecutive: Optional[int] = None,
        window_hours: float = DEFAULT_WINDOW_HOURS,
    ):
        self._state_file = state_file
        self.max_bypasses = max_consecutive if max_consecutive is not None else max_bypasses
        self.window_hours = window_hours

    @property
    def count(self) -> int:
        return self._count_recent()

    def is_bypass_allowed(self) -> bool:
        return self._count_recent() < self.max_bypasses

    def record_bypass(self) -> None:
        state = self._load_state()
        state.setdefault("bypass_times", []).append(time.time())
        self._save_state(state)

    def record_normal_push(self) -> None:
        state = self._load_state()
        state["bypass_times"] = []
        state["last_normal_push"] = time.time()
        self._save_state(state)

    def _purge_stale(self) -> None:
        """Remove bypass entries older than the window."""
        state = self._load_state()
        now = time.time()
        cutoff = now - (self.window_hours * 3600)
        times = state.get("bypass_times", [])
        recent = [t for t in times if t > cutoff]
        if len(recent) != len(times):
            state["bypass_times"] = recent
            self._save_state(state)

    def _state_path(self) -> Path:
        if self._state_file:
            return Path(self._state_file)
        return _state_path()

    def _load_state(self) -> dict:
        sp = self._state_path()
        if sp.exists():
            return json.loads(sp.read_text())
        return {
            "bypass_times": [],
            "last_normal_push": None,
            "max_consecutive_bypasses": self.max_bypasses,
            "window_hours": self.window_hours,
        }

    def _save_state(self, state: dict) -> None:
        sp = self._state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(state))

    def _count_recent(self) -> int:
        self._purge_stale()
        state = self._load_state()
        return len(state.get("bypass_times", []))


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <check-bypass|record-bypass|record-normal>", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]
    tracker = ForcePushTracker()

    if cmd == "check-bypass":
        if tracker.is_bypass_allowed():
            sys.exit(0)
        count = tracker._count_recent()
        print(
            f"FORCE-PUSH BLOCKED: {count} consecutive force-pushes within "
            f"{tracker.window_hours}h window. Use normal push or wait."
        )
        sys.exit(1)

    elif cmd == "record-bypass":
        tracker.record_bypass()
        sys.exit(0)

    elif cmd == "record-normal":
        tracker.record_normal_push()
        sys.exit(0)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
