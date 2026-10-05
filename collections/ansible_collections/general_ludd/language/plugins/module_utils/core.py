"""Authenticated controller client for daemon-owned language operations.

The collection is intentionally a transport boundary: managed hosts and the
Ansible controller never import Gludd's application package.  The daemon owns
the single implementation of every language algorithm and this client reuses
the agent collection's stdlib-only authenticated HTTP transport.
"""

from __future__ import annotations

from typing import Any, Protocol

from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    DEFAULT_DAEMON_URL,
    DEFAULT_TIMEOUT,
    GluddClient,
)


class LanguageServiceError(RuntimeError):
    """Raised when the authenticated daemon cannot produce a valid result."""


class _Transport(Protocol):
    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]: ...


class LanguageClient:
    """Reusable authenticated client for the bounded language endpoint."""

    def __init__(
        self,
        *,
        daemon_url: str = DEFAULT_DAEMON_URL,
        psk: str,
        timeout: int = DEFAULT_TIMEOUT,
        transport: _Transport | None = None,
    ) -> None:
        if not isinstance(psk, str) or not psk.strip():
            raise ValueError("psk must be a non-empty string")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive integer")
        self._transport = transport or GluddClient(
            base_url=daemon_url,
            psk=psk,
            timeout=timeout,
        )

    def execute(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one operation while preserving the daemon's result schema."""
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation must be a non-empty string")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        response = self._transport.post(
            "/api/language/execute",
            {"operation": operation, "payload": payload},
        )
        status = response.get("_status")
        if type(status) is not int or not 200 <= status < 300:
            detail = response.get("detail") or response.get("_error") or "language service request failed"
            raise LanguageServiceError(str(detail))
        result = response.get("result")
        if not isinstance(result, dict):
            raise LanguageServiceError("language service returned an invalid result")
        return result


def detect_language(
    text: str,
    *,
    psk: str,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Compatibility wrapper for authenticated language detection."""
    return LanguageClient(daemon_url=daemon_url, psk=psk, timeout=timeout).execute(
        "language_detect",
        {"input_text": text},
    )


def translate(
    text: str,
    source_language: str,
    target_language: str,
    *,
    psk: str,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Compatibility wrapper for authenticated bounded translation."""
    return LanguageClient(daemon_url=daemon_url, psk=psk, timeout=timeout).execute(
        "translate",
        {
            "input_text": text,
            "source_language": source_language,
            "target_language": target_language,
        },
    )


def transliterate(
    text: str,
    scheme: str,
    *,
    psk: str,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Compatibility wrapper for authenticated transliteration."""
    return LanguageClient(daemon_url=daemon_url, psk=psk, timeout=timeout).execute(
        "transliterate",
        {"input_text": text, "scheme": scheme},
    )


__all__ = [
    "LanguageClient",
    "LanguageServiceError",
    "detect_language",
    "translate",
    "transliterate",
]
