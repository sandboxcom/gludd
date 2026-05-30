"""Token window manager for tracking and enforcing per-agent token budgets."""

from __future__ import annotations


class TokenWindowManager:
    def __init__(self, default_budget: int = 128000) -> None:
        self._default_budget = default_budget
        self._usage: dict[str, int] = {}

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def check_budget(self, agent_name: str, task_prompt: str, max_tokens: int) -> bool:
        estimated = self.estimate_tokens(task_prompt)
        remaining = self.get_remaining_budget(agent_name)
        budget_cap = min(max_tokens, remaining)
        return estimated <= budget_cap

    def get_remaining_budget(self, agent_name: str) -> int:
        used = self._usage.get(agent_name, 0)
        return max(0, self._default_budget - used)

    def record_usage(self, agent_name: str, tokens_used: int) -> None:
        self._usage[agent_name] = self._usage.get(agent_name, 0) + tokens_used

    def compact_context(self, agent_name: str) -> str:
        remaining = self.get_remaining_budget(agent_name)
        threshold = self._default_budget * 0.2
        if remaining > threshold:
            return ""
        return f"[compacted context for {agent_name}: {remaining} tokens remaining]"
