"""Integration tests for Scheduler.plan() — alpha.4 E-05.

Focus: "due" vs "not-yet-due" execution semantics expressed through
Scheduler.plan() batch placement.

  - A job with ALL deps satisfied is "due": it appears in exactly ONE batch,
    and that batch is the EARLIEST it can legally go.
  - A job with UNMET deps is "not-yet-due": it is absent from every batch
    before its deps complete, then fires in exactly one later batch.
  - Each job id appears exactly once across all batches (fires once, never repeated).
  - Greenfield items (no resources, is_greenfield=True) are due immediately and
    fire in batch 0 even alongside resource-holding items.
  - A chain A->B->C->D produces four distinct batches; each fires exactly once in
    strict dependency order.
  - A diamond A->{B,C}->D: B and C are "due" after A (same batch); D is
    "not-yet-due" until both B and C complete.

Determinism: any set/frozenset iterated for parametrize is wrapped in sorted().
No real sleep or wall-clock time — purely structural batch assertions.
"""

from __future__ import annotations

import pytest

from general_ludd.scheduling.scheduler import (
    Scheduler,
    WorkItem,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def assert_fires_exactly_once(batches: list[list[str]], item_id: str) -> int:
    """Assert item_id appears in exactly one batch; return that batch index."""
    found = [i for i, batch in enumerate(batches) if item_id in batch]
    assert len(found) == 1, f"{item_id!r} should appear in exactly 1 batch, found in: {found}"
    return found[0]


# ---------------------------------------------------------------------------
# 1. Due job (no deps) fires in batch 0, exactly once
# ---------------------------------------------------------------------------


def test_due_job_fires_in_batch_zero() -> None:
    """A job with no dependencies is immediately due and lands in batch 0."""
    item = WorkItem(id="ready")
    s = Scheduler()
    batches = s.plan([item])
    idx = assert_fires_exactly_once(batches, "ready")
    assert idx == 0, f"due job must be in batch 0, got batch {idx}"


def test_due_job_fires_exactly_once_among_many() -> None:
    """Among several independent jobs, each is due and each appears exactly once."""
    items = [WorkItem(id=f"job_{i}") for i in range(5)]
    s = Scheduler()
    batches = s.plan(items)
    for item in items:
        assert_fires_exactly_once(batches, item.id)
    # All independent — should fit in one batch
    assert len(batches) == 1


# ---------------------------------------------------------------------------
# 2. Not-yet-due job (has unmet deps) is absent from early batches
# ---------------------------------------------------------------------------


def test_not_yet_due_job_skipped_until_dep_completes() -> None:
    """B depends on A: B must NOT appear in batch 0 (not-yet-due), only after A."""
    a = WorkItem(id="A")
    b = WorkItem(id="B", depends_on=frozenset({"A"}))
    s = Scheduler()
    batches = s.plan([a, b])

    a_idx = assert_fires_exactly_once(batches, "A")
    b_idx = assert_fires_exactly_once(batches, "B")

    # B must not appear in any batch at or before A's batch
    assert b_idx > a_idx, (
        f"B (not-yet-due until A completes) must be in a later batch than A; "
        f"A={a_idx}, B={b_idx}"
    )
    # B must be absent from batches 0..a_idx (the "skipped" region)
    for early_batch_idx in range(a_idx + 1):
        assert "B" not in batches[early_batch_idx], (
            f"B must be absent from batch {early_batch_idx} (skipped region)"
        )


def test_not_yet_due_job_fires_in_exactly_one_later_batch() -> None:
    """C depends on B depends on A — three-step chain, each fires exactly once."""
    a = WorkItem(id="A")
    b = WorkItem(id="B", depends_on=frozenset({"A"}))
    c = WorkItem(id="C", depends_on=frozenset({"B"}))
    s = Scheduler()
    batches = s.plan([a, b, c])

    a_idx = assert_fires_exactly_once(batches, "A")
    b_idx = assert_fires_exactly_once(batches, "B")
    c_idx = assert_fires_exactly_once(batches, "C")

    assert a_idx < b_idx < c_idx, (
        f"Chain ordering violated: A={a_idx}, B={b_idx}, C={c_idx}"
    )


# ---------------------------------------------------------------------------
# 3. Four-step chain: each node fires exactly once in strict order
# ---------------------------------------------------------------------------


def test_chain_abcd_each_fires_once_in_strict_order() -> None:
    """A->B->C->D: four distinct batches, each id appears exactly once."""
    a = WorkItem(id="A")
    b = WorkItem(id="B", depends_on=frozenset({"A"}))
    c = WorkItem(id="C", depends_on=frozenset({"B"}))
    d = WorkItem(id="D", depends_on=frozenset({"C"}))
    s = Scheduler()
    batches = s.plan([a, b, c, d])

    assert len(batches) == 4, f"Expected 4 batches for A->B->C->D, got {len(batches)}: {batches}"

    indices = {node_id: assert_fires_exactly_once(batches, node_id) for node_id in ("A", "B", "C", "D")}
    assert indices["A"] < indices["B"] < indices["C"] < indices["D"], (
        f"Strict chain order violated: {indices}"
    )


# ---------------------------------------------------------------------------
# 4. Diamond: fan-out then fan-in
# ---------------------------------------------------------------------------


def test_diamond_fanout_fanin() -> None:
    """A->{B,C}->D: B and C are due together after A; D not-yet-due until both complete."""
    a = WorkItem(id="A")
    b = WorkItem(id="B", depends_on=frozenset({"A"}))
    c = WorkItem(id="C", depends_on=frozenset({"A"}))
    d = WorkItem(id="D", depends_on=frozenset({"B", "C"}))
    s = Scheduler()
    batches = s.plan([a, b, c, d])

    a_idx = assert_fires_exactly_once(batches, "A")
    b_idx = assert_fires_exactly_once(batches, "B")
    c_idx = assert_fires_exactly_once(batches, "C")
    d_idx = assert_fires_exactly_once(batches, "D")

    # B and C are due together (same batch) after A
    assert b_idx == c_idx, f"B and C should be in the same batch; B={b_idx}, C={c_idx}"
    assert b_idx > a_idx, f"B/C must come after A; A={a_idx}, B={b_idx}"

    # D not-yet-due until BOTH B and C complete
    assert d_idx > b_idx, f"D must come after B; B={b_idx}, D={d_idx}"
    assert d_idx > c_idx, f"D must come after C; C={c_idx}, D={d_idx}"

    # D must be absent from all batches before its scheduled batch
    for early in range(d_idx):
        assert "D" not in batches[early], (
            f"D (not-yet-due) must be absent from batch {early}"
        )


# ---------------------------------------------------------------------------
# 5. Greenfield items: due immediately even alongside resource-holding items
# ---------------------------------------------------------------------------


def test_greenfield_item_due_immediately_in_batch_zero() -> None:
    """A greenfield item fires in batch 0 regardless of other items' resources."""
    green = WorkItem(id="green", is_greenfield=True)
    heavy_a = WorkItem(id="heavy_a", resources=frozenset({"db"}))
    heavy_b = WorkItem(id="heavy_b", resources=frozenset({"gate"}))
    s = Scheduler()
    batches = s.plan([green, heavy_a, heavy_b])

    green_idx = assert_fires_exactly_once(batches, "green")
    assert green_idx == 0, f"greenfield item must be due immediately (batch 0), got {green_idx}"

    # All three fit in batch 0 (no resource conflicts between them)
    assert len(batches) == 1, f"Expected 1 batch, got {len(batches)}: {batches}"


# ---------------------------------------------------------------------------
# 6. Every item fires exactly once — global invariant across complex plans
# ---------------------------------------------------------------------------


def test_every_item_fires_exactly_once_complex_plan() -> None:
    """In a complex multi-dependency plan, every id appears in exactly one batch."""
    items = [
        WorkItem(id="root"),
        WorkItem(id="worker_a", depends_on=frozenset({"root"}), resources=frozenset({"db"})),
        WorkItem(id="worker_b", depends_on=frozenset({"root"}), resources=frozenset({"db"})),
        WorkItem(id="merger", depends_on=frozenset({"worker_a", "worker_b"})),
        WorkItem(id="green1", is_greenfield=True),
        WorkItem(id="green2", is_greenfield=True),
        WorkItem(id="finalizer", depends_on=frozenset({"merger"})),
    ]
    s = Scheduler()
    batches = s.plan(items)

    all_ids_in_batches = [item_id for batch in batches for item_id in batch]
    expected_ids = {item.id for item in items}

    # Each id present
    assert set(all_ids_in_batches) == expected_ids, (
        f"Missing ids: {expected_ids - set(all_ids_in_batches)}"
    )
    # Each id exactly once
    for item in items:
        assert_fires_exactly_once(batches, item.id)

    # Ordering invariants
    root_idx = assert_fires_exactly_once(batches, "root")
    wa_idx = assert_fires_exactly_once(batches, "worker_a")
    wb_idx = assert_fires_exactly_once(batches, "worker_b")
    merger_idx = assert_fires_exactly_once(batches, "merger")
    finalizer_idx = assert_fires_exactly_once(batches, "finalizer")

    assert wa_idx > root_idx
    assert wb_idx > root_idx
    assert merger_idx > wa_idx
    assert merger_idx > wb_idx
    assert finalizer_idx > merger_idx

    # worker_a and worker_b share "db" resource — must be in DIFFERENT batches
    assert wa_idx != wb_idx, "worker_a and worker_b share 'db' and must serialize"


# ---------------------------------------------------------------------------
# 7. Parametrize over resource-sharing pairs (sorted for xdist determinism)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shared_resource", sorted(["master_tree", "gate", "project_db"]))
def test_shared_resource_items_are_not_yet_due_together(shared_resource: str) -> None:
    """Two items sharing a resource: one is due, the other not-yet-due in the same batch."""
    a = WorkItem(id="first", resources=frozenset({shared_resource}))
    b = WorkItem(id="second", resources=frozenset({shared_resource}))
    s = Scheduler()
    batches = s.plan([a, b])

    a_idx = assert_fires_exactly_once(batches, "first")
    b_idx = assert_fires_exactly_once(batches, "second")

    # They must be in different batches (one is "not-yet-due" while the other runs)
    assert a_idx != b_idx, (
        f"Items sharing '{shared_resource}' must be in different batches; "
        f"both ended up in batch {a_idx}"
    )
