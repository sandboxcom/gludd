"""Account backup and deletion logic.

Public API:
    backup_account(user_id, ...) -> Path
        Export all user-scoped data to a JSON file.
    delete_account(user_id, ...) -> dict
        Delete all user-scoped data and return a summary.
    get_deletion_policy(service) -> str
        Look up a cloud service's data-retention text.

A "user" maps to existing DB columns that already carry per-user attribution:
    * todos       -> TodoModel.created_by / TodoModel.assigned_agent
    * returns     -> TaskReturnModel joined via TodoModel.todo_id
    * memory      -> MemoryRecordModel.agent_id
    * settings    -> VariableNamespaceModel.namespace == f"user:{user_id}" or user_id
                     (joined to VariableValueModel)

The backup is a single JSON file: deterministic shape, easy to import elsewhere.
``delete_account`` is irreversible — the CLI/HTTP layer gates it behind an
explicit ``confirm=True`` flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_policy_text,
)
from general_ludd.db.models import (
    MemoryRecordModel,
    TaskReturnModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_filename_segment(text: str, max_len: int = 64) -> str:
    """Sanitize ``text`` for inclusion in a filename (alnum + dash/underscore)."""
    cleaned = _SAFE_RE.sub("_", text).strip("_")
    return (cleaned or "anonymous")[:max_len]


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    """Build a session factory from the project's default DB URL.

    Lazy import to avoid import cycle when ``db.session`` bootstraps models.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from general_ludd.db.session import (
        create_async_session_factory,
        get_default_db_url,
        run_wal_pragmas,
    )

    url = get_default_db_url()
    engine = create_async_engine(url)
    run_wal_pragmas(engine)
    return create_async_session_factory(engine)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


async def _collect_todos(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    stmt = select(TodoModel).where(
        or_(
            TodoModel.created_by == user_id,
            TodoModel.assigned_agent == user_id,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "todo_id": r.todo_id,
            "title": r.title,
            "description": r.description,
            "status": r.status,
            "queue": r.queue,
            "priority": r.priority,
            "work_type": r.work_type,
            "tags": r.tags,
            "created_by": r.created_by,
            "assigned_agent": r.assigned_agent,
            "project_id": r.project_id,
            "created_at": str(r.created_at) if r.created_at else None,
            "updated_at": str(r.updated_at) if r.updated_at else None,
            "completed_at": str(r.completed_at) if getattr(r, "completed_at", None) else None,
        }
        for r in rows
    ]


async def _collect_returns(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    # Join TaskReturnModel to TodoModel on todo_id so we can scope by user.
    stmt = (
        select(TaskReturnModel)
        .outerjoin(TodoModel, TaskReturnModel.todo_id == TodoModel.todo_id)
        .where(
            or_(
                TodoModel.created_by == user_id,
                TodoModel.assigned_agent == user_id,
            )
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "return_id": r.return_id,
            "todo_id": r.todo_id,
            "job_id": r.job_id,
            "playbook": r.playbook,
            "queue": r.queue,
            "status": r.status,
            "exit_code": r.exit_code,
            "result_summary": r.result_summary,
            "created_at": str(r.created_at) if r.created_at else None,
            "updated_at": str(r.updated_at) if r.updated_at else None,
        }
        for r in rows
    ]


async def _collect_memory(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    stmt = select(MemoryRecordModel).where(MemoryRecordModel.agent_id == user_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "key": r.key,
            "value": r.value,
            "namespace": r.namespace,
            "ttl_seconds": r.ttl_seconds,
            "created_at": str(r.created_at) if r.created_at else None,
            "updated_at": str(r.updated_at) if r.updated_at else None,
        }
        for r in rows
    ]


async def _collect_settings(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    # Settings = VariableValueModel rows under a namespace that is either the
    # raw user_id or a "user:<user_id>" prefix.
    ns_aliases = (user_id, f"user:{user_id}")
    stmt = (
        select(VariableValueModel, VariableNamespaceModel)
        .join(
            VariableNamespaceModel,
            VariableValueModel.namespace_id == VariableNamespaceModel.id,
            isouter=True,
        )
        .where(VariableNamespaceModel.namespace.in_(ns_aliases))
    )
    out: list[dict[str, Any]] = []
    for value, ns in (await session.execute(stmt)).all():
        out.append(
            {
                "namespace": ns.namespace if ns is not None else None,
                "key": value.key,
                "value": value.value,
                "value_type": value.value_type,
                "created_at": str(value.created_at) if value.created_at else None,
                "updated_at": str(value.updated_at) if value.updated_at else None,
            }
        )
    return out


async def _export_user_data(
    session_factory: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    async with session_factory() as session:
        return {
            "user_id": user_id,
            "exported_at": _utcnow_iso(),
            "todos": await _collect_todos(session, user_id),
            "returns": await _collect_returns(session, user_id),
            "memory": await _collect_memory(session, user_id),
            "settings": await _collect_settings(session, user_id),
        }


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def _delete_user_data(
    session_factory: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    async with session_factory() as session:
        # Todos owned by / assigned to the user.
        todo_rows = (
            await session.execute(
                select(TodoModel).where(
                    or_(
                        TodoModel.created_by == user_id,
                        TodoModel.assigned_agent == user_id,
                    )
                )
            )
        ).scalars().all()
        todo_ids = {r.todo_id for r in todo_rows}

        # Task returns attached to those todos.
        ret_rows: list[TaskReturnModel] = []
        if todo_ids:
            ret_rows = list(
                (
                    await session.execute(
                        select(TaskReturnModel).where(
                            TaskReturnModel.todo_id.in_(todo_ids)
                        )
                    )
                ).scalars().all()
            )

        # Memory records owned by the user.
        mem_rows = list(
            (
                await session.execute(
                    select(MemoryRecordModel).where(
                        MemoryRecordModel.agent_id == user_id
                    )
                )
            ).scalars().all()
        )

        # Settings namespaces scoped to this user (cascades to values via FK
        # ondelete=CASCADE).
        ns_rows = list(
            (
                await session.execute(
                    select(VariableNamespaceModel).where(
                        VariableNamespaceModel.namespace.in_(
                            (user_id, f"user:{user_id}")
                        )
                    )
                )
            ).scalars().all()
        )

        for _ret in ret_rows:
            await session.delete(_ret)
        for _todo in todo_rows:
            await session.delete(_todo)
        for _mem in mem_rows:
            await session.delete(_mem)
        for _ns in ns_rows:
            await session.delete(_ns)

        await session.commit()

        return {
            "user_id": user_id,
            "deleted_at": _utcnow_iso(),
            "todos_deleted": len(todo_rows),
            "returns_deleted": len(ret_rows),
            "memory_deleted": len(mem_rows),
            "settings_namespaces_deleted": len(ns_rows),
        }


# ---------------------------------------------------------------------------
# Public sync API (runs the coroutine for you)
# ---------------------------------------------------------------------------


def backup_account(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    dest_dir: Path | str | None = None,
) -> Path:
    """Export all user-scoped data as a JSON file.

    Args:
        user_id: The account identifier (maps to TodoModel.created_by /
            assigned_agent, MemoryRecordModel.agent_id, and
            VariableNamespaceModel.namespace == "user:<user_id>").
        session_factory: Optional async session factory. Falls back to the
            default DB URL via :mod:`general_ludd.db.session`.
        dest_dir: Where to write the backup file. Defaults to the system temp
            dir (or ``$GLUDD_BACKUP_DIR`` if set).

    Returns:
        The path to the written JSON backup file.
    """
    if not user_id or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")

    factory = session_factory or _default_session_factory()
    dest = Path(dest_dir or os.environ.get("GLUDD_BACKUP_DIR") or tempfile.gettempdir())
    dest.mkdir(parents=True, exist_ok=True)

    safe_user = _safe_filename_segment(user_id)
    timestamp = _utcnow_iso().replace(":", "").replace("-", "")
    fname = f"account-backup-{safe_user}-{timestamp}.json"
    path = dest / fname

    payload = asyncio.run(_export_user_data(factory, user_id))
    path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info(
        "backup_account: wrote %s (todos=%d, returns=%d, memory=%d, settings=%d)",
        path,
        len(payload["todos"]),
        len(payload["returns"]),
        len(payload["memory"]),
        len(payload["settings"]),
    )
    return path


def delete_account(
    user_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    """Delete all user-scoped data; return a summary of what was deleted.

    **Irreversible.** The CLI/HTTP layer should gate this behind an explicit
    confirmation.

    Args:
        user_id: The account identifier to delete.
        session_factory: Optional async session factory.

    Returns:
        ``{user_id, deleted_at, todos_deleted, returns_deleted,
        memory_deleted, settings_namespaces_deleted}``
    """
    if not user_id or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")

    factory = session_factory or _default_session_factory()
    summary = asyncio.run(_delete_user_data(factory, user_id))
    logger.info(
        "delete_account: removed %s (todos=%d, memory=%d)",
        user_id,
        summary["todos_deleted"],
        summary["memory_deleted"],
    )
    return summary


# ---------------------------------------------------------------------------
# Cloud-service deletion policy (re-export from deletion_notice)
# ---------------------------------------------------------------------------


def get_deletion_policy(service: str) -> str:
    """Return the data-retention policy text for a cloud service.

    Args:
        service: One of :data:`general_ludd.account.deletion_notice.SUPPORTED_SERVICES`
            (case-insensitive; whitespace and ``_``/``-`` normalized).

    Raises:
        ValueError: if the service is unknown.
    """
    return get_policy_text(service)


def list_supported_services() -> list[str]:
    """Return the sorted list of services known to :func:`get_deletion_policy`."""
    return sorted(SUPPORTED_SERVICES)


def build_notice(service: str) -> str:
    """Re-export of :func:`general_ludd.account.deletion_notice.build_deletion_notice`."""
    return build_deletion_notice(service)
