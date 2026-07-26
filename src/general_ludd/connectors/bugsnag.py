"""Bugsnag error-events connector (KIND='logs').

Self-contained namespace-package module: no sibling/base/__init__ imports and no
module-level HTTP client. The transport is injected for mocked tests.

Contract (shared across general_ludd.connectors.*):
  * ``KIND`` class attr; ``name`` instance attr
  * config-driven ``__init__``; token read from ``os.environ`` via ``token_env``
  * literal-host SSRF block on ``base_url`` (no DNS)
  * ``health() -> {'ok', 'detail'}`` never raises
  * ``query(spec) -> list[dict]`` normalized records
    (ts, source, kind, level_or_status, message, value, labels, raw)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)


@runtime_checkable
class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


def _assert_public_base_url(base_url: str) -> None:
    """Reject a ``base_url`` whose *literal* host is internal.

    The private/loopback/metadata decision is delegated to the canonical
    :func:`general_ludd.security.ssrf.is_url_blocked` (no DNS) so this connector
    can never drift from the single source of truth. An always-on scheme and
    present-host check is kept for a precise error message.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"unsupported scheme: {parts.scheme!r}")
    if not parts.hostname:
        raise ConnectorConfigError("base_url has no host")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(
            f"base_url host is internal/loopback/metadata and is blocked: {base_url!r}"
        )


class BugsnagSource:
    """Read Bugsnag project errors and normalize them to records."""

    KIND = "logs"
    DEFAULT_BASE_URL = "https://api.bugsnag.com"

    def __init__(
        self,
        config: dict[str, object],
        transport: HttpTransport | Callable[..., HttpResponse],
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.name = str(config.get("name", "bugsnag"))
        self.base_url = str(config.get("base_url", self.DEFAULT_BASE_URL)).rstrip("/")
        _assert_public_base_url(self.base_url)
        project_id = config.get("project_id")
        if not project_id:
            raise ConnectorConfigError("config must set 'project_id'")
        self.project_id = str(project_id)
        token_env = config.get("token_env")
        if not token_env:
            raise ConnectorConfigError("config must set 'token_env' (name of env var holding the token)")
        token = env.get(str(token_env))
        if not token:
            raise ConnectorConfigError(f"environment variable {token_env!r} is unset or empty")
        self._token = token
        self._transport = transport
        self._timeout = float(str(config.get("timeout", 15.0)))

    def _headers(self) -> dict[str, str]:
        # Bugsnag Data Access API: Authorization: token <PERSONAL_AUTH_TOKEN>
        return {"Authorization": f"token {self._token}", "Accept": "application/json"}

    def _request(self, url: str, *, params: dict[str, object] | None = None) -> HttpResponse:
        """Call either the canonical ``request`` transport or a simple callable.

        Connector E2E harnesses commonly inject a callable ``(method, url, ...)``
        while production adapters expose ``.request``. Supporting both keeps the
        transport seam explicit without forcing every adapter to wrap itself.
        """
        request = getattr(self._transport, "request", None)
        if callable(request):
            return request("GET", url, headers=self._headers(), params=params, timeout=self._timeout)
        if callable(self._transport):
            try:
                return self._transport("GET", url, headers=self._headers(), params=params, timeout=self._timeout)
            except TypeError as first_error:
                try:
                    return self._transport(url, headers=self._headers(), params=params, timeout=self._timeout)
                except TypeError:
                    raise first_error from None
        raise TypeError("Bugsnag transport must expose request() or be callable")

    def _errors_url(self) -> str:
        return f"{self.base_url}/projects/{self.project_id}/errors"

    def health(self) -> dict[str, object]:
        try:
            resp = self._request(self._errors_url(), params={"per_page": 1})
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= resp.status_code < 300:
            return {"ok": True, "detail": f"HTTP {resp.status_code}"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, object] | None = None) -> list[dict[str, object]]:
        spec = spec or {}
        params: dict[str, object] = {}
        for key in ("status", "release_stage", "per_page", "sort"):
            if key in spec:
                params[key] = spec[key]
        resp = self._request(self._errors_url(), params=params)
        if not (200 <= resp.status_code < 300):
            raise ConnectorConfigError(f"bugsnag query failed: HTTP {resp.status_code}")
        payload = resp.json()
        errors = payload if isinstance(payload, list) else (payload or {}).get("errors", [])
        return [self._normalize(err) for err in errors]

    def _normalize(self, err: dict[str, object]) -> dict[str, object]:
        klass = err.get("error_class") or err.get("class")
        message = err.get("message")
        if klass and message:
            combined: object = f"{klass}: {message}"
        else:
            combined = message or klass
        return {
            "ts": err.get("last_seen"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": err.get("severity"),
            "message": combined,
            "value": err.get("events"),
            "labels": {
                "id": err.get("id"),
                "status": err.get("status"),
                "release_stage": err.get("release_stage"),
            },
            "raw": err,
        }
