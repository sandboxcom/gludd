"""Structural tests for connectors/prom_scrape.py — Prometheus scrape connector."""

from __future__ import annotations

from general_ludd.connectors.prom_scrape import (
    PromScrapeSource,
    _guard_base_url,
    _HttpxTransport,
    _parse_labels,
    _parse_value_ts,
    _split_metric_line,
)


class TestParseLabels:
    def test_empty_string(self):
        assert _parse_labels("") == {}

    def test_single_label(self):
        assert _parse_labels('{a="1"}') == {"a": "1"}

    def test_multiple_labels(self):
        result = _parse_labels('{a="1",b="2"}')
        assert result == {"a": "1", "b": "2"}

    def test_escaped_quotes(self):
        result = _parse_labels(r'{msg="hello \"world\""}')
        assert "msg" in result


class TestSplitMetricLine:
    def test_simple_no_labels(self):
        result = _split_metric_line("http_requests_total 42")
        assert result is not None
        name, labels, _rest = result
        assert name == "http_requests_total"
        assert labels == {}

    def test_with_labels(self):
        result = _split_metric_line('http_requests_total{method="GET"} 42')
        assert result is not None
        name, labels, _rest = result
        assert name == "http_requests_total"
        assert labels == {"method": "GET"}

    def test_comment_not_rejected_at_leaf(self):
        result = _split_metric_line("# HELP metric")
        assert result is not None

    def test_empty_returns_none(self):
        assert _split_metric_line("") is None


class TestParseValueTs:
    def test_simple_value(self):
        result = _parse_value_ts("42")
        assert result is not None
        value, ts = result
        assert value == 42.0
        assert ts is None

    def test_value_with_timestamp(self):
        result = _parse_value_ts("42 1700000000000")
        assert result is not None
        value, ts = result
        assert value == 42.0
        assert ts == 1700000000.0

    def test_nan_rejected(self):
        assert _parse_value_ts("nan") is None

    def test_empty_returns_none(self):
        assert _parse_value_ts("") is None


class TestGuardBaseUrl:
    def test_valid_public_url(self):
        result = _guard_base_url("https://example.com:9090", allow_private=False)
        assert result == "https://example.com:9090"

    def test_allows_private_when_opted_in(self):
        result = _guard_base_url("http://10.0.0.1:9100", allow_private=True)
        assert result == "http://10.0.0.1:9100"

    def test_rejects_bad_scheme(self):
        try:
            _guard_base_url("ftp://example.com", allow_private=False)
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_rejects_no_host(self):
        try:
            _guard_base_url("http:///metrics", allow_private=False)
        except ValueError:
            return
        raise AssertionError("expected ValueError")


class TestHttpxTransport:
    def test_instantiates(self):
        transport = _HttpxTransport()
        assert transport is not None


class TestPromScrapeSource:
    def test_minimal_construction(self):
        source = PromScrapeSource({"base_url": "https://example.com:9090"})
        assert source.KIND == "metrics"
        assert source.name is not None

    def test_custom_name(self):
        source = PromScrapeSource(
            {"base_url": "https://example.com:9090", "name": "my-exporter"}
        )
        assert source.name == "my-exporter"

    def test_health_returns_dict(self):
        source = PromScrapeSource({"base_url": "https://example.com:9090"})
        result = source.health()
        assert "ok" in result
        assert "detail" in result
        assert isinstance(result["ok"], bool)

    def test_query_returns_list_on_empty_response(self):
        class FakeTransport:
            def get(self, url, headers=None, timeout=None):
                class Resp:
                    status_code = 200
                    text = ""
                return Resp()

        source = PromScrapeSource(
            {"base_url": "https://example.com:9090"}, transport=FakeTransport()
        )
        result = source.query()
        assert isinstance(result, list)

    def test_query_with_metric_prefix(self):
        class FakeTransport:
            def get(self, url, headers=None, timeout=None):
                class Resp:
                    status_code = 200
                    text = "# HELP foo bar\nfoo{method=\"GET\"} 42 1700000000000\n\nbar 10\n"
                return Resp()

        source = PromScrapeSource(
            {"base_url": "https://example.com:9090"}, transport=FakeTransport()
        )
        result = source.query({"metric_prefix": "foo"})
        assert len(result) == 1
        assert result[0]["message"] == "foo"

    def test_query_defaults_to_empty_list_on_transport_error(self):
        class BrokenTransport:
            def get(self, url, headers=None, timeout=None):
                raise ConnectionError("boom")

        source = PromScrapeSource(
            {"base_url": "https://example.com:9090"}, transport=BrokenTransport()
        )
        result = source.query()
        assert result == []

    def test_construction_with_allow_private(self):
        source = PromScrapeSource(
            {"base_url": "http://10.0.0.1:9100", "allow_private": True}
        )
        assert source is not None
