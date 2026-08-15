"""Structural tests for issue_sources/gitlab_issues.py."""

from __future__ import annotations

from typing import Any

from general_ludd.issue_sources.gitlab_issues import _PRIORITY_LABELS, GitLabIssueSource


class FakeHTTPResponse:
    def __init__(self, status_code: int, data: dict[str, Any] | list[Any]):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeHTTPTransport:
    def __init__(self, responses: list[FakeHTTPResponse] | None = None):
        self.responses = responses or []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, *, headers, params=None, json=None, timeout):
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
        if self.responses:
            return self.responses.pop(0)
        return FakeHTTPResponse(200, [])


def _make_config(**overrides):
    config = {"base_url": "https://gitlab.example.com", "project_id": "42"}
    config.update(overrides)
    return config


def test_gitlab_construction_defaults():
    env = {"GITLAB_TOKEN": "glpat-fake"}
    source = GitLabIssueSource(_make_config(), transport=FakeHTTPTransport(), env=env)
    assert source._base_url == "https://gitlab.example.com"
    assert source._project_id == "42"
    assert source.name == "gitlab"


def test_gitlab_construction_no_token():
    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env={},
    )
    assert source._token() == ""


def test_gitlab_health_no_project_id():
    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        {"base_url": "https://gitlab.com"},
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is False
    assert "missing" in result["detail"]


def test_gitlab_health_no_token():
    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={},
    )
    result = source.health()
    assert result["ok"] is False
    assert "missing token" in result["detail"]


def test_gitlab_health_ok():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"id": 42})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is True


def test_gitlab_health_loopback_blocked():
    source = GitLabIssueSource(
        {"base_url": "http://127.0.0.1", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is False


def test_gitlab_normalize_minimal():
    issue = {"iid": 1, "title": "Test", "description": "desc"}
    result = GitLabIssueSource._normalize(issue)
    assert result["external_id"] == "1"
    assert result["source"] == "gitlab"
    assert result["title"] == "Test"
    assert result["status"] == "open"
    assert result["assignee"] is None
    assert result["labels"] == []
    assert result["priority"] is None


def test_gitlab_normalize_closed():
    issue = {"iid": 5, "title": "Done", "description": "", "state": "closed"}
    result = GitLabIssueSource._normalize(issue)
    assert result["status"] == "closed"


def test_gitlab_normalize_with_labels():
    issue = {
        "iid": 10,
        "title": "High pri bug",
        "description": "",
        "labels": [{"name": "P0"}, {"name": "bug"}],
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["labels"] == ["P0", "bug"]
    assert result["priority"] == "critical"


def test_gitlab_normalize_assignee():
    issue = {
        "iid": 3,
        "title": "Assigned",
        "description": "",
        "assignee": {"username": "alice"},
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["assignee"] == "alice"


def test_gitlab_normalize_assignees_fallback():
    issue = {
        "iid": 4,
        "title": "Multi assignee",
        "description": "",
        "assignees": [{"username": "bob"}],
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["assignee"] == "bob"


def test_gitlab_fetch_issues_open(monkeypatch):
    transport = FakeHTTPTransport(
        [
            FakeHTTPResponse(
                200,
                [
                    {"iid": 1, "title": "Bug", "description": ""},
                ],
            )
        ]
    )
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues({"state": "open"})
    assert len(issues) == 1
    assert issues[0]["external_id"] == "1"
    call = transport.calls[0]
    assert call["params"]["state"] == "opened"


def test_gitlab_fetch_issues_closed(monkeypatch):
    transport = FakeHTTPTransport(
        [
            FakeHTTPResponse(
                200,
                [
                    {"iid": 2, "title": "Done", "description": "", "state": "closed"},
                ],
            )
        ]
    )
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues({"state": "closed"})
    assert len(issues) == 1
    assert issues[0]["status"] == "closed"


def test_gitlab_update_status(monkeypatch):
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"iid": 1})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.update_status("1", "closed")
    assert result["ok"] is True
    assert result["state_event"] == "close"


def test_priority_labels_complete():
    assert _PRIORITY_LABELS["p0"] == "critical"
    assert _PRIORITY_LABELS["p1"] == "high"
    assert _PRIORITY_LABELS["p2"] == "medium"
    assert _PRIORITY_LABELS["p3"] == "low"
    assert _PRIORITY_LABELS["critical"] == "critical"
    assert _PRIORITY_LABELS["urgent"] == "critical"
    assert _PRIORITY_LABELS["high"] == "high"
    assert _PRIORITY_LABELS["medium"] == "medium"
    assert _PRIORITY_LABELS["low"] == "low"
    assert len(_PRIORITY_LABELS) == 9


# -- import checks ------------------------------------------------------


def test_module_exports():
    from general_ludd.issue_sources import gitlab_issues as mod

    assert mod.SYSTEM == "gitlab"
    assert mod._DEFAULT_BASE_URL == "https://gitlab.com"
    assert mod._DEFAULT_TIMEOUT == 30.0
    assert isinstance(mod._PRIORITY_LABELS, dict)


def test_http_response_protocol():
    try:
        from typing import get_protocol_members  # Python 3.12+
    except ImportError:
        from typing_extensions import get_protocol_members

    from general_ludd.issue_sources.gitlab_issues import HTTPResponse

    assert HTTPResponse.__class__.__name__ == "_ProtocolMeta"
    assert get_protocol_members(HTTPResponse) == frozenset({"status_code", "json"})


def test_http_response_satisfies() -> None:
    from general_ludd.issue_sources.gitlab_issues import HTTPResponse

    class R:
        status_code: int = 200

        def json(self) -> dict[str, Any]:
            return {}

    assert isinstance(R(), HTTPResponse)


def test_http_transport_protocol():
    try:
        from typing import get_protocol_members  # Python 3.12+
    except ImportError:
        from typing_extensions import get_protocol_members

    from general_ludd.issue_sources.gitlab_issues import HTTPTransport

    assert HTTPTransport.__class__.__name__ == "_ProtocolMeta"
    assert get_protocol_members(HTTPTransport) == frozenset({"__call__"})


def test_http_transport_satisfies() -> None:
    from general_ludd.issue_sources.gitlab_issues import HTTPTransport

    class T:
        def __call__(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, str] | None = None,
            json: dict[str, Any] | None = None,
            timeout: float,
        ) -> FakeHTTPResponse:
            return FakeHTTPResponse(200, {})

    assert isinstance(T(), HTTPTransport)


# -- is_internal_host ---------------------------------------------------


def test_is_internal_host_delegates():
    from general_ludd.issue_sources.gitlab_issues import _is_internal_host
    from general_ludd.security.ssrf import host_is_blocked

    assert _is_internal_host("127.0.0.1") is host_is_blocked("127.0.0.1")
    assert _is_internal_host("gitlab.com") is host_is_blocked("gitlab.com")
    assert _is_internal_host("192.168.1.1") is host_is_blocked("192.168.1.1")


# -- default transport --------------------------------------------------


def test_default_transport_returns_callable():
    from general_ludd.issue_sources.gitlab_issues import _default_transport

    transport = _default_transport()
    assert callable(transport)


# -- default values -----------------------------------------------------


def test_default_base_url():
    source = GitLabIssueSource({}, env={}, transport=FakeHTTPTransport())
    assert source._base_url == "https://gitlab.com"


def test_default_timeout():
    source = GitLabIssueSource({}, env={}, transport=FakeHTTPTransport())
    assert source._timeout == 30.0


def test_default_token_env():
    source = GitLabIssueSource({}, env={}, transport=FakeHTTPTransport())
    assert source._token_env == "GITLAB_TOKEN"


# -- SSRF ---------------------------------------------------------------


def test_ssrf_base_url_internal_host_parsed():
    source = GitLabIssueSource(
        {"base_url": "http://10.0.0.1", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._host == "10.0.0.1"
    assert source._base_internal is True


def test_ssrf_base_url_external_host_not_blocked():
    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._base_internal is False
    assert source._host == "gitlab.example.com"


def test_ssrf_scheme_parsed():
    source = GitLabIssueSource(
        {"base_url": "http://gitlab.local", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._scheme == "http"


def test_ssrf_strips_trailing_slash():
    source = GitLabIssueSource(
        {"base_url": "https://gitlab.example.com/", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._base_url == "https://gitlab.example.com"


# -- _request -----------------------------------------------------------


def test_request_permission_error_internal():
    source = GitLabIssueSource(
        {"base_url": "http://127.0.0.1", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    import pytest

    with pytest.raises(PermissionError, match="internal host"):
        source._request("GET", "/test")


def test_request_url_construction():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, [])])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    source._request("GET", "/projects/42/issues", params={"state": "opened"})
    call = transport.calls[0]
    assert call["url"] == "https://gitlab.example.com/api/v4/projects/42/issues"
    assert call["method"] == "GET"
    assert call["params"] == {"state": "opened"}


# -- _token / _headers --------------------------------------------------


def test_token_from_custom_env():
    source = GitLabIssueSource(
        _make_config(token_env="MY_CUSTOM_TOKEN"),
        transport=FakeHTTPTransport(),
        env={"MY_CUSTOM_TOKEN": "secret123"},
    )
    assert source._token() == "secret123"


def test_headers_includes_token():
    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "glpat-abc"},
    )
    headers = source._headers()
    assert headers["PRIVATE-TOKEN"] == "glpat-abc"
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"] == "general-ludd-agent"


def test_headers_no_token():
    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env={},
    )
    headers = source._headers()
    assert "PRIVATE-TOKEN" not in headers


# -- _get_transport -----------------------------------------------------


def test_get_transport_injected():
    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._get_transport() is transport


def test_get_transport_creates_default():
    source = GitLabIssueSource(
        _make_config(),
        transport=None,
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._transport is None
    t = source._get_transport()
    assert callable(t)
    assert source._transport is not None


# -- health: exception path ---------------------------------------------


def test_health_request_exception():
    class FailingTransport:
        def __call__(self, *args, **kwargs):
            raise ConnectionError("network down")

    source = GitLabIssueSource(
        _make_config(),
        transport=FailingTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is False
    assert "request failed" in result["detail"]


# -- _normalize edges ---------------------------------------------------


def test_normalize_web_url():
    issue = {
        "iid": 1,
        "title": "T",
        "description": "",
        "web_url": "https://gitlab.com/u/p/issues/1",
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["url"] == "https://gitlab.com/u/p/issues/1"


def test_normalize_no_web_url():
    issue = {"iid": 1, "title": "T", "description": ""}
    result = GitLabIssueSource._normalize(issue)
    assert result["url"] == ""


def test_normalize_updated_ts():
    issue = {
        "iid": 1,
        "title": "T",
        "description": "",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["updated_ts"] == "2024-01-01T00:00:00Z"


def test_normalize_updated_ts_none():
    issue = {"iid": 1, "title": "T", "description": ""}
    result = GitLabIssueSource._normalize(issue)
    assert result["updated_ts"] is None


def test_normalize_raw_preserved():
    issue = {"iid": 1, "title": "T", "description": "", "extra": "keep"}
    result = GitLabIssueSource._normalize(issue)
    assert result["raw"] is issue


def test_normalize_string_labels():
    issue = {"iid": 1, "title": "T", "description": "", "labels": ["bug", "enhancement"]}
    result = GitLabIssueSource._normalize(issue)
    assert result["labels"] == ["bug", "enhancement"]


def test_normalize_fallback_id_no_iid():
    issue = {"id": 999, "title": "T", "description": ""}
    result = GitLabIssueSource._normalize(issue)
    assert result["external_id"] == "999"


def test_normalize_default_title_description():
    issue = {}
    result = GitLabIssueSource._normalize(issue)
    assert result["title"] == ""
    assert result["description"] == ""
    assert result["source"] == "gitlab"


# -- update_status ------------------------------------------------------


def test_update_status_invalid_raises():
    import pytest

    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    with pytest.raises(ValueError, match="must be 'open' or 'closed'"):
        source.update_status("1", "in_progress")


def test_update_status_with_comment():
    transport = FakeHTTPTransport(
        [
            FakeHTTPResponse(200, {"iid": 1}),
            FakeHTTPResponse(201, {"id": 100}),
        ]
    )
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.update_status("1", "open", comment="Reopening")
    assert result["ok"] is True
    assert "comment" in result
    assert result["comment"]["ok"] is True


def test_update_status_reopen():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"iid": 1})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.update_status("1", "open")
    assert result["state_event"] == "reopen"


# -- add_comment --------------------------------------------------------


def test_add_comment_posts_note():
    transport = FakeHTTPTransport([FakeHTTPResponse(201, {"id": 100})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.add_comment("1", "This is a note")
    assert result["ok"] is True
    assert result["external_id"] == "1"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert "/notes" in call["url"]
    assert call["json"] == {"body": "This is a note"}


def test_add_comment_non_2xx():
    transport = FakeHTTPTransport([FakeHTTPResponse(400, {"error": "bad"})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.add_comment("1", "bad")
    assert result["ok"] is False
    assert result["raw"] is None


# -- fetch_issues -------------------------------------------------------


def test_fetch_issues_labels_joined():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, [])])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    source.fetch_issues({"labels": ["bug", "critical"]})
    assert transport.calls[0]["params"]["labels"] == "bug,critical"


def test_fetch_issues_labels_string():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, [])])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    source.fetch_issues({"labels": "bug"})
    assert transport.calls[0]["params"]["labels"] == "bug"


def test_fetch_issues_non_list_response():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"error": "not found"})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    assert source.fetch_issues() == []


def test_fetch_issues_skips_non_dict_items():
    transport = FakeHTTPTransport(
        [
            FakeHTTPResponse(
                200,
                [
                    {"iid": 1, "title": "Valid", "description": ""},
                    "not-a-dict",
                    {"iid": 2, "title": "Also valid", "description": ""},
                ],
            )
        ]
    )
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues()
    assert len(issues) == 2


def test_fetch_issues_no_spec():
    transport = FakeHTTPTransport(
        [
            FakeHTTPResponse(
                200,
                [
                    {"iid": 1, "title": "Bug", "description": ""},
                ],
            )
        ]
    )
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues()
    assert len(issues) == 1


# -- base_url edge cases ------------------------------------------------


def test_base_url_config_override():
    source = GitLabIssueSource(
        {"project_id": "1", "base_url": "https://my-gitlab.local"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._base_url == "https://my-gitlab.local"


def test_timeout_config_override():
    source = GitLabIssueSource(
        {"project_id": "1", "timeout": "60"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    assert source._timeout == 60.0


# -- env fallback to os.environ ----------------------------------------


def test_env_defaults_to_os_environ():
    """When env=None, constructor reads os.environ."""
    import os

    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env=None,
    )
    assert source._env is not None
    assert source._token_env in source._env or source._token() == os.environ.get("GITLAB_TOKEN", "")
