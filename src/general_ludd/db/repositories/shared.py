"""Shared contracts for database repository implementations."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Generator
from typing import TypeAlias

from sqlalchemy.dialects.postgresql import Insert as PostgreSQLInsert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase

DEFAULT_LIST_LIMIT = 1000
DialectInsert: TypeAlias = PostgreSQLInsert | SQLiteInsert


def dialect_insert(
    model: type[DeclarativeBase],
    dialect_name: str,
) -> DialectInsert:
    """Return the native upsert-capable INSERT for a supported SQL dialect.

    SQLAlchemy exposes ``ON CONFLICT`` only on dialect-specific INSERT
    subclasses.  Repositories resolve the dialect from their already-bound
    session and call this helper before performing any I/O.  Failing closed on
    unknown backends prevents accidentally compiling SQLite SQL for a different
    database.
    """
    if dialect_name == "postgresql":
        return postgresql_insert(model)
    if dialect_name == "sqlite":
        return sqlite_insert(model)
    raise ValueError(f"Repository upserts do not support SQL dialect {dialect_name!r}")


def current_list_limit() -> int:
    """Read the facade-owned limit so its historical patch seam still works.

    The component layer does not import the facade, which keeps imports acyclic.
    When callers import a component directly, the stable default is used.
    """
    facade = sys.modules.get("general_ludd.db.repository")
    return int(getattr(facade, "_DEFAULT_LIST_LIMIT", DEFAULT_LIST_LIMIT))


@contextlib.contextmanager
def scoped_to(project_id: str) -> Generator[None, None, None]:
    """Apply tenant filtering to repository operations within the context."""
    from general_ludd.db.tenant import reset_tenant, set_tenant

    token = set_tenant(project_id)
    try:
        yield
    finally:
        reset_tenant(token)


def _is_locked_error(exc: OperationalError) -> bool:
    """Return whether an operational failure is SQLite's transient lock error."""
    original = getattr(exc, "orig", None)
    message = str(original if original is not None else exc).lower()
    return "database is locked" in message or "database table is locked" in message


class ConcurrencyError(RuntimeError):
    """Raised when optimistic concurrency detects a stale repository write."""


class InvalidTransitionError(ConcurrencyError):
    """Raised when a persisted task cannot enter the requested state."""
