"""Tests for the reachable, tiered SLM compaction entry (compaction/aggressive).

Covers the two entry points ``compact_messages`` / ``compact_dicts``:
* a working ``summarize_fn`` shrinks a long history,
* ``summarize_fn=None`` still shrinks via the extractive fallback,
* a short history (below the level threshold) passes through untouched and never
  calls the summarizer,
* the fail-soft boundary: neither a raising ``summarize_fn`` nor a hard failure
  inside the SLM strategy may propagate — the latter returns the ORIGINAL
  messages unchanged.
"""

from __future__ import annotations

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.aggressive import (
    LEVELS,
    CompactionLevel,
    compact_dicts,
    compact_messages,
    level_at,
)

# LEVELS[1] == CompactionLevel(4, 0.8): keep 4 recent, compact at ratio >= 0.8.
LEVEL = LEVELS[1]


def _long_history(n: int = 12, per_tokens: int = 10_000) -> list[ContextMessage]:
    """A history whose token total crosses the 0.8 ratio (0.8 * 128000 = 102400).

    n * per_tokens = 120000 tokens > 102400, so ``needs_compaction`` fires. All
    non-system so preserve_recent leaves a real old middle to summarize.
    """
    return [
        ContextMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"message {i} " + ("x" * 40),
            token_estimate=per_tokens,
        )
        for i in range(n)
    ]


def _short_history() -> list[ContextMessage]:
    """Well below the threshold: 3 tiny messages."""
    return [
        ContextMessage(role="user", content="hi", token_estimate=10),
        ContextMessage(role="assistant", content="hello", token_estimate=10),
        ContextMessage(role="user", content="ok", token_estimate=10),
    ]


class _Spy:
    def __init__(self, ret: str = "SHORT SUMMARY") -> None:
        self.calls: list[tuple[str, str]] = []
        self._ret = ret

    def __call__(self, goal: str, text: str) -> str:
        self.calls.append((goal, text))
        return self._ret


def test_working_summarize_fn_shrinks_long_history() -> None:
    msgs = _long_history()
    spy = _Spy()
    out = compact_messages(msgs, goal="do the thing", level=LEVEL, summarize_fn=spy)

    assert len(out) < len(msgs)
    assert spy.calls, "summarize_fn should have been invoked"
    # The 4 most-recent turns are preserved verbatim at the tail.
    assert out[-1].content == msgs[-1].content
    # The summarized middle is a single system message.
    assert any(m.is_system and "prior context" in m.content for m in out)


def test_none_summarize_fn_extractive_fallback_still_shrinks() -> None:
    msgs = _long_history()
    out = compact_messages(msgs, goal="g", level=LEVEL, summarize_fn=None)
    assert len(out) < len(msgs)


def test_short_history_below_threshold_unchanged_no_summarize_call() -> None:
    msgs = _short_history()
    spy = _Spy()
    out = compact_messages(msgs, goal="g", level=LEVEL, summarize_fn=spy)
    assert out == msgs
    assert spy.calls == [], "summarizer must not run below the threshold"


def test_summarize_fn_raising_is_fail_soft() -> None:
    """A raising summarize_fn must never propagate.

    The SLM strategy catches summarizer errors and falls back to extractive
    truncation, so the call still returns a valid (shrunk) list rather than
    crashing the generation path.
    """

    def boom(goal: str, text: str) -> str:
        raise RuntimeError("model exploded")

    msgs = _long_history()
    out = compact_messages(msgs, goal="g", level=LEVEL, summarize_fn=boom)
    assert isinstance(out, list)
    assert len(out) <= len(msgs)


def test_hard_failure_in_strategy_returns_original_unchanged(monkeypatch) -> None:
    """If the SLM strategy itself raises, the outer wrapper returns the ORIGINAL.

    Compaction can NEVER raise into a caller — the whole body is wrapped, so a
    hard failure yields the untouched input, not a partial/empty result.
    """

    class _Exploding:
        def __init__(self, *a, **k) -> None:
            pass

        def compact(self, request):
            raise ValueError("strategy blew up")

    monkeypatch.setattr(
        "general_ludd.compaction.aggressive.SLMCompactor", _Exploding
    )
    msgs = _long_history()
    out = compact_messages(msgs, goal="g", level=LEVEL, summarize_fn=_Spy())
    assert out == msgs


def test_compact_dicts_round_trips_and_shrinks() -> None:
    dicts = [{"role": m.role, "content": m.content} for m in _long_history()]
    # Give the dicts real token weight by inflating content so the estimate
    # (len // 4) crosses the threshold on its own.
    dicts = [{"role": d["role"], "content": "y" * 40_000} for d in dicts]
    spy = _Spy()
    out = compact_dicts(dicts, goal="g", level=LEVEL, summarize_fn=spy)
    assert all(set(d.keys()) == {"role", "content"} for d in out)
    assert len(out) < len(dicts)
    assert spy.calls


def test_compact_dicts_fail_soft_returns_original(monkeypatch) -> None:
    class _Exploding:
        def __init__(self, *a, **k) -> None:
            pass

        def compact(self, request):
            raise ValueError("boom")

    monkeypatch.setattr(
        "general_ludd.compaction.aggressive.SLMCompactor", _Exploding
    )
    dicts = [{"role": "user", "content": "y" * 40_000} for _ in range(12)]
    out = compact_dicts(dicts, goal="g", level=LEVEL, summarize_fn=_Spy())
    assert out == dicts


def test_levels_ladder_is_ordered_least_to_most_aggressive() -> None:
    assert LEVELS[0] == CompactionLevel(8, 0.9)
    assert LEVELS[-1] == CompactionLevel(1, 0.5)
    # preserve_recent strictly decreases (more aggressive) down the ladder.
    preserves = [lvl.preserve_recent for lvl in LEVELS]
    assert preserves == sorted(preserves, reverse=True)


def test_level_at_infinite_index_clamps_without_raising() -> None:
    """An infinite config index must clamp, never crash the loop that reads it.

    ``int(float("inf"))`` raises OverflowError (not TypeError/ValueError), so
    without OverflowError in level_at's caught tuple an ``inf`` compaction.level
    would propagate out of the generation/tool loop. level_at must catch it and
    fall back to a valid clamped rung.
    """
    result = level_at(float("inf"))
    assert isinstance(result, CompactionLevel)
    assert result in LEVELS
    # Negative infinity likewise clamps to a valid rung rather than raising.
    assert level_at(float("-inf")) in LEVELS
