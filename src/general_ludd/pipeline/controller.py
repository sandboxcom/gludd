"""PipelineController — owns the three lanes as concurrent asyncio tasks (#77).

Responsibilities:

  * ``start()`` — launch the three lane loops (dispatch / integrate / gate) plus
    a heartbeat loop as supervised asyncio tasks.
  * ``stop()``  — signal every lane to stop, cancel the tasks, and await them.
  * ``status()``— a point-in-time snapshot of the shared :class:`LaneState`
    (running / pending / awaiting-merge / awaiting-gate / counters), used by the
    daemon's ``/api/status`` surface.
  * Back-pressure — exposed via :meth:`backpressured`; the DispatchLane consults
    the same predicate so the controller never plants more worktrees than the
    ``max_worktrees`` ceiling (or disk pressure) allows.
  * Heartbeat — emits a periodic snapshot through an injected sink so NO lane is
    ever a silent black box (the observability invariant: "unseen events aren't
    events").

The controller is the ONLY component that creates the shared lock and state, so
the lanes' mutations are serialized while their long-running work stays off the
lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from general_ludd.pipeline.lanes import (
    DispatchFn,
    DispatchLane,
    GateFn,
    GateLane,
    IntegrateLane,
    MergeFn,
    PidProvider,
)
from general_ludd.pipeline.state import (
    Heartbeat,
    LaneState,
    PipelineConfig,
)

logger = logging.getLogger(__name__)

__all__ = ["PipelineController"]

HeartbeatSink = Callable[[Heartbeat], None]


class PipelineController:
    """Own and supervise the three concurrent pipeline lanes."""

    def __init__(
        self,
        config: PipelineConfig,
        dispatch_fn: DispatchFn,
        merge_fn: MergeFn,
        gate_fn: GateFn,
        *,
        state: LaneState | None = None,
        disk_ok: Callable[[], bool] | None = None,
        heartbeat_sink: HeartbeatSink | None = None,
        clock: Callable[[], float] = time.time,
        pid_provider: PidProvider | None = None,
        pid_group: str | None = None,
    ) -> None:
        self._config = config
        self._state = state or LaneState()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._disk_ok = disk_ok or (lambda: True)
        self._heartbeat_sink = heartbeat_sink or self._default_heartbeat_sink

        self._dispatch_lane = DispatchLane(
            config, self._state, self._lock, dispatch_fn, disk_ok=self._disk_ok,
            pid_provider=pid_provider, pid_group=pid_group,
        )
        self._integrate_lane = IntegrateLane(
            config, self._state, self._lock, merge_fn,
        )
        self._gate_lane = GateLane(
            config, self._state, self._lock, gate_fn, clock=clock,
        )
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False

    # --- backlog feed ---------------------------------------------------- #

    async def submit(self, unit_ids: Iterable[str]) -> int:
        """Append backlog unit ids for the DispatchLane to pull. Returns count."""
        added = 0
        async with self._lock:
            for uid in unit_ids:
                self._state.pending.append(uid)
                added += 1
        return added

    async def report_completed(self, unit: Any) -> None:
        """Enqueue a completed unit's worktree for the IntegrateLane to merge."""
        async with self._lock:
            self._state.completed_awaiting_merge.append(unit)

    # --- back-pressure --------------------------------------------------- #

    def backpressured(self) -> bool:
        """True iff the worktree ceiling or disk pressure currently blocks dispatch."""
        return self._dispatch_lane.backpressured()

    # --- lifecycle ------------------------------------------------------- #

    async def start(self) -> None:
        """Launch the three lane loops + heartbeat as supervised tasks."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._dispatch_lane.run(), name="pipeline-dispatch"),
            asyncio.create_task(self._integrate_lane.run(), name="pipeline-integrate"),
            asyncio.create_task(self._gate_lane.run(), name="pipeline-gate"),
            asyncio.create_task(self._heartbeat_loop(), name="pipeline-heartbeat"),
        ]
        for task in self._tasks:
            task.add_done_callback(self._on_task_done)
        logger.info(
            "PipelineController started: floor=%d target=%d max_worktrees=%d "
            "gate_debounce=%.0fs",
            self._config.floor, self._config.target,
            self._config.max_worktrees, self._config.gate_debounce_s,
        )

    async def stop(self) -> None:
        """Signal lanes to stop, cancel their tasks, and await them."""
        if not self._running:
            return
        self._running = False
        self._dispatch_lane.stop()
        self._integrate_lane.stop()
        self._gate_lane.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with _suppress_cancel():
                await task
        self._tasks = []
        logger.info("PipelineController stopped")

    @staticmethod
    def _on_task_done(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Pipeline lane task %s terminated: %s", task.get_name(), exc)

    # --- observability --------------------------------------------------- #

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await self.emit_heartbeat()
            await asyncio.sleep(self._config.heartbeat_interval_s)

    async def emit_heartbeat(self) -> Heartbeat:
        async with self._lock:
            hb = self._state.snapshot_heartbeat(backpressure=self.backpressured())
        try:
            self._heartbeat_sink(hb)
        except Exception as exc:  # pragma: no cover - sink must never kill the loop
            logger.error("Pipeline heartbeat sink raised: %s", exc)
        return hb

    @staticmethod
    def _default_heartbeat_sink(hb: Heartbeat) -> None:
        logger.info(
            "[pipeline heartbeat] running=%d pending=%d awaiting_merge=%d "
            "awaiting_gate=%d backpressure=%s last_gate_epoch=%.0f",
            hb.running, hb.pending, hb.awaiting_merge, hb.awaiting_gate,
            hb.backpressure, hb.last_gate_epoch,
        )

    async def status(self) -> dict[str, Any]:
        """Point-in-time snapshot of the pipeline for the daemon status surface."""
        async with self._lock:
            s = self._state
            return {
                "enabled": self._config.enabled,
                "running": sorted(s.running),
                "pending": list(s.pending),
                "awaiting_merge": [u.unit_id for u in s.completed_awaiting_merge],
                "awaiting_gate": list(s.merged_awaiting_gate),
                "worktree_count": s.worktree_count(),
                "backpressure": self.backpressured(),
                "last_gate_epoch": s.last_gate_epoch,
                "counters": {
                    "dispatched": s.total_dispatched,
                    "merged": s.total_merged,
                    "clobbers_refused": s.total_clobbers_refused,
                    "gates_run": s.total_gates_run,
                    "gates_green": s.total_gates_green,
                },
                "config": {
                    "floor": self._config.floor,
                    "target": self._config.target,
                    "max_worktrees": self._config.max_worktrees,
                    "gate_debounce_s": self._config.gate_debounce_s,
                },
                # Live concurrency the PID/load controller is requesting right
                # now (the static config.target when no PID provider is wired).
                "desired_target": self._dispatch_lane.desired_target(),
            }


class _suppress_cancel:
    """Async-aware suppressor for CancelledError when awaiting a cancelled task."""

    def __enter__(self) -> _suppress_cancel:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)
