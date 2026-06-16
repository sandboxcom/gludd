"""Self-contained NATS monitoring-endpoint observability connector.

Pulls server stats from the NATS HTTP monitoring endpoints (``/varz``,
``/connz``, ``/subsz``) and normalizes connection counts, subject/subscription
counts, and message throughput into flat metric records.

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url`` (no DNS resolution, no network at
  construction time). Plain ``http`` is allowed (the monitoring port is HTTP).
* An optional bearer token is read from the environment via ``token_env``
  (NATS monitoring is usually unauthenticated, but can sit behind a proxy).
* The HTTP transport is *injectable* so the connector is driven entirely from
  mocked responses in tests (no real network, no DNS, no shell, no subprocess).
* ``health()`` never raises (reports failure in ``{"ok", "detail"}``).
  ``query()`` never raises: transport / protocol failures become a single
  normalized error record.
* Every backend call is time-bound (``timeout`` passed to the transport).

The monitoring server listens on port ``8222`` by default; if ``base_url`` has
no explicit port we append ``:8222``.

Record shape (one dict per sample)::

    {
        "ts": float,                 # scrape time (unix seconds)
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # human-readable line
        "value": float,              # numeric payload
        "labels": dict[str, str],    # {server_id, endpoint, metric, ...}
        "raw": Any,                  # original API object
    }
"""

from __future__ import annotations

import ipaddress
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# Injectable transport signature: (url, params, headers, timeout) -> (status, json)
HttpGet = Callable[..., "tuple[int, Any]"]

KIND = "metrics"

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
    }
)

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_MONITOR_PORT = 8222


def _strip_brackets(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _is_blocked_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(_strip_brackets(host))
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def _validate_base_url(base_url: str) -> str:
    """Reject SSRF-prone literal hosts; append the monitor port if absent.

    Allows http and https only. Performs NO DNS resolution.
    """
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    if host in _BLOCKED_HOSTNAMES:
        raise ValueError(f"blocked host: {host!r}")

    if _is_blocked_ip(host):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    normalized = base_url.rstrip("/")
    if parts.port is None:
        host_token = f"[{host}]" if ":" in host else host
        normalized = f"{parts.scheme}://{host_token}:{_DEFAULT_MONITOR_PORT}"
    return normalized


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class NatsSource:
    """A metrics source backed by the NATS HTTP monitoring endpoints."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, Any],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if http_get is None:
            raise ValueError("http_get transport must be injected")

        base_url = config.get("base_url", "")
        self._base_url = _validate_base_url(base_url)
        self._token_env = config.get("token_env")
        self._http_get = http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"nats:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(self._token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str) -> tuple[int, Any]:
        url = f"{self._base_url}{path}"
        return self._http_get(
            url, params=None, headers=self._headers(), timeout=self._timeout
        )

    def _error_record(self, message: str, raw: Any) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "error",
            "message": message,
            "value": 0.0,
            "labels": {},
            "raw": raw,
        }

    def _record(
        self, message: str, value: float, labels: dict[str, str], ts: float, raw: Any
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": self.name,
            "kind": KIND,
            "level_or_status": "",
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # -- normalization ----------------------------------------------------

    def _normalize_varz(self, varz: Any, ts: float) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not isinstance(varz, dict):
            return records
        server_id = str(varz.get("server_id", ""))
        labels = {"server_id": server_id, "endpoint": "varz"}
        metrics = {
            "connections": _f(varz.get("connections")),
            "total_connections": _f(varz.get("total_connections")),
            "in_msgs": _f(varz.get("in_msgs")),
            "out_msgs": _f(varz.get("out_msgs")),
            "in_bytes": _f(varz.get("in_bytes")),
            "out_bytes": _f(varz.get("out_bytes")),
            "slow_consumers": _f(varz.get("slow_consumers")),
            "subscriptions": _f(varz.get("subscriptions")),
            "mem": _f(varz.get("mem")),
        }
        for metric, value in metrics.items():
            records.append(
                self._record(
                    f"varz {metric}",
                    value,
                    {**labels, "metric": metric},
                    ts,
                    varz,
                )
            )
        return records

    def _normalize_connz(self, connz: Any, ts: float) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not isinstance(connz, dict):
            return records
        server_id = str(connz.get("server_id", ""))
        labels = {"server_id": server_id, "endpoint": "connz"}
        records.append(
            self._record(
                "connz num_connections",
                _f(connz.get("num_connections")),
                {**labels, "metric": "num_connections"},
                ts,
                connz,
            )
        )
        records.append(
            self._record(
                "connz total",
                _f(connz.get("total")),
                {**labels, "metric": "total"},
                ts,
                connz,
            )
        )
        return records

    def _normalize_subsz(self, subsz: Any, ts: float) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not isinstance(subsz, dict):
            return records
        server_id = str(subsz.get("server_id", ""))
        labels = {"server_id": server_id, "endpoint": "subsz"}
        records.append(
            self._record(
                "subsz num_subscriptions",
                _f(subsz.get("num_subscriptions")),
                {**labels, "metric": "num_subscriptions"},
                ts,
                subsz,
            )
        )
        records.append(
            self._record(
                "subsz num_inserts",
                _f(subsz.get("num_inserts")),
                {**labels, "metric": "num_inserts"},
                ts,
                subsz,
            )
        )
        records.append(
            self._record(
                "subsz num_matches",
                _f(subsz.get("num_matches")),
                {**labels, "metric": "num_matches"},
                ts,
                subsz,
            )
        )
        return records

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull varz/connz/subsz and normalize. Never raises.

        ``spec`` may carry ``endpoints`` (a subset of
        ``{"varz", "connz", "subsz"}``) to restrict the scrape.
        """
        requested = spec.get("endpoints") or ("varz", "connz", "subsz")
        ts = time.time()
        records: list[dict[str, Any]] = []

        endpoint_map = {
            "varz": ("/varz", self._normalize_varz),
            "connz": ("/connz", self._normalize_connz),
            "subsz": ("/subsz", self._normalize_subsz),
        }

        for key in requested:
            entry = endpoint_map.get(key)
            if entry is None:
                continue
            path, normalizer = entry
            try:
                status, payload = self._get(path)
            except Exception as exc:
                records.append(
                    self._error_record(
                        f"transport error on {path}: {exc}", {"path": path}
                    )
                )
                continue
            if not (200 <= int(status) < 300):
                records.append(
                    self._error_record(
                        f"http status {status} on {path}", {"status": status}
                    )
                )
                continue
            records.extend(normalizer(payload, ts))

        return records

    def health(self) -> dict[str, Any]:
        """Return ``{"ok": bool, "detail": str}``. Never raises.

        Probes ``/varz`` (always present on the monitoring server).
        """
        try:
            status, payload = self._get("/varz")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}

        ok = 200 <= int(status) < 300 and isinstance(payload, dict)
        if ok:
            version = payload.get("version", "?")
            return {"ok": True, "detail": f"varz ok (nats {version})"}
        return {"ok": False, "detail": f"unhealthy (status {status})"}
