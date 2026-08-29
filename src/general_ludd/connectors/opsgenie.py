"""Opsgenie incident-source connector.

Self-contained: imports no sibling connector, base class, or package __init__
helper. Transport is injectable for testing; production falls back to ``httpx``.

Security notes:
* The API key is read from the environment (``token_env``); never hardcoded.
* ``base_url`` is validated against a literal-host SSRF blocklist with no DNS
  resolution.
* All requests are time-bounded and never use a shell.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlparse

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)


def _invoke_get(transport: object, url: str, **kwargs: object) -> HttpResponse:
    """Invoke an injected GET transport, including callable tuple doubles."""
    getter = getattr(transport, "get", None)
    if callable(getter):
        result = getter(url, **kwargs)
    elif callable(transport):
        result = transport("GET", url, **kwargs)
    else:
        raise TypeError("transport must expose get or be callable")
    if isinstance(result, tuple) and len(result) == 2:

        class _TupleResponse:
            status_code = int(result[0]) if isinstance(result[0], int) else 0

            def json(self) -> object:
                return result[1]

        return cast(HttpResponse, _TupleResponse())
    return cast(HttpResponse, result)


DEFAULT_BASE_URL = "https://api.opsgenie.com"
DEFAULT_TIMEOUT = 15.0
DEFAULT_LIMIT = 100


@runtime_checkable
class HttpTransport(Protocol):
    """Minimal injectable transport returning an ``HttpResponse``."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: Mapping[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse:
        """Fetch ``url`` and return an ``HttpResponse``."""
        ...


def _validate_base_url(base_url: str) -> str:
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        raise ValueError(f"refusing to target internal/loopback host: {host!r}")
    return base_url.rstrip("/")


def _parse_created_at(raw: object) -> str | None:
    """Normalize Opsgenie ``createdAt`` to an ISO-8601 string.

    Opsgenie returns an ISO string with millisecond precision and a ``Z``
    suffix; some integrations return an epoch (micro/milli/seconds) integer.
    Anything unparseable is returned as a stringified passthrough.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # Heuristic on magnitude: micro -> milli -> seconds.
        value = float(raw)
        if value > 1e14:
            value /= 1_000_000.0
        elif value > 1e11:
            value /= 1_000.0
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    text = str(raw)
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).isoformat()
    except ValueError:
        return text


class _DefaultTransport:
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        import httpx

        return httpx.get(
            url,
            headers=headers,
            params=cast("Mapping[str, str | int | list[str]]", params) if isinstance(params, dict) else None,
            timeout=timeout,
            follow_redirects=False,
        )


class _TupleTransport:
    def __init__(self, fn: object) -> None:
        self._fn = cast("Callable[..., object]", fn)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        result = self._fn(
            "GET",
            url,
            headers=headers or {},
            params=params,
            timeout=timeout,
        )
        status, payload = result if isinstance(result, tuple) and len(result) == 2 else (0, {})

        class Response:
            status_code = int(status)

            def json(self_nonlocal) -> object:
                return payload

        return cast(HttpResponse, Response())


class OpsgenieSource:
    """Incident source backed by the Opsgenie Alerts API."""

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        """Build the source from connector config and select the transport."""
        self.config = dict(config)
        self.name = str(self.config.get("name", "opsgenie"))
        self.base_url = _validate_base_url(str(self.config.get("base_url", DEFAULT_BASE_URL)))
        self.token_env = str(self.config.get("token_env", "OPSGENIE_API_KEY"))
        self.timeout = float(str(self.config.get("timeout", DEFAULT_TIMEOUT)))
        self.default_limit = int(str(self.config.get("limit", DEFAULT_LIMIT)))
        self._transport: HttpTransport = (
            _TupleTransport(transport)
            if callable(transport) and not hasattr(transport, "get")
            else transport or _DefaultTransport()
        )

    # -- internals --------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(f"Opsgenie API key not found in environment variable {self.token_env!r}")
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"GenieKey {self._token()}"}

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object] | None = None) -> list[dict[str, object]]:
        """Return normalized alerts from ``/v2/alerts``."""
        spec = spec or {}
        params: dict[str, object] = {"limit": int(str(spec.get("limit", self.default_limit)))}
        if spec.get("query"):
            params["query"] = spec["query"]

        resp = _invoke_get(
            self._transport,
            f"{self.base_url}/v2/alerts",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Opsgenie /v2/alerts returned status {resp.status_code}")
        payload = resp.json() or {}
        alerts = payload.get("data", []) or []
        return [self._normalize(alert) for alert in alerts]

    def health(self) -> dict[str, object]:
        """Probe the source. Never raises."""
        try:
            resp = _invoke_get(
                self._transport,
                f"{self.base_url}/v2/alerts",
                headers=self._headers(),
                params={"limit": 1},
                timeout=self.timeout,
            )
        except Exception:  # never raises
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if status >= 400:
            return {"ok": False, "detail": f"http status {status}"}
        return {"ok": True, "detail": f"http status {status}"}

    # -- normalization ----------------------------------------------------

    def _normalize(self, alert: dict[str, object]) -> dict[str, object]:
        status = alert.get("status", "")
        priority = alert.get("priority", "")
        level_or_status = f"{status}/{priority}" if priority else str(status)

        labels = {
            "id": alert.get("id", ""),
            "tinyId": alert.get("tinyId", ""),
            "owner": alert.get("owner", ""),
            "tags": alert.get("tags", []),
            "source": alert.get("source", ""),
        }
        return {
            "ts": _parse_created_at(alert.get("createdAt")),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level_or_status,
            "message": alert.get("message", ""),
            "value": None,
            "labels": labels,
            "raw": alert,
        }
