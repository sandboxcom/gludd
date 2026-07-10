"""Unit tests for the Jira issue-source adapter.

All transport is MOCKED — no real network calls. A canned transport records
each request and returns scripted responses, so we assert on the exact HTTP
verbs, paths, bodies, and headers the adapter produces and on the normalized
issue dicts it returns.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from general_ludd.issue_sources.jira import JiraIssueSource


# --------------------------------------------------------------------------- #
# Mock transport
# --------------------------------------------------------------------------- #
@dataclass
class MockResponse:
    """Minimal stand-in for an HTTP response the adapter consumes."""

    status_code: int
    _payload: Any = None

    def json(self) -> Any:
        return self._payload


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    json_body: Any
    timeout: float | None


@dataclass
class MockTransport:
    """Injectable transport: scripts responses, records requests.

    ``responses`` is a list consumed in order. Each request appends a
    :class:`RecordedRequest` to ``calls``.
    """

    responses: list[MockResponse] = field(default_factory=list)
    calls: list[RecordedRequest] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> MockResponse:
        self.calls.append(
            RecordedRequest(
                method=method.upper(),
                url=url,
                headers=dict(headers or {}),
                json_body=json,
                timeout=timeout,
            )
        )
        if not self.responses:
            raise AssertionError("MockTransport ran out of scripted responses")
        return self.responses.pop(0)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
EMAIL_ENV = "JIRA_TEST_EMAIL"
TOKEN_ENV = "JIRA_TEST_TOKEN"
EMAIL = "agent@example.com"
TOKEN = "s3cr3t-token"


def base_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "base_url": "https://example.atlassian.net",
        "project": "GLUDD",
        "email_env": EMAIL_ENV,
        "token_env": TOKEN_ENV,
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture(autouse=True)
def _creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_ENV, EMAIL)
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def expected_basic_header() -> str:
    raw = f"{EMAIL}:{TOKEN}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


SEARCH_PAYLOAD = {
    "issues": [
        {
            "key": "GLUDD-1",
            "fields": {
                "summary": "Fix the loom",
                "description": "The loom is broken",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Ned"},
                "labels": ["bug", "urgent"],
                "priority": {"name": "High"},
                "updated": "2026-06-12T10:30:00.000+0000",
            },
        },
        {
            "key": "GLUDD-2",
            "fields": {
                "summary": "Add steam guard",
                "description": None,
                "status": {"name": "To Do"},
                "assignee": None,
                "labels": [],
                "priority": None,
                "updated": "2026-06-11T08:00:00.000+0000",
            },
        },
    ]
}


# --------------------------------------------------------------------------- #
# Contract: class shape
# --------------------------------------------------------------------------- #
def test_system_class_attr() -> None:
    assert JiraIssueSource.SYSTEM == "jira"


def test_name_attr_present() -> None:
    src = JiraIssueSource(base_config(), transport=MockTransport())
    assert src.name == "jira"


def test_no_hardcoded_credentials() -> None:
    """Credentials must come from env, never the config dict literally."""
    src = JiraIssueSource(base_config(), transport=MockTransport())
    # The adapter must read from env vars, not store plaintext creds in config.
    assert TOKEN not in json.dumps(src.config)
    assert EMAIL not in json.dumps(src.config)


# --------------------------------------------------------------------------- #
# SSRF: literal-host block on base_url (no DNS)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/rest",
        "http://localhost:8080",
        "http://[::1]/x",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://0.0.0.0",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        JiraIssueSource(base_config(base_url=bad_url), transport=MockTransport())


def test_public_base_url_accepted() -> None:
    # Should not raise.
    JiraIssueSource(base_config(base_url="https://example.atlassian.net"), transport=MockTransport())


@pytest.mark.parametrize(
    "bad_url",
    ["http://metadata.google.internal/", "http://metadata.goog/", "http://instance-data/"],
)
def test_metadata_alias_names_rejected(bad_url: str) -> None:
    # Previously missing from this adapter's own 4-name blocklist; now covered
    # via the canonical general_ludd.security.ssrf.host_is_blocked delegation.
    with pytest.raises(ValueError):
        JiraIssueSource(base_config(base_url=bad_url), transport=MockTransport())


def test_alibaba_metadata_ip_rejected() -> None:
    # 100.100.100.200 sits in the 100.64.0.0/10 CGNAT range: is_private is
    # False for it in Python's ipaddress, so the OLD is_private-only check
    # would NOT have caught it. BLOCKED_METADATA_IPS names it explicitly.
    with pytest.raises(ValueError):
        JiraIssueSource(base_config(base_url="http://100.100.100.200/"), transport=MockTransport())


def test_cgnat_address_rejected() -> None:
    # 100.65.1.1 sits in the 100.64.0.0/10 CGNAT range: is_private is False,
    # so only the canonical guard's `not is_global` flag closes this gap.
    with pytest.raises(ValueError):
        JiraIssueSource(base_config(base_url="http://100.65.1.1/"), transport=MockTransport())


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok_on_200() -> None:
    transport = MockTransport(responses=[MockResponse(200, {"name": "agent"})])
    src = JiraIssueSource(base_config(), transport=transport)
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_401() -> None:
    transport = MockTransport(responses=[MockResponse(401, {"errorMessages": ["auth"]})])
    src = JiraIssueSource(base_config(), transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_health_never_raises() -> None:
    class BoomTransport(MockTransport):
        def request(self, *a: Any, **k: Any) -> MockResponse:
            raise RuntimeError("network down")

    src = JiraIssueSource(base_config(), transport=BoomTransport())
    result = src.health()
    assert result["ok"] is False
    assert "detail" in result


def test_health_uses_basic_auth_header() -> None:
    transport = MockTransport(responses=[MockResponse(200, {})])
    src = JiraIssueSource(base_config(), transport=transport)
    src.health()
    assert transport.calls[0].headers["Authorization"] == expected_basic_header()


# --------------------------------------------------------------------------- #
# fetch_issues()
# --------------------------------------------------------------------------- #
def test_fetch_issues_posts_search_endpoint() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    src.fetch_issues({})
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "https://example.atlassian.net/rest/api/3/search"
    assert "jql" in call.json_body
    assert "maxResults" in call.json_body
    assert "fields" in call.json_body


def test_fetch_issues_jql_from_project() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(project="GLUDD"), transport=transport)
    src.fetch_issues({})
    assert "GLUDD" in transport.calls[0].json_body["jql"]


def test_fetch_issues_explicit_jql_from_spec_wins() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    src.fetch_issues({"jql": "status = Done"})
    assert transport.calls[0].json_body["jql"] == "status = Done"


def test_fetch_issues_normalizes() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    issues = src.fetch_issues({})
    assert len(issues) == 2

    first = issues[0]
    assert first["external_id"] == "GLUDD-1"
    assert first["source"] == "jira"
    assert first["title"] == "Fix the loom"
    assert first["description"] == "The loom is broken"
    assert first["status"] == "In Progress"
    assert first["assignee"] == "Ned"
    assert first["labels"] == ["bug", "urgent"]
    assert first["priority"] == "High"
    assert first["url"] == "https://example.atlassian.net/browse/GLUDD-1"
    # updated_ts is parsed from fields.updated into a numeric epoch.
    assert isinstance(first["updated_ts"], float)
    assert first["updated_ts"] > 0
    assert first["raw"]["key"] == "GLUDD-1"


def test_fetch_issues_handles_missing_optional_fields() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    issues = src.fetch_issues({})
    second = issues[1]
    assert second["external_id"] == "GLUDD-2"
    assert second["assignee"] is None
    assert second["priority"] is None
    assert second["labels"] == []
    assert second["description"] is None


def test_fetch_issues_basic_auth_and_timeout() -> None:
    transport = MockTransport(responses=[MockResponse(200, SEARCH_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    src.fetch_issues({})
    call = transport.calls[0]
    assert call.headers["Authorization"] == expected_basic_header()
    assert call.timeout is not None


# --------------------------------------------------------------------------- #
# update_status()
# --------------------------------------------------------------------------- #
TRANSITIONS_PAYLOAD = {
    "transitions": [
        {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
        {"id": "21", "name": "Resolve Issue", "to": {"name": "Done"}},
        {"id": "31", "name": "Close Issue", "to": {"name": "Closed"}},
    ]
}


def test_update_status_resolves_transition_id_and_posts() -> None:
    transport = MockTransport(
        responses=[
            MockResponse(200, TRANSITIONS_PAYLOAD),  # GET transitions
            MockResponse(204, None),  # POST transition
        ]
    )
    src = JiraIssueSource(base_config(), transport=transport)
    result = src.update_status("GLUDD-1", "Done")

    # First call looks up transitions.
    get_call = transport.calls[0]
    assert get_call.method == "GET"
    assert get_call.url == "https://example.atlassian.net/rest/api/3/issue/GLUDD-1/transitions"

    # Second call executes the transition matching target name "Done" -> id "21".
    post_call = transport.calls[1]
    assert post_call.method == "POST"
    assert post_call.url == "https://example.atlassian.net/rest/api/3/issue/GLUDD-1/transitions"
    assert post_call.json_body["transition"]["id"] == "21"

    assert result["external_id"] == "GLUDD-1"
    assert result["status"] == "Done"
    assert result["transition_id"] == "21"


def test_update_status_with_comment_includes_comment_in_transition() -> None:
    transport = MockTransport(
        responses=[
            MockResponse(200, TRANSITIONS_PAYLOAD),
            MockResponse(204, None),
        ]
    )
    src = JiraIssueSource(base_config(), transport=transport)
    src.update_status("GLUDD-1", "Done", comment="auto-resolved by gludd")

    post_call = transport.calls[1]
    body = json.dumps(post_call.json_body)
    assert "auto-resolved by gludd" in body


def test_update_status_unknown_target_raises() -> None:
    transport = MockTransport(responses=[MockResponse(200, TRANSITIONS_PAYLOAD)])
    src = JiraIssueSource(base_config(), transport=transport)
    with pytest.raises(ValueError):
        src.update_status("GLUDD-1", "Nonexistent Status")


def test_update_status_uses_basic_auth() -> None:
    transport = MockTransport(
        responses=[MockResponse(200, TRANSITIONS_PAYLOAD), MockResponse(204, None)]
    )
    src = JiraIssueSource(base_config(), transport=transport)
    src.update_status("GLUDD-1", "Done")
    for call in transport.calls:
        assert call.headers["Authorization"] == expected_basic_header()


# --------------------------------------------------------------------------- #
# add_comment()
# --------------------------------------------------------------------------- #
def test_add_comment_posts_comment_endpoint() -> None:
    transport = MockTransport(responses=[MockResponse(201, {"id": "10000"})])
    src = JiraIssueSource(base_config(), transport=transport)
    result = src.add_comment("GLUDD-1", "looking into this")

    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "https://example.atlassian.net/rest/api/3/issue/GLUDD-1/comment"
    assert "looking into this" in json.dumps(call.json_body)
    assert call.headers["Authorization"] == expected_basic_header()
    assert result["external_id"] == "GLUDD-1"
    assert result["ok"] is True
