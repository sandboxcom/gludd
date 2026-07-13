"""Centralized exception sanitizer — canonical import for connector exception safety.

All connector modules MUST import ``sanitize_exc_message`` and
``sanitize_str`` from here instead of from ``_errors`` directly.
This module is the single choke point for exception-text sanitization
across all ~90 connector modules.

Re-exports ``sanitize_exc_message`` / ``sanitize_str`` from ``_errors``
and adds conveniences for the common connector error-return patterns.
"""

from __future__ import annotations

from general_ludd.connectors._errors import (
    sanitize_exc_message,
    sanitize_str,
)

__all__ = [
    "sanitize_exc_message",
    "sanitize_str",
    "sanitize_exc_for_health",
    "sanitize_exc_for_query",
]


def sanitize_exc_for_health(exc: BaseException) -> str:
    """Return a safe detail string for a failing ``health()`` response.

    Always logs the full traceback with ``exc_info=True`` and returns
    ``"health check failed"`` — a generic, path-free, token-free label.

    Usage in connector ``health()`` methods::

        except Exception as exc:
            return {"ok": False, "detail": sanitize_exc_for_health(exc)}
    """
    return sanitize_exc_message(exc)


def sanitize_exc_for_query(exc: BaseException) -> str:
    """Return a safe error message for a ``query()`` error record.

    Always logs the full traceback with ``exc_info=True`` and returns
    ``type(exc).__name__`` — no paths, tokens, URLs, or stack traces.

    Usage in connector ``query()`` methods::

        except _ConfigError as exc:
            return [self._error(sanitize_exc_for_query(exc))]
    """
    return sanitize_exc_message(exc)
