"""SLM compactor — work-item-aware abstractive summarization of old context.

The strategy: keep every system message and the most-recent ``preserve_recent``
messages verbatim, and replace the older middle of the conversation with a
single ``[prior context]`` summary produced by a SMALL model, steered by the
current work item's ``goal`` so it retains goal-relevant facts and drops the
rest.

The model call is a dependency-injected callable ``summarize_fn(goal, text) ->
summary`` — so this class has no hard model dependency and tests run offline.
:func:`make_slm_summarize_fn` wires it to gludd's ``ModelGateway`` (a small local
``compactor`` profile), but any callable works.  If ``summarize_fn`` is missing
or raises, the strategy FAILS SOFT to a deterministic extractive truncation of
the old text — compaction must never crash the context path it sits on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import (
    CompactionRequest,
    CompactionResult,
    estimate_tokens,
    messages_tokens,
)

logger = logging.getLogger(__name__)

SummarizeFn = Callable[[str, str], str]

# Prompt template — instructs the SLM to compress toward the work item's goal.
_SYSTEM_PROMPT = (
    "You compress an AI agent's prior conversation into a dense factual summary. "
    "Keep only information relevant to the stated GOAL: decisions made, facts "
    "learned, file paths, identifiers, errors, and open questions. Drop chit-chat "
    "and redundancy. Preserve exact names and numbers. Be terse. No preamble."
)


def _build_summary_prompt(goal: str, text: str) -> str:
    goal_line = f"GOAL: {goal}\n\n" if goal.strip() else ""
    return f"{goal_line}Summarize the prior context below for that goal:\n\n{text}"


def _extractive_fallback(text: str, max_chars: int) -> str:
    """Deterministic, model-free summary: head + tail of the old text.

    Keeping both ends preserves the earliest setup and the latest state, which
    is more faithful than a raw head-truncation.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n...[trimmed]...\n{text[-half:]}"


class SLMCompactor:
    """Compact old context via a small model, steered by the work item goal."""

    def __init__(
        self,
        summarize_fn: SummarizeFn | None = None,
        *,
        preserve_recent: int = 4,
        fallback_max_chars: int = 2000,
        model_name: str = "compactor",
        name: str = "slm",
    ) -> None:
        self._summarize_fn = summarize_fn
        self._preserve_recent = preserve_recent
        self._fallback_max_chars = fallback_max_chars
        self.model_name = model_name
        # Per-instance name so several SLM configs are distinct in the arena.
        self.name = name

    def _summarize(self, goal: str, text: str) -> str:
        if self._summarize_fn is None:
            return _extractive_fallback(text, self._fallback_max_chars)
        try:
            out = self._summarize_fn(goal, text)
        except Exception:
            # Fail SOFT: a model hiccup must not break the caller's context.
            logger.warning("SLM summarize_fn failed; using extractive fallback", exc_info=True)
            return _extractive_fallback(text, self._fallback_max_chars)
        if not isinstance(out, str) or not out.strip():
            return _extractive_fallback(text, self._fallback_max_chars)
        return out.strip()

    def compact(self, request: CompactionRequest) -> CompactionResult:
        msgs = request.messages
        system = [m for m in msgs if m.is_system]
        rest = [m for m in msgs if not m.is_system]

        # Honor the request's preserve_recent DIRECTLY. CompactionRequest always
        # carries a concrete int (default 4), so an explicit preserve_recent=0
        # must mean "keep zero recent, summarize everything" — not silently fall
        # back to the instance default (which a falsy `or` would wrongly do). The
        # instance default (self._preserve_recent) is a convenience for callers
        # constructing requests, not an override applied here.
        keep = max(0, request.preserve_recent)
        recent = rest[-keep:] if keep else []
        old = rest[:-keep] if keep else list(rest)

        if not old:
            # Nothing to summarize — recency already fits.
            return CompactionResult(
                messages=list(msgs),
                method=self.name,
                original_tokens=messages_tokens(msgs),
                compacted_tokens=messages_tokens(msgs),
                dropped_messages=0,
            )

        old_text = "\n".join(f"[{m.role}] {m.content}" for m in old)
        summary = self._summarize(request.goal, old_text)

        # Honor ``target_tokens`` on a BEST-EFFORT basis by trimming the summary.
        # NOTE: this is a SOFT bound, not a hard guarantee. Only the summary is
        # trimmable — the preserved system and recent messages are kept verbatim.
        # If those alone already exceed ``target_tokens``, the budget for the
        # summary is 0 (summary dropped to empty) and the compacted result can
        # STILL exceed ``target_tokens``. Callers needing a hard cap must trim /
        # drop recent messages themselves (e.g. via a smaller preserve_recent).
        if request.target_tokens is not None:
            budget_chars = max(0, request.target_tokens - messages_tokens([*system, *recent])) * 4
            if budget_chars and len(summary) > budget_chars:
                summary = _extractive_fallback(summary, budget_chars)

        summary_msg = ContextMessage(
            role="system",
            content=f"[prior context — compacted for goal] {summary}",
            token_estimate=estimate_tokens(summary),
            is_system=True,
        )
        compacted = [*system, summary_msg, *recent]
        return CompactionResult(
            messages=compacted,
            method=self.name,
            original_tokens=messages_tokens(msgs),
            compacted_tokens=messages_tokens(compacted),
            dropped_messages=len(old),
        )


def make_slm_summarize_fn(
    model_gateway: Any,
    profile_id: str = "compactor",
    *,
    max_output_tokens: int = 512,
) -> SummarizeFn:
    """Wire a ``summarize_fn`` to gludd's ``ModelGateway`` (a small local model).

    Point ``profile_id`` at a locally-served small model (llama.cpp / vLLM via
    the OpenAI-compatible provider, or any registered profile).  The returned
    callable is what :class:`SLMCompactor` expects.  It never raises — on any
    gateway error it returns ``""`` so the compactor's fail-soft path engages.
    """

    def _fn(goal: str, text: str) -> str:
        try:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_summary_prompt(goal, text)},
            ]
            resp = model_gateway.call_model(
                profile_id,
                messages=messages,
                requested_max_output_tokens=max_output_tokens,
                work_type="compaction",
            )
            return str(getattr(resp, "content", "") or "")
        except Exception:
            logger.warning("compactor gateway call failed", exc_info=True)
            return ""

    return _fn
