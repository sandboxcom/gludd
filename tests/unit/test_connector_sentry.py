"""Unit tests for Sentry observability connector.

Covers all public and private members of
src/general_ludd/connectors/sentry.py aiming for 85%+ branch coverage.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.connectors.sentry import (
    _DEFAULT_BASE_URL,
    _DEFAULT_LIMIT,
    _DEFAULT_STATS_PERIOD,
    _DEFAULT_TIMEOUT,
    _SentryResponse,
    _UrllibTransport,
    SentrySource,
    Transport,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_VALID_CONFIG: dict[str, object] = {
    "token_env": "SENTRY_TOKEN",
    "org": "acme",
    "project": "backend",
}


def _make_config(**overrides: object) -> dict[str, object]:
    config = dict(_VALID_CONFIG)
    config.update(overrides)
    return config


def _make_source(**overrides: object) -> SentrySource:
    return SentrySource(_make_config(**overrides))


def _mock_transport(**kwargs: object) -> MagicMock:
    transport = MagicMock(spec=Transport)
    for attr, val in kwargs.items():
        setattr(transport, attr, val)
    return transport


def _response(status: int, body: bytes | str | object = b"{}") -> _SentryResponse:
    if isinstance(body, dict):
        body = json.dumps(body).encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return _SentryResponse(status, body)


# --------------------------------------------------------------------------- #
# _SentryResponse
# --------------------------------------------------------------------------- #


class TestSentryResponse:
    def test_init_with_bytes_body(self) -> None:
        r = _SentryResponse(200, b'{"ok":true}')
        assert r.status == 200
        assert r._body == b'{"ok":true}'

    def test_init_with_str_body(self) -> None:
        r = _SentryResponse(201, '{"created":true}')
        assert r.status == 201
        assert r._body == '{"created":true}'

    def test_text_from_bytes(self) -> None:
        r = _SentryResponse(200, b"hello")
        assert r.text == "hello"

    def test_text_from_str_passthrough(self) -> None:
        r = _SentryResponse(200, "already str")
        assert r.text == "already str"

    def test_text_decode_errors_replace(self) -> None:
        r = _SentryResponse(200, b"\xff\xfeinvalid")
        result = r.text
        assert "\ufffd" in result

    def test_json_valid(self) -> None:
        r = _SentryResponse(200, b'{"a":1}')
        assert r.json() == {"a": 1}

    def test_json_empty_body(self) -> None:
        r = _SentryResponse(200, b"")
        assert r.json() is None

    def test_json_whitespace_only(self) -> None:
        r = _SentryResponse(200, b"   \n\t  ")
        assert r.json() is None

    def test_json_invalid_raises_json_decode_error(self) -> None:
        r = _SentryResponse(200, b"not json")
        with pytest.raises(json.JSONDecodeError):
            r.json()


# --------------------------------------------------------------------------- #
# Transport (Protocol)
# --------------------------------------------------------------------------- #


class TestTransportProtocol:
    def test_isinstance_with_transport_implementation(self) -> None:
        transport = _UrllibTransport()
        assert isinstance(transport, Transport)

    def test_isinstance_with_mock(self) -> None:
        mock = _mock_transport()
        assert isinstance(mock, Transport)

    def test_isinstance_rejects_plain_object(self) -> None:
        class NotATransport:
            pass

        assert not isinstance(NotATransport(), Transport)


# --------------------------------------------------------------------------- #
# _UrllibTransport
# --------------------------------------------------------------------------- #


class TestUrllibTransport:
    def test_get_happy_path(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b'{"data":"ok"}'
            mock_client.get.return_value = mock_response
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            transport = _UrllibTransport()
            result = transport.get(
                "https://example.com/api",
                headers={"Accept": "application/json"},
                timeout=15.0,
            )

        mock_httpx.Client.assert_called_once_with(
            timeout=15.0, follow_redirects=False
        )
        mock_client.get.assert_called_once_with(
            "https://example.com/api", headers={"Accept": "application/json"}
        )
        assert result.status == 200
        assert result._body == b'{"data":"ok"}'

    def test_get_connect_error_propagates(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            transport = _UrllibTransport()
            with pytest.raises(httpx.ConnectError):
                transport.get(
                    "https://down.example.com/",
                    headers={},
                    timeout=5.0,
                )

    def test_get_timeout_propagates(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.side_effect = httpx.TimeoutException("timed out")
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            transport = _UrllibTransport()
            with pytest.raises(httpx.TimeoutException):
                transport.get(
                    "https://slow.example.com/",
                    headers={},
                    timeout=0.1,
                )

    def test_follow_redirects_is_false(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_client.get.return_value = mock_response
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            _UrllibTransport().get(
                "https://a.com/", headers={}, timeout=10.0
            )

        mock_httpx.Client.assert_called_once_with(
            timeout=10.0, follow_redirects=False
        )

    def test_headers_forwarded(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_client.get.return_value = mock_response
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            _UrllibTransport().get(
                "https://a.com/",
                headers={"X-Custom": "value", "Authorization": "Bearer abc"},
                timeout=10.0,
            )

        mock_client.get.assert_called_once_with(
            "https://a.com/",
            headers={"X-Custom": "value", "Authorization": "Bearer abc"},
        )

    def test_timeout_forwarded(self) -> None:
        with patch("general_ludd.connectors.sentry.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_client.get.return_value = mock_response
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_httpx.Client.return_value = mock_client

            _UrllibTransport().get(
                "https://a.com/", headers={}, timeout=42.5
            )

        mock_httpx.Client.assert_called_once_with(
            timeout=42.5, follow_redirects=False
        )


# --------------------------------------------------------------------------- #
# SentrySource.__init__
# --------------------------------------------------------------------------- #


class TestSentrySourceInit:
    def test_non_dict_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="config must be a dict"):
            SentrySource("not-a-dict")  # type: ignore[arg-type]

    def test_missing_token_env_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            SentrySource({"org": "a", "project": "b"})

    def test_missing_org_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="org"):
            SentrySource({"token_env": "T", "project": "b"})

    def test_missing_project_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="project"):
            SentrySource({"token_env": "T", "org": "a"})

    def test_valid_config_sets_attributes(self) -> None:
        source = _make_source()
        assert source.name == "sentry"
        assert source._token_env == "SENTRY_TOKEN"
        assert source.org == "acme"
        assert source.project == "backend"
        assert source.base_url == _DEFAULT_BASE_URL
        assert source.timeout == _DEFAULT_TIMEOUT
        assert isinstance(source._transport, _UrllibTransport)
        assert source.KIND == "logs"

    def test_custom_name(self) -> None:
        source = _make_source(name="my-sentry")
        assert source.name == "my-sentry"

    def test_name_falsy_defaults_to_sentry(self) -> None:
        source = _make_source(name="")
        assert source.name == "sentry"

    def test_name_none_defaults_to_sentry(self) -> None:
        source = _make_source(name=None)
        assert source.name == "sentry"

    def test_custom_base_url(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            source = _make_source(base_url="https://sentry.example.com")
        assert source.base_url == "https://sentry.example.com"

    def test_base_url_trailing_slash_stripped(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            source = _make_source(base_url="https://sentry.example.com/")
        assert source.base_url == "https://sentry.example.com"

    def test_base_url_multiple_trailing_slashes(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            source = _make_source(base_url="https://sentry.example.com///")
        assert source.base_url == "https://sentry.example.com"

    def test_base_url_non_http_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url must be http"):
            _make_source(base_url="ftp://evil.com")

    def test_base_url_no_host_raises(self) -> None:
        with pytest.raises(ValueError, match="no host"):
            _make_source(base_url="https://")

    def test_base_url_ssrf_blocked_raises(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=True
        ):
            with pytest.raises(ValueError, match="SSRF guard"):
                _make_source(base_url="http://127.0.0.1/api")

    def test_default_base_url(self) -> None:
        source = _make_source()
        assert source.base_url == _DEFAULT_BASE_URL

    def test_timeout_default(self) -> None:
        source = _make_source()
        assert source.timeout == _DEFAULT_TIMEOUT

    def test_timeout_int(self) -> None:
        source = _make_source(timeout=60)
        assert source.timeout == 60.0
        assert isinstance(source.timeout, float)

    def test_timeout_float(self) -> None:
        source = _make_source(timeout=12.5)
        assert source.timeout == 12.5

    def test_timeout_string(self) -> None:
        source = _make_source(timeout="45")
        assert source.timeout == 45.0

    def test_timeout_invalid_string(self) -> None:
        with pytest.raises(ValueError, match="timeout.*number"):
            _make_source(timeout="abc")

    def test_timeout_none_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout.*number"):
            _make_source(timeout=None)

    def test_injected_transport(self) -> None:
        mock_transport = _mock_transport()
        source = _make_source(transport=mock_transport)
        assert source._transport is mock_transport

    def test_base_url_present_is_stored(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            source = _make_source(base_url="http://my.sentry.io")
        assert source.base_url == "http://my.sentry.io"

    def test_base_url_empty_uses_default(self) -> None:
        source = _make_source(base_url="")
        assert source.base_url == _DEFAULT_BASE_URL


# --------------------------------------------------------------------------- #
# _validate_base_url
# --------------------------------------------------------------------------- #


class TestValidateBaseUrl:
    def test_valid_https(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            SentrySource._validate_base_url("https://sentry.io/api/")

    def test_valid_http(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            SentrySource._validate_base_url("http://sentry.local:9000/")

    def test_invalid_scheme_ftp(self) -> None:
        with pytest.raises(ValueError, match="http\\(s\\)"):
            SentrySource._validate_base_url("ftp://example.com/")

    def test_invalid_scheme_empty(self) -> None:
        with pytest.raises(ValueError, match="http\\(s\\)"):
            SentrySource._validate_base_url("example.com")

    def test_no_hostname(self) -> None:
        with pytest.raises(ValueError, match="no host"):
            SentrySource._validate_base_url("https:///path")

    def test_ssrf_blocked_host(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=True
        ):
            with pytest.raises(ValueError, match="SSRF guard"):
                SentrySource._validate_base_url("https://169.254.169.254/")

    def test_url_with_port(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            SentrySource._validate_base_url("https://sentry.io:8443/")

    def test_url_with_auth(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            SentrySource._validate_base_url(
                "https://user:pass@sentry.io/"
            )


# --------------------------------------------------------------------------- #
# _token
# --------------------------------------------------------------------------- #


class TestToken:
    def test_token_from_env(self) -> None:
        with patch.dict(
            "os.environ", {"SENTRY_TOKEN": "abc123"}, clear=True
        ):
            source = _make_source()
            assert source._token() == "abc123"

    def test_token_env_var_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            source = _make_source()
            assert source._token() == ""

    def test_token_env_var_empty_string(self) -> None:
        with patch.dict(
            "os.environ", {"SENTRY_TOKEN": ""}, clear=True
        ):
            source = _make_source()
            assert source._token() == ""

    def test_token_different_env_var_name(self) -> None:
        with patch.dict(
            "os.environ", {"CUSTOM_SENTRY": "tok"}, clear=True
        ):
            source = _make_source(token_env="CUSTOM_SENTRY")
            assert source._token() == "tok"


# --------------------------------------------------------------------------- #
# _headers
# --------------------------------------------------------------------------- #


class TestHeaders:
    def test_headers_with_token(self) -> None:
        with patch.dict(
            "os.environ", {"SENTRY_TOKEN": "tok"}, clear=True
        ):
            source = _make_source()
            headers = source._headers()
            assert headers["Authorization"] == "Bearer tok"
            assert headers["Accept"] == "application/json"

    def test_headers_empty_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            source = _make_source()
            headers = source._headers()
            assert headers["Authorization"] == "Bearer "
            assert headers["Accept"] == "application/json"


# --------------------------------------------------------------------------- #
# _get
# --------------------------------------------------------------------------- #


class TestGet:
    @staticmethod
    def _patched_source(**kwargs: object) -> SentrySource:
        transport = _mock_transport(get=MagicMock(return_value=_response(200)))
        return _make_source(transport=transport, **kwargs)

    def test_happy_path(self) -> None:
        source = self._patched_source()
        resp = source._get("https://sentry.io/api/0/issues/")
        assert resp.status == 200

    def test_calls_transport_with_headers(self) -> None:
        source = self._patched_source()
        with patch.dict(
            "os.environ", {"SENTRY_TOKEN": "secret"}, clear=True
        ):
            source._get("https://sentry.io/api/0/")
        source._transport.get.assert_called_once()
        _, kwargs = source._transport.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret"

    def test_calls_transport_with_timeout(self) -> None:
        source = self._patched_source(timeout=12.3)
        source._get("https://sentry.io/api/0/")
        _, kwargs = source._transport.get.call_args
        assert kwargs["timeout"] == 12.3

    def test_url_validation_failure_propagates(self) -> None:
        source = self._patched_source()
        with pytest.raises(ValueError, match="http\\(s\\)"):
            source._get("ftp://bad.scheme/")

    def test_transport_exception_propagates(self) -> None:
        transport = _mock_transport(
            get=MagicMock(side_effect=httpx.ConnectError("down"))
        )
        source = _make_source(transport=transport)
        with pytest.raises(httpx.ConnectError):
            source._get("https://sentry.io/api/0/")


# --------------------------------------------------------------------------- #
# _issues_url
# --------------------------------------------------------------------------- #


class TestIssuesUrl:
    def test_empty_params(self) -> None:
        source = _make_source()
        url = source._issues_url({})
        expected = (
            f"{_DEFAULT_BASE_URL}/api/0/projects/acme/backend/issues/"
        )
        assert url == expected

    def test_single_param(self) -> None:
        source = _make_source()
        url = source._issues_url({"limit": "50"})
        assert "?limit=50" in url
        assert url.startswith(
            f"{_DEFAULT_BASE_URL}/api/0/projects/acme/backend/issues/?"
        )

    def test_multiple_params(self) -> None:
        source = _make_source()
        url = source._issues_url(
            {"statsPeriod": "14d", "query": "is:unresolved", "limit": "25"}
        )
        assert "statsPeriod=14d" in url
        assert "query=is%3Aunresolved" in url
        assert "limit=25" in url

    def test_special_char_encoding(self) -> None:
        source = _make_source()
        url = source._issues_url({"query": "is:unresolved assigned:#me"})
        assert "%" in url
        assert "is%3Aunresolved" in url
        assert "%3A" in url

    def test_custom_base_url(self) -> None:
        with patch(
            "general_ludd.connectors.sentry.is_url_blocked", return_value=False
        ):
            source = _make_source(base_url="https://sentry.acme.com")
        url = source._issues_url({"limit": "10"})
        assert url.startswith("https://sentry.acme.com/api/0/projects/acme/backend/issues/?")


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


class TestHealth:
    def test_ok_200(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(200)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result == {"ok": True, "detail": "sentry api 200"}

    def test_ok_299(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(299)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result == {"ok": True, "detail": "sentry api 299"}

    def test_not_ok_400(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(400)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result == {"ok": False, "detail": "sentry api returned 400"}

    def test_not_ok_401(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(401)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result == {"ok": False, "detail": "sentry api returned 401"}

    def test_not_ok_500(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(500)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result == {"ok": False, "detail": "sentry api returned 500"}

    def test_not_ok_302(self) -> None:
        transport = _mock_transport(get=MagicMock(return_value=_response(302)))
        source = _make_source(transport=transport)
        result = source.health()
        assert result["ok"] is False
        assert "302" in str(result["detail"])

    def test_transport_exception_caught(self) -> None:
        transport = _mock_transport(
            get=MagicMock(side_effect=httpx.ConnectError("refused"))
        )
        source = _make_source(transport=transport)
        result = source.health()
        assert result["ok"] is False
        assert "ConnectError" in str(result["detail"])

    def test_value_error_caught(self) -> None:
        transport = _mock_transport(
            get=MagicMock(side_effect=ValueError("bad data"))
        )
        source = _make_source(transport=transport)
        result = source.health()
        assert result["ok"] is False
        assert "ValueError" in str(result["detail"])

    def test_base_exception_propagates(self) -> None:
        transport = _mock_transport(
            get=MagicMock(side_effect=KeyboardInterrupt)
        )
        source = _make_source(transport=transport)
        with pytest.raises(KeyboardInterrupt):
            source.health()


# --------------------------------------------------------------------------- #
# query
# --------------------------------------------------------------------------- #


class TestQuery:
    @staticmethod
    def _make_mock_response(status: int, payload: object) -> MagicMock:
        mock = MagicMock()
        mock.get.return_value = _response(
            status,
            json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload,
        )
        return mock

    @staticmethod
    def _source_with_payload(
        status: int = 200,
        payload: object | None = None,
    ) -> SentrySource:
        if payload is None:
            payload = [
                {
                    "id": "1",
                    "title": "Test Error",
                    "lastSeen": "2024-01-01T00:00:00Z",
                }
            ]
        transport = TestQuery._make_mock_response(status, payload)
        return _make_source(transport=transport)

    def test_happy_path(self) -> None:
        source = self._source_with_payload()
        results = source.query({"query": "is:unresolved", "limit": 10})
        assert len(results) == 1
        assert results[0]["kind"] == "logs"

    def test_default_spec_none(self) -> None:
        source = self._source_with_payload()
        results = source.query(None)
        assert len(results) == 1

    def test_default_spec_empty(self) -> None:
        source = self._source_with_payload()
        results = source.query({})
        assert len(results) == 1

    def test_default_stats_period(self) -> None:
        transport = self._make_mock_response(200, [{"id": "1", "title": "X"}])
        source = _make_source(transport=transport)
        source.query({})
        url = transport.get.call_args[0][0]
        assert f"statsPeriod={_DEFAULT_STATS_PERIOD}" in url

    def test_custom_stats_period(self) -> None:
        transport = self._make_mock_response(200, [{"id": "1", "title": "X"}])
        source = _make_source(transport=transport)
        source.query({"statsPeriod": "14d"})
        url = transport.get.call_args[0][0]
        assert "statsPeriod=14d" in url

    def test_custom_query(self) -> None:
        transport = self._make_mock_response(200, [{"id": "1", "title": "X"}])
        source = _make_source(transport=transport)
        source.query({"query": "is:unresolved is:for_review"})
        url = transport.get.call_args[0][0]
        assert "is%3Aunresolved" in url

    def test_non_200_status_returns_empty(self) -> None:
        source = self._source_with_payload(status=404)
        results = source.query({"limit": 10})
        assert results == []

    def test_non_200_400_returns_empty(self) -> None:
        source = self._source_with_payload(status=400)
        results = source.query({"limit": 10})
        assert results == []

    def test_non_list_payload_returns_empty(self) -> None:
        source = self._source_with_payload(payload={"error": "bad"})
        results = source.query({})
        assert results == []

    def test_payload_string_returns_empty(self) -> None:
        source = self._source_with_payload(payload="not-a-list")
        results = source.query({})
        assert results == []

    def test_json_decode_error_propagates(self) -> None:
        transport = MagicMock(spec=Transport)
        transport.get.return_value = _response(200, b"not json")
        source = _make_source(transport=transport)
        with pytest.raises(json.JSONDecodeError):
            source.query({})

    def test_limit_enforced(self) -> None:
        payload = [
            {"id": str(i), "title": f"Error {i}"} for i in range(50)
        ]
        source = self._source_with_payload(payload=payload)
        results = source.query({"limit": 3})
        assert len(results) == 3

    def test_limit_default(self) -> None:
        payload = [{"id": str(i), "title": f"Error {i}"} for i in range(5)]
        source = self._source_with_payload(payload=payload)
        results = source.query({})
        assert len(results) == 5

    def test_limit_invalid_string_uses_default(self) -> None:
        payload = [
            {"id": str(i), "title": f"Error {i}"} for i in range(50)
        ]
        source = self._source_with_payload(payload=payload)
        results = source.query({"limit": "abc"})
        assert len(results) == min(50, _DEFAULT_LIMIT)

    def test_limit_zero_uses_default(self) -> None:
        source = self._source_with_payload(
            payload=[{"id": "1", "title": "E"}]
        )
        results = source.query({"limit": 0})
        assert len(results) == 1

    def test_limit_string_int_works(self) -> None:
        source = self._source_with_payload(
            payload=[
                {"id": str(i), "title": f"E{i}"} for i in range(5)
            ]
        )
        results = source.query({"limit": "2"})
        assert len(results) == 2

    def test_skips_non_dict_items(self) -> None:
        payload: list[object] = [{"id": "1", "title": "A"}, "string_item", {"id": "2", "title": "B"}]
        source = self._source_with_payload(payload=payload)
        results = source.query({"limit": 10})
        assert len(results) == 2
        assert results[0]["raw"]["id"] == "1"
        assert results[1]["raw"]["id"] == "2"


# --------------------------------------------------------------------------- #
# fetch_event
# --------------------------------------------------------------------------- #


class TestFetchEvent:
    @staticmethod
    def _source_with_payload(
        status: int = 200,
        payload: object | None = None,
    ) -> SentrySource:
        if payload is None:
            payload = {
                "eventID": "abcdef",
                "title": "Test Event",
                "dateCreated": "2024-01-01T00:00:00Z",
            }
        transport = MagicMock(spec=Transport)
        transport.get.return_value = _response(
            status,
            json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload,
        )
        return _make_source(transport=transport)

    def test_happy_path(self) -> None:
        source = self._source_with_payload()
        result = source.fetch_event("12345")
        assert result is not None
        assert result["kind"] == "logs"
        assert result["labels"]["eventId"] == "abcdef"
        assert result["labels"]["issueId"] == "12345"

    def test_url_encodes_issue_id(self) -> None:
        transport = MagicMock(spec=Transport)
        transport.get.return_value = _response(
            200,
            b'{"eventID":"abc","title":"T","dateCreated":"2024-01-01T00:00:00Z"}',
        )
        source = _make_source(transport=transport)
        source.fetch_event("issue/with slashes?query=1")
        url = transport.get.call_args[0][0]
        assert "issue%2Fwith" in url
        assert "%3Fquery%3D1" in url

    def test_non_200_returns_none(self) -> None:
        source = self._source_with_payload(status=404)
        result = source.fetch_event("12345")
        assert result is None

    def test_non_200_500_returns_none(self) -> None:
        source = self._source_with_payload(status=500)
        result = source.fetch_event("12345")
        assert result is None

    def test_non_dict_returns_none(self) -> None:
        source = self._source_with_payload(payload=["item"])
        result = source.fetch_event("12345")
        assert result is None

    def test_non_dict_string_returns_none(self) -> None:
        source = self._source_with_payload(payload="a string")
        result = source.fetch_event("12345")
        assert result is None

    def test_json_decode_error_propagates(self) -> None:
        transport = MagicMock(spec=Transport)
        transport.get.return_value = _response(200, b"not json")
        source = _make_source(transport=transport)
        with pytest.raises(json.JSONDecodeError):
            source.fetch_event("12345")

    def test_returns_none_on_empty_response(self) -> None:
        transport = MagicMock(spec=Transport)
        transport.get.return_value = _response(200, b"")
        source = _make_source(transport=transport)
        result = source.fetch_event("12345")
        assert result is None


# --------------------------------------------------------------------------- #
# _normalize_issue
# --------------------------------------------------------------------------- #


class TestNormalizeIssue:
    @staticmethod
    def _source(**overrides: object) -> SentrySource:
        return _make_source(**overrides)

    def test_basic_issue(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "id": "1",
            "title": "Something broke",
            "lastSeen": "2024-01-01T00:00:00Z",
        }
        result = source._normalize_issue(issue)
        assert result["source"] == "sentry"
        assert result["kind"] == "logs"
        assert result["message"] == "Something broke"
        assert "raw" in result

    def test_title_from_metadata_type(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "metadata": {"type": "ValueError", "value": "bad"},
        }
        result = source._normalize_issue(issue)
        assert result["message"] == "ValueError"

    def test_title_fallback_to_empty_string(self) -> None:
        source = self._source()
        issue: dict[str, object] = {}
        result = source._normalize_issue(issue)
        assert result["message"] == ""

    def test_culprit_appended(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "Error",
            "culprit": "myapp.views.home",
            "lastSeen": "2024-01-01T00:00:00Z",
        }
        result = source._normalize_issue(issue)
        assert "Error — myapp.views.home" == result["message"]

    def test_culprit_and_title_none_fallback(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "metadata": {"type": "Warning"},
            "culprit": "some.module",
        }
        result = source._normalize_issue(issue)
        assert "Warning — some.module" == result["message"]

    def test_count_to_value(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "count": "42",
        }
        result = source._normalize_issue(issue)
        assert result["value"] == 42.0

    def test_count_int(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "count": 7,
        }
        result = source._normalize_issue(issue)
        assert result["value"] == 7.0

    def test_count_none(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "count": None,
        }
        result = source._normalize_issue(issue)
        assert result["value"] is None

    def test_count_invalid_string(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "count": "many",
        }
        result = source._normalize_issue(issue)
        assert result["value"] is None

    def test_labels_short_id(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "shortId": "PROJ-1A",
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["shortId"] == "PROJ-1A"

    def test_labels_status(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "status": "resolved",
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["status"] == "resolved"

    def test_labels_project_dict_slug(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "project": {"slug": "my-proj", "name": "My Project"},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["project"] == "my-proj"

    def test_labels_project_dict_no_slug_falls_back_to_name(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "project": {"name": "My Project"},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["project"] == "My Project"

    def test_labels_project_dict_empty_returns_none(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "project": {},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["project"] is None

    def test_labels_project_string(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "project": "acme-backend",
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["project"] == "acme-backend"

    def test_labels_project_none_falls_back_to_self_project(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "project": None,
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["project"] == "backend"

    def test_labels_commit(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "metadata": {"commit": "abc123"},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["commit"] == "abc123"

    def test_labels_commit_id_prefers_commit(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "metadata": {"commitId": "def456", "commit": "abc123"},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["commit"] == "abc123"

    def test_labels_commit_id_only(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "metadata": {"commitId": "def456"},
        }
        result = source._normalize_issue(issue)
        assert result["labels"]["commit"] == "def456"

    def test_labels_metadata_not_dict(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "metadata": "not-a-dict",
        }
        result = source._normalize_issue(issue)
        assert "commit" not in result["labels"]

    def test_ts_field(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "lastSeen": "2024-06-15T12:00:00Z",
        }
        result = source._normalize_issue(issue)
        assert result["ts"] == "2024-06-15T12:00:00Z"

    def test_level_or_status(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "level": "error",
        }
        result = source._normalize_issue(issue)
        assert result["level_or_status"] == "error"

    def test_raw_includes_original_issue(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "title": "E",
            "extra_field": "present",
        }
        result = source._normalize_issue(issue)
        assert result["raw"] is issue

    def test_message_no_culprit_no_title_has_metadata(self) -> None:
        source = self._source()
        issue: dict[str, object] = {
            "metadata": {"type": "Crash"},
        }
        result = source._normalize_issue(issue)
        assert result["message"] == "Crash"


# --------------------------------------------------------------------------- #
# _normalize_event
# --------------------------------------------------------------------------- #


class TestNormalizeEvent:
    @staticmethod
    def _source(**overrides: object) -> SentrySource:
        return _make_source(**overrides)

    def test_basic_event(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "eventID": "evt1",
            "title": "Runtime Error",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="42")
        assert result["source"] == "sentry"
        assert result["kind"] == "logs"
        assert result["labels"]["issueId"] == "42"
        assert result["labels"]["eventId"] == "evt1"
        assert result["message"] == "Runtime Error"
        assert result["value"] is None

    def test_title_from_message_fallback(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "eventID": "evt1",
            "message": "fallback message",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["message"] == "fallback message"

    def test_message_empty_string(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "eventID": "evt1",
            "title": "",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["message"] == ""

    def test_culprit_appended(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "eventID": "evt1",
            "title": "Oops",
            "culprit": "app.models",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["message"] == "Oops — app.models"

    def test_event_id_from_id_field(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "alt-id",
            "title": "E",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["eventId"] == "alt-id"

    def test_trace_id_from_contexts_trace(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "trace": {
                    "trace_id": "trace-abc123",
                    "span_id": "span-xyz",
                }
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["trace_id"] == "trace-abc123"
        assert result["labels"]["span_id"] == "span-xyz"

    def test_trace_id_from_contexts_trace_trace_id_only(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "trace": {"trace_id": "trace-abc123"},
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["trace_id"] == "trace-abc123"

    def test_trace_id_from_top_level(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "trace_id": "top-trace-456",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["trace_id"] == "top-trace-456"

    def test_trace_id_top_level_trace_id_camel_case(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "traceId": "camel-trace",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["trace_id"] == "camel-trace"

    def test_no_trace_id_anywhere(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "trace_id" not in result["labels"]

    def test_runtime_context_surfaced(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "runtime": {"name": "CPython 3.11"},
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["runtime"] == "CPython 3.11"

    def test_os_context_surfaced(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "os": {"name": "Linux"},
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["os"] == "Linux"

    def test_contexts_not_a_dict(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": "not-a-dict",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "trace_id" not in result["labels"]

    def test_contexts_trace_not_a_dict(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {"trace": "not-a-dict"},
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "trace_id" not in result["labels"]

    def test_context_runtime_not_a_dict(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {"runtime": "not-a-dict"},
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "runtime" not in result["labels"]

    def test_context_os_not_a_dict(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {"os": "not-a-dict"},
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "os" not in result["labels"]

    def test_ts_from_date_created(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "dateCreated": "2024-03-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["ts"] == "2024-03-01T00:00:00Z"

    def test_ts_fallback_to_date_received(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "dateReceived": "2024-04-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["ts"] == "2024-04-01T00:00:00Z"

    def test_level_or_status(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "level": "fatal",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["level_or_status"] == "fatal"

    def test_raw_includes_original_event(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "extra": "data",
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["raw"] is event

    def test_context_trace_trace_id_camel_case(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "trace": {
                    "traceId": "camel-trace-id",
                }
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert result["labels"]["trace_id"] == "camel-trace-id"

    def test_span_id_not_set_when_missing(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "id": "evt1",
            "title": "E",
            "contexts": {
                "trace": {"trace_id": "t1"},
            },
            "dateCreated": "2024-02-01T00:00:00Z",
        }
        result = source._normalize_event(event, issue_id="id")
        assert "span_id" not in result["labels"]

    def test_return_dict_integrity_all_keys(self) -> None:
        source = self._source()
        event: dict[str, object] = {
            "eventID": "evt99",
            "title": "Integrity Check",
            "dateCreated": "2024-05-01T00:00:00Z",
            "level": "warning",
            "culprit": "tests",
        }
        result = source._normalize_event(event, issue_id="55")
        expected_keys = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
        assert set(result.keys()) == expected_keys
        assert result["value"] is None
