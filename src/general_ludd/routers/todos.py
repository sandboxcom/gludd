"""Register bounded todo and daemon-status HTTP routes."""

from __future__ import annotations

import collections
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd import __version__
from general_ludd.db.models import TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore
from general_ludd.quality.preflight import run_preflight
from general_ludd.routers.web_search import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


def _deserialize_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return parsed
    return []


class AddTodoRequest(BaseModel):
    """Validate an immediate todo creation request."""

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4096)
    queue: str = Field(default="core", pattern=r"^[a-z0-9_\-]+$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    work_type: str = Field(default="code", pattern=r"^[a-z_]+$")
    project_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    definition_of_done: str = Field(default="", max_length=4096)


class AddScheduledTodoRequest(BaseModel):
    """Validate a one-shot or recurring todo creation request."""

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4096)
    queue: str = Field(default="core", pattern=r"^[a-z0-9_\-]+$")
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")
    work_type: str = Field(default="code", pattern=r"^[a-z_]+$")
    project_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    definition_of_done: str = Field(default="", max_length=4096)
    # One-shot: fire once at this UTC datetime.
    scheduled_at: datetime | None = None
    # Recurring: 5-field cron expression (e.g. "0 9 * * 1-5").
    cron: str | None = None
    schedule_timezone: str = "UTC"
    max_runs: int | None = None


class LogLevelRequest(BaseModel):
    """Validate a runtime log-level update request."""

    level: str


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _validate_project_id(app: FastAPI, project_id: str | None) -> None:
    """Reject unknown project_id in multi-project mode (mirrors CREATE).

    TG-1: read/update endpoints previously returned 404 ("not found") or an empty
    result for an unknown project_id, while CREATE returns 422. This unifies the
    contract: when a ProjectManager exists AND has >=1 active project, a non-null
    but unknown project_id is 422 ("unknown"). A null project_id is always allowed
    (global/unscoped). When no projects are active the field is unconstrained
    (single-project / no-PM back-compat) and this is a no-op.
    """
    pm = getattr(app.state, "_project_manager", None)
    if pm is None:
        return
    active_ids = {p.project_id for p in pm.list_active()}
    if not active_ids:
        return
    if project_id is not None and project_id not in active_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown project_id: {project_id}",
        )


def _todo_to_dict(todo: TodoModel) -> dict[str, object]:
    return {
        "todo_id": todo.todo_id,
        "title": todo.title,
        "description": todo.description,
        "queue": todo.queue,
        "priority": todo.priority,
        "work_type": todo.work_type,
        "status": todo.status,
        "project_id": todo.project_id,
        "version": todo.version,
        "created_at": str(todo.created_at) if todo.created_at else None,
        "acceptance_criteria": _deserialize_json_list(todo.acceptance_criteria),
        "definition_of_done": todo.definition_of_done if todo.definition_of_done else "",
    }


def _todo_to_dict_scheduled(todo: TodoModel) -> dict[str, object]:
    """Serialize a todo including all scheduling fields."""
    base = _todo_to_dict(todo)
    base.update(
        {
            "scheduled_at": str(todo.scheduled_at) if getattr(todo, "scheduled_at", None) else None,
            "cron": getattr(todo, "cron", None),
            "schedule_timezone": getattr(todo, "schedule_timezone", "UTC"),
            "next_run_at": str(todo.next_run_at) if getattr(todo, "next_run_at", None) else None,
            "last_run_at": str(todo.last_run_at) if getattr(todo, "last_run_at", None) else None,
            "run_count": getattr(todo, "run_count", 0),
            "max_runs": getattr(todo, "max_runs", None),
            "schedule_paused": getattr(todo, "schedule_paused", False),
        }
    )
    return base


_PRIORITY_MAP: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Memory-leak guard (follow-up to P3 470253a): the degraded-mode in-memory todo
# fallback (`_daemon_state["todos"]`) is unbounded — without a session factory
# every POST /api/todos appends forever, so a long-lived degraded daemon grows
# this list without limit. Bound it to the most-recent N via a deque(maxlen).
# All consumers iterate or list()-convert it (never index/slice the raw object
# or JSON-serialize it directly), so a deque is a drop-in: FIFO eviction silently
# drops the oldest entries once the cap is hit.
_MAX_INMEMORY_TODOS = 1000

_TODO_MAX_REQUESTS = 30
_TODO_WINDOW_SECONDS = 60.0


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Attach todo routes and their application-owned mutable resources."""
    rate_limiter = getattr(app.state, "_todo_rate_limiter", None)
    if rate_limiter is None:
        rate_limiter = SlidingWindowRateLimiter(
            max_requests=_TODO_MAX_REQUESTS,
            window_seconds=_TODO_WINDOW_SECONDS,
        )
        app.state._todo_rate_limiter = rate_limiter

    # Keep the factory's plain list untouched until degraded-mode writes need
    # it. `_todos()` below performs the bounded conversion lazily on first use.
    def _todos() -> collections.deque[dict[str, object]]:
        td = _daemon_state.get("todos")
        if not isinstance(td, collections.deque):
            td = collections.deque(td if isinstance(td, list) else [], maxlen=_MAX_INMEMORY_TODOS)
            _daemon_state["todos"] = td
        return cast(collections.deque[dict[str, object]], td)

    @app.post("/admin/preflight")
    async def admin_run_preflight() -> dict[str, object]:
        result = run_preflight()
        _daemon_state["quality_gate"] = result
        return result

    @app.post("/api/todos", status_code=201)
    async def api_add_todo(req: AddTodoRequest) -> dict[str, object]:
        if not rate_limiter.allow():
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — max 30 todos per minute",
            )
        # Cross-project create guard: a caller could previously pass ANY
        # project_id (including one belonging to a different tenant). When a
        # ProjectManager exists AND has at least one active project, a non-null
        # project_id MUST name one of them; an unknown id is rejected 422.
        # A null/missing project_id is always allowed (global/unscoped todo).
        # When no projects are registered the field stays unconstrained
        # (back-compat: single-project / no-project deployments and the many
        # tests that create todos with arbitrary or null project_ids keep
        # working).
        pm = getattr(app.state, "_project_manager", None)
        if pm is not None:
            active_ids = {p.project_id for p in pm.list_active()}
            if active_ids and req.project_id is not None and req.project_id not in active_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown project_id: {req.project_id}",
                )
        factory = _get_session_factory(app)
        todo_id = f"TODO-{uuid.uuid4().hex[:8].upper()}"
        todo: dict[str, object] = {
            "todo_id": todo_id,
            "title": req.title,
            "description": req.description,
            "queue": req.queue,
            "priority": _PRIORITY_MAP.get(req.priority, 1),
            "work_type": req.work_type,
            "status": "queued",
            "project_id": req.project_id,
            "acceptance_criteria": json.dumps(req.acceptance_criteria),
            "definition_of_done": req.definition_of_done,
        }
        if factory is not None:
            try:
                async with factory() as session:
                    repo = TodoRepository(session)
                    result = await repo.create(todo_data=todo)
                    await session.commit()
                    return _todo_to_dict(result)
            except (OSError, SQLAlchemyError) as exc:
                logger.warning("Todo database unavailable; using bounded fallback: %s", exc)
        _todos().append(todo)
        return todo

    @app.get("/api/todos")
    async def api_list_todos(
        queue: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        _limit = max(1, min(limit, 500))
        _offset = max(0, offset)
        # Cross-tenant isolation: when no project_id is supplied in multi-project
        # mode, refuse to leak all tenants' todos.  Single-project / no-PM
        # deployments fall through to the unscoped path for back-compat.
        _pm = getattr(app.state, "_project_manager", None)
        _active_ids: set[str] = {p.project_id for p in _pm.list_active()} if _pm is not None else set()
        if project_id is None and _active_ids:
            return []
        factory = _get_session_factory(app)
        if factory is not None:
            try:
                async with factory() as session:
                    repo = (
                        TodoRepository.scoped(session, project_id)
                        if project_id is not None
                        else TodoRepository(session)
                    )
                    todos = await repo.list_all(
                        queue=queue,
                        status=status,
                        limit=_limit,
                        offset=_offset,
                    )
                    return [_todo_to_dict(t) for t in todos][:limit]
            except (OSError, SQLAlchemyError) as exc:
                logger.warning("Todo database unavailable; reading bounded fallback: %s", exc)
        results = list(_todos())
        if queue is not None:
            results = [t for t in results if t.get("queue") == queue]
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        if project_id is not None:
            results = [t for t in results if t.get("project_id") == project_id]
        return results[_offset : _offset + _limit]

    @app.post("/api/todos/scheduled", status_code=201)
    async def api_create_scheduled_todo(req: AddScheduledTodoRequest) -> dict[str, object]:
        """Create a scheduled (one-shot or cron) todo in SCHEDULED status.

        ``scheduled_at`` sets the one-shot fire time; ``cron`` makes it
        recurring. At least one of the two must be supplied.  ``next_run_at``
        is computed automatically from ``cron`` when provided.
        """
        if not rate_limiter.allow():
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — max 30 todos per minute",
            )
        if req.scheduled_at is None and req.cron is None:
            raise HTTPException(
                status_code=422,
                detail="At least one of scheduled_at or cron must be provided",
            )
        if req.cron is not None and len(req.cron.split()) != 5:
            raise HTTPException(
                status_code=422,
                detail="cron must be a 5-field expression (min hour dom month dow)",
            )
        pm = getattr(app.state, "_project_manager", None)
        if pm is not None:
            active_ids = {p.project_id for p in pm.list_active()}
            if active_ids and req.project_id is not None and req.project_id not in active_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown project_id: {req.project_id}",
                )
        # Compute initial next_run_at from the cron expression.
        initial_next_run_at: datetime | None = None
        if req.cron is not None:
            try:
                from zoneinfo import ZoneInfo

                from croniter import croniter

                _zone = ZoneInfo(req.schedule_timezone)
                _start = (req.scheduled_at or datetime.now(UTC)).astimezone(_zone)
                _it = croniter(req.cron, _start)
                _nxt: datetime = _it.get_next(datetime)
                if _nxt.tzinfo is None:
                    _nxt = _nxt.replace(tzinfo=_zone)
                initial_next_run_at = _nxt.astimezone(UTC)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid cron expression or timezone: {exc}",
                ) from exc

        factory = _get_session_factory(app)
        todo_id = f"TODO-{uuid.uuid4().hex[:8].upper()}"
        todo_data: dict[str, object] = {
            "todo_id": todo_id,
            "title": req.title,
            "description": req.description,
            "queue": req.queue,
            "priority": _PRIORITY_MAP.get(req.priority, 1),
            "work_type": req.work_type,
            "status": "scheduled",
            "project_id": req.project_id,
            "scheduled_at": req.scheduled_at,
            "cron": req.cron,
            "schedule_timezone": req.schedule_timezone,
            "max_runs": req.max_runs,
            "schedule_paused": False,
            "run_count": 0,
            "next_run_at": initial_next_run_at,
            "acceptance_criteria": json.dumps(req.acceptance_criteria),
            "definition_of_done": req.definition_of_done,
        }
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository(session)
                result = await repo.create(todo_data=todo_data)
                await session.commit()
                return _todo_to_dict_scheduled(result)
        raise HTTPException(status_code=503, detail="No database available")

    @app.get("/api/todos/scheduled")
    async def api_list_scheduled_todos(
        project_id: str | None = None,
        include_paused: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """List todos in SCHEDULED status (both one-shot and cron templates)."""
        _limit = max(1, min(limit, 500))
        _offset = max(0, offset)
        # Cross-tenant isolation: when no project_id is supplied in multi-project
        # mode, refuse to leak all tenants' scheduled todos.
        _pm = getattr(app.state, "_project_manager", None)
        _active_ids: set[str] = {p.project_id for p in _pm.list_active()} if _pm is not None else set()
        if project_id is None and _active_ids:
            return []
        # TG-1: a non-None but UNKNOWN project_id must 422 (not silently scope to
        # an empty list), matching CREATE and the other read/update endpoints.
        _validate_project_id(app, project_id)
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository.scoped(session, project_id) if project_id is not None else TodoRepository(session)
                todos = await repo.list_all(
                    status="scheduled",
                    limit=_limit,
                    offset=_offset,
                    # DEFECT 3: push paused-filtering into SQL so pagination is
                    # correct (None = no filter, backwards-compatible default).
                    schedule_paused=None if include_paused else False,
                )
                return [_todo_to_dict_scheduled(t) for t in todos]
        return []

    @app.post("/api/todos/{todo_id}/schedule/pause")
    async def api_pause_schedule(todo_id: str, project_id: str) -> dict[str, object]:
        """Pause a SCHEDULED todo's schedule. Skips future fires until resumed."""
        _validate_project_id(app, project_id)
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = TodoRepository.scoped(session, project_id)
            todo = await repo.get_by_id(todo_id)
            if todo is None:
                raise HTTPException(status_code=404, detail="Todo not found")
            if todo.status != "scheduled":
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot pause a todo in status {todo.status!r}; must be 'scheduled'",
                )
            await repo.update(todo_id, {"schedule_paused": True}, expected_version=todo.version)
            await session.commit()
        return {"todo_id": todo_id, "schedule_paused": True, "status": "ok"}

    @app.post("/api/todos/{todo_id}/schedule/resume")
    async def api_resume_schedule(todo_id: str, project_id: str) -> dict[str, object]:
        """Resume a paused SCHEDULED todo's schedule."""
        _validate_project_id(app, project_id)
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = TodoRepository.scoped(session, project_id)
            todo = await repo.get_by_id(todo_id)
            if todo is None:
                raise HTTPException(status_code=404, detail="Todo not found")
            if todo.status != "scheduled":
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot resume a todo in status {todo.status!r}; must be 'scheduled'",
                )
            await repo.update(todo_id, {"schedule_paused": False}, expected_version=todo.version)
            await session.commit()
        return {"todo_id": todo_id, "schedule_paused": False, "status": "ok"}

    @app.get("/api/todos/{todo_id}")
    async def api_get_todo(todo_id: str, project_id: str | None = None) -> dict[str, object]:
        _validate_project_id(app, project_id)
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository.scoped(session, project_id) if project_id is not None else TodoRepository(session)
                todo = await repo.get_by_id(todo_id)
                if todo is not None:
                    return _todo_to_dict(todo)
                raise HTTPException(status_code=404, detail="Todo not found")
        for _row in _todos():
            if str(_row.get("todo_id", "")) == todo_id and (project_id is None or _row.get("project_id") == project_id):
                return dict(_row)
        raise HTTPException(status_code=404, detail="Todo not found")

    @app.put("/api/todos/{todo_id}")
    async def api_update_todo(todo_id: str, req: AddTodoRequest, project_id: str | None = None) -> dict[str, object]:
        _validate_project_id(app, project_id)
        factory = _get_session_factory(app)
        updates: dict[str, object] = {
            "title": req.title,
            "description": req.description,
            "acceptance_criteria": json.dumps(req.acceptance_criteria),
            "definition_of_done": req.definition_of_done,
        }
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository.scoped(session, project_id) if project_id is not None else TodoRepository(session)
                todo = await repo.get_by_id(todo_id)
                if todo is None:
                    raise HTTPException(status_code=404, detail="Todo not found")
                await repo.update(todo_id, updates, expected_version=todo.version)
                await session.commit()
                updated = await repo.get_by_id(todo_id)
                if updated is not None:
                    return _todo_to_dict(updated)
        for i, _row in enumerate(_todos()):
            if str(_row.get("todo_id", "")) == todo_id and (project_id is None or _row.get("project_id") == project_id):
                _todos()[i] = {**_row, **updates}
                return dict(_todos()[i])
        raise HTTPException(status_code=404, detail="Todo not found")

    @app.get("/admin/todos")
    async def admin_list_todos(
        status: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        # ADMIN cross-tenant listing is INTENTIONAL here: this route is NOT in
        # daemon._PUBLIC_PATHS, so it is PSK-gated (operator-only). Unlike the
        # public GET /api/todos — which returns [] when project_id is omitted in
        # multi-project mode to avoid leaking other tenants — an operator needs
        # global visibility, so an unscoped TodoRepository(session) with
        # project_id=None (full cross-tenant scan) is correct. Do NOT add the
        # multi-project isolation guard from api_list_todos to this handler.
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository(session)
                todos = await repo.list_all(status=status, project_id=project_id)
                results = [_todo_to_dict(t) for t in todos]
                return {"todos": results, "count": len(results)}
        results = list(_todos())
        if status is not None:
            results = [t for t in results if t.get("status") == status]
        if project_id is not None:
            results = [t for t in results if t.get("project_id") == project_id]
        return {"todos": results, "count": len(results)}

    @app.get(
        "/api/status",
        summary="Get daemon status, queue depths, and hardware info",
        description=(
            "Observability snapshot: version, uptime ticks, per-queue counts, "
            "tick metrics, filestore + binary versions, quality gate, hardware "
            "profile. Public GET."
        ),
    )
    async def api_status() -> dict[str, object]:
        factory = _get_session_factory(app)
        queue_depths: dict[str, int] = {}
        todo_count = 0
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository(session)
                # P12/Defect-1: use aggregate COUNT queries (status_summary)
                # instead of list_all() so queue-depth counts are never
                # silently truncated by the _DEFAULT_LIST_LIMIT cap.
                summary = await repo.status_summary()
                todo_count = summary["total"]
                for q, c in summary["by_queue"].items():
                    queue_depths[q or "unknown"] = queue_depths.get(q or "unknown", 0) + c
        else:
            for todo in _todos():
                q = cast(str, todo.get("queue") or "unknown")
                queue_depths[q] = queue_depths.get(q, 0) + 1
                todo_count += 1

        bare_binaries: list[dict[str, str]] = []
        known_versions: dict[str, str] = {}
        filestore_available = False
        try:
            store = FileStore()
            filestore_available = bool(store.root_path) and os.path.isdir(store.root_path)
            boot = BinaryBootstrapper(store=store)
            bare_binaries = [
                {"name": b["binary_name"], "version": b.get("version", "?")} for b in boot.list_binaries_with_versions()
            ]
            known_versions = boot.get_known_versions()
        except Exception:
            pass

        config_dir = getattr(app.state, "_config_dir", None)
        config_file_count = 0
        if config_dir and os.path.isdir(config_dir):
            config_file_count = sum(1 for f in os.listdir(config_dir) if f.endswith(".yml") or f.endswith(".yaml"))

        elapsed = cast(dict[str, object], _daemon_state.get("tick_metrics", {}))
        qg = _daemon_state.get("quality_gate", {})
        if not qg:
            qg = {"overall": "not_run", "passed_count": 0, "total_count": 0}
        return {
            "version": __version__,
            "uptime_ticks": elapsed.get("total_ticks", 0),
            "todos_total": todo_count,
            "queue_depths": queue_depths,
            "tick_metrics": elapsed,
            "filestore_available": filestore_available,
            "filestore_binaries": bare_binaries,
            "binary_versions": known_versions,
            "quality_gate": qg,
            "hardware": (getattr(app.state, "_hardware", None) and app.state._hardware.to_dict()) or {},
            "config_file_count": config_file_count,
        }

    @app.post("/admin/log-level")
    async def admin_log_level(req: LogLevelRequest) -> dict[str, str]:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level_upper = req.level.upper()
        if level_upper not in valid_levels:
            raise HTTPException(status_code=422, detail=f"Invalid log level: {req.level}")
        logging.getLogger().setLevel(level_upper)
        return {"status": "ok", "level": req.level}
