"""Unit tests for security/auth.py — shared auth, path-confinement, and SSRF primitives."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from general_ludd.security.auth import (
    AuthPosture,
    check_admin_token,
    check_bearer_token,
    is_join_within,
    is_path_within,
    is_safe_fetch_url,
    load_auth_posture,
    require_auth_env,
    verify_psk,
)


class TestAuthPosture:
    def test_construction(self) -> None:
        ap = AuthPosture(psk="secret", require_auth=True, no_auth=False, surface="worker")
        assert ap.psk == "secret"
        assert ap.require_auth is True
        assert ap.no_auth is False
        assert ap.surface == "worker"

    def test_frozen(self) -> None:
        ap = AuthPosture(psk="key", require_auth=False, no_auth=True, surface="daemon")
        with pytest.raises((TypeError, AttributeError)):
            ap.psk = "new"  # type: ignore[misc]


class TestVerifyPsk:
    def test_matching_keys_return_true(self) -> None:
        assert verify_psk("abc123", "abc123") is True

    def test_non_matching_keys_return_false(self) -> None:
        assert verify_psk("abc123", "xyz789") is False

    def test_empty_presented_returns_false(self) -> None:
        assert verify_psk("", "secret") is False

    def test_empty_expected_returns_false(self) -> None:
        assert verify_psk("secret", "") is False

    def test_both_empty_returns_false(self) -> None:
        assert verify_psk("", "") is False


class TestCheckBearerToken:
    def test_valid_bearer_token(self) -> None:
        assert check_bearer_token("Bearer mytoken", "mytoken") is True

    def test_invalid_token_value(self) -> None:
        assert check_bearer_token("Bearer wrong", "mytoken") is False

    def test_missing_bearer_prefix(self) -> None:
        assert check_bearer_token("mytoken", "mytoken") is False

    def test_empty_header(self) -> None:
        assert check_bearer_token("", "secret") is False

    def test_none_header(self) -> None:
        assert check_bearer_token(None, "secret") is False  # type: ignore[arg-type]


class TestCheckAdminToken:
    def test_matching_admin_token(self) -> None:
        assert check_admin_token("admintoken", "admintoken") is True

    def test_non_matching_admin_token(self) -> None:
        assert check_admin_token("admintoken", "wrong") is False

    def test_empty_header_returns_false(self) -> None:
        assert check_admin_token("", "admintoken") is False

    def test_empty_expected_returns_false(self) -> None:
        assert check_admin_token("admintoken", "") is False

    def test_falls_back_to_env_when_expected_none(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ADMIN_TOKEN": "env-token"}, clear=True):
            assert check_admin_token("env-token") is True
            assert check_admin_token("wrong") is False

    def test_no_env_fallback_returns_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert check_admin_token("anything") is False


class TestRequireAuthEnv:
    def test_truthy_values(self) -> None:
        for val in ["1", "true", "yes", "on", "TRUE", "Yes"]:
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is True

    def test_falsy_values(self) -> None:
        for val in ["0", "false", "no", "off", "", "whatever"]:
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is False

    def test_missing_key_returns_false(self) -> None:
        assert require_auth_env({}) is False

    def test_defaults_to_os_environ(self) -> None:
        with patch.dict(os.environ, {"GLUDD_REQUIRE_AUTH": "1"}, clear=True):
            assert require_auth_env() is True


class TestLoadAuthPosture:
    def test_no_psk_no_disable_flag_require_auth_true(self) -> None:
        ap = load_auth_posture("worker", {})
        assert ap.psk == ""
        assert ap.no_auth is True
        assert ap.require_auth is True

    def test_no_psk_with_disable_flag_true(self) -> None:
        ap = load_auth_posture("worker", {"GLUDD_PSK_DISABLE": "1"})
        assert ap.no_auth is True
        assert ap.require_auth is False

    def test_no_psk_with_allow_no_auth_true(self) -> None:
        ap = load_auth_posture("daemon", {"GLUDD_ALLOW_NO_AUTH": "true"})
        assert ap.no_auth is True
        assert ap.require_auth is False

    def test_with_psk_no_require_auth(self) -> None:
        ap = load_auth_posture("worker", {"GLUDD_AUTH_PSK": "mykey", "GLUDD_REQUIRE_AUTH": "0"})
        assert ap.psk == "mykey"
        assert ap.no_auth is False
        assert ap.require_auth is False

    def test_with_psk_and_require_auth(self) -> None:
        ap = load_auth_posture("daemon", {"GLUDD_AUTH_PSK": "mykey", "GLUDD_REQUIRE_AUTH": "1"})
        assert ap.psk == "mykey"
        assert ap.no_auth is False
        assert ap.require_auth is True

    def test_psk_stripped(self) -> None:
        ap = load_auth_posture("worker", {"GLUDD_AUTH_PSK": "  key  "})
        assert ap.psk == "key"

    def test_surface_preserved(self) -> None:
        ap = load_auth_posture("worker", {})
        assert ap.surface == "worker"


class TestIsJoinWithin:
    def test_candidate_inside_base(self, tmp_path: str) -> None:
        assert is_join_within(str(tmp_path), "file.txt") is True

    def test_candidate_escape_dotdot_blocked(self, tmp_path: str) -> None:
        assert is_join_within(str(tmp_path), "../escape.txt") is False

    def test_absolute_candidate_outside_base(self, tmp_path: str) -> None:
        base = str(tmp_path / "subdir")
        os.makedirs(base, exist_ok=True)
        assert is_join_within(base, "/etc/passwd") is False

    def test_same_path(self, tmp_path: str) -> None:
        assert is_join_within(str(tmp_path), str(tmp_path)) is True

    def test_identity_alias(self) -> None:
        assert is_path_within is is_join_within


class TestIsSafeFetchUrl:
    def test_https_url_allowed(self) -> None:
        assert is_safe_fetch_url("https://example.com/skill") is True

    def test_http_url_blocked(self) -> None:
        assert is_safe_fetch_url("http://example.com/skill") is False

    def test_empty_url_blocked(self) -> None:
        assert is_safe_fetch_url("") is False

    def test_none_url_blocked(self) -> None:
        assert is_safe_fetch_url(None) is False  # type: ignore[arg-type]

    def test_non_string_url_blocked(self) -> None:
        assert is_safe_fetch_url(42) is False  # type: ignore[arg-type]

    def test_localhost_https_blocked(self) -> None:
        assert is_safe_fetch_url("https://127.0.0.1/skill") is False

    def test_loopback_hostname_blocked(self) -> None:
        assert is_safe_fetch_url("https://localhost/skill") is False
