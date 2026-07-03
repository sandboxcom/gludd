"""Aggressive, tiered SLM context-compaction — the REACHABLE generation-path entry.

The plain :class:`~general_ludd.compaction.slm.SLMCompactor` summarizes the older
middle of a conversation via a small local model.  This module wraps it in a
config-selectable *aggression ladder* and a HARD fail-soft boundary so the
generation path can turn compaction on without any risk that compaction itself
raises into the caller.

Aggression is expressed as a :class:`CompactionLevel`:

* ``preserve_recent`` — how many most-recent non-system turns are kept verbatim
  (fewer = more aggressive).
* ``threshold`` — the :class:`~general_ludd.agents.context.ContextCompactor`
  compaction ratio that must be crossed before we compact at all (lower = we
  start compacting sooner = more aggressive).
* ``target_tokens`` — optional soft output budget handed to the SLM strategy.

:data:`LEVELS` is the ladder, least-aggressive first.  ``compact_messages`` /
``compact_dicts`` are the two entry points; BOTH are fail-soft — on any
exception they return the ORIGINAL messages unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.compaction.base import CompactionRequest, estimate_tokens
from general_ludd.compaction.slm import SLMCompactor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompactionLevel:
    """One rung of the aggression ladder.

    ``preserve_recent`` verbatim recent turns; compact only once the plain
    ContextCompactor's ratio crosses ``threshold``; hand ``target_tokens`` (soft)
    to the SLM strategy.
    """

    preserve_recent: int
    threshold: float
    target_tokens: int | None = None


# Least-aggressive first (index 0). Later rungs keep fewer recent turns and
# start compacting sooner.
LEVELS: list[CompactionLevel] = [
    CompactionLevel(8, 0.9),
    CompactionLevel(4, 0.8),
    CompactionLevel(2, 0.6),
    CompactionLevel(1, 0.5),
]

# Conservative default rung used by the generation-path callers when compaction
# is enabled without an explicit index.
_DEFAULT_LEVEL_INDEX = 1


def level_at(index: int) -> CompactionLevel:
    """Return the aggression rung at ``index``, clamped into :data:`LEVELS`.

    A garbage / out-of-range index never raises — it clamps to the nearest valid
    rung so config threading is fail-soft.
    """
    try:
        i = max(0, min(int(index), len(LEVELS) - 1))
    except (TypeError, ValueError, OverflowError):
        # OverflowError guards an infinite index: int(float("inf")) raises it,
        # so an inf config value clamps to the default rung instead of crashing
        # the loop that reads compaction.level.
        i = _DEFAULT_LEVEL_INDEX
    return LEVELS[i]


def compact_messages(
    messages: list[ContextMessage],
    *,
    goal: str,
    level: CompactionLevel,
    summarize_fn: Callable[[str, str], str] | None = None,
    max_tokens: int = 128000,
) -> list[ContextMessage]:
    """Compact ``messages`` at the given aggression ``level``, fail-soft.

    Gates on ``ContextCompactor(max_tokens=..., compaction_threshold=level.threshold)``
    so short conversations pass through untouched, then summarizes the old middle
    via an :class:`SLMCompactor` (using ``summarize_fn``, or the extractive
    fallback when ``summarize_fn`` is None). ``max_tokens`` is the budget the
    ratio gate compares against (default matches ``ContextCompactor``); callers
    with a custom window pass their own.

    The ENTIRE body is wrapped in ``try/except Exception`` — compaction must
    NEVER raise into a caller, so on any error the original ``messages`` are
    returned unchanged.
    """
    try:
        gate = ContextCompactor(
            max_tokens=max_tokens, compaction_threshold=level.threshold
        )
        if not gate.needs_compaction(messages):
            return messages
        compactor = SLMCompactor(
            summarize_fn,
            preserve_recent=level.preserve_recent,
        )
        result = compactor.compact(
            CompactionRequest(
                messages=messages,
                goal=goal,
                preserve_recent=level.preserve_recent,
                target_tokens=level.target_tokens,
            )
        )
        return result.messages
    except Exception:
        # Fail SOFT: compaction can never crash the generation path it sits on.
        logger.warning("aggressive compaction failed; returning original messages", exc_info=True)
        return messages


def compact_dicts(
    messages: list[dict[str, str]],
    *,
    goal: str,
    level: CompactionLevel,
    summarize_fn: Callable[[str, str], str] | None = None,
    max_tokens: int = 128000,
) -> list[dict[str, str]]:
    """dict-in/dict-out adapter around :func:`compact_messages`.

    Mirrors the ``{"role", "content"}`` <-> :class:`ContextMessage` conversion
    shape used by ``AgentCapabilities.prepare_messages``.  Also fail-soft: any
    conversion error returns the original list unchanged.
    """
    try:
        msgs = [
            ContextMessage(
                role=turn.get("role", "user"),
                content=turn.get("content", ""),
                token_estimate=estimate_tokens(turn.get("content", "")),
                is_system=turn.get("role") == "system",
            )
            for turn in messages
        ]
        compacted = compact_messages(
            msgs,
            goal=goal,
            level=level,
            summarize_fn=summarize_fn,
            max_tokens=max_tokens,
        )
        return [{"role": m.role, "content": m.content} for m in compacted]
    except Exception:
        logger.warning("aggressive compact_dicts failed; returning original messages", exc_info=True)
        return messages
