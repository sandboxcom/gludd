"""Saturation controller: keep N agents / N GPU sources maximally busy.

This controller answers a single question each tick: *given a target
concurrency and the set of items already running, how many NEW items should
we pull from the backlog right now so that every slot is busy?*

Design principles (mirroring the event loop's dispatch phase):

  - **Reconcile, never drift.**  The number of items currently running is an
    AUTHORITATIVE input passed in by the caller every tick (e.g. counted from
    the live job table).  This controller never keeps its own running counter
    that could drift out of sync with reality.
  - **Backfill = target - running.**  We pull exactly enough work to refill
    empty slots, never more.  When ``running >= target`` we pull nothing.
  - **Zero idle when backlog is non-empty.**  If there is empty capacity and
    the backlog has items, we backfill up to that capacity (bounded only by
    the backlog length and per-source caps).
  - **Per-source caps.**  When work is dispatched across several GPU/model
    sources, each source has its own capacity; we never assign more than a
    source's remaining headroom.
  - **Pure / deterministic.**  Both public functions are side-effect free and
    drain the backlog in order, so the same inputs always produce the same
    plan.  No sleeps, no clocks, no internal mutable state.

The backlog unit is :class:`~general_ludd.scheduling.scheduler.WorkItem`, the
same frozen dataclass the Scheduler partitions, so a backfill plan can be fed
straight into ``Scheduler.plan()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from general_ludd.scheduling.scheduler import WorkItem

__all__ = ["SaturationController", "SourceCapacity"]


@dataclass(frozen=True)
class SourceCapacity:
    """Capacity of a single work source (e.g. a GPU or model endpoint).

    Attributes:
        source_id: Stable identifier for the source.
        capacity: Maximum number of items that may run concurrently on this
            source.  Must be >= 0.
        running: Authoritative count of items CURRENTLY running on this source,
            reconciled by the caller each tick (never a drifting counter).
    """

    source_id: str
    capacity: int
    running: int = 0

    @property
    def headroom(self) -> int:
        """Free slots on this source right now (never negative)."""
        return max(0, self.capacity - self.running)


@dataclass(frozen=True)
class BackfillAssignment:
    """A backfill plan partitioned per source.

    Attributes:
        items: Flat, in-order list of items selected for dispatch this tick.
        by_source: Mapping of ``source_id`` -> items assigned to that source.
            Empty when no per-source caps were supplied.
    """

    items: list[WorkItem] = field(default_factory=list)
    by_source: dict[str, list[WorkItem]] = field(default_factory=dict)


class SaturationController:
    """Compute how many backlog items to pull to keep all slots busy.

    The controller holds no per-tick state.  Every call is reconciled from the
    inputs the caller passes in, so it cannot drift away from the true running
    count.
    """

    @staticmethod
    def utilization(running: int, target: int) -> float:
        """Fraction of target capacity currently in use, clamped to [0, 1].

        Args:
            running: Authoritative count of items currently running.
            target: Target concurrency (number of slots to keep busy).

        Returns:
            ``running / target`` clamped to the closed interval ``[0.0, 1.0]``.
            Returns ``0.0`` when ``target <= 0`` (no slots => no utilization to
            speak of) so callers never divide by zero.
        """
        if target <= 0:
            return 0.0
        if running <= 0:
            return 0.0
        return min(1.0, running / target)

    def plan_backfill(
        self,
        target: int,
        running: int,
        backlog: list[WorkItem],
        per_source_caps: list[SourceCapacity] | None = None,
    ) -> list[WorkItem]:
        """Select backlog items to pull so empty slots are refilled.

        The number pulled is ``min(target - running, len(backlog))`` and, when
        ``per_source_caps`` is given, is further bounded by the total remaining
        headroom across all sources.  Items are drained from the FRONT of the
        backlog so the caller's ordering (e.g. priority) is preserved.

        Args:
            target: Target concurrency (total slots to keep busy).  Values <= 0
                yield an empty plan.
            running: AUTHORITATIVE count of items currently running, reconciled
                by the caller this tick.  Never an internal drifting counter.
            backlog: Ordered candidate items.  Not mutated.
            per_source_caps: Optional per-source capacities.  When supplied, the
                global backfill is additionally capped by the sum of each
                source's :attr:`SourceCapacity.headroom`.

        Returns:
            The in-order list of items to dispatch this tick.  Empty when there
            is no free capacity or the backlog is empty.
        """
        return self.plan_backfill_by_source(
            target, running, backlog, per_source_caps
        ).items

    def plan_backfill_by_source(
        self,
        target: int,
        running: int,
        backlog: list[WorkItem],
        per_source_caps: list[SourceCapacity] | None = None,
    ) -> BackfillAssignment:
        """Like :meth:`plan_backfill` but also partition items per source.

        Returns:
            A :class:`BackfillAssignment`.  ``items`` is the flat in-order plan;
            ``by_source`` maps each source id to the items routed to it (only
            populated when ``per_source_caps`` is supplied).
        """
        # Global free slots, reconciled from the authoritative running count.
        global_headroom = max(0, target - running)
        if global_headroom <= 0 or not backlog:
            return BackfillAssignment()

        if not per_source_caps:
            chosen = backlog[:global_headroom]
            return BackfillAssignment(items=list(chosen))

        # Bound the global backfill by total remaining source headroom: we can
        # never run more than the sources can physically hold.
        total_source_headroom = sum(src.headroom for src in per_source_caps)
        pull = min(global_headroom, total_source_headroom, len(backlog))
        if pull <= 0:
            return BackfillAssignment()

        # Distribute, in source order, never exceeding any single source's cap.
        remaining = {src.source_id: src.headroom for src in per_source_caps}
        source_order = [src.source_id for src in per_source_caps]
        by_source: dict[str, list[WorkItem]] = {sid: [] for sid in source_order}
        items: list[WorkItem] = []

        for item in backlog:
            if len(items) >= pull:
                break
            for sid in source_order:
                if remaining[sid] > 0:
                    remaining[sid] -= 1
                    by_source[sid].append(item)
                    items.append(item)
                    break

        return BackfillAssignment(items=items, by_source=by_source)
