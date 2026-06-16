"""VictoriaMetrics metrics connector.

Self-contained Prometheus-compatible source for VictoriaMetrics' HTTP API.
No imports from a connector base or sibling modules: everything this source
needs (transport protocol, SSRF guard, normalization) lives in this file.

Contract (see project connector spec):
    - class attr KIND = 'metrics'
    - instance attr ``name``
    - __init__(config, transport=None) — fully config-driven
    - secrets resolved from ``*_env`` keys, never inlined
    - literal-host SSRF block on ``base_url`` (opt-in ``allow_private``)
    - health() -> {'ok', 'detail'} and NEVER raises
    - query(spec) -> list[dict] normalized records
    - injectable HTTP transport (defaults to a stdlib urllib transport)
    - time-bound requests; never uses shell
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable

KIND = "metrics"

_DEFAULT_TIMEOUT = 30.0


@runtime_checkable
class Transport(Protocol):
    """Minimal injectable HTTP transport.

    A transport returns ``(status_code, body_text)`` for a request. Tests pass
    a fake; production uses :class:`_UrllibTransport`.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str]:
        ...


class _UrllibTransport:
    """Default transport backed by :mod:`urllib` (no third-party deps, no shell)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str]:
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return int(resp.status), raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            return int(exc.code), raw.decode("utf-8", "replace")


class SSRFError(ValueError):
    """Raised when ``base_url`` resolves to a private/loopback host."""


def _is_private_host(host: str) -> bool:
    """True if ``host`` is a literal private/loopback/link-local/reserved IP.

    Only *literal* IPs are inspected (no DNS resolution), matching the
    contract's "literal-host SSRF block". A hostname that is not an IP literal
    is treated as non-private here.
    """
    candidate = host.strip("[]")
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # Not an IP literal — also block obvious loopback aliases.
        return host.lower() in {"localhost", "localhost.localdomain"}
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def _guard_base_url(base_url: str, allow_private: bool) -> str:
    """Validate scheme + SSRF policy; return a normalized base_url (no trailing /)."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFError(f"unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise SSRFError("base_url has no host")
    if not allow_private and _is_private_host(host):
        raise SSRFError(f"refusing private/loopback host: {host!r} (set allow_private=True)")
    return base_url.rstrip("/")


def _coerce_ts(value: Any) -> float | None:
    """Prometheus timestamps are unix seconds (possibly float)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class VictoriaMetricsSource:
    """Read records from a VictoriaMetrics (Prometheus-compatible) endpoint.

    Querying:
        - ``GET {base_url}/api/v1/query`` for instant queries (PromQL via ``query=``)
        - ``GET {base_url}/api/v1/query_range`` for range queries

    A normalized record has keys:
        ts, source, kind, level_or_status, message, value, labels, raw
    where ``message`` is the metric ``__name__`` and ``labels`` is the metric
    labelset.
    """

    KIND = "metrics"

    def __init__(self, config: dict[str, Any], transport: Transport | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "victoriametrics"))
        self.allow_private: bool = bool(self.config.get("allow_private", False))
        self.base_url: str = _guard_base_url(
            str(self.config["base_url"]), self.allow_private
        )
        self.timeout: float = float(self.config.get("timeout", _DEFAULT_TIMEOUT))
        self._transport: Transport = transport or _UrllibTransport()

        # Optional HTTP Basic auth from env (never inline secrets).
        self._username: str | None = None
        self._password: str | None = None
        user_env = self.config.get("username_env")
        pass_env = self.config.get("password_env")
        if user_env:
            self._username = os.environ.get(str(user_env))
        if pass_env:
            self._password = os.environ.get(str(pass_env))

    # -- internals ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._username is not None and self._password is not None:
            token = base64.b64encode(
                f"{self._username}:{self._password}".encode()
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _get(self, path: str, params: dict[str, str]) -> tuple[int, str]:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}"
        return self._transport.request(
            "GET", url, headers=self._auth_headers(), timeout=self.timeout
        )

    def _records_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("status") != "success":
            return []
        data = payload.get("data") or {}
        result_type = data.get("resultType")
        results = data.get("result") or []
        records: list[dict[str, Any]] = []
        for series in results:
            metric = dict(series.get("metric") or {})
            name = metric.get("__name__", "")
            labels = {k: v for k, v in metric.items() if k != "__name__"}
            if result_type == "matrix":
                points = series.get("values") or []
            else:  # vector / scalar / string -> single point under "value"
                single = series.get("value")
                points = [single] if single else []
            for point in points:
                if not point or len(point) < 2:
                    continue
                ts = _coerce_ts(point[0])
                val = _coerce_value(point[1])
                records.append(
                    {
                        "ts": ts,
                        "source": self.name,
                        "kind": self.KIND,
                        "level_or_status": "ok",
                        "message": name,
                        "value": val,
                        "labels": labels,
                        "raw": series,
                    }
                )
        return records

    # -- public API --------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run an instant or range PromQL query and return normalized records.

        ``spec`` keys:
            query (required): PromQL expression
            start, end, step: present -> use /api/v1/query_range
            time: optional instant evaluation timestamp
        """
        promql = spec.get("query")
        if not promql:
            return []
        params: dict[str, str] = {"query": str(promql)}
        is_range = any(k in spec and spec[k] is not None for k in ("start", "end", "step"))
        if is_range:
            for key in ("start", "end", "step"):
                if spec.get(key) is not None:
                    params[key] = str(spec[key])
            path = "/api/v1/query_range"
        else:
            if spec.get("time") is not None:
                params["time"] = str(spec["time"])
            path = "/api/v1/query"
        try:
            status, body = self._get(path, params)
        except Exception:
            return []
        if status != 200 or not body:
            return []
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []
        return self._records_from_payload(payload)

    def health(self) -> dict[str, Any]:
        """Probe the endpoint. Returns {'ok': bool, 'detail': str}; never raises."""
        try:
            status, _ = self._get("/api/v1/query", {"query": "vector(1)"})
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}
        if status == 200:
            return {"ok": True, "detail": "ok"}
        return {"ok": False, "detail": f"http status {status}"}


__all__ = ["KIND", "SSRFError", "Transport", "VictoriaMetricsSource"]
