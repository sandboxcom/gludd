"""Per-todo cost ceilings and daily budget with kill switch.

Extends RunBudgetGuard with per-todo limits and daily cap enforcement.
When breached, sets todo to BLOCKED and healthz reports budget_exhausted.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def _is_uncomputable(cost: float) -> bool:
    """True when an estimated cost cannot be reasoned about against a cap.

    NaN (and any non-numeric value coerced to it) makes every ``>`` comparison
    return False, which would silently fail the kill switch OPEN. We treat such
    an uncomputable cost as a hard signal to fail CLOSED. ``+inf`` is finite
    enough to compare normally (it exceeds any finite cap), so it is NOT flagged
    here and flows through the ordinary comparison.
    """
    try:
        return math.isnan(float(cost))
    except (TypeError, ValueError):
        # A value that will not even coerce to float is, by definition,
        # uncomputable -> fail closed.
        return True


class BudgetManager:
    def __init__(
        self,
        daily_limit_usd: float = float("inf"),
        monthly_limit_usd: float = float("inf"),
        per_todo_limit_usd: float = float("inf"),
        alert_threshold_pct: float = 80.0,
    ) -> None:
        self._daily_limit = daily_limit_usd
        self._monthly_limit = monthly_limit_usd
        self._per_todo_limit = per_todo_limit_usd
        self._alert_pct = alert_threshold_pct
        self._todo_spend: dict[str, float] = {}
        self._daily_spend: float = 0.0
        self._daily_start: float = time.monotonic()
        self._paused: bool = False
        self._spend_lock = threading.Lock()
        # Tracks the projected cost that was atomically reserved by the last
        # check_todo_budget + check_daily_budget call pair for each todo_id.
        # record_spend() uses this to reconcile the ledger to the ACTUAL cost
        # (delta = actual - reserved) rather than adding on top of the reservation.
        # Keys are cleared after reconciliation so a missing key means no
        # outstanding reservation and record_spend() falls back to a plain add.
        self._todo_reservations: dict[str, float] = {}
        self._daily_reservations: dict[str, float] = {}
        # API cost estimation + local-resource gating are delegated to the
        # threshold-based BudgetController so this manager has a single source
        # for both monetary and compute-pressure budget decisions.
        from general_ludd.controllers.pid import BudgetController

        self._controller = BudgetController(
            default_run_budget_usd=(
                daily_limit_usd if daily_limit_usd != float("inf") else 200.0
            )
        )

    def estimate_call_cost(self, tokens: int, cost_per_1k: float) -> float:
        """Estimate the USD cost of a model call (via BudgetController)."""
        return self._controller.estimate_call_cost(tokens, cost_per_1k)

    def check_local_model_resources(self, snapshot: Any) -> dict[str, bool | str]:
        """Gate a local-model run on CPU/memory/disk/load pressure."""
        return self._controller.check_local_model_resources(snapshot)

    def check_todo_budget(self, todo_id: str, estimated_cost: float) -> dict[str, Any]:
        with self._spend_lock:
            current = self._todo_spend.get(todo_id, 0.0)
            if self._per_todo_limit != float("inf") and _is_uncomputable(estimated_cost):
                # Fail CLOSED: an uncomputable cost under a real cap must block.
                return {
                    "allowed": False,
                    "reason": (
                        "Per-todo budget check failed closed: "
                        "uncomputable (NaN/unknown) estimated cost"
                    ),
                }
            # Account for a prior reservation that has not yet been reconciled so
            # a repeated check on the same todo_id (e.g. a retry) does not
            # double-count its own outstanding hold against the cap. The
            # reservation replaces (not stacks), mirroring check_daily_budget_reserved.
            prior_todo_reservation = self._todo_reservations.get(todo_id, 0.0)
            prospective = current - prior_todo_reservation + estimated_cost
            if prospective > self._per_todo_limit:
                return {
                    "allowed": False,
                    "reason": (
                        f"Per-todo budget exceeded: "
                        f"${prospective:.4f} > "
                        f"${self._per_todo_limit:.4f}"
                    ),
                }
            # Atomically reserve the projected cost so no concurrent caller can race
            # past the cap.  The reservation is later reconciled to the actual cost
            # by record_spend(); until then it acts as a hold on the budget.
            self._todo_spend[todo_id] = prospective
            self._todo_reservations[todo_id] = estimated_cost
            return {"allowed": True, "reason": "ok", "charged": True}

    def check_daily_budget(self, estimated_cost: float) -> dict[str, Any]:
        with self._spend_lock:
            # Reset BEFORE the paused check: a sticky pause from a prior day's breach
            # must not survive the daily-window rollover (else the kill-switch never
            # recovers and every subsequent day is blocked).
            self._reset_daily_if_needed()
            if self._paused:
                return {"allowed": False, "reason": "budget_exhausted"}
            if self._daily_limit != float("inf") and _is_uncomputable(estimated_cost):
                # Fail CLOSED: an uncomputable cost under a real cap trips the
                # sticky kill switch rather than silently allowing the call.
                self._paused = True
                return {
                    "allowed": False,
                    "reason": (
                        "Daily budget check failed closed: "
                        "uncomputable (NaN/unknown) estimated cost"
                    ),
                }
            if self._daily_spend + estimated_cost > self._daily_limit:
                self._paused = True
                return {
                    "allowed": False,
                    "reason": (
                        f"Daily budget exceeded: "
                        f"${self._daily_spend + estimated_cost:.4f} > "
                        f"${self._daily_limit:.4f}"
                    ),
                }
            # Atomically record the charge so no concurrent caller can race past the cap.
            self._daily_spend += estimated_cost
            pct = (
                (self._daily_spend / self._daily_limit) * 100
                if self._daily_limit > 0 else 0
            )
            if pct >= self._alert_pct:
                logger.warning("Budget at %.1f%% of daily limit", pct)
            return {"allowed": True, "reason": "ok", "charged": True}

    def check_daily_budget_reserved(self, todo_id: str, estimated_cost: float) -> dict[str, Any]:
        """Like check_daily_budget but records a daily reservation keyed by todo_id.

        Use this instead of check_daily_budget when you will later call
        record_spend(todo_id, actual) to reconcile the ledger to the actual cost.
        The reservation is replaced (not stacked) if called again for the same
        todo_id before reconciliation, so retries stay correct.
        """
        with self._spend_lock:
            self._reset_daily_if_needed()
            if self._paused:
                return {"allowed": False, "reason": "budget_exhausted"}
            if self._daily_limit != float("inf") and _is_uncomputable(estimated_cost):
                self._paused = True
                return {
                    "allowed": False,
                    "reason": (
                        "Daily budget check failed closed: "
                        "uncomputable (NaN/unknown) estimated cost"
                    ),
                }
            # Account for a prior reservation that has not yet been reconciled so
            # we don't double-hold budget for retries on the same todo_id.
            prior_daily_reservation = self._daily_reservations.get(todo_id, 0.0)
            prospective = self._daily_spend - prior_daily_reservation + estimated_cost
            if prospective > self._daily_limit:
                self._paused = True
                return {
                    "allowed": False,
                    "reason": (
                        f"Daily budget exceeded: "
                        f"${prospective:.4f} > "
                        f"${self._daily_limit:.4f}"
                    ),
                }
            self._daily_spend = self._daily_spend - prior_daily_reservation + estimated_cost
            self._daily_reservations[todo_id] = estimated_cost
            pct = (
                (self._daily_spend / self._daily_limit) * 100
                if self._daily_limit > 0 else 0
            )
            if pct >= self._alert_pct:
                logger.warning("Budget at %.1f%% of daily limit", pct)
            return {"allowed": True, "reason": "ok", "charged": True}

    def record_spend(self, todo_id: str, amount: float) -> None:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(
                f"BudgetManager.record_spend(): amount must be a finite "
                f"non-negative value, got {amount!r}."
            )
        # Serialize the read-modify-write so concurrent spend records can't lose
        # updates (the controller is shared across worker threads).
        with self._spend_lock:
            # Reconcile against any outstanding reservations so the net recorded
            # spend equals `amount` exactly (not reservation + amount).
            # If no reservation exists the call was not preceded by a check_*_reserved
            # call, so fall back to a plain add (backward-compatible path).
            todo_reserved = self._todo_reservations.pop(todo_id, None)
            daily_reserved = self._daily_reservations.pop(todo_id, None)

            if todo_reserved is not None:
                # Replace the reservation with the actual: delta adjusts the ledger.
                todo_delta = amount - todo_reserved
                self._todo_spend[todo_id] = max(
                    0.0, self._todo_spend.get(todo_id, 0.0) + todo_delta
                )
            else:
                self._todo_spend[todo_id] = self._todo_spend.get(todo_id, 0.0) + amount

            if daily_reserved is not None:
                daily_delta = amount - daily_reserved
                self._daily_spend = max(0.0, self._daily_spend + daily_delta)
            else:
                self._daily_spend += amount

    def release_reservation(self, todo_id: str) -> None:
        """Cancel any outstanding reservation for todo_id without recording spend.

        Use on the abort path (deferral / failed call) so a reserved-but-never-spent
        projected cost does not stay held against the budget. Reverses the reservation
        held in _todo_spend / _daily_spend and clears the reservation keys. Idempotent.
        """
        with self._spend_lock:
            todo_reserved = self._todo_reservations.pop(todo_id, None)
            if todo_reserved is not None:
                self._todo_spend[todo_id] = max(
                    0.0, self._todo_spend.get(todo_id, 0.0) - todo_reserved
                )
            daily_reserved = self._daily_reservations.pop(todo_id, None)
            if daily_reserved is not None:
                self._daily_spend = max(0.0, self._daily_spend - daily_reserved)

    def get_status(self) -> dict[str, Any]:
        with self._spend_lock:
            self._reset_daily_if_needed()
            pct = (
                (self._daily_spend / self._daily_limit) * 100
                if self._daily_limit > 0 else 0
            )
            return {
                "daily_spend": self._daily_spend,
                "daily_limit": self._daily_limit,
                "daily_pct": round(pct, 1),
                "paused": self._paused,
                "per_todo_limit": self._per_todo_limit,
            }

    def _reset_daily_if_needed(self) -> None:
        # MUST be called while _spend_lock is already held.
        elapsed = time.monotonic() - self._daily_start
        if elapsed > 86400:
            self._daily_spend = 0.0
            self._daily_start = time.monotonic()
            self._paused = False
