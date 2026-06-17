"""Unit tests for the Redmine issue-source adapter (mocked transport).

These tests never touch the network: a small recording transport stands in
for HTTP. They assert request shape (method, path, params, body, headers) and
the normalized output contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.issue_sources.redmine import RedmineError, RedmineIssueSource

API_KEY_ENV = "REDMINE_TEST_API_KEY"
BASE_URL = "https://redmine.example.com"


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Callable transport that records calls and replays queued responses."""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "params": dict(params) if params else None,
                "json": dict(json) if json is not None else None,
                "timeout": timeout,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(200, {})


class ExplodingTransport:
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("boom")


CANNED_ISSUES = {
    "issues": [
        {
            "id": 42,
            "subject": "Loom jammed",
            "description": "The mechanical loom is stuck.",
            "status": {"id": 2, "name": "In Progress"},
            "assigned_to": {"id": 7, "name": "Ned Ludd"},
            "tracker": {"id": 1, "name": "Bug"},
            "category": {"id": 3, "name": "machinery"},
            "priority": {"id": 4, "name": "High"},
            "updated_on": "2024-05-01T12:30:00Z",
        },
        {
            "id": 43,
            "subject": "Frame breaker request",
            "description": None,
            "status": {"id": 1, "name": "New"},
            "tracker": {"id": 2, "name": "Feature"},
            "priority": {"id": 2, "name": "Normal"},
            "updated_on": "2024-05-02 09:00:00",
        },
    ]
}


def _make_source(transport: Any, monkeypatch: pytest.MonkeyPatch, **extra: Any) -> RedmineIssueSource:
    monkeypatch.setenv(API_KEY_ENV, "secret-key-123")
    config: dict[str, Any] = {
        "base_url": BASE_URL,
        "project_id": "weavers",
        "api_key_env": API_KEY_ENV,
    }
    config.update(extra)
    return RedmineIssueSource(config, transport=transport)


# -- construction / contract -------------------------------------------------

def test_class_contract_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_source(RecordingTransport(), monkeypatch)
    assert RedmineIssueSource.SYSTEM == "redmine"
    assert src.name == "redmine"
    assert src.base_url == BASE_URL


def test_missing_api_key_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        RedmineIssueSource(
            {"base_url": BASE_URL, "project_id": "x"},
            transport=RecordingTransport(),
        )


# -- SSRF / internal-host blocking ------------------------------------------

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://169.254.169.254",
        "http://[::1]",
        "http://metadata.google.internal",
        "http://0.0.0.0",
    ],
)
def test_internal_base_url_rejected(bad_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "k")
    with pytest.raises(ValueError):
        RedmineIssueSource(
            {"base_url": bad_url, "api_key_env": API_KEY_ENV},
            transport=RecordingTransport(),
        )


def test_external_base_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "k")
    src = RedmineIssueSource(
        {"base_url": "https://redmine.example.org", "api_key_env": API_KEY_ENV},
        transport=RecordingTransport(),
    )
    assert src.base_url == "https://redmine.example.org"


# -- fetch_issues ------------------------------------------------------------

def test_fetch_issues_normalizes_canned_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, CANNED_ISSUES))
    src = _make_source(transport, monkeypatch)

    issues = src.fetch_issues({"status_id": "open", "limit": 25})

    # request shape
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{BASE_URL}/issues.json"
    assert call["params"] == {
        "project_id": "weavers",
        "status_id": "open",
        "limit": 25,
    }

    # normalization of first issue
    first = issues[0]
    assert first == {
        "external_id": "42",
        "source": "redmine",
        "title": "Loom jammed",
        "description": "The mechanical loom is stuck.",
        "status": "In Progress",
        "assignee": "Ned Ludd",
        "labels": ["Bug", "machinery"],
        "priority": "High",
        "url": f"{BASE_URL}/issues/42",
        "updated_ts": "2024-05-01T12:30:00+00:00",
        "raw": CANNED_ISSUES["issues"][0],
    }

    # second issue: missing assignee/category/description tolerated
    second = issues[1]
    assert second["external_id"] == "43"
    assert second["assignee"] is None
    assert second["labels"] == ["Feature"]
    assert second["description"] is None
    assert second["updated_ts"] == "2024-05-02T09:00:00+00:00"


def test_fetch_issues_external_id_is_string(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, CANNED_ISSUES))
    src = _make_source(transport, monkeypatch)
    issues = src.fetch_issues()
    assert all(isinstance(i["external_id"], str) for i in issues)


def test_fetch_issues_error_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(500, {}))
    src = _make_source(transport, monkeypatch)
    with pytest.raises(RedmineError):
        src.fetch_issues()


# -- API-key header ----------------------------------------------------------

def test_api_key_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, CANNED_ISSUES))
    src = _make_source(transport, monkeypatch)
    src.fetch_issues()
    headers = transport.calls[0]["headers"]
    assert headers["X-Redmine-API-Key"] == "secret-key-123"


def test_api_key_not_hardcoded_tracks_env_change(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        FakeResponse(200, CANNED_ISSUES), FakeResponse(200, CANNED_ISSUES)
    )
    src = _make_source(transport, monkeypatch)
    src.fetch_issues()
    monkeypatch.setenv(API_KEY_ENV, "rotated-key")
    src.fetch_issues()
    assert transport.calls[0]["headers"]["X-Redmine-API-Key"] == "secret-key-123"
    assert transport.calls[1]["headers"]["X-Redmine-API-Key"] == "rotated-key"


# -- update_status -----------------------------------------------------------

def test_update_status_puts_status_id_and_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(transport, monkeypatch)

    result = src.update_status("42", "Closed", comment="resolved by patch")

    call = transport.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == f"{BASE_URL}/issues/42.json"
    assert call["json"] == {"issue": {"status_id": 5, "notes": "resolved by patch"}}
    assert call["headers"]["X-Redmine-API-Key"] == "secret-key-123"
    assert result["status_id"] == 5
    assert result["external_id"] == "42"


def test_update_status_uses_config_status_map(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(transport, monkeypatch, status_map={"Done": 99})
    src.update_status("7", "Done")
    assert transport.calls[0]["json"] == {"issue": {"status_id": 99, "notes": None}}


def test_update_status_unknown_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(transport, monkeypatch)
    with pytest.raises(ValueError):
        src.update_status("1", "Nonexistent Status Name")


def test_update_status_error_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(422, {}))
    src = _make_source(transport, monkeypatch)
    with pytest.raises(RedmineError):
        src.update_status("1", "Closed")


# -- add_comment -------------------------------------------------------------

def test_add_comment_puts_notes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(transport, monkeypatch)
    result = src.add_comment("42", "looking into it")
    call = transport.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == f"{BASE_URL}/issues/42.json"
    assert call["json"] == {"issue": {"notes": "looking into it"}}
    assert result == {"ok": True, "external_id": "42"}


# -- health ------------------------------------------------------------------

def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {"issues": []}))
    src = _make_source(transport, monkeypatch)
    assert src.health() == {"ok": True, "detail": "ok"}
    # health should be a cheap, bounded probe
    assert transport.calls[0]["params"] == {"limit": 1}


def test_health_not_ok_on_bad_status(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(401, {}))
    src = _make_source(transport, monkeypatch)
    result = src.health()
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_health_never_raises_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_source(ExplodingTransport(), monkeypatch)
    result = src.health()
    assert result["ok"] is False
    assert "ConnectionError" in result["detail"]
