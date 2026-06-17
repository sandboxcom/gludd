"""Unit tests for BitbucketIssueSource (mocked transport, no network)."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.issue_sources.bitbucket_issues import BitbucketIssueSource


class RecordingTransport:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json_body,
                "timeout": timeout,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return 200, {}


def _issues_payload() -> dict[str, Any]:
    return {
        "values": [
            {
                "id": 42,
                "title": "Crash on save",
                "state": "open",
                "kind": "bug",
                "priority": "major",
                "content": {"raw": "stack trace here"},
                "assignee": {"display_name": "Ada Lovelace", "uuid": "{u-1}"},
                "links": {"html": {"href": "https://bitbucket.org/ws/repo/issues/42"}},
                "updated_on": "2026-06-10T08:00:00Z",
            }
        ]
    }


def _cfg(**extra: Any) -> dict[str, Any]:
    base = {"workspace": "ws", "repo": "repo"}
    base.update(extra)
    return base


def test_fetch_normalizes_issues() -> None:
    tx = RecordingTransport([(200, _issues_payload())])
    src = BitbucketIssueSource(_cfg(token_env="BBTOK"), transport=tx, env={"BBTOK": "tok"})
    issues = src.fetch_issues()
    assert len(issues) == 1
    issue = issues[0]
    assert issue["external_id"] == "42"
    assert issue["source"] == "bitbucket"
    assert issue["title"] == "Crash on save"
    assert issue["description"] == "stack trace here"
    assert issue["status"] == "open"
    assert issue["assignee"] == "Ada Lovelace"
    assert issue["labels"] == ["bug"]
    assert issue["priority"] == "major"
    assert issue["url"] == "https://bitbucket.org/ws/repo/issues/42"
    assert issue["updated_ts"] == "2026-06-10T08:00:00Z"
    assert issue["raw"]["id"] == 42


def test_auth_bearer_from_env_and_url() -> None:
    tx = RecordingTransport([(200, {"values": []})])
    src = BitbucketIssueSource(_cfg(token_env="BBTOK"), transport=tx, env={"BBTOK": "abc"})
    src.fetch_issues()
    call = tx.calls[0]
    assert call["headers"]["Authorization"] == "Bearer abc"
    assert call["method"] == "GET"
    assert call["url"] == "https://api.bitbucket.org/2.0/repositories/ws/repo/issues"


def test_auth_basic_username_password_from_env() -> None:
    tx = RecordingTransport([(200, {"values": []})])
    src = BitbucketIssueSource(
        _cfg(username="ada", password_env="BBPW"),
        transport=tx,
        env={"BBPW": "app-pass"},
    )
    src.fetch_issues()
    expected = "Basic " + base64.b64encode(b"ada:app-pass").decode("ascii")
    assert tx.calls[0]["headers"]["Authorization"] == expected


def test_update_status_writeback_body() -> None:
    tx = RecordingTransport(
        [
            (200, {"id": 42, "state": "resolved"}),
            (201, {"id": 5}),
        ]
    )
    src = BitbucketIssueSource(_cfg(), transport=tx, env={})
    res = src.update_status("42", "resolved", comment="fixed in 1.2")
    assert res["ok"] is True
    status_call = tx.calls[0]
    assert status_call["method"] == "PUT"
    assert status_call["url"] == "https://api.bitbucket.org/2.0/repositories/ws/repo/issues/42"
    assert status_call["body"] == {"state": "resolved"}
    comment_call = tx.calls[1]
    assert comment_call["method"] == "POST"
    assert comment_call["url"].endswith("/issues/42/comments")
    assert comment_call["body"] == {"content": {"raw": "fixed in 1.2"}}


def test_add_comment_body() -> None:
    tx = RecordingTransport([(201, {"id": 1})])
    src = BitbucketIssueSource(_cfg(), transport=tx, env={})
    res = src.add_comment("7", "note")
    assert res["ok"] is True
    assert tx.calls[0]["body"] == {"content": {"raw": "note"}}
    assert tx.calls[0]["url"].endswith("/issues/7/comments")


def test_internal_base_url_rejected() -> None:
    for bad in (
        "http://169.254.169.254",
        "http://127.0.0.1",
        "http://localhost",
        "http://172.16.0.1",
        "http://git-internal",
        "http://scm.local",
    ):
        with pytest.raises(ValueError):
            BitbucketIssueSource(_cfg(base_url=bad), transport=RecordingTransport([]), env={})


def test_health_ok() -> None:
    tx = RecordingTransport([(200, {"uuid": "{u-1}", "username": "ada"})])
    src = BitbucketIssueSource(_cfg(), transport=tx, env={"BITBUCKET_TOKEN": "t"})
    assert src.health() == {"ok": True, "detail": "ok"}


def test_health_not_ok() -> None:
    tx = RecordingTransport([(403, {"error": {"message": "forbidden"}})])
    src = BitbucketIssueSource(_cfg(), transport=tx, env={})
    h = src.health()
    assert h["ok"] is False
    assert "403" in h["detail"]


def test_health_never_raises() -> None:
    def boom(*_a: Any, **_k: Any) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("connection refused")

    src = BitbucketIssueSource(_cfg(), transport=boom, env={})
    h = src.health()
    assert h["ok"] is False
    assert "connection refused" in h["detail"]


def test_system_and_name_attrs() -> None:
    assert BitbucketIssueSource.SYSTEM == "bitbucket"
    src = BitbucketIssueSource(_cfg(name="tracker"), transport=RecordingTransport([]), env={})
    assert src.name == "tracker"
