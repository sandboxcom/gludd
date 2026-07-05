"""Terraform watchdog: monitor stacks for budget overruns."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WatchdogFinding:
    stack_name: str
    exceeded_budget: bool
    current_cost: float
    budget_limit: float


class TerraformWatchdog:
    """Monitor Terraform stacks for cost overruns.

    Inspects terraform state files and plan outputs to detect when estimated
    infrastructure cost exceeds configured budgets.
    """

    def __init__(self, stacks_dir: str) -> None:
        self._stacks_dir = Path(stacks_dir)

    def is_applied(self, stack_name: str) -> bool:
        stack_dir = self._stacks_dir / stack_name
        tfstate = stack_dir / "terraform.tfstate"
        return tfstate.exists()

    def current_cost_estimate(self, stack_name: str) -> float:
        stack_dir = self._stacks_dir / stack_name
        tfstate = stack_dir / "terraform.tfstate"
        if not tfstate.exists():
            return 0.0
        try:
            data = json.loads(tfstate.read_text())
        except (json.JSONDecodeError, OSError):
            return 0.0
        resources = data.get("resources", [])
        cost = 0.0
        for resource in resources:
            instances = resource.get("instances", [])
            for instance in instances:
                attrs = instance.get("attributes", {})
                cost_str = attrs.get("monthly_cost_estimate") or attrs.get("cost_estimate")
                if cost_str is not None:
                    try:
                        cost += float(cost_str)
                    except (ValueError, TypeError):
                        cost += self._estimate_from_resource_type(
                            resource.get("type", ""), attrs
                        )
                else:
                    cost += self._estimate_from_resource_type(
                        resource.get("type", ""), attrs
                    )
        return cost

    def check_all_stacks(self) -> list[WatchdogFinding]:
        findings: list[WatchdogFinding] = []
        if not self._stacks_dir.is_dir():
            return findings
        for stack_dir in sorted(self._stacks_dir.iterdir()):
            if not stack_dir.is_dir():
                continue
            stack_name = stack_dir.name
            if not self.is_applied(stack_name):
                continue
            current = self.current_cost_estimate(stack_name)
            budget = self._read_budget(stack_name)
            exceeded = current > budget and budget > 0
            findings.append(
                WatchdogFinding(
                    stack_name=stack_name,
                    exceeded_budget=exceeded,
                    current_cost=current,
                    budget_limit=budget,
                )
            )
        return findings

    def _read_budget(self, stack_name: str) -> float:
        budget_file = self._stacks_dir / stack_name / "budget.json"
        if not budget_file.exists():
            return 0.0
        try:
            data = json.loads(budget_file.read_text())
            return float(data.get("monthly_limit_usd", 0.0))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return 0.0

    @staticmethod
    def _estimate_from_resource_type(resource_type: str, attrs: dict[str, Any]) -> float:
        _ = attrs
        estimates = {
            "aws_instance": 50.0,
            "google_compute_instance": 60.0,
            "azurerm_linux_virtual_machine": 55.0,
            "aws_eks_cluster": 100.0,
            "google_container_cluster": 110.0,
        }
        return estimates.get(resource_type, 10.0)
