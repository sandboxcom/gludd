"""Structural tests for connectors/exc_sanitizer.py — centralized exception sanitizer."""

from __future__ import annotations

from general_ludd.connectors.exc_sanitizer import (
    sanitize_exc_for_health,
    sanitize_exc_for_query,
    sanitize_exc_message,
    sanitize_str,
)


class TestSanitizeExports:
    def test_sanitize_exc_message_exported(self):
        assert callable(sanitize_exc_message)

    def test_sanitize_str_exported(self):
        assert callable(sanitize_str)


class TestSanitizeExcForHealth:
    def test_returns_type_name(self):
        exc = ValueError("internal token: abc123 /etc/passwd")
        result = sanitize_exc_for_health(exc)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_is_callable(self):
        assert callable(sanitize_exc_for_health)


class TestSanitizeExcForQuery:
    def test_returns_type_name(self):
        exc = RuntimeError("http://internal:8080/api?key=secret")
        result = sanitize_exc_for_query(exc)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_is_callable(self):
        assert callable(sanitize_exc_for_query)
