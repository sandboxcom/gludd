from __future__ import annotations

import time


class RunBudgetGuard:
    def __init__(
        self,
        run_budget_usd: float = float("inf"),
        run_timeout_seconds: float = float("inf"),
        per_call_budget_usd: float = float("inf"),
    ) -> None:
        self._run_budget_usd = run_budget_usd
        self._run_timeout_seconds = run_timeout_seconds
        self._per_call_budget_usd = per_call_budget_usd
        self._total_spend: float = 0.0
        self._start_monotonic: float = time.monotonic()

    def record_spend(self, amount_usd: float) -> None:
        self._total_spend += amount_usd

    def get_total_spend(self) -> float:
        return self._total_spend

    def get_elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_monotonic

    def check_run_budget(self) -> dict[str, bool | str | float]:
        total = self._total_spend
        remaining = max(0.0, self._run_budget_usd - total)
        if total > self._run_budget_usd:
            return {
                "allowed": False,
                "reason": f"run budget exceeded: ${total:.4f} > ${self._run_budget_usd:.4f}",
                "total_spend": total,
                "remaining_budget": remaining,
            }
        return {
            "allowed": True,
            "reason": "ok",
            "total_spend": total,
            "remaining_budget": remaining,
        }

    def check_wall_clock(self) -> dict[str, bool | str | float]:
        elapsed = self.get_elapsed_seconds()
        if elapsed > self._run_timeout_seconds:
            return {
                "allowed": False,
                "reason": f"wall-clock timeout exceeded: {elapsed:.2f}s > {self._run_timeout_seconds:.2f}s",
                "elapsed_seconds": elapsed,
            }
        return {
            "allowed": True,
            "reason": "ok",
            "elapsed_seconds": elapsed,
        }

    def check_per_call(self, estimated_cost: float) -> dict[str, bool | str | float]:
        if estimated_cost > self._per_call_budget_usd:
            return {
                "allowed": False,
                "reason": f"per-call budget exceeded: ${estimated_cost:.4f} > ${self._per_call_budget_usd:.4f}",
                "estimated_cost": estimated_cost,
            }
        return {
            "allowed": True,
            "reason": "ok",
            "estimated_cost": estimated_cost,
        }

    def check_all_limits(self, estimated_cost: float = 0.0) -> dict[str, bool | str | float]:
        budget = self.check_run_budget()
        if not budget["allowed"]:
            return budget

        wall = self.check_wall_clock()
        if not wall["allowed"]:
            return wall

        per_call = self.check_per_call(estimated_cost)
        if not per_call["allowed"]:
            return per_call

        return {
            "allowed": True,
            "reason": "ok",
            "total_spend": self._total_spend,
            "elapsed_seconds": self.get_elapsed_seconds(),
            "remaining_budget": max(0.0, self._run_budget_usd - self._total_spend),
        }
