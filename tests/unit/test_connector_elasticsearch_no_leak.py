from __future__ import annotations

import pytest

from general_ludd.connectors.elasticsearch import ElasticsearchSource


def _error_http_request(secret: str):
    def _boom(method, url, headers, body):
        raise RuntimeError(f"connect to https://es.internal:9200?token={secret}")

    return _boom


def _oserror_http_request():
    def _boom(method, url, headers, body):
        raise OSError("connection refused")

    return _boom


def test_query_never_raises_returns_error_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ES_TOKEN", "secret-token")
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "token_env": "ES_TOKEN"},
        http_request=_oserror_http_request(),
    )
    records = src.query({})
    assert isinstance(records, list)
    assert len(records) > 0
    assert records[0]["message"] == "query failed"


def test_query_error_record_no_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ES_TOKEN", "secret-token")
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "token_env": "ES_TOKEN"},
        http_request=_error_http_request("SEKRET"),
    )
    records = src.query({})
    assert isinstance(records, list)
    for record in records:
        msg = str(record.get("message", ""))
        raw = str(record.get("raw", ""))
        assert "SEKRET" not in msg
        assert "token=" not in msg
        assert "SEKRET" not in raw


def test_health_no_exception_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ES_TOKEN", "secret-token")
    src = ElasticsearchSource(
        {"base_url": "https://es.example.com", "index": "logs-*", "token_env": "ES_TOKEN"},
        http_request=_error_http_request("SEKRET"),
    )
    h = src.health()
    assert h["ok"] is False
    assert h["error"] == "health check failed"
    assert "SEKRET" not in str(h.get("error", ""))
