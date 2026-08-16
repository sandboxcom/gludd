"""TDD tests for general_ludd.connectors.exc_sanitizer — credential-leak prevention."""

from __future__ import annotations

import logging
from typing import ClassVar
from unittest.mock import patch

import pytest

from general_ludd.connectors._errors import (
    ConnectorConfigError,
    SSRFError,
)
from general_ludd.connectors.exc_sanitizer import (
    sanitize_exc_for_health,
    sanitize_exc_for_query,
    sanitize_exc_message,
    sanitize_str,
)


class TestSanitizeExcForHealth:
    def test_returns_type_name_only(self) -> None:
        exc = ValueError("secret token=abc123def456ghi789 /home/user/key")
        result = sanitize_exc_for_health(exc)
        assert result == "ValueError"

    def test_no_path_leak(self) -> None:
        exc = RuntimeError("failed at /etc/secret/credentials")
        result = sanitize_exc_for_health(exc)
        assert "/etc" not in result
        assert "credentials" not in result

    def test_no_token_leak(self) -> None:
        exc = ConnectionError("bearer eyJhbGciOiJIUzI1NiJ9.12345678")
        result = sanitize_exc_for_health(exc)
        assert "bearer" not in result
        assert "eyJ" not in result

    def test_no_url_leak(self) -> None:
        exc = OSError("connect to https://user:pass@internal.local/admin failed")
        result = sanitize_exc_for_health(exc)
        assert "http" not in result
        assert "internal.local" not in result
        assert "user:pass" not in result

    def test_logs_full_traceback(self) -> None:
        logger = logging.getLogger("general_ludd.connectors._errors")
        with patch.object(logger, "warning") as mock_warn:
            sanitize_exc_for_health(KeyError("missing-key"))
        mock_warn.assert_called_once()
        # No traceback attachment: exc_info would embed the secret-bearing
        # message in the log record (H20 no-leak contract).
        assert mock_warn.call_args[1].get("exc_info") is None

    def test_custom_exception_class(self) -> None:
        exc = SSRFError("http://127.0.0.1:8080 blocked")
        result = sanitize_exc_for_health(exc)
        assert result == "SSRFError"
        assert "127" not in result

    def test_config_error(self) -> None:
        exc = ConnectorConfigError("missing api_key=secret12345678")
        result = sanitize_exc_for_health(exc)
        assert result == "ConnectorConfigError"
        assert "secret" not in result
        assert "api_key" not in result

    def test_empty_exception(self) -> None:
        exc = Exception()
        result = sanitize_exc_for_health(exc)
        assert result == "Exception"

    def test_exception_with_empty_message(self) -> None:
        exc = ValueError("")
        result = sanitize_exc_for_health(exc)
        assert result == "ValueError"

    def test_exception_with_only_sensitive_content(self) -> None:
        exc = PermissionError("bearer abc123def456ghi789")
        result = sanitize_exc_for_health(exc)
        assert result == "PermissionError"


class TestSanitizeExcForQuery:
    def test_returns_type_name_only(self) -> None:
        exc = ValueError("api_key=deadbeef12345678 /var/run/secrets")
        result = sanitize_exc_for_query(exc)
        assert result == "ValueError"

    def test_ssrf_error_sanitized(self) -> None:
        exc = SSRFError("http://10.0.0.1:6379 blocked with token=abc123def456")
        result = sanitize_exc_for_query(exc)
        assert result == "SSRFError"
        assert "10.0" not in result
        assert "token" not in result

    def test_config_error_sanitized(self) -> None:
        exc = ConnectorConfigError("password=supersecretkey123 /etc/config")
        result = sanitize_exc_for_query(exc)
        assert result == "ConnectorConfigError"
        assert "password" not in result
        assert "supersecret" not in result

    def test_logs_full_traceback(self) -> None:
        logger = logging.getLogger("general_ludd.connectors._errors")
        with patch.object(logger, "warning") as mock_warn:
            sanitize_exc_for_query(TimeoutError("timed out"))
        mock_warn.assert_called_once()
        # No traceback attachment: exc_info would embed the secret-bearing
        # message in the log record (H20 no-leak contract).
        assert mock_warn.call_args[1].get("exc_info") is None

    def test_never_returns_str_of_exc(self) -> None:
        exc = ValueError("https://admin:pass@leak.local/secrets")
        result = sanitize_exc_for_query(exc)
        assert str(exc) not in result
        assert result == "ValueError"

    def test_non_standard_exception_types(self) -> None:
        class CustomAuthError(Exception):
            pass

        exc = CustomAuthError("token=xyz789abc456def123 /run/secrets")
        result = sanitize_exc_for_query(exc)
        assert result == "CustomAuthError"
        assert "token" not in result
        assert "xyz" not in result

    def test_builtin_exception_types(self) -> None:
        for exc_cls in (TypeError, KeyError, IndexError, AttributeError):
            exc = exc_cls("secret=topsecret12345678")
            result = sanitize_exc_for_query(exc)
            assert result == exc_cls.__name__
            assert "secret" not in result

    def test_empty_exception(self) -> None:
        exc = Exception()
        result = sanitize_exc_for_query(exc)
        assert result == "Exception"


class TestReExportedFunctions:
    def test_sanitize_exc_message_importable(self) -> None:
        assert callable(sanitize_exc_message)
        result = sanitize_exc_message(ValueError("sensitive"))
        assert callable(sanitize_str)
        assert result == "ValueError"

    def test_sanitize_str_importable(self) -> None:
        result = sanitize_str("bearer abc123def456ghij78")
        assert "abc123" not in result
        assert "[REDACTED]" in result

    def test_re_exports_match_originals(self) -> None:
        from general_ludd.connectors._errors import (
            sanitize_exc_message as _orig_exc,
        )
        from general_ludd.connectors._errors import (
            sanitize_str as _orig_str,
        )

        assert sanitize_exc_message is _orig_exc
        assert sanitize_str is _orig_str


class TestSanitizeExcForHealthForQueryEquivalence:
    def test_both_return_type_name(self) -> None:
        exc = RuntimeError("secret=s3cretkey12345678 /tmp/creds")
        health_result = sanitize_exc_for_health(exc)
        query_result = sanitize_exc_for_query(exc)
        assert health_result == query_result == "RuntimeError"

    def test_both_identical_for_hard_cases(self) -> None:
        exc = SSRFError("https://token:abc123@evil.internal/leak")
        assert sanitize_exc_for_health(exc) == sanitize_exc_for_query(exc)

    def test_both_log_to_same_logger(self) -> None:
        logger = logging.getLogger("general_ludd.connectors._errors")
        with patch.object(logger, "warning") as mock_warn:
            sanitize_exc_for_health(ValueError("a"))
            sanitize_exc_for_query(ValueError("b"))
        assert mock_warn.call_count == 2


class TestNestedAndChainedExceptions:
    def test_nested_exception_sanitized(self) -> None:
        try:
            try:
                raise ValueError("inner token=abc123def456")
            except ValueError as e:
                raise RuntimeError("outer /secret/path") from e
        except RuntimeError as exc:
            result = sanitize_exc_for_health(exc)
            assert result == "RuntimeError"
            assert "token" not in result
            assert "/secret" not in result

    def test___cause___chain_sanitized(self) -> None:
        try:
            try:
                raise KeyError("key with api_key=abc123def456")
            except KeyError as e:
                raise SSRFError("https://leak.example.com/endpoint") from e
        except SSRFError as exc:
            result = sanitize_exc_for_query(exc)
            assert result == "SSRFError"
            assert "leak.example.com" not in result
            assert "api_key" not in result

    def test_exception_group_sanitized(self) -> None:
        if hasattr(BaseException, "add_note"):
            exc = ValueError("token=secret12345678")
            exc.add_note("happened at /run/secrets/creds")
            result = sanitize_exc_for_health(exc)
            assert result == "ValueError"
            assert "secret" not in result
            assert "secrets" not in result


class TestSecurityCriticalNoLeak:
    COMMON_CREDENTIAL_PATTERNS: ClassVar[list[str]] = [
        "api_key=abc123def456ghi789",
        "api key: super-secret-key-12345",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def",
        "Authorization: token ghp_abc123def456ghi789",
        "password=CorrectHorseBatteryStaple",
        "secret: dGhpcyBpcyBhIHNlY3JldCBtZXNzYWdl",
        "https://admin:hunter2@internal.company.com/secrets",
        "/home/deploy/.ssh/id_rsa with key abcdef1234567890",
        "DATABASE_URL=postgres://user:pass@db.internal:5432/mydb",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ]

    @pytest.mark.parametrize("credential_text", COMMON_CREDENTIAL_PATTERNS)  # type: ignore[misc]
    def test_credential_pattern_stripped_from_health(self, credential_text: str) -> None:
        exc = ValueError(credential_text)
        result = sanitize_exc_for_health(exc)
        assert result == "ValueError"
        assert (
            any(token in result.lower() for token in ("key", "token", "secret", "passw", "bearer", "admin")) is False
        ), f"credential fragment leaked: {result!r}"

    @pytest.mark.parametrize("credential_text", COMMON_CREDENTIAL_PATTERNS)  # type: ignore[misc]
    def test_credential_pattern_stripped_from_query(self, credential_text: str) -> None:
        exc = ValueError(credential_text)
        result = sanitize_exc_for_query(exc)
        assert result == "ValueError"
        assert (
            any(token in result.lower() for token in ("key", "token", "secret", "passw", "bearer", "admin")) is False
        ), f"credential fragment leaked: {result!r}"


class TestIntegrationWithHealthPattern:
    def test_typical_connector_health_usage(self) -> None:
        def mock_health() -> dict[str, object]:
            try:
                raise ConnectionError("https://user:pass@leak.example.com:443/api")
            except ConnectionError as exc:
                return {"ok": False, "detail": sanitize_exc_for_health(exc)}

        result = mock_health()
        assert result["ok"] is False
        detail = result["detail"]
        assert isinstance(detail, str)
        assert detail == "ConnectionError"
        assert "user" not in detail
        assert "pass" not in detail
        assert "leak" not in detail

    def test_typical_connector_query_usage(self) -> None:
        def mock_query() -> list[dict[str, str]]:
            try:
                raise ConnectorConfigError("missing api_key=abc123def456")
            except ConnectorConfigError as exc:
                return [
                    {"error": sanitize_exc_for_query(exc)},
                ]

        result = mock_query()
        assert result[0]["error"] == "ConnectorConfigError"
        assert "api_key" not in result[0]["error"]
        assert "abc123" not in result[0]["error"]
