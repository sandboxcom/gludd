"""Self-contained RabbitMQ management-API observability connector.

Pulls cluster health from the RabbitMQ management HTTP API
(``/api/overview``, ``/api/queues``, ``/api/nodes``) and normalizes the
operationally-interesting figures (queue depth, unacked messages, node memory)
into flat metric records.

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url`` (no DNS resolution, no network at
  construction time). Plain ``http`` is allowed (the management UI commonly
  runs on internal HTTP).
* Basic-auth credentials are read from the environment via ``user_env`` /
  ``password_env`` — never hard-coded, never logged.
* The HTTP transport is *injectable* so the connector is driven entirely from
  mocked responses in tests (no real network, no DNS, no shell, no subprocess).
* ``health()`` never raises (reports failure in ``{"ok", "detail"}``).
  ``query()`` never raises: transport / protocol failures become a single
  normalized error record.
* Every backend call is time-bound (``timeout`` passed to the transport).

The management API listens on port ``15672`` by default; if ``base_url`` has no
explicit port we append ``:15672``.

Record shape (one dict per sample)::

    {
        "ts": float,                 # scrape time (unix seconds)
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # human-readable line
        "value": float,              # numeric payload
        "labels": dict[str, str],    # {queue, vhost, node, metric, ...}
        "raw": Any,                  # original API object
    }

"""

from __future__ import annotations

import base64
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
_DEFAULT_MGMT_PORT = 15672


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
    """Reject SSRF-prone literal hosts; append the mgmt port if absent.

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
        # Re-bracket IPv6 literals when appending the port.
        host_token = f"[{host}]" if ":" in host else host
        normalized = f"{parts.scheme}://{host_token}:{_DEFAULT_MGMT_PORT}"
    return normalized


def _f(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


class RabbitMqSource:
    """A metrics source backed by the RabbitMQ management HTTP API."""

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
        self._user_env = config.get("user_env")
        self._password_env = config.get("password_env")
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"rabbitmq:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        user = os.environ.get(str(self._user_env)) if self._user_env else None
        password = os.environ.get(str(self._password_env)) if self._password_env else None
        if user is not None and password is not None:
            token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
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

    def _normalize_queues(
        self, queues: object, ts: float
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if not isinstance(queues, list):
            return records
        for q in queues:
            if not isinstance(q, dict):
                continue
            name = str(q.get("name", ""))
            vhost = str(q.get("vhost", ""))
            base_labels = {"queue": name, "vhost": vhost}
            depth = _f(q.get("messages"))
            ready = _f(q.get("messages_ready"))
            unacked = _f(q.get("messages_unacknowledged"))
            memory = _f(q.get("memory"))
            records.append(
                self._record(
                    f"queue {vhost}/{name} depth",
                    depth,
                    {**base_labels, "metric": "queue_depth"},
                    ts,
                    q,
                )
            )
            records.append(
                self._record(
                    f"queue {vhost}/{name} ready",
                    ready,
                    {**base_labels, "metric": "queue_ready"},
                    ts,
                    q,
                )
            )
            records.append(
                self._record(
                    f"queue {vhost}/{name} unacked",
                    unacked,
                    {**base_labels, "metric": "queue_unacked"},
                    ts,
                    q,
                )
            )
            records.append(
                self._record(
                    f"queue {vhost}/{name} memory",
                    memory,
                    {**base_labels, "metric": "queue_memory"},
                    ts,
                    q,
                )
            )
        return records

    def _normalize_nodes(self, nodes: object, ts: float) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if not isinstance(nodes, list):
            return records
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nname = str(node.get("name", ""))
            base_labels = {"node": nname}
            records.append(
                self._record(
                    f"node {nname} mem_used",
                    _f(node.get("mem_used")),
                    {**base_labels, "metric": "node_mem_used"},
                    ts,
                    node,
                )
            )
            records.append(
                self._record(
                    f"node {nname} fd_used",
                    _f(node.get("fd_used")),
                    {**base_labels, "metric": "node_fd_used"},
                    ts,
                    node,
                )
            )
            records.append(
                self._record(
                    f"node {nname} sockets_used",
                    _f(node.get("sockets_used")),
                    {**base_labels, "metric": "node_sockets_used"},
                    ts,
                    node,
                )
            )
            running = 1.0 if node.get("running") else 0.0
            records.append(
                self._record(
                    f"node {nname} running",
                    running,
                    {**base_labels, "metric": "node_running"},
                    ts,
                    node,
                )
            )
        return records

    def _normalize_overview(self, overview: object, ts: float) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if not isinstance(overview, dict):
            return records
        totals = overview.get("queue_totals")
        if isinstance(totals, dict):
            records.append(
                self._record(
                    "cluster total messages",
                    _f(totals.get("messages")),
                    {"metric": "cluster_messages"},
                    ts,
                    totals,
                )
            )
            records.append(
                self._record(
                    "cluster total unacked",
                    _f(totals.get("messages_unacknowledged")),
                    {"metric": "cluster_unacked"},
                    ts,
                    totals,
                )
            )
        obj_totals = overview.get("object_totals")
        if isinstance(obj_totals, dict):
            records.append(
                self._record(
                    "cluster connections",
                    _f(obj_totals.get("connections")),
                    {"metric": "cluster_connections"},
                    ts,
                    obj_totals,
                )
            )
        return records

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Pull overview/queues/nodes and normalize. Never raises.

        ``spec`` may carry ``endpoints`` (a subset of
        ``{"overview", "queues", "nodes"}``) to restrict the scrape.
        """
        requested = spec.get("endpoints") or ("overview", "queues", "nodes")
        if not isinstance(requested, (list, tuple, set, frozenset)):
            requested = ("overview", "queues", "nodes")
        ts = time.time()
        records: list[dict[str, object]] = []

        endpoint_map = {
            "overview": ("/api/overview", self._normalize_overview),
            "queues": ("/api/queues", self._normalize_queues),
            "nodes": ("/api/nodes", self._normalize_nodes),
        }

        for key in requested:
            spec_entry = endpoint_map.get(key)
            if spec_entry is None:
                continue
            path, normalizer = spec_entry
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

        Probes ``/api/overview`` (cheap, always present on the mgmt API).
        """
        try:
            status, payload = self._get("/api/overview")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}

        ok = 200 <= int(status) < 300 and isinstance(payload, dict)
        if ok:
            assert isinstance(payload, dict)
            version = payload.get("rabbitmq_version", "?")
            return {"ok": True, "detail": f"overview ok (rabbitmq {version})"}
        return {"ok": False, "detail": f"unhealthy (status {status})"}
