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

import logging
import os
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

import httpx

from general_ludd.security.ssrf import is_url_blocked

# Injectable transport signature: (url, params, headers, timeout) -> (status, json)
HttpGet = Callable[..., "tuple[int, object]"]

KIND = "metrics"

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_MONITOR_PORT = 8222


def _default_http_get(
    url: str,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, object]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the ``(url, params, headers, timeout) -> (status, json)`` contract.
    Only ``http``/``https`` are allowed; the request is bounded by an explicit
    timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if params:
        sep = "&" if parsed.query else "?"
        url = f"{url}{sep}{urlencode(params, doseq=True)}"
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url, headers=headers or {})
    status = resp.status_code
    parsed_body: object = resp.json() if resp.content else {}
    return status, parsed_body


def _validate_base_url(base_url: str) -> str:
    """Reject SSRF-prone literal hosts; append the monitor port if absent.

    Allows http and https only. Performs NO DNS resolution. The
    private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked`.
    """
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    normalized = base_url.rstrip("/")
    if parts.port is None:
        host_token = f"[{host}]" if ":" in host else host
        normalized = f"{parts.scheme}://{host_token}:{_DEFAULT_MONITOR_PORT}"
    return normalized


def _f(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


class NatsSource:
    """A metrics source backed by the NATS HTTP monitoring endpoints."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = str(config.get("base_url", ""))
        self._base_url = _validate_base_url(base_url)
        self._token_env = config.get("token_env")
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"nats:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str) -> tuple[int, object]:
        url = f"{self._base_url}{path}"
        return self._http_get(
            url, params=None, headers=self._headers(), timeout=self._timeout
        )

    def _error_record(self, message: str, raw: object) -> dict[str, object]:
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
        self, message: str, value: float, labels: dict[str, str], ts: float, raw: object
    ) -> dict[str, object]:
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

    def _normalize_varz(self, varz: object, ts: float) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
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

    def _normalize_connz(self, connz: object, ts: float) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
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

    def _normalize_subsz(self, subsz: object, ts: float) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
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

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Pull varz/connz/subsz and normalize. Never raises.

        ``spec`` may carry ``endpoints`` (a subset of
        ``{"varz", "connz", "subsz"}``) to restrict the scrape.
        """
        requested = spec.get("endpoints") or ("varz", "connz", "subsz")
        if not isinstance(requested, (list, tuple, set, frozenset)):
            requested = ("varz", "connz", "subsz")
        ts = time.time()
        records: list[dict[str, object]] = []

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

    def health(self) -> dict[str, object]:
        """Return ``{"ok": bool, "detail": str}``. Never raises.

        Probes ``/varz`` (always present on the monitoring server).
        """
        try:
            status, payload = self._get("/varz")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}

        ok = 200 <= int(status) < 300 and isinstance(payload, dict)
        if ok:
            assert isinstance(payload, dict)
            version = payload.get("version", "?")
            return {"ok": True, "detail": f"varz ok (nats {version})"}
        return {"ok": False, "detail": f"unhealthy (status {status})"}
