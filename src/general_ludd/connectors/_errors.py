"""Shared connector error classes and sanitization helpers.

Every connector that raises an ``SSRFError`` or ``ConnectorConfigError``
should import from here instead of defining its own local copy.

``sanitize_exc_message`` provides a safe, path/token/credential-free
error label for the ``detail``/``error`` field of health and query records.
Connectors MUST use it instead of embedding raw ``str(exc)`` / ``{exc!r}``
in records returned to callers.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_PATH_PATTERN = re.compile(r"(/[^\s,:;\"')\]}]*)+")
_TOKEN_PATTERN = re.compile(
    r"(bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*|secret[=:]\s*|password[=:]\s*)"
    r"[A-Za-z0-9+/=_-]{8,}",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s,\"')}\]]+")


def sanitize_exc_message(exc: BaseException) -> str:
    """Return a safe error label that never leaks paths, tokens, or URLs.

    Logs only the exception type so failures remain observable without copying
    attacker-controlled exception text, credentials, paths, or URLs into logs.
    ``exc_info`` stays unset: attaching the traceback would embed the
    secret-bearing exception message in the log record, defeating the
    sanitizer (see tests/unit/test_h20_connector_exc_leak.py).
    """
    error_type = type(exc).__name__
    logger.warning("connector exception sanitized type=%s", error_type)
    return error_type


def sanitize_str(text: str) -> str:
    """Redact paths, tokens/keys, and internal URLs from arbitrary text.

    Returns a best-effort safe copy.  Callers that need the original value
    for logging MUST log BEFORE calling this function.
    """
    text = _URL_PATTERN.sub("[REDACTED-URL]", text)
    text = _PATH_PATTERN.sub("[REDACTED-PATH]", text)
    text = _TOKEN_PATTERN.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


class SSRFError(ValueError):
    """Raised when ``base_url`` points at a forbidden (internal) host."""


class ConnectorConfigError(ValueError):
    """Invalid connector config or a blocked ``base_url`` host."""
