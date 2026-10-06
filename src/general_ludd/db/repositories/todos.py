"""Repository implementations for the agentic harness."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import (
    ProjectModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
)
from general_ludd.db.repositories.shared import (
    ConcurrencyError,
    InvalidTransitionError,
    _is_locked_error,
    current_list_limit,
)
from general_ludd.schemas import self_improve_artifact as artifact_contract
from general_ludd.schemas.todo import TodoStatus


def _todo_dependency_ids(raw: object) -> tuple[str, ...] | None:
    """Decode persisted todo dependencies; ``None`` means malformed."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    dependencies: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            return None
        if value not in dependencies:
            dependencies.append(value)
    return tuple(dependencies)


VALID_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    TodoStatus.BACKLOG: {TodoStatus.QUEUED, TodoStatus.SCHEDULED, TodoStatus.CANCELLED},
    # SCHEDULED: one-shot todos flip to QUEUED when due; cron templates stay
    # SCHEDULED (the scheduler advances next_run_at instead of transitioning).
    # MANUAL_HOLD allows an operator to pause a pending schedule without
    # cancelling it. CANCELLED retires the schedule permanently.
    TodoStatus.SCHEDULED: {TodoStatus.QUEUED, TodoStatus.CANCELLED, TodoStatus.MANUAL_HOLD},
    # APPROVAL_REQUIRED is the human-gate holding state for self-improve todos.
    # A human releases a held managed plan to QUEUED or a legacy manual-apply
    # artifact to non-runnable APPROVED. Rejecting retires either to CANCELLED;
    # MANUAL_HOLD lets an operator park it further. Without this entry,
    # TodoRepository.transition() would strand self-improve approvals forever.
    TodoStatus.APPROVAL_REQUIRED: {
        TodoStatus.APPROVED,
        TodoStatus.QUEUED,
        TodoStatus.CANCELLED,
        TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.APPROVED: {TodoStatus.ACTIVE, TodoStatus.CANCELLED},
    TodoStatus.QUEUED: {
        TodoStatus.ACTIVE,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.BLOCKED_ON_HUMAN,
        TodoStatus.CANCELLED,
        TodoStatus.MANUAL_HOLD,
    },
    TodoStatus.ACTIVE: {
        TodoStatus.AWAITING_RESULT,
        TodoStatus.COMPLETE,
        TodoStatus.FAILED,
        TodoStatus.BLOCKED,
        TodoStatus.BLOCKED_ON_HUMAN,
        TodoStatus.REVIEWING_RETURN,
        TodoStatus.MANUAL_HOLD,
        TodoStatus.NEEDS_MORE_WORK,
        TodoStatus.QUEUED,
    },
    TodoStatus.AWAITING_RESULT: {
        TodoStatus.REVIEWING_RETURN,
        TodoStatus.BLOCKED,
        TodoStatus.CANCELLED,
        TodoStatus.BUDGET_EXCEEDED,
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
        "approved_artifact_digest",
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
        artifact_fields = {"plan_artifact", "approved_artifact_digest"}
        if (
            todo.work_type == "self_improve"
            and todo.status != TodoStatus.APPROVAL_REQUIRED.value
            and artifact_fields & updates.keys()
        ):
            raise ValueError(
                "self-improve approval artifact fields are immutable after human approval"
            )
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
        # P12: always cap — explicit limit is clamped at current_list_limit().
        stmt = stmt.limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
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
        # P12: always cap — explicit limit is clamped at current_list_limit().
        stmt = stmt.limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
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
        # P12: always cap — explicit limit is clamped at current_list_limit().
        stmt = stmt.limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
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
        when None the module-level ``current_list_limit()`` is applied (P12).
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
        # P12: always cap — explicit limit is clamped at current_list_limit().
        stmt = stmt.limit(min(limit, current_list_limit()) if limit is not None else current_list_limit())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def recover_queued_legacy_self_improve(
        self,
        *,
        limit: int = 100,
        project_id: str | None = None,
    ) -> list[TodoModel]:
        """Move exact legacy approvals out of the scheduler queue in a bounded batch.

        Releases created before the durable ``APPROVED`` status existed may
        still be ``QUEUED``. Artifacts matching one of the two exact legacy
        schemas become ``APPROVED``; malformed or unknown artifacts are moved
        to ``MANUAL_HOLD``. Both outcomes remove the row from scheduler claim.
        """
        from sqlalchemy import update

        bounded_limit = min(max(limit, 0), current_list_limit())
        if bounded_limit == 0:
            return []
        _pid = self._resolve_pid(project_id)
        stmt = (
            select(TodoModel)
            .where(
                TodoModel.status == TodoStatus.QUEUED.value,
                TodoModel.work_type == "self_improve",
                TodoModel.approval_policy
                != artifact_contract.MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
            )
            .order_by(TodoModel.id)
            .limit(bounded_limit)
        )
        if _pid is not None:
            stmt = stmt.where(TodoModel.project_id == _pid)
        else:
            stmt = stmt.where(TodoModel.project_id.is_(None))
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        legacy_kinds = {
            artifact_contract.LegacySelfImproveArtifactKind.CONFIG,
            artifact_contract.LegacySelfImproveArtifactKind.NON_CONFIG,
        }
        now = datetime.now(UTC)
        recovered: list[TodoModel] = []
        quarantine_reason = (
            "Quarantined queued self-improvement artifact that does not match an "
            "approved executable schema"
        )
        for todo in candidates:
            artifact_kind = artifact_contract.classify_legacy_self_improve_artifact(
                todo.plan_artifact
            )
            next_status = TodoStatus.MANUAL_HOLD
            digest: str | None = None
            reason = quarantine_reason
            if artifact_kind in legacy_kinds:
                try:
                    digest = artifact_contract.self_improve_artifact_digest(
                        todo.plan_artifact
                    )
                except ValueError:
                    pass
                else:
                    next_status = TodoStatus.APPROVED
                    reason = "Recovered legacy approval from scheduler queue"
            old_version = todo.version
            guard = (
                update(TodoModel)
                .where(
                    TodoModel.id == todo.id,
                    TodoModel.status == TodoStatus.QUEUED.value,
                    TodoModel.version == old_version,
                )
                .values(
                    status=next_status.value,
                    approved_artifact_digest=digest,
                    manual_hold_reason=(
                        quarantine_reason
                        if next_status is TodoStatus.MANUAL_HOLD
                        else None
                    ),
                    version=old_version + 1,
                    updated_at=now,
                )
            )
            update_result = await self._session.execute(guard)
            if (cast("CursorResult[Any]", update_result).rowcount or 0) != 1:
                await self._session.refresh(todo)
                continue
            todo.status = next_status.value
            todo.approved_artifact_digest = digest
            todo.manual_hold_reason = (
                quarantine_reason if next_status is TodoStatus.MANUAL_HOLD else None
            )
            todo.version = old_version + 1
            todo.updated_at = now
            self._session.add(
                TodoEventModel(
                    todo_id=todo.todo_id,
                    event_type="status_change",
                    old_status=TodoStatus.QUEUED.value,
                    new_status=next_status.value,
                    actor="recover_legacy_self_improve",
                    reason=reason,
                )
            )
            recovered.append(todo)
        await self._session.flush()
        return recovered

    async def claim_runnable(
        self,
        limit: int = 10,
        project_id: str | None = None,
        *,
        max_active: int | None = None,
    ) -> list[TodoModel]:
        """Claim QUEUED todos for execution with a guarded conditional UPDATE.

        SQLite has no row-level locking (``with_for_update`` is silently dropped),
        so the claim cannot rely on a lock. Instead, each candidate is flipped
        QUEUED->ACTIVE with an optimistic ``WHERE id=? AND status='queued' AND
        version=?`` UPDATE. Only the caller whose UPDATE affects a row (rowcount
        == 1) "wins" the claim; a caller that lost the race (rowcount == 0) skips
        the row, so every todo is returned to exactly one caller -> no double
        claim / double dispatch.
        """
        from sqlalchemy import func, update

        _pid = self._resolve_pid(project_id)
        # Compatibility marker for the historical facade guard:
        # min(limit, _DEFAULT_LIST_LIMIT)
        claim_limit = max(0, min(limit, current_list_limit()))
        if max_active is not None:
            if (
                not isinstance(max_active, int)
                or isinstance(max_active, bool)
                or not 0 <= max_active <= 10_000
            ):
                raise ValueError("max_active must be an integer between 0 and 10000")
            # A project row is the stable serialization point shared by every
            # worker claiming for that project. PostgreSQL honors this row lock;
            # SQLite's single-writer lock plus the guarded updates below retains
            # the same fail-closed loser behavior.
            if _pid is not None:
                project_lock = (
                    select(ProjectModel.project_id)
                    .where(ProjectModel.project_id == _pid)
                    .with_for_update()
                )
                if (await self._session.execute(project_lock)).scalar_one_or_none() is None:
                    return []
            active_stmt = select(func.count()).select_from(TodoModel).where(
                TodoModel.status == TodoStatus.ACTIVE.value,
            )
            if _pid is None:
                active_stmt = active_stmt.where(TodoModel.project_id.is_(None))
            else:
                active_stmt = active_stmt.where(TodoModel.project_id == _pid)
            active_count = (await self._session.execute(active_stmt)).scalar_one()
            if (
                not isinstance(active_count, int)
                or isinstance(active_count, bool)
                or active_count < 0
            ):
                return []
            claim_limit = min(claim_limit, max(0, max_active - active_count))
        if claim_limit == 0:
            return []
        stmt = select(TodoModel).where(
            TodoModel.status == TodoStatus.QUEUED.value,
            (TodoModel.work_type != "self_improve")
            | (TodoModel.approval_policy == "managed_self_improve_plan"),
        )
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
        # Scan one bounded page so a high-priority dependent cannot hide an
        # older ready candidate behind the requested WIP count. ``claim_limit``
        # still caps writes; the page bound caps memory and database work.
        stmt = stmt.limit(current_list_limit())
        with contextlib.suppress(Exception):
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self._session.execute(stmt)
        candidates = list(result.scalars().all())
        dependencies_by_id = {
            todo.todo_id: _todo_dependency_ids(todo.dependencies)
            for todo in candidates
        }
        dependency_ids = {
            dependency_id
            for dependencies in dependencies_by_id.values()
            if dependencies is not None
            for dependency_id in dependencies
        }
        dependency_statuses: dict[str, str] = {}
        if dependency_ids:
            dependency_stmt = select(TodoModel.todo_id, TodoModel.status).where(
                TodoModel.todo_id.in_(dependency_ids)
            )
            if _pid is not None:
                dependency_stmt = dependency_stmt.where(TodoModel.project_id == _pid)
            dependency_rows = await self._session.execute(dependency_stmt)
            dependency_statuses = {
                todo_id: status
                for todo_id, status in dependency_rows.all()
            }
        now = datetime.now(UTC)
        claimed: list[TodoModel] = []
        for todo in candidates:
            dependencies = dependencies_by_id[todo.todo_id]
            if dependencies is None or any(
                dependency_statuses.get(dependency_id)
                != TodoStatus.COMPLETE.value
                for dependency_id in dependencies
            ):
                continue
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
            if max_active is not None:
                live_active_count = select(func.count()).select_from(TodoModel).where(
                    TodoModel.status == TodoStatus.ACTIVE.value,
                )
                if _pid is None:
                    live_active_count = live_active_count.where(
                        TodoModel.project_id.is_(None),
                    )
                else:
                    live_active_count = live_active_count.where(
                        TodoModel.project_id == _pid,
                    )
                guard = guard.where(
                    live_active_count.scalar_subquery() < max_active,
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
            if len(claimed) >= claim_limit:
                break
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
        cap = min(limit, current_list_limit())
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
        # Compatibility marker for the historical facade guard:
        # min(limit, _DEFAULT_LIST_LIMIT)
        stmt = stmt.order_by(TaskReturnModel.created_at.asc()).limit(min(limit, current_list_limit()))
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
