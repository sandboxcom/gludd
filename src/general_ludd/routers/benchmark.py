from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/admin/benchmark/scores")
    async def admin_benchmark_scores(
        task_type: str | None = None,
    ) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"scores": []}
        repo = BenchmarkRepository(session)
        scores = await repo.get_aggregate_scores(task_type=task_type)
        return {"scores": [dict(s) for s in scores]}

    @app.get("/admin/benchmark/recent")
    async def admin_benchmark_recent(limit: int = 50) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"results": []}
        repo = BenchmarkRepository(session)
        results = await repo.list_recent(limit=limit)
        return {
            "results": [
                {
                    "id": r.id,
                    "prompt_profile_id": r.prompt_profile_id,
                    "model_profile_id": r.model_profile_id,
                    "task_type": r.task_type,
                    "completion_score": r.completion_score,
                    "code_quality_score": r.code_quality_score,
                    "instruction_adherence_score": r.instruction_adherence_score,
                    "token_efficiency_score": r.token_efficiency_score,
                    "success": r.success,
                    "cost_usd": r.cost_usd,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in results
            ]
        }

    @app.get("/admin/benchmark/leaderboard")
    async def admin_benchmark_leaderboard(
        task_type: str | None = None,
    ) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"leaderboard": []}
        repo = BenchmarkRepository(session)
        router = AdaptiveRouter(benchmark_repo=repo)
        tt = TaskType(task_type) if task_type else None
        lb = await router.get_leaderboard(task_type=tt)
        return {
            "leaderboard": [
                {
                    "prompt_profile_id": c.prompt_profile_id,
                    "model_profile_id": c.model_profile_id,
                    "composite_score": c.composite_score,
                    "avg_cost_usd": c.avg_cost_usd,
                    "sample_count": c.sample_count,
                    "task_type": c.task_type.value,
                }
                for c in lb
            ]
        }

    @app.post("/admin/benchmark/record")
    async def admin_benchmark_record(req: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.db.repository import BenchmarkRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            raise HTTPException(status_code=503, detail="No database session")
        repo = BenchmarkRepository(session)
        row = await repo.record_result(
            model_profile_id=req.get("model_profile_id", ""),
            task_type=req.get("task_type", "feature"),
            scores=req.get("scores", {}),
            success=req.get("success", True),
            prompt_profile_id=req.get("prompt_profile_id"),
            task_description=req.get("task_description", ""),
            time_seconds=req.get("time_seconds", 0.0),
            input_tokens=req.get("input_tokens", 0),
            output_tokens=req.get("output_tokens", 0),
            cost_usd=req.get("cost_usd", 0.0),
            error_message=req.get("error_message", ""),
            raw_output=req.get("raw_output", ""),
        )
        return {"id": row.id, "success": row.success}

    @app.get("/admin/prompt-profiles")
    async def admin_prompt_profiles() -> dict[str, Any]:
        from general_ludd.db.repository import PromptProfileRepository

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"profiles": []}
        repo = PromptProfileRepository(session)
        profiles = await repo.list_all()
        return {
            "profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "source": p.source,
                    "source_url": p.source_url,
                    "version": p.version,
                }
                for p in profiles
            ]
        }
