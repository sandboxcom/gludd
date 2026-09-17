"""Debounced validation lane for coherent pipeline snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from general_ludd.pipeline.state import LaneState, PipelineConfig

logger = logging.getLogger(__name__)

GateFn = Callable[[], Awaitable[bool]]


class GateLane:
    """Run at most one debounced gate over a stable merged-work snapshot."""

    def __init__(
        self,
        config: PipelineConfig,
        state: LaneState,
        lock: asyncio.Lock,
        gate_fn: GateFn,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Configure shared state, synchronization, gate callback, and clock."""
        self._config = config
        self._state = state
        self._lock = lock
        self._gate_fn = gate_fn
        self._clock = clock
        self._in_flight = False
        self._stopped = False

    def _due(self) -> bool:
        if self._in_flight:
            return False
        if not self._state.merged_awaiting_gate:
            return False
        elapsed = self._clock() - self._state.last_gate_epoch
        return elapsed >= self._config.gate_debounce_s

    async def step(self) -> bool | None:
        """Run one gate when due, returning its verdict or ``None`` when idle."""
        async with self._lock:
            if not self._due():
                return None
            covered = list(self._state.merged_awaiting_gate)
            self._state.last_gate_epoch = self._clock()
            self._state.total_gates_run += 1
            self._in_flight = True

        try:
            green = await self._gate_fn()
        except Exception as exc:
            logger.error("GateLane: gate run raised: %s", exc)
            green = False
        finally:
            async with self._lock:
                self._in_flight = False

        async with self._lock:
            if green:
                self._state.total_gates_green += 1
                covered_set = set(covered)
                self._state.merged_awaiting_gate = [
                    unit_id
                    for unit_id in self._state.merged_awaiting_gate
                    if unit_id not in covered_set
                ]
                logger.info(
                    "GateLane: GREEN — committed snapshot covering %d unit(s)",
                    len(covered),
                )
            else:
                logger.warning(
                    "GateLane: RED — snapshot of %d unit(s) NOT committed; "
                    "will re-gate after debounce",
                    len(covered),
                )
        return green

    async def run(self) -> None:
        """Reconcile gates until cancellation or an explicit stop request."""
        self._stopped = False
        while not self._stopped:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - loop guard
                logger.error("GateLane loop error: %s", exc)
            await asyncio.sleep(self._config.gate_poll_interval_s)

    def stop(self) -> None:
        """Request a graceful stop after the current iteration."""
        self._stopped = True


__all__ = ("GateFn", "GateLane")
