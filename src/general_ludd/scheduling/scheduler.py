"""Concurrency/blocking scheduler for agentic work items.

Given a set of WorkItems, determines which can run CONCURRENTLY and which must
be SERIAL.  Core rules:
  - Items touching the SAME exclusive resource (master tree, gate, a project DB,
    etc.) must serialize into different batches.
  - Items with no shared exclusive resources and no intra-batch dependency edge
    can be placed in the same batch (concurrent).
  - Greenfield items (is_greenfield=True, resources=frozenset()) never force-
    serialize other items; they are slotted freely and never block others.
  - Dependency ordering is always respected: an item only appears after every
    item in its depends_on set has been emitted in an earlier batch.
  - A cycle in depends_on raises CycleError immediately (fail-closed).

Integration: Scheduler.plan() drives the event loop's tick — EventLoop uses it
to compute concurrency-safe batches (asyncio.gather) while serializing items
that share an exclusive resource. See EventLoop._phase_dispatch_execute_jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class CycleError(ValueError):
    """Raised when a dependency cycle is detected in the work-item graph."""


@dataclass(frozen=True)
class WorkItem:
    """A unit of agentic work to be scheduled.

    Attributes:
        id: Unique identifier for this item.
        resources: Frozenset of *exclusive* resource names this item needs.
            Two items sharing any element of this set must run serially.
        depends_on: Frozenset of item ids that must complete before this item
            can start.  Transitively establishes ordering across batches.
        is_greenfield: When True *and* resources is empty, this item is
            greenfield work not yet wired in to any shared resource.  It may
            run freely alongside anything and never forces serialization.
    """

    id: str
    resources: frozenset[str] = field(default_factory=frozenset)
    depends_on: frozenset[str] = field(default_factory=frozenset)
    is_greenfield: bool = False


def can_run_concurrently(a: WorkItem, b: WorkItem) -> bool:
    """Return True iff items *a* and *b* may safely run at the same time.

    Concurrent iff:
      1. They share no exclusive resource, AND
      2. Neither depends on the other.

    Greenfield items (is_greenfield=True, resources=frozenset()) satisfy (1)
    trivially and will only be blocked by rule (2).
    """
    shared_resources = a.resources & b.resources
    if shared_resources:
        return False
    return not (b.id in a.depends_on or a.id in b.depends_on)


class Scheduler:
    """Partitions a list of WorkItems into ordered, concurrency-safe batches.

    Usage::

        scheduler = Scheduler()
        batches = scheduler.plan(items)
        # batches is a list[list[str]] — each inner list is a batch of item ids
        # that may run concurrently; batches are ordered (later batches depend
        # on earlier ones having completed).

    Guarantees:
      - All items in a batch are mutually concurrency-safe (pairwise).
      - An item only appears in a batch after all items in its depends_on have
        appeared in strictly earlier batches.
      - Greenfield items (is_greenfield=True, no resources) never force-serialize
        any other item.
      - A cycle in depends_on raises CycleError immediately (fail-closed).
      - Output is deterministic for a given input order.
    """

    def plan(self, items: list[WorkItem]) -> list[list[str]]:
        """Compute ordered concurrency-safe batches.

        Args:
            items: Work items to schedule.  May be in any order.

        Returns:
            Ordered list of batches; each batch is a list of item ids.

        Raises:
            CycleError: If a dependency cycle exists in *items*.
            ValueError: If depends_on references an unknown item id.
        """
        if not items:
            return []

        item_map: dict[str, WorkItem] = {item.id: item for item in items}

        # Validate that all depends_on references point at known items.
        for item in items:
            for dep_id in item.depends_on:
                if dep_id not in item_map:
                    raise ValueError(
                        f"WorkItem {item.id!r} depends on unknown item {dep_id!r}"
                    )

        # Kahn's topological sort to detect cycles and produce a base ordering.
        in_degree: dict[str, int] = {item.id: 0 for item in items}
        adjacency: dict[str, list[str]] = {item.id: [] for item in items}

        for item in items:
            for dep_id in item.depends_on:
                # dep_id must finish before item.id starts
                adjacency[dep_id].append(item.id)
                in_degree[item.id] += 1

        # Kahn's algorithm — maintains original insertion order for stable output.
        ready: list[str] = [
            item.id for item in items if in_degree[item.id] == 0
        ]
        topo_order: list[str] = []

        while ready:
            # Pop from the front to preserve input order within a ready set.
            node = ready.pop(0)
            topo_order.append(node)
            for successor in adjacency[node]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    ready.append(successor)

        if len(topo_order) != len(items):
            # Some nodes were never emitted — cycle detected.
            cyclic = {item.id for item in items} - set(topo_order)
            raise CycleError(
                f"Dependency cycle detected among items: {sorted(cyclic)}"
            )

        # Now greedily assign each item (in topo order) to the earliest batch
        # where:
        #   (a) all depends_on predecessors are in strictly earlier batches, AND
        #   (b) the item is concurrency-safe with every item already in that batch.
        #
        # "earliest" means we try batch 0, 1, 2, ... and pick the first that works.
        batches: list[list[str]] = []
        # Track which batch index each placed item landed in.
        placed_batch: dict[str, int] = {}

        for item_id in topo_order:
            item = item_map[item_id]

            # Minimum batch index forced by dependencies.
            min_batch = 0
            for dep_id in item.depends_on:
                min_batch = max(min_batch, placed_batch[dep_id] + 1)

            # Try to slot item into the earliest eligible batch.
            placed = False
            for batch_idx in range(min_batch, len(batches)):
                batch_items = [item_map[bid] for bid in batches[batch_idx]]
                if all(can_run_concurrently(item, other) for other in batch_items):
                    batches[batch_idx].append(item_id)
                    placed_batch[item_id] = batch_idx
                    placed = True
                    break

            if not placed:
                # Start a new batch.
                new_idx = len(batches)
                batches.append([item_id])
                placed_batch[item_id] = new_idx

        return batches
