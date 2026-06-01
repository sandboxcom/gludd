"""Context compaction for managing conversation history within token budgets."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel


class ContextMessage(BaseModel):
    model_config = {"strict": True}

    role: str
    content: str
    token_estimate: int = 0
    is_system: bool = False
    timestamp: float = 0.0


class ContextCompactor:
    def __init__(
        self,
        max_tokens: int = 128000,
        compaction_threshold: float = 0.8,
        preserve_recent_count: int = 4,
    ) -> None:
        self._max_tokens = max_tokens
        self._compaction_threshold = compaction_threshold
        self._preserve_recent_count = preserve_recent_count

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def get_compaction_ratio(self, messages: list[ContextMessage]) -> float:
        if self._max_tokens == 0:
            return 0.0
        total = sum(m.token_estimate for m in messages)
        return total / self._max_tokens

    def needs_compaction(self, messages: list[ContextMessage]) -> bool:
        return self.get_compaction_ratio(messages) >= self._compaction_threshold

    def compact(
        self,
        messages: list[ContextMessage],
        summary_fn: Callable[[str], str] | None = None,
    ) -> list[ContextMessage]:
        if not messages:
            return []

        if not self.needs_compaction(messages):
            return messages

        system_messages: list[ContextMessage] = []
        non_system: list[ContextMessage] = []

        for msg in messages:
            if msg.is_system:
                system_messages.append(msg)
            else:
                non_system.append(msg)

        if len(non_system) <= self._preserve_recent_count:
            return messages

        split = len(non_system) - self._preserve_recent_count
        old_messages = non_system[:split]
        recent_messages = non_system[split:]

        concatenated = "\n".join(m.content for m in old_messages)

        if summary_fn is not None:
            summarized = summary_fn(concatenated)
        else:
            summarized = concatenated[:500]
            if len(concatenated) > 500:
                summarized += "..."

        summary_msg = ContextMessage(
            role="system",
            content=f"[prior context] {summarized}",
            token_estimate=self.estimate_tokens(f"[prior context] {summarized}"),
            is_system=True,
        )

        return [*system_messages, summary_msg, *recent_messages]
