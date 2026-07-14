"""Structural tests for connectors/_errors.py — error classes and sanitization."""

from __future__ import annotations

from general_ludd.connectors._errors import (
    ConnectorConfigError,
    SSRFError,
    sanitize_exc_message,
    sanitize_str,
)


class TestErrorClasses:
    def test_ssrf_error_instantiation(self) -> None:
        err = SSRFError("loopback blocked")
        assert isinstance(err, ValueError)
        assert str(err) == "loopback blocked"

    def test_connector_config_error_instantiation(self) -> None:
        err = ConnectorConfigError("missing host")
        assert isinstance(err, ValueError)
        assert str(err) == "missing host"


class TestSanitizeExcMessage:
    def test_returns_class_name(self) -> None:
        assert sanitize_exc_message(ValueError("secret")) == "ValueError"

    def test_strips_sensitive_content(self) -> None:
        result = sanitize_exc_message(SSRFError("http://internal:8080/api key=secret"))
        assert "http" not in result
        assert "secret" not in result


class TestSanitizeStr:
    def test_path_redaction(self) -> None:
        result = sanitize_str("Error in /etc/passwd")
        assert "/etc/passwd" not in result
        assert "REDACTED-PATH" in result

    def test_token_redaction(self) -> None:
        result = sanitize_str("bearer token123abc456def78")
        assert "token123abc" not in result
        assert "REDACTED" in result

    def test_url_redaction(self) -> None:
        result = sanitize_str("Load https://internal.local/leak")
        assert "internal.local" not in result
        assert "REDACTED-URL" in result

    def test_safe_text_preserved(self) -> None:
        text = "clean message with no secrets"
        assert sanitize_str(text) == text
