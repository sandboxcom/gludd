"""Thread-safe tenant context for DB session scoping.

Uses ``contextvars.ContextVar`` so the tenant identity is automatically
propagated into worker threads (``asyncio.to_thread`` inherits the parent
context), closing the gap where ``ThreadPoolExecutor``-spawned sessions
lacked a tenant filter (C.3).
"""

from __future__ import annotations

import contextvars
from contextvars import Token

_current_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gludd_tenant_project_id", default=None
)


def set_tenant(project_id: str | None) -> Token[str | None]:
    """Set the current tenant for the calling context and its threads.

    Returns a :class:`contextvars.Token` that MUST be passed to
    :func:`reset_tenant` to restore the previous value.
    """
    return _current_tenant.set(project_id)


def get_tenant() -> str | None:
    """Return the active tenant ``project_id``, or ``None``."""
    return _current_tenant.get()


def reset_tenant(token: Token[str | None]) -> None:
    """Restore the tenant to the value captured by *token*."""
    _current_tenant.reset(token)
