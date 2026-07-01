"""Core types for context compaction: the request/result models and the
``Compactor`` interface every strategy implements.

Design notes
------------
- A compaction operates on a list of :class:`~general_ludd.agents.context.ContextMessage`
  (the same typed record the hibernation snapshotter and ``ContextCompactor``
  use), so a compactor drops straight into the existing context path.
- ``CompactionRequest.goal`` carries the CURRENT work item's objective.  A
  work-item-aware compactor uses it to decide what to keep vs. drop — the whole
  point is "optimize what the LLM gets sent for *that specific* work item".
- Token counts use the same cheap ``len // 4`` heuristic as ``ContextCompactor``
  so ratios are comparable across the baseline and SLM strategies.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from general_ludd.agents.context import ContextMessage

# Same heuristic as agents.context.ContextCompactor.estimate_tokens — one shared
# definition so compression ratios are apples-to-apples across strategies.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate (``len // 4``)."""
    return len(text) // _CHARS_PER_TOKEN


def messages_tokens(messages: list[ContextMessage]) -> int:
    """Total estimated tokens across a message list.

    Prefers each message's own ``token_estimate`` when populated (non-zero),
    else falls back to estimating from ``content``.
    """
    total = 0
    for m in messages:
        total += m.token_estimate if m.token_estimate > 0 else estimate_tokens(m.content)
    return total


class CompactionRequest(BaseModel):
    """A request to compact a context for one work item."""

    model_config = {"strict": True, "arbitrary_types_allowed": True}

    messages: list[ContextMessage] = Field(default_factory=list)
    # The current work item's objective — steers a work-item-aware compactor.
    goal: str = ""
    # Soft token budget for the compacted output. None = "compact as the
    # strategy sees fit" (strategies may still honor preserve_recent).
    target_tokens: int | None = None
    # Always keep at least this many of the most-recent non-system messages
    # verbatim (recency is the cheapest, safest signal).
    preserve_recent: int = 4


class CompactionResult(BaseModel):
    """The outcome of a compaction: the shrunken messages plus metrics."""

    model_config = {"strict": True}

    messages: list[ContextMessage] = Field(default_factory=list)
    method: str = "noop"
    original_tokens: int = 0
    compacted_tokens: int = 0
    dropped_messages: int = 0

    @property
    def ratio(self) -> float:
        """Compacted/original token ratio (0..1, lower = more compression)."""
        if self.original_tokens <= 0:
            return 1.0
        return self.compacted_tokens / self.original_tokens

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compacted_tokens)


@runtime_checkable
class Compactor(Protocol):
    """A pluggable compaction strategy.

    Implementations MUST be pure w.r.t. the request (no hidden global state) so
    the arena can evaluate several side by side deterministically.
    """

    name: str

    def compact(self, request: CompactionRequest) -> CompactionResult:
        ...
