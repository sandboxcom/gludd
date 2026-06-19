"""Unit tests for the standalone GitHub issue-source adapter (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.issue_sources.github_issues import GitHubIssueSource


class _Resp:
    """Canned HTTP response satisfying the adapter's HTTPResponse protocol."""

    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class _Transport:
    """Recording fake transport: returns queued responses, records calls."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> _Resp:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "timeout": timeout,
            }
        )
        return self._responses.pop(0)


_ISSUES_PAYLOAD = [
    {
        "id": 1001,
        "number": 7,
        "title": "Bug: crash on save",
        "body": "stack trace here",
        "state": "open",
        "assignee": {"login": "alice"},
        "labels": [{"name": "bug"}, {"name": "p1"}],
        "html_url": "https://github.com/acme/widget/issues/7",
        "updated_at": "2026-06-10T12:00:00Z",
    },
    {
        "id": 1002,
        "number": 8,
        "title": "A pull request, not an issue",
        "body": "",
        "state": "open",
        "labels": [],
        "pull_request": {"url": "https://api.github.com/.../pulls/8"},
        "html_url": "https://github.com/acme/widget/pull/8",
        "updated_at": "2026-06-11T12:00:00Z",
    },
    {
        "id": 1003,
        "number": 9,
        "title": "Closed feature",
        "body": "done",
        "state": "closed",
        "assignee": None,
        "labels": ["enhancement"],
        "html_url": "https://github.com/acme/widget/issues/9",
        "updated_at": "2026-06-09T12:00:00Z",
    },
]


def _make(
    responses: list[_Resp],
    *,
    config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[GitHubIssueSource, _Transport]:
    transport = _Transport(responses)
    cfg = {"repo": "acme/widget", "token_env": "GH_TOKEN"}
    if config:
        cfg.update(config)
    src = GitHubIssueSource(cfg, transport=transport, env=env or {"GH_TOKEN": "secret-tok"})
    return src, transport


def test_system_and_name() -> None:
    src, _ = _make([])
    assert GitHubIssueSource.name == "github"
    assert src.name == "github"


def test_fetch_issues_normalizes_and_skips_prs() -> None:
    src, transport = _make([_Resp(200, _ISSUES_PAYLOAD)])
    issues = src.fetch_issues({"state": "all", "labels": ["bug", "p1"], "since": "2026-06-01"})

    # PR item (#8) skipped -> 2 normalized issues.
    assert len(issues) == 2
    first = issues[0]
    assert first == {
        "external_id": "7",
        "source": "github",
        "title": "Bug: crash on save",
        "description": "stack trace here",
        "status": "open",
        "assignee": "alice",
        "labels": ["bug", "p1"],
        "priority": "high",
        "url": "https://github.com/acme/widget/issues/7",
        "updated_ts": "2026-06-10T12:00:00Z",
        "raw": _ISSUES_PAYLOAD[0],
    }
    assert issues[1]["external_id"] == "9"
    assert issues[1]["status"] == "closed"
    assert issues[1]["assignee"] is None

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.github.com/repos/acme/widget/issues"
    assert call["params"] == {"state": "all", "labels": "bug,p1", "since": "2026-06-01"}


def test_token_bearer_header_from_env() -> None:
    src, transport = _make([_Resp(200, [])])
    src.fetch_issues()
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-tok"


def test_update_status_close_patches_body() -> None:
    src, transport = _make([_Resp(200, {})])
    result = src.update_status("7", "closed")
    call = transport.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"] == "https://api.github.com/repos/acme/widget/issues/7"
    assert call["json"] == {"state": "closed"}
    assert result["ok"] is True
    assert result["status"] == "closed"


def test_update_status_reopen_patches_body() -> None:
    src, transport = _make([_Resp(200, {})])
    src.update_status("7", "open")
    assert transport.calls[0]["json"] == {"state": "open"}


def test_update_status_with_comment_posts_comment() -> None:
    src, transport = _make([_Resp(200, {}), _Resp(201, {"id": 55})])
    result = src.update_status("7", "closed", comment="fixed in #10")
    assert transport.calls[0]["method"] == "PATCH"
    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[1]["url"] == "https://api.github.com/repos/acme/widget/issues/7/comments"
    assert transport.calls[1]["json"] == {"body": "fixed in #10"}
    assert result["comment"]["ok"] is True


def test_update_status_rejects_bad_status() -> None:
    src, _ = _make([])
    with pytest.raises(ValueError):
        src.update_status("7", "wip")


def test_add_comment_posts_body() -> None:
    src, transport = _make([_Resp(201, {"id": 99})])
    result = src.add_comment("7", "hello world")
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.github.com/repos/acme/widget/issues/7/comments"
    assert call["json"] == {"body": "hello world"}
    assert result["ok"] is True
    assert result["raw"] == {"id": 99}


def test_internal_base_url_rejected() -> None:
    src, transport = _make(
        [_Resp(200, [])],
        config={"base_url": "http://169.254.169.254"},
    )
    with pytest.raises(PermissionError):
        src.fetch_issues()
    assert transport.calls == []


def test_internal_base_localhost_rejected() -> None:
    src, _ = _make([], config={"base_url": "http://localhost:8080"})
    with pytest.raises(PermissionError):
        src.fetch_issues()


def test_health_ok() -> None:
    src, transport = _make([_Resp(200, {"full_name": "acme/widget"})])
    health = src.health()
    assert health["ok"] is True
    assert "200" in health["detail"]
    assert transport.calls[0]["url"] == "https://api.github.com/repos/acme/widget"


def test_health_not_ok_on_http_error() -> None:
    src, _ = _make([_Resp(404, {})])
    health = src.health()
    assert health["ok"] is False
    assert "404" in health["detail"]


def test_health_never_raises_on_transport_failure() -> None:
    class _Boom:
        def __call__(self, *a: Any, **k: Any) -> _Resp:
            raise RuntimeError("network down")

    src = GitHubIssueSource(
        {"repo": "acme/widget", "token_env": "GH_TOKEN"},
        transport=_Boom(),
        env={"GH_TOKEN": "tok"},
    )
    health = src.health()
    assert health["ok"] is False
    assert "network down" in health["detail"]


def test_health_not_ok_missing_token() -> None:
    src = GitHubIssueSource(
        {"repo": "acme/widget", "token_env": "GH_TOKEN"},
        transport=_Transport([]),
        env={},
    )
    health = src.health()
    assert health["ok"] is False
    assert "token" in health["detail"].lower()


def test_health_internal_base_blocked() -> None:
    src = GitHubIssueSource(
        {"repo": "acme/widget", "base_url": "http://127.0.0.1"},
        transport=_Transport([]),
        env={"GITHUB_TOKEN": "t"},
    )
    health = src.health()
    assert health["ok"] is False
    assert "internal" in health["detail"].lower()


def test_default_base_url_and_token_env() -> None:
    src = GitHubIssueSource({"repo": "acme/widget"}, transport=_Transport([_Resp(200, [])]), env={"GITHUB_TOKEN": "z"})
    assert src._base_url == "https://api.github.com"
    src.fetch_issues()


def test_url_injection_external_id_is_quoted() -> None:
    src, transport = _make([_Resp(200, {})])
    src.update_status("../../admin", "closed")
    url = transport.calls[0]["url"]
    assert "%2F" in url
    assert "../" not in url
