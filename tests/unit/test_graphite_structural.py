"""Structural tests for connectors/graphite.py — Graphite metrics connector."""

from __future__ import annotations

from general_ludd.connectors.graphite import (
    GraphiteSource,
    _assert_public_base_url,
)


class TestAssertPublicBaseUrl:
    def test_valid_public_url(self):
        _assert_public_base_url("https://graphite.example.com")

    def test_rejects_localhost(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _assert_public_base_url("http://localhost:8080")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_rejects_bad_scheme(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _assert_public_base_url("ftp://graphite.example.com")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_rejects_no_host(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _assert_public_base_url("http:///render")
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")


class TestGraphiteSource:
    def test_minimal_construction(self):
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                pass

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=FakeTransport(),
        )
        assert source.KIND == "metrics"
        assert source.name == "graphite"

    def test_construction_rejects_missing_base_url(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        class FakeTransport:
            pass
        try:
            GraphiteSource({}, transport=FakeTransport())
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_custom_name(self):
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                pass

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com", "name": "prod-graphite"},
            transport=FakeTransport(),
        )
        assert source.name == "prod-graphite"

    def test_token_env_config(self):
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                pass

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com", "token_env": "GRAPHITE_TOKEN"},
            transport=FakeTransport(),
            environ={"GRAPHITE_TOKEN": "secret"},
        )
        assert source._auth_header["Authorization"] == "Bearer secret"

    def test_token_env_missing_raises(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        class FakeTransport:
            pass
        try:
            GraphiteSource(
                {"base_url": "https://graphite.example.com", "token_env": "GRAPHITE_TOKEN"},
                transport=FakeTransport(),
                environ={},
            )
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_health_returns_dict(self):
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                class Resp:
                    status_code = 200
                return Resp()

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=FakeTransport(),
        )
        result = source.health()
        assert "ok" in result
        assert result["ok"] is True

    def test_health_never_raises(self):
        class BrokenTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                raise OSError("no route")

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=BrokenTransport(),
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_list(self):
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                class Resp:
                    status_code = 200
                    def json(self):
                        return [
                            {
                                "target": "cpu.usage",
                                "datapoints": [[42.5, 1700000000], [43.0, 1700000060]],
                            }
                        ]
                return Resp()

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=FakeTransport(),
        )
        result = source.query({"target": "cpu.usage"})
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["kind"] == "metrics"
        assert result[0]["message"] == "cpu.usage"

    def test_query_missing_target_raises(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                pass

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=FakeTransport(),
        )
        try:
            source.query({})
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_query_non_200_raises(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        class FakeTransport:
            def request(self, method, url, *, headers=None, params=None, timeout=None):
                class Resp:
                    status_code = 500
                return Resp()

        source = GraphiteSource(
            {"base_url": "https://graphite.example.com"},
            transport=FakeTransport(),
        )
        try:
            source.query({"target": "cpu.usage"})
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")
