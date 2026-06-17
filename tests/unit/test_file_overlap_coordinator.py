"""TDD tests for the in-product file-overlap coordinator (issue #31).

Covers:
  - Two roles with OVERLAPPING paths serialize (the second waits for the first).
  - Two roles with DISJOINT paths run CONCURRENTLY (no serialization).
  - The TIMEOUT path: a waiter that can never clear raises OverlapTimeout
    rather than blocking forever.
  - ORDERED ACQUISITION: two roles each wanting the same two paths in opposite
    declared order never deadlock (both complete).
  - MERGE-AWARE waiting: a waiter blocks until the overlapping role releases
    with merged=True, not merely on completion.
  - Glob overlap detection (literal-vs-glob and glob-vs-glob).
  - Scheduler integration: paths_to_resources folds file overlap into the
    existing WorkItem.resources model so the scheduler serializes same-path
    roles WITHOUT any scheduler change.
"""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.coordination.file_overlap import (
    FileOverlapCoordinator,
    OverlapTimeout,
    globs_overlap,
    paths_to_resources,
)
from general_ludd.scheduling.scheduler import Scheduler, WorkItem, can_run_concurrently

# pytest-asyncio runs in auto mode (see pyproject.toml asyncio_mode = "auto"),
# so async test functions are collected and run without a per-test marker.


# ---------------------------------------------------------------------------
# glob overlap helper (filesystem-free)
# ---------------------------------------------------------------------------


def test_glob_overlap_identical_literals() -> None:
    assert globs_overlap("src/foo.py", "./src/foo.py") is True


def test_glob_overlap_literal_vs_glob() -> None:
    assert globs_overlap("src/foo.py", "src/*.py") is True
    assert globs_overlap("src/sub/foo.py", "src/*.py") is False


def test_glob_overlap_disjoint() -> None:
    assert globs_overlap("src/a.py", "src/b.py") is False
    assert globs_overlap("src/aaa/*.py", "src/bbb/*.py") is False


def test_glob_overlap_dir_prefix() -> None:
    # A directory literal contains a file beneath it.
    assert globs_overlap("src/pkg", "src/pkg/mod.py") is True


# ---------------------------------------------------------------------------
# overlapping paths serialize
# ---------------------------------------------------------------------------


async def test_overlapping_paths_serialize() -> None:
    """The second role must not enter its critical section until the first
    role has released the overlapping path."""
    coord = FileOverlapCoordinator(default_timeout=2.0)
    order: list[str] = []
    a_in_section = asyncio.Event()
    a_may_release = asyncio.Event()

    async def role_a() -> None:
        async with coord.guard("A", ["src/shared.py"]):
            order.append("A-enter")
            a_in_section.set()
            await a_may_release.wait()
            order.append("A-exit")

    async def role_b() -> None:
        await a_in_section.wait()  # ensure A is already inside
        async with coord.guard("B", ["src/shared.py"]):
            order.append("B-enter")

    ta = asyncio.ensure_future(role_a())
    tb = asyncio.ensure_future(role_b())
    await a_in_section.wait()
    # Give B a chance to (wrongly) enter — it must still be blocked.
    await asyncio.sleep(0.05)
    assert "B-enter" not in order, "B entered while A held the overlapping path"
    a_may_release.set()
    await asyncio.gather(ta, tb)

    assert order == ["A-enter", "A-exit", "B-enter"]


# ---------------------------------------------------------------------------
# disjoint paths run concurrently
# ---------------------------------------------------------------------------


async def test_disjoint_paths_run_concurrently() -> None:
    """Two roles with non-overlapping paths must both be inside their critical
    sections at the same time."""
    coord = FileOverlapCoordinator(default_timeout=2.0)
    both_in = asyncio.Event()
    inside = 0

    async def role(path: str) -> None:
        nonlocal inside
        async with coord.guard(path, [path]):
            inside += 1
            if inside == 2:
                both_in.set()
            await asyncio.wait_for(both_in.wait(), timeout=1.0)

    await asyncio.gather(role("src/a.py"), role("src/b.py"))
    assert both_in.is_set(), "disjoint roles did not run concurrently"


# ---------------------------------------------------------------------------
# timeout path
# ---------------------------------------------------------------------------


async def test_wait_for_clear_times_out() -> None:
    """A waiter whose overlap never clears must raise OverlapTimeout, not hang."""
    coord = FileOverlapCoordinator(default_timeout=0.1)
    coord.register_active("holder", ["src/locked.py"])  # never released

    with pytest.raises(OverlapTimeout):
        await coord.wait_for_clear(["src/locked.py"], timeout=0.1)


async def test_guard_acquire_times_out() -> None:
    """guard() entry must time out if the overlapping holder never releases."""
    coord = FileOverlapCoordinator(default_timeout=0.1)
    coord.register_active("holder", ["src/locked.py"])

    with pytest.raises(OverlapTimeout):
        async with coord.guard("waiter", ["src/locked.py"], timeout=0.1):
            pass  # pragma: no cover - must not be reached


# ---------------------------------------------------------------------------
# ordered acquisition -> no deadlock
# ---------------------------------------------------------------------------


async def test_ordered_acquisition_no_deadlock() -> None:
    """Two roles each wanting the same two paths in OPPOSITE declared order must
    both complete — ordered (sorted) acquisition makes a lock-ordering deadlock
    impossible."""
    coord = FileOverlapCoordinator(default_timeout=2.0)
    completed: list[str] = []

    async def role(name: str, paths: list[str]) -> None:
        async with coord.guard(name, paths):
            await asyncio.sleep(0.02)
            completed.append(name)

    # A declares [x, y]; B declares [y, x] — opposite orders.
    await asyncio.wait_for(
        asyncio.gather(
            role("A", ["src/x.py", "src/y.py"]),
            role("B", ["src/y.py", "src/x.py"]),
        ),
        timeout=1.5,
    )
    assert sorted(completed) == ["A", "B"]


# ---------------------------------------------------------------------------
# merge-aware waiting
# ---------------------------------------------------------------------------


async def test_merge_aware_waits_for_merge_not_just_completion() -> None:
    """A merge-aware waiter must stay blocked after a plain release and resume
    only once the overlapping role releases with merged=True."""
    coord = FileOverlapCoordinator(default_timeout=2.0)
    coord.register_active("producer", ["src/shared.py"])

    resumed = asyncio.Event()

    async def consumer() -> None:
        await coord.wait_for_clear(
            ["src/shared.py"], merge_aware=True, timeout=2.0
        )
        resumed.set()

    task = asyncio.ensure_future(consumer())
    await asyncio.sleep(0.05)
    # Plain (un-merged) release: merge-aware consumer must NOT resume yet.
    coord.release("producer", merged=False)
    await asyncio.sleep(0.05)
    assert not resumed.is_set(), "merge-aware waiter resumed before merge"

    # Now signal the merge landed.
    coord.release("producer", merged=True)
    await asyncio.wait_for(task, timeout=1.0)
    assert resumed.is_set()


async def test_non_merge_aware_clears_on_plain_release() -> None:
    """A non-merge-aware waiter clears as soon as the overlapping role releases,
    even without a merge signal."""
    coord = FileOverlapCoordinator(default_timeout=2.0)
    coord.register_active("producer", ["src/shared.py"])

    async def consumer() -> None:
        await coord.wait_for_clear(["src/shared.py"], merge_aware=False, timeout=2.0)

    task = asyncio.ensure_future(consumer())
    await asyncio.sleep(0.05)
    coord.release("producer", merged=False)
    await asyncio.wait_for(task, timeout=1.0)  # must complete


# ---------------------------------------------------------------------------
# overlaps() reporting + active snapshot
# ---------------------------------------------------------------------------


async def test_overlaps_reporting() -> None:
    coord = FileOverlapCoordinator()
    coord.register_active("A", ["src/foo.py", "src/bar.py"])
    coord.register_active("B", ["src/bar.py"])
    coord.register_active("C", ["src/baz.py"])

    assert coord.overlaps(["src/bar.py"]) == ["A", "B"]
    assert coord.overlaps(["src/baz.py"]) == ["C"]
    assert coord.overlaps(["src/none.py"]) == []
    # role_id excludes self.
    assert coord.overlaps(["src/bar.py"], role_id="A") == ["B"]


# ---------------------------------------------------------------------------
# scheduler integration
# ---------------------------------------------------------------------------


def test_paths_to_resources_maps_globs_to_resource_tokens() -> None:
    res = paths_to_resources(["src/foo.py", "./src/foo.py", "src/bar.py"])
    # ./ normalized so the two spellings collapse to one resource.
    assert res == {"file:src/foo.py", "file:src/bar.py"}


def test_scheduler_serializes_same_path_roles_via_resources() -> None:
    """Folding paths_to_resources into WorkItem.resources makes the EXISTING
    scheduler serialize two roles that name the same file — no scheduler change."""
    a = WorkItem(id="A", resources=paths_to_resources(["src/shared.py"]))
    b = WorkItem(id="B", resources=paths_to_resources(["src/shared.py"]))
    assert can_run_concurrently(a, b) is False

    batches = Scheduler().plan([a, b])
    # Different batches => serialized.
    assert [sorted(batch) for batch in batches] == [["A"], ["B"]]


def test_scheduler_allows_disjoint_path_roles_to_share_a_batch() -> None:
    a = WorkItem(id="A", resources=paths_to_resources(["src/a.py"]))
    b = WorkItem(id="B", resources=paths_to_resources(["src/b.py"]))
    assert can_run_concurrently(a, b) is True

    batches = Scheduler().plan([a, b])
    assert len(batches) == 1
    assert sorted(batches[0]) == ["A", "B"]
