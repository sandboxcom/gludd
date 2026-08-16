"""NomadSource — HashiCorp Nomad HTTP API log/event connector.

Self-contained: no imports from sibling connectors, no package base class.

Talks to a Nomad agent's HTTP API (``{base_url}`` defaulting port 4646):

* ``GET /v1/client/fs/logs/{allocID}``      alloc stdout/stderr log stream
* ``GET /v1/event/stream``                  SSE event stream (line-delimited)
* ``GET /v1/metrics?format=prometheus``     agent metrics (Prometheus text)

The HTTP transport is *injectable*: pass ``config['transport']`` — any callable

    transport(method, url, headers, params) -> Response

where ``Response`` has ``.status_code: int`` and ``.text: str`` (and optionally
``.iter_lines()``). This keeps the connector testable with canned payloads and
free of a hard ``requests``/``httpx`` dependency. If no transport is supplied a
minimal ``urllib`` one is used.

SSRF hardening: Nomad is an internal service, so by default we *block* requests
whose host resolves to a private/loopback/link-local/reserved address unless the
operator opts in with ``config['allow_private'] = True``. The ACL token is read
from the environment variable named by ``config['token_env']`` and sent as the
``X-Nomad-Token`` header — never logged, never taken inline.

Normalized records (one per alloc log line / event / metric sample) follow the
shared schema: ts, source, kind, level_or_status, message, value, labels, raw.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlparse

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import resolved_host_is_blocked


class NomadSSRFError(ValueError):
    """Raised when a target host is blocked by SSRF policy."""


class NomadTransportError(RuntimeError):
    """Raised when the HTTP transport returns a non-2xx response."""


@runtime_checkable
class _Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
    ) -> HttpResponse: ...


class _UrllibResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        import json

        return json.loads(self.text) if self.text else None


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str] | None,
) -> _UrllibResponse:
    # Imported lazily so the module needs no network deps to import/parse.
    import urllib.parse

    import httpx

    full = url
    if params:
        full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            resp = client.request(method, full, headers=headers)
        body = resp.content.decode("utf-8", errors="replace")
        return _UrllibResponse(resp.status_code, body)
    except Exception as exc:  # pragma: no cover - network path not unit-tested
        raise NomadTransportError(f"transport error: {exc}") from exc


def _host_is_private(host: str) -> bool:
    """Return True if the host is a blocked literal or private address.

    Delegates entirely to the canonical
    :func:`general_ludd.security.ssrf.resolved_host_is_blocked` so this
    connector's private/loopback/metadata classification (previously a local
    reimplementation missing the ``not is_global`` catch for TEST-NET /
    documentation ranges and the cloud-metadata NAME blocklist) can never drift
    from the single source of truth. Nomad agents are typically referenced by
    an internal DNS name rather than a literal IP, so this connector already
    accepted DNS-resolution as part of its threat model before this
    consolidation; ``resolved_host_is_blocked`` performs that resolution with a
    bounded timeout and fails CLOSED (unresolvable -> unsafe), matching this
    function's prior behavior.
    """
    return resolved_host_is_blocked(host)


class NomadSource:
    """Nomad HTTP API connector (KIND='logs', also a pipeline source)."""

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: _Transport | None = None,
    ) -> None:
        """Initialize the Nomad source; SSRF is enforced lazily at request time."""
        cfg = config or {}
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError("NomadSource requires config['base_url']")
        self.name = str(cfg.get("name", "nomad"))
        self._base_url = str(base_url).rstrip("/")
        parsed = urlparse(self._base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme {parsed.scheme!r} in base_url")
        if not parsed.hostname:
            raise ValueError("base_url has no host")
        self._host = parsed.hostname
        self._allow_private = bool(cfg.get("allow_private", False))
        self._token_env = cfg.get("token_env")
        config_transport = cfg.get("transport")
        self._transport: _Transport = cast(
            _Transport,
            transport
            if transport is not None
            else config_transport
            if config_transport is not None
            else _urllib_transport,
        )
        # SSRF is enforced LAZILY: construction always succeeds (the
        # contract tests pin this), while _get() re-checks the host before
        # every request — health() surfaces it as ssrf-blocked, query()
        # raises NomadSSRFError. Re-checking per request also protects
        # against DNS rebinding between construction and use.

    # -- SSRF + auth ----------------------------------------------------------
    def _guard_ssrf(self) -> None:
        if self._allow_private:
            return
        if _host_is_private(self._host):
            raise NomadSSRFError(
                f"refusing internal/private host {self._host!r}; set allow_private=True to permit (Nomad is internal)"
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["X-Nomad-Token"] = token
        return headers

    def _get(self, path: str, params: dict[str, str] | None = None) -> HttpResponse:
        self._guard_ssrf()
        url = f"{self._base_url}{path}"
        resp = self._transport("GET", url, self._headers(), params)
        if not (200 <= resp.status_code < 300):
            raise NomadTransportError(f"GET {path} -> HTTP {resp.status_code}")
        return resp

    # -- contract -------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Probe /v1/agent/health. Never raises."""
        try:
            resp = self._get("/v1/agent/health")
        except NomadSSRFError as exc:
            return {"ok": False, "detail": f"ssrf-blocked: {exc}"}
        except Exception as exc:  # health() must never raise
            return {"ok": False, "detail": f"unhealthy: {exc}"}
        return {"ok": True, "detail": f"nomad ok (HTTP {resp.status_code})"}

    # -- normalizers ----------------------------------------------------------
    def _alloc_log_records(self, alloc_id: str, log_type: str, text: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        level = "error" if log_type == "stderr" else "info"
        for line in text.splitlines():
            if not line:
                continue
            records.append(
                {
                    "ts": None,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": level,
                    "message": line,
                    "value": None,
                    "labels": {"alloc_id": alloc_id, "log_type": log_type},
                    "raw": line,
                }
            )
        return records

    def _event_records(self, text: str) -> list[dict[str, Any]]:
        """Parse line-delimited SSE/NDJSON Nomad events into records."""
        records: list[dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Tolerate SSE 'data: {...}' framing as well as bare NDJSON.
            payload = line
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
            if not payload or payload.startswith(":"):
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # A Nomad event-stream frame is {"Index":N,"Events":[{...}]}.
            events = obj.get("Events") if isinstance(obj, dict) else None
            if isinstance(events, list):
                for ev in events:
                    records.append(self._one_event_record(ev, raw=payload))
            else:
                records.append(self._one_event_record(obj, raw=payload))
        return records

    def _one_event_record(self, ev: Any, raw: str) -> dict[str, Any]:
        if not isinstance(ev, dict):
            return {
                "ts": None,
                "source": self.name,
                "kind": "pipeline",
                "level_or_status": "info",
                "message": str(ev),
                "value": None,
                "labels": {},
                "raw": raw,
            }
        topic = ev.get("Topic", "")
        etype = ev.get("Type", "")
        labels: dict[str, Any] = {"topic": topic, "type": etype}
        key = ev.get("Key")
        if key is not None:
            labels["key"] = key
        ns = ev.get("Namespace")
        if ns is not None:
            labels["namespace"] = ns
        return {
            "ts": None,
            "source": self.name,
            "kind": "pipeline",
            "level_or_status": str(etype) if etype else "info",
            "message": f"{topic}:{etype}".strip(":"),
            "value": None,
            "labels": labels,
            "raw": raw,
        }

    def _metric_records(self, text: str) -> list[dict[str, Any]]:
        """Parse Prometheus-format metrics text into records."""
        records: list[dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # name{labels} value   OR   name value
            try:
                left, _, value_str = line.rpartition(" ")
                if not left:
                    continue
                value = float(value_str)
            except ValueError:
                continue
            labels: dict[str, Any] = {}
            metric_name = left
            if "{" in left and left.endswith("}"):
                metric_name, _, label_blob = left.partition("{")
                label_blob = label_blob[:-1]
                for pair in label_blob.split(","):
                    pair = pair.strip()
                    if not pair or "=" not in pair:
                        continue
                    k, _, v = pair.partition("=")
                    labels[k.strip()] = v.strip().strip('"')
            records.append(
                {
                    "ts": None,
                    "source": self.name,
                    "kind": "metrics",
                    "level_or_status": "gauge",
                    "message": metric_name,
                    "value": value,
                    "labels": labels,
                    "raw": line,
                }
            )
        return records

    # -- query ----------------------------------------------------------------
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch and normalize Nomad data.

        spec['type'] selects the endpoint:
          'logs'    -> GET /v1/client/fs/logs/{alloc_id}
                       spec: alloc_id (req), log_type ('stdout'|'stderr'),
                             task, origin, offset, plain
          'events'  -> GET /v1/event/stream  (parsed line-delimited)
          'metrics' -> GET /v1/metrics?format=prometheus
        """
        spec = spec or {}
        qtype = spec.get("type", "logs")

        if qtype == "logs":
            alloc_id = spec.get("alloc_id")
            if not alloc_id:
                raise ValueError("logs query requires spec['alloc_id']")
            log_type = str(spec.get("log_type", "stdout"))
            params: dict[str, str] = {"type": log_type, "plain": "true"}
            for key in ("task", "origin", "offset"):
                if spec.get(key) is not None:
                    params[key] = str(spec[key])
            resp = self._get(f"/v1/client/fs/logs/{alloc_id}", params)
            return self._alloc_log_records(str(alloc_id), log_type, resp.text)

        if qtype == "events":
            params = {}
            if spec.get("topic") is not None:
                params["topic"] = str(spec["topic"])
            resp = self._get("/v1/event/stream", params or None)
            records = self._event_records(resp.text)
            limit = spec.get("limit")
            if limit is not None:
                records = records[: int(limit)]
            return records

        if qtype == "metrics":
            resp = self._get("/v1/metrics", {"format": "prometheus"})
            return self._metric_records(resp.text)

        raise ValueError(f"unknown query type {qtype!r}")
