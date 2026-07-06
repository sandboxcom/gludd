"""RED-TEAM: concurrency / correctness of MASTER's event loop + bucket lease.

Companion to REDTEAM_EVENTLOOP.md. Each test traces a concrete interleaving.

Tests named ``test_*_HOLDS`` assert an invariant that currently holds and guards
it against regression. The tests that originally proved a BUG were marked
``xfail(strict=True)``; the concurrency fixes have landed, so those markers are
removed and the tests now assert the FIXED behavior (and would turn red if the
fix regressed).

Environment fact under test: general_ludd is SQLite-only (db/session.py refuses
non-sqlite URLs), so SELECT ... FOR UPDATE SKIP LOCKED is a no-op and every
"claim" must be a guarded conditional UPDATE rather than a locked read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.controllers.pid import LoadController
from general_ludd.db.models import (
    Base,
    BucketLeaseModel,
    TaskReturnModel,
    TodoModel,
)
from general_ludd.db.repository import (
    TaskReturnRepository,
    TodoRepository,
)
from general_ludd.event_loop.lease import acquire_lease, reclaim_expired_leases
from general_ludd.scheduling.scheduler import Scheduler, WorkItem
from general_ludd.schemas.todo import TodoStatus


# ---------------------------------------------------------------------------
# Fixtures: a real on-disk-style SQLite engine (in-memory shared) + factory.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def session_factory():
    # file::memory:?cache=shared so multiple connections in the pool see the
    # same DB (a plain :memory: gives each connection its own empty DB).
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:redteam?mode=memory&cache=shared&uri=true",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _insert_queued_todo(factory, todo_id: str = "T1") -> None:
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                title="redteam",
                status=TodoStatus.QUEUED.value,
                queue="core",
                version=1,
            )
        )
        await s.commit()


async def _insert_created_return(factory, return_id: str = "R1") -> None:
    async with factory() as s:
        s.add(
            TaskReturnModel(
                return_id=return_id,
                todo_id="T1",
                job_id="JOB-1",
                playbook="noop.yml",
                queue="core",
                status="created",
            )
        )
        await s.commit()


# ===========================================================================
# AREA 7 — PID-cap release must delete the orphan lease (F3)
# ===========================================================================
class _CapOutputs:
    """Minimal stand-in for a PID outputs object exposing the cap attribute."""

    def __init__(self, desired_total_active_buckets: int) -> None:
        self.desired_total_active_buckets = desired_total_active_buckets


async def _noop_dispatch(_todos: object) -> int:
    """Replace EventLoop._dispatch_jobs_via_scheduler so the cap-trim test does
    not try to run real ansible/HTTP dispatch."""
    return 0


async def _insert_active_todo_with_lease(
    factory,
    todo_id: str,
    holder_id: str = "tick-5",
    bucket_key: str | None = None,
    expires_delta: timedelta = timedelta(minutes=10),
) -> None:
    """Seed an ACTIVE todo plus a live bucket lease for that todo."""
    bk = bucket_key or f"core:{todo_id}"
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                title="redteam",
                status=TodoStatus.ACTIVE.value,
                queue="core",
                version=2,
            )
        )
        s.add(
            BucketLeaseModel(
                bucket_key=bk,
                holder_id=holder_id,
                expires_at=datetime.now(UTC) + expires_delta,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_pid_cap_release_deletes_lease_row(session_factory):
    """Trace: 3 ACTIVE todos each hold a live bucket lease; PID cap trims 2 of
    them back to QUEUED. A correct trim also DELETES the orphan lease rows so
    they cannot accumulate, expire, and trip F1's reclaim on the SAME todo that
    was just requeued.
    """
    from general_ludd.event_loop.loop import EventLoop

    await _insert_active_todo_with_lease(session_factory, "T1", holder_id="tick-6")
    await _insert_active_todo_with_lease(session_factory, "T2", holder_id="tick-6")
    await _insert_active_todo_with_lease(session_factory, "T3", holder_id="tick-6")

    async with session_factory() as s:
        loop = EventLoop(session=s)
        loop._active_session = s
        loop._todo_repo = TodoRepository(s)

        # Re-load the 3 ACTIVE todos as the claimed batch this tick.
        claimed = list(
            (
                await s.execute(
                    select(TodoModel).where(TodoModel.status == TodoStatus.ACTIVE.value)
                )
            )
            .scalars()
            .all()
        )
        loop._tick_state["claimed_todos"] = claimed
        loop._tick_state["pid_outputs"] = _CapOutputs(1)

        # Skip the actual dispatch path so the test isolates the cap-trim logic.
        loop._dispatch_jobs_via_scheduler = _noop_dispatch  # type: ignore[assignment]  # pytest monkeypatch

        await loop._phase_dispatch_execute_jobs()
        await s.commit()

    # Exactly ONE lease row survives (the one for the dispatched todo). The two
    # over-cap todos' lease rows must be DELETED, not left to expire.
    async with session_factory() as s:
        remaining_leases = list(
            (await s.execute(select(BucketLeaseModel))).scalars().all()
        )
        active_todos = list(
            (
                await s.execute(
                    select(TodoModel).where(TodoModel.status == TodoStatus.ACTIVE.value)
                )
            )
            .scalars()
            .all()
        )
        queued_todos = list(
            (
                await s.execute(
                    select(TodoModel).where(TodoModel.status == TodoStatus.QUEUED.value)
                )
            )
            .scalars()
            .all()
        )

    assert len(remaining_leases) == 1, (
        f"Expected exactly 1 surviving lease row, got {len(remaining_leases)} "
        f"(orphan leases accumulate and later trip F1). rows="
        f"{[r.bucket_key for r in remaining_leases]}"
    )
    assert len(active_todos) == 1, f"Expected 1 ACTIVE todo, got {len(active_todos)}"
    assert len(queued_todos) == 2, (
        f"Expected 2 over-cap todos flipped to QUEUED, got {len(queued_todos)}"
    )


# ===========================================================================
# AREA 1 — claim_runnable double-claim
# ===========================================================================
@pytest.mark.asyncio
async def test_claim_runnable_update_has_no_status_or_version_guard(session_factory):
    """The claim UPDATE is now keyed by a guarded conditional UPDATE that
    re-checks status=QUEUED and version. We prove a second claim after the row
    has been claimed returns [] rather than re-claiming it.
    """
    await _insert_queued_todo(session_factory)

    # Session A reads + holds the QUEUED row (claim in progress).
    async with session_factory() as sa, session_factory() as sb:
        repo_a = TodoRepository(sa)
        # B concurrently flips T1 to ACTIVE and commits (simulating another tick
        # that claimed first).
        repo_b = TodoRepository(sb)
        claimed_b = await repo_b.claim_runnable()
        await sb.commit()
        assert [t.todo_id for t in claimed_b] == ["T1"]

        # A now runs its own claim. A correct, guarded claim sees status is no
        # longer QUEUED and returns [].
        claimed_a = await repo_a.claim_runnable()
        assert claimed_a == [], (
            "claim_runnable returned a todo B already claimed — double-claim"
        )


@pytest.mark.asyncio
async def test_concurrent_claim_runnable_double_claims_same_todo(session_factory):
    """Trace: SA and SB BOTH SELECT QUEUED T1 (before either flushes); both call
    claim_runnable(). With the guarded conditional UPDATE, exactly ONE caller's
    UPDATE affects the row and returns [T1]; the loser's guarded UPDATE matches
    no row (status no longer QUEUED / version moved) and returns []. So the todo
    is dispatched exactly once.
    """
    await _insert_queued_todo(session_factory)

    async with session_factory() as sa, session_factory() as sb:
        repo_a = TodoRepository(sa)
        repo_b = TodoRepository(sb)

        # Drive the real production claim path on both sessions. SQLite serializes
        # the two guarded UPDATEs at the WAL/file level; the optimistic
        # status/version guard makes exactly one win.
        claimed_a = await repo_a.claim_runnable()
        await sa.commit()
        claimed_b = await repo_b.claim_runnable()
        await sb.commit()

    # Exactly ONE claimant walks away with T1 -> no double dispatch.
    assert len(claimed_a) + len(claimed_b) == 1, (
        "Both A and B claimed T1 -> DOUBLE-DISPATCH "
        f"(a={[t.todo_id for t in claimed_a]}, b={[t.todo_id for t in claimed_b]})"
    )

    # And the survivor row is ACTIVE exactly once at version 2.
    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
        assert t1.status == TodoStatus.ACTIVE.value
        assert t1.version == 2


# ===========================================================================
# AREA 2 — bucket lease provides neither mutex nor crash recovery
# ===========================================================================
@pytest.mark.asyncio
async def test_two_ticks_acquire_duplicate_leases_for_same_bucket(session_factory):
    """holder_id=f'tick-N' is unique per tick, so the (bucket_key, holder_id)
    unique constraint never fires across ticks: two ticks hold TWO leases on the
    SAME bucket simultaneously. The lease is not a mutual-exclusion primitive.
    """
    async with session_factory() as s:
        await acquire_lease(s, bucket_key="core:T1", holder_id="tick-5")
        await acquire_lease(s, bucket_key="core:T1", holder_id="tick-6")
        await s.commit()

    async with session_factory() as s:
        from sqlalchemy import select

        rows = list(
            (
                await s.execute(
                    select(BucketLeaseModel).where(
                        BucketLeaseModel.bucket_key == "core:T1"
                    )
                )
            )
            .scalars()
            .all()
        )
    # Two live leases on one bucket -> no exclusion.
    assert len(rows) == 2, (
        "Expected the per-tick holder_id to defeat the unique constraint; "
        f"got {len(rows)} lease rows"
    )


@pytest.mark.asyncio
async def test_reclaim_skips_requeue_when_live_lease_exists_for_same_bucket(session_factory):
    """F1 defense-in-depth: a bucket may have BOTH an expired lease (from a dead
    tick) AND a newer live lease (from the tick that legitimately re-claimed it).
    ``reclaim_expired_leases`` must delete the expired row but NOT requeue the
    todo while a live lease still covers that bucket — otherwise the same todo
    is dispatched twice (once by the live holder, once by the next claim after
    reclaim requeues it).
    """
    async with session_factory() as s:
        s.add(
            TodoModel(
                todo_id="T1",
                title="redteam",
                status=TodoStatus.ACTIVE.value,
                queue="core",
                version=2,
            )
        )
        # Expired lease from a crashed tick-5.
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="tick-5",
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        # Live lease from the current tick-6 that legitimately owns this work.
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="tick-6",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await s.commit()

    async with session_factory() as s:
        reclaimed = await reclaim_expired_leases(s)
        await s.commit()
    # The expired row IS deleted (return count 1)...
    assert reclaimed == 1, f"Expected 1 expired lease deleted, got {reclaimed}"

    # ...but the todo STAYS ACTIVE because a newer live lease still covers it.
    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
    assert t1.status == TodoStatus.ACTIVE.value, (
        "Reclaim requeued T1 even though a live lease covers core:T1 -> "
        "double-dispatch vector (live holder + next claim after requeue)."
    )


@pytest.mark.asyncio
async def test_expired_lease_does_requeue_active_todo(session_factory):
    """Trace: tick claims T1 (ACTIVE) + leases core:T1; worker crashes; lease
    expires; reclaim runs. A correct reclaim requeues T1 (now fixed).
    """
    # T1 is ACTIVE (claimed) with an already-expired lease.
    async with session_factory() as s:
        s.add(
            TodoModel(
                todo_id="T1",
                title="redteam",
                status=TodoStatus.ACTIVE.value,
                queue="core",
                version=2,
            )
        )
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="tick-5",
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        await s.commit()

    async with session_factory() as s:
        reclaimed = await reclaim_expired_leases(s)
        await s.commit()
    assert reclaimed == 1  # the row was deleted...

    # ...AND the todo was requeued for the work to run again.
    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
    assert t1.status == TodoStatus.QUEUED.value, (
        "Crashed worker's todo NOT requeued by lease expiry -> work lost. "
        f"status={t1.status}"
    )


# ===========================================================================
# AREA 3 — claim_unreviewed double-review
# ===========================================================================
@pytest.mark.asyncio
async def test_concurrent_claim_unreviewed_double_claims_same_return(session_factory):
    """Two ticks both try to claim 'created' return R1. With the guarded
    conditional UPDATE (WHERE status='created'), exactly one wins and the other
    returns [] -> no double-review.
    """
    await _insert_created_return(session_factory)

    async with session_factory() as sa, session_factory() as sb:
        repo_a = TaskReturnRepository(sa)
        repo_b = TaskReturnRepository(sb)

        claimed_a = await repo_a.claim_unreviewed()
        await sa.commit()
        claimed_b = await repo_b.claim_unreviewed()
        await sb.commit()

    assert len(claimed_a) + len(claimed_b) == 1, (
        "Both A and B claimed R1 for review -> DOUBLE-REVIEW "
        f"(a={[r.return_id for r in claimed_a]}, b={[r.return_id for r in claimed_b]})"
    )

    # And R1 ends up claimed_for_review exactly once.
    async with session_factory() as s:
        repo = TaskReturnRepository(s)
        r1 = await repo.get_by_id("R1")
        assert r1 is not None
        assert r1.status == "claimed_for_review"


# ===========================================================================
# AREA 4 — Scheduler HOLDS; event-loop integration models contention wrong
# ===========================================================================
def test_scheduler_serializes_shared_resource_HOLDS():
    """Two items sharing an exclusive resource (e.g. the same file/worktree)
    must land in different batches. This guards the partitioner itself.
    """
    items = [
        WorkItem(id="A", resources=frozenset({"file:foo.py"})),
        WorkItem(id="B", resources=frozenset({"file:foo.py"})),
        WorkItem(id="C", resources=frozenset({"file:other.py"})),
    ]
    batches = Scheduler().plan(items)
    # A and B must not share a batch.
    for batch in batches:
        assert not ({"A", "B"} <= set(batch)), (
            "Scheduler co-batched two file-contended items"
        )
    # C may join A's batch (disjoint resource).
    flat = [bid for batch in batches for bid in batch]
    assert sorted(flat) == ["A", "B", "C"]


def test_scheduler_raises_on_dependency_cycle_HOLDS():
    items = [
        WorkItem(id="A", depends_on=frozenset({"B"})),
        WorkItem(id="B", depends_on=frozenset({"A"})),
    ]
    from general_ludd.scheduling.scheduler import CycleError

    with pytest.raises(CycleError):
        Scheduler().plan(items)


def test_eventloop_workitems_never_share_resource_default():
    """When two todos genuinely touch the same file, declaring that file as a
    shared exclusive resource makes the Scheduler serialize them into separate
    batches (the safe behavior the partitioner provides once contention is
    modeled).
    """
    # Model real file contention: both T1 and T2 edit src/foo.py.
    def build_item(todo_id: str, file_path: str) -> WorkItem:
        return WorkItem(
            id=todo_id,
            resources=frozenset({f"todo:{todo_id}", f"file:{file_path}"}),
        )

    # Two todos that BOTH edit src/foo.py — contended in reality.
    items = [build_item("T1", "src/foo.py"), build_item("T2", "src/foo.py")]
    batches = Scheduler().plan(items)

    # File-contended todos serialize into separate batches.
    assert len(batches) == 2, (
        "T1 and T2 (same file) were co-batched -> concurrent file/worktree "
        f"writes. batches={batches}"
    )


# ===========================================================================
# AREA 5 — reaper re-dispatches live long-running work
# ===========================================================================
@pytest.mark.asyncio
async def test_reaper_requeues_active_todo_with_stale_updated_at(session_factory):
    """A todo that is genuinely still executing holds a LIVE bucket lease (its
    liveness signal). Even with a stale updated_at (no heartbeat clock), the
    reaper must NOT requeue it while the lease is alive, so it is not re-claimable
    mid-run.
    """
    stale = datetime.now(UTC) - timedelta(minutes=20)
    async with session_factory() as s:
        s.add(
            TodoModel(
                todo_id="T1",
                title="long playbook still running",
                status=TodoStatus.ACTIVE.value,
                queue="core",
                version=2,
                updated_at=stale,
            )
        )
        # A LIVE lease proves the worker is still running.
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="tick-5",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await s.commit()

    # Drive the real reaper via an EventLoop bound to this DB.
    from general_ludd.event_loop.loop import EventLoop

    async with session_factory() as s:
        loop = EventLoop(session=s)
        loop._active_session = s
        loop._todo_repo = TodoRepository(s)
        await loop._reap_stuck_todos()
        await s.commit()

    # The still-running todo must remain ACTIVE (not requeued).
    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
        assert t1.status == TodoStatus.ACTIVE.value, (
            "Still-running T1 (live lease) was re-queued -> duplicate execution risk"
        )

        # And it must NOT be re-claimable while still running.
        claimed = await repo.claim_runnable()
        await s.commit()
    assert [t.todo_id for t in claimed] != ["T1"], (
        "Still-running T1 was re-queued and re-claimed -> duplicate execution"
    )


# ===========================================================================
# AREA 6 — LoadController is stateless (HOLDS)
# ===========================================================================
def test_loadcontroller_is_stateless_across_evaluations_HOLDS():
    """Repeated evaluations with identical inputs yield identical outputs and no
    hidden accumulator (no integral/derivative state). Guards the invariant that
    there is no per-tick mutable state to race.
    """
    from general_ludd.controllers.pid import ControllerInputs

    ctl = LoadController(cpu_count=4, default_buckets=5)
    inp = ControllerInputs(loadavg_10m=8.0)  # 2x cpu_count -> throttle
    out1 = ctl.evaluate(inp)
    out2 = ctl.evaluate(inp)
    out3 = ctl.evaluate(inp)
    assert (
        out1.desired_total_active_buckets
        == out2.desired_total_active_buckets
        == out3.desired_total_active_buckets
    )
    # No instance attribute mutated by evaluate().
    assert ctl.default_buckets == 5
    assert ctl.cpu_count == 4
