"""Elasticsearch observability connector.

A self-contained source that reads logs (and, via APM indices, traces) from an
Elasticsearch ``_search`` endpoint and normalizes each hit into the connector
record shape consumed by the observability façade.

Design constraints (deliberate):

* **Injectable transport.** All HTTP goes through an ``http_request`` callable
  ``(method, url, headers, body) -> (status, json)``. Tests inject a mock; the
  default uses ``httpx`` with a bounded timeout and ``shell=True`` nowhere.
* **SSRF literal-host block.** ``base_url`` is validated at construction time
  against loopback / private / link-local / metadata addresses. The check is
  performed on the *literal* host only — no DNS resolution is done (resolving
  attacker-controlled names is itself an SSRF vector), so a hostname that is not
  a literal IP and not a known-bad metadata name is permitted.
* **Auth from env.** The bearer/api-key value is read from an environment
  variable named in the config (``api_key_env`` -> ``ApiKey <v>``,
  ``token_env`` -> ``Bearer <v>``). Secrets never appear in config.
* **health() never raises.** It always returns a dict.

This module imports nothing from the rest of the package (no base class, no
``__init__`` re-exports, no sibling connectors) so it can be developed and
tested in isolation.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Literal, TypedDict, cast
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Transport contract: (method, url, headers, body) -> (status_code, json_dict)
HttpRequest = Callable[[str, str, Mapping[str, str], "bytes | None"], "tuple[int, dict[str, object]]"]

RecordKind = Literal["logs", "traces"]


class ElasticsearchConfig(TypedDict, total=False):
    base_url: str
    index: str
    api_key_env: str
    token_env: str


class ElasticsearchQuerySpec(TypedDict, total=False):
    dsl: Mapping[str, object]
    query: str
    size: int
    time_field: str
    start: object
    end: object


class ElasticsearchHit(TypedDict, total=False):
    _id: str
    _index: str
    _source: Mapping[str, object]
    _score: float


_DEFAULT_TIMEOUT_SECONDS = 10.0


class ElasticsearchConfigError(ValueError):
    """Raised when the connector config is malformed."""


def _validate_base_url(base_url: str) -> str:
    """Validate ``base_url`` against SSRF rules and return it normalized (no trailing slash).

    No DNS resolution is performed. A literal IP host is checked against the
    blocked ranges; a known-bad metadata hostname is rejected by name; any other
    hostname is allowed (the transport will resolve it at call time).
    """
    if not base_url or not isinstance(base_url, str):
        raise ElasticsearchConfigError("base_url is required")

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ElasticsearchConfigError(f"base_url must be http(s), got scheme {parts.scheme!r}")

    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        host = parts.hostname or ""
        raise SSRFError(f"base_url host {host!r} is blocked")

    return base_url.rstrip("/")


def _default_http_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, object]]:
    """Real transport — httpx with a bounded timeout. Imported lazily.

    Kept tiny and side-effect-light so tests never need it (they inject a mock).
    """
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=dict(headers),
        content=body,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    return resp.status_code, cast("dict[str, object]", payload if isinstance(payload, dict) else {})


def _parse_timestamp(value: object) -> float:
    """Parse an ES time value into an epoch float. Best-effort; 0.0 on failure."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        # ES epoch_millis vs epoch_second heuristic
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if isinstance(value, str):
        s = value.strip()
        # ISO-8601, tolerate a trailing 'Z'.
        try:
            normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _dig(source: Mapping[str, object], dotted: str) -> object:
    """Read a possibly-nested value addressed either as 'a.b' nesting or a flat 'a.b' key.

    Returns object: dotted-path lookup into arbitrary JSON yields an unknown shape;
    callers narrow via isinstance.
    """
    if dotted in source:
        return source[dotted]
    cur: object = source
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


class ElasticsearchSource:
    """Read logs/traces from an Elasticsearch ``_search`` endpoint."""

    #: Primary kind this source emits; individual records may be 'traces' when a
    #: hit carries trace/span ids (APM data served from the same source).
    KIND: RecordKind = "logs"

    def __init__(
        self,
        config: ElasticsearchConfig,
        http_request: HttpRequest | None = None,
    ) -> None:
        base_url = config.get("base_url")
        index = config.get("index")
        if not index or not isinstance(index, str):
            raise ElasticsearchConfigError("index is required")

        # SSRF validation happens at construction — fail fast, before any call.
        self._base_url = _validate_base_url(cast("str", base_url))
        self._index = index
        self._api_key_env = config.get("api_key_env")
        self._token_env = config.get("token_env")
        self._http_request: HttpRequest = http_request or _default_http_request

        host = urlsplit(self._base_url).hostname or self._base_url
        self.name: str = f"elasticsearch:{host}/{index}"

    # -- auth ----------------------------------------------------------------

    def _auth_header(self) -> dict[str, str]:
        """Build the Authorization header from the configured env var.

        ``api_key_env`` -> ``ApiKey <value>``; ``token_env`` -> ``Bearer <value>``.
        Missing env var -> no header (caller may be an open cluster).
        """
        import os

        if self._api_key_env:
            value = os.environ.get(self._api_key_env)
            if value:
                return {"Authorization": f"ApiKey {value}"}
        if self._token_env:
            value = os.environ.get(self._token_env)
            if value:
                return {"Authorization": f"Bearer {value}"}
        return {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self._auth_header())
        return headers

    # -- error record --------------------------------------------------------

    def _error_record(self, message: str, raw: object = None) -> dict[str, object]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error",
            "message": message,
            "value": None,
            "labels": {},
            "raw": raw,
        }

    # -- health --------------------------------------------------------------

    def health(self) -> dict[str, object]:
        """GET ``_cluster/health``; ok when status is green or yellow. Never raises."""
        url = f"{self._base_url}/_cluster/health"
        try:
            status, payload = self._http_request("GET", url, self._headers(), None)
        except Exception:  # health must never propagate
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "error": "health check failed", "source": self.name}

        if status >= 400:
            return {"ok": False, "error": f"http {status}", "status": None, "source": self.name}

        cluster_status = payload.get("status") if isinstance(payload, Mapping) else None
        ok = cluster_status in ("green", "yellow")
        return {"ok": ok, "status": cluster_status, "source": self.name}

    # -- query ---------------------------------------------------------------

    def _build_search_body(self, spec: ElasticsearchQuerySpec) -> dict[str, object]:
        """Translate a query spec into an ES ``_search`` request body."""
        body: dict[str, object] = {}

        dsl = spec.get("dsl")
        query_str = spec.get("query")
        if isinstance(dsl, Mapping):
            body["query"] = dict(dsl)
        elif isinstance(query_str, str) and query_str and query_str != "*":
            body["query"] = {"query_string": {"query": query_str}}
        else:
            body["query"] = {"match_all": {}}

        size = spec.get("size")
        if isinstance(size, int):
            body["size"] = size

        # Optional time bound on the configured time field.
        time_field = spec.get("time_field") or "@timestamp"
        start = spec.get("start")
        end = spec.get("end")
        if start is not None or end is not None:
            rng: dict[str, object] = {}
            if start is not None:
                rng["gte"] = start
            if end is not None:
                rng["lte"] = end
            base_query = body["query"]
            body["query"] = {
                "bool": {
                    "must": [base_query],
                    "filter": [{"range": {time_field: rng}}],
                }
            }
        return body

    def query(self, spec: ElasticsearchQuerySpec) -> list[dict[str, object]]:
        """Issue an ES ``_search`` and return normalized records."""
        url = f"{self._base_url}/{self._index}/_search"
        body = self._build_search_body(spec)
        encoded = json.dumps(body).encode("utf-8")

        try:
            status, payload = self._http_request("POST", url, self._headers(), encoded)
        except Exception:
            logger.warning("elasticsearch query failed", exc_info=True)
            return [self._error_record("query failed")]

        if status >= 400:
            logger.warning("elasticsearch _search returned http %d", status)
            return [self._error_record("query failed")]

        time_field = spec.get("time_field") or "@timestamp"
        hits = []
        if isinstance(payload, Mapping):
            hits_block = payload.get("hits")
            if isinstance(hits_block, Mapping):
                inner = hits_block.get("hits")
                if isinstance(inner, list):
                    hits = inner

        return [
            self._normalize_hit(cast("ElasticsearchHit", hit), time_field)
            for hit in hits
            if isinstance(hit, Mapping)
        ]

    def _normalize_hit(self, hit: ElasticsearchHit, time_field: str) -> dict[str, object]:
        source = hit.get("_source")
        src_map: Mapping[str, object] = source if isinstance(source, Mapping) else {}

        ts = _parse_timestamp(_dig(src_map, time_field))

        # level_or_status: prefer top-level `level`, fall back to `log.level`.
        level = src_map.get("level")
        if level is None:
            level = _dig(src_map, "log.level")

        message = src_map.get("message")

        trace_id = _dig(src_map, "trace.id")
        span_id = _dig(src_map, "span.id")
        kind: RecordKind = "traces" if (trace_id is not None or span_id is not None) else "logs"

        labels: dict[str, object] = {
            "index": hit.get("_index"),
            "_id": hit.get("_id"),
        }
        service_name = _dig(src_map, "service.name")
        if service_name is not None:
            labels["service.name"] = service_name
        if trace_id is not None:
            # surfaced for the façade's associate-by-trace_id
            labels["trace.id"] = trace_id
        if span_id is not None:
            # surfaced for the façade's associate-by-span_id
            labels["span.id"] = span_id

        return {
            "ts": ts,
            "source": self.name,
            "kind": kind,
            "level_or_status": level,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": dict(hit),
        }
