"""Shared gate-observability data types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateEvent:
    """A single gate segment such as a phase or a batch."""

    kind: str
    label: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Heartbeat:
    """A recorded wall-clock elapsed sample inside a gate log."""

    line: int
    elapsed: float


@dataclass
class GateRun:
    """Parsed representation of one gate log."""

    path: Path
    events: list[GateEvent]
    total_lines: int
    start_time: float
    end_time: float | None
    heartbeats: list[Heartbeat]
    shards: list[tuple[str, int]]
    lines: list[str]
    terminal: str | None
    resume_batches: int = 0

    @property
    def duration(self) -> float:
        """Return the wall-clock duration in seconds, or 0 if still running."""
        if self.heartbeats:
            return max(hb.elapsed for hb in self.heartbeats)
        end = self.end_time or self.start_time
        return max(0.0, end - self.start_time)


@dataclass
class ProgressReport:
    """Human-readable progress/ETA summary."""

    current_label: str | None
    elapsed_seconds: float
    estimated_total_seconds: float
    estimated_remaining_seconds: float
    percent_complete: float
    completed_segments: int
    total_segments: int
    remaining_known: bool = True
