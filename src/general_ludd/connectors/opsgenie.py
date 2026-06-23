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

import os
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from general_ludd.security.ssrf import is_url_blocked

DEFAULT_BASE_URL = "https://api.opsgenie.com"
DEFAULT_TIMEOUT = 15.0
DEFAULT_LIMIT = 100


@runtime_checkable
class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: Any = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


def _validate_base_url(base_url: str) -> str:
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        raise ValueError(f"refusing to target internal/loopback host: {host!r}")
    return base_url.rstrip("/")


def _parse_created_at(raw: Any) -> str | None:
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
        if value > 1e16:
            value /= 1_000_000.0
        elif value > 1e13:
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
        params: Any = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        import httpx

        return httpx.get(
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            follow_redirects=False,
        )


class OpsgenieSource:
    """Incident source backed by the Opsgenie Alerts API."""

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = dict(config)
        self.name = str(self.config.get("name", "opsgenie"))
        self.base_url = _validate_base_url(
            str(self.config.get("base_url", DEFAULT_BASE_URL))
        )
        self.token_env = str(self.config.get("token_env", "OPSGENIE_API_KEY"))
        self.timeout = float(self.config.get("timeout", DEFAULT_TIMEOUT))
        self.default_limit = int(self.config.get("limit", DEFAULT_LIMIT))
        self._transport: HttpTransport = transport or _DefaultTransport()

    # -- internals --------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"Opsgenie API key not found in environment variable "
                f"{self.token_env!r}"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"GenieKey {self._token()}"}

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        params: dict[str, Any] = {"limit": int(spec.get("limit", self.default_limit))}
        if spec.get("query"):
            params["query"] = spec["query"]

        resp = self._transport.get(
            f"{self.base_url}/v2/alerts",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Opsgenie /v2/alerts returned status {resp.status_code}"
            )
        payload = resp.json() or {}
        alerts = payload.get("data", []) or []
        return [self._normalize(alert) for alert in alerts]

    def health(self) -> dict[str, Any]:
        try:
            resp = self._transport.get(
                f"{self.base_url}/v2/alerts",
                headers=self._headers(),
                params={"limit": 1},
                timeout=self.timeout,
            )
        except Exception as exc:  # never raises
            return {"ok": False, "detail": f"request failed: {exc}"}
        status = getattr(resp, "status_code", 0)
        if status >= 400:
            return {"ok": False, "detail": f"http status {status}"}
        return {"ok": True, "detail": f"http status {status}"}

    # -- normalization ----------------------------------------------------

    def _normalize(self, alert: dict[str, Any]) -> dict[str, Any]:
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
