"""Unit tests for the ServiceNow issue-source adapter.

Transport is fully mocked -- no network is touched. Each test injects a
recording fake transport so we can assert on method, URL, params, headers and
JSON body of every request.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from general_ludd.issue_sources.servicenow import (
    ServiceNowIssueSource,
    SSRFError,
)

INSTANCE = "https://dev12345.service-now.com"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Records every request and returns a queued (or default) response."""

    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "json": json,
                "timeout": timeout,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(200, {"result": []})


class RaisingTransport:
    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("boom")


CANNED_TABLE_PAYLOAD = {
    "result": [
        {
            "sys_id": "abc123def456",
            "number": "INC0010001",
            "short_description": "Email server down",
            "description": "Users cannot send mail.",
            "state": "2",
            "assigned_to": {"value": "user-sys-id-001", "link": "https://x/api"},
            "category": "network",
            "priority": "1",
            "sys_updated_on": "2026-06-15 14:30:00",
        }
    ]
}


def _basic_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "instance_url": INSTANCE,
        "user_env": "SN_USER",
        "password_env": "SN_PASS",
    }
    cfg.update(overrides)
    return cfg


def _token_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "instance_url": INSTANCE,
        "token_env": "SN_TOKEN",
    }
    cfg.update(overrides)
    return cfg


# --------------------------------------------------------------------------- #
# Contract / construction
# --------------------------------------------------------------------------- #


def test_system_and_name_attrs() -> None:
    src = ServiceNowIssueSource(
        _basic_config(), transport=RecordingTransport(), env={}
    )
    assert ServiceNowIssueSource.SYSTEM == "servicenow"
    assert src.SYSTEM == "servicenow"
    assert src.name == "servicenow"


def test_name_override_from_config() -> None:
    src = ServiceNowIssueSource(
        _basic_config(name="prod-snow"), transport=RecordingTransport(), env={}
    )
    assert src.name == "prod-snow"


def test_default_table_is_incident() -> None:
    src = ServiceNowIssueSource(
        _basic_config(), transport=RecordingTransport(), env={}
    )
    assert src.table == "incident"


def test_custom_table() -> None:
    src = ServiceNowIssueSource(
        _basic_config(table="sc_task"), transport=RecordingTransport(), env={}
    )
    assert src.table == "sc_task"


# --------------------------------------------------------------------------- #
# SSRF host blocking (literal, no DNS)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/x",
        "http://127.0.0.1",
        "https://10.0.0.5",
        "https://192.168.1.10",
        "https://172.16.5.4",
        "http://169.254.169.254",  # cloud metadata
        "http://metadata.google.internal",
        "https://foo.internal",
        "https://svc.cluster.local",
        "http://[::1]",
        "http://0.0.0.0",
    ],
)
def test_internal_instance_rejected(bad_url: str) -> None:
    with pytest.raises(SSRFError):
        ServiceNowIssueSource(
            _basic_config(instance_url=bad_url),
            transport=RecordingTransport(),
            env={},
        )


def test_public_instance_accepted() -> None:
    src = ServiceNowIssueSource(
        _basic_config(instance_url=INSTANCE),
        transport=RecordingTransport(),
        env={},
    )
    assert src.instance_url == INSTANCE


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        ServiceNowIssueSource(
            _basic_config(instance_url="ftp://dev.service-now.com"),
            transport=RecordingTransport(),
            env={},
        )


def test_trailing_slash_normalized() -> None:
    src = ServiceNowIssueSource(
        _basic_config(instance_url=INSTANCE + "/"),
        transport=RecordingTransport(),
        env={},
    )
    assert src.instance_url == INSTANCE


# --------------------------------------------------------------------------- #
# Auth from env (never hardcoded)
# --------------------------------------------------------------------------- #


def test_basic_auth_header_from_env() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "alice", "SN_PASS": "s3cret"},
    )
    src.fetch_issues({})
    sent = transport.calls[0]["headers"]["Authorization"]
    expected = "Basic " + base64.b64encode(b"alice:s3cret").decode()
    assert sent == expected


def test_bearer_auth_header_from_env() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    src = ServiceNowIssueSource(
        _token_config(),
        transport=transport,
        env={"SN_TOKEN": "tok-xyz"},
    )
    src.fetch_issues({})
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok-xyz"


def test_bearer_takes_precedence_over_basic() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    cfg = _token_config(user_env="SN_USER", password_env="SN_PASS")
    src = ServiceNowIssueSource(
        cfg,
        transport=transport,
        env={"SN_TOKEN": "tok-xyz", "SN_USER": "a", "SN_PASS": "b"},
    )
    src.fetch_issues({})
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok-xyz"


def test_missing_basic_env_raises_on_request() -> None:
    transport = RecordingTransport()
    src = ServiceNowIssueSource(_basic_config(), transport=transport, env={})
    with pytest.raises(RuntimeError):
        src.fetch_issues({})


def test_missing_token_env_raises_on_request() -> None:
    transport = RecordingTransport()
    src = ServiceNowIssueSource(_token_config(), transport=transport, env={})
    with pytest.raises(RuntimeError):
        src.fetch_issues({})


def test_no_auth_configured_raises() -> None:
    transport = RecordingTransport()
    src = ServiceNowIssueSource(
        {"instance_url": INSTANCE}, transport=transport, env={}
    )
    with pytest.raises(RuntimeError):
        src.fetch_issues({})


# --------------------------------------------------------------------------- #
# fetch_issues: GET + normalization
# --------------------------------------------------------------------------- #


def _src_with_payload(payload: Any, **cfg: Any) -> tuple[ServiceNowIssueSource, RecordingTransport]:
    transport = RecordingTransport([FakeResponse(200, payload)])
    src = ServiceNowIssueSource(
        _basic_config(**cfg),
        transport=transport,
        env={"SN_USER": "alice", "SN_PASS": "s3cret"},
    )
    return src, transport


def test_fetch_issues_hits_table_endpoint() -> None:
    src, transport = _src_with_payload(CANNED_TABLE_PAYLOAD)
    src.fetch_issues({"query": "active=true", "limit": 50})
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{INSTANCE}/api/now/table/incident"
    assert call["params"]["sysparm_query"] == "active=true"
    assert call["params"]["sysparm_limit"] == 50
    assert call["timeout"] is not None


def test_fetch_issues_default_query_and_limit() -> None:
    src, transport = _src_with_payload({"result": []})
    src.fetch_issues()
    params = transport.calls[0]["params"]
    assert params["sysparm_query"] == ""
    assert params["sysparm_limit"] == 100


def test_fetch_issues_normalizes_canned_payload() -> None:
    src, _ = _src_with_payload(CANNED_TABLE_PAYLOAD)
    issues = src.fetch_issues({})
    assert len(issues) == 1
    issue = issues[0]

    assert issue["external_id"] == "abc123def456"
    assert issue["source"] == "servicenow"
    assert issue["title"] == "Email server down"
    assert issue["description"] == "Users cannot send mail."
    # No state_map configured -> raw state passes through.
    assert issue["status"] == "2"
    # Reference field flattened to its value.
    assert issue["assignee"] == "user-sys-id-001"
    assert issue["labels"] == ["network", "INC0010001"]
    assert issue["priority"] == "1"
    assert issue["url"] == (
        f"{INSTANCE}/nav_to.do?uri=incident.do?sys_id=abc123def456"
    )
    assert issue["raw"] == CANNED_TABLE_PAYLOAD["result"][0]


def test_number_appears_in_labels() -> None:
    src, _ = _src_with_payload(CANNED_TABLE_PAYLOAD)
    issue = src.fetch_issues({})[0]
    assert "INC0010001" in issue["labels"]


def test_state_map_applied_when_configured() -> None:
    src, _ = _src_with_payload(
        CANNED_TABLE_PAYLOAD, state_map={"2": "In Progress", "6": "Resolved"}
    )
    issue = src.fetch_issues({})[0]
    assert issue["status"] == "In Progress"


def test_updated_ts_parsed_to_epoch() -> None:
    src, _ = _src_with_payload(CANNED_TABLE_PAYLOAD)
    issue = src.fetch_issues({})[0]
    # 2026-06-15 14:30:00 UTC == 1781533800
    assert issue["updated_ts"] == pytest.approx(1781533800.0)


def test_url_uses_custom_table() -> None:
    src, _ = _src_with_payload(CANNED_TABLE_PAYLOAD, table="sc_task")
    issue = src.fetch_issues({})[0]
    assert issue["url"] == (
        f"{INSTANCE}/nav_to.do?uri=sc_task.do?sys_id=abc123def456"
    )


def test_empty_result_returns_empty_list() -> None:
    src, _ = _src_with_payload({"result": []})
    assert src.fetch_issues({}) == []


def test_missing_fields_normalize_safely() -> None:
    payload = {"result": [{"sys_id": "only-id"}]}
    src, _ = _src_with_payload(payload)
    issue = src.fetch_issues({})[0]
    assert issue["external_id"] == "only-id"
    assert issue["title"] == ""
    assert issue["labels"] == []
    assert issue["updated_ts"] is None


# --------------------------------------------------------------------------- #
# update_status: PATCH state + work_notes
# --------------------------------------------------------------------------- #


def test_update_status_patches_state_and_url() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": {"sys_id": "abc123"}})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    out = src.update_status("abc123", "6")
    call = transport.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"] == f"{INSTANCE}/api/now/table/incident/abc123"
    assert call["json"] == {"state": "6"}
    assert out == {"sys_id": "abc123"}


def test_update_status_includes_work_notes_comment() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": {}})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    src.update_status("sys999", "2", comment="working on it")
    body = transport.calls[0]["json"]
    assert body == {"state": "2", "work_notes": "working on it"}


def test_update_status_resolves_state_name_via_map() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": {}})])
    src = ServiceNowIssueSource(
        _basic_config(state_map={"6": "Resolved"}),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    src.update_status("sys999", "Resolved")
    assert transport.calls[0]["json"]["state"] == "6"


def test_update_status_sys_id_in_url() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": {}})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    src.update_status("THE_SYS_ID", "1")
    assert "THE_SYS_ID" in transport.calls[0]["url"]


# --------------------------------------------------------------------------- #
# add_comment: PATCH work_notes
# --------------------------------------------------------------------------- #


def test_add_comment_patches_work_notes() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": {}})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    src.add_comment("sys42", "a note")
    call = transport.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"] == f"{INSTANCE}/api/now/table/incident/sys42"
    assert call["json"] == {"work_notes": "a note"}


# --------------------------------------------------------------------------- #
# health: never raises, ok / not-ok
# --------------------------------------------------------------------------- #


def test_health_ok() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_4xx() -> None:
    transport = RecordingTransport([FakeResponse(401, {"error": "nope"})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    result = src.health()
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_health_never_raises_on_transport_error() -> None:
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=RaisingTransport(),
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    result = src.health()
    assert result["ok"] is False
    assert "ConnectionError" in result["detail"]


def test_health_uses_limit_one() -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    src = ServiceNowIssueSource(
        _basic_config(),
        transport=transport,
        env={"SN_USER": "a", "SN_PASS": "b"},
    )
    src.health()
    assert transport.calls[0]["params"]["sysparm_limit"] == 1
    assert transport.calls[0]["url"] == f"{INSTANCE}/api/now/table/incident"
