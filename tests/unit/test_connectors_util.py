"""Structural tests for general_ludd.connectors._util."""

from __future__ import annotations

import pytest

from general_ludd.connectors._util import parse_timestamp, validate_base_url


class TestValidateBaseUrl:
    def test_strips_trailing_slash(self) -> None:
        result = validate_base_url("https://example.com/")
        assert result == "https://example.com"

    def test_no_trailing_slash_unchanged(self) -> None:
        result = validate_base_url("http://api.example.com/v1")
        assert result == "http://api.example.com/v1"

    def test_raises_on_blocked_url(self) -> None:
        with pytest.raises(ValueError, match="base_url host is blocked"):
            validate_base_url("http://127.0.0.1:8080")

    def test_raises_on_loopback(self) -> None:
        with pytest.raises(ValueError):
            validate_base_url("http://localhost:3000")


class TestParseTimestamp:
    def test_iso_format_with_z_suffix(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00Z")
        assert result is not None
        assert result > 0

    def test_iso_format_with_offset(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00+00:00")
        assert result is not None

    def test_naive_datetime_assumes_utc(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00")
        assert result is not None
        result2 = parse_timestamp("2024-01-15T10:30:00+00:00")
        assert result == result2

    def test_invalid_string_returns_none(self) -> None:
        assert parse_timestamp("not-a-timestamp") is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_timestamp("") is None

    def test_none_value_returns_none(self) -> None:
        assert parse_timestamp(None) is None

    def test_non_string_value_returns_none(self) -> None:
        assert parse_timestamp(12345) is None

    def test_falsy_int_returns_none(self) -> None:
        assert parse_timestamp(0) is None

    def test_strips_whitespace(self) -> None:
        result = parse_timestamp("  2024-01-15T10:30:00Z  ")
        assert result is not None

    def test_return_type_is_float(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00Z")
        assert isinstance(result, float)
