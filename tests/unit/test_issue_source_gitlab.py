"""Unit tests for the standalone GitLab issue-source adapter (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource


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
        "id": 5001,
        "iid": 3,
        "title": "Login broken",
        "description": "users cannot log in",
        "state": "opened",
        "assignee": {"username": "bob"},
        "labels": ["bug", "critical"],
        "web_url": "https://gitlab.com/acme/app/-/issues/3",
        "updated_at": "2026-06-10T09:00:00Z",
    },
    {
        "id": 5002,
        "iid": 4,
        "title": "Old request",
        "description": "",
        "state": "closed",
        "assignees": [{"username": "carol"}],
        "labels": [{"name": "wontfix"}],
        "web_url": "https://gitlab.com/acme/app/-/issues/4",
        "updated_at": "2026-06-08T09:00:00Z",
    },
]


def _make(
    responses: list[_Resp],
    *,
    config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[GitLabIssueSource, _Transport]:
    transport = _Transport(responses)
    cfg = {"project_id": "42", "token_env": "GL_TOKEN"}
    if config:
        cfg.update(config)
    src = GitLabIssueSource(cfg, transport=transport, env=env or {"GL_TOKEN": "glpat-xyz"})
    return src, transport


def test_system_and_name() -> None:
    src, _ = _make([])
    assert GitLabIssueSource.name == "gitlab"
    assert src.name == "gitlab"


def test_fetch_issues_normalizes() -> None:
    src, transport = _make([_Resp(200, _ISSUES_PAYLOAD)])
    issues = src.fetch_issues({"state": "open", "labels": ["bug"]})

    assert len(issues) == 2
    assert issues[0] == {
        "external_id": "3",
        "source": "gitlab",
        "title": "Login broken",
        "description": "users cannot log in",
        "status": "open",
        "assignee": "bob",
        "labels": ["bug", "critical"],
        "priority": "critical",
        "url": "https://gitlab.com/acme/app/-/issues/3",
        "updated_ts": "2026-06-10T09:00:00Z",
        "raw": _ISSUES_PAYLOAD[0],
    }
    # closed + assignees[] + dict-form labels.
    assert issues[1]["status"] == "closed"
    assert issues[1]["assignee"] == "carol"
    assert issues[1]["labels"] == ["wontfix"]

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://gitlab.com/api/v4/projects/42/issues"
    # "open" mapped to GitLab's "opened" vocabulary.
    assert call["params"] == {"state": "opened", "labels": "bug"}


def test_private_token_header_from_env() -> None:
    src, transport = _make([_Resp(200, [])])
    src.fetch_issues()
    headers = transport.calls[0]["headers"]
    assert headers["PRIVATE-TOKEN"] == "glpat-xyz"
    assert "Authorization" not in headers


def test_update_status_close_puts_state_event() -> None:
    src, transport = _make([_Resp(200, {})])
    result = src.update_status("3", "closed")
    call = transport.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "https://gitlab.com/api/v4/projects/42/issues/3"
    assert call["json"] == {"state_event": "close"}
    assert result["ok"] is True
    assert result["state_event"] == "close"


def test_update_status_reopen_puts_state_event() -> None:
    src, transport = _make([_Resp(200, {})])
    result = src.update_status("3", "open")
    assert transport.calls[0]["json"] == {"state_event": "reopen"}
    assert result["state_event"] == "reopen"


def test_update_status_with_comment_posts_note() -> None:
    src, transport = _make([_Resp(200, {}), _Resp(201, {"id": 7})])
    result = src.update_status("3", "closed", comment="resolved")
    assert transport.calls[0]["method"] == "PUT"
    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[1]["url"] == "https://gitlab.com/api/v4/projects/42/issues/3/notes"
    assert transport.calls[1]["json"] == {"body": "resolved"}
    assert result["comment"]["ok"] is True


def test_update_status_rejects_bad_status() -> None:
    src, _ = _make([])
    with pytest.raises(ValueError):
        src.update_status("3", "merged")


def test_add_comment_posts_note() -> None:
    src, transport = _make([_Resp(201, {"id": 88})])
    result = src.add_comment("3", "a note")
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://gitlab.com/api/v4/projects/42/issues/3/notes"
    assert call["json"] == {"body": "a note"}
    assert result["ok"] is True
    assert result["raw"] == {"id": 88}


def test_internal_base_url_rejected() -> None:
    src, transport = _make([_Resp(200, [])], config={"base_url": "http://10.0.0.5"})
    with pytest.raises(PermissionError):
        src.fetch_issues()
    assert transport.calls == []


def test_internal_base_localhost_rejected() -> None:
    src, _ = _make([], config={"base_url": "https://localhost"})
    with pytest.raises(PermissionError):
        src.fetch_issues()


@pytest.mark.parametrize(
    "bad_url",
    ["https://metadata.google.internal", "https://metadata.goog", "https://instance-data"],
)
def test_metadata_alias_names_rejected(bad_url: str) -> None:
    # Previously missing from this adapter's own 4-name blocklist; now covered
    # via the canonical general_ludd.security.ssrf.host_is_blocked delegation.
    src, transport = _make([_Resp(200, [])], config={"base_url": bad_url})
    with pytest.raises(PermissionError):
        src.fetch_issues()
    assert transport.calls == []


def test_alibaba_metadata_ip_rejected() -> None:
    # 100.100.100.200 sits in the 100.64.0.0/10 CGNAT range: is_private is
    # False for it in Python's ipaddress, so the OLD is_private-only check
    # would NOT have caught it. BLOCKED_METADATA_IPS names it explicitly.
    src, transport = _make([_Resp(200, [])], config={"base_url": "http://100.100.100.200"})
    with pytest.raises(PermissionError):
        src.fetch_issues()
    assert transport.calls == []


def test_cgnat_address_rejected() -> None:
    # 100.65.1.1 sits in the 100.64.0.0/10 CGNAT range: is_private is False,
    # so only the canonical guard's `not is_global` flag closes this gap.
    src, transport = _make([_Resp(200, [])], config={"base_url": "http://100.65.1.1"})
    with pytest.raises(PermissionError):
        src.fetch_issues()
    assert transport.calls == []


def test_health_ok() -> None:
    src, transport = _make([_Resp(200, {"id": 42})])
    health = src.health()
    assert health["ok"] is True
    assert "200" in health["detail"]
    assert transport.calls[0]["url"] == "https://gitlab.com/api/v4/projects/42"


def test_health_not_ok_on_http_error() -> None:
    src, _ = _make([_Resp(401, {})])
    health = src.health()
    assert health["ok"] is False
    assert "401" in health["detail"]


def test_health_never_raises_on_transport_failure() -> None:
    class _Boom:
        def __call__(self, *a: Any, **k: Any) -> _Resp:
            raise RuntimeError("connection refused")

    src = GitLabIssueSource(
        {"project_id": "42", "token_env": "GL_TOKEN"},
        transport=_Boom(),
        env={"GL_TOKEN": "t"},
    )
    health = src.health()
    assert health["ok"] is False
    assert "connection refused" in health["detail"]


def test_health_not_ok_missing_token() -> None:
    src = GitLabIssueSource(
        {"project_id": "42", "token_env": "GL_TOKEN"},
        transport=_Transport([]),
        env={},
    )
    health = src.health()
    assert health["ok"] is False
    assert "token" in health["detail"].lower()


def test_health_internal_base_blocked() -> None:
    src = GitLabIssueSource(
        {"project_id": "42", "base_url": "http://192.168.1.1"},
        transport=_Transport([]),
        env={"GITLAB_TOKEN": "t"},
    )
    health = src.health()
    assert health["ok"] is False
    assert "internal" in health["detail"].lower()


def test_default_base_url_and_token_env() -> None:
    src = GitLabIssueSource(
        {"project_id": "42"},
        transport=_Transport([_Resp(200, [])]),
        env={"GITLAB_TOKEN": "z"},
    )
    assert src._base_url == "https://gitlab.com"
    src.fetch_issues()
