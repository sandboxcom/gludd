"""G2 eval router — /admin/eval/run + /admin/eval/results."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from general_ludd.eval.schema import EvalCase


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/eval/run")
    async def admin_eval_run(request: Request) -> dict[str, Any]:
        harness = getattr(app.state, "eval_harness", None)
        if harness is None:
            raise HTTPException(status_code=503, detail="eval harness not configured")
        if not harness.ready:
            raise HTTPException(status_code=503, detail="no evaluator configured")

        body = await request.json() if hasattr(request, "json") else {}
        cases_raw: list[dict[str, Any]] = body.get("cases", [])
        if not cases_raw:
            raise HTTPException(status_code=422, detail="cases list is required")

        cases: list[EvalCase] = []
        for c in cases_raw:
            try:
                cases.append(
                    EvalCase(
                        id=c["id"],
                        description=c.get("description", ""),
                        input_files=c.get("input_files", {}),
                        expected_patch=c.get("expected_patch", ""),
                        task_type=c.get("task_type", ""),
                        assertions=c.get("assertions", {}),
                    )
                )
            except KeyError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"missing required field in case: {exc}",
                ) from exc

        results = harness.run_benchmark(cases)
        return {
            "run": True,
            "total": len(cases),
            "passed": sum(1 for r in results if r.passed),
            "results": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": r.score,
                    "actual_patch": r.actual_patch[:500] if r.actual_patch else "",
                    "tokens_used": r.tokens_used,
                    "duration_ms": r.duration_ms,
                    "errors": r.errors,
                }
                for r in results
            ],
        }

    @app.get("/admin/eval/results")
    async def admin_eval_results() -> dict[str, Any]:
        harness = getattr(app.state, "eval_harness", None)
        if harness is None:
            raise HTTPException(status_code=503, detail="eval harness not configured")

        results = harness.last_results
        return {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "results": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": r.score,
                    "actual_patch": r.actual_patch[:500] if r.actual_patch else "",
                    "tokens_used": r.tokens_used,
                    "duration_ms": r.duration_ms,
                    "errors": r.errors,
                }
                for r in results
            ],
        }
