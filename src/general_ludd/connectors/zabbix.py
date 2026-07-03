"""Zabbix monitoring connector.

Self-contained read-only source that talks to the Zabbix JSON-RPC API
(``/api_jsonrpc.php``) and normalizes ``history.get`` samples and
``problem.get`` alerts into the project's flat record shape. No base/sibling
imports.

Contract
--------
* ``KIND == "metrics"``.
* ``name`` attribute identifies the source instance.
* ``__init__(config)`` is config-driven; the API auth token is read from the
  environment via ``token_env`` (never inline).
* ``base_url`` is checked with a *literal-host* SSRF guard (private/loopback/
  link-local/reserved IP literals rejected; no DNS).
* ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
* ``query(spec)`` returns normalized ``dict`` records with the keys
  ``ts, source, kind, level_or_status, message, value, labels, raw``.
* The HTTP transport is injectable; the default uses ``httpx``. Time-bound,
  no ``shell=True``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked

KIND = "metrics"

# Zabbix problem severity code -> human label, used for level_or_status.
_SEVERITY = {
    "0": "not_classified",
    "1": "information",
    "2": "warning",
    "3": "average",
    "4": "high",
    "5": "disaster",
}


@runtime_checkable
class HttpResponse(Protocol):
    """Minimal response surface the connector relies on."""

    status_code: int

    def json(self) -> Any: ...


Transport = Callable[..., HttpResponse]


class SSRFError(ValueError):
    """Raised when ``base_url`` points at a blocked internal literal host."""


def _validate_base_url(base_url: str) -> str:
    """Fail-closed SSRF guard on a base URL's literal host.

    Delegates the private/loopback/link-local/reserved *and* the cloud-metadata
    NAME decision (localhost, metadata.google.internal, ...) to the canonical
    shared guard :func:`general_ludd.security.ssrf.is_url_blocked`, so this
    connector can never drift weaker than the single source of truth. Zabbix has
    no ``allow_private`` opt-in — internal literals and metadata names are always
    rejected.
    """
    if not isinstance(base_url, str) or not base_url:
        raise SSRFError("base_url must be a non-empty string")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"base_url scheme must be http/https, got {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise SSRFError(f"base_url has no host: {base_url!r}")
    if is_url_blocked(base_url):
        raise SSRFError(f"base_url host {host!r} is a blocked internal address")
    return base_url.rstrip("/")


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int_ts(clock: Any) -> int | None:
    try:
        if clock is None:
            return None
        return int(clock)
    except (TypeError, ValueError):
        return None


class ZabbixSource:
    """Query Zabbix history/problems via JSON-RPC and normalize them."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: Transport | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.name: str = str(config.get("name", "zabbix"))
        self.base_url: str = _validate_base_url(str(config.get("base_url", "")))
        self.timeout: float = float(config.get("timeout", 30.0))
        self._transport: Transport = transport or _default_transport

        # Auth token resolved from the environment via *_env indirection only.
        self._token: str | None = None
        token_env = config.get("token_env")
        if token_env:
            self._token = env.get(str(token_env))

    def _rpc_url(self) -> str:
        return f"{self.base_url}/api_jsonrpc.php"

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        """Issue one JSON-RPC call; returns the ``result`` field."""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        # Zabbix 5.4+ takes the token as a Bearer header; older servers use the
        # ``auth`` body field. We send both forms so either server accepts it,
        # except for token-less methods like apiinfo.version.
        headers = {"Content-Type": "application/json-rpc"}
        if self._token and method != "apiinfo.version":
            headers["Authorization"] = f"Bearer {self._token}"
            payload["auth"] = self._token
        resp = self._transport(
            "POST",
            self._rpc_url(),
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        body = resp.json() or {}
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(f"Zabbix RPC error: {body['error']}")
        return body.get("result") if isinstance(body, dict) else None

    def health(self) -> dict[str, Any]:
        """Call apiinfo.version; never raises."""
        try:
            version = self._call("apiinfo.version", {})
        except Exception as exc:  # health() must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if version:
            return {"ok": True, "detail": f"Zabbix API {version}"}
        return {"ok": False, "detail": "no version returned"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch to history.get or problem.get and normalize the result."""
        method = str(spec.get("method", "history.get"))
        params = dict(spec.get("params") or {})
        result = self._call(method, params)
        rows = result if isinstance(result, list) else []
        if method == "problem.get":
            return [self._normalize_problem(row) for row in rows]
        return [self._normalize_history(row) for row in rows]

    def _normalize_history(self, row: dict[str, Any]) -> dict[str, Any]:
        labels: dict[str, Any] = {}
        if row.get("host") is not None:
            labels["host"] = row["host"]
        if row.get("itemid") is not None:
            labels["item"] = str(row["itemid"])
        if row.get("name") is not None:
            labels["item_name"] = row["name"]
        return {
            "ts": _as_int_ts(row.get("clock")),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "ok",
            "message": str(row.get("name") or row.get("itemid") or ""),
            "value": _as_float(row.get("value")),
            "labels": labels,
            "raw": row,
        }

    def _normalize_problem(self, row: dict[str, Any]) -> dict[str, Any]:
        severity = str(row.get("severity", ""))
        labels: dict[str, Any] = {}
        if row.get("objectid") is not None:
            labels["trigger"] = str(row["objectid"])
        if row.get("host") is not None:
            labels["host"] = row["host"]
        if row.get("eventid") is not None:
            labels["event"] = str(row["eventid"])
        return {
            "ts": _as_int_ts(row.get("clock")),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": _SEVERITY.get(severity, severity or "unknown"),
            "message": str(row.get("name") or "problem"),
            "value": _as_float(severity),
            "labels": labels,
            "raw": row,
        }
