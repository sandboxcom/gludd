"""Fine-grained budget envelopes — per-agent, per-task, per-tool.

AG.10: Each envelope tracks its own limit/spent/remaining. The BudgetManager
aggregates multiple envelopes and gates operations against their respective caps.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field


class BudgetEnvelope:
    """A named budget envelope with a limit and tracked spend."""

    def __init__(self, name: str, limit: float = float("inf")) -> None:
        if not math.isfinite(limit) and limit != float("inf"):
            raise ValueError(
                f"BudgetEnvelope limit must be finite or inf, got {limit!r}"
            )
        if limit < 0:
            raise ValueError(
                f"BudgetEnvelope limit must be non-negative, got {limit!r}"
            )
        self.name = name
        self.limit = limit
        self._spent: float = 0.0
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self._spent)

    @property
    def is_exhausted(self) -> bool:
        return self._spent >= self.limit

    def try_spend(self, amount: float) -> dict[str, object]:
        """Atomically check and deduct. Returns allowed/denied dict.

        Fails CLOSED: non-finite or NaN amount is denied and NOT deducted.
        Only finite, non-negative amounts move the meter.
        """
        if not math.isfinite(amount) or amount < 0:
            return {
                "allowed": False,
                "reason": (
                    f"Envelope '{self.name}': non-finite or negative "
                    f"amount ({amount!r}) denied — fail closed"
                ),
                "envelope": self.name,
                "remaining": self.remaining,
            }
        with self._lock:
            if self._spent + amount > self.limit:
                return {
                    "allowed": False,
                    "reason": (
                        f"Envelope '{self.name}' budget exceeded: "
                        f"+${amount:.4f} would bring total to "
                        f"${self._spent + amount:.4f} > ${self.limit:.4f}"
                    ),
                    "envelope": self.name,
                    "remaining": self.remaining,
                    "spent": self._spent,
                    "limit": self.limit,
                }
            self._spent += amount
            return {
                "allowed": True,
                "reason": "ok",
                "envelope": self.name,
                "remaining": self.remaining,
                "spent": self._spent,
                "limit": self.limit,
            }

    def record_spend(self, amount: float) -> None:
        """Record spend without gating (for external cost ingestion)."""
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(
                f"BudgetEnvelope.record_spend(): amount must be finite "
                f"non-negative, got {amount!r}"
            )
        with self._lock:
            self._spent += amount

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "name": self.name,
                "limit": self.limit,
                "spent": self._spent,
                "remaining": self.remaining,
                "exhausted": self.is_exhausted,
            }

    def reset(self) -> None:
        with self._lock:
            self._spent = 0.0


class PerAgentEnvelope:
    """Budget per agent type (model name). Internal dict of BudgetEnvelope.

    Each agent model gets its own envelope; the manager gates model-selection
    and per-call charges against the model-specific cap.
    """

    def __init__(self) -> None:
        self._envelopes: dict[str, BudgetEnvelope] = {}
        self._lock = threading.Lock()

    def set_limit(self, agent_type: str, limit: float) -> None:
        with self._lock:
            envelope = self._envelopes.get(agent_type)
            if envelope is not None:
                envelope.limit = limit
            else:
                self._envelopes[agent_type] = BudgetEnvelope(
                    name=f"agent:{agent_type}", limit=limit
                )

    def try_spend(self, agent_type: str, amount: float) -> dict[str, object]:
        with self._lock:
            envelope = self._envelopes.get(agent_type)
            if envelope is None:
                return {
                    "allowed": True,
                    "reason": "ok (no limit configured for agent)",
                    "envelope": f"agent:{agent_type}",
                    "remaining": float("inf"),
                }
        return envelope.try_spend(amount)

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                name: env.get_status()
                for name, env in self._envelopes.items()
            }

    def total_spent(self) -> float:
        with self._lock:
            return sum(e.spent for e in self._envelopes.values())

    def reset_all(self) -> None:
        with self._lock:
            for env in self._envelopes.values():
                env.reset()


class PerTaskEnvelope:
    """Budget per task ID. Individual task cost ceiling.

    Each dispatched task gets its own cap; the manager blocks
    further model calls when a task exhausts its allowance.
    """

    def __init__(self, default_limit: float = float("inf")) -> None:
        self._default_limit = default_limit
        self._envelopes: dict[str, BudgetEnvelope] = {}
        self._lock = threading.Lock()

    def set_limit(self, task_id: str, limit: float) -> None:
        with self._lock:
            envelope = self._envelopes.get(task_id)
            if envelope is not None:
                envelope.limit = limit
            else:
                self._envelopes[task_id] = BudgetEnvelope(
                    name=f"task:{task_id}", limit=limit
                )

    def try_spend(self, task_id: str, amount: float) -> dict[str, object]:
        with self._lock:
            envelope = self._envelopes.get(task_id)
            if envelope is None and self._default_limit != float("inf"):
                envelope = BudgetEnvelope(
                    name=f"task:{task_id}", limit=self._default_limit
                )
                self._envelopes[task_id] = envelope
            if envelope is None:
                return {
                    "allowed": True,
                    "reason": "ok (no limit configured for task)",
                    "envelope": f"task:{task_id}",
                    "remaining": float("inf"),
                }
        return envelope.try_spend(amount)

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                tid: env.get_status()
                for tid, env in self._envelopes.items()
            }

    def total_spent(self) -> float:
        with self._lock:
            return sum(e.spent for e in self._envelopes.values())

    def reset_all(self) -> None:
        with self._lock:
            self._envelopes.clear()


class PerToolEnvelope:
    """Budget per tool type. A tool-call cost cap independent of agent/task.

    If a tool (e.g. 'bash', 'task', 'write') exhausts its budget, that
    specific tool is blocked but others remain available.
    """

    def __init__(self) -> None:
        self._envelopes: dict[str, BudgetEnvelope] = {}
        self._lock = threading.Lock()

    def set_limit(self, tool_type: str, limit: float) -> None:
        with self._lock:
            envelope = self._envelopes.get(tool_type)
            if envelope is not None:
                envelope.limit = limit
            else:
                self._envelopes[tool_type] = BudgetEnvelope(
                    name=f"tool:{tool_type}", limit=limit
                )

    def try_spend(self, tool_type: str, amount: float) -> dict[str, object]:
        with self._lock:
            envelope = self._envelopes.get(tool_type)
            if envelope is None:
                return {
                    "allowed": True,
                    "reason": "ok (no limit configured for tool)",
                    "envelope": f"tool:{tool_type}",
                    "remaining": float("inf"),
                }
        return envelope.try_spend(amount)

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                name: env.get_status()
                for name, env in self._envelopes.items()
            }

    def total_spent(self) -> float:
        with self._lock:
            return sum(e.spent for e in self._envelopes.values())

    def reset_all(self) -> None:
        with self._lock:
            for env in self._envelopes.values():
                env.reset()


@dataclass
class BudgetCheckResult:
    """Aggregate result from checking all envelope layers."""
    allowed: bool
    reason: str
    details: dict[str, object] = field(default_factory=dict)


class BudgetManager:
    """Coordinates per-agent, per-task, and per-tool budget envelopes.

    Before an operation, check_all() gates against all configured limits.
    The first denied envelope short-circuits with its reason.
    """

    def __init__(
        self,
        per_agent: PerAgentEnvelope | None = None,
        per_task: PerTaskEnvelope | None = None,
        per_tool: PerToolEnvelope | None = None,
    ) -> None:
        self.per_agent = per_agent or PerAgentEnvelope()
        self.per_task = per_task or PerTaskEnvelope()
        self.per_tool = per_tool or PerToolEnvelope()

    def check_all(
        self,
        agent_type: str | None = None,
        task_id: str | None = None,
        tool_type: str | None = None,
        amount: float = 0.0,
    ) -> BudgetCheckResult:
        """Gate an operation against all applicable budget envelopes.

        Order: tool -> task -> agent. The first exhausted envelope blocks.
        """
        if tool_type is not None:
            result = self.per_tool.try_spend(tool_type, amount)
            if not result["allowed"]:
                return BudgetCheckResult(
                    allowed=False,
                    reason=str(result["reason"]),
                    details={"layer": "tool", **result},
                )

        if task_id is not None:
            result = self.per_task.try_spend(task_id, amount)
            if not result["allowed"]:
                return BudgetCheckResult(
                    allowed=False,
                    reason=str(result["reason"]),
                    details={"layer": "task", **result},
                )

        if agent_type is not None:
            result = self.per_agent.try_spend(agent_type, amount)
            if not result["allowed"]:
                return BudgetCheckResult(
                    allowed=False,
                    reason=str(result["reason"]),
                    details={"layer": "agent", **result},
                )

        return BudgetCheckResult(
            allowed=True,
            reason="ok",
            details={},
        )

    def get_status(self) -> dict[str, object]:
        return {
            "agents": self.per_agent.get_status(),
            "tasks": self.per_task.get_status(),
            "tools": self.per_tool.get_status(),
        }

    def total_spent(self) -> float:
        return (
            self.per_agent.total_spent()
            + self.per_task.total_spent()
            + self.per_tool.total_spent()
        )

    def reset_all(self) -> None:
        self.per_agent.reset_all()
        self.per_task.reset_all()
        self.per_tool.reset_all()
