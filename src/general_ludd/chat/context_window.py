"""ContextWindow: per-turn token tracking, sliding-window trimming, and summarization trigger.

Used by ChatSession to keep the active message list within a model's context budget.
Token estimation is a cheap heuristic (~4 chars/token); it is deliberately not a
tokenizer — the goal is a deterministic, dependency-free signal for "approaching limit."
"""

from __future__ import annotations

from typing import Final

_CHARS_PER_TOKEN: Final[int] = 4
_MIN_TOKENS: Final[int] = 1

DEFAULT_MAX_TOKENS = 8192
DEFAULT_SUMMARIZATION_THRESHOLD = 0.8
DEFAULT_RESERVE_TOKENS = 1024
DEFAULT_KEEP_RECENT = 4
SUMMARY_ROLE = "system"


class ContextWindow:
    """Tracks tokens per turn, exposes a sliding-window message selector, and
    signals when the conversation should be summarized to reclaim context."""

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        summarization_threshold: float = DEFAULT_SUMMARIZATION_THRESHOLD,
        reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        if not 0.0 < summarization_threshold <= 1.0:
            raise ValueError(
                f"summarization_threshold must be in (0, 1], got {summarization_threshold}"
            )
        if reserve_tokens < 0:
            raise ValueError(f"reserve_tokens cannot be negative, got {reserve_tokens}")
        self._max_tokens = max_tokens
        self._summarization_threshold = summarization_threshold
        self._reserve_tokens = reserve_tokens
        self._per_turn_tokens: list[int] = []

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Cheap, deterministic token estimate (~4 chars/token, floor of 1)."""
        if not text:
            return _MIN_TOKENS
        return max(_MIN_TOKENS, len(text) // _CHARS_PER_TOKEN)

    def record_turn(self, tokens: int) -> None:
        """Record the token cost of a completed turn."""
        if tokens < 0:
            raise ValueError(f"tokens cannot be negative, got {tokens}")
        self._per_turn_tokens.append(tokens)

    def per_turn_tokens(self) -> list[int]:
        """Return a copy of the per-turn token history."""
        return list(self._per_turn_tokens)

    def total_tokens(self) -> int:
        return sum(self._per_turn_tokens)

    def remaining(self) -> int:
        """Tokens left before the reserve is reached. Floors at 0."""
        return max(0, self._max_tokens - self.total_tokens() - self._reserve_tokens)

    def needs_summarization(self) -> bool:
        """True when accumulated tokens cross the summarization threshold."""
        return self.total_tokens() >= int(self._max_tokens * self._summarization_threshold)

    def sliding_window_messages(
        self,
        messages: list[dict[str, str]],
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> list[dict[str, str]]:
        """Select a window of messages: all system messages plus the most recent
        ``keep_recent`` non-system messages. Does not mutate the input."""
        if not messages:
            return []
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        kept = non_system if keep_recent >= len(non_system) else non_system[-keep_recent:] if keep_recent > 0 else []
        return system_msgs + kept

    def summarize_if_needed(
        self,
        messages: list[dict[str, str]],
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> list[dict[str, str]] | None:
        """If the threshold has been crossed, return a new message list where
        older non-system messages are folded into a system summary placeholder
        and only ``keep_recent`` recent turns are retained verbatim.

        Returns None when summarization is not yet warranted, so callers can
        treat the result as an opt-in compaction step."""
        if not self.needs_summarization():
            return None
        if not messages:
            return None

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        keep_recent = max(0, keep_recent)
        to_summarize = non_system[:-keep_recent] if keep_recent < len(non_system) else []
        kept = non_system[-keep_recent:] if keep_recent > 0 else []

        summary_lines: list[str] = []
        for m in to_summarize:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            summary_lines.append(f"- [{role}] {content}")
        summary_text = (
            "[Conversation summary] Earlier turns compacted:\n"
            + "\n".join(summary_lines)
        ) if summary_lines else "[Conversation summary] No prior turns to compact."

        base_system_content = (
            system_msgs[-1]["content"] if system_msgs else ""
        )
        merged_system = {
            "role": SUMMARY_ROLE,
            "content": f"{base_system_content}\n\n{summary_text}".strip(),
        }
        return [merged_system, *kept]
