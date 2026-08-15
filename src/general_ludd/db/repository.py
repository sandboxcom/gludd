"""Repository implementations for the agentic harness."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import (
    AgentMessageModel,
    AuditEventModel,
    AuditEventType,
    BenchmarkResultModel,
    FeatureModel,
    FeatureStatus,
    HumanTodoModel,
    LocationKind,
    MemoryRecordModel,
    ModelCallLogModel,
    ModelPerformanceModel,
    ProjectModel,
    ProjectRelationshipModel,
    PromptProfileModel,
    QueueModel,
    RelationType,
    RemediationActionModel,
    RoleRunModel,
    SlurmJobModel,
    SpendRecordModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.schemas.todo import TodoStatus

# Hard upper bound applied to unbounded ``list_*`` reads (P12). Callers that
# pass no ``limit`` receive at most this many rows; an explicit ``limit`` is
# itself capped at this value so a single query can never load an unbounded
# result set into memory. ``offset`` enables forward pagination.
_DEFAULT_LIST_LIMIT = 1000


# C.3 / S27: scoped_to context manager for explicit tenant-scoped operations.
# Sets the tenant contextvar for the duration of the block and restores it on
# exit.  Combined with the do_orm_execute listener in session.py, this makes
# every ORM query inside the block auto-filter to the given project_id.
@contextlib.contextmanager
def scoped_to(project_id: str) -> Generator[None, None, None]:
    """Apply tenant filtering to repository operations within the context.

    Args:
        project_id: Tenant identifier applied to ORM queries in the context.

    Yields:
        Control while the requested tenant is active.
    """
    from general_ludd.db.tenant import reset_tenant, set_tenant

    token = set_tenant(project_id)
    try:
        yield
    finally:
        reset_tenant(token)


VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.BACKLOG: {TodoStatus.QUEUED, TodoStatus.SCHEDULED, TodoStatus.CANCELLED},
    # SCHEDULED: one-shot todos flip to QUEUED when due; cron templates stay
    # SCHEDULED (the scheduler advances next_run_at instead of transitioning).
    # MANUAL_HOLD allows an operator to pause a pending schedule without
    # cancelling it. CANCELLED retires the schedule permanently.
    TodoStatus.SCHEDULED: {TodoStatus.QUEUED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    # APPROVAL_REQUIRED is the human-gate holding state for self-improve todos.
    # A human releases a held todo to QUEUED (approve) or retires it to
    # CANCELLED (reject) via SelfImproveApprovalManager; MANUAL_HOLD lets an
    # operator park it further. Without this entry TodoRepository.transition()
    # would reject the release and self-improve todos would strand in
    # APPROVAL_REQUIRED forever.
    TodoStatus.APPROVAL_REQUIRED: {TodoStatus.QUEUED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    TodoStatus.QUEUED: {TodoStatus.ACTIVE, TodoStatus.FAILED, TodoStatus.BLOCKED, TodoStatus.BLOCKED_ON_HUMAN},
    TodoStatus.ACTIVE: {
        TodoStatus.COMPLETE,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.BLOCKED_ON_HUMAN,
        TodoStatus.REVIEWING_RETURN,
        TodoStatus.MANUAL_HOLD,
        TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.QUEUED,
    },
    TodoStatus.REVIEWING_RETURN: {
        TodoStatus.COMPLETE,
        TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.NEEDS_MORE_WORK: {TodoStatus.QUEUED, TodoStatus.ACTIVE},
    TodoStatus.MANUAL_HOLD: {TodoStatus.QUEUED, TodoStatus.ACTIVE},
    TodoStatus.BLOCKED: {TodoStatus.QUEUED},
    TodoStatus.BLOCKED_ON_HUMAN: {TodoStatus.QUEUED, TodoStatus.CANCELLED},
    TodoStatus.FAILED: {TodoStatus.QUEUED},
    TodoStatus.BUDGET_EXCEEDED: {TodoStatus.QUEUED, TodoStatus.FAILED},
    TodoStatus.CANCELLED: set(),
    TodoStatus.COMPLETE: set(),
}

_MAX_PRIORITY: int = 1000
_MIN_PRIORITY: int = 0
_PRIORITY_LABELS: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# Fields that callers are permitted to set via TodoRepository.create().
# Excludes auto-managed columns (id, version, created_at, updated_at) to
# prevent mass-assignment of internal state.
ALLOWED_TODO_CREATE_FIELDS: frozenset[str] = frozenset(
    {
        "todo_id",
        "project_id",
        "title",
        "description",
        "status",
        "priority",
        "queue",
        "tags",
        "risk_level",
        "work_type",
        "resource_profile",
        "parent_todo_id",
        "child_todo_ids",
        "acceptance_criteria",
        "test_commands",
        "molecule_scenarios",
        "molecule_evidence_refs",
        "coverage_requirements",
        "dependencies",
        "created_by",
        "assigned_agent",
        "model_profile",
        "prompt_profile",
        "worktree",
        "branch_name",
        "artifacts",
        "evidence_refs",
        "plan_artifact",
        "confidence",
        "manual_hold_reason",
        "approval_policy",
        "completed_at",
        # Scheduling fields (integrated cron / one-shot scheduling).
        "scheduled_at",
        "cron",
        "schedule_timezone",
        "next_run_at",
        "last_run_at",
        "run_count",
        "max_runs",
        "schedule_paused",
    }
)

# Maximum UTF-8 byte length for any single string field on a TodoModel create.
_TODO_STR_FIELD_MAX_BYTES = 65536


class ConcurrencyError(RuntimeError):
    """Raised when optimistic concurrency detects a stale repository write."""


class InvalidTransitionError(ConcurrencyError):
    """Raised when a persisted task cannot enter the requested state."""


def _is_locked_error(exc: OperationalError) -> bool:
    """True when an OperationalError is SQLite's transient 'database is locked'.

    SQLite has no row-level locks, so under concurrent claimers a guarded UPDATE
    can raise SQLITE_BUSY -> OperationalError('database is locked') instead of
    simply affecting zero rows. That is a *lost race*, not a real fault: the
    losing claimer should skip the row, exactly as it would on rowcount == 0.
    """
    original = getattr(exc, "orig", None)
    msg = str(original if original is not None else exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


class TodoRepository:
    """Persist tenant-scoped todos with validated, concurrency-safe updates."""

    # D-28: Fields that callers must not supply in create() — they are set by the
    # DB/ORM (the autoincrement primary key `id`, `created_at`, `updated_at`) or must
    # start at a fixed value (version=1).  Accepting them would let callers forge the
    # database primary key or skip version accounting.
    #
    # NOTE: `todo_id` is intentionally NOT here. It is an APPLICATION-assigned business
    # identifier (e.g. "TODO-001", generated by the /api/todos router or supplied by a
    # caller), set at creation time — distinct from the DB primary key `id`. Rejecting
    # it at create() broke every legitimate create path (the router and direct repo
    # callers both supply it). The real-primary-key forgery risk is covered by `id`.
    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "version", "created_at", "updated_at"})
    # Finding #10: Fields that must NEVER change via update(). These are the
    # identity / tenant / audit columns — set once at create() and frozen
    # thereafter. This is a SEPARATE set from _IMMUTABLE_FIELDS (which guards
    # create()): todo_id and project_id are legitimately caller-supplied at
    # create time (they establish the business key and tenant scope) but must
    # be immutable on every subsequent update. Letting project_id through here
    # would permit a cross-tenant escape (move a todo into another tenant's
    # namespace); letting todo_id through would swap the entity's identity.
    _IMMUTABLE_UPDATE_FIELDS: frozenset[str] = frozenset(
        {
            "id",  # DB primary key
            "todo_id",  # application business key — swap = identity change
            "project_id",  # tenant scope — reassign = cross-tenant escape
            "version",  # managed by update() via the expected_version protocol
            "created_at",  # audit origin
            "updated_at",  # managed by update() itself
            "created_by",  # set-once audit attribution
        }
    )
    # D-28: Maximum byte length for text columns.  Prevents callers from storing
    # arbitrarily large blobs through the create() path.
    _MAX_TEXT_BYTES: int = 65_536  # 64 KiB

    def __init__(self, session: AsyncSession, project_id: str | None = None) -> None:
        """Initialize the repository with an optional default tenant scope."""
        self._session = session
        self._project_id = project_id

    @classmethod
    def scoped(cls, session: AsyncSession, project_id: str) -> TodoRepository:
        """Return a repository pre-scoped to *project_id*.

        Every read/write method that accepts ``project_id`` will fall back to
        this scope when the caller passes ``project_id=None`` (the default).
        This prevents the silent cross-tenant query that the unscoped constructor
        allows.  Admin/cross-tenant callers should use the plain constructor (or
        pass an explicit ``project_id`` override per-call).
        """
        return cls(session, project_id=project_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_pid(self, project_id: str | None) -> str | None:
        """Resolve an explicit project or fall back to the instance scope.

        ``None`` is propagated only for an unscoped administrative repository.
        """
        return project_id if project_id is not None else self._project_id

    @classmethod
    def _validate_create_data(cls, todo_data: dict[str, Any]) -> None:
        """Reject immutable fields and oversized values before model creation.

        Validation prevents primary-key forgery, version manipulation, and
        unbounded text storage before mass assignment to ``TodoModel``.
        """
        bad_fields = cls._IMMUTABLE_FIELDS & todo_data.keys()
        if bad_fields:
            raise ValueError(
                f"create() rejected: these fields are immutable and must not be "
                f"supplied by callers: {sorted(bad_fields)}"
            )
        for key, value in todo_data.items():
            if key == "priority":
                if isinstance(value, str):
                    label = value.strip().lower()
                    if label in _PRIORITY_LABELS:
                        todo_data[key] = _PRIORITY_LABELS[label]
                        continue
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"create() rejected: priority must be an integer, got {type(value).__name__}")
                if value < _MIN_PRIORITY:
                    todo_data[key] = _MIN_PRIORITY
                elif value > _MAX_PRIORITY:
                    todo_data[key] = _MAX_PRIORITY
            if isinstance(value, str) and len(value.encode()) > cls._MAX_TEXT_BYTES:
                raise ValueError(
                    f"create() rejected: field '{key}' exceeds the "
                    f"{cls._MAX_TEXT_BYTES}-byte limit "
                    f"({len(value.encode())} bytes)"
                )

    async def create(self, todo_data: dict[str, Any]) -> TodoModel:
        """Validate, persist, and return a new version-one todo."""
        # D-28: validate before mass-assignment so callers cannot forge primary
        # keys, skip version accounting, or store oversized blobs.
        self._validate_create_data(todo_data)
        todo = TodoModel(**todo_data)
        # D-28: enforce version=1 regardless of what the caller passed (already
        # blocked above, but belt-and-suspenders guard for future callers).
        todo.version = 1
        self._session.add(todo)
        await self._session.flush()
        return todo

    async def get_by_id(self, todo_id: str, project_id: str | None = None) -> TodoModel | None:
        """Return a todo by business identifier within the resolved scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel).where(TodoModel.todo_id == todo_id)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, todo_ids: list[str], project_id: str | None = None) -> dict[str, TodoModel]:
        """Return resolved-scope todos keyed by their requested identifiers."""
        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel).where(TodoModel.todo_id.in_(todo_ids))
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return {t.todo_id: t for t in result.scalars().all()}

    @classmethod
    def _validate_update_fields(cls, updates: dict[str, Any]) -> None:
        """Reject fields that must remain immutable after creation.

        Guards update() against
        mass-assignment of the tenant scope (``project_id`` → cross-tenant
        escape), the business key (``todo_id`` → identity swap), and the
        DB-managed / audit columns. Fires before any DB read or write so a
        rejected update leaves the row untouched.
        """
        bad = cls._IMMUTABLE_UPDATE_FIELDS & updates.keys()
        if bad:
            raise ValueError(
                f"update() rejected: these fields are immutable after "
                f"creation and must not be supplied to update(): {sorted(bad)}"
            )

    async def update(
        self,
        todo_id: str,
        updates: dict[str, Any],
        expected_version: int,
        project_id: str | None = None,
    ) -> TodoModel:
        """Apply a guarded update and increment the todo version.

        Raises:
            InvalidTransitionError: If the scoped todo does not exist.
            ConcurrencyError: If ``expected_version`` is stale or the write loses a race.
            ValueError: If an update attempts to change an immutable field.
        """
        # Finding #10: reject mass-assignment of identity/tenant/audit fields
        # before any DB read or write. See _validate_update_fields.
        self._validate_update_fields(updates)
        from sqlalchemy import update as _update

        _pid = self._resolve_pid(project_id)
        todo = await self.get_by_id(todo_id, project_id=_pid)
        if todo is None:
            raise InvalidTransitionError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(f"Version mismatch: expected {expected_version}, actual {todo.version}")
        now = datetime.now(UTC)
        # Guarded conditional UPDATE: the version read above can go stale before
        # this write commits. Carry the version (and scope) into the WHERE clause
        # so a concurrent writer at the same version makes one of us affect zero
        # rows -> ConcurrencyError, instead of silently losing an update.
        guard = _update(TodoModel).where(
            TodoModel.id == todo.id,
            TodoModel.version == expected_version,
        )
        if _pid is not None:
            guard = guard.where(TodoModel.project_id == _pid)
        guard = guard.values(**updates, version=expected_version + 1, updated_at=now)
        res = await self._session.execute(guard)
        if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
            raise ConcurrencyError(
                f"Lost update on todo {todo_id}: row changed concurrently (expected version {expected_version})"
            )
        # Sync the in-memory ORM object to the committed values.
        for key, value in updates.items():
            setattr(todo, key, value)
        todo.version = expected_version + 1
        todo.updated_at = now
        await self._session.flush()
        return todo

    async def list_by_status(
        self,
        status: TodoStatus,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[TodoModel]:
        """List bounded todos with a status in the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel).where(TodoModel.status == status.value)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        # P12: always cap — explicit limit is clamped at _DEFAULT_LIST_LIMIT.
        stmt = stmt.limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        queue: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        schedule_paused: bool | None = None,
    ) -> list[TodoModel]:
        """List a bounded page of todos matching optional filters."""
        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel)
        if queue is not None:
            stmt = stmt.where(TodoModel.queue == queue)
        if status is not None:
            stmt = stmt.where(TodoModel.status == status)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        if schedule_paused is not None:
            # DEFECT 3: filter paused rows in SQL (before LIMIT) so a capped
            # scheduled-list page can't under-return. Mirrors list_due_scheduled.
            stmt = stmt.where(TodoModel.schedule_paused.is_(schedule_paused))
        if offset:
            stmt = stmt.offset(offset)
        # P12: always cap — explicit limit is clamped at _DEFAULT_LIST_LIMIT.
        stmt = stmt.limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_work_type(
        self,
        work_type: str,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[TodoModel]:
        """List bounded todos for a work type in the resolved scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel).where(TodoModel.work_type == work_type)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        # P12: always cap — explicit limit is clamped at _DEFAULT_LIST_LIMIT.
        stmt = stmt.limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_due_scheduled(
        self,
        now: datetime,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[TodoModel]:
        """Return SCHEDULED todos whose fire time has arrived.

        Matches rows where:
          - status == 'scheduled'
          - schedule_paused is False
          - COALESCE(next_run_at, scheduled_at) <= now

        Ordered earliest-due-first so the scheduler processes them in
        chronological order.  ``project_id`` scopes to a single tenant when
        set; omit for cross-tenant scheduling.  ``limit`` caps the batch size;
        when None the module-level ``_DEFAULT_LIST_LIMIT`` is applied (P12).
        """
        from sqlalchemy import func

        _pid = self._resolve_pid(project_id)
        due_col = func.coalesce(TodoModel.next_run_at, TodoModel.scheduled_at)
        stmt = (
            select(TodoModel)
            .where(
                TodoModel.status == TodoStatus.SCHEDULED.value,
                TodoModel.schedule_paused.is_(False),
                due_col <= now,
            )
            .order_by(due_col)
        )
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        # P12: always cap — explicit limit is clamped at _DEFAULT_LIST_LIMIT.
        stmt = stmt.limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim_runnable(self, limit: int = 10, project_id: str | None = None) -> list[TodoModel]:
        """Claim QUEUED todos for execution with a guarded conditional UPDATE.

        SQLite has no row-level locking (``with_for_update`` is silently dropped),
        so the claim cannot rely on a lock. Instead, each candidate is flipped
        QUEUED->ACTIVE with an optimistic ``WHERE id=? AND status='queued' AND
        version=?`` UPDATE. Only the caller whose UPDATE affects a row (rowcount
        == 1) "wins" the claim; a caller that lost the race (rowcount == 0) skips
        the row, so every todo is returned to exactly one caller -> no double
        claim / double dispatch.
        """
        from sqlalchemy import update

        _pid = self._resolve_pid(project_id)
        stmt = select(TodoModel).where(TodoModel.status == TodoStatus.QUEUED.value)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        else:
            stmt = stmt.where(TodoModel.project_id.is_(None))
        # FIFO fairness: claim oldest QUEUED todos first so a backlog of newer
        # todos can never indefinitely starve an older one. Without an explicit
        # ORDER BY, row order is database-defined (undefined) and starvation is
        # possible under load. id is a deterministic tiebreaker for same-instant
        # created_at (e.g. todos inserted within the same microsecond in tests).
        stmt = stmt.order_by(TodoModel.priority.desc(), TodoModel.created_at, TodoModel.id)
        # P12: cap even an explicit caller limit so a huge value can't load an
        # unbounded result set (claim semantics are per-batch, so a cap is safe).
        stmt = stmt.limit(min(limit, _DEFAULT_LIST_LIMIT))
        with contextlib.suppress(Exception):
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        now = datetime.now(UTC)
        claimed: list[TodoModel] = []
        for todo in candidates:
            old_status = todo.status
            old_version = todo.version
            # Guarded conditional claim: transition only if the row is STILL
            # queued at the same version we read. Mirrors transition()'s
            # version/status guard so a concurrent claimer cannot also win.
            guard = (
                update(TodoModel)
                .where(
                    TodoModel.id == todo.id,
                    TodoModel.status == TodoStatus.QUEUED.value,
                    TodoModel.version == old_version,
                )
                .values(status=TodoStatus.ACTIVE.value, version=old_version + 1, updated_at=now)
            )
            try:
                res = await self._session.execute(guard)
            except OperationalError as exc:
                # SQLite has no row locks: a concurrent claimer can make the
                # guarded UPDATE raise SQLITE_BUSY ('database is locked') instead
                # of affecting zero rows. Treat that as a lost race exactly like
                # rowcount == 0 — refresh our stale copy and skip — so "loser
                # skips" still holds and a transient busy never aborts the claim.
                if not _is_locked_error(exc):
                    raise
                with contextlib.suppress(Exception):
                    await self._session.refresh(todo)
                continue
            if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
                # Lost the race: another caller already claimed this row. Drop our
                # stale in-memory copy and skip it so it is never returned twice.
                await self._session.refresh(todo)
                continue
            # We won: sync the in-memory ORM object to the committed values.
            todo.status = TodoStatus.ACTIVE.value
            todo.version = old_version + 1
            todo.updated_at = now
            evt = TodoEventModel(
                todo_id=todo.todo_id,
                event_type="status_change",
                old_status=old_status,
                new_status=TodoStatus.ACTIVE.value,
                actor="claim_runnable",
                reason="Claimed for execution",
            )
            self._session.add(evt)
            claimed.append(todo)
        await self._session.flush()
        return claimed

    async def count_active(self, project_id: str | None = None) -> int:
        """Count active todos in the resolved tenant scope."""
        from sqlalchemy import func

        _pid = self._resolve_pid(project_id)
        stmt = select(func.count()).select_from(TodoModel).where(TodoModel.status == TodoStatus.ACTIVE.value)
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def status_summary(self, project_id: str | None = None) -> dict[str, Any]:
        """Aggregate todo counts, oldest age, and backlog size.

        The facts endpoint reuses this database-level aggregation.
        """
        _pid = self._resolve_pid(project_id)
        from sqlalchemy import func

        async def _group_counts(column: Any) -> dict[str, int]:
            stmt = select(column, func.count()).group_by(column)
            if _pid is not None:
                stmt = stmt.where(TodoModel.project_id == _pid)
            result = await self._session.execute(stmt)
            # Coerce a NULL group key (e.g. queue IS NULL) to "unknown" so the
            # returned dict is always JSON-serializable — /api/facts serializes
            # this directly and a None key would raise TypeError in json.dumps.
            return {(key if key is not None else "unknown"): count for key, count in result.all()}

        by_status = await _group_counts(TodoModel.status)
        by_queue = await _group_counts(TodoModel.queue)
        by_work_type = await _group_counts(TodoModel.work_type)

        oldest_stmt = select(func.min(TodoModel.created_at))
        if _pid is not None:
            oldest_stmt = oldest_stmt.where(TodoModel.project_id == _pid)
        oldest_created = (await self._session.execute(oldest_stmt)).scalar()
        if oldest_created is not None and oldest_created.tzinfo is None:
            oldest_created = oldest_created.replace(tzinfo=UTC)

        oldest_age_seconds: float | None = None
        if oldest_created is not None:
            oldest_age_seconds = (datetime.now(UTC) - oldest_created).total_seconds()
        backlog = by_status.get(TodoStatus.BACKLOG.value, 0) + by_status.get(TodoStatus.QUEUED.value, 0)
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_queue": by_queue,
            "by_work_type": by_work_type,
            "oldest_age_seconds": oldest_age_seconds,
            "backlog_size": backlog,
        }

    async def transition(
        self,
        todo_id: str,
        new_status: TodoStatus,
        expected_version: int,
        project_id: str | None = None,
    ) -> TodoModel:
        """Perform a validated, optimistic state transition.

        Raises:
            InvalidTransitionError: If the todo is absent or the transition is disallowed.
            ConcurrencyError: If the expected version is stale or the guarded write loses.
        """
        from sqlalchemy import update as _update

        _pid = self._resolve_pid(project_id)
        todo = await self.get_by_id(todo_id, project_id=_pid)
        if todo is None:
            raise InvalidTransitionError(f"Todo {todo_id} not found")
        if todo.version != expected_version:
            raise ConcurrencyError(f"Version mismatch: expected {expected_version}, actual {todo.version}")
        current = TodoStatus(todo.status)
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidTransitionError(f"Invalid transition: {current.value} -> {new_status.value}")
        now = datetime.now(UTC)
        # Guarded conditional UPDATE keyed on the version AND the status we
        # validated the transition against: a concurrent writer that moved the
        # row out from under us (changing version or status) makes this affect
        # zero rows -> ConcurrencyError, never a silent lost transition.
        guard = _update(TodoModel).where(
            TodoModel.id == todo.id,
            TodoModel.version == expected_version,
            TodoModel.status == current.value,
        )
        if _pid is not None:
            guard = guard.where(TodoModel.project_id == _pid)
        guard = guard.values(status=new_status.value, version=expected_version + 1, updated_at=now)
        res = await self._session.execute(guard)
        if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
            raise ConcurrencyError(
                f"Lost transition on todo {todo_id}: row changed concurrently "
                f"(expected version {expected_version}, status {current.value})"
            )
        todo.status = new_status.value
        todo.version = expected_version + 1
        todo.updated_at = now
        await self._session.flush()
        return todo

    async def requeue_needs_more_work(
        self,
        *,
        cooldown_hours: int = 24,
        max_run_count: int = 3,
        limit: int = 10,
        project_id: str | None = None,
    ) -> int:
        """Requeue an eligible bounded batch and return the changed-row count."""
        from sqlalchemy import update as _update

        _pid = self._resolve_pid(project_id)
        cutoff = datetime.now(UTC) - timedelta(hours=cooldown_hours)
        cap = min(limit, _DEFAULT_LIST_LIMIT)
        stmt = (
            select(TodoModel)
            .where(
                TodoModel.status == TodoStatus.NEEDS_MORE_WORK.value,
                TodoModel.updated_at < cutoff,
                TodoModel.run_count < max_run_count,
            )
            .limit(cap)
        )
        # H.12: mirror claim_runnable's tenant guard. A scoped requeue MUST only
        # flip rows in that scope; an unscooped requeue (project_id=None and no
        # instance scope) MUST only touch NULL-project rows. Without this branch
        # the requeue was a cross-tenant mutation (every tenant's NEEDS_MORE_WORK
        # rows got flipped to QUEUED).
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        else:
            stmt = stmt.where(TodoModel.project_id.is_(None))
        result = await self._session.execute(stmt)
        todos = list(result.scalars().all())
        requeued = 0
        for todo in todos:
            guard = _update(TodoModel).where(
                TodoModel.id == todo.id,
                TodoModel.status == TodoStatus.NEEDS_MORE_WORK.value,
            )
            guard = guard.values(
                status=TodoStatus.QUEUED.value,
                version=TodoModel.version + 1,
                updated_at=datetime.now(UTC),
            )
            res = await self._session.execute(guard)
            if (cast("CursorResult[Any]", res).rowcount or 0) == 1:
                todo.status = TodoStatus.QUEUED.value
                todo.version += 1
                todo.updated_at = datetime.now(UTC)
                requeued += 1
        await self._session.flush()
        return requeued


class TaskReturnRepository:
    """Persist task-return records and aggregate execution outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> TaskReturnModel:
        """Persist and return a task-return record."""
        row = TaskReturnModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, return_id: str) -> TaskReturnModel | None:
        """Return the task-return record with the requested identifier."""
        stmt = select(TaskReturnModel).where(TaskReturnModel.return_id == return_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def work_summary(self, project_id: str | None = None) -> dict[str, Any]:
        """In-flight/claimed task-return counts by status / queue / work_type.

        Task returns represent dispatched work; this is the "work" facet of
        /api/facts. Reused, not duplicated, by the facts endpoint.

        Aggregated in SQL via ``GROUP BY`` per facet (P6) rather than loading
        every row and counting in Python — mirrors ``TodoRepository.status_summary``.
        ``status``/``queue``/``work_type`` are all NOT NULL on TaskReturnModel, so
        no None bucket can occur (no NULL-key handling needed).
        """
        from sqlalchemy import func

        async def _group_counts(column: Any) -> dict[str, int]:
            stmt = select(column, func.count()).group_by(column)
            if project_id is not None:
                stmt = stmt.where(TaskReturnModel.project_id == project_id)
            result = await self._session.execute(stmt)
            return {key: count for key, count in result.all()}

        by_status = await _group_counts(TaskReturnModel.status)
        by_queue = await _group_counts(TaskReturnModel.queue)
        by_work_type = await _group_counts(TaskReturnModel.work_type)
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_queue": by_queue,
            "by_work_type": by_work_type,
        }

    async def history_summary(self, project_id: str | None = None, recent_limit: int = 10) -> dict[str, Any]:
        """Recent returns + success/failure rates (exit_code 0 == success).

        Split into (a) an aggregate count query (total + successes via
        ``func.count``/``func.sum(case(...))``) and (b) a separate ordered+LIMITed
        query for the recent slice (P7), rather than loading every row just to
        count and head-slice. ``exit_code`` is NOT NULL, so a return is a success
        iff ``exit_code == 0`` exactly as the old Python loop computed.
        """
        from sqlalchemy import case, func

        agg_stmt = select(
            func.count(),
            func.sum(case((TaskReturnModel.exit_code == 0, 1), else_=0)),
        )
        if project_id is not None:
            agg_stmt = agg_stmt.where(TaskReturnModel.project_id == project_id)
        total, successes_raw = (await self._session.execute(agg_stmt)).one()
        total = total or 0
        # func.sum over zero rows is SQL NULL -> coerce to 0 (matches the old
        # ``sum(1 for ...)`` which is 0 on an empty result set).
        successes = successes_raw or 0
        failures = total - successes

        recent_stmt = select(TaskReturnModel)
        if project_id is not None:
            recent_stmt = recent_stmt.where(TaskReturnModel.project_id == project_id)
        recent_stmt = recent_stmt.order_by(TaskReturnModel.created_at.desc()).limit(recent_limit)
        recent_rows = list((await self._session.execute(recent_stmt)).scalars().all())
        recent = [
            {
                "return_id": r.return_id,
                "playbook": r.playbook,
                "status": r.status,
                "exit_code": r.exit_code,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in recent_rows
        ]
        return {
            "total_returns": total,
            "success_count": successes,
            "failure_count": failures,
            "success_rate": (successes / total) if total else 0.0,
            "recent": recent,
        }

    async def claim_unreviewed(self, project_id: str | None = None, limit: int = 10) -> list[TaskReturnModel]:
        """Claim 'created' task-returns for review with a guarded conditional UPDATE.

        TaskReturnModel has no version column, so the optimistic guard is on
        ``status`` alone: each candidate is flipped 'created'->'claimed_for_review'
        with ``WHERE id=? AND status='created'``. Only the caller whose UPDATE
        affects a row (rowcount == 1) claims it; a loser (rowcount == 0) skips it.
        This makes each return claimed by exactly one caller -> no double-review.
        """
        from sqlalchemy import update

        stmt = select(TaskReturnModel).where(TaskReturnModel.status == "created")
        if project_id is not None:
            stmt = stmt.where(TaskReturnModel.project_id == project_id)
        else:
            # H.12: an unscooped claim MUST only touch NULL-project rows. The
            # missing else branch was a cross-tenant leak: project_id=None
            # returned EVERY created row across all tenants. Mirrors the
            # claim_runnable guard.
            stmt = stmt.where(TaskReturnModel.project_id.is_(None))
        stmt = stmt.order_by(TaskReturnModel.created_at.asc()).limit(min(limit, _DEFAULT_LIST_LIMIT))
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        now = datetime.now(UTC)
        claimed: list[TaskReturnModel] = []
        for row in candidates:
            guard = (
                update(TaskReturnModel)
                .where(
                    TaskReturnModel.id == row.id,
                    TaskReturnModel.status == "created",
                )
                .values(status="claimed_for_review", updated_at=now)
            )
            try:
                res = await self._session.execute(guard)
            except OperationalError as exc:
                # SQLITE_BUSY under concurrent claimers == a lost race, not a
                # fault: skip the row just as we would on rowcount == 0 so each
                # return is still claimed by exactly one caller.
                if not _is_locked_error(exc):
                    raise
                with contextlib.suppress(Exception):
                    await self._session.refresh(row)
                continue
            if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
                # Lost the race: another caller already claimed this return.
                await self._session.refresh(row)
                continue
            row.status = "claimed_for_review"
            row.updated_at = now
            claimed.append(row)
        await self._session.flush()
        return claimed


class AuditEventRepository:
    """Persist append-only audit events and expose bounded history queries."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: str = "{}",
    ) -> AuditEventModel:
        """Persist an audit event attributed to a project.

        Raises:
            ValueError: If ``project_id`` is omitted.
        """
        if project_id is None:
            raise ValueError(
                "project_id is required for audit events — NULL project_id silently orphans the event from its project"
            )
        row = AuditEventModel(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=details or "{}",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_typed(
        self,
        event_type: AuditEventType,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEventModel:
        """Record an audit event from the typed AuditEventType taxonomy.

        Serializes ``details`` to JSON and delegates to :meth:`create`. This is
        the typed entry point the event loop uses so audit rows carry values
        from the AuditEventType enum rather than ad-hoc magic strings.
        """
        import json as _json

        return await self.create(
            event_type=event_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=_json.dumps(details) if details is not None else "{}",
        )

    async def list_by_entity(self, entity_type: str, entity_id: str, limit: int = 50) -> list[AuditEventModel]:
        """List recent audit events for one entity."""
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.entity_type == entity_type, AuditEventModel.entity_id == entity_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(self, project_id: str, limit: int = 50) -> list[AuditEventModel]:
        """List recent audit events attributed to a project."""
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.project_id == project_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class VariableNamespaceRepository:
    """Persist project and global namespaced configuration variables."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def load_vars_for_project(self, project_id: str | None) -> dict[str, str]:
        """Load merged global and project variables with project values winning."""
        stmt = (
            select(VariableValueModel)
            .join(VariableNamespaceModel)
            .where((VariableNamespaceModel.project_id == project_id) | (VariableNamespaceModel.project_id.is_(None)))
            .order_by(VariableNamespaceModel.project_id.is_(None).desc())
            # P12: defensive cap; variable sets are expected to be small but
            # an unbounded JOIN load is still a risk surface.
            .limit(_DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        result_dict: dict[str, str] = {}
        for row in rows:
            result_dict[row.key] = row.value
        return result_dict

    async def create_namespace(self, namespace: str, project_id: str | None = None) -> VariableNamespaceModel:
        """Create and return a global or project-specific namespace."""
        row = VariableNamespaceModel(namespace=namespace, project_id=project_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_var(self, namespace: str, key: str, value: str, project_id: str | None = None) -> VariableValueModel:
        """Atomically upsert and return a namespaced variable value."""
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        # Resolve (or atomically create) the namespace. get-then-insert here is a
        # TOCTOU race on the (namespace, project_id) unique key: two concurrent
        # first-writers both see ns is None and both INSERT -> IntegrityError.
        # ON CONFLICT DO NOTHING on the unique constraint makes the insert a no-op
        # for the loser, who then re-reads the winner's row.
        stmt = select(VariableNamespaceModel).where(
            VariableNamespaceModel.namespace == namespace,
            VariableNamespaceModel.project_id == project_id,
        )
        ns = (await self._session.execute(stmt)).scalar_one_or_none()
        if ns is None:
            ns_insert = (
                sqlite_insert(VariableNamespaceModel)
                .values(namespace=namespace, project_id=project_id)
                .on_conflict_do_nothing(index_elements=["namespace", "project_id"])
            )
            await self._session.execute(ns_insert)
            await self._session.flush()
            ns = (await self._session.execute(stmt)).scalar_one()

        # Upsert the value on the (namespace_id, key) unique key. on_conflict_do_update
        # closes the get-then-insert TOCTOU: a concurrent first-write no longer
        # raises IntegrityError (and is no longer silently lost) — last writer wins.
        now = datetime.now(UTC)
        val_insert = (
            sqlite_insert(VariableValueModel)
            .values(namespace_id=ns.id, key=key, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=["namespace_id", "key"],
                set_={"value": value, "updated_at": now},
            )
        )
        await self._session.execute(val_insert)
        await self._session.flush()
        row = (
            await self._session.execute(
                select(VariableValueModel).where(
                    VariableValueModel.namespace_id == ns.id,
                    VariableValueModel.key == key,
                )
            )
        ).scalar_one()
        # on_conflict_do_update bypasses the identity map; refresh so a previously
        # loaded value row reflects the just-committed value.
        await self._session.refresh(row)
        return row


class BenchmarkRepository:
    """Persist benchmark results and compute model-selection aggregates."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a transactional session factory."""
        self._session = session
        self._session_factory = session_factory

    async def _execute_with_session(self, fn: Callable[[AsyncSession], Any]) -> Any:
        if self._session_factory is not None:
            async with self._session_factory() as session, session.begin():
                result = await fn(session)
                if hasattr(result, "_sa_instance_state"):
                    session.expunge(result)
                return result
        if self._session is not None:
            return await fn(self._session)
        raise RuntimeError("BenchmarkRepository: no session or session_factory")

    async def record_result(self, data: dict[str, Any]) -> BenchmarkResultModel:
        """Persist and return a benchmark result."""

        async def _do(session: AsyncSession) -> BenchmarkResultModel:
            row = BenchmarkResultModel(**data)
            session.add(row)
            await session.flush()
            return row

        return cast(BenchmarkResultModel, await self._execute_with_session(_do))

    async def get_aggregate_scores(
        self,
        task_type: str | None = None,
        project_id: str | None = None,
        task_role: str | None = None,
        skill_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate scores by prompt/model/task, project, role, and skill.

        ``project_id`` is the project-hierarchy phase-3 axis: when None (the
        default and every legacy caller's behaviour) the scores are GLOBAL —
        grouped across all projects exactly as before. When a ``project_id`` is
        passed, results are filtered to that project's history, so the
        AdaptiveRouter can read its own history and a related project's history
        separately and borrow across declared edges. ``project_id`` is always a
        group key and is returned in every row so the router can attribute a
        borrowed pick to its source project.

        ``task_role`` filters and groups by the assigned role (planner, coder,
        reviewer, editor, compactor, enumerator), enabling per-role quality
        comparisons. When provided, results are filtered to that role.
        ``task_role`` is included as a group-by key and returned in every row.

        ``skill_id`` is an optional filter and an unconditional group key. NULL
        therefore keeps legacy results distinct from skill-attributed results,
        while callers can request a single skill's model usefulness history.
        """

        async def _do(session: AsyncSession) -> list[dict[str, Any]]:
            from sqlalchemy import func

            stmt = (
                select(
                    BenchmarkResultModel.prompt_profile_id,
                    BenchmarkResultModel.model_profile_id,
                    BenchmarkResultModel.task_type,
                    BenchmarkResultModel.project_id,
                    BenchmarkResultModel.task_role,
                    BenchmarkResultModel.skill_id,
                    func.avg(BenchmarkResultModel.completion_score).label("avg_completion"),
                    func.avg(BenchmarkResultModel.code_quality_score).label("avg_quality"),
                    func.avg(BenchmarkResultModel.instruction_adherence_score).label("avg_instruction"),
                    func.avg(BenchmarkResultModel.token_efficiency_score).label("avg_efficiency"),
                    func.count().label("sample_count"),
                    func.avg(BenchmarkResultModel.cost_usd).label("avg_cost"),
                    func.avg(
                        BenchmarkResultModel.completion_score * 0.4
                        + BenchmarkResultModel.code_quality_score * 0.3
                        + BenchmarkResultModel.instruction_adherence_score * 0.2
                        + BenchmarkResultModel.token_efficiency_score * 0.1
                    ).label("composite_score"),
                )
                .where(BenchmarkResultModel.success.is_(True))
                .group_by(
                    BenchmarkResultModel.prompt_profile_id,
                    BenchmarkResultModel.model_profile_id,
                    BenchmarkResultModel.task_type,
                    BenchmarkResultModel.project_id,
                    BenchmarkResultModel.task_role,
                    BenchmarkResultModel.skill_id,
                )
            )
            if task_type is not None:
                stmt = stmt.where(BenchmarkResultModel.task_type == task_type)
            if project_id is not None:
                stmt = stmt.where(BenchmarkResultModel.project_id == project_id)
            if task_role is not None:
                stmt = stmt.where(BenchmarkResultModel.task_role == task_role)
            if skill_id is not None:
                stmt = stmt.where(BenchmarkResultModel.skill_id == skill_id)
            result = await session.execute(stmt)
            rows = result.all()
            return [
                {
                    "prompt_profile_id": r.prompt_profile_id,
                    "model_profile_id": r.model_profile_id,
                    "task_type": r.task_type,
                    "project_id": r.project_id,
                    "task_role": r.task_role,
                    "skill_id": r.skill_id,
                    "avg_completion": r.avg_completion,
                    "avg_quality": r.avg_quality,
                    "avg_instruction": r.avg_instruction,
                    "avg_efficiency": r.avg_efficiency,
                    "sample_count": r.sample_count,
                    "avg_cost": r.avg_cost,
                    "composite_score": getattr(r, "composite_score", None),
                }
                for r in rows
            ]

        return cast("list[dict[str, Any]]", await self._execute_with_session(_do))

    async def get_best_for_task(self, task_type: str, min_samples: int = 3) -> list[dict[str, Any]]:
        """Rank task-specific aggregates that meet the sample threshold."""
        scores = await self.get_aggregate_scores(task_type=task_type)
        filtered = [s for s in scores if s["sample_count"] >= min_samples]
        filtered.sort(key=lambda s: s.get("composite_score", 0) or 0, reverse=True)
        return filtered

    async def get_model_scores(self, model_profile_id: str) -> list[BenchmarkResultModel]:
        """List bounded benchmark results for a model, newest first."""

        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            stmt = (
                select(BenchmarkResultModel)
                .where(BenchmarkResultModel.model_profile_id == model_profile_id)
                .order_by(BenchmarkResultModel.created_at.desc())
                # P12: defensive cap; dead API path but still bounded.
                .limit(_DEFAULT_LIST_LIMIT)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))

    async def list_recent(self, limit: int = 50) -> list[BenchmarkResultModel]:
        """List a bounded set of recent benchmark results."""

        async def _do(session: AsyncSession) -> list[BenchmarkResultModel]:
            stmt = (
                select(BenchmarkResultModel)
                .order_by(BenchmarkResultModel.created_at.desc())
                .limit(min(limit, _DEFAULT_LIST_LIMIT))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        return cast("list[BenchmarkResultModel]", await self._execute_with_session(_do))


class PromptProfileRepository:
    """Persist reusable prompt profiles and query their task applicability."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def upsert(self, data: dict[str, Any]) -> PromptProfileModel:
        """Atomically insert or update a prompt profile by name."""
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        # Upsert on the unique ``name`` key. The old get-then-insert was a TOCTOU
        # race: two concurrent first-writers for the same name both saw None and
        # both INSERTed -> IntegrityError (one write lost). on_conflict_do_update
        # makes the conflicting write an UPDATE instead, so concurrent first-writes
        # converge (last writer wins) with no IntegrityError.
        now = datetime.now(UTC)
        values = {**data, "updated_at": now}
        update_cols = {k: v for k, v in values.items() if k not in ("id", "name")}
        stmt = (
            sqlite_insert(PromptProfileModel)
            .values(**values)
            .on_conflict_do_update(index_elements=["name"], set_=update_cols)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        # Resolve the row through the repository's existing global name lookup.
        # Prompt profiles are not project-scoped in the schema, so do not pass
        # tenant-only arguments or call helpers that this repository does not
        # define.
        row = await self.get_by_name(data.get("name", ""))
        assert row is not None  # just upserted
        # Core INSERT ... ON CONFLICT bypasses the ORM identity map; refresh any
        # already-loaded instance so callers see the committed (updated) values.
        await self._session.refresh(row)
        return row

    async def get_by_name(self, name: str) -> PromptProfileModel | None:
        """Return a prompt profile by its unique name."""
        stmt = select(PromptProfileModel).where(PromptProfileModel.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: str) -> PromptProfileModel | None:
        """Return a prompt profile by primary identifier."""
        stmt = select(PromptProfileModel).where(PromptProfileModel.id == profile_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int | None = None, offset: int = 0) -> list[PromptProfileModel]:
        """List a bounded page of prompt profiles."""
        stmt = (
            select(PromptProfileModel)
            .offset(offset)
            .limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_source(self, source: str, limit: int | None = None, offset: int = 0) -> list[PromptProfileModel]:
        """List a bounded page of prompt profiles from one source."""
        stmt = (
            select(PromptProfileModel)
            .where(PromptProfileModel.source == source)
            .offset(offset)
            .limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_task_type(self, task_type: str) -> list[PromptProfileModel]:
        """List profiles that support a task type or declare no restriction."""
        import json as _json

        stmt = select(PromptProfileModel).limit(_DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        out: list[PromptProfileModel] = []
        for row in rows:
            try:
                types_raw = _json.loads(row.task_types or "[]")
            except Exception:
                types_raw = []
            if isinstance(types_raw, list):
                types: list[str] = types_raw
            elif isinstance(types_raw, str):
                types = [types_raw]
            else:
                types = []
            if not types or task_type in types:
                out.append(row)
        return out


class QueueRepository:
    """Persist queue configuration and expose bounded queue listings."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> QueueModel:
        """Persist and return a queue configuration."""
        row = QueueModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_name(self, name: str) -> QueueModel | None:
        """Return a queue configuration by name."""
        stmt = select(QueueModel).where(QueueModel.queue_name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int | None = None, offset: int = 0) -> list[QueueModel]:
        """List a bounded page of queue configurations."""
        stmt = (
            select(QueueModel)
            .offset(offset)
            .limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_enabled(self, limit: int | None = None, offset: int = 0) -> list[QueueModel]:
        """List a bounded page of enabled queue configurations."""
        stmt = (
            select(QueueModel)
            .where(QueueModel.queue_enabled.is_(True))
            .offset(offset)
            .limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ProjectRepository:
    """Persist project records and their active lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> ProjectModel:
        """Persist and return a project record."""
        row = ProjectModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, project_id: str) -> ProjectModel | None:
        """Return a project by its public identifier."""
        stmt = select(ProjectModel).where(ProjectModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[ProjectModel]:
        """List a bounded set of active projects."""
        # P12: bound the read so a large project table can't load unboundedly.
        stmt = select(ProjectModel).where(ProjectModel.active.is_(True)).limit(_DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, project_id: str) -> None:
        """Atomically deactivate an active project, leaving missing rows unchanged."""
        from sqlalchemy import update as _update

        # Single guarded UPDATE rather than read-then-mutate: the read-modify-write
        # form lets a concurrent writer's change be lost between the SELECT and the
        # ORM flush. Guarding on active == True also makes a double-deactivate a
        # detectable no-op instead of a silent clobber.
        guard = (
            _update(ProjectModel)
            .where(
                ProjectModel.project_id == project_id,
                ProjectModel.active.is_(True),
            )
            .values(active=False)
        )
        await self._session.execute(guard)
        await self._session.flush()
        # Keep any already-loaded ORM instance consistent with the committed row.
        project = await self.get_by_id(project_id)
        if project is not None:
            await self._session.refresh(project)


class ProjectRelationshipRepository:
    """Persistence for declared project-topology edges (ProjectRelationshipModel).

    Edges are USER-DECLARED (config or API), never inferred. ``add_relationship``
    is an idempotent upsert keyed on the unique edge tuple
    ``(project_id, relation_type, location_kind, location_value)``. The
    "one parent per project" rule is enforced here (``add_relationship`` replaces an
    existing parent edge) because SQLite cannot express a portable partial unique
    index; PostgreSQL also carries the ``uq_one_parent`` partial index.
    """

    # The owning project may declare at most one of these relation types.
    _SINGLETON_RELATIONS: frozenset[str] = frozenset({RelationType.PARENT.value})
    _LEGACY_LOCATION_KINDS: ClassVar[dict[str, str]] = {"path": LocationKind.DIRECTORY.value}

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def add_relationship(self, data: dict[str, Any]) -> ProjectRelationshipModel:
        """Idempotent upsert of one declared edge; enforces the one-parent rule.

        Rejects a self-edge (``related_project_id == project_id``). For a
        ``relation_type='parent'`` edge that does not match an existing parent
        row's full unique tuple, any existing parent edge for the project is first
        removed (replace-on-second-parent), so a project never carries two parents.
        Re-declaring the SAME edge tuple updates it in place (no duplicate row).
        """
        data = dict(data)
        raw_location_kind = str(data.get("location_kind", ""))
        data["location_kind"] = self._LEGACY_LOCATION_KINDS.get(raw_location_kind, raw_location_kind)

        project_id = data.get("project_id", "")
        relation_type = str(data.get("relation_type", ""))
        location_kind = str(data.get("location_kind", ""))

        # Enum validation: the repository is the security boundary for the
        # direct-call path (config parsing validates upstream, but callers may
        # invoke add_relationship directly). Reject anything that is not a valid
        # RelationType / LocationKind StrEnum value BEFORE persisting, so the
        # column never holds an out-of-domain string.
        try:
            RelationType(relation_type)
        except ValueError as exc:
            valid = ", ".join(r.value for r in RelationType)
            raise ValueError(f"invalid relation_type {relation_type!r}; must be one of: {valid}") from exc
        try:
            LocationKind(location_kind)
        except ValueError as exc:
            valid = ", ".join(k.value for k in LocationKind)
            raise ValueError(f"invalid location_kind {location_kind!r}; must be one of: {valid}") from exc

        related_project_id = data.get("related_project_id")
        if related_project_id is not None and related_project_id == project_id:
            raise ValueError(f"self-edge rejected: related_project_id {related_project_id!r} == project_id")

        existing = await self._get_edge(
            project_id,
            relation_type,
            location_kind,
            str(data.get("location_value", "")),
        )

        if existing is not None:
            for key, value in data.items():
                if key in ("id", "project_id", "created_at"):
                    continue
                setattr(existing, key, value)
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        # One-parent guard + insert as ONE atomic transaction unit. A NEW parent
        # edge (tuple differs from any existing parent row) replaces the prior
        # parent: we delete the prior parent rows and add the new edge with NO
        # intermediate flush/commit between them, then a single flush at the end.
        # Either both the delete and the insert land or neither does (they share
        # one transaction and roll back together on failure), so the project can
        # never be left with zero parents mid-operation. Re-declaring the same
        # parent tuple is handled by the upsert branch above, so it never reaches
        # here as a "second" parent.
        #
        # This ordering covers the local SQLite case (single writer per file).
        # True multi-connection concurrency safety relies on the PostgreSQL
        # ``uq_one_parent`` partial unique index already added in migration 008,
        # which rejects a concurrent second parent at the database level.
        if relation_type in self._SINGLETON_RELATIONS:
            for prior in await self.list_for_project(project_id, relation_type=relation_type):
                await self._session.delete(prior)

        row = ProjectRelationshipModel(**data)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def _get_edge(
        self,
        project_id: str,
        relation_type: str,
        location_kind: str,
        location_value: str,
    ) -> ProjectRelationshipModel | None:
        stmt = select(ProjectRelationshipModel).where(
            ProjectRelationshipModel.project_id == project_id,
            ProjectRelationshipModel.relation_type == relation_type,
            ProjectRelationshipModel.location_kind == location_kind,
            ProjectRelationshipModel.location_value == location_value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: str,
        relation_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProjectRelationshipModel]:
        """List a bounded page of declared relationships for a project."""
        stmt = select(ProjectRelationshipModel).where(ProjectRelationshipModel.project_id == project_id)
        if relation_type is not None:
            stmt = stmt.where(ProjectRelationshipModel.relation_type == relation_type)
        stmt = (
            stmt.order_by(ProjectRelationshipModel.id)
            .offset(offset)
            .limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_parent(self, project_id: str) -> ProjectRelationshipModel | None:
        """Return the project's declared parent edge, if present."""
        edges = await self.list_for_project(project_id, relation_type=RelationType.PARENT.value)
        return edges[0] if edges else None

    async def list_children(
        self, project_id: str, limit: int | None = None, offset: int = 0
    ) -> list[ProjectRelationshipModel]:
        """List a bounded page of the project's declared child edges."""
        return await self.list_for_project(
            project_id,
            relation_type=RelationType.CHILD.value,
            limit=limit,
            offset=offset,
        )

    async def remove(self, rel_id: str) -> bool:
        """Delete one edge by its primary key. Returns True iff a row was removed."""
        stmt = select(ProjectRelationshipModel).where(ProjectRelationshipModel.id == rel_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


BROADCAST_RECIPIENT = "broadcast"


class AgentMessageRepository:
    """Persistence for the inter-agent message queue (AgentMessageModel)."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def send(
        self,
        data: dict[str, Any] | None = None,
        *,
        sender: str | None = None,
        recipient: str | None = None,
        topic: str = "",
        body: str = "",
        project_id: str | None = None,
        priority: str = "normal",
        ttl_seconds: int | None = None,
    ) -> AgentMessageModel:
        """Persist a message using mapping or keyword-style input."""
        keyword_style = data is None and sender is not None
        payload = dict(data or {})
        if sender is not None:
            payload["sender"] = sender
        if recipient is not None:
            payload["recipient"] = recipient
        if topic or "topic" not in payload:
            payload["topic"] = topic
        if body or "body" not in payload:
            payload["body"] = body
        if project_id is not None:
            payload["project_id"] = project_id
        if priority != "normal" or "priority" not in payload:
            payload["priority"] = priority
        if ttl_seconds is not None:
            payload["ttl_seconds"] = ttl_seconds
        row = AgentMessageModel(**payload)
        if keyword_style:
            # Preserve the legacy row-returning mapping API while supporting
            # the newer keyword API's boolean acknowledgement contract.
            row._keyword_style = True  # type: ignore[attr-defined]
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, message_id: str) -> AgentMessageModel | None:
        """Return an inter-agent message by identifier."""
        stmt = select(AgentMessageModel).where(AgentMessageModel.id == message_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def inbox(
        self,
        recipient: str,
        unread_only: bool = True,
        include_broadcast: bool = True,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentMessageModel]:
        """Return messages addressed to ``recipient`` (and broadcasts).

        Expired messages (past their ttl) are never returned.
        """
        from sqlalchemy import or_

        target: Any
        if include_broadcast:
            target = AgentMessageModel.recipient.in_([recipient, BROADCAST_RECIPIENT])
        else:
            target = AgentMessageModel.recipient == recipient
        stmt = select(AgentMessageModel).where(target)
        if unread_only:
            stmt = stmt.where(AgentMessageModel.read_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        stmt = stmt.order_by(AgentMessageModel.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(UTC)
        return [r for r in rows if not self._is_expired(r, now)]

    async def ack(self, message_id: str, project_id: str | None = None) -> AgentMessageModel | bool | None:
        """Mark a message read. Returns the row, or None if it does not exist.

        XT-11: when ``project_id`` is supplied, a message belonging to another
        project is treated as not-found (the ack neither reads nor mutates it),
        so a caller scoped to project A cannot mark project B's message read by
        guessing its id. ``project_id=None`` preserves the unscoped/admin path.
        """
        from sqlalchemy import update as _update

        row = await self.get_by_id(message_id)
        if row is None:
            return None
        if project_id is not None and row.project_id != project_id:
            return None
        # Guarded conditional UPDATE on read_at IS NULL: the read-then-mutate form
        # let two concurrent acks both see read_at None and both write, clobbering
        # the first ack's timestamp. Guarding on read_at IS NULL means only the
        # first ack writes; a later ack affects zero rows and leaves it untouched.
        now = datetime.now(UTC)
        guard = _update(AgentMessageModel).where(
            AgentMessageModel.id == message_id,
            AgentMessageModel.read_at.is_(None),
        )
        if project_id is not None:
            # Atomic backstop: the UPDATE itself refuses a cross-project row.
            guard = guard.where(AgentMessageModel.project_id == project_id)
        guard = guard.values(read_at=now)
        await self._session.execute(guard)
        await self._session.flush()
        await self._session.refresh(row)
        if getattr(row, "_keyword_style", False):
            return True
        return row

    async def purge(self) -> int:
        """Compatibility alias for purge_expired."""
        return await self.purge_expired()

    async def purge_expired(self) -> int:
        """Delete every message whose ttl has elapsed. Returns the count purged.

        Single set-based DELETE pushed into SQL rather than fetch-all + per-row
        ``session.delete()``: the expiry predicate mirrors :meth:`_is_expired`
        (``elapsed_seconds > ttl_seconds``) using SQLite ``julianday`` day-diff
        arithmetic. AgentMessageModel declares no child relationships (its only
        FK is ``project_id`` ondelete=SET NULL, an outbound reference), so the
        bulk delete bypasses no ORM cascade.
        """
        from sqlalchemy import delete, func

        elapsed_seconds = (func.julianday("now") - func.julianday(AgentMessageModel.created_at)) * 86400.0
        stmt = delete(AgentMessageModel).where(
            AgentMessageModel.ttl_seconds.isnot(None),
            elapsed_seconds > AgentMessageModel.ttl_seconds,
        )
        result = await self._session.execute(stmt)
        purged = int(cast("CursorResult[Any]", result).rowcount or 0)
        if purged:
            await self._session.flush()
        return purged

    async def unread_counts(self, project_id: str | None = None) -> dict[str, int]:
        """Per-recipient unread counts (excludes expired). Used by /api/facts.

        The TTL cutoff is pushed into the WHERE clause (a row survives when it
        has no ttl or its elapsed seconds are still within ttl, mirroring
        :meth:`_is_expired`) and the per-recipient tally is a SQL ``GROUP BY``
        instead of a full-table load + Python aggregation. ``recipient`` is
        NOT NULL, so there is no None bucket.
        """
        from sqlalchemy import func, or_

        elapsed_seconds = (func.julianday("now") - func.julianday(AgentMessageModel.created_at)) * 86400.0
        stmt = (
            select(AgentMessageModel.recipient, func.count())
            .where(
                AgentMessageModel.read_at.is_(None),
                or_(
                    AgentMessageModel.ttl_seconds.is_(None),
                    elapsed_seconds <= AgentMessageModel.ttl_seconds,
                ),
            )
            .group_by(AgentMessageModel.recipient)
        )
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        result = await self._session.execute(stmt)
        return {recipient: count for recipient, count in result.all()}

    @staticmethod
    def _is_expired(row: AgentMessageModel, now: datetime) -> bool:
        if row.ttl_seconds is None:
            return False
        created = row.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (now - created).total_seconds() > row.ttl_seconds


class FeatureRepository:
    """Persistence for the feature database (FeatureModel).

    JSON (de)serialization happens at the repo boundary — callers pass / receive
    Python objects (lists/dicts); the DB stores JSON-in-Text as do PromptProfileModel
    and TodoModel.
    """

    def __init__(self, session: AsyncSession, project_id: str | None = None) -> None:
        """Initialize the repository with an optional default tenant scope."""
        self._session = session
        self._project_id = project_id

    @classmethod
    def scoped(cls, session: AsyncSession, project_id: str) -> FeatureRepository:
        """Return a repository pre-scoped to *project_id*.

        Read methods that accept ``project_id`` fall back to this scope when the
        caller passes ``project_id=None``, preventing silent cross-tenant reads
        (XT-2/5/6/7). Mirrors ``TodoRepository.scoped``.
        """
        return cls(session, project_id=project_id)

    def _resolve_pid(self, project_id: str | None) -> str | None:
        """Return *project_id* if explicitly supplied, else the instance scope.

        ``None`` propagates only when the instance was not scoped (admin path),
        preserving cross-tenant reads for internal callers like ``set_status``.
        """
        return project_id if project_id is not None else self._project_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(value: Any) -> str:
        import json as _json

        if isinstance(value, str):
            return value
        return _json.dumps(value)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert(self, data: dict[str, Any]) -> FeatureModel:
        """Insert or update a feature row, keyed by name (unique).

        Fields that are lists/dicts in ``data`` are serialized to JSON before
        being written to the DB.  The returned model has JSON-string columns.
        """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        json_fields = {"acceptance_criteria", "evidence"}
        serialized: dict[str, Any] = {}
        for key, val in data.items():
            if key in json_fields:
                serialized[key] = self._serialize(val)
            else:
                serialized[key] = val

        # Upsert on the unique ``name`` key. get-then-insert was a TOCTOU race
        # (two concurrent first-writes -> IntegrityError, one silently lost);
        # on_conflict_do_update turns the conflicting write into an UPDATE so
        # concurrent first-writes converge instead of raising.
        update_cols = {k: v for k, v in serialized.items() if k not in ("id", "name")}
        stmt = sqlite_insert(FeatureModel).values(**serialized)
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=["name"], set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self.get_by_name(data.get("name", ""))
        assert row is not None  # just upserted
        # The core INSERT ... ON CONFLICT bypasses the ORM identity map, so a row
        # already loaded this session is stale after the UPDATE — refresh it.
        await self._session.refresh(row)
        return row

    async def set_status(
        self,
        feature_id: str,
        status: FeatureStatus,
        detail: dict[str, Any] | None = None,
        verified_at: datetime | None = None,
    ) -> FeatureModel:
        """Update status, optional verified_at, and persist the verify-detail JSON."""
        import json as _json

        from sqlalchemy import update as _update

        row = await self.get_by_id(feature_id)
        if row is None:
            raise KeyError(f"Feature {feature_id!r} not found")
        # Single guarded UPDATE keyed on id instead of read-modify-write: the
        # ORM dirty-write form lets a concurrent status write be lost between the
        # SELECT above and the flush. Only the columns actually supplied are set.
        values: dict[str, Any] = {"status": status.value}
        if verified_at is not None:
            values["verified_at"] = verified_at
        if detail is not None:
            values["last_verify_detail"] = _json.dumps(detail)
        guard = _update(FeatureModel).where(FeatureModel.id == feature_id).values(**values)
        res = await self._session.execute(guard)
        if (cast("CursorResult[Any]", res).rowcount or 0) != 1:
            raise KeyError(f"Feature {feature_id!r} not found")
        await self._session.flush()
        await self._session.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_name(self, name: str, project_id: str | None = None) -> FeatureModel | None:
        """Return a named feature within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.name == name)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, feature_id: str, project_id: str | None = None) -> FeatureModel | None:
        """Return a feature by identifier within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.id == feature_id)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self, limit: int | None = None, offset: int = 0, project_id: str | None = None
    ) -> list[FeatureModel]:
        """List a bounded feature page within the resolved tenant scope."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        status: FeatureStatus,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[FeatureModel]:
        """List a bounded page of features with the requested status."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.status == status.value)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_category(
        self,
        category: str,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[FeatureModel]:
        """List a bounded page of features in the requested category."""
        _pid = self._resolve_pid(project_id)
        stmt = select(FeatureModel).where(FeatureModel.category == category)
        if _pid is not None:
            stmt = stmt.where(FeatureModel.project_id == _pid)
        stmt = stmt.offset(offset).limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class SpendRepository:
    """Persistence for rolling-window spend records.

    Each row represents one spend event recorded by :class:`SpendLimiter`.
    ``ts`` is an epoch float (e.g. from ``time.monotonic()`` or
    ``time.time()``); the rolling-window math compares timestamps as plain
    floats.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def add(
        self,
        ts: float,
        cost_usd: float,
        kind: str,
        project_id: str | None = None,
        model: str | None = None,
    ) -> SpendRecordModel:
        """Persist a spend event and return the new row.

        Args:
            ts:         Epoch float timestamp of the event.
            cost_usd:   Amount spent in USD.
            kind:       Resource kind (e.g. ``"token"``, ``"infra"``).
            project_id: Optional project scope.
            model:      Optional model identifier.

        Returns:
            The newly created :class:`SpendRecordModel` instance.
        """
        row = SpendRecordModel(
            ts=ts,
            cost_usd=cost_usd,
            kind=kind,
            project_id=project_id,
            model=model,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_since(
        self,
        since_epoch: float,
        project_id: str | None = None,
    ) -> list[SpendRecordModel]:
        """Return all records with ``ts >= since_epoch``.

        Args:
            since_epoch: Lower bound for ``ts`` (inclusive).
            project_id:  When set, restrict to records for this project.

        Returns:
            List of :class:`SpendRecordModel` rows ordered by ``ts`` ascending.
        """
        stmt = select(SpendRecordModel).where(SpendRecordModel.ts >= since_epoch)
        if project_id is not None:
            stmt = stmt.where(SpendRecordModel.project_id == project_id)
        stmt = stmt.order_by(SpendRecordModel.ts.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def total_since(
        self,
        since_epoch: float,
        project_id: str | None = None,
    ) -> float:
        """Sum of ``cost_usd`` for all records with ``ts >= since_epoch``.

        Args:
            since_epoch: Lower bound for ``ts`` (inclusive).
            project_id:  When set, restrict to records for this project.

        Returns:
            Total spend in USD (0.0 when no matching records).
        """
        from sqlalchemy import func

        stmt = select(func.sum(SpendRecordModel.cost_usd)).where(SpendRecordModel.ts >= since_epoch)
        if project_id is not None:
            stmt = stmt.where(SpendRecordModel.project_id == project_id)
        result = await self._session.execute(stmt)
        total: float | None = result.scalar_one_or_none()
        return float(total) if total is not None else 0.0


class RoleRunRepository:
    """Persistence for per-project role-run records.

    Each row records that a named role executed once for a given project.
    ``count_by_role`` returns the {role: run_count} map used by the
    accounting ledger.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def record(self, project_id: str | None, role: str) -> RoleRunModel:
        """Insert a single role-run record."""
        row = RoleRunModel(project_id=project_id, role=role)
        self._session.add(row)
        await self._session.flush()
        return row

    async def count_by_role(self, project_id: str | None = None) -> dict[str, int]:
        """Return {role: count} for the given project_id (or all if None).

        Aggregated in SQL via ``GROUP BY role`` rather than loading every
        row and counting in Python (P8).
        """
        from sqlalchemy import func

        stmt = select(RoleRunModel.role, func.count()).group_by(RoleRunModel.role)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return {role: count for role, count in result.all()}

    async def list_all(
        self,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RoleRunModel]:
        """List a bounded page of role runs, optionally scoped to a project."""
        stmt = select(RoleRunModel)
        if project_id is not None:
            stmt = stmt.where(RoleRunModel.project_id == project_id)
        stmt = stmt.offset(offset).limit(min(limit, _DEFAULT_LIST_LIMIT) if limit is not None else _DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# HumanTodo — bot→human request store (separate from agent TodoModel)
# ---------------------------------------------------------------------------

HUMAN_TODO_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "done", "dismissed", "superseded"})
HUMAN_TODO_TERMINAL: frozenset[str] = frozenset({"done", "dismissed", "superseded"})
HUMAN_TODO_CATEGORIES: frozenset[str] = frozenset(
    {
        "permission_escalation",
        "external_action",
        "decision",
        "input_request",
        "blocker",
    }
)
HUMAN_TODO_PRIORITIES: frozenset[str] = frozenset({"low", "medium", "high", "urgent"})

_HUMAN_TODO_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "done", "dismissed", "superseded"}),
    "in_progress": frozenset({"done", "dismissed", "superseded", "open"}),
    "done": frozenset(),
    "dismissed": frozenset(),
    "superseded": frozenset(),
}


class HumanTodoRepository:
    """Persistence + state-machine for bot→human requests.

    Separate from :class:`TodoRepository` (agent todos). The link between the
    two is ``parent_agent_todo_id``: when a human-todo with a parent is filed,
    the parent agent todo transitions to ``blocked_on_human``; when the
    human-todo resolves (done/dismissed), the parent moves back to ``queued``
    (done) or ``cancelled`` (dismissed). The blocking integration is opt-in:
    a human-todo filed without ``parent_agent_todo_id`` is just a logged need.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the human-request state machine with a database session."""
        self._session = session

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in HUMAN_TODO_CATEGORIES:
            raise ValueError(f"invalid category {category!r}; must be one of: {sorted(HUMAN_TODO_CATEGORIES)}")

    @staticmethod
    def _validate_priority(priority: str) -> None:
        if priority not in HUMAN_TODO_PRIORITIES:
            raise ValueError(f"invalid priority {priority!r}; must be one of: {sorted(HUMAN_TODO_PRIORITIES)}")

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        allowed = _HUMAN_TODO_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise InvalidTransitionError(f"invalid human-todo transition: {current!r} -> {target!r}")

    async def create(
        self,
        *,
        agent_id: str,
        title: str,
        body: str,
        category: str,
        priority: str = "medium",
        parent_agent_todo_id: str | None = None,
        session_id: str | None = None,
        due_at: datetime | None = None,
        tags: list[str] | None = None,
    ) -> HumanTodoModel:
        """Validate, persist, and return an open human request.

        Raises:
            ValueError: If required text, category, or priority is invalid.
        """
        if not title or not title.strip():
            raise ValueError("title must not be empty")
        if not body or not body.strip():
            raise ValueError("body must not be empty")
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must not be empty")
        self._validate_category(category)
        self._validate_priority(priority)
        import json as _json

        row = HumanTodoModel(
            agent_id=agent_id,
            title=title.strip(),
            body=body,
            category=category,
            priority=priority,
            parent_agent_todo_id=parent_agent_todo_id,
            session_id=session_id,
            due_at=due_at,
            tags=_json.dumps(tags or []),
            status="open",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, human_todo_id: str) -> HumanTodoModel | None:
        """Return a human request by identifier."""
        stmt = select(HumanTodoModel).where(HumanTodoModel.id == human_todo_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_open(
        self,
        filter_category: str | None = None,
        filter_agent_id: str | None = None,
        filter_priority: str | None = None,
    ) -> list[HumanTodoModel]:
        """List bounded open human requests matching optional filters."""
        stmt = select(HumanTodoModel).where(HumanTodoModel.status == "open")
        if filter_category is not None:
            stmt = stmt.where(HumanTodoModel.category == filter_category)
        if filter_agent_id is not None:
            stmt = stmt.where(HumanTodoModel.agent_id == filter_agent_id)
        if filter_priority is not None:
            stmt = stmt.where(HumanTodoModel.priority == filter_priority)
        stmt = stmt.order_by(HumanTodoModel.created_at.asc()).limit(_DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        agent_id: str | None = None,
    ) -> list[HumanTodoModel]:
        """List a bounded page of human requests matching optional filters."""
        stmt = select(HumanTodoModel)
        if status is not None:
            stmt = stmt.where(HumanTodoModel.status == status)
        if category is not None:
            stmt = stmt.where(HumanTodoModel.category == category)
        if priority is not None:
            stmt = stmt.where(HumanTodoModel.priority == priority)
        if agent_id is not None:
            stmt = stmt.where(HumanTodoModel.agent_id == agent_id)
        stmt = (
            stmt.order_by(HumanTodoModel.created_at.desc())
            .offset(max(0, offset))
            .limit(min(limit, _DEFAULT_LIST_LIMIT))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_changed_since(self, since: datetime) -> list[HumanTodoModel]:
        """List bounded human requests updated at or after a timestamp."""
        stmt = (
            select(HumanTodoModel)
            .where(HumanTodoModel.updated_at >= since)
            .order_by(HumanTodoModel.updated_at.asc())
            .limit(_DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _transition(
        self,
        human_todo_id: str,
        target: str,
        *,
        human_resolver: str | None = None,
        resolution_text: str | None = None,
    ) -> HumanTodoModel:
        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        self._validate_transition(row.status, target)
        now = datetime.now(UTC)
        row.status = target
        row.updated_at = now
        if human_resolver is not None:
            row.human_resolver = human_resolver
        if resolution_text is not None:
            row.human_resolution = resolution_text
        if target in HUMAN_TODO_TERMINAL:
            row.resolved_at = now
        await self._session.flush()
        return row

    async def mark_done(
        self,
        human_todo_id: str,
        human_resolver: str,
        resolution_text: str,
    ) -> HumanTodoModel:
        """Resolve a human request with a nonempty resolution."""
        if not resolution_text or not resolution_text.strip():
            raise ValueError("resolution_text must not be empty")
        return await self._transition(
            human_todo_id,
            "done",
            human_resolver=human_resolver,
            resolution_text=resolution_text,
        )

    async def mark_in_progress(self, human_todo_id: str) -> HumanTodoModel:
        """Transition an open human request to in progress."""
        return await self._transition(human_todo_id, "in_progress")

    async def dismiss(
        self,
        human_todo_id: str,
        human_resolver: str,
        reason: str,
    ) -> HumanTodoModel:
        """Dismiss a human request with a nonempty reason."""
        if not reason or not reason.strip():
            raise ValueError("dismiss reason must not be empty")
        return await self._transition(
            human_todo_id,
            "dismissed",
            human_resolver=human_resolver,
            resolution_text=reason,
        )

    async def supersede(
        self,
        human_todo_id: str,
        new_id: str,
        reason: str,
    ) -> HumanTodoModel:
        """Mark a human request as replaced by another request."""
        return await self._transition(
            human_todo_id,
            "superseded",
            resolution_text=f"superseded by {new_id}: {reason}",
        )

    async def get_done_for_parent(self, parent_todo_id: str) -> HumanTodoModel | None:
        """Return the most-recently-resolved DONE human-todo for a parent agent todo.

        Filters in SQL (not Python) so callers are not loading every recent
        human-todo row per dispatch. E12: replaces the N+1 pattern where
        EventLoop._resolve_human_input_for_todo loaded all 50 human-todos and
        filtered in Python.
        """
        stmt = (
            select(HumanTodoModel)
            .where(
                HumanTodoModel.parent_agent_todo_id == parent_todo_id,
                HumanTodoModel.status == "done",
            )
            .order_by(
                HumanTodoModel.resolved_at.desc().nulls_last(),
                HumanTodoModel.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_tag(self, human_todo_id: str, tag: str) -> HumanTodoModel:
        """Add a tag to a human request if it is not already present."""
        import json as _json

        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        tags: list[str] = _json.loads(row.tags or "[]")
        if tag not in tags:
            tags.append(tag)
            row.tags = _json.dumps(tags)
            row.updated_at = datetime.now(UTC)
            await self._session.flush()
        return row

    async def remove_tag(self, human_todo_id: str, tag: str) -> HumanTodoModel:
        """Remove a tag from a human request if it is present."""
        import json as _json

        row = await self.get(human_todo_id)
        if row is None:
            raise InvalidTransitionError(f"human-todo {human_todo_id} not found")
        tags: list[str] = _json.loads(row.tags or "[]")
        if tag in tags:
            tags.remove(tag)
            row.tags = _json.dumps(tags)
            row.updated_at = datetime.now(UTC)
            await self._session.flush()
        return row

    async def search(self, query: str) -> list[HumanTodoModel]:
        """Search bounded human-request titles and bodies for literal text."""
        if not query or not query.strip():
            return []
        from sqlalchemy import or_

        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = (
            select(HumanTodoModel)
            .where(
                or_(
                    HumanTodoModel.title.like(like, escape="\\"),
                    HumanTodoModel.body.like(like, escape="\\"),
                )
            )
            .order_by(HumanTodoModel.created_at.desc())
            .limit(_DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class RemediationActionRepository:
    """Persistence for remediation audit-trail rows.

    The dispatcher writes one row per action via :meth:`record`; operators
    read the history via :meth:`list_for_project` / :meth:`list_since`.
    Reads use the same session as the caller; writes ``flush`` so the row
    is visible in the caller's transaction without committing.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def record(
        self,
        *,
        blocked_todo_id: str,
        action_kind: str,
        blocker_kind: str,
        summary: str = "",
        detail: str = "{}",
        project_id: str | None = None,
        ok: bool = True,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> RemediationActionModel:
        """Persist and return a remediation action audit record."""
        row = RemediationActionModel(
            blocked_todo_id=blocked_todo_id,
            action_kind=action_kind,
            blocker_kind=blocker_kind,
            summary=summary,
            detail=detail,
            project_id=project_id,
            ok=ok,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, remediation_id: str) -> RemediationActionModel | None:
        """Return a remediation action by identifier."""
        stmt = select(RemediationActionModel).where(RemediationActionModel.id == remediation_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RemediationActionModel]:
        """List a bounded remediation-history page, optionally by project."""
        stmt = select(RemediationActionModel).order_by(RemediationActionModel.created_at.desc())
        if project_id is not None:
            stmt = stmt.where(RemediationActionModel.project_id == project_id)
        stmt = stmt.offset(max(0, offset)).limit(min(limit, _DEFAULT_LIST_LIMIT))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_since(self, since: datetime, project_id: str | None = None) -> list[RemediationActionModel]:
        """List remediation actions recorded since a timestamp."""
        stmt = (
            select(RemediationActionModel)
            .where(RemediationActionModel.created_at >= since)
            .order_by(RemediationActionModel.created_at.asc())
            .limit(_DEFAULT_LIST_LIMIT)
        )
        if project_id is not None:
            stmt = stmt.where(RemediationActionModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def exists_recent(self, blocked_todo_id: str, since: datetime) -> bool:
        """True if an action was already recorded for this blocked task since ``since``.

        Used by the auto-remediation tick phase (#52) for idempotency: a
        finding whose ``blocked_todo_id`` already has an audit row within the
        configured ``retry_delay_hours`` cooldown is skipped so a
        still-blocked task doesn't get a fresh dispatch/retry/human-todo
        filed on every single tick before the operator has had time to react.
        """
        stmt = (
            select(RemediationActionModel.id)
            .where(RemediationActionModel.blocked_todo_id == blocked_todo_id)
            .where(RemediationActionModel.created_at >= since)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def exists_recent_by_action(
        self,
        blocked_todo_id: str,
        action_kind: str,
        since: datetime,
    ) -> bool:
        """True if a (target, action) pair was already recorded since ``since``.

        C25: Dedupe on (action, target, window) — both ``blocked_todo_id``
        AND ``action_kind`` must match.  A prior ``schedule_retry`` on the
        same target is NOT a duplicate of a new ``dispatch_agent``.
        """
        stmt = (
            select(RemediationActionModel.id)
            .where(RemediationActionModel.blocked_todo_id == blocked_todo_id)
            .where(RemediationActionModel.action_kind == action_kind)
            .where(RemediationActionModel.created_at >= since)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_by_idempotency_key(self, idempotency_key: str) -> list[RemediationActionModel]:
        """List actions sharing an idempotency key in creation order."""
        stmt = (
            select(RemediationActionModel)
            .where(RemediationActionModel.idempotency_key == idempotency_key)
            .order_by(RemediationActionModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ModelPerformanceRepository:
    """Persistence for model call logs and aggregated performance stats.

    Two-table design:

    * ``model_call_logs`` — one row per model invocation, immutable.
      Written by the worker HTTP path and the EventLoop in-process runner
      path so every model call is centrally observable.
    * ``model_performance`` — pre-aggregated per-profile stats, updated
      periodically by ``refresh_recent_stats()`` for fast dashboard reads.

    All write methods accept an optional ``session`` override so callers
    that already hold an active session (e.g. the EventLoop tick) can share
    it rather than opening a new one.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a lazy session factory."""
        self._session = session
        self._session_factory = session_factory

    # ── recording ───────────────────────────────────────────────────────

    async def record_call(
        self,
        *,
        service: str,
        model_name: str,
        model_profile_id: str,
        task_type: str = "generation",
        work_type: str | None = None,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float = 0.0,
        todo_id: str | None = None,
        job_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        session: AsyncSession | None = None,
    ) -> ModelCallLogModel:
        """Persist a single model call log entry.

        When *session* is provided it is used directly; otherwise a new
        session is opened from the factory (if available) or an error is
        raised.  Returns the newly created :class:`ModelCallLogModel`.

        This method does NOT update the aggregated ``model_performance``
        table — call :meth:`refresh_recent_stats` to recompute aggregates
        in batch.
        """
        eff_session = session or self._resolve_session()
        row = ModelCallLogModel(
            todo_id=todo_id,
            job_id=job_id,
            service=service,
            model_name=model_name,
            model_profile_id=model_profile_id,
            task_type=task_type,
            work_type=work_type,
            success=success,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )
        eff_session.add(row)
        await eff_session.flush()
        return row

    def record_call_sync(
        self,
        *,
        service: str,
        model_name: str,
        model_profile_id: str,
        task_type: str = "generation",
        work_type: str | None = None,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float = 0.0,
        todo_id: str | None = None,
        job_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Synchronous version of :meth:`record_call`.

        Intended for use from ``asyncio.to_thread`` worker paths where an
        async session is not available.  Opens and commits its own session
        synchronously (blocking).  Fail-soft: any exception is logged and
        swallowed so a broken repo never kills the model call.
        """
        try:
            import asyncio as _asyncio

            coro = self.record_call(
                service=service,
                model_name=model_name,
                model_profile_id=model_profile_id,
                task_type=task_type,
                work_type=work_type,
                success=success,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                todo_id=todo_id,
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
                session=None,
            )
            _asyncio.run(coro)
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "ModelPerformanceRepository.record_call_sync failed: %s",
                exc,
                exc_info=True,
            )

    # ── refresh (aggregated stats) ──────────────────────────────────────

    async def refresh_recent_stats(
        self,
        session: AsyncSession | None = None,
        window_hours: float = 24.0,
    ) -> int:
        """Recompute the ``model_performance`` table from recent call logs.

        Queries all call log rows within the rolling *window_hours* window,
        groups by ``model_profile_id``, and upserts each profile's aggregate
        row in ``model_performance``.  Returns the number of profiles
        refreshed.
        """
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        eff_session = session or self._resolve_session()
        cutoff = _dt.now(_UTC) - _td(hours=window_hours)

        stmt = (
            select(
                ModelCallLogModel.model_profile_id,
                _func.max(ModelCallLogModel.model_name).label("model_name"),
                _func.max(ModelCallLogModel.service).label("service"),
                _func.count().label("total_calls"),
                _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
                (_func.count() - _func.sum(_func.cast(ModelCallLogModel.success, _Integer))).label("failed_calls"),
                _func.coalesce(_func.sum(ModelCallLogModel.input_tokens), 0).label("total_input_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.output_tokens), 0).label("total_output_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
                _func.avg(ModelCallLogModel.duration_ms).label("avg_duration_ms"),
                _func.max(ModelCallLogModel.created_at).label("last_call_at"),
                _func.min(ModelCallLogModel.created_at).label("first_call_at"),
            )
            .where(ModelCallLogModel.created_at >= cutoff)
            .group_by(ModelCallLogModel.model_profile_id)
        )
        result = await eff_session.execute(stmt)
        rows = result.all()

        refreshed = 0
        now = _dt.now(_UTC)
        for row in rows:
            profile_id = row.model_profile_id
            existing = await eff_session.execute(
                select(ModelPerformanceModel).where(ModelPerformanceModel.model_profile_id == profile_id)
            )
            perf: ModelPerformanceModel | None = existing.scalar_one_or_none()
            if perf is None:
                perf = ModelPerformanceModel(
                    model_profile_id=profile_id,
                    model_name=str(row.model_name or ""),
                    service=str(row.service or ""),
                )
                eff_session.add(perf)
            perf.model_name = str(row.model_name or perf.model_name)
            perf.service = str(row.service or perf.service)
            perf.total_calls = int(row.total_calls or 0)
            perf.successful_calls = int(row.successful_calls or 0)
            perf.failed_calls = int(row.failed_calls or 0)
            perf.total_input_tokens = int(row.total_input_tokens or 0)
            perf.total_output_tokens = int(row.total_output_tokens or 0)
            perf.total_cost_usd = float(row.total_cost_usd or 0.0)
            perf.avg_duration_ms = float(row.avg_duration_ms) if row.avg_duration_ms is not None else 0.0
            perf.last_call_at = row.last_call_at
            perf.first_call_at = row.first_call_at
            perf.updated_at = now
            refreshed += 1

        if refreshed:
            await eff_session.flush()
        return refreshed

    # ── queries ─────────────────────────────────────────────────────────

    async def get_stats_by_model(
        self,
        model_profile_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[ModelPerformanceModel]:
        """Return aggregated performance rows, optionally filtered by profile.

        When *model_profile_id* is None, returns stats for ALL profiles.
        """
        eff_session = session or self._resolve_session()
        stmt = select(ModelPerformanceModel)
        if model_profile_id is not None:
            stmt = stmt.where(ModelPerformanceModel.model_profile_id == model_profile_id)
        stmt = stmt.order_by(ModelPerformanceModel.total_cost_usd.desc())
        result = await eff_session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_calls(
        self,
        limit: int = 100,
        model_profile_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[ModelCallLogModel]:
        """Return the most recent call log entries."""
        eff_session = session or self._resolve_session()
        stmt = select(ModelCallLogModel).order_by(ModelCallLogModel.created_at.desc()).limit(min(limit, 1000))
        if model_profile_id is not None:
            stmt = stmt.where(ModelCallLogModel.model_profile_id == model_profile_id)
        result = await eff_session.execute(stmt)
        return list(result.scalars().all())

    async def get_stats_by_service(
        self,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return aggregated performance grouped by service/provider."""
        from sqlalchemy import func as _func

        eff_session = session or self._resolve_session()
        stmt = (
            select(
                ModelPerformanceModel.service,
                _func.count().label("profile_count"),
                _func.sum(ModelPerformanceModel.total_calls).label("total_calls"),
                _func.sum(ModelPerformanceModel.successful_calls).label("successful_calls"),
                _func.sum(ModelPerformanceModel.total_cost_usd).label("total_cost"),
            )
            .group_by(ModelPerformanceModel.service)
            .order_by(_func.sum(ModelPerformanceModel.total_cost_usd).desc())
        )
        result = await eff_session.execute(stmt)
        return [
            {
                "service": r.service,
                "profile_count": int(r.profile_count),
                "total_calls": int(r.total_calls or 0),
                "successful_calls": int(r.successful_calls or 0),
                "total_cost_usd": float(r.total_cost or 0.0),
            }
            for r in result.all()
        ]

    async def get_daily_stats(
        self,
        days: int = 7,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily call volume and cost aggregates."""
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        eff_session = session or self._resolve_session()
        cutoff = _dt.now(_UTC) - _td(days=days)
        stmt = (
            select(
                _func.date(ModelCallLogModel.created_at).label("day"),
                _func.count().label("total_calls"),
                _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
                _func.coalesce(_func.sum(ModelCallLogModel.input_tokens), 0).label("total_input_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.output_tokens), 0).label("total_output_tokens"),
                _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
            )
            .where(ModelCallLogModel.created_at >= cutoff)
            .group_by(_func.date(ModelCallLogModel.created_at))
            .order_by(_func.date(ModelCallLogModel.created_at).desc())
        )
        result = await eff_session.execute(stmt)
        return [
            {
                "date": str(r.day),
                "total_calls": int(r.total_calls),
                "successful_calls": int(r.successful_calls or 0),
                "total_input_tokens": int(r.total_input_tokens or 0),
                "total_output_tokens": int(r.total_output_tokens or 0),
                "total_cost_usd": float(r.total_cost_usd or 0.0),
            }
            for r in result.all()
        ]

    # ── router-facing queries ───────────────────────────────────────────

    async def get_ranking(
        self,
        task_type: str,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return per-(service, model) outcome stats for *task_type*.

        Grouped from the immutable ``model_call_logs`` table so the router
        always sees the latest recorded outcomes (no aggregate refresh
        required).  Each row carries ``success_rate``, ``avg_latency_ms``,
        ``avg_cost_usd`` and ``sample_count``.
        """
        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        eff_session = session or self._resolve_session()
        stmt = (
            select(
                ModelCallLogModel.service,
                ModelCallLogModel.model_name,
                ModelCallLogModel.model_profile_id,
                _func.count().label("sample_count"),
                _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successes"),
                _func.coalesce(_func.avg(ModelCallLogModel.duration_ms), 0.0).label("avg_latency_ms"),
                _func.coalesce(_func.avg(ModelCallLogModel.cost_usd), 0.0).label("avg_cost_usd"),
            )
            .where(ModelCallLogModel.task_type == task_type)
            .group_by(
                ModelCallLogModel.service,
                ModelCallLogModel.model_name,
                ModelCallLogModel.model_profile_id,
            )
        )
        rows = (await eff_session.execute(stmt)).all()
        ranking: list[dict[str, Any]] = []
        for row in rows:
            sample_count = int(row.sample_count or 0)
            successes = int(row.successes or 0)
            ranking.append(
                {
                    "service": str(row.service or ""),
                    "model_name": str(row.model_name or ""),
                    "model_profile_id": str(row.model_profile_id or ""),
                    "sample_count": sample_count,
                    "success_rate": round(successes / sample_count, 4) if sample_count else 0.0,
                    "avg_latency_ms": float(row.avg_latency_ms or 0.0),
                    "avg_cost_usd": float(row.avg_cost_usd or 0.0),
                }
            )
        return ranking

    async def get_best_model(
        self,
        task_type: str,
        min_calls: int = 3,
        prefer_cost: bool = False,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        """Return the best (service, model_name) for *task_type*.

        Considers only rows with ``sample_count >= min_calls``.  By default
        the highest success rate wins (cost as tiebreak); with
        ``prefer_cost`` the lowest average cost wins (success as tiebreak).
        Returns ``None`` when no model meets the minimum sample count.
        """
        ranking = await self.get_ranking(task_type, session=session)
        eligible = [r for r in ranking if int(r.get("sample_count", 0)) >= min_calls]
        if not eligible:
            return None
        if prefer_cost:
            eligible.sort(
                key=lambda r: (
                    float(r.get("avg_cost_usd", 0.0)),
                    -float(r.get("success_rate", 0.0)),
                )
            )
        else:
            eligible.sort(
                key=lambda r: (
                    -float(r.get("success_rate", 0.0)),
                    float(r.get("avg_cost_usd", 0.0)),
                )
            )
        best = eligible[0]
        if prefer_cost:
            composite = round(1.0 / (1.0 + float(best.get("avg_cost_usd", 0.0))), 4)
        else:
            composite = float(best.get("success_rate", 0.0))
        return {
            "service": str(best.get("service", "openai")),
            "model_name": str(best.get("model_name", "")),
            "model_profile_id": str(best.get("model_profile_id", "")),
            "composite_score": composite,
            "sample_count": int(best.get("sample_count", 0)),
        }

    async def get_summary(
        self,
        service: str | None = None,
        task_type: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Return aggregated per-model outcome summaries for dashboards.

        Groups call logs by (service, task_type, model).  Optional filters
        narrow to a single *service* and/or *task_type*.
        """
        from sqlalchemy import Integer as _Integer
        from sqlalchemy import func as _func

        eff_session = session or self._resolve_session()
        stmt = select(
            ModelCallLogModel.service,
            ModelCallLogModel.task_type,
            ModelCallLogModel.model_name,
            ModelCallLogModel.model_profile_id,
            _func.count().label("total_calls"),
            _func.sum(_func.cast(ModelCallLogModel.success, _Integer)).label("successful_calls"),
            _func.coalesce(_func.sum(ModelCallLogModel.cost_usd), 0.0).label("total_cost_usd"),
            _func.coalesce(_func.avg(ModelCallLogModel.duration_ms), 0.0).label("avg_duration_ms"),
        )
        if service is not None:
            stmt = stmt.where(ModelCallLogModel.service == service)
        if task_type is not None:
            stmt = stmt.where(ModelCallLogModel.task_type == task_type)
        stmt = stmt.group_by(
            ModelCallLogModel.service,
            ModelCallLogModel.task_type,
            ModelCallLogModel.model_name,
            ModelCallLogModel.model_profile_id,
        )
        rows = (await eff_session.execute(stmt)).all()
        summary: list[dict[str, Any]] = []
        for row in rows:
            total = int(row.total_calls or 0)
            successful = int(row.successful_calls or 0)
            summary.append(
                {
                    "service": str(row.service or ""),
                    "task_type": str(row.task_type or ""),
                    "model_name": str(row.model_name or ""),
                    "model_profile_id": str(row.model_profile_id or ""),
                    "total_calls": total,
                    "successful_calls": successful,
                    "failed_calls": total - successful,
                    "success_rate": round(successful / total, 4) if total else 0.0,
                    "total_cost_usd": float(row.total_cost_usd or 0.0),
                    "avg_duration_ms": float(row.avg_duration_ms or 0.0),
                }
            )
        return summary

    # ── helpers ─────────────────────────────────────────────────────────

    def _resolve_session(self) -> AsyncSession:
        """Return a usable async session, creating one from the factory if needed.

        S28: previously raised RuntimeError when session_factory was present but
        no concrete session was provided — making every production record_call_sync()
        silently fail (bare except swallowed RuntimeError). Now lazily creates a
        session from the factory when one is available.
        """
        if self._session is not None:
            return self._session
        if self._session_factory is not None:
            session = self._session_factory()
            self._session = session
            return session
        raise RuntimeError(
            "ModelPerformanceRepository._resolve_session: no session configured and no session_factory available."
        )


class SlurmJobRepository:
    """Persistence for Slurm job lifecycle tracking.

    Each row represents one Slurm job submitted by a daemon process.
    Used by the shutdown hook to scancel active jobs and by startup
    to detect orphaned jobs from a prior daemon instance.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(self, data: dict[str, Any]) -> SlurmJobModel:
        """Persist and return a Slurm job record."""
        row = SlurmJobModel(**data)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_job_id(self, job_id: str) -> SlurmJobModel | None:
        """Return a Slurm job by scheduler identifier."""
        stmt = select(SlurmJobModel).where(SlurmJobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        cost_incurred: float | None = None,
    ) -> bool:
        """Update status (and optional cost) for a Slurm job by job_id.

        Returns True if at least one row was updated, False otherwise.
        """
        from sqlalchemy import update as _update

        values: dict[str, Any] = {"status": status}
        if cost_incurred is not None:
            values["cost_incurred"] = cost_incurred
        if status in ("completed", "failed", "cancelled"):
            values["completed_at"] = datetime.now(UTC)
        guard = _update(SlurmJobModel).where(SlurmJobModel.job_id == job_id).values(**values)
        res = await self._session.execute(guard)
        await self._session.flush()
        return (cast("CursorResult[Any]", res).rowcount or 0) > 0

    async def list_active(self, daemon_pid: int | None = None) -> list[SlurmJobModel]:
        """Return jobs with status 'submitted' or 'running'.

        When ``daemon_pid`` is provided, only jobs matching that pid are returned.
        """
        stmt = (
            select(SlurmJobModel).where(SlurmJobModel.status.in_(["submitted", "running"])).limit(_DEFAULT_LIST_LIMIT)
        )
        if daemon_pid is not None:
            stmt = stmt.where(SlurmJobModel.daemon_pid == daemon_pid)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_deployment(self, deployment_id: str) -> list[SlurmJobModel]:
        """List bounded Slurm jobs associated with a deployment."""
        stmt = select(SlurmJobModel).where(SlurmJobModel.deployment_id == deployment_id).limit(_DEFAULT_LIST_LIMIT)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_orphans(self, current_pid: int) -> list[SlurmJobModel]:
        """Return jobs with status 'running' where daemon_pid != current_pid."""
        stmt = (
            select(SlurmJobModel)
            .where(
                SlurmJobModel.status == "running",
                SlurmJobModel.daemon_pid != current_pid,
            )
            .limit(_DEFAULT_LIST_LIMIT)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class MemoryRepository:
    """Persistence for agent-memory key-value records (G1).

    Each record is scoped to an (agent_id, namespace) pair and keyed by
    ``key``. TTL support allows automatic expiry of transient entries.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a transactional session factory."""
        self._session = session
        self._session_factory = session_factory

    @contextlib.asynccontextmanager
    async def _resolve_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._session is not None:
            yield self._session
        elif self._session_factory is not None:
            async with self._session_factory() as session, session.begin():
                yield session
        else:
            raise RuntimeError("MemoryRepository: no session or session_factory")

    async def _get_with_session(
        self,
        session: AsyncSession,
        agent_id: str,
        key: str,
        namespace: str,
        project_id: str | None = None,
    ) -> MemoryRecordModel | None:
        stmt = select(MemoryRecordModel).where(
            MemoryRecordModel.agent_id == agent_id,
            MemoryRecordModel.key == key,
            MemoryRecordModel.namespace == namespace,
        )
        if project_id is None:
            stmt = stmt.where(MemoryRecordModel.project_id.is_(None))
        else:
            stmt = stmt.where(MemoryRecordModel.project_id == project_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None and self._is_expired(row):
            await session.delete(row)
            await session.flush()
            return None
        return row

    async def get(
        self,
        agent_id: str,
        key: str,
        namespace: str = "default",
        project_id: str | None = None,
    ) -> MemoryRecordModel | None:
        """Return an unexpired memory value from an agent namespace."""
        async with self._resolve_session() as session:
            return await self._get_with_session(session, agent_id, key, namespace, project_id)

    async def set(
        self,
        agent_id: str,
        key: str,
        value: str | None = None,
        namespace: str = "default",
        project_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> MemoryRecordModel:
        """Upsert and return a scoped agent-memory value with optional TTL.

        A ``value`` of ``None`` preserves the existing value on update (used by
        the API when the client omits the field); on create it stores ``""``.
        """
        async with self._resolve_session() as session:
            now = datetime.now(UTC)
            stmt = select(MemoryRecordModel).where(
                MemoryRecordModel.agent_id == agent_id,
                MemoryRecordModel.key == key,
                MemoryRecordModel.namespace == namespace,
            )
            if project_id is None:
                stmt = stmt.where(MemoryRecordModel.project_id.is_(None))
            else:
                stmt = stmt.where(MemoryRecordModel.project_id == project_id)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                existing = MemoryRecordModel(
                    agent_id=agent_id,
                    key=key,
                    value=value or "",
                    namespace=namespace,
                    project_id=project_id,
                    ttl_seconds=ttl_seconds,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
            else:
                if value is not None:
                    existing.value = value
                existing.ttl_seconds = ttl_seconds
                existing.updated_at = now
            await session.flush()
            await session.refresh(existing)
            return existing

    async def delete(
        self,
        agent_id: str,
        key: str,
        namespace: str = "default",
        project_id: str | None = None,
    ) -> bool:
        """Delete a scoped agent-memory value and report whether it existed."""
        async with self._resolve_session() as session:
            row = await self._get_with_session(session, agent_id, key, namespace, project_id)
            if row is None:
                return False
            await session.delete(row)
            await session.flush()
            return True

    async def list_by_namespace(
        self,
        agent_id: str,
        namespace: str = "default",
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecordModel]:
        """List bounded, unexpired memory values for an agent namespace."""
        async with self._resolve_session() as session:
            stmt = (
                select(MemoryRecordModel)
                .where(
                    MemoryRecordModel.agent_id == agent_id,
                )
                .order_by(MemoryRecordModel.key)
                .limit(min(limit, _DEFAULT_LIST_LIMIT))
            )
            if namespace != "*":
                stmt = stmt.where(MemoryRecordModel.namespace == namespace)
            if project_id is not None:
                stmt = stmt.where(MemoryRecordModel.project_id == project_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            return [r for r in rows if not self._is_expired(r)]

    async def purge_expired(self) -> int:
        """Delete expired memory records and return the number removed."""
        from sqlalchemy import delete, func

        async with self._resolve_session() as session:
            elapsed_seconds = (func.julianday("now") - func.julianday(MemoryRecordModel.created_at)) * 86400.0
            stmt = delete(MemoryRecordModel).where(
                MemoryRecordModel.ttl_seconds.isnot(None),
                elapsed_seconds > MemoryRecordModel.ttl_seconds,
            )
            result = await session.execute(stmt)
            purged = int(cast("CursorResult[Any]", result).rowcount or 0)
            if purged:
                await session.flush()
            return purged

    @staticmethod
    def _is_expired(row: MemoryRecordModel | None) -> bool:
        if row is None or row.ttl_seconds is None:
            return False
        now = datetime.now(UTC)
        created = row.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (now - created).total_seconds() > row.ttl_seconds
