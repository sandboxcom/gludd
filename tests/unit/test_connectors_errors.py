"""Structural tests for general_ludd.connectors._errors."""

from __future__ import annotations

import logging
from unittest.mock import patch

from general_ludd.connectors._errors import (
    _PATH_PATTERN,
    _TOKEN_PATTERN,
    _URL_PATTERN,
    ConnectorConfigError,
    SSRFError,
    sanitize_exc_message,
    sanitize_str,
)


class TestSSRFError:
    def test_is_value_error_subclass(self) -> None:
        assert issubclass(SSRFError, ValueError)

    def test_can_instantiate_with_message(self) -> None:
        err = SSRFError("blocked host")
        assert "blocked host" in str(err)


class TestConnectorConfigError:
    def test_is_value_error_subclass(self) -> None:
        assert issubclass(ConnectorConfigError, ValueError)

    def test_can_instantiate_with_message(self) -> None:
        err = ConnectorConfigError("bad config")
        assert "bad config" in str(err)


class TestSanitizeExcMessage:
    def test_returns_type_name(self) -> None:
        exc = ValueError("some /path/with keys and bear=abc123secret")
        result = sanitize_exc_message(exc)
        assert result == "ValueError"

    def test_logs_full_exception_detail(self) -> None:
        logger = logging.getLogger("general_ludd.connectors._errors")
        with patch.object(logger, "warning") as mock_warn:
            sanitize_exc_message(RuntimeError("boom"))
        mock_warn.assert_called_once()
        call_kwargs = mock_warn.call_args[1]
        # The sanitizer must NOT attach the traceback: exc_info would embed
        # the secret-bearing message in the log record (H20 no-leak contract).
        assert call_kwargs.get("exc_info") is None

    def test_never_leaks_message_content(self) -> None:
        exc = ValueError("/etc/passwd token=abc123def456ghi")
        result = sanitize_exc_message(exc)
        assert "/etc/passwd" not in result
        assert "abc123" not in result
        assert result == "ValueError"


class TestSanitizeStr:
    def test_redacts_paths(self) -> None:
        result = sanitize_str("error in /home/user/config.yml")
        assert "[REDACTED-PATH]" in result
        assert "/home/user/config.yml" not in result

    def test_redacts_tokens(self) -> None:
        result = sanitize_str("auth with bearer xyzabc123def456")
        assert "[REDACTED]" in result
        assert "xyzabc123def456" not in result

    def test_redacts_api_keys(self) -> None:
        result = sanitize_str("api_key=abcd1234efgh5678ij")
        assert "[REDACTED]" in result

    def test_redacts_urls(self) -> None:
        result = sanitize_str("fetch from http://internal.example.com/metrics")
        assert "[REDACTED" in result
        assert "http://internal.example.com/metrics" not in result

    def test_clean_text_passes_unchanged(self) -> None:
        text = "simple error message with no secrets"
        assert sanitize_str(text) == text

    def test_multiple_paths_all_redacted(self) -> None:
        result = sanitize_str("/etc/hosts and /var/log/syslog")
        assert result.count("[REDACTED-PATH]") >= 2


class TestCompiledPatterns:
    def test_path_pattern_matches_absolute_paths(self) -> None:
        assert _PATH_PATTERN.search("/usr/local/bin/python")

    def test_token_pattern_matches_bearer_tokens(self) -> None:
        assert _TOKEN_PATTERN.search("bearer abcdef1234567890")

    def test_url_pattern_matches_http_urls(self) -> None:
        assert _URL_PATTERN.search("http://example.com/path")
