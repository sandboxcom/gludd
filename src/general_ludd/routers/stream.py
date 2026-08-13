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
import os
import re
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.stream import (
    _SAFE_BINARY_RE,
    SUPPORTED_PROCESSOR_TOOLS,
    RoleCloner,
    _parse_processor_args,
)

logger = logging.getLogger(__name__)

# Playbook env allowlist — subprocess children must never inherit daemon
# secrets (ZAI_API_KEY, AWS_*, DATABASE_URL, GLUDD_PSK, …).  Mirrors
# AnsibleCoreRunner._PLAYBOOK_ENV_ALLOWLIST.
_STREAM_PLAYBOOK_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "GLUDD_PLAYBOOK_TIMEOUT",
        "ANSIBLE_CONFIG",
        "ANSIBLE_ROLES_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_LIBRARY",
        "ANSIBLE_MODULE_UTILS",
        "ANSIBLE_FILTER_PLUGINS",
        "ANSIBLE_CALLBACK_PLUGINS",
        "ANSIBLE_LOOKUP_PLUGINS",
        "ANSIBLE_STRATEGY_PLUGINS",
        "ANSIBLE_CACHE_PLUGINS",
        "ANSIBLE_CONNECTION_PLUGINS",
        "ANSIBLE_VARS_PLUGINS",
        "ANSIBLE_HOST_KEY_CHECKING",
        "ANSIBLE_STDOUT_CALLBACK",
        "ANSIBLE_RETRY_FILES_ENABLED",
        "ANSIBLE_FORCE_COLOR",
        "ANSIBLE_NOCOLOR",
        "ANSIBLE_VERBOSITY",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "VIRTUAL_ENV",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "TERM",
        "COLUMNS",
        "LINES",
    }
)

# Role names are directory names under collection_root/roles/ — restrict to a
# simple identifier so a caller cannot smuggle path-traversal segments (e.g.
# "../../../etc") through into a filesystem join / shutil.copytree source.
_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StreamDispatchRequest(BaseModel):
    """Body for ``POST /admin/stream/dispatch``."""

    role: str = Field(min_length=1, max_length=128)
    source_role_invocation: dict[str, object] = Field(default_factory=dict)
    extra_vars: dict[str, object] = Field(default_factory=dict)
    processor: dict[str, object] | None = None
    wait_for_completion: bool = False
    priority: int = Field(default=5, ge=0, le=20)
    work_type: str = Field(default="stream_chunk")

    @field_validator("role")
    @classmethod
    def _validate_role_name(cls, v: str) -> str:
        if not _ROLE_NAME_RE.match(v):
            raise ValueError("role must be a simple identifier ([A-Za-z0-9_-]+)")
        return v

    @field_validator("role")
    @classmethod
    def _validate_role_no_traversal(cls, v: str) -> str:
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("role must not contain path traversal characters")
        return v


def _get_role_cloner(app: FastAPI) -> RoleCloner | None:
    cloner = getattr(app.state, "_role_cloner", None)
    return cloner if isinstance(cloner, RoleCloner) else None


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _scrub_child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _STREAM_PLAYBOOK_ENV_ALLOWLIST}


# Indirection seam for tests: tests monkeypatch this to stub the subprocess so
# the wait_for_completion path can be exercised without ansible on PATH.
def _run_subprocess(args: list[str], cwd: str, timeout: float) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_scrub_child_env(),
    )


def _kill_and_reap(
    proc: subprocess.Popen[bytes],
    timeout: float = 5.0,
) -> None:
    """Kill a subprocess, drain its pipes, and reap it without leaking FDs."""
    proc.kill()
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.wait(timeout=timeout)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.post(
        "/admin/stream/dispatch",
        summary="Dispatch a stream chunk to a cloned role instance",
        description=(
            "Clones the requested role with injected stream vars and either "
            "enqueues it for execution via the existing job queue or runs it "
            "synchronously. PSK-authenticated."
        ),
    )
    async def admin_stream_dispatch(req: StreamDispatchRequest) -> dict[str, object]:
        cloner = _get_role_cloner(app)
        if cloner is None:
            raise HTTPException(
                status_code=503,
                detail="RoleCloner not configured (collection root unavailable)",
            )

        # Validate role exists in the collection. `req.role` is already
        # restricted to a simple identifier by the pydantic validator above,
        # but resolve + confine defensively in case this handler is ever
        # invoked with a pre-validated / constructed request object.
        roles_root = (cloner.collection_root / "roles").resolve()
        role_dir = (roles_root / req.role).resolve()
        try:
            role_dir.relative_to(roles_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Role {req.role!r} resolves outside the roles directory",
            ) from exc
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
                        f"processor.tool {tool!r} not supported; expected one of {sorted(SUPPORTED_PROCESSOR_TOOLS)}"
                    ),
                )
            if tool in {"whisper.cpp", "ffmpeg"}:
                binary = req.processor.get("binary", tool)
                if not _SAFE_BINARY_RE.match(str(binary)):
                    raise HTTPException(
                        status_code=422,
                        detail=(f"processor.binary {binary!r} contains unsafe characters; expected [a-zA-Z0-9_./-]+"),
                    )
                extra_args = req.processor.get("args", "")
                try:
                    _parse_processor_args(str(extra_args))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

        task_id = f"STREAM-{uuid.uuid4().hex[:12].upper()}"

        # Materialize the clone + optional processor shim.
        clone_path = cloner.clone(req.role, overrides=dict(req.extra_vars))
        if req.processor is not None:
            cloner.materialize_processor(clone_path, processor=dict(req.processor))

        if not req.wait_for_completion:
            return await _enqueue_clone(app, task_id, req, clone_path)
        return await _run_clone_sync(task_id, req, clone_path)

    async def _enqueue_clone(
        app: FastAPI,
        task_id: str,
        req: StreamDispatchRequest,
        clone_path: Path,
    ) -> dict[str, object]:
        """Enqueue the clone via TodoRepository (mirrors /api/todos)."""
        factory = _get_session_factory(app)
        if factory is None:
            # Degraded (no DB) — still report the clone so a caller can run it.
            logger.warning(
                "stream/dispatch: no session_factory; returning queued clone without DB persistence (task_id=%s)",
                task_id,
            )
            return {
                "task_id": task_id,
                "status": "queued",
                "clone_path": str(clone_path),
            }
        from general_ludd.db.repository import TodoRepository

        todo_data: dict[str, object] = {
            "todo_id": task_id,
            "title": f"stream_chunk:{req.role}:{task_id}",
            "description": (f"Server-side stream dispatch for role {req.role!r} (clone_path={clone_path})"),
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
        clone_path: Path,
    ) -> dict[str, object]:
        """Run ansible-playbook run-clone.yml synchronously in the clone dir."""
        timeout = 60.0
        if req.processor is not None:
            raw_timeout = req.processor.get("timeout_seconds", 60)
            try:
                timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else 60.0
            except (TypeError, ValueError):
                timeout = 60.0

        args = ["ansible-playbook", "run-clone.yml"]
        try:
            proc = await asyncio.to_thread(_run_subprocess, args, str(clone_path), timeout)
            try:
                stdout, stderr = await asyncio.to_thread(proc.communicate, timeout=timeout)
            except Exception as exc:
                await asyncio.to_thread(_kill_and_reap, proc)
                raise HTTPException(
                    status_code=504,
                    detail=f"stream clone execution timed out after {timeout}s: {exc}",
                ) from exc
            if proc.returncode != 0:
                logger.warning(
                    "stream clone %s exited rc=%s stderr=%s",
                    task_id,
                    proc.returncode,
                    stderr.decode(errors="replace")[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"stream clone execution failed (rc={proc.returncode}): {stderr.decode(errors='replace')[:300]}"
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
