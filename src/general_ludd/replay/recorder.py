"""G10 Per-run replay — record and replay agent runs for audit/debug."""

from __future__ import annotations

import json
from typing import Any

from general_ludd.filestore.store import FileStore


class RunRecorder:
    """Records agent run events and replays them for audit or debugging.

    A "run" is a single agent turn: the prompt, the model call, the response,
    and the tool calls made. Recording captures the full context; replay
    reconstructs it — useful for post-mortem analysis, regression testing,
    and reproducing bugs deterministically.
    """

    def __init__(self, store: FileStore | None = None) -> None:
        self._store = store if store is not None else FileStore(root_path=".gludd/replays")

    def record(self, run_id: str, event: dict[str, Any]) -> None:
        events_dir = f"runs/{run_id}/events"
        seq = self._next_seq(events_dir)
        path = f"{events_dir}/{seq}.json"
        self._store.write_text(path, json.dumps(event))

    def replay(self, run_id: str) -> list[dict[str, Any]]:
        events_dir = f"runs/{run_id}/events"
        if not self._store.exists(events_dir):
            return []
        entries = self._store.list_dir(events_dir)
        events: list[dict[str, Any]] = []
        for entry in sorted(entries, key=lambda e: int(e["name"].removesuffix(".json"))):
            if entry["is_dir"]:
                continue
            path = f"{events_dir}/{entry['name']}"
            events.append(json.loads(self._store.read_text(path)))
        return events

    def list_runs(self) -> list[str]:
        runs_dir = "runs"
        if not self._store.exists(runs_dir):
            return []
        entries = self._store.list_dir(runs_dir)
        return sorted(e["name"] for e in entries if e["is_dir"])

    def _next_seq(self, events_dir: str) -> int:
        if not self._store.exists(events_dir):
            return 0
        entries = self._store.list_dir(events_dir)
        max_seq = -1
        for e in entries:
            if e["is_dir"]:
                continue
            try:
                num = int(e["name"].removesuffix(".json"))
                if num > max_seq:
                    max_seq = num
            except ValueError:
                continue
        return max_seq + 1
