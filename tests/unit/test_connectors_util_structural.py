"""Structural tests for connectors/_util.py — validate_base_url and parse_timestamp."""

from __future__ import annotations

from general_ludd.connectors._util import parse_timestamp, validate_base_url


class TestValidateBaseUrl:
    def test_valid_url_strips_trailing_slash(self):
        assert validate_base_url("https://example.com/") == "https://example.com"

    def test_valid_url_no_trailing_slash(self):
        assert validate_base_url("https://example.com") == "https://example.com"

    def test_valid_url_with_path(self):
        assert validate_base_url("https://example.com/api/") == "https://example.com/api"


class TestParseTimestamp:
    def test_rfc3339_with_z(self):
        result = parse_timestamp("2024-01-01T00:00:00Z")
        assert result is not None
        assert result == 1704067200.0

    def test_rfc3339_with_offset(self):
        result = parse_timestamp("2024-01-01T00:00:00+00:00")
        assert result is not None
        assert result == 1704067200.0

    def test_iso_no_timezone(self):
        result = parse_timestamp("2024-01-01T00:00:00")
        assert result is not None

    def test_empty_string_returns_none(self):
        assert parse_timestamp("") is None

    def test_none_returns_none(self):
        assert parse_timestamp(None) is None

    def test_falsy_zero_returns_none(self):
        assert parse_timestamp(0) is None

    def test_non_string_returns_none(self):
        assert parse_timestamp(123) is None

    def test_invalid_format_returns_none(self):
        assert parse_timestamp("not-a-date") is None

    def test_whitespace_only_returns_none(self):
        assert parse_timestamp("   ") is None
