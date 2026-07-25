"""Structural tests for connectors/statsd_parse.py — StatsdParseSource."""

from __future__ import annotations

import pytest

from general_ludd.connectors.statsd_parse import (
    _VALID_TYPES,
    StatsdParseError,
    StatsdParseSource,
)


class TestStatsdParseModule:
    def test_source_importable(self) -> None:
        assert StatsdParseSource is not None

    def test_statsd_parse_error_importable(self) -> None:
        assert StatsdParseError is not None
        assert issubclass(StatsdParseError, ValueError)

    def test_valid_types_constants(self) -> None:
        assert "c" in _VALID_TYPES
        assert "g" in _VALID_TYPES
        assert "ms" in _VALID_TYPES
        assert "h" in _VALID_TYPES
        assert "s" in _VALID_TYPES

    def test_kind_is_metrics(self) -> None:
        src = StatsdParseSource()
        assert src.KIND == "metrics"

    def test_default_name(self) -> None:
        src = StatsdParseSource()
        assert src.name == "statsd_parse"

    def test_custom_name(self) -> None:
        src = StatsdParseSource({"name": "custom-parser"})
        assert src.name == "custom-parser"

    def test_strict_defaults_false(self) -> None:
        src = StatsdParseSource()
        assert src._strict is False

    def test_strict_from_config(self) -> None:
        src = StatsdParseSource({"strict": True})
        assert src._strict is True

    def test_health_never_raises(self) -> None:
        src = StatsdParseSource()
        result = src.health()
        assert result["ok"] is True

    def test_parse_line_counter(self) -> None:
        src = StatsdParseSource()
        record = src.parse_line("page.views:1|c")
        assert record["message"] == "page.views"
        assert record["value"] == 1.0
        assert record["level_or_status"] == "c"

    def test_parse_line_with_sample_rate(self) -> None:
        src = StatsdParseSource()
        record = src.parse_line("requests:10|c|@0.5")
        assert record["labels"]["sample_rate"] == 0.5

    def test_parse_line_with_tags(self) -> None:
        src = StatsdParseSource()
        record = src.parse_line("latency:100|ms|#env:prod,region:us-east-1")
        assert record["labels"]["env"] == "prod"
        assert record["labels"]["region"] == "us-east-1"

    def test_parse_line_malformed_raises_in_strict(self) -> None:
        src = StatsdParseSource({"strict": True})
        with pytest.raises(StatsdParseError):
            src.parse_line("not a valid line")

    def test_query_returns_list(self) -> None:
        src = StatsdParseSource()
        result = src.query({"lines": ["test:1|c"]})
        assert isinstance(result, list)
        assert len(result) == 1

    def test_query_empty_lines(self) -> None:
        src = StatsdParseSource()
        result = src.query({"lines": []})
        assert result == []
