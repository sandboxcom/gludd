"""Unit tests for RollbarSource — mocked transport, no network."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.rollbar import ConnectorConfigError, RollbarSource


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _MockTransport:
    """Records the last request and replays a canned response."""

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
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        return self.response


CANNED = {
    "result": {
        "items": [
            {
                "id": 111,
                "counter": 42,
                "title": "NullPointer in checkout",
                "level": "error",
                "status": "active",
                "environment": "production",
                "total_occurrences": 137,
                "last_occurrence_timestamp": 1700000000,
            }
        ]
    }
}

CONFIG = {"name": "rb", "token_env": "ROLLBAR_TOKEN"}


def test_kind_is_logs() -> None:
    assert RollbarSource.KIND == "logs"


def test_token_read_from_env_not_hardcoded() -> None:
    env = {"ROLLBAR_TOKEN": "secret-tok"}
    t = _MockTransport(_Resp(200, CANNED))
    src = RollbarSource(CONFIG, t, environ=env)
    src.query({})
    assert t.calls[-1]["headers"]["X-Rollbar-Access-Token"] == "secret-tok"


def test_missing_token_env_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        RollbarSource({"name": "rb"}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_unset_env_var_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        RollbarSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={})


def test_normalization() -> None:
    env = {"ROLLBAR_TOKEN": "t"}
    src = RollbarSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ=env)
    records = src.query({})
    assert len(records) == 1
    rec = records[0]
    assert rec["ts"] == 1700000000
    assert rec["source"] == "rb"
    assert rec["kind"] == "logs"
    assert rec["level_or_status"] == "error"
    assert rec["message"] == "NullPointer in checkout"
    assert rec["value"] == 137
    assert rec["labels"] == {"counter": 42, "environment": "production", "status": "active"}
    assert rec["raw"]["id"] == 111


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost/api",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.1.1",
        # Alibaba cloud-metadata endpoint — coverage gained by delegating to the
        # canonical is_url_blocked (the old bespoke guard missed this address).
        "http://100.100.100.200/",
        "ftp://example.com",
    ],
)
def test_internal_base_url_rejected(bad: str) -> None:
    with pytest.raises(ConnectorConfigError):
        RollbarSource({**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={"ROLLBAR_TOKEN": "t"})


@pytest.mark.parametrize(
    "bad",
    [
        "http://metadata.google.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://METADATA.GOOGLE.INTERNAL/",
        "http://metadata/",
        "http://metadata",
    ],
)
def test_metadata_hostname_rejected(bad: str) -> None:
    # DNS name for the cloud metadata endpoint must be refused even without DNS
    # resolution (the 169.254.169.254 IP literal is covered above).
    with pytest.raises(ConnectorConfigError):
        RollbarSource({**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={"ROLLBAR_TOKEN": "t"})


def test_metadata_lookalike_hostname_allowed() -> None:
    # Exact-match only: a benign host that merely contains "metadata" is fine.
    src = RollbarSource(
        {**CONFIG, "base_url": "https://metadata.example.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={"ROLLBAR_TOKEN": "t"},
    )
    assert src.base_url == "https://metadata.example.com"


def test_public_base_url_allowed() -> None:
    src = RollbarSource(
        {**CONFIG, "base_url": "https://api.rollbar.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={"ROLLBAR_TOKEN": "t"},
    )
    assert src.base_url == "https://api.rollbar.com"


def test_health_ok() -> None:
    src = RollbarSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={"ROLLBAR_TOKEN": "t"})
    assert src.health() == {"ok": True, "detail": "HTTP 200"}


def test_health_not_ok_on_error_status() -> None:
    src = RollbarSource(CONFIG, _MockTransport(_Resp(403, {})), environ={"ROLLBAR_TOKEN": "t"})
    h = src.health()
    assert h["ok"] is False


def test_health_never_raises_on_transport_error() -> None:
    src = RollbarSource(CONFIG, _MockTransport(exc=RuntimeError("boom")), environ={"ROLLBAR_TOKEN": "t"})
    h = src.health()
    assert h["ok"] is False
    assert "boom" in h["detail"]
