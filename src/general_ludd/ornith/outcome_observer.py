"""Outcome observer for Ornith training pairs.

The outcome of an Ornith-produced scaffold is NOT known at ``solve()``
time -- it is resolved later when:

* the patch is applied to the repo -> status ``applied``
* the patch runs through ``make gate`` -> ``succeeded`` (green) or
  ``rejected_by_gate`` (red)
* a code reviewer rejects it -> ``rejected_by_review``
* the patch is later reverted (git revert / commit rollback) ->
  ``reverted``

This module provides :class:`OutcomeObserver`, which subscribes to those
events and runs as a background task polling pending outcomes every N
minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from general_ludd.ornith.training_repo import OrnithTrainingRepo

logger = logging.getLogger(__name__)

#: A gate-completion subscriber: called with (pair_id, gate_passed: bool).
GateListener = Callable[[str, bool], Awaitable[None]]
#: A review-decision subscriber: called with (pair_id, approved: bool, reason).
ReviewListener = Callable[[str, bool, str], Awaitable[None]]
#: A git-revert subscriber: called with (pair_id, reason).
RevertListener = Callable[[str, str], Awaitable[None]]


class OutcomeObserver:
    """Resolves the outcome half of Ornith training pairs.

    The observer exposes three event hooks -- :meth:`on_gate_complete`,
    :meth:`on_review_decision`, :meth:`on_commit_revert` -- that daemon
    subsystems (the gate runner, the review router, the git auditor)
    invoke when they know what happened to a scaffold. It ALSO runs a
    background poll loop that re-scans pending pairs so outcomes set
    out-of-band (e.g. by a CI webhook writing to the DB) eventually get
    picked up.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[Any],
        poll_interval_seconds: int = 300,
        pending_older_than_minutes: int = 0,
    ) -> None:
        self._factory = session_factory
        self._poll_interval = max(10, poll_interval_seconds)
        self._pending_older_than_minutes = pending_older_than_minutes
        self._gate_listeners: list[GateListener] = []
        self._review_listeners: list[ReviewListener] = []
        self._revert_listeners: list[RevertListener] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # -- subscription hooks -------------------------------------------------

    def subscribe_gate(self, fn: GateListener) -> None:
        self._gate_listeners.append(fn)

    def subscribe_review(self, fn: ReviewListener) -> None:
        self._review_listeners.append(fn)

    def subscribe_revert(self, fn: RevertListener) -> None:
        self._revert_listeners.append(fn)

    # -- event entrypoints (called by the rest of the daemon) --------------

    async def on_gate_complete(self, pair_id: str, gate_passed: bool) -> None:
        """Apply succeeded-OR-rejected_by_gate from a gate result."""
        status = "succeeded" if gate_passed else "rejected_by_gate"
        details: dict[str, Any] = {
            "gate_passed": gate_passed,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        await self._apply_outcome(pair_id, status, details)
        for fn in list(self._gate_listeners):
            try:
                await fn(pair_id, gate_passed)
            except Exception as exc:
                logger.warning("gate listener error: %s", exc)

    async def on_review_decision(
        self, pair_id: str, approved: bool, reason: str = ""
    ) -> None:
        """A human/agent reviewer rejected (or approved) the scaffold."""
        if approved:
            # Approval alone does not resolve the outcome -- the gate still
            # has to pass. Just record the review signal in details.
            return
        details = {
            "review_approved": False,
            "review_reason": reason,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        await self._apply_outcome(pair_id, "rejected_by_review", details)
        for fn in list(self._review_listeners):
            try:
                await fn(pair_id, approved, reason)
            except Exception as exc:
                logger.warning("review listener error: %s", exc)

    async def on_commit_revert(self, pair_id: str, reason: str = "") -> None:
        """A commit attributed to this Ornith pair was reverted."""
        details = {
            "reverted_because": reason,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        await self._apply_outcome(pair_id, "reverted", details)
        for fn in list(self._revert_listeners):
            try:
                await fn(pair_id, reason)
            except Exception as exc:
                logger.warning("revert listener error: %s", exc)

    async def mark_applied(self, pair_id: str) -> None:
        """The scaffold's patch was applied to the repo (but not yet gated)."""
        details = {"applied_at": datetime.now(UTC).isoformat()}
        await self._apply_outcome(pair_id, "applied", details)

    # -- internals ----------------------------------------------------------

    async def _apply_outcome(
        self, pair_id: str, status: str, details: dict[str, Any]
    ) -> None:
        async with self._factory() as session:
            repo = OrnithTrainingRepo(session)
            try:
                await repo.set_outcome(pair_id, status, details)
            except KeyError:
                # Unknown pair id -- nothing to do. Logged at debug.
                logger.debug("set_outcome: unknown pair %s", pair_id)
                return
            await session.commit()

    # -- background poll loop ----------------------------------------------

    async def run_forever(self) -> None:
        """Background loop: poll pending outcomes and let listeners react.

        The poll loop is a safety net. Most outcomes arrive via the explicit
        hooks (``on_gate_complete`` etc.); the loop ensures outcomes written
        out-of-band to the DB are at least observed and surfaced to any
        subscribers that need to fire on every state transition.
        """
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception as exc:
                logger.warning("outcome observer poll error: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)

    async def _poll_once(self) -> None:
        async with self._factory() as session:
            repo = OrnithTrainingRepo(session)
            pending = await repo.get_pending_outcomes(
                limit=100, older_than_minutes=self._pending_older_than_minutes
            )
            # No automatic transition here -- we only surface the count so
            # subscribers / operators know there is unresolved work. The
            # actual outcome is set by the explicit hooks.
            if pending:
                logger.info(
                    "outcome observer: %d pending pair(s) older than %dm",
                    len(pending),
                    self._pending_older_than_minutes,
                )

    def start(self) -> asyncio.Task[None]:
        self._stop.clear()
        self._task = asyncio.create_task(self.run_forever(), name="ornith-outcome-observer")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
