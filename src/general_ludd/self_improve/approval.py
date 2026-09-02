"""Approve / reject transitions for human-gated self-improve todos (F8).

When ``SelfImproveGate`` parks a proposed self-improve todo in
``APPROVAL_REQUIRED`` (the default, no-auto-queue path), a human must release it
before the event loop will ever claim it.  This module is the backend for that
release: an approve flips ``APPROVAL_REQUIRED -> QUEUED``, and a reject flips it
to ``CANCELLED``.  Both are validated against the real todo state machine
(``schemas/todo.py``) so an out-of-state approval is rejected rather than
silently corrupting status.

Two surfaces are provided:

* :meth:`approve` / :meth:`reject` operate on in-process ``Todo`` schema objects
  (no DB) — fully unit-testable; a repository-backed caller persists afterwards.
* :meth:`approve_by_id` / :meth:`reject_by_id` / :meth:`list_pending` are the
  wired release path: they load, guard and persist through a
  :class:`~general_ludd.db.repository.TodoRepository`. These back the
  ``/admin/self-improve/approvals`` daemon routes and the
  ``gludd self-improve approve/reject`` CLI, so an admitted self-improve todo can
  never strand in ``APPROVAL_REQUIRED``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from general_ludd.schemas.todo import Todo, TodoStatus, validate_transition
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    ManagedSelfImproveArtifactKind,
    classify_self_improve_artifact,
    validate_bound_managed_plan,
)

#: Work type stamped on self-improve todos (see event_loop/loop.py). The wired
#: release path refuses to approve/reject anything else so this human gate can
#: only release self-authored self-improve work, not arbitrary todos.
SELF_IMPROVE_WORK_TYPE = "self_improve"


class _TodoStore(Protocol):
    """Structural type for the subset of ``TodoRepository`` the release path uses."""

    async def get_by_id(self, todo_id: str, project_id: str | None = ...) -> Any: ...

    async def transition(
        self,
        todo_id: str,
        new_status: TodoStatus,
        expected_version: int,
        project_id: str | None = ...,
    ) -> Any: ...

    async def update(
        self,
        todo_id: str,
        updates: dict[str, Any],
        expected_version: int,
        project_id: str | None = ...,
    ) -> Any: ...

    async def list_by_status(
        self,
        status: TodoStatus,
        project_id: str | None = ...,
        limit: int | None = ...,
    ) -> list[Any]: ...


class ApprovalError(Exception):
    """Raised when an approve/reject is attempted from an invalid state."""


class SelfImproveApprovalManager:
    """Release or cancel human-gated self-improve todos.

    A todo is *eligible* for approval only when it is currently in
    ``APPROVAL_REQUIRED``.  Approving any other status is an error (callers must
    not be able to "approve" an already-running or completed todo).
    """

    def __init__(
        self,
        managed_repo_resolver: Callable[[str], Path] | None = None,
    ) -> None:
        """Bind the optional trusted project-to-repository resolver.

        Legacy config/non-config records do not need this resolver. Managed
        periodic requests do: without a trusted canonical root, their stored
        execution plan cannot be safely released into the scheduler.
        """
        self._managed_repo_resolver = managed_repo_resolver

    def is_pending_approval(self, todo: Todo) -> bool:
        """True when ``todo`` is awaiting a human approve/reject decision."""
        return todo.status == TodoStatus.APPROVAL_REQUIRED

    def approve(self, todo: Todo) -> Todo:
        """Release a held self-improve todo into the queue.

        Transitions ``APPROVAL_REQUIRED -> QUEUED`` in place and returns the
        same object.

        Raises:
            ApprovalError: if the todo is not currently ``APPROVAL_REQUIRED`` or
                           the transition is not permitted by the state machine.
        """
        self._require_pending(todo, action="approve")
        if not validate_transition(todo.status, TodoStatus.QUEUED):
            raise ApprovalError(
                f"Cannot approve {todo.todo_id}: "
                f"{todo.status.value} -> queued is not a valid transition"
            )
        self._validate_managed_release(todo)
        todo.transition_to(TodoStatus.QUEUED)
        return todo

    def reject(self, todo: Todo, reason: str = "") -> Todo:
        """Cancel a held self-improve todo (human declined the proposal).

        Transitions ``APPROVAL_REQUIRED -> CANCELLED`` in place.  ``reason`` is
        recorded on ``manual_hold_reason`` for the audit trail.

        Raises:
            ApprovalError: if the todo is not currently ``APPROVAL_REQUIRED``.
        """
        self._require_pending(todo, action="reject")
        if not validate_transition(todo.status, TodoStatus.CANCELLED):
            raise ApprovalError(
                f"Cannot reject {todo.todo_id}: "
                f"{todo.status.value} -> cancelled is not a valid transition"
            )
        todo.transition_to(TodoStatus.CANCELLED)
        if reason:
            todo.manual_hold_reason = reason
        return todo

    def _require_pending(self, todo: Todo, *, action: str) -> None:
        if not self.is_pending_approval(todo):
            raise ApprovalError(
                f"Cannot {action} {todo.todo_id}: not awaiting approval "
                f"(status={todo.status.value})"
            )

    # ------------------------------------------------------------------
    # Repository-backed release path (the WIRED human gate).
    # ------------------------------------------------------------------

    async def list_pending(
        self,
        store: _TodoStore,
        *,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        """Return the self-improve todos awaiting a human approve/reject."""
        rows = await store.list_by_status(
            TodoStatus.APPROVAL_REQUIRED, project_id=project_id, limit=limit
        )
        return [
            row
            for row in rows
            if getattr(row, "work_type", None) == SELF_IMPROVE_WORK_TYPE
        ]

    async def approve_by_id(
        self,
        store: _TodoStore,
        todo_id: str,
        *,
        project_id: str | None = None,
    ) -> Any:
        """Release a persisted held self-improve todo: APPROVAL_REQUIRED -> QUEUED.

        Loads the row, verifies it is a self-improve todo still awaiting approval,
        and persists the transition through the repository (optimistic-version
        guarded). Raises :class:`ApprovalError` if the row is missing, not a
        self-improve todo, or not in ``APPROVAL_REQUIRED``.
        """
        return await self._release(
            store, todo_id, TodoStatus.QUEUED, action="approve", project_id=project_id
        )

    async def reject_by_id(
        self,
        store: _TodoStore,
        todo_id: str,
        *,
        reason: str = "",
        project_id: str | None = None,
    ) -> Any:
        """Cancel a persisted held self-improve todo: APPROVAL_REQUIRED -> CANCELLED.

        ``reason`` (if given) is recorded on ``manual_hold_reason`` for the audit
        trail via a follow-up version-guarded update.
        """
        cancelled = await self._release(
            store, todo_id, TodoStatus.CANCELLED, action="reject", project_id=project_id
        )
        if reason:
            # Return the row AFTER the reason write so the caller sees the
            # accurate version and manual_hold_reason (the follow-up update bumps
            # the version again).
            return await store.update(
                todo_id,
                {"manual_hold_reason": reason[:1000]},
                expected_version=cancelled.version,
                project_id=project_id,
            )
        return cancelled

    async def _release(
        self,
        store: _TodoStore,
        todo_id: str,
        target: TodoStatus,
        *,
        action: str,
        project_id: str | None,
    ) -> Any:
        todo = await store.get_by_id(todo_id, project_id=project_id)
        if todo is None:
            raise ApprovalError(f"Cannot {action} {todo_id}: not found")
        status = getattr(todo, "status", None)
        if status != TodoStatus.APPROVAL_REQUIRED.value:
            raise ApprovalError(
                f"Cannot {action} {todo_id}: not awaiting approval (status={status})"
            )
        work_type = getattr(todo, "work_type", None)
        if work_type != SELF_IMPROVE_WORK_TYPE:
            raise ApprovalError(
                f"Cannot {action} {todo_id}: not a self-improve todo "
                f"(work_type={work_type})"
            )
        if target == TodoStatus.QUEUED:
            self._validate_managed_release(todo)
        return await store.transition(
            todo_id, target, expected_version=todo.version, project_id=project_id
        )

    def _validate_managed_release(self, todo: Any) -> None:
        """Require a canonical, row-bound plan for managed periodic work."""
        policy = getattr(todo, "approval_policy", None)
        if policy != MANAGED_SELF_IMPROVE_APPROVAL_POLICY:
            # Existing config and non-config flows use their own immutable
            # artifacts and two-step apply endpoints. Preserve that behavior
            # until those flows are migrated to a separate status/type.
            return
        raw = getattr(todo, "plan_artifact", None)
        artifact_kind = classify_self_improve_artifact(raw, policy)
        if artifact_kind is ManagedSelfImproveArtifactKind.MANAGED_PLAN_REQUEST:
            raise ApprovalError(
                "Cannot approve managed self-improve todo: plan must be prepared "
                "from explicit immutable baseline/reference commits first"
            )
        if artifact_kind is not ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN:
            raise ApprovalError(
                "Cannot approve managed self-improve todo: managed plan artifact "
                "is missing or malformed"
            )
        todo_id = getattr(todo, "todo_id", None)
        project_id = getattr(todo, "project_id", None)
        if not isinstance(todo_id, str) or not isinstance(project_id, str):
            raise ApprovalError(
                "Cannot approve managed self-improve todo: row identity is malformed"
            )
        if self._managed_repo_resolver is None:
            raise ApprovalError(
                "Cannot approve managed self-improve todo: canonical repository "
                "resolver is unavailable"
            )
        try:
            repo_root = self._managed_repo_resolver(project_id)
            validate_bound_managed_plan(
                raw,
                todo_id=todo_id,
                project_id=project_id,
                repo_root=repo_root,
            )
        except ApprovalError:
            raise
        except Exception as exc:
            raise ApprovalError(
                "Cannot approve managed self-improve todo: stored plan is not "
                "bound to its todo, project, and canonical repository"
            ) from exc
