"""Unit tests for InfluxDbSource — mocked transport, no network."""

from __future__ import annotations

from typing import Any

import pytest

import general_ludd.connectors.influxdb as influxdb_module
from general_ludd.connectors.influxdb import ConnectorConfigError, InfluxDbSource


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _MockTransport:
    def __init__(self, response: _Resp | None = None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: Any = None,
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "data": data,
                "timeout": timeout,
            }
        )
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        return self.response


FLUX_PAYLOAD = {
    "tables": [
        {
            "records": [
                {
                    "values": {
                        "_time": "2026-06-15T00:00:00Z",
                        "_value": 12.5,
                        "_measurement": "cpu",
                        "host": "node-1",
                        "region": "us-east",
                        "result": "_result",
                        "table": 0,
                    }
                }
            ]
        }
    ]
}

INFLUXQL_PAYLOAD = {
    "results": [
        {
            "series": [
                {
                    "name": "mem",
                    "tags": {"host": "node-2"},
                    "columns": ["time", "used"],
                    "values": [["2026-06-15T01:00:00Z", 88.0]],
                }
            ]
        }
    ]
}

FLUX_CONFIG = {
    "name": "ix",
    "base_url": "https://influx.example.com",
    "dialect": "flux",
    "token_env": "INFLUX_TOKEN",
    "org": "acme",
}
IQL_CONFIG = {
    "name": "ix",
    "base_url": "https://influx.example.com",
    "dialect": "influxql",
    "token_env": "INFLUX_TOKEN",
    "database": "metrics",
}


def test_kind_is_metrics() -> None:
    assert InfluxDbSource.KIND == "metrics"


def test_public_name_callback_and_query_alias_compatibility() -> None:
    calls: list[tuple[str, str]] = []

    def transport(method: str, url: str, **_: object) -> tuple[int, object]:
        calls.append((method, url))
        return 200, FLUX_PAYLOAD

    source_type = influxdb_module.InfluxDBSource
    src = source_type(
        FLUX_CONFIG,
        transport=transport,
        environ={"INFLUX_TOKEN": "tok-9"},
    )

    records = src.query({"query": 'from(bucket:"b")'})

    assert len(records) == 1
    assert records[0]["message"] == "cpu"
    assert calls == [("POST", "https://influx.example.com/api/v2/query")]


def test_flux_auth_header_and_post() -> None:
    t = _MockTransport(_Resp(200, FLUX_PAYLOAD))
    src = InfluxDbSource(FLUX_CONFIG, t, environ={"INFLUX_TOKEN": "tok-9"})
    src.query({"flux": 'from(bucket:"b")'})
    call = t.calls[-1]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v2/query")
    assert call["headers"]["Authorization"] == "Token tok-9"
    assert call["data"] == 'from(bucket:"b")'
    assert call["params"]["org"] == "acme"


def test_influxql_get_query() -> None:
    t = _MockTransport(_Resp(200, INFLUXQL_PAYLOAD))
    src = InfluxDbSource(IQL_CONFIG, t, environ={"INFLUX_TOKEN": "tok-9"})
    src.query({"q": "SELECT used FROM mem"})
    call = t.calls[-1]
    assert call["method"] == "GET"
    assert call["url"].endswith("/query")
    assert call["params"]["q"] == "SELECT used FROM mem"
    assert call["params"]["db"] == "metrics"


def test_missing_base_url_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        InfluxDbSource(
            {"name": "ix", "dialect": "flux", "token_env": "INFLUX_TOKEN"},
            _MockTransport(_Resp(200, FLUX_PAYLOAD)),
            environ={"INFLUX_TOKEN": "t"},
        )


def test_unset_env_var_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        InfluxDbSource(FLUX_CONFIG, _MockTransport(_Resp(200, FLUX_PAYLOAD)), environ={})


def test_flux_normalization() -> None:
    src = InfluxDbSource(FLUX_CONFIG, _MockTransport(_Resp(200, FLUX_PAYLOAD)), environ={"INFLUX_TOKEN": "t"})
    records = src.query({"flux": "x"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "metrics"
    assert rec["ts"] == "2026-06-15T00:00:00Z"
    assert rec["value"] == 12.5
    assert rec["message"] == "cpu"
    assert rec["labels"] == {"host": "node-1", "region": "us-east"}
    assert rec["source"] == "ix"


def test_influxql_normalization() -> None:
    src = InfluxDbSource(IQL_CONFIG, _MockTransport(_Resp(200, INFLUXQL_PAYLOAD)), environ={"INFLUX_TOKEN": "t"})
    records = src.query({"q": "SELECT used FROM mem"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "metrics"
    assert rec["ts"] == "2026-06-15T01:00:00Z"
    assert rec["value"] == 88.0
    assert rec["message"] == "mem"
    assert rec["labels"] == {"host": "node-2"}


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost:8086",
        "http://127.0.0.1:8086",
        "http://10.0.0.9:8086",
        "http://192.168.5.5",
        "http://169.254.169.254",
        "redis://example.com",
    ],
)
def test_internal_base_url_rejected(bad: str) -> None:
    with pytest.raises(ConnectorConfigError):
        InfluxDbSource(
            {**FLUX_CONFIG, "base_url": bad},
            _MockTransport(_Resp(200, FLUX_PAYLOAD)),
            environ={"INFLUX_TOKEN": "t"},
        )


def test_health_ok() -> None:
    src = InfluxDbSource(FLUX_CONFIG, _MockTransport(_Resp(200, {"status": "pass"})), environ={"INFLUX_TOKEN": "t"})
    assert src.health()["ok"] is True


def test_health_not_ok() -> None:
    src = InfluxDbSource(FLUX_CONFIG, _MockTransport(_Resp(503, {})), environ={"INFLUX_TOKEN": "t"})
    assert src.health()["ok"] is False


def test_health_never_raises() -> None:
    src = InfluxDbSource(FLUX_CONFIG, _MockTransport(exc=TimeoutError("slow")), environ={"INFLUX_TOKEN": "t"})
    h = src.health()
    assert h["ok"] is False
    assert "slow" in h["detail"]
