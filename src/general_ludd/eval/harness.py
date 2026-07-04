"""G2 offline eval harness — runs benchmark cases against agent models."""

from __future__ import annotations

from general_ludd.eval.schema import EvalCase, EvalResult


class EvalHarness:
    def __init__(self, model: str = "sonnet") -> None:
        self.model = model

    def run_benchmark(self, cases: list[EvalCase]) -> list[EvalResult]:
        return [
            EvalResult(
                case_id=c.id,
                passed=False,
                actual_patch="",
                errors=["not yet implemented"],
            )
            for c in cases
        ]
