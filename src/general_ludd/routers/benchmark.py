"""Benchmark and bounded A/B comparison HTTP routes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException
from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator

from general_ludd.abtest.compare import run_ab
from general_ludd.abtest.workloads import import_module_workload
from general_ludd.db.repository import BenchmarkRepository, PromptProfileRepository
from general_ludd.routers._runtime import StrictRuntimeRequest
from general_ludd.routers._util import get_session_factory as _get_session_factory
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.security.capability_guard import RequireCapability
from general_ludd.security.sanitize import is_path_within


class ABTestRequest(StrictRuntimeRequest):
    """Bounded import-only candidate comparison request."""

    baseline_root: StrictStr = Field(min_length=1, max_length=4096)
    candidate_root: StrictStr = Field(min_length=1, max_length=4096)
    module: StrictStr = Field(
        min_length=1,
        max_length=512,
        pattern=r"^general_ludd(?:\.[A-Za-z_][A-Za-z0-9_]*)+$",
    )
    expect_attr: StrictStr | None = Field(
        default=None,
        max_length=256,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    timeout: StrictFloat = Field(default=60.0, ge=0.1, le=120.0)
    mem_limit_mb: StrictInt = Field(default=512, ge=64, le=8192)

    @field_validator("baseline_root", "candidate_root")
    @classmethod
    def _require_absolute_root(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("A/B roots must be absolute")
        return value


def _abtest_allowed_roots(app: FastAPI) -> list[Path]:
    roots = [Path("/tmp/gludd-worktrees")]
    project_root = getattr(app.state, "_project_root", None)
    if project_root:
        roots.append(Path(project_root))
    return roots


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register benchmark and A/B comparison routes on ``app``."""

    @app.post(
        "/admin/abtest/run",
        dependencies=[Depends(RequireCapability(resource="admin:abtest", action="execute"))],
    )
    async def admin_abtest_run(req: ABTestRequest) -> dict[str, object]:
        allowed_roots = _abtest_allowed_roots(app)
        for selected in (req.baseline_root, req.candidate_root):
            if not any(is_path_within(selected, str(root)) for root in allowed_roots):
                raise HTTPException(status_code=422, detail="A/B root is outside daemon-owned worktrees")
            if not Path(selected).is_dir():
                raise HTTPException(status_code=422, detail="A/B root does not exist")
        workload = import_module_workload(req.module, req.expect_attr)
        try:
            verdict = await asyncio.wait_for(
                asyncio.to_thread(
                    run_ab,
                    req.baseline_root,
                    req.candidate_root,
                    workload,
                    req.timeout,
                    req.mem_limit_mb,
                ),
                timeout=req.timeout * 2.0 + 10.0,
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="A/B comparison timed out") from exc
        return {"verdict": verdict.to_dict(), "promote": verdict.promote}

    @app.get("/admin/benchmark/scores")
    async def admin_benchmark_scores(
        task_type: str | None = None,
    ) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {"scores": []}
        async with factory() as session:
            repo = BenchmarkRepository(session)
            scores = await repo.get_aggregate_scores(task_type=task_type)
            return {"scores": list(scores)}

    @app.get("/admin/benchmark/recent")
    async def admin_benchmark_recent(limit: int = 50) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {"results": []}
        async with factory() as session:
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
    ) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {"leaderboard": []}
        async with factory() as session:
            repo = BenchmarkRepository(session)
            router = AdaptiveRouter(benchmark_repo=repo)
            from general_ludd.schemas.benchmark import TaskType
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
    async def admin_benchmark_record(req: dict[str, object]) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database session")
        async with factory() as session:
            repo = BenchmarkRepository(session)
            scores = cast(dict[str, object], req.get("scores", {}))
            row = await repo.record_result(data={
                "model_profile_id": cast(str, req.get("model_profile_id", "")),
                "task_type": cast(str, req.get("task_type", "feature")),
                "success": cast(bool, req.get("success", True)),
                "prompt_profile_id": cast(str | None, req.get("prompt_profile_id")),
                "completion_score": scores.get("completion", 0.0),
                "code_quality_score": scores.get("code_quality", 0.0),
                "instruction_adherence_score": scores.get("instruction", 0.0),
                "token_efficiency_score": scores.get("token_efficiency", 0.0),
                "time_seconds": cast(float, req.get("time_seconds", 0.0)),
                "input_tokens": cast(int, req.get("input_tokens", 0)),
                "output_tokens": cast(int, req.get("output_tokens", 0)),
                "cost_usd": cast(float, req.get("cost_usd", 0.0)),
                "error_message": cast(str, req.get("error_message", "")),
                "raw_output": cast(str, req.get("raw_output", "")),
            })
            await session.commit()
            return {"id": row.id, "success": row.success}

    @app.get("/admin/prompt-profiles")
    async def admin_prompt_profiles() -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {"profiles": []}
        async with factory() as session:
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
