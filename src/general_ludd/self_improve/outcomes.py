"""Outcome-driven self-improvement — analyze execution outcomes to guide learning."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from general_ludd.eval.schema import EvalResult

logger = logging.getLogger(__name__)


class OutcomeAnalyzer:
    """Analyze execution outcomes (pass/fail/timeout/error) to guide self-improvement.

    G5 outcome-driven self-improve: each subagent/task execution produces an
    outcome record. This analyzer aggregates them, detects patterns, and
    generates actionable improvement suggestions.
    """

    DEFAULT_MIN_SAMPLES: int = 10

    def __init__(self, min_samples: int | None = None) -> None:
        self.min_samples = min_samples or self.DEFAULT_MIN_SAMPLES
        self._outcomes: list[dict[str, Any]] = []

    def analyze(
        self,
        outcomes: list[dict[str, Any]] | None = None,
        threshold: float = 0.5,
    ) -> dict[str, Any]:
        if outcomes is not None:
            self._outcomes.extend(outcomes)

        if not self._outcomes:
            return {"status": "no_data", "suggestions": []}

        suggestions = _compute_suggestions(self._outcomes, threshold)
        return {"status": "analyzed", "suggestions": suggestions}


def analyze_eval_results(
    outcomes: Sequence[EvalResult],
    case_task_types: dict[str, str],
    model: str = "",
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Analyze EvalResult objects and return improvement suggestions.

    Groups results by task_type x model, computes pass_rate / avg_tokens /
    avg_duration, and flags groups with pass_rate below threshold.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for r in outcomes:
        task_type = case_task_types.get(r.case_id, "unknown")
        key = (task_type, model)
        groups[key].append({
            "case_id": r.case_id,
            "task_type": task_type,
            "model": model,
            "passed": r.passed,
            "tokens_used": r.tokens_used,
            "duration_ms": r.duration_ms,
        })

    all_outcomes: list[dict[str, Any]] = []
    for outcomes_list in groups.values():
        all_outcomes.extend(outcomes_list)

    suggestions = _compute_suggestions(all_outcomes, threshold)
    return {"status": "analyzed", "suggestions": suggestions}


def _compute_suggestions(
    outcomes: list[dict[str, Any]],
    threshold: float,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for o in outcomes:
        task_type = o.get("task_type", "unknown")
        model = o.get("model", "unknown")
        groups[(task_type, model)].append(o)

    suggestions: list[dict[str, Any]] = []
    for (task_type, model), group_outcomes in groups.items():
        total = len(group_outcomes)
        passed = sum(1 for o in group_outcomes if o.get("passed", False))
        pass_rate = passed / total if total > 0 else 0.0
        avg_tokens = (
            sum(o.get("tokens_used", 0) for o in group_outcomes) / total
            if total > 0
            else 0.0
        )
        avg_duration_ms = (
            sum(o.get("duration_ms", 0) for o in group_outcomes) / total
            if total > 0
            else 0.0
        )

        if pass_rate < threshold:
            suggestions.append({
                "task_type": task_type,
                "model": model,
                "pass_rate": round(pass_rate, 4),
                "sample_count": total,
                "avg_tokens": round(avg_tokens, 1),
                "avg_duration_ms": round(avg_duration_ms, 1),
                "suggestion": _generate_suggestion(task_type, model, pass_rate),
            })

    return suggestions


def _generate_suggestion(task_type: str, model: str, pass_rate: float) -> str:
    if pass_rate == 0.0:
        return (
            f"{task_type} tasks fail entirely on {model}. "
            "Try a different model or investigate prompt compatibility."
        )
    if pass_rate < 0.25:
        return (
            f"{task_type} has very low pass rate ({pass_rate:.0%}) on {model}. "
            "Try prompt variant or different model."
        )
    return (
        f"{task_type} pass rate is below threshold ({pass_rate:.0%}) on {model}. "
        "Try prompt variant or different model."
    )
