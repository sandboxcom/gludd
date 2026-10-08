"""Bounded, redacted visibility for persisted self-improvement outcomes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.ornith.training_data import TrainingDataCollector, TrainingExample
from general_ludd.self_improve.outcomes import OutcomeAnalyzer

OUTCOME_ROW_LIMIT: Final = 100
OUTCOME_LOOKBACK_DAYS: Final = 7
OUTCOME_GROUP_LIMIT: Final = 32

_REJECTED_OUTCOMES: Final = (
    "rejected_by_review",
    "rejected_by_gate",
    "reverted",
)
_STOP_KEYWORDS: Final = frozenset({"stop", "premature", "halt", "abort"})
_GRIND_KEYWORDS: Final = frozenset({"grind", "token", "main_thread", "inline"})


def _limits() -> dict[str, int]:
    return {
        "rows": OUTCOME_ROW_LIMIT,
        "lookback_days": OUTCOME_LOOKBACK_DAYS,
        "groups": OUTCOME_GROUP_LIMIT,
    }


def unavailable_outcome_analysis(reason: str) -> dict[str, object]:
    """Return a stable failure state without exposing an underlying exception."""
    return {
        "status": "unavailable",
        "reason": reason,
        "limits": _limits(),
    }


def _bounded_identifier(value: object, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return value.strip()[:maximum]


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _public_group(value: object) -> dict[str, object] | None:
    """Copy only the analyzer's aggregate fields into the HTTP-safe report."""
    if not isinstance(value, dict):
        return None
    numeric_fields = ("pass_rate", "avg_tokens", "avg_duration_ms")
    numeric: dict[str, float] = {}
    for field in numeric_fields:
        raw = value.get(field, 0.0)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raw = 0.0
        numeric[field] = round(max(float(raw), 0.0), 4)
    return {
        "task_type": _bounded_identifier(value.get("task_type"), maximum=32),
        "model": _bounded_identifier(value.get("model")),
        "pass_rate": numeric["pass_rate"],
        "sample_count": _nonnegative_int(value.get("sample_count")),
        "avg_tokens": numeric["avg_tokens"],
        "avg_duration_ms": numeric["avg_duration_ms"],
        "suggestion": _bounded_identifier(value.get("suggestion"), maximum=512),
    }


def summarize_outcome_examples(
    examples: Sequence[TrainingExample],
    *,
    analyzer: OutcomeAnalyzer | None = None,
) -> dict[str, object]:
    """Aggregate examples without returning prompts, artifacts, paths, or errors."""
    outcome_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    analyzer_rows: list[dict[str, object]] = []

    for example in examples:
        outcome_counts[_bounded_identifier(example.outcome, maximum=32)] += 1
        instruction = example.instruction.casefold()
        stop_match = any(keyword in instruction for keyword in _STOP_KEYWORDS)
        grind_match = any(keyword in instruction for keyword in _GRIND_KEYWORDS)
        if stop_match:
            pattern_counts["premature_stop"] += 1
        if grind_match:
            pattern_counts["grind_failure"] += 1
        if not stop_match and not grind_match:
            pattern_counts["generic_failure"] += 1

        metadata = example.metadata
        analyzer_rows.append(
            {
                "task_type": _bounded_identifier(
                    metadata.get("scaffold_kind"), maximum=32
                ),
                "model": _bounded_identifier(metadata.get("model_sha")),
                "passed": example.outcome in {"succeeded", "applied"},
                "tokens_used": _nonnegative_int(metadata.get("tokens_consumed")),
                "duration_ms": _nonnegative_int(metadata.get("duration_ms")),
            }
        )

    analyzer_result = (analyzer or OutcomeAnalyzer(min_samples=1)).analyze(
        outcomes=analyzer_rows
    )
    raw_groups = analyzer_result.get("suggestions", [])
    public_groups: list[dict[str, object]] = []
    if isinstance(raw_groups, list):
        for raw_group in raw_groups:
            public_group = _public_group(raw_group)
            if public_group is not None:
                public_groups.append(public_group)

    groups_at_limit = len(public_groups) > OUTCOME_GROUP_LIMIT
    groups = public_groups[:OUTCOME_GROUP_LIMIT]
    return {
        "status": "available",
        "sample_count": len(examples),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "pattern_counts": dict(sorted(pattern_counts.items())),
        "groups": groups,
        "group_count": len(groups),
        "rows_at_limit": len(examples) >= OUTCOME_ROW_LIMIT,
        "groups_at_limit": groups_at_limit,
        "limits": _limits(),
    }


async def collect_outcome_analysis(session: AsyncSession) -> dict[str, object]:
    """Read one recent, bounded rejected-outcome window and aggregate it."""
    collector = TrainingDataCollector(session)
    examples = await collector.list_by_statuses(
        statuses=list(_REJECTED_OUTCOMES),
        limit=OUTCOME_ROW_LIMIT,
        lookback_days=OUTCOME_LOOKBACK_DAYS,
    )
    return summarize_outcome_examples(examples)
