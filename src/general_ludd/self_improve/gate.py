"""Admission gate for self-improvement todos.

Caps how many self-improve todos may be open at once (runaway guard) and decides
each admitted todo's initial status. Defaults to ``APPROVAL_REQUIRED`` so
self-authored code/test work is parked behind a human gate rather than silently
executing without review (a self-modification approval bypass otherwise). A held
todo is released by ``SelfImproveApprovalManager`` — wired to the
``gludd self-improve approve/reject`` CLI subcommands and the daemon
``/self-improve/approvals`` routes. Set ``auto_queue=True`` (or
``self_improve.auto_queue: true`` in config) to opt back into immediate
``QUEUED`` admission where self-modification without review is acceptable.

C13 (self-improve gate bypasses): ``allow_auto_promote`` was removed — it was a
backdoor that let callers set ``allow_auto_promote=True`` to promote
APPROVAL_REQUIRED to QUEUED without going through human approval. The gate now
has a single choke-point: ``auto_queue`` (OFF by default). The
``SelfImproveApprovalManager`` wired human-approval path is the ONLY way to
release a held todo.
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
    def __init__(self, max_open: int = 10, auto_queue: bool = False) -> None:
        self.max_open = max_open
        self.auto_queue = auto_queue

    def evaluate(self, todo: dict[str, Any], open_count: int) -> GateDecision:
        if open_count >= self.max_open:
            return GateDecision(admitted=False, initial_status="")
        initial = (
            TodoStatus.QUEUED.value
            if self.auto_queue
            else TodoStatus.APPROVAL_REQUIRED.value
        )
        return GateDecision(admitted=True, initial_status=initial)
