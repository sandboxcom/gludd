"""G2 offline eval harness — runs benchmark cases against agent models."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.eval.scorers import composite_eval_score

if TYPE_CHECKING:
    from general_ludd.eval.model import ModelEvaluator


class EvalHarness:
    def __init__(
        self,
        model: str = "sonnet",
        evaluator: ModelEvaluator | None = None,
    ) -> None:
        self.model = model
        self._evaluator = evaluator
        self._last_results: list[EvalResult] = []

    def run_benchmark(self, cases: list[EvalCase]) -> list[EvalResult]:
        if self._evaluator is None:
            results = [
                EvalResult(
                    case_id=c.id,
                    passed=False,
                    actual_patch="",
                    errors=["no evaluator configured"],
                )
                for c in cases
            ]
            self._last_results = results
            return results

        results_start = time.monotonic()
        results: list[EvalResult] = []
        for c in cases:
            try:
                result = self.run_single(c)
            except Exception as exc:
                result = EvalResult(
                    case_id=c.id,
                    passed=False,
                    actual_patch="",
                    errors=[str(exc)],
                )
            results.append(result)

        self._last_results = results
        return results

    def run_single(self, case: EvalCase) -> EvalResult:
        if self._evaluator is None:
            return EvalResult(
                case_id=case.id,
                passed=False,
                actual_patch="",
                errors=["no evaluator configured"],
            )
        start = time.monotonic()
        patch = self._evaluator.generate_patch(case)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return composite_eval_score(case, patch, tokens_used=0, duration_ms=elapsed_ms)

    @property
    def last_results(self) -> list[EvalResult]:
        return list(self._last_results)

    @property
    def ready(self) -> bool:
        return self._evaluator is not None
