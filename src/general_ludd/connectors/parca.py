"""Parca continuous-profiling connector (self-contained).

``ParcaSource`` queries a Parca server's HTTP/Connect QueryService and normalizes
CPU-profile series into the project's eight-key telemetry-record shape. It is
deliberately *self-contained*: it imports nothing from the connector ``base`` or
any sibling module, so it can be vendored or reasoned about in isolation. The
only third-party dependency is ``httpx`` (used solely to build the *default*
transport; tests inject their own).

Contract
--------
- ``KIND = 'traces'`` (continuous CPU profiles are modeled under the traces kind).
- ``__init__(config, *, transport=None)`` — fully config-driven.
- Secrets are never inlined: the bearer token is read at call time from the
  environment variable *named* by ``config['token_env']``.
- ``base_url`` passes a literal-host SSRF guard (no DNS) before any request;
  loopback / private / link-local / metadata hosts are rejected unless
  ``config['allow_private']`` is truthy. Non-http(s) schemes are always rejected.
- ``health() -> {'ok': bool, 'detail': str}`` never raises.
- ``query(spec) -> list[dict]`` returns normalized records; on any transport
  error it returns ``[]`` (fail-soft) rather than raising.
- The HTTP transport is injectable; every request is time-bound; no ``shell``.

Wire format
-----------
Parca's Connect API answers ``POST {base_url}/parca.query.v1alpha1.QueryService/
QueryRange`` with a ``series`` list. Each series carries a ``labelset`` (a list of
``{name, value}`` labels — typically ``function`` / ``comm`` / ``job``) and a list
of ``samples`` (``{timestamp_ms, value}``). One normalized record is emitted per
series: ``value`` is the cumulative sample count, ``ts`` the latest sample's epoch
seconds, ``message`` the function name, and ``labels`` the decoded label-set.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Connect/HTTP path for the Parca QueryService range query.
_QUERY_RANGE_PATH = "/parca.query.v1alpha1.QueryService/QueryRange"


# --------------------------------------------------------------------------- #
# Injectable transport contract
# --------------------------------------------------------------------------- #
class _Transport(Protocol):
    def post(
        self,
        url: str,
        *,
        json: Any = ...,
        headers: dict[str, str] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


class _HttpxTransport:
    """Default transport: a thin per-request ``httpx`` wrapper.

    A fresh client per call keeps the connector stateless and avoids leaking
    sockets across a long-lived registry. Tests never reach this path — they
    inject their own transport.
    """

    def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            return client.post(url, json=json, headers=headers)


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class ParcaSource:
    """A Parca CPU-profile source normalized into telemetry records."""

    KIND: str = "traces"

    def __init__(self, config: dict[str, Any], *, transport: _Transport | None = None) -> None:
        self.name: str = str(config.get("name", "parca"))

        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("ParcaSource requires a base_url")
        self._allow_private = bool(config.get("allow_private", False))
        # SSRF: enforce the http(s) scheme + a present host always; delegate the
        # private/loopback + cloud-metadata host decision to the canonical shared
        # guard so it can never drift weaker than the single source of truth.
        # Private hosts remain opt-in via ``allow_private``.
        parts = urlsplit(base_url)
        if parts.scheme.lower() not in ("http", "https"):
            raise ValueError(f"base_url scheme must be http/https: {base_url!r}")
        if not parts.hostname:
            raise ValueError(f"base_url has no host: {base_url!r}")
        if not self._allow_private and is_url_blocked(base_url):
            raise ValueError(f"base_url host is blocked: {base_url!r}")
        self._base_url = base_url

        self._token_env = config.get("token_env")
        self._timeout: float = float(config.get("timeout", 20.0))
        self._transport: _Transport = transport if transport is not None else _HttpxTransport()

    # -- helpers ----------------------------------------------------------- #
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _query_range_url(self) -> str:
        return f"{self._base_url}{_QUERY_RANGE_PATH}"

    @staticmethod
    def _decode_labels(series: dict[str, Any]) -> dict[str, Any]:
        labels: dict[str, Any] = {}
        labelset = series.get("labelset") or {}
        for label in labelset.get("labels", []) or []:
            name = label.get("name")
            if name is not None:
                labels[str(name)] = label.get("value")
        return labels

    @staticmethod
    def _series_record(series: dict[str, Any], source: str, kind: str) -> dict[str, Any] | None:
        """Fold one Parca series into a normalized record (or ``None`` to skip)."""
        labels = ParcaSource._decode_labels(series)

        cumulative = 0.0
        latest_ts: float | None = None
        for sample in series.get("samples", []) or []:
            try:
                cumulative += float(sample.get("value", 0) or 0)
            except (TypeError, ValueError):
                continue
            raw_ts = sample.get("timestamp")
            if raw_ts is not None:
                try:
                    ts_s = float(raw_ts) / 1000.0
                except (TypeError, ValueError):
                    ts_s = None
                if ts_s is not None and (latest_ts is None or ts_s > latest_ts):
                    latest_ts = ts_s

        function = labels.get("function")
        message = str(function) if function is not None else "(unknown)"

        return {
            "ts": latest_ts,
            "source": source,
            "kind": kind,
            "level_or_status": "info",
            "message": message,
            "value": cumulative,
            "labels": labels,
            "raw": series,
        }

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a QueryRange and normalize the returned series. Fail-soft -> []."""
        body: dict[str, Any] = {
            "query": spec.get("query", ""),
            "start": spec.get("from"),
            "end": spec.get("to"),
            "step": spec.get("step"),
        }
        try:
            resp = self._transport.post(
                self._query_range_url(),
                json=body,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:
            logger.warning("Parca query transport error: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Parca query returned HTTP %s", resp.status_code)
            return []

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("Parca query returned undecodable body: %s", exc)
            return []

        records: list[dict[str, Any]] = []
        for series in payload.get("series", []) or []:
            record = self._series_record(series, self.name, self.KIND)
            if record is not None:
                records.append(record)
        return records

    def health(self) -> dict[str, Any]:
        """Probe the backend with a bounded QueryRange. Never raises."""
        try:
            resp = self._transport.post(
                self._query_range_url(),
                json={"query": "", "limit": 1},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:
            return {"ok": False, "detail": f"transport error: {exc}"}

        if resp.status_code == 200:
            return {"ok": True, "detail": "QueryRange reachable (HTTP 200)"}
        return {"ok": False, "detail": f"QueryRange returned HTTP {resp.status_code}"}
