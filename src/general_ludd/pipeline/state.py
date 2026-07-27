"""Pipeline lane state + configuration dataclasses (#77).

These types are pure data carriers shared by the three lanes and the
controller. They hold NO behaviour and NO I/O, so they are trivially
constructible in unit tests and never drift from the lanes that mutate them.

The single :class:`LaneState` is the shared, authoritative view of the
pipeline that all three lanes reconcile against each tick:

  * ``running``                 — ids of work units currently dispatched.
  * ``pending``                 — ordered backlog of work-unit ids not yet run.
  * ``completed_awaiting_merge``— completed units whose worktrees still need
    to be merged into the repo by the IntegrateLane.
  * ``merged_awaiting_gate``    — units merged into the working tree but not
    yet covered by a green gate run.
  * ``last_gate_epoch``         — wall-clock epoch (seconds) of the last gate
    run START, used by the GateLane to debounce.

Following the SaturationController's "reconcile, never drift" principle, the
controller treats ``len(running)`` as the authoritative running count each tick
rather than keeping a separate counter.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

__all__ = [
    "CompletedUnit",
    "Heartbeat",
    "LaneState",
    "MergeOutcome",
    "PipelineConfig",
]


@dataclass(frozen=True)
class PipelineConfig:
    """Static configuration for the 3-lane pipeline.

    Attributes:
        enabled: Master flag. When False the daemon never starts the pipeline.
        floor: The pipeline must never let the number of running agents drop
            below this while backlog remains (keep-the-floor-busy guarantee).
        target: Desired concurrency — the DispatchLane backfills empty slots
            up to this many running agents.
        gate_debounce_s: Minimum seconds between two gate runs. A burst of
            merges coalesces into a single gate run rather than one-per-merge.
        max_worktrees: Hard ceiling on concurrent worktrees. When
            ``running + completed_awaiting_merge`` reaches this, the
            DispatchLane stops dispatching (back-pressure) until the
            IntegrateLane drains and reclaims worktrees.
        dispatch_interval_s / integrate_interval_s / gate_poll_interval_s:
            Per-lane loop sleep so an idle lane yields the event loop instead
            of spinning.
        heartbeat_interval_s: How often the controller emits a heartbeat.
    """

    enabled: bool = False
    floor: int = 1
    target: int = 3
    gate_debounce_s: float = 30.0
    max_worktrees: int = 6
    dispatch_interval_s: float = 0.5
    integrate_interval_s: float = 0.5
    gate_poll_interval_s: float = 0.5
    heartbeat_interval_s: float = 5.0

    def __post_init__(self) -> None:
        if self.floor < 0:
            raise ValueError("floor must be >= 0")
        if self.target < self.floor:
            raise ValueError("target must be >= floor")
        if self.max_worktrees < self.target:
            raise ValueError("max_worktrees must be >= target")
        if self.gate_debounce_s < 0:
            raise ValueError("gate_debounce_s must be >= 0")


@dataclass
class CompletedUnit:
    """A completed work unit awaiting merge into the repo.

    Attributes:
        unit_id: The work-unit / todo id (matches the dispatched id).
        worktree_path: Absolute path to the agent's worktree to merge + reclaim.
        branch: Optional branch name produced by the agent.
    """

    unit_id: str
    worktree_path: str
    branch: str | None = None
    base_sha: str | None = None  # S4: fork-point SHA for 3-way merge base


@dataclass(frozen=True)
class MergeOutcome:
    """Result of an IntegrateLane merge attempt for one completed unit.

    Attributes:
        unit_id: The unit that was merged (or refused).
        merged: True iff the merge applied cleanly and the worktree was
            reclaimed. False on a refused clobber/conflict.
        clobber_refused: True iff the merge was refused because applying it
            would have clobbered a divergent base change (conflict). The
            worktree is NOT reclaimed in this case so the work is not lost.
        detail: Human-readable provenance (mirrors ``MergeResult.source``).
    """

    unit_id: str
    merged: bool
    clobber_refused: bool = False
    detail: str = ""


@dataclass
class Heartbeat:
    """A single observability heartbeat snapshot emitted by the controller."""

    epoch: float
    running: int
    pending: int
    awaiting_merge: int
    awaiting_gate: int
    last_gate_epoch: float
    backpressure: bool


@dataclass
class LaneState:
    """Shared, authoritative pipeline state reconciled by all three lanes.

    The lanes mutate this under the controller's lock; it is the single source
    of truth for ``status()`` and the heartbeat.
    """

    running: set[str] = field(default_factory=set)
    pending: deque[str] = field(default_factory=deque)
    completed_awaiting_merge: deque[CompletedUnit] = field(default_factory=deque)
    merged_awaiting_gate: list[str] = field(default_factory=list)
    last_gate_epoch: float = 0.0
    # Monotonic counters for observability (never reset).
    total_dispatched: int = 0
    total_merged: int = 0
    total_clobbers_refused: int = 0
    total_gates_run: int = 0
    total_gates_green: int = 0

    def worktree_count(self) -> int:
        """Worktrees currently consuming disk: running + awaiting-merge.

        This is the quantity the ``max_worktrees`` back-pressure ceiling caps.
        A unit only stops consuming a worktree once the IntegrateLane has
        merged it and reclaimed the directory.
        """
        return len(self.running) + len(self.completed_awaiting_merge)

    def snapshot_heartbeat(self, *, backpressure: bool) -> Heartbeat:
        return Heartbeat(
            epoch=time.time(),
            running=len(self.running),
            pending=len(self.pending),
            awaiting_merge=len(self.completed_awaiting_merge),
            awaiting_gate=len(self.merged_awaiting_gate),
            last_gate_epoch=self.last_gate_epoch,
            backpressure=backpressure,
        )
