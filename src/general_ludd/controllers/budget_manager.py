"""Per-todo cost ceilings and daily budget with kill switch.

Extends RunBudgetGuard with per-todo limits and daily cap enforcement.
When breached, sets todo to BLOCKED and healthz reports budget_exhausted.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


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
        current = self._todo_spend.get(todo_id, 0.0)
        if current + estimated_cost > self._per_todo_limit:
            return {
                "allowed": False,
                "reason": (
                    f"Per-todo budget exceeded: "
                    f"${current + estimated_cost:.4f} > "
                    f"${self._per_todo_limit:.4f}"
                ),
            }
        return {"allowed": True, "reason": "ok"}

    def check_daily_budget(self, estimated_cost: float) -> dict[str, Any]:
        if self._paused:
            return {"allowed": False, "reason": "budget_exhausted"}
        self._reset_daily_if_needed()
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
        pct = (
            (self._daily_spend / self._daily_limit) * 100
            if self._daily_limit > 0 else 0
        )
        if pct >= self._alert_pct:
            logger.warning("Budget at %.1f%% of daily limit", pct)
        return {"allowed": True, "reason": "ok"}

    def record_spend(self, todo_id: str, amount: float) -> None:
        self._todo_spend[todo_id] = self._todo_spend.get(todo_id, 0.0) + amount
        self._daily_spend += amount

    def get_status(self) -> dict[str, Any]:
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
        elapsed = time.monotonic() - self._daily_start
        if elapsed > 86400:
            self._daily_spend = 0.0
            self._daily_start = time.monotonic()
            self._paused = False
