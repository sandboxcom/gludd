"""Tests for connector _errors: sanitize_exc_message, sanitize_str, SSRFError, ConnectorConfigError."""

from __future__ import annotations

import logging

from general_ludd.connectors._errors import (
    ConnectorConfigError,
    SSRFError,
    sanitize_exc_message,
    sanitize_str,
)


class TestErrorClasses:
    def test_ssrf_error_is_value_error(self):
        assert issubclass(SSRFError, ValueError)

    def test_ssrf_error_instantiable(self):
        err = SSRFError("blocked host")
        assert "blocked host" in str(err)

    def test_connector_config_error_is_value_error(self):
        assert issubclass(ConnectorConfigError, ValueError)

    def test_connector_config_error_instantiable(self):
        err = ConnectorConfigError("invalid config")
        assert "invalid config" in str(err)


class TestSanitizeExcMessage:
    def test_returns_type_name_for_value_error(self):
        exc = ValueError("some error detail")
        assert sanitize_exc_message(exc) == "ValueError"

    def test_returns_type_name_for_custom_exception(self):
        exc = SSRFError("blocked host 127.0.0.1")
        assert sanitize_exc_message(exc) == "SSRFError"

    def test_returns_type_name_for_runtime_error(self):
        exc = RuntimeError("something broke")
        assert sanitize_exc_message(exc) == "RuntimeError"

    def test_does_not_leak_message_content(self, caplog):
        caplog.set_level(logging.WARNING)
        exc = ValueError("secret token abc123 /very/secret/path")
        result = sanitize_exc_message(exc)
        assert "secret" not in result
        assert "abc123" not in result
        assert "/very/secret/path" not in result
        assert result == "ValueError"
        assert caplog.records

    def test_logs_exception_detail(self, caplog):
        caplog.set_level(logging.WARNING)
        exc = RuntimeError("internal detail")
        sanitize_exc_message(exc)
        assert len(caplog.records) >= 1
        record = caplog.records[0]
        # No traceback attachment: exc_info would embed the secret-bearing
        # message in the log record (H20 no-leak contract).
        assert record.exc_info is None


class TestSanitizeStr:
    def test_redacts_paths(self):
        text = "File at /home/user/secret/file.txt was not found"
        result = sanitize_str(text)
        assert "/home/user/secret/file.txt" not in result
        assert "[REDACTED-PATH]" in result

    def test_redacts_multiple_paths(self):
        text = "Read /etc/passwd and /var/log/auth.log"
        result = sanitize_str(text)
        assert "/etc/passwd" not in result
        assert "/var/log/auth.log" not in result

    def test_redacts_bearer_tokens(self):
        text = "Authorization: Bearer abcdef1234567890abcdef1234567890"
        result = sanitize_str(text)
        assert "abcdef1234567890abcdef1234567890" not in result
        assert "[REDACTED]" in result

    def test_redacts_token_keyword(self):
        text = "token=abcdef1234567890abcdef12"
        result = sanitize_str(text)
        assert "abcdef1234567890abcdef12" not in result
        assert "[REDACTED]" in result

    def test_redacts_api_key_keyword(self):
        text = "api_key=abcdef1234567890abcdef12"
        result = sanitize_str(text)
        assert "abcdef1234567890abcdef12" not in result
        assert "[REDACTED]" in result

    def test_redacts_secret_keyword(self):
        text = "secret=abcdef1234567890abcdef12"
        result = sanitize_str(text)
        assert "abcdef1234567890abcdef12" not in result
        assert "[REDACTED]" in result

    def test_redacts_urls(self):
        text = "connect to https://internal.service.local/debug?token=x"
        result = sanitize_str(text)
        assert "internal.service.local" not in result
        assert "[REDACTED-PATH]" in result or "[REDACTED-URL]" in result

    def test_passes_safe_text_unchanged(self):
        text = "Operation completed successfully"
        result = sanitize_str(text)
        assert result == text

    def test_handles_empty_string(self):
        assert sanitize_str("") == ""

    def test_no_false_positive_on_short_tokens(self):
        text = "token=abc"  # too short for token pattern (min 8 chars)
        result = sanitize_str(text)
        assert "abc" in result
        assert "[REDACTED]" not in result
