"""Thread-safe tenant context for DB session scoping.

Uses ``contextvars.ContextVar`` so the tenant identity is automatically
propagated into worker threads (``asyncio.to_thread`` inherits the parent
context), closing the gap where ``ThreadPoolExecutor``-spawned sessions
lacked a tenant filter (C.3).
"""

from __future__ import annotations

import contextvars
from contextvars import Token

_current_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar("gludd_tenant_project_id", default=None)


def set_tenant(project_id: str | None) -> Token[str | None]:
    """Set the current tenant for the calling context and its threads.

    Returns a :class:`contextvars.Token` that MUST be passed to
    :func:`reset_tenant` to restore the previous value.
    """
    return _current_tenant.set(project_id)


def get_tenant() -> str | None:
    """Return the active tenant ``project_id``, or ``None``.

    .. warning::

        S27 — Tenant scoping is NOT enforced at the query level today.
        This contextvar is SET by the event loop on every tick, but
        nothing in the query path calls ``get_tenant()`` to inject
        a ``project_id`` WHERE filter. Cross-tenant isolation relies
        on explicit per-repository ``project_id`` arguments, which
        callers may forget. Tests in ``test_db_tenant_scoping.py``
        verify contextvar get/set/reset in isolation — NOT wired into
        a real query. Do not assume tenant isolation from this module
        until the query-path injection is built.
    """
    return _current_tenant.get()


def reset_tenant(token: Token[str | None]) -> None:
    """Restore the tenant to the value captured by *token*."""
    _current_tenant.reset(token)
