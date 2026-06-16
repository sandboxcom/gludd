"""Human-gating + runaway cap for self-improvement todos (F8).

The self-improvement harness can file work for *itself* every N ticks
(``event_loop/loop.py::_phase_self_improve``).  Without a gate, an agent that
proposes more work than it completes will fill its own backlog indefinitely —
the "runaway loop" F8 exists to prevent.

``SelfImproveGate`` is a pure decision helper (no DB, no daemon) consumed at the
point where generated self-improve todos are about to be persisted.  It answers
two questions for a batch of proposed todos:

1. **What initial status should each proposed todo carry?**
   When ``auto_queue`` is true the todos go straight to ``QUEUED`` (the legacy
   behaviour).  When false (the safe default) they enter ``APPROVAL_REQUIRED``
   so a human must release them via ``SelfImproveApprovalManager``.

2. **How many of the batch may be admitted right now?**
   Given the count of self-improve todos already open (``open_count``) and a cap
   (``max_open``), only the headroom ``max(0, max_open - open_count)`` proposals
   are admitted; the rest are dropped this cycle (they will be re-proposed on a
   later cycle once headroom frees up — see ``self_improve/dedup.py`` for the
   companion that avoids re-filing the *same* gap twice).

The status strings returned are the ``TodoStatus`` *values* (``"queued"`` /
``"approval_required"``) so callers can drop them directly into a repository
payload without importing the enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.schemas.todo import TodoStatus

#: Default ceiling on simultaneously-open self-improve todos.  Matches the F8
#: spec ("cap open self-improve todos (default 10)").
DEFAULT_MAX_OPEN = 10


@dataclass(frozen=True)
class GateDecision:
    """Outcome of admitting a batch of proposed self-improve todos.

    Attributes:
        admitted:   The proposals that fit within the open cap, each annotated
                    with an injected ``"status"`` key (the value to persist).
        rejected:   Proposals dropped this cycle because the cap was reached.
        initial_status: The status applied to every admitted proposal.
        headroom:   How many slots were free when the batch arrived.
    """

    admitted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    initial_status: str = TodoStatus.APPROVAL_REQUIRED.value
    headroom: int = 0


class SelfImproveGate:
    """Gate + cap for self-improvement proposals.

    Args:
        auto_queue: When True, admitted proposals are ``QUEUED`` immediately
                    (no human gate).  When False (default), they are parked in
                    ``APPROVAL_REQUIRED`` until a human approves them.
        max_open:   Maximum number of self-improve todos allowed open at once.
                    Must be >= 0; 0 means "admit nothing" (fully paused).
    """

    def __init__(
        self,
        auto_queue: bool = False,
        max_open: int = DEFAULT_MAX_OPEN,
    ) -> None:
        if max_open < 0:
            raise ValueError("max_open must be non-negative")
        self._auto_queue = auto_queue
        self._max_open = max_open

    @property
    def initial_status(self) -> str:
        """The status value applied to admitted proposals given ``auto_queue``."""
        return (
            TodoStatus.QUEUED.value
            if self._auto_queue
            else TodoStatus.APPROVAL_REQUIRED.value
        )

    def headroom(self, open_count: int) -> int:
        """Free slots remaining before the open cap is hit (never negative)."""
        return max(0, self._max_open - max(0, open_count))

    def admit(
        self,
        proposals: list[dict[str, Any]],
        open_count: int,
    ) -> GateDecision:
        """Decide which proposals to admit and at what status.

        Admission is order-preserving: the first ``headroom`` proposals are
        admitted, the remainder rejected.  Each admitted proposal is returned as
        a *new* dict (the input is not mutated) carrying an added ``"status"``
        key set to :pyattr:`initial_status`.

        Args:
            proposals:  Generated self-improve todo payloads (dicts).
            open_count: Count of self-improve todos already open in the system.

        Returns:
            A :class:`GateDecision`.
        """
        free = self.headroom(open_count)
        status = self.initial_status
        admitted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for proposal in proposals:
            if len(admitted) < free:
                admitted.append({**proposal, "status": status})
            else:
                rejected.append(proposal)
        return GateDecision(
            admitted=admitted,
            rejected=rejected,
            initial_status=status,
            headroom=free,
        )
