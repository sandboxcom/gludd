"""Scoring functions for G2 eval harness."""

from __future__ import annotations

import difflib

from general_ludd.eval.schema import EvalCase, EvalResult


def compute_patch_similarity(expected: str, actual: str) -> float:
    if not expected and not actual:
        return 1.0
    if not expected or not actual:
        return 0.0
    return difflib.SequenceMatcher(None, expected, actual).ratio()


def check_assertions(assertions: dict[str, str], patch: str) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for key, value in assertions.items():
        if key in ("patch_contains", "filename"):
            results[key] = value in patch
        elif key == "line_count_min":
            try:
                threshold = int(value)
            except (TypeError, ValueError):
                results[key] = False
            else:
                results[key] = len(patch.splitlines()) >= threshold
        else:
            results[key] = value in patch
    return results


def composite_eval_score(
    case: EvalCase,
    patch: str,
    tokens_used: int,
    duration_ms: int,
) -> EvalResult:
    similarity = compute_patch_similarity(case.expected_patch, patch)

    assertion_results = check_assertions(case.assertions, patch) if case.assertions else {}
    assertions_passed = (
        sum(1 for v in assertion_results.values() if v) if assertion_results else 0
    )
    assertions_total = len(assertion_results)
    assertion_score = (
        assertions_passed / assertions_total if assertions_total > 0 else 1.0
    )

    score = 0.6 * similarity + 0.4 * assertion_score
    passed = score >= 0.7

    errors: list[str] = []
    if similarity < 0.5:
        errors.append(f"low_similarity={similarity:.2f}")
    for key, ok in assertion_results.items():
        if not ok:
            errors.append(f"assertion_failed:{key}")

    return EvalResult(
        case_id=case.id,
        passed=passed,
        actual_patch=patch,
        score=round(score, 4),
        tokens_used=tokens_used,
        duration_ms=duration_ms,
        errors=errors,
    )
