from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from general_ludd.db.repository import TodoRepository
from general_ludd.self_improve.harness import SelfImprovementHarness


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/self-improve/analyze")
    async def admin_self_improve_analyze() -> dict[str, Any]:
        harness = SelfImprovementHarness()
        findings = harness.run_gap_analysis()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": findings,
            "findings_count": len(findings),
        }
        return {"findings": findings, "findings_count": len(findings)}

    @app.post("/admin/self-improve/run")
    async def admin_self_improve_run() -> dict[str, Any]:
        harness = SelfImprovementHarness()
        result = harness.run_full_cycle()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": result["findings"],
            "findings_count": result["findings_count"],
            "todos_enqueued": result["todos_enqueued"],
        }
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository(session)
                persisted_ids: list[str] = []
                for todo_data in result["todos"]:
                    created = await repo.create(todo_data=todo_data)
                    persisted_ids.append(created.todo_id)
                await session.commit()
                result["persisted_todo_ids"] = persisted_ids
        else:
            _daemon_state["todos"].extend(result["todos"])
        return result

    @app.get("/admin/self-improve/status")
    async def admin_self_improve_status() -> dict[str, Any]:
        last = _daemon_state.get("self_improve_last_analysis")
        if last is None:
            return {"status": "never_run", "findings_count": 0}
        return {"status": "completed", **last}
