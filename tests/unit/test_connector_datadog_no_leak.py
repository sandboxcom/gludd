from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.datadog import DatadogSource


def _recursive_str(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps({k: _recursive_str(v) for k, v in value.items()})
    if isinstance(value, list):
        return json.dumps([_recursive_str(v) for v in value])
    return str(value)


class _MockTransport:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[int, Any]:
        raise self._exc


def test_query_error_record_no_exception_text() -> None:
    exc = RuntimeError("connect to https://api.datadoghq.com?api_key=SEKRET")
    transport = _MockTransport(exc)
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=transport)
    records = src.query({"mode": "logs", "query": "*"})

    assert len(records) == 1
    record = records[0]
    flat = _recursive_str(record)
    assert "SEKRET" not in flat
    assert "api_key=" not in flat


def test_query_error_record_has_static_message() -> None:
    exc = RuntimeError("connect to https://api.datadoghq.com?api_key=SEKRET")
    transport = _MockTransport(exc)
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=transport)
    records = src.query({"mode": "logs", "query": "*"})

    assert len(records) == 1
    assert records[0]["message"] == "transport error"


def test_query_error_record_preserves_url_context() -> None:
    exc = RuntimeError("network is down")
    transport = _MockTransport(exc)
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=transport)
    records = src.query({"mode": "logs", "query": "*"})

    assert len(records) == 1
    assert records[0]["raw"] == {"url": "https://api.datadoghq.com/api/v2/logs/events/search"}


def test_metrics_error_also_scrubbed() -> None:
    exc = RuntimeError("connect to https://api.datadoghq.com?api_key=SEKRET&app_key=abc")
    transport = _MockTransport(exc)
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=transport)
    records = src.query({"mode": "metrics", "query": "avg:system.cpu.user{*}"})

    assert len(records) == 1
    assert records[0]["message"] == "transport error"
    assert records[0]["raw"] == {"url": "https://api.datadoghq.com/api/v1/query"}
    flat = _recursive_str(records[0])
    assert "SEKRET" not in flat
    assert "api_key=" not in flat
    assert "app_key=" not in flat
