"""E2E tests for connectors batch 4 — Auth/Identity, ITSM, Error Tracking, Logging.

Covers 18 uncovered connector modules. Uses mock transports — no real network I/O.

Targets:
  Auth/Identity:  okta, entra_signin
  ITSM:           servicenow, zendesk
  Error Tracking: bugsnag, rollbar
  Logging:        graylog, syslog_file, journald
  Productivity:   linear, notion, trello, airtable, asana, monday
  Profiling:      pyroscope, parca
  macOS:          mac_unified_log
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

# ============================================================================
# Mock transports — handles both HttpResponse and tuple patterns
# ============================================================================


class MockHttpResponse:
    """Mock httpx-like response for connectors expecting HttpResponse protocol."""

    def __init__(self, status_code: int = 200, body: object = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        if isinstance(self._body, (dict, list)):
            return self._body
        return self._body


@dataclass
class MockHttpTransport:
    """Injectable HTTP transport returning canned (status, body) tuples.

    For connectors using HttpResponse protocol, wrap with MockHttpResponse.
    """

    responses: dict[str, tuple[int, object]] = field(default_factory=dict)
    default_status: int = 200
    default_body: object = None
    calls: list[dict[str, object]] = field(default_factory=list)
    _response_objects: dict[str, MockHttpResponse] = field(default_factory=dict)

    def __call__(
        self,
        method_or_url: str,
        url_or_headers: str | dict[str, str] | None = None,
        *,
        params: dict[str, object] | None = None,
        json: object = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> tuple[int, object]:
        self.calls.append({
            "method": method_or_url,
            "url": url_or_headers if isinstance(url_or_headers, str) else method_or_url,
            "params": params,
            "json": json,
            "headers": headers,
            "auth": auth,
            "timeout": timeout,
        })
        return self.responses.get(
            str(url_or_headers) if isinstance(url_or_headers, str) else "default",
            (self.default_status, self.default_body),
        )


class MockHttpResponseTransport:
    """Injectable transport returning MockHttpResponse objects.

    For connectors using the `HttpResponse` protocol (status_code, json(), headers).
    """

    def __init__(
        self,
        status_code: int = 200,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status = status_code
        self._body = body
        self._headers = headers or {}
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        method_or_url: str,
        url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, object] | None = None,
        json: object = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
        **kwargs: object,
    ) -> MockHttpResponse:
        method = method_or_url if url is not None else "GET"
        request_url = url if url is not None else method_or_url
        self.calls.append({
            "method": method,
            "url": request_url,
            "headers": headers,
            "params": params,
            "json": json,
            "auth": auth,
            "timeout": timeout,
        })
        resp = MockHttpResponse(self._status, self._body)
        resp.headers = dict(self._headers)
        return resp

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, object] | None = None,
        timeout: float = 30.0,
        **kwargs: object,
    ) -> MockHttpResponse:
        """Expose the object-style transport protocol used by Bugsnag."""
        return self(
            method,
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            **kwargs,
        )

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, object] | None = None,
        timeout: float = 30.0,
        **kwargs: object,
    ) -> MockHttpResponse:
        """Expose the object-style GET protocol used by profiling connectors."""
        return self(
            "GET",
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            **kwargs,
        )


def _make_http_get(status: int = 200, body: object = None):
    """Factory: tuple-returning http_get for CircleCI/nagios-style connectors."""

    def _get(url: str, headers: dict[str, str], **kw: object) -> tuple[int, object]:
        return status, body

    return _get


# ============================================================================
# 1. Okta Connector
# ============================================================================


class TestOktaConnector:
    def test_config_requires_org_url(self):
        from general_ludd.connectors.okta import OktaSource

        with pytest.raises(ValueError, match="org_url"):
            OktaSource({})

    def test_config_requires_token_env(self):
        from general_ludd.connectors.okta import OktaSource

        with pytest.raises(ValueError, match="token_env"):
            OktaSource({"org_url": "https://example.okta.com"})

    def test_rejects_private_host(self):
        from general_ludd.connectors.okta import OktaSource

        with pytest.raises(ValueError):
            OktaSource({"org_url": "http://10.0.0.1", "token_env": "OKTA_TOKEN"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        monkeypatch.setenv("OKTA_TEST_TOKEN_B4", "test-token-abc")
        try:
            source = OktaSource(
                {"org_url": "https://example.okta.com", "token_env": "OKTA_TEST_TOKEN_B4"},
            )
            assert source.KIND == "events"
            assert source.name == "okta"
            assert source.max_pages == 10
        finally:
            del os.environ["OKTA_TEST_TOKEN_B4"]

    def test_constructs_custom_name_and_timeout(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        monkeypatch.setenv("OKTA_TOK_B4", "tok")
        try:
            source = OktaSource({
                "org_url": "https://my.okta.com",
                "token_env": "OKTA_TOK_B4",
                "max_pages": 5,
                "timeout": 15,
            })
            assert source.max_pages == 5
            assert source.timeout == 15
        finally:
            del os.environ["OKTA_TOK_B4"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        transport = MockHttpResponseTransport(status_code=200, body=[{"id": "e1"}])
        monkeypatch.setenv("OKTA_TOK_H", "tok")
        try:
            source = OktaSource(
                {"org_url": "https://okta.example.com", "token_env": "OKTA_TOK_H"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["OKTA_TOK_H"]

    def test_health_not_ok_on_bad_status(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("OKTA_TOK_H2", "tok")
        try:
            source = OktaSource(
                {"org_url": "https://ok.example.com", "token_env": "OKTA_TOK_H2"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["OKTA_TOK_H2"]

    def test_health_handles_transport_error(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        def _fail(*_: object, **__: object) -> MockHttpResponse:
            raise OSError("timeout")

        monkeypatch.setenv("OKTA_H3", "tok")
        try:
            source = OktaSource(
                {"org_url": "https://ok.example.com", "token_env": "OKTA_H3"},
                transport=_fail,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["OKTA_H3"]

    def test_query_returns_normalized_records(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body=[
                {
                    "published": "2025-01-01T12:00:00.000Z",
                    "displayMessage": "User logged in",
                    "eventType": "user.session.start",
                    "outcome": {"result": "SUCCESS"},
                    "actor": {"alternateId": "user@example.com"},
                    "client": {"ipAddress": "1.2.3.4"},
                }
            ],
        )
        monkeypatch.setenv("OKTA_Q1", "tok")
        try:
            source = OktaSource(
                {"org_url": "https://ok.example.com", "token_env": "OKTA_Q1"},
                transport=transport,
            )
            records = source.query({"limit": 10})
            assert len(records) == 1
            assert records[0]["level_or_status"] == "SUCCESS"
            assert "User logged in" in str(records[0]["message"])
        finally:
            del os.environ["OKTA_Q1"]

    def test_query_paginates_via_link_header(self, monkeypatch):
        from general_ludd.connectors.okta import OktaSource

        call_count = [0]

        def _transport(method: str, url: str, **kw: object) -> MockHttpResponse:
            call_count[0] += 1
            msg = f"event {call_count[0]}"
            resp = MockHttpResponse(
                200, [{"published": "2025-01-01T00:00:00Z", "displayMessage": msg}]
            )
            if call_count[0] < 3:
                resp.headers = {"Link": '<https://ok.example.com/api/v1/logs?after=2>; rel="next"'}
            return resp

        monkeypatch.setenv("OKTA_PG", "tok")
        try:
            source = OktaSource(
                {"org_url": "https://ok.example.com", "token_env": "OKTA_PG", "max_pages": 5},
                transport=_transport,
            )
            records = source.query({})
            assert len(records) == 3
        finally:
            del os.environ["OKTA_PG"]


# ============================================================================
# 2. Entra (Azure AD) Signin Connector
# ============================================================================


class TestEntraSigninConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        with pytest.raises((ValueError, RuntimeError)):
            EntraSigninSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        monkeypatch.setenv("ENTRA_GRAPH_TOKEN", "graph-token")
        try:
            source = EntraSigninSource({
                "token_env": "ENTRA_GRAPH_TOKEN",
            })
            assert source.name is not None
        finally:
            del os.environ["ENTRA_GRAPH_TOKEN"]

    def test_rejects_private_host(self):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        with pytest.raises((ValueError, RuntimeError)):
            EntraSigninSource({
                "token_env": "ENTRA_GRAPH_TOKEN",
                "base_url": "http://127.0.0.1",
            })

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        transport = MockHttpResponseTransport(status_code=200, body={"value": []})
        monkeypatch.setenv("ENTRA_GRAPH_TOKEN", "graph-token")
        try:
            source = EntraSigninSource(
                {"token_env": "ENTRA_GRAPH_TOKEN"},
                transport=transport,
            )
            result = source.health()
            assert isinstance(result, dict)
        finally:
            del os.environ["ENTRA_GRAPH_TOKEN"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        transport = MockHttpResponseTransport(status_code=401, body={})
        monkeypatch.setenv("ENTRA_GRAPH_TOKEN", "graph-token")
        try:
            source = EntraSigninSource(
                {"token_env": "ENTRA_GRAPH_TOKEN"},
                transport=transport,
            )
            result = source.health()
            assert result.get("ok") is False
        finally:
            del os.environ["ENTRA_GRAPH_TOKEN"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.entra_signin import EntraSigninSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "value": [
                    {
                        "id": "s1",
                        "createdDateTime": "2025-01-01T12:00:00Z",
                        "userPrincipalName": "user@example.com",
                        "status": {"errorCode": 0},
                        "ipAddress": "1.2.3.4",
                    }
                ]
            },
        )
        monkeypatch.setenv("ENTRA_GRAPH_TOKEN", "graph-token")
        try:
            source = EntraSigninSource(
                {"token_env": "ENTRA_GRAPH_TOKEN"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] in ("logs", "events")
        finally:
            del os.environ["ENTRA_GRAPH_TOKEN"]


# ============================================================================
# 3. ServiceNow Connector
# ============================================================================


class TestServiceNowConnector:
    def test_config_requires_instance(self):
        from general_ludd.connectors.servicenow import ServiceNowSource

        with pytest.raises((ValueError, RuntimeError)):
            ServiceNowSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.servicenow import ServiceNowSource

        monkeypatch.setenv("SN_USER_B4", "admin")
        monkeypatch.setenv("SN_PASS_B4", "pass")
        try:
            source = ServiceNowSource({
                "instance": "dev12345",
                "user_env": "SN_USER_B4",
                "pass_env": "SN_PASS_B4",
            })
            assert source.KIND == "tickets"
            assert source.name == "servicenow"
            assert source.instance == "dev12345"
        finally:
            del os.environ["SN_USER_B4"], os.environ["SN_PASS_B4"]

    def test_rejects_private_host(self):
        from general_ludd.connectors.servicenow import ServiceNowSource

        with pytest.raises((ValueError, RuntimeError)):
            ServiceNowSource({"instance": "x", "base_url": "http://10.0.0.1"})

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.servicenow import ServiceNowSource

        transport = MockHttpResponseTransport(status_code=200, body={"result": []})
        monkeypatch.setenv("SN_U", "u")
        monkeypatch.setenv("SN_P", "p")
        try:
            source = ServiceNowSource(
                {"instance": "dev", "user_env": "SN_U", "pass_env": "SN_P"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["SN_U"], os.environ["SN_P"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.servicenow import ServiceNowSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("SN_U2", "u")
        monkeypatch.setenv("SN_P2", "p")
        try:
            source = ServiceNowSource(
                {"instance": "dev", "user_env": "SN_U2", "pass_env": "SN_P2"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["SN_U2"], os.environ["SN_P2"]

    def test_query_returns_normalized_records(self, monkeypatch):
        from general_ludd.connectors.servicenow import ServiceNowSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "result": [
                    {
                        "number": "INC001",
                        "short_description": "Server down",
                        "state": "2",
                        "opened_at": "2025-01-01T12:00:00",
                        "caller_id": {"value": "user1"},
                    }
                ]
            },
        )
        monkeypatch.setenv("SN_U3", "u")
        monkeypatch.setenv("SN_P3", "p")
        try:
            source = ServiceNowSource(
                {"instance": "dev", "user_env": "SN_U3", "pass_env": "SN_P3"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "tickets"
            assert records[0]["message"] == "Server down"
            assert records[0]["labels"]["number"] == "INC001"
        finally:
            del os.environ["SN_U3"], os.environ["SN_P3"]


# ============================================================================
# 4. Zendesk Connector
# ============================================================================


class TestZendeskConnector:
    def test_config_requires_subdomain(self):
        from general_ludd.connectors.zendesk import ZendeskSource

        with pytest.raises((ValueError, RuntimeError)):
            ZendeskSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.zendesk import ZendeskSource

        monkeypatch.setenv("ZD_EMAIL_B4", "agent@example.com")
        monkeypatch.setenv("ZD_TOK_B4", "tok")
        try:
            source = ZendeskSource({
                "subdomain": "mycompany",
                "email_env": "ZD_EMAIL_B4",
                "token_env": "ZD_TOK_B4",
            })
            assert source.KIND == "tickets"
        finally:
            del os.environ["ZD_EMAIL_B4"], os.environ["ZD_TOK_B4"]

    def test_rejects_private_host(self):
        from general_ludd.connectors.zendesk import ZendeskSource

        with pytest.raises(ValueError, match=r"private|loopback"):
            ZendeskSource({
                "subdomain": "127.0.0.1",
                "email_env": "ZD_EMAIL_PRIVATE",
                "token_env": "ZD_TOKEN_PRIVATE",
            })

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.zendesk import ZendeskSource

        transport = MockHttpResponseTransport(status_code=200, body={"tickets": []})
        monkeypatch.setenv("ZD_EMAIL", "agent@example.com")
        monkeypatch.setenv("ZD_TOK", "tok")
        try:
            source = ZendeskSource(
                {
                    "subdomain": "test",
                    "email_env": "ZD_EMAIL",
                    "token_env": "ZD_TOK",
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["ZD_EMAIL"], os.environ["ZD_TOK"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.zendesk import ZendeskSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("ZD_EMAIL_2", "agent@example.com")
        monkeypatch.setenv("ZD_TOK_2", "tok")
        try:
            source = ZendeskSource(
                {
                    "subdomain": "test",
                    "email_env": "ZD_EMAIL_2",
                    "token_env": "ZD_TOK_2",
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["ZD_EMAIL_2"], os.environ["ZD_TOK_2"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.zendesk import ZendeskSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "tickets": [
                    {
                        "id": 1,
                        "subject": "Help!",
                        "status": "open",
                        "created_at": "2025-01-01T12:00:00Z",
                    }
                ]
            },
        )
        monkeypatch.setenv("ZD_EMAIL_Q", "agent@example.com")
        monkeypatch.setenv("ZD_T_Q", "tok")
        try:
            source = ZendeskSource(
                {
                    "subdomain": "test",
                    "email_env": "ZD_EMAIL_Q",
                    "token_env": "ZD_T_Q",
                },
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "tickets"
        finally:
            del os.environ["ZD_EMAIL_Q"], os.environ["ZD_T_Q"]


# ============================================================================
# 5. Bugsnag Connector
# ============================================================================


class TestBugsnagConnector:
    def test_config_requires_project_id(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        from general_ludd.connectors.bugsnag import BugsnagSource

        with pytest.raises(ConnectorConfigError, match="project_id"):
            BugsnagSource({}, transport=MockHttpResponseTransport())

    def test_config_requires_token_env(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        from general_ludd.connectors.bugsnag import BugsnagSource

        with pytest.raises(ConnectorConfigError, match="token_env"):
            BugsnagSource({"project_id": "abc"}, transport=MockHttpResponseTransport())

    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.bugsnag import BugsnagSource

        source = BugsnagSource(
            {"project_id": "proj1", "token_env": "BUGSNAG_TOK", "name": "my-bugsnag"},
            transport=MockHttpResponseTransport(),
            environ={"BUGSNAG_TOK": "test-token"},
        )
        assert source.name == "my-bugsnag"
        assert source.KIND == "logs"

    def test_rejects_private_host(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        from general_ludd.connectors.bugsnag import BugsnagSource

        with pytest.raises(ConnectorConfigError):
            BugsnagSource(
                {"project_id": "x", "token_env": "T", "base_url": "http://10.0.0.1"},
                transport=MockHttpResponseTransport(),
                environ={"T": "tok"},
            )

    def test_health_ok(self):
        from general_ludd.connectors.bugsnag import BugsnagSource

        transport = MockHttpResponseTransport(status_code=200, body=[])
        source = BugsnagSource(
            {"project_id": "p1", "token_env": "B_T"},
            transport=transport,
            environ={"B_T": "tok"},
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.bugsnag import BugsnagSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        source = BugsnagSource(
            {"project_id": "p1", "token_env": "B_T2"},
            transport=transport,
            environ={"B_T2": "tok"},
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_errors(self):
        from general_ludd.connectors.bugsnag import BugsnagSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body=[
                {
                    "id": "err1",
                    "error_class": "RuntimeError",
                    "message": "Something broke",
                    "severity": "error",
                    "events": 5,
                    "last_seen": "2025-01-01T12:00:00Z",
                    "status": "open",
                    "release_stage": "production",
                }
            ],
        )
        source = BugsnagSource(
            {"project_id": "p1", "token_env": "B_Q"},
            transport=transport,
            environ={"B_Q": "tok"},
        )
        records = source.query({})
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["level_or_status"] == "error"
        assert "RuntimeError" in str(records[0]["message"])


# ============================================================================
# 6. Rollbar Connector
# ============================================================================


class TestRollbarConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.rollbar import RollbarSource

        with pytest.raises(ValueError, match="token_env"):
            RollbarSource({}, MockHttpResponseTransport())

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.rollbar import RollbarSource

        monkeypatch.setenv("ROLLBAR_TOK", "tok")
        try:
            source = RollbarSource(
                {"token_env": "ROLLBAR_TOK", "name": "my-rollbar"},
                MockHttpResponseTransport(),
            )
            assert source.name == "my-rollbar"
        finally:
            del os.environ["ROLLBAR_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.rollbar import RollbarSource

        transport = MockHttpResponseTransport(status_code=200, body={"result": []})
        monkeypatch.setenv("RB_TOK", "tok")
        try:
            source = RollbarSource({"token_env": "RB_TOK"}, transport=transport)
            result = source.health()
            assert isinstance(result, dict)
        finally:
            del os.environ["RB_TOK"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.rollbar import RollbarSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("RB_TOK2", "tok")
        try:
            source = RollbarSource({"token_env": "RB_TOK2"}, transport=transport)
            result = source.health()
            assert result.get("ok") is False
        finally:
            del os.environ["RB_TOK2"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.rollbar import RollbarSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "result": {
                    "items": [
                        {
                            "id": 1,
                            "title": "Error in api",
                            "status": "active",
                            "total_occurrences": 42,
                            "last_occurrence_timestamp": 1700000000,
                        }
                    ]
                }
            },
        )
        monkeypatch.setenv("RB_Q", "tok")
        try:
            source = RollbarSource({"token_env": "RB_Q"}, transport=transport)
            records = source.query({})
            assert len(records) == 1
            assert records[0]["kind"] == "logs"
            assert records[0]["message"] == "Error in api"
            assert records[0]["value"] == 42
            assert records[0]["ts"] == 1700000000
        finally:
            del os.environ["RB_Q"]


# ============================================================================
# 7. Graylog Connector
# ============================================================================


class TestGraylogConnector:
    def test_config_requires_base_url(self):
        from general_ludd.connectors.graylog import GraylogSource

        with pytest.raises((ValueError, RuntimeError)):
            GraylogSource({})

    def test_config_requires_token_env(self):
        from general_ludd.connectors.graylog import GraylogSource

        with pytest.raises((ValueError, RuntimeError)):
            GraylogSource({"base_url": "https://graylog.example.com"})

    def test_rejects_private_host(self):
        from general_ludd.connectors.graylog import GraylogSource

        with pytest.raises((ValueError, RuntimeError)):
            GraylogSource({"base_url": "http://127.0.0.1", "token_env": "G_T"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.graylog import GraylogSource

        monkeypatch.setenv("GL_TOK", "tok")
        try:
            source = GraylogSource(
                {"base_url": "https://graylog.example.com", "token_env": "GL_TOK"},
            )
            assert source.KIND == "logs"
            assert source.name == "graylog"
        finally:
            del os.environ["GL_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.graylog import GraylogSource

        transport = MockHttpResponseTransport(status_code=200, body={"messages": []})
        monkeypatch.setenv("GL_H", "tok")
        try:
            source = GraylogSource(
                {"base_url": "https://gl.example.com", "token_env": "GL_H"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["GL_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.graylog import GraylogSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("GL_H2", "tok")
        try:
            source = GraylogSource(
                {"base_url": "https://gl.example.com", "token_env": "GL_H2"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["GL_H2"]

    def test_query_returns_normalized_records(self, monkeypatch):
        from general_ludd.connectors.graylog import GraylogSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "messages": [
                    {
                        "message": {
                            "message": "Connection refused",
                            "timestamp": "2025-01-01T12:00:00.000Z",
                            "source": "app-server-1",
                            "level": 3,
                        }
                    }
                ]
            },
        )
        monkeypatch.setenv("GL_Q", "tok")
        try:
            source = GraylogSource(
                {"base_url": "https://gl.example.com", "token_env": "GL_Q"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "logs"
        finally:
            del os.environ["GL_Q"]


# ============================================================================
# 8. Syslog File Connector
# ============================================================================


class TestSyslogFileConnector:
    def test_config_requires_root(self):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        with pytest.raises((ValueError, RuntimeError)):
            SyslogFileSource({})

    def test_constructs_with_valid_config(self, tmp_path):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        source = SyslogFileSource({"root": str(tmp_path), "name": "syslog-prod"})
        assert source.name == "syslog-prod"
        assert source.KIND == "logs"

    def test_health_ok_when_root_readable(self, tmp_path):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        source = SyslogFileSource({"root": str(tmp_path)})
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_root_missing(self, tmp_path):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        source = SyslogFileSource({"root": str(tmp_path / "missing")})
        result = source.health()
        assert result["ok"] is False

    def test_query_reads_lines(self, tmp_path):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        content = "Jan  1 12:00:00 host1 message one\nJan  1 12:01:00 host2 message two\n"
        log_path = tmp_path / "syslog.log"
        log_path.write_text(content)
        source = SyslogFileSource({"root": str(tmp_path)})
        records = source.query({"path": log_path.name})
        assert len(records) == 2
        assert records[0]["kind"] == "logs"

    def test_query_empty_on_empty_file(self, tmp_path):
        from general_ludd.connectors.syslog_file import SyslogFileSource

        log_path = tmp_path / "empty.log"
        log_path.write_text("")
        source = SyslogFileSource({"root": str(tmp_path)})
        records = source.query({"path": log_path.name})
        assert records == []


# ============================================================================
# 9. Journald Connector
# ============================================================================


class TestJournaldConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.journald import JournaldSource

        source = JournaldSource()
        assert source.KIND == "logs"
        assert source.name == "journald"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.journald import JournaldSource

        source = JournaldSource({"name": "my-journal"})
        assert source.name == "my-journal"

    def test_health_ok_with_injected_runner(self):
        from general_ludd.connectors.journald import JournaldSource

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            assert argv == ["journalctl", "--version"]
            return (0, "systemd 255 (255.4)\n", "")

        source = JournaldSource(runner=_runner)
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        import json

        from general_ludd.connectors.journald import JournaldSource

        entries = [
                {"MESSAGE": "line 1", "_HOSTNAME": "host1", "__REALTIME_TIMESTAMP": "1700000000000000"},
                {"MESSAGE": "line 2", "_HOSTNAME": "host2", "__REALTIME_TIMESTAMP": "1700000001000000"},
        ]

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            return (0, "\n".join(json.dumps(entry) for entry in entries), "")

        source = JournaldSource(runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert records[0]["kind"] == "logs"

    def test_query_empty_on_runner_error(self):
        from general_ludd.connectors.journald import JournaldSource

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            return (1, "", "journalctl unavailable")

        source = JournaldSource(runner=_runner)
        records = source.query({})
        assert records == []


# ============================================================================
# 10. Linear Connector
# ============================================================================


class TestLinearConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.linear import LinearSource

        with pytest.raises(ValueError, match="token_env"):
            LinearSource({"team_id": "TEAM1"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.linear import LinearSource

        monkeypatch.setenv("LIN_TOK", "lin-api-key")
        try:
            source = LinearSource({"token_env": "LIN_TOK", "team_id": "TEAM1"})
            assert source.KIND == "tickets"
        finally:
            del os.environ["LIN_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.linear import LinearSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={"data": {"viewer": {"id": "u1", "name": "Test User"}}},
        )
        monkeypatch.setenv("LIN_H", "tok")
        try:
            source = LinearSource(
                {"token_env": "LIN_H", "team_id": "TEAM1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["LIN_H"]

    def test_health_not_ok_on_401(self, monkeypatch):
        from general_ludd.connectors.linear import LinearSource

        transport = MockHttpResponseTransport(status_code=401, body={"errors": [{"message": "unauthorized"}]})
        monkeypatch.setenv("LIN_H2", "tok")
        try:
            source = LinearSource(
                {"token_env": "LIN_H2", "team_id": "TEAM1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["LIN_H2"]

    def test_query_returns_normalized_tickets(self, monkeypatch):
        from general_ludd.connectors.linear import LinearSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "data": {
                    "team": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": "i1",
                                    "title": "Fix login bug",
                                    "state": {"name": "In Progress"},
                                    "createdAt": "2025-01-01T12:00:00.000Z",
                                    "updatedAt": "2025-01-02T12:00:00.000Z",
                                    "assignee": {"name": "Alice"},
                                    "priority": 1,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            },
        )
        monkeypatch.setenv("LIN_Q", "tok")
        try:
            source = LinearSource({"token_env": "LIN_Q", "team_id": "T1"}, transport=transport)
            records = source.query({})
            assert len(records) == 1
            assert records[0]["kind"] == "tickets"
            assert "Fix login bug" in str(records[0]["message"])
        finally:
            del os.environ["LIN_Q"]

    def test_query_propagates_transport_error(self, monkeypatch):
        from general_ludd.connectors.linear import LinearSource

        def _fail(*_: object, **__: object) -> MockHttpResponse:
            raise OSError("network down")

        monkeypatch.setenv("LIN_ERR", "tok")
        try:
            source = LinearSource(
                {"token_env": "LIN_ERR", "team_id": "TEAM1"},
                transport=_fail,
            )
            with pytest.raises(OSError, match="network down"):
                source.query({})
        finally:
            del os.environ["LIN_ERR"]


# ============================================================================
# 11. Notion Connector
# ============================================================================


class TestNotionConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.notion import NotionSource

        with pytest.raises((ValueError, RuntimeError)):
            NotionSource({})

    def test_config_requires_database_id(self, monkeypatch):
        from general_ludd.connectors.notion import NotionSource

        monkeypatch.setenv("NOT_TOK", "secret")
        try:
            with pytest.raises((ValueError, RuntimeError)):
                NotionSource({"token_env": "NOT_TOK"})
        finally:
            del os.environ["NOT_TOK"]

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.notion import NotionSource

        monkeypatch.setenv("NOT_TOK", "secret")
        try:
            source = NotionSource({
                "token_env": "NOT_TOK",
                "database_id": "db123",
            })
            assert source.name == "notion"
        finally:
            del os.environ["NOT_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.notion import NotionSource

        transport = MockHttpResponseTransport(status_code=200, body={"results": []})
        monkeypatch.setenv("NOT_H", "s")
        try:
            source = NotionSource(
                {"token_env": "NOT_H", "database_id": "db1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["NOT_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.notion import NotionSource

        transport = MockHttpResponseTransport(status_code=403, body={})
        monkeypatch.setenv("NOT_H2", "s")
        try:
            source = NotionSource(
                {"token_env": "NOT_H2", "database_id": "db1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["NOT_H2"]

    def test_query_returns_pages(self, monkeypatch):
        from general_ludd.connectors.notion import NotionSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "results": [
                    {
                        "id": "p1",
                        "created_time": "2025-01-01T12:00:00.000Z",
                        "last_edited_time": "2025-01-02T12:00:00.000Z",
                        "properties": {
                            "Name": {"title": [{"plain_text": "Task 1"}]},
                            "Status": {"status": {"name": "In Progress"}},
                        },
                    }
                ]
            },
        )
        monkeypatch.setenv("NOT_Q", "s")
        try:
            source = NotionSource(
                {"token_env": "NOT_Q", "database_id": "db1"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "pages"
        finally:
            del os.environ["NOT_Q"]


# ============================================================================
# 12. Trello Connector
# ============================================================================


class TestTrelloConnector:
    def test_config_requires_key_and_token(self):
        from general_ludd.connectors.trello import TrelloSource

        with pytest.raises((ValueError, RuntimeError)):
            TrelloSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.trello import TrelloSource

        monkeypatch.setenv("TRELLO_KEY", "key")
        monkeypatch.setenv("TRELLO_TOKEN", "tok")
        try:
            source = TrelloSource({
                "key_env": "TRELLO_KEY",
                "token_env": "TRELLO_TOKEN",
                "board_id": "board1",
            })
            assert source.KIND == "tasks"
        finally:
            del os.environ["TRELLO_KEY"], os.environ["TRELLO_TOKEN"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.trello import TrelloSource

        transport = MockHttpResponseTransport(status_code=200, body=[])
        monkeypatch.setenv("TK", "k")
        monkeypatch.setenv("TT", "t")
        try:
            source = TrelloSource(
                {"key_env": "TK", "token_env": "TT", "board_id": "b1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["TK"], os.environ["TT"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.trello import TrelloSource

        transport = MockHttpResponseTransport(status_code=404, body="not found")
        monkeypatch.setenv("TK2", "k")
        monkeypatch.setenv("TT2", "t")
        try:
            source = TrelloSource(
                {"key_env": "TK2", "token_env": "TT2", "board_id": "bad"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["TK2"], os.environ["TT2"]

    def test_query_returns_cards(self, monkeypatch):
        from general_ludd.connectors.trello import TrelloSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body=[
                {
                    "id": "c1",
                    "name": "Fix bug",
                    "idList": "list1",
                    "dateLastActivity": "2025-01-01T12:00:00.000Z",
                    "desc": "description here",
                }
            ],
        )
        monkeypatch.setenv("TK3", "k")
        monkeypatch.setenv("TT3", "t")
        try:
            source = TrelloSource(
                {"key_env": "TK3", "token_env": "TT3", "board_id": "b1"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "tasks"
            assert "Fix bug" in str(records[0]["message"])
        finally:
            del os.environ["TK3"], os.environ["TT3"]


# ============================================================================
# 13. Airtable Connector
# ============================================================================


class TestAirtableConnector:
    def test_config_requires_base_id(self):
        from general_ludd.connectors.airtable import AirtableSource

        with pytest.raises((ValueError, RuntimeError)):
            AirtableSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.airtable import AirtableSource

        monkeypatch.setenv("AT_TOK", "tok")
        try:
            source = AirtableSource({
                "token_env": "AT_TOK",
                "base_id": "app123",
                "table_name": "Tasks",
            })
            assert source.KIND == "records"
        finally:
            del os.environ["AT_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.airtable import AirtableSource

        transport = MockHttpResponseTransport(status_code=200, body={"records": []})
        monkeypatch.setenv("AT_H", "tok")
        try:
            source = AirtableSource(
                {
                    "token_env": "AT_H",
                    "base_id": "app1",
                    "table_name": "Tasks",
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["AT_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.airtable import AirtableSource

        transport = MockHttpResponseTransport(status_code=403, body={})
        monkeypatch.setenv("AT_H2", "tok")
        try:
            source = AirtableSource(
                {
                    "token_env": "AT_H2",
                    "base_id": "app1",
                    "table_name": "Tasks",
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["AT_H2"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.airtable import AirtableSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "records": [
                    {
                        "id": "rec1",
                        "createdTime": "2025-01-01T12:00:00.000Z",
                        "fields": {"Name": "Task A", "Status": "Done"},
                    }
                ]
            },
        )
        monkeypatch.setenv("AT_Q", "tok")
        try:
            source = AirtableSource(
                {
                    "token_env": "AT_Q",
                    "base_id": "app1",
                    "table_name": "Tasks",
                },
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "records"
        finally:
            del os.environ["AT_Q"]


# ============================================================================
# 14. Asana Connector
# ============================================================================


class TestAsanaConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.asana import AsanaSource

        with pytest.raises(ValueError, match="token_env"):
            AsanaSource({"project_gid": "project1"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.asana import AsanaSource

        monkeypatch.setenv("ASANA_TOK", "tok")
        try:
            source = AsanaSource({
                "token_env": "ASANA_TOK",
                "project_gid": "project1",
            })
            assert source.name == "asana"
            assert source.project_gid == "project1"
        finally:
            del os.environ["ASANA_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.asana import AsanaSource

        transport = MockHttpResponseTransport(status_code=200, body={"data": []})
        monkeypatch.setenv("AS_H", "tok")
        try:
            source = AsanaSource(
                {"token_env": "AS_H", "project_gid": "project1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["AS_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.asana import AsanaSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        monkeypatch.setenv("AS_H2", "tok")
        try:
            source = AsanaSource(
                {"token_env": "AS_H2", "project_gid": "project1"},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["AS_H2"]

    def test_query_returns_tasks(self, monkeypatch):
        from general_ludd.connectors.asana import AsanaSource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "data": [
                    {
                        "gid": "t1",
                        "name": "Review PR",
                        "resource_type": "task",
                        "created_at": "2025-01-01T12:00:00.000Z",
                        "completed": False,
                        "assignee": {"gid": "u1", "name": "Bob"},
                    }
                ]
            },
        )
        monkeypatch.setenv("AS_Q", "tok")
        try:
            source = AsanaSource(
                {"token_env": "AS_Q", "project_gid": "project1"},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "tasks"
        finally:
            del os.environ["AS_Q"]


# ============================================================================
# 15. Monday.com Connector
# ============================================================================


class TestMondayConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.monday import MondaySource

        with pytest.raises(ValueError, match="token_env"):
            MondaySource({"board_ids": [1]})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.monday import MondaySource

        monkeypatch.setenv("MON_TOK", "tok")
        try:
            source = MondaySource({
                "token_env": "MON_TOK",
                "board_ids": [1, 2],
            })
            assert source.KIND == "tasks"
            assert source.board_ids == [1, 2]
        finally:
            del os.environ["MON_TOK"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.monday import MondaySource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={"data": {"me": {"id": "u1"}}},
        )
        monkeypatch.setenv("MON_H", "tok")
        try:
            source = MondaySource(
                {"token_env": "MON_H", "board_ids": [1]},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["MON_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.monday import MondaySource

        transport = MockHttpResponseTransport(status_code=401, body={})
        monkeypatch.setenv("MON_H2", "tok")
        try:
            source = MondaySource(
                {"token_env": "MON_H2", "board_ids": [1]},
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["MON_H2"]

    def test_query_returns_items(self, monkeypatch):
        from general_ludd.connectors.monday import MondaySource

        transport = MockHttpResponseTransport(
            status_code=200,
            body={
                "data": {
                    "boards": [
                        {
                            "items_page": {
                                "items": [
                                    {
                                        "id": "i1",
                                        "name": "Fix pipeline",
                                        "state": "In Progress",
                                        "created_at": "2025-01-01T12:00:00Z",
                                        "updated_at": "2025-01-02T12:00:00Z",
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
        )
        monkeypatch.setenv("MON_Q", "tok")
        try:
            source = MondaySource(
                {"token_env": "MON_Q", "board_ids": [1]},
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "tasks"
        finally:
            del os.environ["MON_Q"]


# ============================================================================
# 16. Pyroscope Connector
# ============================================================================


class TestPyroscopeConnector:
    def test_config_requires_base_url(self):
        from general_ludd.connectors.pyroscope import PyroscopeSource

        with pytest.raises(ValueError, match="base_url"):
            PyroscopeSource({})

    def test_constructs_custom_config(self):
        from general_ludd.connectors.pyroscope import PyroscopeSource

        source = PyroscopeSource({"base_url": "https://pyroscope.example.com", "name": "pyro-prod"})
        assert source.name == "pyro-prod"
        assert source.KIND == "traces"

    def test_health_ok_with_transport(self):
        from general_ludd.connectors.pyroscope import PyroscopeSource

        transport = MockHttpResponseTransport(status_code=200, body={"labels": []})
        source = PyroscopeSource(
            {"base_url": "https://pyro.example.com"},
            transport=transport,
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.pyroscope import PyroscopeSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        source = PyroscopeSource(
            {"base_url": "https://pyro.example.com"},
            transport=transport,
        )
        result = source.health()
        assert result["ok"] is False


# ============================================================================
# 17. Parca Connector
# ============================================================================


class TestParcaConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.parca import ParcaSource

        source = ParcaSource()
        assert source.KIND == "metrics"
        assert source.name == "parca"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.parca import ParcaSource

        source = ParcaSource({"base_url": "https://parca.example.com", "name": "parca-prod"})
        assert source.name == "parca-prod"

    def test_health_ok_with_transport(self):
        from general_ludd.connectors.parca import ParcaSource

        transport = MockHttpResponseTransport(status_code=200, body={"labels": []})
        source = ParcaSource(
            {"base_url": "https://parca.example.com"},
            transport=transport,
        )
        result = source.health()
        assert isinstance(result, dict)

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.parca import ParcaSource

        transport = MockHttpResponseTransport(status_code=500, body={})
        source = ParcaSource(
            {"base_url": "https://parca.example.com"},
            transport=transport,
        )
        result = source.health()
        assert result["ok"] is False


# ============================================================================
# 18. macOS Unified Log Connector
# ============================================================================


class TestMacUnifiedLogConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.mac_unified_log import MacUnifiedLogSource

        source = MacUnifiedLogSource()
        assert source.KIND == "logs"
        assert source.name == "mac_unified_log"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.mac_unified_log import MacUnifiedLogSource

        source = MacUnifiedLogSource({"predicate": 'process == "sshd"', "name": "ssh-audit"})
        assert source.name == "ssh-audit"

    def test_health_ok_with_injected_executor(self):
        from general_ludd.connectors.mac_unified_log import MacUnifiedLogSource

        def _executor(**kw: object) -> list[dict[str, object]]:
            return [{"timestamp": "2025-01-01T12:00:00Z", "message": "test"}]

        source = MacUnifiedLogSource(executor=_executor)
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.mac_unified_log import MacUnifiedLogSource

        def _executor(**kw: object) -> list[dict[str, object]]:
            return [
                {
                    "timestamp": "2025-01-01T12:00:00Z",
                    "message": "Connection from 1.2.3.4",
                    "processImagePath": "/usr/sbin/sshd",
                },
                {
                    "timestamp": "2025-01-01T12:01:00Z",
                    "message": "Accepted publickey",
                    "processImagePath": "/usr/sbin/sshd",
                },
            ]

        source = MacUnifiedLogSource(executor=_executor)
        records = source.query({})
        assert len(records) >= 2
        assert records[0]["kind"] == "logs"

    def test_query_empty_without_executor(self):
        from general_ludd.connectors.mac_unified_log import MacUnifiedLogSource

        source = MacUnifiedLogSource()
        records = source.query({})
        assert records == []
