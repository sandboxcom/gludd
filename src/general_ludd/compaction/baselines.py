"""Baseline compactors — the mechanisms the SLM compactor must beat.

- :class:`NoOpCompactor` — control (no compression); the fidelity ceiling.
- :class:`TruncationCompactor` — drop the oldest non-system messages, keep the
  most recent + all system messages.  The naive "just fit the window" baseline.
- :class:`ContextCompactorAdapter` — wraps the existing
  ``agents.context.ContextCompactor`` (extractive summary of old messages) so
  the current in-tree mechanism is a first-class competitor in the arena.
"""

from __future__ import annotations

from collections.abc import Callable

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.compaction.base import (
    CompactionRequest,
    CompactionResult,
    estimate_tokens,
    messages_tokens,
)


def _split_system(
    messages: list[ContextMessage],
) -> tuple[list[ContextMessage], list[ContextMessage]]:
    system = [m for m in messages if m.is_system]
    rest = [m for m in messages if not m.is_system]
    return system, rest


def _result(
    method: str,
    original: list[ContextMessage],
    compacted: list[ContextMessage],
) -> CompactionResult:
    return CompactionResult(
        messages=compacted,
        method=method,
        original_tokens=messages_tokens(original),
        compacted_tokens=messages_tokens(compacted),
        dropped_messages=max(0, len(original) - len(compacted)),
    )


class NoOpCompactor:
    """Returns the context unchanged. The fidelity ceiling / compression floor."""

    name = "noop"

    def compact(self, request: CompactionRequest) -> CompactionResult:
        return _result(self.name, request.messages, list(request.messages))


class TruncationCompactor:
    """Keep every system message + the most-recent ``preserve_recent`` others.

    When ``target_tokens`` is set, keep as many recent messages as fit under the
    budget (system messages are always kept).  This is the cheapest strategy and
    the honest baseline: it preserves recency but blindly discards older content.
    """

    name = "truncate"

    def compact(self, request: CompactionRequest) -> CompactionResult:
        system, rest = _split_system(request.messages)
        keep = max(0, request.preserve_recent)
        recent = rest[-keep:] if keep else []

        if request.target_tokens is not None:
            # Grow the kept window backwards until we'd exceed the budget.
            budget = request.target_tokens - messages_tokens(system)
            recent = []
            for m in reversed(rest):
                cost = m.token_estimate if m.token_estimate > 0 else estimate_tokens(m.content)
                if messages_tokens(recent) + cost > budget and len(recent) >= keep:
                    break
                recent.insert(0, m)

        return _result(self.name, request.messages, [*system, *recent])


class ContextCompactorAdapter:
    """Adapts the existing ``agents.context.ContextCompactor`` as a compactor.

    ``ContextCompactor`` summarizes the old (non-recent, non-system) messages
    into a single ``[prior context]`` system message.  By default it truncates
    the concatenated old text to 500 chars, but an injected ``summary_fn`` (e.g.
    an SLM) can produce a real abstractive summary — which is exactly how the SLM
    strategy plugs into the same seam.
    """

    name = "context_compactor"

    def __init__(
        self,
        max_tokens: int = 128000,
        compaction_threshold: float = 0.0,
        preserve_recent_count: int = 4,
        summary_fn: Callable[[str], str] | None = None,
        name: str = "context_compactor",
    ) -> None:
        # threshold 0.0 => always compact (the arena wants an actual compaction,
        # not the pass-through that a high threshold would produce).
        self._impl = ContextCompactor(
            max_tokens=max_tokens,
            compaction_threshold=compaction_threshold,
            preserve_recent_count=preserve_recent_count,
        )
        self._summary_fn = summary_fn
        # Per-instance name so several adapter configs are distinct in the arena.
        self.name = name

    def compact(self, request: CompactionRequest) -> CompactionResult:
        compacted = self._impl.compact(list(request.messages), summary_fn=self._summary_fn)
        return _result(self.name, request.messages, compacted)
