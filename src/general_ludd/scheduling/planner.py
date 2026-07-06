"""OrchestrationPlanner — deterministic parallelism planner for the orchestrator.

The orchestrator historically decided "is this task blocked?" by judgment (ad-hoc
reasoning about what files each agent was touching). This module replaces that
judgment with code: given a list of pending work items that declare the files they
will touch and their dependencies, OrchestrationPlanner feeds them through
Scheduler.plan() to compute concurrency-safe parallel batches deterministically.

Usage::

    from general_ludd.scheduling.planner import OrchestrationPlanner

    planner = OrchestrationPlanner()
    result = planner.plan_work([
        {"id": "task-a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        {"id": "task-b", "files": ["src/bar.py"], "depends_on": [], "is_greenfield": False},
        {"id": "task-c", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
    ])
    # result["parallel_now"] == ["task-a", "task-b"]  (batch 0; task-c must wait)
    # result["batches"]      == [["task-a", "task-b"], ["task-c"]]

# TODO(integration): when FileClaimRegistry (#31) is merged, source live
# file-claims as the resource set instead of the caller-declared files list.
# Wire: planner.plan_work(registry.current_claims()) at the start of each
# orchestrator tick so the planner sees *actual* in-flight file locks, not just
# the declared intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.scheduling.scheduler import Scheduler, WorkItem


@dataclass
class PlanResult:
    """Structured result from OrchestrationPlanner.plan_work().

    Attributes:
        batches: Ordered list of concurrency-safe batches (each batch is a list
            of item ids).  Batches are ordered: items in batch N must wait for
            all items in batches 0..N-1 to complete.
        parallel_now: Item ids in batch 0 — the items that may ALL start right
            now (no predecessor constraints).
        serialized: List of (id_a, id_b, shared_file) tuples explaining why
            each pair of items was placed in different batches due to a shared
            exclusive resource (file).
        explanation: Human-readable summary of why each serialized pair cannot
            run in parallel.
    """

    batches: list[list[str]] = field(default_factory=list)
    parallel_now: list[str] = field(default_factory=list)
    serialized: list[tuple[str, str, str]] = field(default_factory=list)
    explanation: str = ""


class OrchestrationPlanner:
    """Deterministic replacement for ad-hoc 'is this blocked?' orchestrator judgment.

    Given pending work items — each declaring the files it will touch (its
    exclusive resources) and its upstream dependencies — this planner computes
    the concurrency-safe parallel batches via Scheduler.plan().

    This is the correct place to extend for live file-claim integration.
    See the module-level TODO(integration) note.
    """

    def __init__(self) -> None:
        self._scheduler = Scheduler()

    def plan_work(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute concurrency-safe parallel batches from a list of work-item dicts.

        Each item dict must contain:
            - ``id`` (str): unique identifier.
            - ``files`` (list[str]): files this item will touch; each file is an
              exclusive resource.  Two items sharing any file must run serially.
            - ``depends_on`` (list[str]): ids of items that must complete first.
            - ``is_greenfield`` (bool): when True *and* files is empty, this item
              is greenfield work that never forces serialization.

        Returns a dict with keys:
            - ``batches`` (list[list[str]]): ordered concurrency-safe batches.
            - ``parallel_now`` (list[str]): ids in batch 0 (can start immediately).
            - ``serialized`` (list[tuple]): (id_a, id_b, shared_file) for every
              pair that was pushed into different batches due to a file conflict.
            - ``explanation`` (str): human-readable explanation of serializations.

        Raises:
            CycleError: If a dependency cycle exists in the items.
            ValueError: If ``depends_on`` references an unknown item id.
            KeyError: If a required key is missing from an item dict.
        """
        work_items = self._build_work_items(items)
        batches = self._scheduler.plan(work_items)
        plan = PlanResult(
            batches=batches,
            parallel_now=batches[0] if batches else [],
        )
        self._compute_serializations(work_items, batches, plan)
        return {
            "batches": plan.batches,
            "parallel_now": plan.parallel_now,
            "serialized": plan.serialized,
            "explanation": plan.explanation,
        }

    def parallelizable(self, items: list[dict[str, Any]]) -> list[str]:
        """Return the ids of items that can ALL run concurrently right now (batch 0).

        This is a convenience wrapper around plan_work() that returns only the
        first batch — items with no predecessors and no mutual file conflicts.

        Args:
            items: Same format as plan_work().

        Returns:
            List of item ids that may start immediately (batch 0).
        """
        result = self.plan_work(items)
        return list(result["parallel_now"])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_work_items(self, items: list[dict[str, Any]]) -> list[WorkItem]:
        """Convert raw dicts to WorkItem objects, mapping files -> resources."""
        work_items: list[WorkItem] = []
        for raw in items:
            item_id: str = raw["id"]
            files: list[str] = list(raw.get("files") or [])
            depends_on: list[str] = list(raw.get("depends_on") or [])
            is_greenfield: bool = bool(raw.get("is_greenfield", False))
            # Each file path is an exclusive resource — two items touching the same
            # file cannot run concurrently.
            resources: frozenset[str] = frozenset(files)
            work_items.append(
                WorkItem(
                    id=item_id,
                    resources=resources,
                    depends_on=frozenset(depends_on),
                    is_greenfield=is_greenfield,
                )
            )
        return work_items

    def _compute_serializations(
        self,
        work_items: list[WorkItem],
        batches: list[list[str]],
        plan: PlanResult,
    ) -> None:
        """Populate plan.serialized and plan.explanation by comparing all item pairs."""
        # Build id -> batch_index and id -> WorkItem maps for O(1) lookups.
        batch_of: dict[str, int] = {}
        for idx, batch in enumerate(batches):
            for item_id in batch:
                batch_of[item_id] = idx
        item_map: dict[str, WorkItem] = {wi.id: wi for wi in work_items}

        serialized: list[tuple[str, str, str]] = []
        explanations: list[str] = []
        ids = [wi.id for wi in work_items]

        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a_id = ids[i]
                b_id = ids[j]
                # Only report pairs that ended up in DIFFERENT batches due to
                # a RESOURCE (file) conflict — not due to dependency ordering.
                if batch_of[a_id] == batch_of[b_id]:
                    continue
                a = item_map[a_id]
                b = item_map[b_id]
                shared_files = sorted(a.resources & b.resources)
                if not shared_files:
                    # Different batch due to dependency, not file conflict —
                    # don't report as "serialized" (it's just ordering).
                    continue
                # Report the first shared file as the canonical conflict reason.
                conflict_file = shared_files[0]
                serialized.append((a_id, b_id, conflict_file))
                explanations.append(
                    f"'{a_id}' and '{b_id}' both touch '{conflict_file}' "
                    f"(and {len(shared_files) - 1} other shared file(s)) — "
                    f"they serialize into batches {batch_of[a_id]} and {batch_of[b_id]}."
                    if len(shared_files) > 1
                    else f"'{a_id}' and '{b_id}' both touch '{conflict_file}' — "
                    f"they serialize into batches {batch_of[a_id]} and {batch_of[b_id]}."
                )

        plan.serialized = serialized
        plan.explanation = (
            "\n".join(explanations) if explanations else "No file-conflict serializations."
        )
