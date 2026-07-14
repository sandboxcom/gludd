"""Structural tests for connectors/graylog.py — GraylogSource."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from general_ludd.connectors.graylog import (
    GraylogSource,
    _coerce_int,
    _first_stream,
    _level_name,
    _parse_timestamp,
    _reject_internal_url,
)


class _FakeTransport:
    def __init__(self, status_code: int = 200, payload_dict: dict | None = None) -> None:
        self._status_code = status_code
        self._payload = payload_dict or {}
        self.status_code = status_code

    def __call__(self, url: str, *, headers, params, timeout):
        return self

    def json(self):
        return self._payload

    @property
    def status_code(self):
        return self._status_code

    @status_code.setter
    def status_code(self, v):
        self._status_code = v


def _make_entry(message_text: str, level: int = 6, timestamp: str = "2024-06-15T10:00:00.000Z"):
    return {
        "message": {
            "message": message_text,
            "level": level,
            "timestamp": timestamp,
            "source": "myhost",
            "facility": "user",
            "streams": ["stream-id-1"],
        }
    }


def _make_payload(messages: list[dict]):
    return {"messages": messages}


class TestCoerceInt:
    def test_valid_int(self):
        assert _coerce_int(42, default=10) == 42

    def test_string_int(self):
        assert _coerce_int("42", default=10) == 42

    def test_none_uses_default(self):
        assert _coerce_int(None, default=10) == 10

    def test_invalid_uses_default(self):
        assert _coerce_int("bad", default=10) == 10


class TestLevelName:
    def test_info_level(self):
        assert _level_name(6) == "info"

    def test_error_level(self):
        assert _level_name(3) == "error"

    def test_none(self):
        assert _level_name(None) is None

    def test_out_of_range(self):
        assert _level_name(999) == "999"


class TestFirstStream:
    def test_non_empty_list(self):
        assert _first_stream(["s1", "s2"]) == "s1"

    def test_empty_list(self):
        assert _first_stream([]) is None

    def test_non_list(self):
        assert _first_stream("not_a_list") is None


class TestParseTimestamp:
    def test_zulu(self):
        result = _parse_timestamp("2024-06-15T10:00:00.000Z")
        assert result is not None
        assert "+00:00" in str(result)

    def test_offset(self):
        result = _parse_timestamp("2024-06-15T10:00:00+00:00")
        assert result is not None

    def test_none(self):
        assert _parse_timestamp(None) is None

    def test_invalid(self):
        result = _parse_timestamp("not-a-date")
        assert result == "not-a-date"


class TestRejectInternalUrl:
    def test_valid_url(self):
        _reject_internal_url("https://graylog.example.com")

    def test_empty_host_raises(self):
        with pytest.raises(ValueError, match="no host"):
            _reject_internal_url("bad://")

    def test_localhost_raises(self):
        with pytest.raises(ValueError, match="refusing"):
            _reject_internal_url("https://localhost")


class TestGraylogSource:
    def test_requires_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            GraylogSource({})

    def test_requires_token_env(self):
        with pytest.raises(ValueError, match="token_env"):
            GraylogSource({"base_url": "https://graylog.example.com"})

    def test_constructs_with_valid_config(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "test-token"},
        )
        assert src.name == "graylog"
        assert src.KIND == "logs"

    def test_constructs_with_custom_name(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN", "name": "my-graylog"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "x"},
        )
        assert src.name == "my-graylog"

    def test_auth_header(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "secret-token"},
        )
        headers = src._auth_header()
        assert "Authorization" in headers
        assert "Basic" in headers["Authorization"]

    def test_health_ok(self):
        transport = _FakeTransport(status_code=200)
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=transport,
            env={"GL_TOKEN": "t"},
        )
        h = src.health()
        assert h["ok"] is True

    def test_health_unauthorized(self):
        transport = _FakeTransport(status_code=401)
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=transport,
            env={"GL_TOKEN": "t"},
        )
        h = src.health()
        assert h["ok"] is False
        assert h["error"] == "unauthorized"

    def test_health_transport_error(self):
        def bad_transport(url, *, headers, params, timeout):
            raise RuntimeError("down")
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=bad_transport,
            env={"GL_TOKEN": "t"},
        )
        h = src.health()
        assert h["ok"] is False

    def test_query_relative(self):
        transport = _FakeTransport(payload_dict=_make_payload([_make_entry("hello world")]))
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=transport,
            env={"GL_TOKEN": "t"},
        )
        records = src.query({"query": "*", "range": 300, "limit": 10})
        assert len(records) == 1
        assert records[0]["message"] == "hello world"

    def test_query_absolute(self):
        transport = _FakeTransport(payload_dict=_make_payload([_make_entry("abs log")]))
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=transport,
            env={"GL_TOKEN": "t"},
        )
        records = src.query({"from": "2024-01-01", "to": "2024-12-31"})
        assert len(records) == 1
        assert records[0]["message"] == "abs log"

    def test_query_error_status_returns_empty(self):
        transport = _FakeTransport(status_code=500)
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=transport,
            env={"GL_TOKEN": "t"},
        )
        records = src.query()
        assert records == []

    def test_build_search_request_relative(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "t"},
        )
        url, params = src._build_search_request({"query": "error", "range": 600})
        assert "/relative" in url
        assert params["query"] == "error"
        assert params["range"] == 600

    def test_build_search_request_absolute(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "t"},
        )
        url, params = src._build_search_request({"from": "2024-06-01", "to": "2024-06-15"})
        assert "/absolute" in url
        assert params["from"] == "2024-06-01"

    def test_normalize_valid_entry(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "t"},
        )
        record = src._normalize(_make_entry("test message", level=3, timestamp="2024-06-15T10:00:00.000Z"))
        assert record is not None
        assert record["message"] == "test message"
        assert record["level_or_status"] == "error"
        assert record["kind"] == "logs"

    def test_normalize_non_dict(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "t"},
        )
        assert src._normalize("not-a-dict") is None

    def test_normalize_non_message_map(self):
        src = GraylogSource(
            {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"},
            transport=_FakeTransport(),
            env={"GL_TOKEN": "t"},
        )
        assert src._normalize({"message": "plain_string"}) is None
