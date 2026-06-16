"""Unit tests for the Elasticsearch observability connector.

All transport is MOCKED — no network is touched. The connector accepts an
injectable ``http_request`` callable, so each test feeds a canned
``(status, json)`` response and asserts on normalization, time parsing,
logs-vs-traces kind detection, health mapping, and SSRF rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.connectors.elasticsearch import (
    ElasticsearchSource,
    SSRFError,
)

# --- canned payloads --------------------------------------------------------


def _logs_search_payload() -> dict[str, Any]:
    """A plain logs _search response (no trace.id on any hit)."""
    return {
        "took": 5,
        "timed_out": False,
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "hits": [
                {
                    "_index": "logs-app-000001",
                    "_id": "abc123",
                    "_source": {
                        "@timestamp": "2026-06-12T10:00:00.000Z",
                        "level": "ERROR",
                        "message": "db connection refused",
                        "service": {"name": "billing"},
                    },
                },
                {
                    "_index": "logs-app-000001",
                    "_id": "def456",
                    "_source": {
                        "@timestamp": "2026-06-12T10:00:01.000Z",
                        "log": {"level": "info"},
                        "message": "retrying",
                        "service": {"name": "billing"},
                    },
                },
            ],
        },
    }


def _traces_search_payload() -> dict[str, Any]:
    """A _search response whose hits carry trace.id / span.id (APM)."""
    return {
        "took": 3,
        "timed_out": False,
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_index": "apm-traces-000002",
                    "_id": "span-1",
                    "_source": {
                        "@timestamp": "2026-06-12T10:05:00.000Z",
                        "message": "GET /checkout 200",
                        "trace": {"id": "trace-xyz-789"},
                        "span": {"id": "span-1"},
                        "service": {"name": "gateway"},
                    },
                }
            ],
        },
    }


def _cluster_health_payload(status: str) -> dict[str, Any]:
    return {
        "cluster_name": "es",
        "status": status,
        "number_of_nodes": 3,
        "active_shards": 10,
    }


class _Transport:
    """Records calls and returns scripted ``(status, json)`` tuples by URL substring."""

    def __init__(self, routes: list[tuple[str, int, dict[str, Any]]]) -> None:
        self._routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        for needle, status, payload in self._routes:
            if needle in url:
                return status, payload
        raise AssertionError(f"no canned route for url {url!r}")


@pytest.fixture
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ES_API_KEY", "secret-api-key")
    return "ES_API_KEY"


# --- construction / metadata ------------------------------------------------


def test_kind_and_name(api_key_env: str) -> None:
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env}
    )
    assert src.KIND == "logs"
    assert isinstance(src.name, str)
    assert src.name


# --- query(): logs normalization -------------------------------------------


def test_query_normalizes_logs_hits(api_key_env: str) -> None:
    transport = _Transport(
        [("/_search", 200, _logs_search_payload())]
    )
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-app-*", "api_key_env": api_key_env},
        http_request=transport,
    )

    records = src.query({"query": "level:ERROR", "size": 50})

    assert len(records) == 2
    first = records[0]
    # POST to {base_url}/{index}/_search
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://es.example.com/logs-app-*/_search"

    # normalized shape
    assert set(first) >= {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert first["kind"] == "logs"
    assert first["source"] == src.name
    assert first["level_or_status"] == "ERROR"
    assert first["message"] == "db connection refused"
    # ts parsed to epoch-ish float (not the raw string)
    assert isinstance(first["ts"], float)
    assert first["ts"] > 0
    # labels carry index, _id, service.name
    assert first["labels"]["index"] == "logs-app-000001"
    assert first["labels"]["_id"] == "abc123"
    assert first["labels"]["service.name"] == "billing"
    # no trace.id present on logs hit
    assert "trace.id" not in first["labels"]
    # raw is the full hit
    assert first["raw"]["_id"] == "abc123"

    # second hit derives level from log.level when top-level level absent
    assert records[1]["level_or_status"] == "info"


def test_query_auth_header_apikey(api_key_env: str) -> None:
    transport = _Transport([("/_search", 200, _logs_search_payload())])
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env},
        http_request=transport,
    )
    src.query({"query": "*"})
    auth = transport.calls[0]["headers"].get("Authorization")
    assert auth == "ApiKey secret-api-key"


def test_query_auth_header_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ES_TOKEN", "bearer-tok")
    transport = _Transport([("/_search", 200, _logs_search_payload())])
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "token_env": "ES_TOKEN"},
        http_request=transport,
    )
    src.query({"query": "*"})
    assert transport.calls[0]["headers"].get("Authorization") == "Bearer bearer-tok"


def test_query_dsl_passthrough(api_key_env: str) -> None:
    """A spec with an explicit dsl dict is sent as the query body."""
    transport = _Transport([("/_search", 200, _logs_search_payload())])
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env},
        http_request=transport,
    )
    src.query({"dsl": {"match": {"level": "ERROR"}}, "size": 10})
    import json as _json

    body = transport.calls[0]["body"]
    assert body is not None
    sent = _json.loads(body)
    assert sent["query"] == {"match": {"level": "ERROR"}}
    assert sent["size"] == 10


# --- query(): traces detection ---------------------------------------------


def test_query_detects_traces_kind(api_key_env: str) -> None:
    transport = _Transport([("/_search", 200, _traces_search_payload())])
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "apm-*", "api_key_env": api_key_env},
        http_request=transport,
    )

    records = src.query({"query": "*"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "traces"
    # trace.id surfaced into labels for façade associate-by-trace_id
    assert rec["labels"]["trace.id"] == "trace-xyz-789"
    assert rec["labels"]["service.name"] == "gateway"


# --- health() ---------------------------------------------------------------


@pytest.mark.parametrize("status", ["green", "yellow"])
def test_health_ok_on_green_yellow(api_key_env: str, status: str) -> None:
    transport = _Transport(
        [("/_cluster/health", 200, _cluster_health_payload(status))]
    )
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env},
        http_request=transport,
    )
    h = src.health()
    assert h["ok"] is True
    assert h["status"] == status
    # GET against the cluster health endpoint
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"] == "https://es.example.com/_cluster/health"


def test_health_not_ok_on_red(api_key_env: str) -> None:
    transport = _Transport(
        [("/_cluster/health", 200, _cluster_health_payload("red"))]
    )
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env},
        http_request=transport,
    )
    h = src.health()
    assert h["ok"] is False
    assert h["status"] == "red"


def test_health_never_raises(api_key_env: str) -> None:
    def _boom(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("transport exploded")

    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "api_key_env": api_key_env},
        http_request=_boom,
    )
    h = src.health()  # must NOT raise
    assert h["ok"] is False
    assert "error" in h


# --- SSRF -------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:9200",
        "http://localhost:9200",
        "http://[::1]:9200",
        "http://10.0.0.5:9200",
        "http://192.168.1.10:9200",
        "http://172.16.0.1:9200",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://0.0.0.0:9200",
    ],
)
def test_ssrf_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(SSRFError):
        ElasticsearchSource({"base_url": bad_url, "index": "logs-*"})


def test_ssrf_metadata_hostname_rejected() -> None:
    # literal metadata hostname blocked without DNS
    with pytest.raises(SSRFError):
        ElasticsearchSource(
            {"base_url": "http://metadata.google.internal/", "index": "logs-*"}
        )


def test_public_base_url_allowed(api_key_env: str) -> None:
    # a public host constructs fine
    src = ElasticsearchSource(
        {"base_url": "https://es.public.example.com:9200", "index": "logs-*", "api_key_env": api_key_env}
    )
    assert src.name
