"""Unit tests for the Zabbix connector (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.zabbix import KIND, SSRFError, ZabbixSource


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Returns queued responses keyed by request order; records calls."""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _history_payload() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [
            {"itemid": "23296", "clock": "1700000000", "value": "42.5", "name": "CPU load", "host": "web01"},
            {"itemid": "23296", "clock": "1700000060", "value": "43.0", "name": "CPU load", "host": "web01"},
        ],
    }


def _problem_payload() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [
            {
                "eventid": "9001",
                "objectid": "15000",
                "clock": "1700000100",
                "name": "High CPU on web01",
                "severity": "4",
                "host": "web01",
            }
        ],
    }


def test_kind_and_name() -> None:
    src = ZabbixSource({"name": "zbx-prod", "base_url": "https://zabbix.example.com"})
    assert src.KIND == "metrics"
    assert KIND == "metrics"
    assert src.name == "zbx-prod"


def test_history_get_normalized() -> None:
    transport = RecordingTransport(FakeResponse(200, _history_payload()))
    src = ZabbixSource(
        {"base_url": "https://zabbix.example.com", "token_env": "ZBX"},
        transport=transport,
        environ={"ZBX": "abctoken"},
    )
    records = src.query({"method": "history.get", "params": {"itemids": ["23296"]}})

    assert len(records) == 2
    expected_keys = {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    for rec in records:
        assert set(rec) == expected_keys
        assert rec["kind"] == "metrics"

    first = records[0]
    assert first["ts"] == 1700000000
    assert first["value"] == 42.5
    assert first["labels"]["host"] == "web01"
    assert first["labels"]["item"] == "23296"

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://zabbix.example.com/api_jsonrpc.php"
    assert call["json"]["method"] == "history.get"


def test_problem_get_normalized_as_level() -> None:
    transport = RecordingTransport(FakeResponse(200, _problem_payload()))
    src = ZabbixSource(
        {"base_url": "https://zabbix.example.com", "token_env": "ZBX"},
        transport=transport,
        environ={"ZBX": "abctoken"},
    )
    records = src.query({"method": "problem.get", "params": {}})
    assert len(records) == 1
    rec = records[0]
    assert rec["level_or_status"] == "high"  # severity 4
    assert rec["message"] == "High CPU on web01"
    assert rec["ts"] == 1700000100
    assert rec["labels"]["host"] == "web01"
    assert rec["labels"]["trigger"] == "15000"


def test_auth_token_from_env() -> None:
    transport = RecordingTransport(FakeResponse(200, _history_payload()))
    src = ZabbixSource(
        {"base_url": "https://zabbix.example.com", "token_env": "ZBX_TOKEN"},
        transport=transport,
        environ={"ZBX_TOKEN": "supersecret"},
    )
    src.query({"method": "history.get", "params": {}})
    call = transport.calls[0]
    assert call["headers"]["Authorization"] == "Bearer supersecret"
    assert call["json"]["auth"] == "supersecret"


def test_no_auth_header_when_env_missing() -> None:
    transport = RecordingTransport(FakeResponse(200, _history_payload()))
    src = ZabbixSource(
        {"base_url": "https://zabbix.example.com", "token_env": "MISSING"},
        transport=transport,
        environ={},
    )
    src.query({"method": "history.get", "params": {}})
    assert "Authorization" not in transport.calls[0]["headers"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://10.1.2.3",
        "http://169.254.169.254",
        "https://172.16.0.1",
        "http://[fe80::1]",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(SSRFError):
        ZabbixSource({"base_url": bad_url})


def test_public_host_allowed() -> None:
    ZabbixSource({"base_url": "https://zabbix.example.com"})


def test_health_ok() -> None:
    transport = RecordingTransport(
        FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "6.4.0"})
    )
    src = ZabbixSource({"base_url": "https://zabbix.example.com"}, transport=transport)
    result = src.health()
    assert result["ok"] is True
    assert "6.4.0" in result["detail"]
    # apiinfo.version must not carry an auth header.
    assert "Authorization" not in transport.calls[0]["headers"]


def test_health_not_ok_on_rpc_error() -> None:
    transport = RecordingTransport(
        FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}})
    )
    src = ZabbixSource({"base_url": "https://zabbix.example.com"}, transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert "nope" in result["detail"]


def test_health_never_raises_on_transport_error() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise TimeoutError("timed out")

    src = ZabbixSource({"base_url": "https://zabbix.example.com"}, transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert "TimeoutError" in result["detail"]
