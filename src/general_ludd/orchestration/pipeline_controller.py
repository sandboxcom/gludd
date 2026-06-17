"""Pure 3-lane multitask + merge pipeline controller.

This module models — with NO real daemon, git, or clock — how General Ludd runs
many work units through three concurrent lanes every ``tick()``:

  1. **DispatchLane** keeps ``target`` workers busy. It reconciles the live
     running count from an injected worker-pool protocol every tick and
     backfills empty slots from the backlog, dispatching *ahead* up to target.
     Its single hard invariant: a tick that would leave ``running`` below the
     ``floor`` MUST backfill first, and — whenever the backlog allows — it
     ``assert running >= floor`` before returning. When the backlog cannot
     refill above the floor (genuinely starved), the violation is *surfaced*
     (``below_floor``) and counted, never silently swallowed.

  2. **IntegrateLane** merges completed units back into the trunk *while the
     other lanes keep running*. It asks an injected merge strategy whether a
     unit overlaps in-flight work: an overlap is resolved with a three-way
     merge (never a clobbering fast-forward), a non-overlapping unit
     fast-forwards. A merge that succeeds reclaims the unit (it becomes
     integrated and eligible for the gate); a merge that fails is reported.

  3. **GateLane** batches an integrated set: snapshot -> gate -> commit. It runs
     against a SNAPSHOT so lanes 1 and 2 keep producing concurrently — the gate
     never drains the dispatch lane. Only a passing gate commits the batch.

``PipelineController.tick()`` advances all three lanes for one step and returns
an immutable :class:`PipelineState` ``(running, integrated, committed,
floor_violations)``.

Design principles (mirroring ``controllers/saturation.py``):

  - **Reconcile, never drift.** The running count is read from the worker pool
    every tick; no lane keeps a private counter that could diverge from reality.
  - **Pure / deterministic.** The control logic has no sleeps, clocks, or hidden
    state; all side effects live behind the injected protocols. Same protocol
    behavior + same inputs => same plan.
  - **Backfill = target - running, floor-first.** Empty slots are refilled up to
    target; a drop below floor is corrected before the tick settles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "DispatchLane",
    "DispatchResult",
    "GateLane",
    "GateResult",
    "GateRunner",
    "IntegrateLane",
    "IntegrateResult",
    "MergeStrategy",
    "PipelineController",
    "PipelineState",
    "WorkerPool",
]


# --------------------------------------------------------------------------- #
# Injected protocols. The pipeline NEVER touches a real daemon/git/clock; it
# only talks to these. Tests pass in fakes; production passes the live adapters.
# --------------------------------------------------------------------------- #


class WorkerPool(Protocol):
    """A pool of concurrent workers the DispatchLane keeps busy.

    ``running`` is AUTHORITATIVE — it is counted from the live job table each
    tick, never derived from a counter the lane increments. ``dispatch`` starts
    a worker for one backlog item and returns its opaque worker id.
    """

    def running(self) -> int: ...

    def dispatch(self, item: object) -> str: ...


class MergeStrategy(Protocol):
    """Strategy for folding a completed unit back into the trunk.

    ``detect_overlap`` reports whether the unit touches the same surface as
    other in-flight work (in which case a three-way merge is required rather
    than a clobbering fast-forward). Each merge call returns ``True`` on
    success, ``False`` on conflict/failure.
    """

    def detect_overlap(self, unit: str) -> bool: ...

    def three_way_merge(self, unit: str) -> bool: ...

    def fast_forward(self, unit: str) -> bool: ...


class GateRunner(Protocol):
    """Runs a batched snapshot -> gate -> commit over an integrated set.

    ``snapshot`` freezes the integrated set so the gate runs against a stable
    view while other lanes keep producing. ``gate`` returns ``True`` when the
    snapshot is green. ``commit`` records the batch and returns a commit id.
    """

    def snapshot(self, units: list[str]) -> str: ...

    def gate(self, snapshot_id: str) -> bool: ...

    def commit(self, snapshot_id: str, units: list[str]) -> str: ...


# --------------------------------------------------------------------------- #
# Per-lane result records (immutable).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one DispatchLane tick.

    Attributes:
        dispatched: Worker ids started this tick (one per backfilled slot).
        running: Authoritative running count AFTER backfill.
        below_floor: ``True`` when, after backfilling everything the backlog
            allowed, ``running`` is still below ``floor`` (genuine starvation —
            surfaced, not hidden).
    """

    dispatched: list[str] = field(default_factory=list)
    running: int = 0
    below_floor: bool = False


@dataclass(frozen=True)
class IntegrateResult:
    """Outcome of one IntegrateLane tick.

    Attributes:
        integrated: Units successfully merged into the trunk this tick.
        failed: Units whose merge failed (conflict) and were NOT integrated.
        three_way: Units that required (and got) a three-way merge.
        fast_forward: Units that fast-forwarded with no overlap.
    """

    integrated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    three_way: list[str] = field(default_factory=list)
    fast_forward: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GateResult:
    """Outcome of one GateLane tick.

    Attributes:
        committed: Units committed this tick (empty when the gate failed or the
            batch was empty).
        snapshot_id: The snapshot the gate ran against, or ``None`` if no batch.
        passed: Whether the gate was green (``None`` when there was no batch).
        commit_id: The commit id when committed, else ``None``.
    """

    committed: list[str] = field(default_factory=list)
    snapshot_id: str | None = None
    passed: bool | None = None
    commit_id: str | None = None


@dataclass(frozen=True)
class PipelineState:
    """Immutable snapshot of one full ``PipelineController.tick()``.

    Attributes:
        running: Workers running after the dispatch lane backfilled.
        integrated: Units integrated this tick.
        committed: Units committed this tick.
        floor_violations: Count of ticks (this run) where the floor could not
            be honored because the backlog was exhausted. Accumulates across
            ticks so a caller can alarm on sustained starvation.
    """

    running: int = 0
    integrated: list[str] = field(default_factory=list)
    committed: list[str] = field(default_factory=list)
    floor_violations: int = 0


# --------------------------------------------------------------------------- #
# Lane 1: Dispatch.
# --------------------------------------------------------------------------- #


class DispatchLane:
    """Keep ``target`` workers busy; never let ``running`` settle below floor.

    The lane holds no running counter of its own — it reads the authoritative
    count from the injected :class:`WorkerPool` each tick and backfills empty
    slots from the backlog (dispatch-ahead up to ``target``). When the backlog
    has enough work, the lane guarantees ``running >= floor`` on return via an
    ``assert``. When the backlog is too short to reach the floor, it surfaces
    ``below_floor=True`` instead of asserting (real starvation is reported, not
    crashed on).
    """

    def __init__(self, pool: WorkerPool, target: int, floor: int) -> None:
        if floor < 0:
            raise ValueError("floor must be >= 0")
        if target < floor:
            raise ValueError("target must be >= floor")
        self._pool = pool
        self.target = target
        self.floor = floor

    def tick(self, backlog: list[object]) -> DispatchResult:
        """Backfill empty slots toward ``target``, floor-first.

        Args:
            backlog: Ordered candidate items. Drained from the FRONT so caller
                priority order is preserved. Not mutated.

        Returns:
            A :class:`DispatchResult` with the worker ids started, the running
            count after backfill, and whether the floor remained breached
            because the backlog was exhausted.
        """
        running = self._pool.running()
        headroom = max(0, self.target - running)
        pull = min(headroom, len(backlog))

        dispatched: list[str] = []
        for i in range(pull):
            dispatched.append(self._pool.dispatch(backlog[i]))

        running_after = self._pool.running()
        below_floor = running_after < self.floor

        # Floor invariant: when the backlog COULD have refilled us to the floor,
        # we must not return below it. If we are still below floor, it can only
        # be because the backlog ran dry first (real starvation) — in which case
        # the shortfall is surfaced via below_floor, not asserted.
        if below_floor:
            assert len(backlog) <= pull, (
                "floor breached with backlog remaining: "
                f"running={running_after} floor={self.floor} "
                f"pulled={pull} backlog={len(backlog)}"
            )
        else:
            assert running_after >= self.floor

        return DispatchResult(
            dispatched=dispatched,
            running=running_after,
            below_floor=below_floor,
        )


# --------------------------------------------------------------------------- #
# Lane 2: Integrate.
# --------------------------------------------------------------------------- #


class IntegrateLane:
    """Merge completed units into the trunk while other lanes keep running.

    For each completed unit the lane asks the injected :class:`MergeStrategy`
    whether it overlaps in-flight work. An overlap is resolved with a three-way
    merge (never a clobbering fast-forward); a clean unit fast-forwards. Only a
    successful merge reclaims the unit (marks it integrated).
    """

    def __init__(self, merge: MergeStrategy) -> None:
        self._merge = merge

    def tick(self, completed: list[str]) -> IntegrateResult:
        """Integrate each completed unit via the appropriate merge.

        Args:
            completed: Units that finished and are ready to fold in. Processed
                in order.

        Returns:
            An :class:`IntegrateResult` partitioning the batch into integrated /
            failed and recording which path (three-way vs fast-forward) each
            took.
        """
        integrated: list[str] = []
        failed: list[str] = []
        three_way: list[str] = []
        fast_forward: list[str] = []

        for unit in completed:
            if self._merge.detect_overlap(unit):
                three_way.append(unit)
                ok = self._merge.three_way_merge(unit)
            else:
                fast_forward.append(unit)
                ok = self._merge.fast_forward(unit)

            if ok:
                integrated.append(unit)
            else:
                failed.append(unit)

        return IntegrateResult(
            integrated=integrated,
            failed=failed,
            three_way=three_way,
            fast_forward=fast_forward,
        )


# --------------------------------------------------------------------------- #
# Lane 3: Gate.
# --------------------------------------------------------------------------- #


class GateLane:
    """Batch an integrated set: snapshot -> gate -> commit.

    The gate runs against a SNAPSHOT of the integrated set, so the dispatch and
    integrate lanes keep producing while the gate is in flight — the gate never
    drains them. Only a passing gate commits. An empty batch is a no-op (no
    snapshot, no gate).
    """

    def __init__(self, runner: GateRunner) -> None:
        self._runner = runner

    def tick(self, integrated: list[str]) -> GateResult:
        """Snapshot, gate, and (if green) commit the integrated batch.

        Args:
            integrated: Units ready to be gated together this tick.

        Returns:
            A :class:`GateResult`. ``committed`` is the batch on a green gate,
            else empty; ``passed`` is ``None`` when there was nothing to gate.
        """
        if not integrated:
            return GateResult()

        snapshot_id = self._runner.snapshot(integrated)
        passed = self._runner.gate(snapshot_id)
        if not passed:
            return GateResult(snapshot_id=snapshot_id, passed=False)

        commit_id = self._runner.commit(snapshot_id, integrated)
        return GateResult(
            committed=list(integrated),
            snapshot_id=snapshot_id,
            passed=True,
            commit_id=commit_id,
        )


# --------------------------------------------------------------------------- #
# The controller: advance all three lanes concurrently for one step.
# --------------------------------------------------------------------------- #


class PipelineController:
    """Advance the dispatch, integrate, and gate lanes one concurrent step.

    The three lanes are independent within a tick: dispatch backfills against
    the live running count, integrate folds in the completed units handed to
    this tick, and gate batches the integrated units handed to this tick — all
    against the inputs/snapshot, so no lane drains another. The controller
    accumulates ``floor_violations`` across ticks so sustained dispatch
    starvation is observable.
    """

    def __init__(
        self,
        dispatch: DispatchLane,
        integrate: IntegrateLane,
        gate: GateLane,
    ) -> None:
        self._dispatch = dispatch
        self._integrate = integrate
        self._gate = gate
        self._floor_violations = 0

    @property
    def floor_violations(self) -> int:
        """Total floor violations observed across all ticks so far."""
        return self._floor_violations

    def tick(
        self,
        backlog: list[object],
        completed: list[str],
        integrated: list[str],
    ) -> PipelineState:
        """Advance all three lanes one step and return the combined state.

        Args:
            backlog: Items the dispatch lane may pull from to backfill.
            completed: Finished units the integrate lane should fold in.
            integrated: Already-integrated units the gate lane should batch.

        Returns:
            A :class:`PipelineState` with the post-backfill running count, the
            units integrated and committed this tick, and the cumulative
            floor-violation count.
        """
        # Lane 1 first: refill empty slots BEFORE the tick settles, so the floor
        # is honored as early as possible. This is what guarantees the headline
        # invariant — many workers finishing at once cannot leave us below floor
        # while backlog remains.
        dispatch_result = self._dispatch.tick(backlog=backlog)
        if dispatch_result.below_floor:
            self._floor_violations += 1

        # Lane 2: integrate completed units while lane 1's workers keep running.
        integrate_result = self._integrate.tick(completed=completed)

        # Lane 3: batch-gate the integrated set against a snapshot — does not
        # touch lane 1, so dispatch is never drained by a gate run.
        gate_result = self._gate.tick(integrated=integrated)

        return PipelineState(
            running=dispatch_result.running,
            integrated=integrate_result.integrated,
            committed=gate_result.committed,
            floor_violations=self._floor_violations,
        )
