"""Unit tests for BugsnagSource — mocked transport, no network."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.bugsnag import BugsnagSource, ConnectorConfigError


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
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        return self.response


CANNED = [
    {
        "id": "err-abc",
        "error_class": "RuntimeError",
        "message": "boom in worker",
        "severity": "error",
        "status": "open",
        "release_stage": "production",
        "events": 250,
        "last_seen": "2026-06-15T12:00:00Z",
    }
]

CONFIG = {"name": "bs", "project_id": "proj-1", "token_env": "BUGSNAG_TOKEN"}


def test_kind_is_logs() -> None:
    assert BugsnagSource.KIND == "logs"


def test_auth_header_from_env() -> None:
    t = _MockTransport(_Resp(200, CANNED))
    src = BugsnagSource(CONFIG, t, environ={"BUGSNAG_TOKEN": "pat-123"})
    src.query({})
    assert t.calls[-1]["headers"]["Authorization"] == "token pat-123"
    assert t.calls[-1]["url"].endswith("/projects/proj-1/errors")


def test_missing_project_id_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        BugsnagSource(
            {"name": "bs", "token_env": "BUGSNAG_TOKEN"},
            _MockTransport(_Resp(200, CANNED)),
            environ={"BUGSNAG_TOKEN": "t"},
        )


def test_missing_token_env_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        BugsnagSource({"name": "bs", "project_id": "p"}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_unset_env_var_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        BugsnagSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={})


def test_normalization() -> None:
    src = BugsnagSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={"BUGSNAG_TOKEN": "t"})
    records = src.query({})
    assert len(records) == 1
    rec = records[0]
    assert rec["ts"] == "2026-06-15T12:00:00Z"
    assert rec["source"] == "bs"
    assert rec["kind"] == "logs"
    assert rec["level_or_status"] == "error"
    assert rec["message"] == "RuntimeError: boom in worker"
    assert rec["value"] == 250
    assert rec["labels"] == {"id": "err-abc", "status": "open", "release_stage": "production"}
    assert rec["raw"]["id"] == "err-abc"


def test_normalization_handles_errors_envelope() -> None:
    src = BugsnagSource(CONFIG, _MockTransport(_Resp(200, {"errors": CANNED})), environ={"BUGSNAG_TOKEN": "t"})
    assert len(src.query({})) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost",
        "http://127.0.0.1:8080",
        "http://10.1.2.3",
        "http://172.16.0.1",
        "http://192.168.0.10",
        "http://169.254.169.254",
        "gopher://example.com",
    ],
)
def test_internal_base_url_rejected(bad: str) -> None:
    with pytest.raises(ConnectorConfigError):
        BugsnagSource(
            {**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={"BUGSNAG_TOKEN": "t"}
        )


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
        BugsnagSource(
            {**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={"BUGSNAG_TOKEN": "t"}
        )


def test_metadata_lookalike_hostname_allowed() -> None:
    # Exact-match only: a benign host that merely contains "metadata" is fine.
    src = BugsnagSource(
        {**CONFIG, "base_url": "https://metadata.example.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={"BUGSNAG_TOKEN": "t"},
    )
    assert src.base_url == "https://metadata.example.com"


def test_public_base_url_allowed() -> None:
    src = BugsnagSource(
        {**CONFIG, "base_url": "https://api.bugsnag.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={"BUGSNAG_TOKEN": "t"},
    )
    assert src.base_url == "https://api.bugsnag.com"


def test_health_ok() -> None:
    src = BugsnagSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={"BUGSNAG_TOKEN": "t"})
    assert src.health()["ok"] is True


def test_health_not_ok() -> None:
    src = BugsnagSource(CONFIG, _MockTransport(_Resp(401, {})), environ={"BUGSNAG_TOKEN": "t"})
    assert src.health()["ok"] is False


def test_health_never_raises() -> None:
    src = BugsnagSource(CONFIG, _MockTransport(exc=ConnectionError("down")), environ={"BUGSNAG_TOKEN": "t"})
    h = src.health()
    assert h["ok"] is False
    assert "down" in h["detail"]
