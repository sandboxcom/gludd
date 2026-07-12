"""Admission gate for self-improvement todos.

Caps how many self-improve todos may be open at once (runaway guard) and
always parks admitted todos in ``APPROVAL_REQUIRED`` so self-authored
code/test work is parked behind a human gate rather than silently executing
without review (a self-modification approval bypass otherwise). A held todo
is released by ``SelfImproveApprovalManager`` — wired to the
``gludd self-improve approve/reject`` CLI subcommands and the daemon
``/self-improve/approvals`` routes.

C13 (self-improve gate bypasses): ``auto_queue`` was removed — it was a
config-driven backdoor (``self_improve.auto_queue: true``) that let admins
bypass the human-approval gate. ``allow_auto_promote`` was also removed —
it was a caller-side backdoor. The ``SelfImproveApprovalManager`` wired
human-approval path is the ONLY way to release a held todo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from general_ludd.schemas.todo import TodoStatus


@dataclass(frozen=True)
class GateDecision:
    admitted: bool
    initial_status: str


class SelfImproveGate:
    def __init__(self, max_open: int = 10) -> None:
        self.max_open = max_open

    def evaluate(self, todo: dict[str, Any], open_count: int) -> GateDecision:
        if open_count >= self.max_open:
            return GateDecision(admitted=False, initial_status="")
        return GateDecision(
            admitted=True, initial_status=TodoStatus.APPROVAL_REQUIRED.value
        )
