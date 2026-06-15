"""TDD tests for the concurrency/blocking scheduler (task #23).

Covers:
  - Two items sharing an exclusive resource are serialized into different batches.
  - Two independent items (no shared resources, no dependency) share a batch.
  - Dependency ordering is respected across batches.
  - A greenfield, resource-less item never forces serialization of others.
  - A cycle in depends_on raises CycleError (fail-closed).
  - Exact batch membership is asserted throughout.
"""

from __future__ import annotations

import pytest

from general_ludd.scheduling.scheduler import (
    CycleError,
    Scheduler,
    WorkItem,
    can_run_concurrently,
)

# ---------------------------------------------------------------------------
# can_run_concurrently helper
# ---------------------------------------------------------------------------


def test_can_run_concurrently_disjoint_resources() -> None:
    a = WorkItem(id="a", resources=frozenset({"master_tree"}))
    b = WorkItem(id="b", resources=frozenset({"gate"}))
    assert can_run_concurrently(a, b) is True


def test_cannot_run_concurrently_shared_resource() -> None:
    a = WorkItem(id="a", resources=frozenset({"master_tree"}))
    b = WorkItem(id="b", resources=frozenset({"master_tree"}))
    assert can_run_concurrently(a, b) is False


def test_cannot_run_concurrently_a_depends_on_b() -> None:
    a = WorkItem(id="a", depends_on=frozenset({"b"}))
    b = WorkItem(id="b")
    assert can_run_concurrently(a, b) is False


def test_cannot_run_concurrently_b_depends_on_a() -> None:
    a = WorkItem(id="a")
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    assert can_run_concurrently(a, b) is False


def test_can_run_concurrently_no_resources_no_deps() -> None:
    a = WorkItem(id="a")
    b = WorkItem(id="b")
    assert can_run_concurrently(a, b) is True


def test_can_run_concurrently_greenfield_items() -> None:
    a = WorkItem(id="a", is_greenfield=True)
    b = WorkItem(id="b", is_greenfield=True)
    assert can_run_concurrently(a, b) is True


# ---------------------------------------------------------------------------
# Scheduler.plan — basic cases
# ---------------------------------------------------------------------------


def test_empty_plan() -> None:
    s = Scheduler()
    assert s.plan([]) == []


def test_single_item_plan() -> None:
    s = Scheduler()
    result = s.plan([WorkItem(id="x")])
    assert result == [["x"]]


def test_two_independent_items_share_a_batch() -> None:
    """Items with no shared resources and no dependency go into batch 0 together."""
    a = WorkItem(id="a", resources=frozenset({"db_a"}))
    b = WorkItem(id="b", resources=frozenset({"db_b"}))
    s = Scheduler()
    result = s.plan([a, b])
    assert len(result) == 1
    assert set(result[0]) == {"a", "b"}


def test_two_shared_resource_items_serialize() -> None:
    """Items sharing 'master_tree' must be in different batches."""
    a = WorkItem(id="a", resources=frozenset({"master_tree"}))
    b = WorkItem(id="b", resources=frozenset({"master_tree"}))
    s = Scheduler()
    result = s.plan([a, b])
    assert len(result) == 2
    # Each item must appear in exactly one batch.
    all_ids = [item for batch in result for item in batch]
    assert set(all_ids) == {"a", "b"}
    # They must not be in the same batch.
    for batch in result:
        assert len(batch) == 1


def test_dependency_ordering_across_batches() -> None:
    """b depends on a; a must appear in an earlier batch than b."""
    a = WorkItem(id="a")
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    s = Scheduler()
    result = s.plan([a, b])
    assert len(result) == 2
    assert result[0] == ["a"]
    assert result[1] == ["b"]


def test_transitive_dependency_ordering() -> None:
    """c depends on b depends on a — three separate batches."""
    a = WorkItem(id="a")
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    c = WorkItem(id="c", depends_on=frozenset({"b"}))
    s = Scheduler()
    result = s.plan([a, b, c])
    assert len(result) == 3
    assert result[0] == ["a"]
    assert result[1] == ["b"]
    assert result[2] == ["c"]


def test_fan_out_dependencies() -> None:
    """b and c both depend on a; they should go in the same batch after a."""
    a = WorkItem(id="a")
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    c = WorkItem(id="c", depends_on=frozenset({"a"}))
    s = Scheduler()
    result = s.plan([a, b, c])
    assert len(result) == 2
    assert result[0] == ["a"]
    assert set(result[1]) == {"b", "c"}


def test_fan_in_dependencies() -> None:
    """c depends on both a and b; a and b can run concurrently, c after them."""
    a = WorkItem(id="a")
    b = WorkItem(id="b")
    c = WorkItem(id="c", depends_on=frozenset({"a", "b"}))
    s = Scheduler()
    result = s.plan([a, b, c])
    assert len(result) == 2
    assert set(result[0]) == {"a", "b"}
    assert result[1] == ["c"]


# ---------------------------------------------------------------------------
# Greenfield items never block others
# ---------------------------------------------------------------------------


def test_greenfield_item_does_not_block_others() -> None:
    """A greenfield item (no resources) must never force-serialize other items."""
    # 'green' is greenfield and resource-less.
    # 'worker_a' and 'worker_b' are independent, resource-claiming items.
    # All three should fit in batch 0 since 'green' has nothing to block on.
    green = WorkItem(id="green", is_greenfield=True)
    worker_a = WorkItem(id="worker_a", resources=frozenset({"db_alpha"}))
    worker_b = WorkItem(id="worker_b", resources=frozenset({"db_beta"}))
    s = Scheduler()
    result = s.plan([green, worker_a, worker_b])
    assert len(result) == 1
    assert set(result[0]) == {"green", "worker_a", "worker_b"}


def test_greenfield_with_resource_does_not_get_special_treatment() -> None:
    """is_greenfield=True but with resources still blocks on resource contention."""
    # This is an edge case: a greenfield item that somehow claims a shared resource
    # should still serialize with any other item claiming that resource.
    green = WorkItem(id="green", is_greenfield=True, resources=frozenset({"master_tree"}))
    other = WorkItem(id="other", resources=frozenset({"master_tree"}))
    s = Scheduler()
    result = s.plan([green, other])
    assert len(result) == 2


def test_greenfield_with_depends_on_respects_ordering() -> None:
    """A greenfield item that depends on another must still come after it."""
    base = WorkItem(id="base", resources=frozenset({"db_x"}))
    green = WorkItem(id="green", is_greenfield=True, depends_on=frozenset({"base"}))
    s = Scheduler()
    result = s.plan([base, green])
    assert len(result) == 2
    assert result[0] == ["base"]
    assert result[1] == ["green"]


# ---------------------------------------------------------------------------
# Mixed scenario: independent + resource-sharing + greenfield
# ---------------------------------------------------------------------------


def test_mixed_plan() -> None:
    """
    Items:
      - alpha: needs master_tree
      - beta: needs master_tree      -> must NOT share a batch with alpha
      - gamma: needs gate
      - delta: no resources, no deps -> can batch with alpha, beta, or gamma
      - green: greenfield            -> slots freely

    Expected: alpha (or beta) can share a batch with gamma and delta/green.
    The key assertions: alpha and beta are NEVER in the same batch.
    """
    alpha = WorkItem(id="alpha", resources=frozenset({"master_tree"}))
    beta = WorkItem(id="beta", resources=frozenset({"master_tree"}))
    gamma = WorkItem(id="gamma", resources=frozenset({"gate"}))
    delta = WorkItem(id="delta")
    green = WorkItem(id="green", is_greenfield=True)

    s = Scheduler()
    result = s.plan([alpha, beta, gamma, delta, green])

    all_ids = [iid for batch in result for iid in batch]
    assert set(all_ids) == {"alpha", "beta", "gamma", "delta", "green"}

    # alpha and beta must be in separate batches.
    for batch in result:
        assert not ({"alpha", "beta"}.issubset(set(batch))), (
            f"alpha and beta must not share a batch: {result}"
        )


# ---------------------------------------------------------------------------
# Cycle detection — fail-closed
# ---------------------------------------------------------------------------


def test_cycle_two_items_raises_cycle_error() -> None:
    """a depends on b, b depends on a — cycle must raise CycleError."""
    a = WorkItem(id="a", depends_on=frozenset({"b"}))
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    s = Scheduler()
    with pytest.raises(CycleError, match="cycle"):
        s.plan([a, b])


def test_cycle_three_items_raises_cycle_error() -> None:
    """a->b->c->a — three-item cycle must raise CycleError."""
    a = WorkItem(id="a", depends_on=frozenset({"c"}))
    b = WorkItem(id="b", depends_on=frozenset({"a"}))
    c = WorkItem(id="c", depends_on=frozenset({"b"}))
    s = Scheduler()
    with pytest.raises(CycleError):
        s.plan([a, b, c])


def test_partial_cycle_raises_cycle_error() -> None:
    """Independent item plus a two-item cycle — cycle is still detected."""
    a = WorkItem(id="a")
    b = WorkItem(id="b", depends_on=frozenset({"c"}))
    c = WorkItem(id="c", depends_on=frozenset({"b"}))
    s = Scheduler()
    with pytest.raises(CycleError):
        s.plan([a, b, c])


# ---------------------------------------------------------------------------
# Unknown dependency reference
# ---------------------------------------------------------------------------


def test_unknown_depends_on_raises_value_error() -> None:
    """depends_on references a non-existent id — must raise ValueError."""
    a = WorkItem(id="a", depends_on=frozenset({"nonexistent"}))
    s = Scheduler()
    with pytest.raises(ValueError, match="unknown item"):
        s.plan([a])


# ---------------------------------------------------------------------------
# Exact batch membership: complex ordering + resource contention
# ---------------------------------------------------------------------------


def test_complex_ordering() -> None:
    """
    Items:
      - build:   no resources
      - test_a:  depends on build, needs test_runner
      - test_b:  depends on build, needs test_runner (contention with test_a)
      - deploy:  depends on test_a AND test_b

    Expected batches:
      0: [build]
      1: [test_a]   (test_b cannot join — same resource)
      2: [test_b]   (now test_a is done, resource free)
      3: [deploy]
    """
    build = WorkItem(id="build")
    test_a = WorkItem(id="test_a", depends_on=frozenset({"build"}), resources=frozenset({"test_runner"}))
    test_b = WorkItem(id="test_b", depends_on=frozenset({"build"}), resources=frozenset({"test_runner"}))
    deploy = WorkItem(id="deploy", depends_on=frozenset({"test_a", "test_b"}))

    s = Scheduler()
    result = s.plan([build, test_a, test_b, deploy])

    assert result[0] == ["build"], f"Expected batch 0 to be [build], got {result[0]}"
    # test_a and test_b must be in separate batches (resource contention).
    # deploy must come after both.
    all_ids = [iid for batch in result for iid in batch]
    assert set(all_ids) == {"build", "test_a", "test_b", "deploy"}

    deploy_batch = next(i for i, b in enumerate(result) if "deploy" in b)
    test_a_batch = next(i for i, b in enumerate(result) if "test_a" in b)
    test_b_batch = next(i for i, b in enumerate(result) if "test_b" in b)

    assert test_a_batch != test_b_batch, "test_a and test_b must be in different batches"
    assert deploy_batch > test_a_batch
    assert deploy_batch > test_b_batch
