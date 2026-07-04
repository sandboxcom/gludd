"""G2 offline eval harness — runs benchmark cases against agent models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from general_ludd.eval.schema import EvalCase, EvalResult

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

    def run_benchmark(self, cases: list[EvalCase]) -> list[EvalResult]:
        if self._evaluator is None:
            return [
                EvalResult(
                    case_id=c.id,
                    passed=False,
                    actual_patch="",
                    errors=["no evaluator configured"],
                )
                for c in cases
            ]
        results: list[EvalResult] = []
        for c in cases:
            try:
                patch = self._evaluator.generate_patch(c)
                results.append(
                    EvalResult(
                        case_id=c.id,
                        passed=True,
                        actual_patch=patch,
                    )
                )
            except Exception as exc:
                results.append(
                    EvalResult(
                        case_id=c.id,
                        passed=False,
                        actual_patch="",
                        errors=[str(exc)],
                    )
                )
        return results

    @property
    def ready(self) -> bool:
        return self._evaluator is not None
