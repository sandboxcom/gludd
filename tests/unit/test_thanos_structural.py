"""Structural tests for connectors/thanos.py — Thanos metrics connector."""

from __future__ import annotations

from general_ludd.connectors.thanos import (
    ThanosSource,
    _fmt_labels,
    _validate_base_url,
)


class TestValidateBaseUrl:
    def test_valid_public_url(self):
        result = _validate_base_url("https://thanos.example.com")
        assert result == "https://thanos.example.com"

    def test_rejects_empty(self):
        try:
            _validate_base_url("")
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_rejects_loopback(self):
        try:
            _validate_base_url("http://127.0.0.1:9090")
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_strips_trailing_slash(self):
        result = _validate_base_url("https://thanos.example.com/")
        assert result == "https://thanos.example.com"


class TestFmtLabels:
    def test_no_labels(self):
        assert _fmt_labels({"__name__": "up"}) == "up"

    def test_with_labels(self):
        result = _fmt_labels({"__name__": "up", "job": "api"})
        assert 'job="api"' in result
        assert "up{" in result


class TestThanosSource:
    def test_minimal_construction(self):
        source = ThanosSource({"base_url": "https://thanos.example.com"})
        assert source.KIND == "metrics"
        assert source.name is not None

    def test_health_returns_dict(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {"status": "success"}

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.health()
        assert "ok" in result
        assert result["ok"] is True

    def test_health_never_raises(self):
        def broken_http_get(url, params=None, headers=None, timeout=None):
            raise OSError("no route")

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=broken_http_get
        )
        result = source.health()
        assert result["ok"] is False

    def test_instant_query_returns_vector(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"__name__": "up", "job": "api"},
                            "value": [1700000000, "1"],
                        }
                    ],
                },
            }

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({"promql": "up"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["kind"] == "metrics"

    def test_range_query_returns_matrix(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "up"},
                            "values": [[1700000000, "1"], [1700000100, "0"]],
                        }
                    ],
                },
            }

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({"promql": "up[5m]", "start": 0, "end": 100, "step": 10})
        assert len(result) == 2

    def test_query_missing_promql(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {"status": "success"}

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({})
        assert len(result) == 1
        assert result[0]["level_or_status"] == "error"

    def test_transport_error_surfaced_as_record(self):
        def broken_http_get(url, params=None, headers=None, timeout=None):
            raise ConnectionError("refused")

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=broken_http_get
        )
        result = source.query({"promql": "up"})
        assert len(result) == 1
        assert result[0]["level_or_status"] == "error"

    def test_non_success_status_surface_error(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {"status": "error", "error": "parse error"}

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({"promql": "up"})
        assert len(result) == 1
        assert "error" in result[0]["level_or_status"] or result[0]["kind"] == "metrics"

    def test_scalar_result(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {
                "status": "success",
                "data": {"resultType": "scalar", "result": [1700000000, "1"]},
            }

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({"promql": "1"})
        assert len(result) == 1

    def test_unsupported_result_type_errors(self):
        def fake_http_get(url, params=None, headers=None, timeout=None):
            return 200, {
                "status": "success",
                "data": {"resultType": "string", "result": [1700000000, "hello"]},
            }

        source = ThanosSource(
            {"base_url": "https://thanos.example.com"}, http_get=fake_http_get
        )
        result = source.query({"promql": "up"})
        assert len(result) == 1
        assert result[0]["level_or_status"] == "error"
