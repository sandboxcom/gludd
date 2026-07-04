"""G10 Per-run replay — record and replay agent runs for audit/debug."""

from __future__ import annotations

from typing import Any


class RunRecorder:
    """Records agent run events and replays them for audit or debugging.

    A "run" is a single agent turn: the prompt, the model call, the response,
    and the tool calls made. Recording captures the full context; replay
    reconstructs it — useful for post-mortem analysis, regression testing,
    and reproducing bugs deterministically.
    """

    def __init__(self, storage_path: str | None = None) -> None:
        self._storage_path = storage_path
        self._runs: dict[str, list[dict[str, Any]]] = {}

    def record(self, run_id: str, event: dict[str, Any]) -> None:
        """Record an *event* for the run identified by *run_id*.

        Events are appended in order and can be replayed later.
        """

    def replay(self, run_id: str) -> list[dict[str, Any]]:
        """Replay the recorded events for *run_id*.

        Returns the list of events in the order they were recorded,
        or an empty list if no events are recorded for that run.
        """
        return []
