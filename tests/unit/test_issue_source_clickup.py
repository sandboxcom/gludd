"""Unit tests for ClickUpIssueSource (mocked transport, no network)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.issue_sources.clickup import ClickUpIssueSource


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


def _tasks_payload() -> dict[str, Any]:
    return {
        "tasks": [
            {
                "id": "abc123",
                "name": "Improve onboarding",
                "description": "users churn early",
                "status": {"status": "in progress", "color": "#fff"},
                "assignees": [{"id": 9, "username": "ada", "email": "ada@x.io"}],
                "tags": [{"name": "ux"}, {"name": "growth"}],
                "priority": {"priority": "urgent"},
                "url": "https://app.clickup.com/t/abc123",
                "date_updated": "1781599047000",
            }
        ]
    }


def test_fetch_normalizes_tasks() -> None:
    tx = RecordingTransport([(200, _tasks_payload())])
    src = ClickUpIssueSource(
        {"list_id": "L1", "token_env": "CUTOK"}, transport=tx, env={"CUTOK": "pk_x"}
    )
    issues = src.fetch_issues()
    assert len(issues) == 1
    issue = issues[0]
    assert issue["external_id"] == "abc123"
    assert issue["source"] == "clickup"
    assert issue["title"] == "Improve onboarding"
    assert issue["description"] == "users churn early"
    assert issue["status"] == "in progress"
    assert issue["assignee"] == "ada"
    assert issue["labels"] == ["ux", "growth"]
    assert issue["priority"] == "urgent"
    assert issue["url"] == "https://app.clickup.com/t/abc123"
    assert issue["updated_ts"] == "1781599047000"
    assert issue["raw"]["id"] == "abc123"


def test_auth_token_from_env_and_url() -> None:
    tx = RecordingTransport([(200, {"tasks": []})])
    src = ClickUpIssueSource(
        {"list_id": "L9", "token_env": "CUTOK"}, transport=tx, env={"CUTOK": "pk_abc"}
    )
    src.fetch_issues()
    call = tx.calls[0]
    assert call["headers"]["Authorization"] == "pk_abc"
    assert call["method"] == "GET"
    assert call["url"] == "https://api.clickup.com/api/v2/list/L9/task"


def test_update_status_writeback_body() -> None:
    tx = RecordingTransport(
        [
            (200, {"id": "t1", "status": {"status": "done"}}),
            (200, {"id": "c1"}),
        ]
    )
    src = ClickUpIssueSource({"list_id": "L1"}, transport=tx, env={})
    res = src.update_status("t1", "done", comment="closing out")
    assert res["ok"] is True
    status_call = tx.calls[0]
    assert status_call["method"] == "PUT"
    assert status_call["url"] == "https://api.clickup.com/api/v2/task/t1"
    assert status_call["body"] == {"status": "done"}
    comment_call = tx.calls[1]
    assert comment_call["method"] == "POST"
    assert comment_call["url"] == "https://api.clickup.com/api/v2/task/t1/comment"
    assert comment_call["body"]["comment_text"] == "closing out"


def test_add_comment_body() -> None:
    tx = RecordingTransport([(200, {"id": "c5"})])
    src = ClickUpIssueSource({"list_id": "L1"}, transport=tx, env={})
    res = src.add_comment("t9", "hello")
    assert res["ok"] is True
    assert tx.calls[0]["url"].endswith("/task/t9/comment")
    assert tx.calls[0]["body"]["comment_text"] == "hello"


def test_internal_base_url_rejected() -> None:
    for bad in (
        "http://169.254.169.254",
        "http://127.0.0.1",
        "http://localhost",
        "http://192.168.1.1",
        "http://internal-host",
        "http://api.internal",
    ):
        with pytest.raises(ValueError):
            ClickUpIssueSource({"base_url": bad}, transport=RecordingTransport([]), env={})


@pytest.mark.parametrize(
    "bad_url",
    ["http://metadata.google.internal/", "http://metadata.goog/", "http://instance-data/"],
)
def test_metadata_alias_names_rejected(bad_url: str) -> None:
    # Previously missing from this adapter's own literal blocklist; now covered
    # via the canonical general_ludd.security.ssrf.host_is_blocked delegation.
    with pytest.raises(ValueError):
        ClickUpIssueSource({"base_url": bad_url}, transport=RecordingTransport([]), env={})


def test_alibaba_metadata_ip_rejected() -> None:
    # 100.100.100.200 sits in the 100.64.0.0/10 CGNAT range: is_private is
    # False for it in Python's ipaddress, so the OLD is_private-only check
    # would NOT have caught it. BLOCKED_METADATA_IPS names it explicitly.
    with pytest.raises(ValueError):
        ClickUpIssueSource(
            {"base_url": "http://100.100.100.200/"}, transport=RecordingTransport([]), env={}
        )


def test_cgnat_address_rejected() -> None:
    # 100.65.1.1 sits in the 100.64.0.0/10 CGNAT range: is_private is False,
    # so only the canonical guard's `not is_global` flag closes this gap.
    with pytest.raises(ValueError):
        ClickUpIssueSource(
            {"base_url": "http://100.65.1.1/"}, transport=RecordingTransport([]), env={}
        )


def test_health_ok() -> None:
    tx = RecordingTransport([(200, {"user": {"id": 7, "username": "ada"}})])
    src = ClickUpIssueSource({}, transport=tx, env={"CLICKUP_API_TOKEN": "t"})
    assert src.health() == {"ok": True, "detail": "ok"}


def test_health_not_ok() -> None:
    tx = RecordingTransport([(401, {"err": "Token invalid", "ECODE": "OAUTH_017"})])
    src = ClickUpIssueSource({}, transport=tx, env={})
    h = src.health()
    assert h["ok"] is False


def test_health_never_raises() -> None:
    def boom(*_a: Any, **_k: Any) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("dns fail")

    src = ClickUpIssueSource({}, transport=boom, env={})
    h = src.health()
    assert h["ok"] is False
    assert "dns fail" in h["detail"]


def test_system_and_name_attrs() -> None:
    assert ClickUpIssueSource.SYSTEM == "clickup"
    src = ClickUpIssueSource({"name": "sprint-list"}, transport=RecordingTransport([]), env={})
    assert src.name == "sprint-list"
