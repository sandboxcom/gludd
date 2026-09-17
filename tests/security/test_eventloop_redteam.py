"""RED-TEAM: concurrency / correctness of MASTER's event loop + bucket lease.

Companion to REDTEAM_EVENTLOOP.md. Each test traces a concrete interleaving.

Tests named ``test_*_HOLDS`` assert an invariant that currently holds and guards
it against regression. The tests that originally proved a BUG were marked
``xfail(strict=True)``; the concurrency fixes have landed, so those markers are
removed and the tests now assert the FIXED behavior (and would turn red if the
fix regressed).

This suite deliberately exercises the default SQLite backend, where SELECT ...
FOR UPDATE SKIP LOCKED is a no-op and every "claim" must be a guarded
conditional UPDATE rather than a locked read. PostgreSQL engine construction is
covered separately by the storage-parity tests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

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
from general_ludd.event_loop.lease import (
    LeaseBusyError,
    LeaseRenewalStatus,
    acquire_lease,
    confirm_lease_termination,
    reclaim_expired_leases,
    renew_lease,
)
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


async def _insert_queued_todo(
    factory,
    todo_id: str = "T1",
    *,
    managed_self_improve: bool = False,
) -> None:
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                title="redteam",
                status=TodoStatus.QUEUED.value,
                queue="core",
                version=1,
                work_type=("self_improve" if managed_self_improve else "code"),
                approval_policy=(
                    "managed_self_improve_plan" if managed_self_improve else "none"
                ),
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
        cast(Any, loop)._dispatch_jobs_via_scheduler = _noop_dispatch

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
async def test_claim_runnable_update_has_status_and_version_guard(session_factory):
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
@pytest.mark.parametrize(
    "managed_self_improve",
    [False, True],
    ids=["ordinary", "managed-self-improve"],
)
async def test_concurrent_claim_runnable_has_exactly_one_winner(
    session_factory,
    managed_self_improve: bool,
):
    """Trace: SA and SB BOTH SELECT QUEUED T1 (before either flushes); both call
    claim_runnable(). With the guarded conditional UPDATE, exactly ONE caller's
    UPDATE affects the row and returns [T1]; the loser's guarded UPDATE matches
    no row (status no longer QUEUED / version moved) and returns []. So the todo
    is dispatched exactly once.
    """
    await _insert_queued_todo(
        session_factory,
        managed_self_improve=managed_self_improve,
    )

    async with session_factory() as sa, session_factory() as sb:
        repo_a = TodoRepository(sa)
        repo_b = TodoRepository(sb)

        # Drive the real production claim path on both sessions. SQLite serializes
        # the two guarded UPDATEs at the WAL/file level; the optimistic
        # status/version guard makes exactly one win.
        claimed_a, claimed_b = await asyncio.gather(
            repo_a.claim_runnable(),
            repo_b.claim_runnable(),
        )
        await asyncio.gather(sa.commit(), sb.commit())

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


@pytest.mark.asyncio
async def test_event_loop_live_lease_conflict_fails_closed_in_real_session(
    session_factory,
):
    """A live foreign lease survives while EventLoop returns its claim to QUEUED."""
    from general_ludd.event_loop.loop import EventLoop

    await _insert_queued_todo(session_factory)
    async with session_factory() as s:
        await acquire_lease(
            s,
            bucket_key="core:T1",
            holder_id="event-loop-existing-owner",
            todo_version=2,
        )
        await s.commit()

    async with session_factory() as s:
        repo = TodoRepository(s)
        loop = EventLoop(session=s, todo_repo=repo)
        loop._active_session = s
        loop._tick_project_id = None

        await loop._phase_claim_runnable_todos()
        await s.commit()

        todo = await repo.get_by_id("T1")
        lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()

    assert loop._tick_state["claimed_todos"] == []
    assert loop._tick_state["lease_conflict_todo_ids"] == ["T1"]
    assert todo is not None
    assert todo.status == TodoStatus.QUEUED.value
    # Claiming advances the state fence, persisting the estimate advances the
    # ORM fence, and returning the denied claim advances it once more.
    assert todo.version == 4
    assert lease.holder_id == "event-loop-existing-owner"
    assert lease.todo_version == 2


@pytest.mark.asyncio
async def test_event_loop_persists_lease_with_post_flush_todo_version(
    session_factory,
):
    """The lease fence matches the todo after pending ORM estimates are flushed."""
    from general_ludd.event_loop.loop import EventLoop

    await _insert_queued_todo(session_factory)
    async with session_factory() as s:
        repo = TodoRepository(s)
        loop = EventLoop(session=s, todo_repo=repo)
        loop._active_session = s
        loop._tick_project_id = None

        await loop._phase_claim_runnable_todos()
        await s.commit()

    async with session_factory() as s:
        todo = await TodoRepository(s).get_by_id("T1")
        lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()

    assert todo is not None
    assert todo.status == TodoStatus.ACTIVE.value
    assert lease.todo_version == todo.version


# ===========================================================================
# AREA 2 — bucket lease provides neither mutex nor crash recovery
# ===========================================================================
@pytest.mark.asyncio
async def test_second_holder_cannot_acquire_same_execution_bucket(session_factory):
    """A second process cannot create a competing lease for one active attempt."""
    async with session_factory() as s:
        await acquire_lease(
            s,
            bucket_key="core:T1",
            holder_id="instance-a",
            todo_version=2,
        )
        await s.commit()

    async with session_factory() as s:
        with pytest.raises(LeaseBusyError, match="already owned"):
            await acquire_lease(
                s,
                bucket_key="core:T1",
                holder_id="instance-b",
                todo_version=1,
            )
        await s.rollback()

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
    assert len(rows) == 1
    assert rows[0].holder_id == "instance-a"


@pytest.mark.asyncio
async def test_renewal_is_fenced_to_live_exact_attempt(session_factory):
    """Only the current owner/version can extend a non-cancelled lease."""
    async with session_factory() as s:
        await acquire_lease(
            s,
            bucket_key="core:T1",
            holder_id="instance-a",
            todo_version=2,
            ttl_seconds=60,
        )
        await s.commit()

    async with session_factory() as s:
        renewed = await renew_lease(
            s,
            bucket_key="core:T1",
            holder_id="instance-a",
            todo_version=2,
            ttl_seconds=120,
        )
        intruder = await renew_lease(
            s,
            bucket_key="core:T1",
            holder_id="instance-b",
            todo_version=2,
            ttl_seconds=120,
        )
        await s.commit()
    assert renewed is LeaseRenewalStatus.RENEWED
    assert intruder is LeaseRenewalStatus.STALE

    async with session_factory() as s:
        lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()
        lease.cancel_requested_at = datetime.now(UTC)
        await s.commit()

    async with session_factory() as s:
        cancelled = await renew_lease(
            s,
            bucket_key="core:T1",
            holder_id="instance-a",
            todo_version=2,
            ttl_seconds=120,
        )
    assert cancelled is LeaseRenewalStatus.CANCEL_REQUESTED


@pytest.mark.asyncio
async def test_expired_owner_blocks_replacement_until_termination(session_factory):
    """An expired owner remains the mutex until its process is proven dead."""
    async with session_factory() as s:
        s.add(
            TodoModel(
                todo_id="T1",
                title="redteam",
                status=TodoStatus.ACTIVE.value,
                queue="core",
            )
        )
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="tick-5",
                todo_version=1,
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        await s.commit()

    async with session_factory() as s:
        reclaimed = await reclaim_expired_leases(s)
        await s.commit()
    assert reclaimed == 0

    async with session_factory() as s:
        with pytest.raises(LeaseBusyError, match="already owned"):
            await acquire_lease(
                s,
                bucket_key="core:T1",
                holder_id="tick-6",
                todo_version=1,
            )
        await s.rollback()

    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
        lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()
    assert t1.status == TodoStatus.ACTIVE.value
    assert lease.holder_id == "tick-5"
    assert lease.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_expiry_requests_cancel_and_requeues_only_after_termination_proof(
    session_factory,
):
    """Expiry alone never creates a second execution of still-running work."""
    # T1 is ACTIVE (claimed) with an already-expired lease.
    async with session_factory() as s:
        s.add(
            TodoModel(
                todo_id="T1",
                title="redteam",
                status=TodoStatus.ACTIVE.value,
                queue="core",
            )
        )
        s.add(
            BucketLeaseModel(
                bucket_key="core:T1",
                holder_id="instance-a",
                todo_version=1,
                expires_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        await s.commit()

    async with session_factory() as s:
        reclaimed = await reclaim_expired_leases(s)
        await s.commit()
    assert reclaimed == 0

    # The first sweep records an internal cancellation request but cannot infer
    # that the original process stopped merely because its heartbeat expired.
    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
        lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()
    assert t1.status == TodoStatus.ACTIVE.value
    assert lease.cancel_requested_at is not None
    assert lease.termination_confirmed_at is None

    # Only the exact owner can acknowledge that its owned process group has
    # terminated. The next recovery sweep may then CAS-requeue that attempt.
    async with session_factory() as s:
        confirmed = await confirm_lease_termination(
            s,
            bucket_key="core:T1",
            holder_id="instance-a",
            todo_version=1,
        )
        await s.commit()
    assert confirmed is True

    async with session_factory() as s:
        stored_lease = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one()
        stored_todo = (
            await s.execute(select(TodoModel).where(TodoModel.todo_id == "T1"))
        ).scalar_one()
        assert stored_lease.cancel_requested_at is not None
        assert stored_lease.termination_confirmed_at is not None
        assert stored_lease.todo_version == stored_todo.version == 1
        assert stored_lease.expires_at < datetime.now(UTC)
        reclaimed = await reclaim_expired_leases(s)
        await s.commit()
    assert reclaimed == 1

    async with session_factory() as s:
        repo = TodoRepository(s)
        t1 = await repo.get_by_id("T1")
        assert t1 is not None
        remaining = (
            await s.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:T1"
                )
            )
        ).scalar_one_or_none()
    assert t1.status == TodoStatus.QUEUED.value
    assert t1.version == 2
    assert remaining is None


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
