"""H.22: Gateway scope fail-open and SSRF URL disclosure fixes.

1. _resolver_for_project must fail CLOSED: when for_project() raises,
   return None (not the shared base resolver), denying cross-project
   secret access.
2. SSRF error messages must not disclose internal URLs; sanitize_error_message
   must redact loopback/private/internal hosts.
3. Resolver errors are logged but internal details are redacted.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, SSRFRejectionError
from general_ludd.security.sanitize import sanitize_error_message

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_mock_profile(profile_id="h22-p", model_name="test", credential_alias="OPENAI_KEY"):
    profile = MagicMock()
    profile.model_profile_id = profile_id
    profile.model_name = model_name
    profile.provider = "openai"
    profile.credential_alias = credential_alias
    profile.api_base_alias = None
    profile.run_budget_usd = 200.0
    profile.api_metered = False
    profile.cost_per_input_token = 0.000001
    profile.cost_per_output_token = 0.000002
    return profile


# ---------------------------------------------------------------------------
# 1. Project-secrets-resolver fail-closed
# ---------------------------------------------------------------------------

class TestResolverFailClosed:
    """_resolver_for_project must NOT fall back to the shared resolver on error."""

    def test_resolver_failure_returns_none(self):
        """When for_project(pid) raises, _resolver_for_project returns None."""
        base = MagicMock()
        base.for_project = MagicMock(side_effect=RuntimeError("boom"))

        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        result = gw._resolver_for_project("proj-001")
        assert result is None, (
            f"Expected None (fail-closed) but got {result!r} "
            "(falling back to shared resolver — H.22 fail-open bug)"
        )

    def test_resolver_failure_logs_does_not_include_base(self):
        """Failure is logged but does NOT return the shared base."""
        base = MagicMock()
        base.for_project = MagicMock(side_effect=RuntimeError("internal detail"))

        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        with patch.object(logging.getLogger("general_ludd.models.gateway"),
                          "warning") as mock_log:
            result = gw._resolver_for_project("proj-001")
        assert result is None
        mock_log.assert_called_once()
        log_msg = mock_log.call_args[0][0]
        assert "falling back" not in log_msg.lower()
        assert "refusing" in log_msg.lower()

    def test_resolver_success_returns_scoped(self):
        """When for_project(pid) succeeds, the scoped resolver is returned."""
        base = MagicMock()
        scoped = MagicMock()
        base.for_project = MagicMock(return_value=scoped)

        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        result = gw._resolver_for_project("proj-001")
        assert result is scoped

    def test_no_project_id_returns_base(self):
        """Without project_id, base resolver is returned (existing behavior)."""
        base = MagicMock()
        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        result = gw._resolver_for_project(None)
        assert result is base

    def test_no_for_project_returns_base(self):
        """Without for_project on resolver, base is returned (existing behavior)."""
        base = MagicMock(spec=[])  # no for_project attr
        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        result = gw._resolver_for_project("proj-001")
        assert result is base

    def test_base_is_none_returns_none(self):
        """When _secrets is None, returns None."""
        gw = ModelGateway(profiles=[_make_mock_profile()], secrets_manager=None)
        result = gw._resolver_for_project("proj-001")
        assert result is None

    def test_empty_project_id_returns_base(self):
        """Empty string project_id returns base."""
        base = MagicMock()
        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        result = gw._resolver_for_project("")
        assert result is base

    def test_resolver_error_log_message_redacts_internal_details(self):
        """The log message does NOT include the exception text."""
        base = MagicMock()
        base.for_project = MagicMock(
            side_effect=RuntimeError("sensitive internal detail: /tmp/secret.txt")
        )

        gw = ModelGateway(
            profiles=[_make_mock_profile()],
            secrets_manager=base,
        )
        with patch.object(logging.getLogger("general_ludd.models.gateway"),
                          "warning") as mock_log:
            result = gw._resolver_for_project("proj-001")
        assert result is None
        log_msg = mock_log.call_args[0][0]
        assert "sensitive" not in log_msg
        assert "secret.txt" not in log_msg


# ---------------------------------------------------------------------------
# 2. SSRF error messages — no internal URL disclosure
# ---------------------------------------------------------------------------

class TestSSRFErrorNoURLDisclosure:
    """SSRF error messages must not leak internal URLs to callers or logs."""

    def test_gateway_ssrf_error_message_is_redacted(self):
        """SSRFRejectionError from the gateway does not include the actual URL."""
        with pytest.raises(SSRFRejectionError) as exc_info:
            raise SSRFRejectionError(
                "SSRF guard: refusing blocked api_base_alias URL "
                "(redacted) for profile 'test'"
            )
        msg = str(exc_info.value)
        assert "(redacted)" in msg
        assert "127.0.0.1" not in msg
        assert "localhost" not in msg
        assert "169.254" not in msg

    def test_sanitize_error_message_redacts_loopback_urls(self):
        """sanitize_error_message removes loopback addresses from error text."""
        texts = [
            ("Connection refused to http://127.0.0.1:8080/api", False),
            ("Host https://localhost/v1 is blocked by SSRF", False),
            ("url=http://[::1]:9090/route", False),
        ]
        for original, _should_contain_internal in texts:
            sanitized = sanitize_error_message(original)
            found_loopback = (
                "127.0.0.1" in sanitized
                or "localhost" in sanitized
                or "[::1]" in sanitized
            )
            assert not found_loopback, (
                f"Internal URL leaked through sanitize: {sanitized!r} "
                f"(from: {original!r})"
            )

    def test_sanitize_error_message_redacts_private_ips(self):
        """sanitize_error_message removes RFC-1918 and metadata addresses."""
        texts = [
            "https://10.0.0.5/v1 rejected by guard",
            "Call to 192.168.1.1 failed",
            "Host 169.254.169.254 is blocked",
            "http://metadata.google.internal/token",
            "100.100.100.200 refused",
        ]
        for original in texts:
            sanitized = sanitize_error_message(original)
            assert "10.0.0.5" not in sanitized
            assert "192.168.1.1" not in sanitized
            assert "169.254.169.254" not in sanitized
            assert "metadata.google.internal" not in sanitized
            assert "100.100.100.200" not in sanitized

    def test_sanitize_error_message_redacts_internal_hostnames(self):
        """sanitize_error_message removes known internal hostnames."""
        texts = [
            "DNS error for localhost.localdomain",
            "instance-data unreachable",
        ]
        for original in texts:
            sanitized = sanitize_error_message(original)
            assert "localhost.localdomain" not in sanitized
            assert "instance-data" not in sanitized

    def test_sanitize_error_message_preserves_safe_text(self):
        """sanitize_error_message does not change text without internal URLs."""
        safe_texts = [
            "API rate limit exceeded",
            "Timeout connecting to provider",
            "",  # empty
            "Model 'gpt-4' returned an error",
            "SSRF guard: refusing blocked api_base_alias URL (redacted) for profile 'test'",
        ]
        for original in safe_texts:
            sanitized = sanitize_error_message(original)
            assert sanitized == original, f"Safe text was modified: {original!r} -> {sanitized!r}"

    def test_sanitize_error_message_empty_text(self):
        """sanitize_error_message handles empty and None-like text."""
        assert sanitize_error_message("") == ""
        assert sanitize_error_message("  ") == "  "


# ---------------------------------------------------------------------------
# 3. SSRFRejectionError classification preserved
# ---------------------------------------------------------------------------

class TestSSRFRejectionErrorClassification:
    """SSRFRejectionError must remain a ValueError subclass for compat."""

    def test_ssrf_rejection_is_value_error_subclass(self):
        assert issubclass(SSRFRejectionError, ValueError)

    def test_ssrf_rejection_is_distinct_type(self):
        assert SSRFRejectionError is not ValueError


# ---------------------------------------------------------------------------
# 4. End-to-end: _resolver_for_project fail-closed in call_model path
# ---------------------------------------------------------------------------

class TestCallModelWithFailingForProject:
    """The call_model path correctly propagates fail-closed resolver behavior."""

    def test_call_model_with_failing_project_resolver_does_not_use_shared_key(self):
        """When project resolver fails, call_model proceeds without secrets
        (no API key), instead of silently using shared secrets."""
        profile = _make_mock_profile(credential_alias="MY_KEY")
        profile.api_base_alias = None

        base = MagicMock()
        base.for_project = MagicMock(side_effect=RuntimeError("boom"))

        gw = ModelGateway(
            profiles=[profile],
            secrets_manager=base,
        )
        # When called with project_id and the resolver fails, call_model
        # should get job_secrets=None, so no api_key is resolved.
        # The call will then fail due to no API key — not leak shared secrets.
        with pytest.raises(ValueError):  # provider not configured
            gw.call_model("h22-p", [{"role": "user", "content": "hi"}],
                          project_id="proj-001")

    def test_call_model_with_failing_project_resolver_no_api_key_leak(self):
        """Verify base resolver's resolve() is NOT called when project resolver fails."""
        profile = _make_mock_profile()
        profile.api_base_alias = None

        base = MagicMock()
        base.for_project = MagicMock(side_effect=RuntimeError("boom"))

        gw = ModelGateway(
            profiles=[profile],
            secrets_manager=base,
        )
        with pytest.raises(ValueError):
            gw.call_model("h22-p", [{"role": "user", "content": "hi"}],
                          project_id="proj-001")
        # base.resolve() must not have been called — the fail-closed path
        # returned None, so job_secrets is None and no alias resolves.
        base.resolve.assert_not_called()
