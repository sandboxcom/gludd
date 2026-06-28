"""POST /admin/stream/dispatch — server-side stream dispatch.

Clones a role + its variables + state into a new role instance with stream
data injected, then either:

  * enqueues the cloned role for execution via the existing
    :class:`general_ludd.db.repository.TodoRepository` (the event loop claims
    + dispatches it like any other todo); or
  * runs the clone synchronously by invoking ``ansible-playbook run-clone.yml``
    in the clone directory (``wait_for_completion=True``).

PSK auth is applied by the daemon middleware — this path is NOT in
``_PUBLIC_PATHS``, exactly like the other ``/admin/*`` routes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from general_ludd.stream import SUPPORTED_PROCESSOR_TOOLS, RoleCloner

logger = logging.getLogger(__name__)


class StreamDispatchRequest(BaseModel):
    """Body for ``POST /admin/stream/dispatch``."""

    role: str = Field(min_length=1, max_length=128)
    source_role_invocation: dict[str, Any] = Field(default_factory=dict)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    processor: dict[str, Any] | None = None
    wait_for_completion: bool = False
    priority: int = Field(default=5, ge=0, le=20)
    work_type: str = Field(default="stream_chunk")


def _get_role_cloner(app: FastAPI) -> RoleCloner | None:
    cloner = getattr(app.state, "_role_cloner", None)
    return cloner if isinstance(cloner, RoleCloner) else None


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


# Indirection seam for tests: tests monkeypatch this to stub the subprocess so
# the wait_for_completion path can be exercised without ansible on PATH.
def _run_subprocess(args: list[str], cwd: str, timeout: float) -> Any:
    import subprocess

    return subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.post(
        "/admin/stream/dispatch",
        summary="Dispatch a stream chunk to a cloned role instance",
        description=(
            "Clones the requested role with injected stream vars and either "
            "enqueues it for execution via the existing job queue or runs it "
            "synchronously. PSK-authenticated."
        ),
    )
    async def admin_stream_dispatch(req: StreamDispatchRequest) -> dict[str, Any]:
        cloner = _get_role_cloner(app)
        if cloner is None:
            raise HTTPException(
                status_code=503,
                detail="RoleCloner not configured (collection root unavailable)",
            )

        # Validate role exists in the collection.
        role_dir = cloner.collection_root / "roles" / req.role
        if not role_dir.is_dir():
            raise HTTPException(
                status_code=422,
                detail=f"Role {req.role!r} not found in collection",
            )

        # Validate processor tool kind (when supplied).
        if req.processor is not None:
            tool = req.processor.get("tool")
            if tool not in SUPPORTED_PROCESSOR_TOOLS:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"processor.tool {tool!r} not supported; expected one of "
                        f"{sorted(SUPPORTED_PROCESSOR_TOOLS)}"
                    ),
                )

        task_id = f"STREAM-{uuid.uuid4().hex[:12].upper()}"

        # Materialize the clone + optional processor shim.
        clone_path = cloner.clone(req.role, overrides=dict(req.extra_vars))
        if req.processor is not None:
            cloner.materialize_processor(clone_path, processor=dict(req.processor))

        if not req.wait_for_completion:
            return await _enqueue_clone(
                app, task_id, req, clone_path
            )
        return await _run_clone_sync(task_id, req, clone_path)

    async def _enqueue_clone(
        app: FastAPI,
        task_id: str,
        req: StreamDispatchRequest,
        clone_path: Any,
    ) -> dict[str, Any]:
        """Enqueue the clone via TodoRepository (mirrors /api/todos)."""
        factory = _get_session_factory(app)
        if factory is None:
            # Degraded (no DB) — still report the clone so a caller can run it.
            logger.warning(
                "stream/dispatch: no session_factory; returning queued clone "
                "without DB persistence (task_id=%s)",
                task_id,
            )
            return {
                "task_id": task_id,
                "status": "queued",
                "clone_path": str(clone_path),
            }
        from general_ludd.db.repository import TodoRepository

        todo_data: dict[str, Any] = {
            "todo_id": task_id,
            "title": f"stream_chunk:{req.role}:{task_id}",
            "description": (
                f"Server-side stream dispatch for role {req.role!r} "
                f"(clone_path={clone_path})"
            ),
            "queue": "core",
            "priority": req.priority,
            "work_type": req.work_type,
            "status": "queued",
            "project_id": None,
        }
        async with factory() as session:
            repo = TodoRepository(session)
            await repo.create(todo_data=todo_data)
            await session.commit()
        return {
            "task_id": task_id,
            "status": "queued",
            "clone_path": str(clone_path),
        }

    async def _run_clone_sync(
        task_id: str,
        req: StreamDispatchRequest,
        clone_path: Any,
    ) -> dict[str, Any]:
        """Run ansible-playbook run-clone.yml synchronously in the clone dir."""
        timeout = 60.0
        if req.processor is not None:
            try:
                timeout = float(req.processor.get("timeout_seconds", 60))
            except (TypeError, ValueError):
                timeout = 60.0

        args = ["ansible-playbook", "run-clone.yml"]
        try:
            proc = await asyncio.to_thread(
                _run_subprocess, args, str(clone_path), timeout
            )
            try:
                stdout, stderr = await asyncio.to_thread(proc.communicate, timeout=timeout)
            except Exception as exc:
                proc.kill()
                raise HTTPException(
                    status_code=504,
                    detail=f"stream clone execution timed out after {timeout}s: {exc}",
                ) from exc
            if proc.returncode != 0:
                logger.warning(
                    "stream clone %s exited rc=%s stderr=%s",
                    task_id, proc.returncode, stderr.decode(errors="replace")[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"stream clone execution failed (rc={proc.returncode}): "
                        f"{stderr.decode(errors='replace')[:300]}"
                    ),
                )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"ansible-playbook not available: {exc}",
            ) from exc

        return {
            "task_id": task_id,
            "status": "completed",
            "result": {
                "returncode": proc.returncode,
                "stdout": stdout.decode(errors="replace")[:1000],
                "stderr": stderr.decode(errors="replace")[:500],
            },
            "clone_path": str(clone_path),
        }
