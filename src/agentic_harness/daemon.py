"""Unified daemon — FastAPI app with embedded event loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_daemon_state: dict[str, Any] = {
    "todos": [],
    "tick_metrics": {},
}


class AddTodoRequest(BaseModel):
    title: str
    description: str = ""
    queue: str = "core"
    priority: str = "medium"
    work_type: str = "code"


class LogLevelRequest(BaseModel):
    level: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    tick_interval = app.state.tick_interval
    event_loop = None
    task = None

    try:
        from agentic_harness.ansible.runner import AnsibleRunnerAdapter
        from agentic_harness.event_loop.loop import EventLoop

        runner = AnsibleRunnerAdapter()
        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
    except Exception as exc:
        logger.warning("Could not start event loop: %s", exc)

    yield

    if event_loop is not None:
        event_loop.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_daemon_app(
    tick_interval: float = 1.0,
    log_level: str = "info",
) -> FastAPI:
    app = FastAPI(title="Hottentot Agent", version="0.1.0", lifespan=_lifespan)
    app.state.tick_interval = tick_interval
    app.state.event_loop = None
    app.state.log_level = log_level

    if log_level == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

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
        }
        _daemon_state["todos"].append(todo)
        return todo

    @app.get("/api/todos")
    async def api_list_todos(queue: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        results = list(_daemon_state["todos"])
        if queue is not None:
            results = [t for t in results if t.get("queue") == queue]
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        return results

    @app.get("/api/todos/{todo_id}")
    async def api_get_todo(todo_id: str) -> dict[str, Any]:
        for todo in _daemon_state["todos"]:
            if str(todo.get("todo_id", "")) == todo_id:
                return dict(todo)
        raise HTTPException(status_code=404, detail="Todo not found")

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        queue_depths: dict[str, int] = {}
        for todo in _daemon_state["todos"]:
            q = todo.get("queue", "unknown")
            queue_depths[q] = queue_depths.get(q, 0) + 1
        return {
            "queue_depths": queue_depths,
            "tick_metrics": _daemon_state["tick_metrics"],
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

    return app
