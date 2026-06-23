"""Admission gate for self-improvement todos.

Caps how many self-improve todos may be open at once (runaway guard) and decides
each admitted todo's initial status — defaulting to ``QUEUED`` so generated
work is claimable by the event loop. Set ``auto_queue=False`` (or
``self_improve.auto_queue: false`` in config) to park self-generated work in
``APPROVAL_REQUIRED`` for a human gate instead.
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
    def __init__(self, max_open: int = 10, auto_queue: bool = True, allow_auto_promote: bool = False) -> None:
        self.max_open = max_open
        self.auto_queue = auto_queue
        self.allow_auto_promote = allow_auto_promote

    def evaluate(self, todo: dict[str, Any], open_count: int) -> GateDecision:
        if open_count >= self.max_open:
            return GateDecision(admitted=False, initial_status="")
        initial = (
            TodoStatus.QUEUED.value
            if self.auto_queue
            else TodoStatus.APPROVAL_REQUIRED.value
        )
        if initial == TodoStatus.APPROVAL_REQUIRED.value and self.allow_auto_promote:
            initial = TodoStatus.QUEUED.value
        return GateDecision(admitted=True, initial_status=initial)
