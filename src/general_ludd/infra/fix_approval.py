"""Accept / reject approval manager for proposed deployment-config fixes (#76).

SLICE C core. An in-process state machine that parks a proposed config-patch in
``pending`` until a human accepts or rejects it. Mirrors
``self_improve/approval.py``'s ``SelfImproveApprovalManager``:

* ``propose`` mints a ``pending`` proposal.
* ``approve`` flips ``pending -> approved`` and exposes the merged config
  (``{**deployment, **patch}``) to apply.
* ``reject`` flips ``pending -> rejected`` and records a reason.
* Approving/rejecting a non-``pending`` proposal raises :class:`FixApprovalError`
  (mirror of ``_require_pending``), so an already-decided fix can never be
  re-decided.

Storage is a plain in-memory dict — the router/persistence wiring is deferred to
a later slice.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

FixStatus = Literal["pending", "approved", "rejected"]


class FixApprovalError(Exception):
    """Raised when an approve/reject is attempted from an invalid state."""


class FixProposal(BaseModel):
    """A proposed config-patch awaiting a human accept/reject decision."""

    fix_id: str
    deployment: dict[str, Any]
    patch: dict[str, Any]
    status: FixStatus = Field(default="pending")
    reason: str = Field(default="")

    def merged_config(self) -> dict[str, Any]:
        """The config to apply if approved: the deployment overlaid with the patch."""
        return {**self.deployment, **self.patch}


class FixApprovalManager:
    """In-memory accept/reject state machine for proposed config fixes.

    A proposal is *eligible* for a decision only while ``pending``. Approving or
    rejecting any other status is a :class:`FixApprovalError` (callers must not be
    able to re-decide an already approved/rejected fix).
    """

    def __init__(self) -> None:
        self._store: dict[str, FixProposal] = {}

    def propose(
        self, deployment: dict[str, Any], patch: dict[str, Any]
    ) -> FixProposal:
        """Register a new ``pending`` proposal and return it."""
        proposal = FixProposal(
            fix_id=uuid4().hex,
            deployment=dict(deployment),
            patch=dict(patch),
        )
        self._store[proposal.fix_id] = proposal
        return proposal

    def get(self, fix_id: str) -> FixProposal:
        """Return the proposal for ``fix_id``.

        Raises:
            FixApprovalError: if no such proposal exists.
        """
        proposal = self._store.get(fix_id)
        if proposal is None:
            raise FixApprovalError(f"No fix proposal with id {fix_id!r}")
        return proposal

    def approve(self, fix_id: str) -> FixProposal:
        """Accept a pending proposal: ``pending -> approved``.

        The returned proposal's :meth:`FixProposal.merged_config` is the config to
        apply (``{**deployment, **patch}``).

        Raises:
            FixApprovalError: if the proposal is missing or not ``pending``.
        """
        proposal = self.get(fix_id)
        self._require_pending(proposal, action="approve")
        proposal.status = "approved"
        return proposal

    def reject(self, fix_id: str, reason: str = "") -> FixProposal:
        """Reject a pending proposal: ``pending -> rejected``.

        ``reason`` is recorded on the proposal for the audit trail.

        Raises:
            FixApprovalError: if the proposal is missing or not ``pending``.
        """
        proposal = self.get(fix_id)
        self._require_pending(proposal, action="reject")
        proposal.status = "rejected"
        if reason:
            proposal.reason = reason
        return proposal

    def _require_pending(self, proposal: FixProposal, *, action: str) -> None:
        if proposal.status != "pending":
            raise FixApprovalError(
                f"Cannot {action} {proposal.fix_id}: not pending "
                f"(status={proposal.status})"
            )


__all__ = [
    "FixApprovalError",
    "FixApprovalManager",
    "FixProposal",
    "FixStatus",
]
