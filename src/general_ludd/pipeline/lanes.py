"""The three concurrent pipeline lanes (#77).

Each lane is an independent async loop that NEVER blocks the others: a slow
gate run does not pause dispatch or merge, a contended repo lock in the merge
lane does not pause dispatch, etc. Every lane exposes:

  * ``step()``  — perform exactly ONE reconcile iteration. Pure-ish and
    directly unit-testable with mocked dependencies (no sleeps, no loops).
  * ``run()``   — the supervised loop: ``while not stopped: await step();
    sleep(interval)``. Used by the controller's asyncio tasks.

The lanes share a single :class:`~general_ludd.pipeline.state.LaneState` and a
single ``asyncio.Lock`` (owned by the controller) so their mutations of the
shared backlog / running set / merge queue are serialized — but the WORK each
lane performs (dispatching an agent, merging a worktree, running the gate) is
done OUTSIDE the lock so a long operation in one lane never stalls another.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from general_ludd.controllers.saturation import SaturationController
from general_ludd.pipeline.state import (
    CompletedUnit,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)
from general_ludd.scheduling.scheduler import WorkItem

if TYPE_CHECKING:
    from general_ludd.controllers.pid import ControllerOutputs

logger = logging.getLogger(__name__)

__all__ = ["DispatchLane", "GateLane", "IntegrateLane", "PidProvider"]

# Injected behaviours. Tests pass mocks/fakes; the daemon passes real adapters.
#
# DispatchFn(unit_id) -> awaitable: launch a role-agent on this backlog unit.
#   It should NOT block to completion — it kicks off the work; the lane learns
#   of completion separately (the agent reports a CompletedUnit, enqueued by the
#   daemon onto state.completed_awaiting_merge). The return value is ignored.
DispatchFn = Callable[[str], Awaitable[object]]

# MergeFn(CompletedUnit) -> awaitable[MergeOutcome]: merge this completed unit's
#   worktree into the repo (the daemon adapter wraps safe_merge under
#   git_repo_lock and reclaims the worktree on success / refuses on clobber).
MergeFn = Callable[[CompletedUnit], Awaitable[MergeOutcome]]

# GateFn() -> awaitable[bool]: run the full gate/validation on the current
#   coherent snapshot; return True iff green. The daemon adapter commits on
#   green.
GateFn = Callable[[], Awaitable[bool]]

# PidProvider() -> ControllerOutputs | None: the live load/PID controller
#   evaluation (gludd's existing LoadController), supplying the DESIRED active
#   concurrency for the keep-N-busy loop. Returning None means "no PID signal
#   right now" and the lane falls back to the static config target. Injected as
#   a plain callable so the lane is unit-testable WITHOUT running the event loop
#   — tests pass a lambda yielding a hand-built ControllerOutputs; the daemon
#   passes a provider that evaluates the real LoadController against a live
#   load snapshot. The return is duck-typed on ``desired_total_active_buckets``
#   and ``desired_active_buckets_by_queue`` so importing psutil is not forced.
PidProvider = Callable[[], "ControllerOutputs | None"]


class DispatchLane:
    """Keep N role-agents running on disjoint backlog units, where N is driven
    by gludd's load/PID controller (not a frozen static target).

    Reconciles every ``step()`` from the authoritative running count
    (``len(state.running)``) using the :class:`SaturationController`, never
    keeping its own drifting counter. The desired concurrency N each tick is
    taken from the injected :data:`PidProvider` (the existing ``LoadController``
    ``ControllerOutputs``): when a ``pid_group`` is configured the lane uses
    that group's ``desired_active_buckets_by_queue`` entry, otherwise the
    aggregate ``desired_total_active_buckets``. When no PID provider is wired
    (or it yields ``None``) the lane falls back to the static ``config.target``
    so the historical behaviour is preserved. The ``floor`` is ALWAYS a hard
    lower bound (the PID target is clamped up to it) and the ``max_worktrees``
    back-pressure ceiling always caps the upper bound, so the lane never plants
    more worktrees than the IntegrateLane can drain.
    """

    def __init__(
        self,
        config: PipelineConfig,
        state: LaneState,
        lock: asyncio.Lock,
        dispatch_fn: DispatchFn,
        *,
        saturation: SaturationController | None = None,
        disk_ok: Callable[[], bool] | None = None,
        pid_provider: PidProvider | None = None,
        pid_group: str | None = None,
    ) -> None:
        self._config = config
        self._state = state
        self._lock = lock
        self._dispatch_fn = dispatch_fn
        self._saturation = saturation or SaturationController()
        # disk_ok() -> False signals disk pressure: dispatch holds (#62 safety).
        self._disk_ok = disk_ok or (lambda: True)
        # PID-driven target source (gludd's LoadController). None => static.
        self._pid_provider = pid_provider
        self._pid_group = pid_group
        self._stopped = False

    def backpressured(self) -> bool:
        """True iff the worktree ceiling or disk pressure blocks dispatch."""
        if not self._disk_ok():
            return True
        return self._state.worktree_count() >= self._config.max_worktrees

    def desired_target(self) -> int:
        """Public, side-effect-free view of the current PID-driven target.

        Exposed so the controller's status/heartbeat surface can show the LIVE
        desired concurrency the load/PID controller is asking for this moment
        (observability invariant: the PID-driven number must not be a silent
        black box), distinct from the static ``config.target`` fallback.
        """
        return self._desired_target()

    def _desired_target(self) -> int:
        """Resolve this tick's desired active-agent count from the PID controller.

        Consults the injected :data:`PidProvider` (gludd's existing
        ``LoadController`` ``ControllerOutputs``). When a ``pid_group`` is set
        and that group appears in ``desired_active_buckets_by_queue`` the lane
        targets that group's bucket count (honouring per-queue throttling);
        otherwise it targets the aggregate ``desired_total_active_buckets``.

        Falls back to the static ``config.target`` when no provider is wired or
        the provider yields ``None`` (no live signal) — preserving the
        historical static behaviour. The result is ALWAYS clamped up to
        ``config.floor`` (the floor is a hard lower bound) so a load-induced
        throttle can never starve the keep-the-floor-busy guarantee.
        """
        cfg = self._config
        target = cfg.target
        if self._pid_provider is not None:
            outputs = self._pid_provider()
            if outputs is not None:
                by_queue = getattr(outputs, "desired_active_buckets_by_queue", {})
                if self._pid_group is not None and self._pid_group in by_queue:
                    target = int(by_queue[self._pid_group])
                else:
                    target = int(getattr(
                        outputs, "desired_total_active_buckets", cfg.target,
                    ))
        # Floor is a hard lower bound regardless of PID throttling.
        return max(target, cfg.floor)

    def _plan(self) -> list[str]:
        """Compute which backlog unit ids to dispatch this tick (under lock).

        Returns the in-order ids to launch, having already removed them from
        ``pending`` and added them to ``running`` so a concurrent step cannot
        double-dispatch the same unit.
        """
        cfg = self._config
        if not self._state.pending:
            return []
        if self.backpressured():
            return []

        running = len(self._state.running)
        # Free worktree slots so we never exceed the ceiling even mid-backfill.
        worktree_headroom = max(0, cfg.max_worktrees - self._state.worktree_count())

        # Desired concurrency N comes from gludd's PID/load controller this
        # tick (with the static target as the fallback), NOT a frozen target.
        desired_target = self._desired_target()

        backlog = [
            WorkItem(id=uid, resources=frozenset({f"unit:{uid}"}))
            for uid in self._state.pending
        ]
        chosen = self._saturation.plan_backfill(
            target=desired_target, running=running, backlog=backlog
        )
        chosen_ids = [wi.id for wi in chosen][:worktree_headroom]

        # Floor guarantee: if backfill would leave us below the floor while
        # backlog remains, pull up to the floor regardless of target math.
        if running + len(chosen_ids) < cfg.floor:
            already = set(chosen_ids)
            for uid in self._state.pending:
                if len(chosen_ids) >= worktree_headroom:
                    break
                if running + len(chosen_ids) >= cfg.floor:
                    break
                if uid not in already:
                    chosen_ids.append(uid)
                    already.add(uid)

        for uid in chosen_ids:
            # deque has no remove-by-value-fast, but backlogs are small per tick.
            try:
                self._state.pending.remove(uid)
            except ValueError:  # pragma: no cover - defensive
                continue
            self._state.running.add(uid)
        return chosen_ids

    async def step(self) -> list[str]:
        """One reconcile: plan under lock, dispatch the planned units off-lock."""
        async with self._lock:
            to_dispatch = self._plan()
        dispatched: list[str] = []
        for uid in to_dispatch:
            try:
                await self._dispatch_fn(uid)
                dispatched.append(uid)
                async with self._lock:
                    self._state.total_dispatched += 1
            except Exception as exc:
                logger.error("DispatchLane: dispatch of %s failed: %s", uid, exc)
                # Roll the unit back to pending so it is retried, not lost.
                async with self._lock:
                    self._state.running.discard(uid)
                    self._state.pending.appendleft(uid)
        if dispatched:
            logger.info("DispatchLane: dispatched %d unit(s): %s", len(dispatched), dispatched)
        return dispatched

    async def run(self) -> None:
        self._stopped = False
        while not self._stopped:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - loop guard
                logger.error("DispatchLane loop error: %s", exc)
            await asyncio.sleep(self._config.dispatch_interval_s)

    def stop(self) -> None:
        self._stopped = True


class IntegrateLane:
    """Merge completed worktrees into the repo, refusing clobbers.

    Drains ``state.completed_awaiting_merge`` one unit at a time, calling the
    injected ``merge_fn`` (the daemon adapter wraps ``safe_merge`` under
    ``git_repo_lock`` and reclaims the worktree on success). A refused clobber
    is requeued at the BACK of the merge queue (so it does not block other
    units) and the worktree is NOT reclaimed — the work is never silently lost.
    """

    def __init__(
        self,
        config: PipelineConfig,
        state: LaneState,
        lock: asyncio.Lock,
        merge_fn: MergeFn,
        *,
        max_clobber_retries: int = 3,
    ) -> None:
        self._config = config
        self._state = state
        self._lock = lock
        self._merge_fn = merge_fn
        self._max_clobber_retries = max_clobber_retries
        self._clobber_retries: dict[str, int] = {}
        self._stopped = False

    async def step(self) -> MergeOutcome | None:
        """Merge at most ONE completed unit. Returns its outcome, or None if idle."""
        async with self._lock:
            if not self._state.completed_awaiting_merge:
                return None
            unit = self._state.completed_awaiting_merge.popleft()

        try:
            outcome = await self._merge_fn(unit)
        except Exception as exc:
            logger.error("IntegrateLane: merge of %s raised: %s", unit.unit_id, exc)
            # Treat an unexpected error as a non-fatal refusal: requeue so it is
            # retried, never dropped.
            async with self._lock:
                self._state.completed_awaiting_merge.append(unit)
            return MergeOutcome(
                unit_id=unit.unit_id, merged=False, clobber_refused=False,
                detail=f"error: {exc}",
            )

        async with self._lock:
            if outcome.merged:
                self._state.running.discard(unit.unit_id)
                self._state.merged_awaiting_gate.append(unit.unit_id)
                self._state.total_merged += 1
                self._clobber_retries.pop(unit.unit_id, None)
                logger.info(
                    "IntegrateLane: merged %s (%s); worktree reclaimed",
                    unit.unit_id, outcome.detail,
                )
            elif outcome.clobber_refused:
                self._state.total_clobbers_refused += 1
                n = self._clobber_retries.get(unit.unit_id, 0) + 1
                self._clobber_retries[unit.unit_id] = n
                if n <= self._max_clobber_retries:
                    # Requeue at the BACK so a stuck conflict never head-of-lines
                    # the merge queue and starve other completed units.
                    self._state.completed_awaiting_merge.append(unit)
                    logger.warning(
                        "IntegrateLane: REFUSED clobber for %s (attempt %d/%d) — "
                        "worktree preserved, requeued",
                        unit.unit_id, n, self._max_clobber_retries,
                    )
                else:
                    logger.error(
                        "IntegrateLane: clobber for %s unresolved after %d attempts "
                        "— leaving worktree for manual resolution, dropping from queue",
                        unit.unit_id, self._max_clobber_retries,
                    )
                    self._state.running.discard(unit.unit_id)
            else:
                # Non-clobber failure: requeue for retry.
                self._state.completed_awaiting_merge.append(unit)
        return outcome

    async def run(self) -> None:
        self._stopped = False
        while not self._stopped:
            try:
                outcome = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - loop guard
                logger.error("IntegrateLane loop error: %s", exc)
                outcome = None
            # Only sleep when idle; when work remains, drain promptly.
            if outcome is None:
                await asyncio.sleep(self._config.integrate_interval_s)

    def stop(self) -> None:
        self._stopped = True


class GateLane:
    """Debounced, single-flight gate/validation on a coherent snapshot.

    Runs at most one gate at a time. A gate is triggered only when there is
    merged-but-ungated work AND at least ``gate_debounce_s`` has elapsed since
    the last gate START — so a burst of merges coalesces into one gate run.
    While the gate runs, the dispatch and integrate lanes keep producing; the
    set of units this run covers is captured at START so newly-merged units
    after the snapshot are gated by the NEXT run.
    """

    def __init__(
        self,
        config: PipelineConfig,
        state: LaneState,
        lock: asyncio.Lock,
        gate_fn: GateFn,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
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
        """Maybe run one gate. Returns green/red bool, or None if not due.

        Single-flight: a concurrent step while a gate is in flight is a no-op.
        Debounced: returns None until ``gate_debounce_s`` has elapsed.
        """
        async with self._lock:
            if not self._due():
                return None
            # Snapshot the coherent set this run covers, and stamp the epoch at
            # START so the debounce window opens now (lanes 1&2 keep producing).
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
                # Clear exactly the units this run covered; any merged AFTER the
                # snapshot remain queued for the next gate.
                covered_set = set(covered)
                self._state.merged_awaiting_gate = [
                    u for u in self._state.merged_awaiting_gate
                    if u not in covered_set
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
        self._stopped = True
