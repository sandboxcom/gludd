"""Tests for connector _util: validate_base_url and parse_timestamp."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from general_ludd.connectors._util import parse_timestamp, validate_base_url


class TestValidateBaseUrl:
    def test_strips_trailing_slash(self):
        assert validate_base_url("https://example.com/") == "https://example.com"

    def test_passes_clean_url(self):
        assert validate_base_url("https://example.com/api") == "https://example.com/api"

    def test_no_trailing_slash_unchanged(self):
        assert validate_base_url("https://example.com") == "https://example.com"

    def test_raises_on_blocked_url(self):
        with pytest.raises(ValueError, match="blocked"):
            validate_base_url("http://127.0.0.1")

    def test_raises_on_private_ip(self):
        with pytest.raises(ValueError, match="blocked"):
            validate_base_url("http://10.0.0.1")

    def test_raises_on_metadata_url(self):
        with pytest.raises(ValueError, match="blocked"):
            validate_base_url("http://169.254.169.254")


class TestParseTimestamp:
    def test_returns_none_for_empty_string(self):
        assert parse_timestamp("") is None

    def test_returns_none_for_empty_string_spaces(self):
        assert parse_timestamp("   ") is None

    def test_returns_none_for_none(self):
        assert parse_timestamp(None) is None

    def test_returns_none_for_zero(self):
        assert parse_timestamp(0) is None

    def test_returns_none_for_object(self):
        assert parse_timestamp(object()) is None

    def test_parses_iso_format_with_offset(self):
        result = parse_timestamp("2024-01-15T10:30:00+01:00")
        assert isinstance(result, float)
        expected = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_parses_iso_format_with_z(self):
        result = parse_timestamp("2024-01-15T10:30:00Z")
        assert isinstance(result, float)
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_parses_iso_format_with_milliseconds(self):
        result = parse_timestamp("2024-01-15T10:30:00.123456+00:00")
        assert isinstance(result, float)

    def test_returns_none_for_invalid_format(self):
        assert parse_timestamp("not-a-timestamp") is None

    def test_returns_none_for_partial_timestamp(self):
        assert parse_timestamp("2024-01") is None

    def test_assumes_utc_for_naive_datetime(self):
        result = parse_timestamp("2024-01-15T10:30:00")
        assert isinstance(result, float)
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc).timestamp()
        assert result == expected

    def test_strips_whitespace(self):
        result = parse_timestamp("  2024-01-15T10:30:00Z  ")
        assert isinstance(result, float)
