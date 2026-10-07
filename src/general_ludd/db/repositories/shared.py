"""Shared contracts for database repository implementations."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Generator

from sqlalchemy.exc import OperationalError

DEFAULT_LIST_LIMIT = 1000


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
