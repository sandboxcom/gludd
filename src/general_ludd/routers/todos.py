from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class AddTodoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4096)
    queue: str = Field(default="core", pattern=r"^[a-z0-9_\-]+$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    work_type: str = Field(default="code", pattern=r"^[a-z_]+$")
    project_id: str | None = None


class LogLevelRequest(BaseModel):
    level: str


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.post("/admin/preflight")
    async def admin_run_preflight() -> dict[str, Any]:
        from general_ludd.quality.preflight import run_preflight

        result = run_preflight()
        _daemon_state["quality_gate"] = result
        return result

    @app.post("/api/todos", status_code=201)
    async def api_add_todo(req: AddTodoRequest) -> dict[str, Any]:
        todo_id = f"TODO-{uuid.uuid4().hex[:8].upper()}"
        todo: dict[str, Any] = {
            "todo_id": todo_id,
            "title": req.title,
            "description": req.description,
            "queue": req.queue,
            "priority": req.priority,
            "work_type": req.work_type,
            "status": "queued",
            "project_id": req.project_id,
        }
        _daemon_state["todos"].append(todo)
        return todo

    @app.get("/api/todos")
    async def api_list_todos(
        queue: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results = list(_daemon_state["todos"])
        if queue is not None:
            results = [t for t in results if t.get("queue") == queue]
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        if project_id is not None:
            results = [t for t in results if t.get("project_id") == project_id]
        return results

    @app.get("/api/todos/{todo_id}")
    async def api_get_todo(todo_id: str) -> dict[str, Any]:
        for todo in _daemon_state["todos"]:
            if str(todo.get("todo_id", "")) == todo_id:
                return dict(todo)
        raise HTTPException(status_code=404, detail="Todo not found")

    @app.get("/admin/todos")
    async def admin_list_todos(
        status: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        results = list(_daemon_state["todos"])
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        if project_id is not None:
            results = [t for t in results if t.get("project_id") == project_id]
        return {"todos": results, "count": len(results)}

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        queue_depths: dict[str, int] = {}
        todo_count = 0
        for todo in _daemon_state["todos"]:
            q = todo.get("queue", "unknown")
            queue_depths[q] = queue_depths.get(q, 0) + 1
            todo_count += 1

        from general_ludd import __version__

        config_dir = getattr(app.state, "_config_dir", None)
        config_paths: list[str] = []
        if config_dir and os.path.isdir(config_dir):
            for f in sorted(os.listdir(config_dir)):
                if f.endswith(".yml") or f.endswith(".yaml"):
                    config_paths.append(os.path.join(config_dir, f))

        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        boot = BinaryBootstrapper(store=store)
        bare_binaries = [
            {"name": b["binary_name"], "version": b.get("version", "?")}
            for b in boot.list_binaries_with_versions()
        ]
        known_versions = boot.get_known_versions()

        elapsed = _daemon_state.get("tick_metrics", {})
        qg = _daemon_state.get("quality_gate", {})
        if not qg:
            qg = {"overall": "not_run", "passed_count": 0, "total_count": 0}
        return {
            "version": __version__,
            "uptime_ticks": elapsed.get("total_ticks", 0),
            "todos_total": todo_count,
            "queue_depths": queue_depths,
            "tick_metrics": elapsed,
            "config_dir": config_dir,
            "config_files": config_paths,
            "filestore_root": store.root_path,
            "filestore_binaries": bare_binaries,
            "binary_versions": known_versions,
            "db_engine": str(getattr(app.state, "_db_engine", None)),
            "db_url": str(getattr(getattr(app.state, "_db_engine", None), "url", "sqlite")),
            "quality_gate": qg,
        }

    @app.get("/api/deployments")
    async def api_deployments() -> list[dict[str, Any]]:
        return []

    @app.post("/admin/log-level")
    async def admin_log_level(req: LogLevelRequest) -> dict[str, str]:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level_upper = req.level.upper()
        if level_upper not in valid_levels:
            raise HTTPException(status_code=422, detail=f"Invalid log level: {req.level}")
        logging.getLogger().setLevel(level_upper)
        return {"status": "ok", "level": req.level}
