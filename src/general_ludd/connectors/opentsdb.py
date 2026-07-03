"""OpenTSDB metrics connector.

Self-contained source for OpenTSDB's HTTP query API. No imports from a
connector base or sibling modules.

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

OpenTSDB-specific: ``POST {base_url}/api/query`` with a JSON body
``{start, end, queries:[{metric, aggregator, tags}]}`` returns a list of result
objects each carrying a ``dps`` map of ``{timestamp: value}``. One normalized
record is emitted per dps point.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable

from general_ludd.security.ssrf import is_url_blocked

KIND = "metrics"

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_AGGREGATOR = "sum"


@runtime_checkable
class Transport(Protocol):
    """Minimal injectable HTTP transport returning ``(status_code, body_text)``."""

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


def _guard_base_url(base_url: str, allow_private: bool) -> str:
    """Validate scheme + SSRF policy; return a normalized base_url (no trailing /).

    The private/loopback + cloud-metadata NAME decision (localhost,
    metadata.google.internal, ...) is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector cannot
    drift weaker than the single source of truth. Private hosts remain opt-in
    via ``allow_private``.
    """
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFError(f"unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise SSRFError("base_url has no host")
    if not allow_private and is_url_blocked(base_url):
        raise SSRFError(f"refusing private/loopback host: {host!r} (set allow_private=True)")
    return base_url.rstrip("/")


def _coerce_ts(value: Any) -> float | None:
    """OpenTSDB dps keys are unix timestamps (seconds or ms) as strings/ints."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OpenTsdbSource:
    """Read records from an OpenTSDB endpoint via ``POST /api/query``.

    A normalized record has keys:
        ts, source, kind, level_or_status, message, value, labels, raw
    where ``message`` is the metric name and ``labels`` is the point's tag map.
    One record is emitted per dps point.
    """

    KIND = "metrics"

    def __init__(self, config: dict[str, Any], transport: Transport | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "opentsdb"))
        self.allow_private: bool = bool(self.config.get("allow_private", False))
        self.base_url: str = _guard_base_url(
            str(self.config["base_url"]), self.allow_private
        )
        self.timeout: float = float(self.config.get("timeout", _DEFAULT_TIMEOUT))
        self.default_aggregator: str = str(
            self.config.get("aggregator", _DEFAULT_AGGREGATOR)
        )
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

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._username is not None and self._password is not None:
            token = base64.b64encode(
                f"{self._username}:{self._password}".encode()
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _build_body(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Translate a query spec into an OpenTSDB request body.

        ``spec`` keys:
            start (required), end (optional)
            queries: an explicit list of query objects, OR
            metric + aggregator + tags for a single-query convenience form.
        """
        body: dict[str, Any] = {}
        if spec.get("start") is not None:
            body["start"] = spec["start"]
        if spec.get("end") is not None:
            body["end"] = spec["end"]

        if isinstance(spec.get("queries"), list):
            body["queries"] = spec["queries"]
        else:
            query_obj: dict[str, Any] = {
                "metric": spec.get("metric"),
                "aggregator": spec.get("aggregator", self.default_aggregator),
            }
            tags = spec.get("tags")
            if isinstance(tags, dict):
                query_obj["tags"] = tags
            body["queries"] = [query_obj]
        return body

    def _post_query(self, body: dict[str, Any]) -> tuple[int, str]:
        url = f"{self.base_url}/api/query"
        payload = json.dumps(body).encode("utf-8")
        return self._transport.request(
            "POST", url, headers=self._headers(), body=payload, timeout=self.timeout
        )

    def _records_from_results(self, results: list[Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            metric = result.get("metric", "")
            tags = dict(result.get("tags") or {})
            dps = result.get("dps") or {}
            if not isinstance(dps, dict):
                continue
            for raw_ts, raw_val in dps.items():
                ts = _coerce_ts(raw_ts)
                val = _coerce_value(raw_val)
                records.append(
                    {
                        "ts": ts,
                        "source": self.name,
                        "kind": self.KIND,
                        "level_or_status": "ok",
                        "message": metric,
                        "value": val,
                        "labels": tags,
                        "raw": result,
                    }
                )
        return records

    # -- public API --------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """POST an OpenTSDB query and return normalized records (one per dps point)."""
        if spec.get("start") is None and not isinstance(spec.get("queries"), list):
            return []
        body = self._build_body(spec)
        try:
            status, text = self._post_query(body)
        except Exception:
            return []
        if status != 200 or not text:
            return []
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return []
        # OpenTSDB returns a JSON array of result objects on success; an error is
        # an object with an "error" key.
        if not isinstance(payload, list):
            return []
        return self._records_from_results(payload)

    def health(self) -> dict[str, Any]:
        """Probe the endpoint. Returns {'ok': bool, 'detail': str}; never raises."""
        try:
            status, _ = self._transport.request(
                "GET",
                f"{self.base_url}/api/version",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}
        if status == 200:
            return {"ok": True, "detail": "ok"}
        return {"ok": False, "detail": f"http status {status}"}


__all__ = ["KIND", "OpenTsdbSource", "SSRFError", "Transport"]
