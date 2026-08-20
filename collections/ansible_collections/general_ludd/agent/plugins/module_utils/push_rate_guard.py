"""Collection-local force-push rate state with atomic adjacent promotion."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any


class ForcePushTracker:
    """Track recent force-push bypasses without a checkout dependency."""

    def __init__(
        self,
        state_file: Path,
        max_bypasses: int = 5,
        window_hours: float = 12.0,
    ) -> None:
        if max_bypasses < 1 or window_hours <= 0:
            raise ValueError("push guard limits must be positive")
        self._state_file = state_file
        self.max_bypasses = max_bypasses
        self.window_hours = window_hours

    def _load_state(self) -> dict[str, Any]:
        if not self._state_file.exists():
            return {"bypass_times": [], "last_normal_push": None}
        data = json.loads(self._state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("bypass_times"), list):
            raise ValueError("invalid force-push state")
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._state_file.name}.",
            dir=self._state_file.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_file)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise

    def _recent(self) -> tuple[dict[str, Any], list[float]]:
        state = self._load_state()
        cutoff = time.time() - self.window_hours * 3600
        times = [float(value) for value in state.get("bypass_times", []) if float(value) > cutoff]
        if times != state.get("bypass_times"):
            state["bypass_times"] = times
            self._save_state(state)
        return state, times

    @property
    def count(self) -> int:
        return len(self._recent()[1])

    def is_bypass_allowed(self) -> bool:
        return self.count < self.max_bypasses

    def record_bypass(self) -> None:
        state, times = self._recent()
        state["bypass_times"] = [*times, time.time()]
        self._save_state(state)

    def record_normal_push(self) -> None:
        state, _ = self._recent()
        state["bypass_times"] = []
        state["last_normal_push"] = time.time()
        self._save_state(state)
