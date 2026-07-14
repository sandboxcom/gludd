"""Structural tests for connectors/_util.py — validate_base_url and parse_timestamp."""

from __future__ import annotations

import pytest

from general_ludd.connectors._util import parse_timestamp, validate_base_url


class TestValidateBaseUrl:
    def test_strips_trailing_slash(self) -> None:
        assert validate_base_url("https://example.com/") == "https://example.com"

    def test_preserves_no_slash(self) -> None:
        assert validate_base_url("https://example.com") == "https://example.com"

    def test_strips_path_trailing_slash(self) -> None:
        assert validate_base_url("https://example.com/api/v1/") == "https://example.com/api/v1"

    def test_raises_on_blocked(self) -> None:
        with pytest.raises(ValueError, match="base_url host is blocked"):
            validate_base_url("http://127.0.0.1")


class TestParseTimestamp:
    def test_iso_with_z(self) -> None:
        result = parse_timestamp("2024-06-15T12:00:00Z")
        assert result is not None
        assert isinstance(result, float)
        assert result > 0

    def test_iso_with_offset(self) -> None:
        result = parse_timestamp("2024-06-15T12:00:00+00:00")
        assert result is not None

    def test_naive_assumes_utc(self) -> None:
        a = parse_timestamp("2024-06-15T12:00:00")
        b = parse_timestamp("2024-06-15T12:00:00+00:00")
        assert a == b

    def test_none_returns_none(self) -> None:
        assert parse_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_timestamp("") is None

    def test_non_string_returns_none(self) -> None:
        assert parse_timestamp(2024) is None

    def test_invalid_returns_none(self) -> None:
        assert parse_timestamp("not-a-date") is None
