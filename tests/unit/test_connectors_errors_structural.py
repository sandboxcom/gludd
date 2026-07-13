"""Structural tests for connectors/_errors.py — error classes and sanitization helpers."""

from __future__ import annotations

from general_ludd.connectors._errors import (
    ConnectorConfigError,
    SSRFError,
    sanitize_exc_message,
    sanitize_str,
)


class TestSSRFError:
    def test_is_value_error(self):
        err = SSRFError("loopback not allowed")
        assert isinstance(err, ValueError)

    def test_message_preserved(self):
        err = SSRFError("custom msg")
        assert str(err) == "custom msg"


class TestConnectorConfigError:
    def test_is_value_error(self):
        err = ConnectorConfigError("bad config")
        assert isinstance(err, ValueError)

    def test_message_preserved(self):
        err = ConnectorConfigError("config missing required field")
        assert str(err) == "config missing required field"


class TestSanitizeExcMessage:
    def test_returns_type_name(self):
        err = ValueError("sensitive detail")
        assert sanitize_exc_message(err) == "ValueError"

    def test_returns_custom_type_name(self):
        assert sanitize_exc_message(SSRFError("loopback")) == "SSRFError"

    def test_never_contains_sensitive_message(self):
        err = SSRFError("http://internal:8080/api key=abc123secret")
        result = sanitize_exc_message(err)
        assert "http" not in result
        assert "abc123" not in result


class TestSanitizeStr:
    def test_removes_paths(self):
        text = "Error at /etc/passwd"
        result = sanitize_str(text)
        assert "/etc/passwd" not in result
        assert "REDACTED-PATH" in result

    def test_removes_tokens(self):
        text = "bearer eyJhbGciOiJIUzI1NiJ9.abc"
        result = sanitize_str(text)
        assert "eyJhbGci" not in result
        assert "REDACTED" in result

    def test_removes_api_keys(self):
        text = "api_key=abcdefgh12345678"
        result = sanitize_str(text)
        assert "abcdefgh12345678" not in result
        assert "REDACTED" in result

    def test_removes_urls(self):
        text = "download from https://internal.example.com/leak"
        result = sanitize_str(text)
        assert "internal.example.com" not in result
        assert "REDACTED-URL" in result

    def test_safe_text_unchanged(self):
        text = "hello world, no sensitive data here"
        assert sanitize_str(text) == text

    def test_idempotent(self):
        text = "token=abcdefgh12345678 and /secret/path"
        once = sanitize_str(text)
        twice = sanitize_str(once)
        assert once == twice
