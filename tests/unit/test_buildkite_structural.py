"""Structural tests for connectors/buildkite.py — Buildkite pipeline connector."""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.buildkite import (
    DEFAULT_BASE_URL,
    BuildkiteSource,
    _guard_base_url,
)


class TestGuardBaseUrl:
    def test_default_url_is_safe(self):
        result = _guard_base_url(DEFAULT_BASE_URL)
        assert result == "https://api.buildkite.com"

    def test_rejects_localhost(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _guard_base_url("http://localhost:8080")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_rejects_private_ip(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _guard_base_url("http://10.0.0.1")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_rejects_bad_scheme(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _guard_base_url("ftp://api.buildkite.com")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_strips_trailing_slash(self):
        result = _guard_base_url("https://api.buildkite.com/")
        assert result == "https://api.buildkite.com"


class TestBuildkiteSource:
    def test_minimal_construction(self):
        source = BuildkiteSource({})
        assert source.KIND == "pipeline"
        assert source.name == "buildkite"

    def test_custom_config(self):
        source = BuildkiteSource(
            {"name": "my-pipeline", "org": "acme", "pipeline": "main", "timeout": 15}
        )
        assert source.name == "my-pipeline"
        assert source.org == "acme"
        assert source.pipeline == "main"

    def test_health_returns_dict(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b"[]"

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        result = source.health()
        assert "ok" in result
        assert result["ok"] is True

    def test_health_never_raises_on_transport_error(self):
        def broken_transport(method, url, headers, timeout):
            raise OSError("boom")

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=broken_transport,
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_list(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b'[{"id": "1", "state": "passed", "branch": "main"}]'

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        result = source.query()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["kind"] == "pipeline"

    def test_query_empty_payload(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b"[]"

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        result = source.query()
        assert result == []

    def test_query_non_json_propagates(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b"not json"

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        with pytest.raises(json.JSONDecodeError):
            source.query()
        assert True

    def test_query_non_200_raises(self):
        def fake_transport(method, url, headers, timeout):
            return 500, b"[]"

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            source.query()
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_fetch_log(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b'{"content": "build log here"}'

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        log = source.fetch_log("job-1")
        assert log == "build log here"

    def test_fetch_log_raw_fallback(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b"raw log content"

        source = BuildkiteSource(
            {"org": "acme", "pipeline": "main"},
            transport=fake_transport,
        )
        log = source.fetch_log("job-1")
        assert log == "raw log content"
