"""Approve / reject transitions for human-gated self-improve todos (F8).

When ``SelfImproveGate`` parks a proposed self-improve todo in
``APPROVAL_REQUIRED`` (the default, no-auto-queue path), a human must release it
before the event loop will ever claim it.  This module is the backend for that
release: ``gludd approve <todo_id>`` flips ``APPROVAL_REQUIRED -> QUEUED``, and a
reject flips it to ``CANCELLED``.  Both are validated against the real todo state
machine (``schemas/todo.py``) so an out-of-state approval is rejected rather than
silently corrupting status.

The manager works on ``Todo`` schema objects (in-process) so it is fully unit
-testable without a DB.  A repository-backed caller persists the mutated object
afterwards.
"""

from __future__ import annotations

from general_ludd.schemas.todo import Todo, TodoStatus, validate_transition


class ApprovalError(Exception):
    """Raised when an approve/reject is attempted from an invalid state."""


class SelfImproveApprovalManager:
    """Release or cancel human-gated self-improve todos.

    A todo is *eligible* for approval only when it is currently in
    ``APPROVAL_REQUIRED``.  Approving any other status is an error (callers must
    not be able to "approve" an already-running or completed todo).
    """

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
