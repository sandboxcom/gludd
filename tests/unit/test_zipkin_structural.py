"""Structural tests for connectors/zipkin.py — Zipkin tracing connector."""

from __future__ import annotations

from general_ludd.connectors.zipkin import (
    ZipkinSource,
    _guard_base_url,
    _ZipkinResponse,
)


class TestGuardBaseUrl:
    def test_valid_public_url(self):
        result = _guard_base_url("https://zipkin.example.com", allow_private=False)
        assert result == "https://zipkin.example.com"

    def test_rejects_private_by_default(self):
        from general_ludd.connectors._errors import SSRFError
        try:
            _guard_base_url("http://10.0.0.1:9411", allow_private=False)
        except SSRFError:
            return
        raise AssertionError("expected SSRFError")

    def test_allows_private_with_opt_in(self):
        result = _guard_base_url("http://10.0.0.1:9411", allow_private=True)
        assert result == "http://10.0.0.1:9411"

    def test_rejects_bad_scheme(self):
        from general_ludd.connectors._errors import SSRFError
        try:
            _guard_base_url("ftp://zipkin.example.com", allow_private=False)
        except SSRFError:
            return
        raise AssertionError("expected SSRFError")

    def test_strips_trailing_slash(self):
        result = _guard_base_url("https://zipkin.example.com/", allow_private=False)
        assert result == "https://zipkin.example.com"


class TestZipkinResponse:
    def test_construction(self):
        resp = _ZipkinResponse(200, b'{"key": "val"}')
        assert resp.status == 200

    def test_json_parsing(self):
        resp = _ZipkinResponse(200, b'{"key": "val"}')
        data = resp.json()
        assert data == {"key": "val"}


class TestZipkinSource:
    def test_minimal_construction(self):
        source = ZipkinSource({"base_url": "https://zipkin.example.com"})
        assert source.KIND == "traces"
        assert source.name == "zipkin"

    def test_custom_config(self):
        source = ZipkinSource(
            {
                "base_url": "https://zipkin.example.com",
                "name": "prod-zipkin",
                "service_name": "my-api",
                "lookback": 60000,
                "limit": 50,
                "token_env": "ZIPKIN_TOKEN",
                "allow_private": True,
            }
        )
        assert source.name == "prod-zipkin"
        assert source.default_service == "my-api"
        assert source.default_lookback == 60000
        assert source.default_limit == 50
        assert source.allow_private is True

    def test_health_returns_dict(self):
        class FakeTransport:
            def get(self, url, *, headers, timeout):
                return _ZipkinResponse(200, b"[[]]")

        source = ZipkinSource(
            {"base_url": "https://zipkin.example.com"}, transport=FakeTransport()
        )
        result = source.health()
        assert "ok" in result
        assert result["ok"] is True

    def test_health_never_raises(self):
        class BrokenTransport:
            def get(self, url, *, headers, timeout):
                raise OSError("no route")

        source = ZipkinSource(
            {"base_url": "https://zipkin.example.com"}, transport=BrokenTransport()
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_list(self):
        class FakeTransport:
            def get(self, url, *, headers, timeout):
                return _ZipkinResponse(
                    200,
                    (
                        b'[[{"id": "1", "traceId": "abc", '
                        b'"name": "GET /api", "timestamp": 1700000000000, '
                        b'"duration": 5000}]]'
                    )
                )

        source = ZipkinSource(
            {"base_url": "https://zipkin.example.com"}, transport=FakeTransport()
        )
        result = source.query()
        assert isinstance(result, list)
        assert len(result) == 1
        record = result[0]
        assert record["kind"] == "traces"
        assert record["message"] == "GET /api"

    def test_query_empty_on_non_list_payload(self):
        class FakeTransport:
            def get(self, url, *, headers, timeout):
                return _ZipkinResponse(200, b"{}")

        source = ZipkinSource(
            {"base_url": "https://zipkin.example.com"}, transport=FakeTransport()
        )
        result = source.query()
        assert result == []

    def test_span_error_tag(self):
        class FakeTransport:
            def get(self, url, *, headers, timeout):
                return _ZipkinResponse(
                    200,
                    b'[[{"id": "1", "traceId": "abc", "name": "error-span", "tags": {"error": "true"}}]]',
                )

        source = ZipkinSource(
            {"base_url": "https://zipkin.example.com"}, transport=FakeTransport()
        )
        result = source.query()
        assert result[0]["level_or_status"] == "error"
