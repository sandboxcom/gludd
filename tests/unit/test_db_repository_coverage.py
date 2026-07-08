"""WP-C1 behavioral coverage lift for db/repository + event_loop/lease.

Targets the uncovered paths identified in the WP-C1 coverage audit:

* ``TodoRepository.update()`` mass-assignment guard (security finding #10) —
  extends test_repository_update_guard.py with edge cases (empty payload,
  unknown-tenant path, class-level validator).
* ``TaskReturnRepository`` project_id scoping across ``work_summary`` /
  ``history_summary`` / ``claim_unreviewed`` — previously had no unit tests.
* ``QueueRepository`` basic create/get/list + empty-result contract.
* ``event_loop.lease.{acquire_lease, reclaim_expired_leases, release_lease}``
  — the queue lease acquire + reclaim surface that backs
  ``EventLoop._phase_*`` (no prior unit-level coverage).

Notes
-----
* asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio needed.
* Mirror of the async-session fixture pattern from
  test_repository_update_guard.py / test_repository_create_guard.py.
* The DB is created with ``Base.metadata.create_all`` and no PRAGMA
  foreign_keys, so we can seed tenant-scoped rows without parent project
  rows (same pattern as the existing security tests).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import (
    QueueRepository,
    TaskReturnRepository,
    TodoRepository,
)
from general_ludd.event_loop.lease import (
    acquire_lease,
    reclaim_expired_leases,
    release_lease,
)
from general_ludd.schemas.todo import TodoStatus


# ---------------------------------------------------------------------------
# Shared async-engine / session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


def _todo_data(
    title: str = "seed",
    project_id: str | None = None,
    status: TodoStatus = TodoStatus.BACKLOG,
) -> dict:
    """Build a minimal create() payload (matches the existing guard tests)."""
    data: dict = {"title": title, "status": status.value}
    if project_id is not None:
        data["project_id"] = project_id
    return data


def _task_return_data(
    return_id: str,
    project_id: str | None = None,
    status: str = "created",
    exit_code: int = 0,
    queue: str = "core",
    work_type: str = "code",
) -> dict:
    """Build a minimal TaskReturnModel payload.

    ``job_id``/``playbook`` are NOT NULL on the model; supply stable values.
    """
    data: dict = {
        "return_id": return_id,
        "job_id": f"job-{return_id}",
        "playbook": "play.yml",
        "queue": queue,
        "work_type": work_type,
        "status": status,
        "exit_code": exit_code,
    }
    if project_id is not None:
        data["project_id"] = project_id
    return data


# ---------------------------------------------------------------------------
# TodoRepository.update() — mass-assignment guard edge cases (finding #10)
# ---------------------------------------------------------------------------


class TestTodoUpdateMassAssignment:
    """update() must reject identity/tenant/audit fields — edge cases that
    complement test_repository_update_guard.py."""

    async def test_todo_update_rejects_project_id_mass_assignment(
        self, async_session: AsyncSession
    ):
        """Cross-tenant escape: reassigning project_id via update() is the
        canonical finding-#10 violation and must raise BEFORE any DB write."""
        repo = TodoRepository(async_session)
        todo = await repo.create(_todo_data("seed", project_id="tenant-A"))
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"project_id": "tenant-B"},
                expected_version=1,
            )
        # Row must be untouched after the rejected call.
        refreshed = await repo.get_by_id(todo.todo_id)
        assert refreshed is not None
        assert refreshed.project_id == "tenant-A"

    async def test_todo_update_rejects_todo_id_mass_assignment(
        self, async_session: AsyncSession
    ):
        """Identity swap: changing the business key todo_id is forbidden."""
        repo = TodoRepository(async_session)
        todo = await repo.create(_todo_data("seed"))
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"todo_id": "TODO-IMPOSTOR"},
                expected_version=1,
            )

    async def test_todo_update_accepts_title_change(
        self, async_session: AsyncSession
    ):
        """A benign mutable field (title) MUST remain updatable — guards
        against the whitelist accidentally over-restricting."""
        repo = TodoRepository(async_session)
        todo = await repo.create(_todo_data("seed"))
        updated = await repo.update(
            todo.todo_id, {"title": "renamed"}, expected_version=1
        )
        assert updated.title == "renamed"
        assert updated.version == 2

    async def test_todo_update_validator_is_classmethod(self):
        """_validate_update_fields is a classmethod and can be exercised
        without a session — proves the guard does not depend on DB state."""
        with pytest.raises(ValueError, match="immutable"):
            TodoRepository._validate_update_fields({"project_id": "x"})
        with pytest.raises(ValueError, match="immutable"):
            TodoRepository._validate_update_fields({"todo_id": "x", "id": 1})
        # An empty / fully-mutable payload must NOT raise.
        TodoRepository._validate_update_fields({})
        TodoRepository._validate_update_fields({"title": "ok", "status": "queued"})

    async def test_todo_update_empty_payload_is_a_noop(
        self, async_session: AsyncSession
    ):
        """An empty updates dict must round-trip without mutating the row
        (still bumps version because update() always increments it)."""
        repo = TodoRepository(async_session)
        todo = await repo.create(_todo_data("seed"))
        updated = await repo.update(todo.todo_id, {}, expected_version=1)
        assert updated.version == 2
        assert updated.title == "seed"

    async def test_todo_update_scoped_repo_rejects_cross_tenant_write(
        self, async_session: AsyncSession
    ):
        """A scoped repo (project_id locked at construction) must refuse to
        update a todo belonging to a different tenant — the scoped path
        cannot be bypassed by passing project_id=None to update()."""
        repo_a = TodoRepository.scoped(async_session, project_id="tenant-A")
        todo = await repo_a.create(_todo_data("seed", project_id="tenant-A"))
        # A caller scoped to tenant-B passes no project_id to update(); the
        # scoped repo falls back to its locked scope and the get_by_id()
        # returns None -> InvalidTransitionError (not a silent cross write).
        repo_b = TodoRepository.scoped(async_session, project_id="tenant-B")
        from general_ludd.db.repository import InvalidTransitionError

        with pytest.raises(InvalidTransitionError):
            await repo_b.update(todo.todo_id, {"title": "pwned"}, expected_version=1)


# ---------------------------------------------------------------------------
# TaskReturnRepository — project_id scoping (no prior coverage)
# ---------------------------------------------------------------------------


class TestTaskReturnProjectScoping:
    """work_summary / history_summary / claim_unreviewed must honour the
    optional project_id filter so a tenant cannot read another tenant's
    task-return facts."""

    async def _seed_two_tenants(self, session: AsyncSession) -> None:
        repo = TaskReturnRepository(session)
        await repo.create(_task_return_data("RET-A1", project_id="proj-A", exit_code=0))
        await repo.create(
            _task_return_data("RET-A2", project_id="proj-A", exit_code=1, status="claimed_for_review")
        )
        await repo.create(_task_return_data("RET-B1", project_id="proj-B", exit_code=0))

    async def test_task_return_scoped_by_project_id_work_summary(
        self, async_session: AsyncSession
    ):
        await self._seed_two_tenants(async_session)
        repo = TaskReturnRepository(async_session)
        a = await repo.work_summary(project_id="proj-A")
        b = await repo.work_summary(project_id="proj-B")
        glob = await repo.work_summary()
        assert a["total"] == 2
        assert b["total"] == 1
        assert glob["total"] == 3
        # proj-A's status facet sees both 'created' and 'claimed_for_review'.
        assert set(a["by_status"].keys()) == {"created", "claimed_for_review"}
        assert set(b["by_status"].keys()) == {"created"}

    async def test_task_return_scoped_by_project_id_history_summary(
        self, async_session: AsyncSession
    ):
        await self._seed_two_tenants(async_session)
        repo = TaskReturnRepository(async_session)
        a = await repo.history_summary(project_id="proj-A")
        b = await repo.history_summary(project_id="proj-B")
        # proj-A: 1 success (exit 0) + 1 failure (exit 1) -> 0.5 rate.
        assert a["total_returns"] == 2
        assert a["success_count"] == 1
        assert a["failure_count"] == 1
        assert a["success_rate"] == 0.5
        # proj-B: 1 success -> 1.0 rate.
        assert b["total_returns"] == 1
        assert b["success_count"] == 1
        assert b["success_rate"] == 1.0
        # recent slice is also tenant-scoped.
        assert {r["return_id"] for r in a["recent"]} == {"RET-A1", "RET-A2"}
        assert {r["return_id"] for r in b["recent"]} == {"RET-B1"}

    async def test_task_return_history_summary_empty_project(
        self, async_session: AsyncSession
    ):
        """history_summary on a project with zero returns must return a
        well-formed zero-summary, not raise (NULL-aggregate coercion)."""
        repo = TaskReturnRepository(async_session)
        out = await repo.history_summary(project_id="proj-EMPTY")
        assert out["total_returns"] == 0
        assert out["success_count"] == 0
        assert out["failure_count"] == 0
        assert out["success_rate"] == 0.0
        assert out["recent"] == []

    async def test_task_return_claim_unreviewed_scoped(
        self, async_session: AsyncSession
    ):
        """claim_unreviewed must only claim 'created' rows for the scoped
        project — a tenant cannot claim another tenant's returns."""
        await self._seed_two_tenants(async_session)
        repo = TaskReturnRepository(async_session)
        claimed_a = await repo.claim_unreviewed(project_id="proj-A")
        claimed_b = await repo.claim_unreviewed(project_id="proj-B")
        assert {c.return_id for c in claimed_a} == {"RET-A1"}
        assert {c.return_id for c in claimed_b} == {"RET-B1"}
        # The 'claimed_for_review' row in proj-A is NOT re-claimed.
        assert all(c.status == "claimed_for_review" for c in claimed_a)

    async def test_task_return_claim_unreviewed_empty_returns_list(
        self, async_session: AsyncSession
    ):
        """When no 'created' rows exist, claim_unreviewed returns [] — never
        None. (list[X] contract; the empty-result anti-pattern is None.)"""
        repo = TaskReturnRepository(async_session)
        # Seed only non-'created' rows.
        await repo.create(
            _task_return_data("RET-X", status="claimed_for_review")
        )
        claimed = await repo.claim_unreviewed()
        assert claimed == []


# ---------------------------------------------------------------------------
# QueueRepository — basic CRUD + empty-result contract
# ---------------------------------------------------------------------------


class TestQueueRepository:
    """QueueRepository is a thin CRUD wrapper; the WP-C1 gap is the
    empty-result contract (list_* must return [], not None) and the
    queue_enabled filter on list_enabled()."""

    async def test_queue_create_and_get_by_name(self, async_session: AsyncSession):
        repo = QueueRepository(async_session)
        created = await repo.create(
            {"queue_name": "core", "queue_enabled": True, "hard_cap": 8}
        )
        assert created.queue_name == "core"
        fetched = await repo.get_by_name("core")
        assert fetched is not None
        assert fetched.hard_cap == 8
        # Missing name -> None (not an exception).
        assert await repo.get_by_name("nope") is None

    async def test_queue_list_enabled_filters_disabled(self, async_session: AsyncSession):
        repo = QueueRepository(async_session)
        await repo.create({"queue_name": "on", "queue_enabled": True})
        await repo.create({"queue_name": "off", "queue_enabled": False})
        enabled = await repo.list_enabled()
        names = {q.queue_name for q in enabled}
        assert names == {"on"}

    async def test_queue_empty_result_set_returns_list_not_none(
        self, async_session: AsyncSession
    ):
        """All list_* reads on an empty table must return [] — the WP-C1
        edge case. Returning None here is the bug we are pinning against."""
        repo = QueueRepository(async_session)
        assert await repo.list_all() == []
        assert await repo.list_enabled() == []


# ---------------------------------------------------------------------------
# event_loop.lease — queue lease acquire + reclaim (H15)
# ---------------------------------------------------------------------------


class TestQueueLeaseAcquireReclaim:
    """acquire_lease / reclaim_expired_leases / release_lease back the
    EventLoop's queue-claim phase. These had no unit-level coverage."""

    async def test_acquire_lease_creates_then_renews(
        self, async_session: AsyncSession
    ):
        """First call inserts; second call with the same (bucket, holder)
        renews in place — idempotent per the unique constraint."""
        first = await acquire_lease(
            async_session, bucket_key="core:TODO-1", holder_id="tick-1", ttl_seconds=60
        )
        # Capture the original expiry BEFORE the renew call — acquire_lease
        # mutates the existing ORM instance in place, so first.expires_at
        # would otherwise read the post-renew value at assertion time.
        first_expires = first.expires_at
        second = await acquire_lease(
            async_session, bucket_key="core:TODO-1", holder_id="tick-1", ttl_seconds=120
        )
        assert second.id == first.id  # same row, renewed
        assert second.expires_at > first_expires

    async def test_release_lease_returns_deleted_count(
        self, async_session: AsyncSession
    ):
        """release_lease deletes the matching row and returns rowcount;
        a second release on the same key returns 0."""
        await acquire_lease(
            async_session, bucket_key="core:TODO-1", holder_id="tick-1"
        )
        n1 = await release_lease(async_session, "core:TODO-1")
        assert n1 == 1
        n2 = await release_lease(async_session, "core:TODO-1")
        assert n2 == 0

    async def test_release_lease_scoped_by_holder(
        self, async_session: AsyncSession
    ):
        """When holder_id is supplied, only that holder's lease is dropped —
        a different holder's lease on the same bucket survives."""
        await acquire_lease(
            async_session, bucket_key="core:TODO-1", holder_id="tick-A"
        )
        await acquire_lease(
            async_session, bucket_key="core:TODO-1", holder_id="tick-B"
        )
        n = await release_lease(async_session, "core:TODO-1", holder_id="tick-A")
        assert n == 1
        # tick-B's lease still present.
        from sqlalchemy import select

        from general_ludd.db.models import BucketLeaseModel

        remaining = (
            await async_session.execute(
                select(BucketLeaseModel).where(
                    BucketLeaseModel.bucket_key == "core:TODO-1"
                )
            )
        ).scalars().all()
        assert {r.holder_id for r in remaining} == {"tick-B"}

    async def test_reclaim_expired_leases_noop_when_none_expired(
        self, async_session: AsyncSession
    ):
        """A live lease must NOT be reclaimed; returns 0."""
        await acquire_lease(
            async_session,
            bucket_key="core:TODO-1",
            holder_id="tick-1",
            ttl_seconds=3600,
        )
        reclaimed = await reclaim_expired_leases(async_session)
        assert reclaimed == 0

    async def test_queue_reclaim_requeues_expired_leases(
        self, async_session: AsyncSession
    ):
        """The H15 invariant: an expired lease whose holder crashed must be
        deleted AND its associated ACTIVE todo reset to QUEUED so
        claim_runnable can re-dispatch it. Without the requeue, the todo
        strands ACTIVE forever."""
        # Seed a todo in ACTIVE (claimed) state.
        todo_repo = TodoRepository(async_session)
        todo = await todo_repo.create(
            _todo_data("claimed work", project_id="proj-A", status=TodoStatus.ACTIVE)
        )
        # Acquire a lease on its bucket with a 1s TTL and wait for it to expire.
        await acquire_lease(
            async_session,
            bucket_key=f"core:{todo.todo_id}",
            holder_id="tick-crashed",
            ttl_seconds=1,
        )
        await async_session.commit()
        import asyncio

        await asyncio.sleep(1.1)
        reclaimed = await reclaim_expired_leases(async_session)
        assert reclaimed == 1
        # The todo must be back in QUEUED.
        refreshed = await todo_repo.get_by_id(todo.todo_id)
        assert refreshed is not None
        assert refreshed.status == TodoStatus.QUEUED.value

    async def test_reclaim_skips_requeue_when_live_lease_exists(
        self, async_session: AsyncSession
    ):
        """F1 defense-in-depth: if BOTH an expired lease (crashed tick) AND
        a live lease (the tick that legitimately re-claimed the todo) exist
        for the same bucket, reclaim must delete the expired one but NOT
        requeue — otherwise the live holder's work is duplicated."""
        todo_repo = TodoRepository(async_session)
        todo = await todo_repo.create(
            _todo_data("double-claimed", status=TodoStatus.ACTIVE)
        )
        bucket = f"core:{todo.todo_id}"
        # Expired lease from the crashed tick.
        await acquire_lease(
            async_session,
            bucket_key=bucket,
            holder_id="tick-crashed",
            ttl_seconds=1,
        )
        # Live lease from the recovery tick.
        await acquire_lease(
            async_session,
            bucket_key=bucket,
            holder_id="tick-recovery",
            ttl_seconds=3600,
        )
        await async_session.commit()
        import asyncio

        await asyncio.sleep(1.1)
        reclaimed = await reclaim_expired_leases(async_session)
        assert reclaimed == 1  # only the expired lease was deleted
        # The todo STAYS ACTIVE because a live lease still covers it.
        refreshed = await todo_repo.get_by_id(todo.todo_id)
        assert refreshed is not None
        assert refreshed.status == TodoStatus.ACTIVE.value


# ---------------------------------------------------------------------------
# Bulk / multi-row ordering contracts (no bulk_insert in repository.py —
# the closest pin is list_all ordering for TaskReturn history).
# ---------------------------------------------------------------------------


class TestBulkOrderingContract:
    """There is no bulk_insert in repository.py; the WP-C1 'bulk operations'
    gap is best expressed as the ordering contract on multi-row reads —
    history_summary 'recent' must preserve insertion order (newest-first)."""

    async def test_history_summary_recent_preserves_newest_first_order(
        self, async_session: AsyncSession
    ):
        """Insert N rows; history_summary(recent_limit=N) must return them
        newest-first (by created_at desc). Pinning the order protects the
        dashboard's 'recent work' slice."""
        repo = TaskReturnRepository(async_session)
        # Insert sequentially; later inserts have strictly greater created_at.
        ids = []
        for i in range(5):
            rid = f"RET-ORD-{i}"
            ids.append(rid)
            await repo.create(_task_return_data(rid, exit_code=0))
            # Force a measurable created_at gap so the DESC order is stable
            # even under coarse datetime resolution.
            import asyncio

            await asyncio.sleep(0.01)
        out = await repo.history_summary(recent_limit=5)
        returned_ids = [r["return_id"] for r in out["recent"]]
        # Newest-first: RET-ORD-4 .. RET-ORD-0.
        assert returned_ids == list(reversed(ids))
