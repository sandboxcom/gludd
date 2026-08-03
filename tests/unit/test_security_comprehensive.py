"""Comprehensive security audit tests: secrets, URLs, input validation, hashing, SSRF, XML."""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.security.cve_checker import (
    CveFinding,
    check_known_cves,
    cve_check_passes,
)
from general_ludd.security.fix_not_disable import (
    DISABLE_PATTERNS,
    FixNotDisablePolicy,
    is_disabling_action,
)
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
    _psk_admin_default_spec,
    check_capability,
    default_human_spec,
    default_spec,
    union_denied,
)
from general_ludd.security.sanitize import (
    _CREDENTIAL_PATTERNS as cred_patterns,
)
from general_ludd.security.sanitize import (
    _INTERNAL_HOST_PATTERNS as internal_host_patterns,
)
from general_ludd.security.sanitize import (
    sanitize_error_message,
    sanitize_path,
    validate_fetch_url,
)
from general_ludd.security.secure_xml import (
    XmlSecurityError,
    XmlSecurityLimits,
    parse_xml_string,
    validate_xml_payload,
)
from general_ludd.security.ssrf import (
    BLOCKED_HOST_NAMES,
    BLOCKED_METADATA_IPS,
    _ip_addr_is_blocked,
    _nonstandard_ip_blocked,
    host_is_blocked,
    is_url_blocked,
)
from general_ludd.security.url_fetch import (
    FetchPolicy,
    UnsafeURLError,
    _host_matches,
    _normalise_host_pattern,
    _validate_url,
)

# ---------------------------------------------------------------------------
# 1. Secret scanning pattern detection
# ---------------------------------------------------------------------------


class TestCredentialPatternDetection:
    """Verify that credential redaction patterns catch known secret shapes."""

    def test_aws_access_key_id_pattern(self) -> None:
        msg = "AWS key: token=AKIAIOSFODNN7EXAMPLE"
        result = sanitize_error_message(msg)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_github_personal_access_token(self) -> None:
        msg = "token=ghp_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize_error_message(msg)
        assert "ghp_abcdef" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_openai_sk_key_prefix(self) -> None:
        msg = "got error sk-proj-abcdefghijklmnopqrstuvwx"
        result = sanitize_error_message(msg)
        assert "sk-proj-" not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_bearer_token_jwt_format(self) -> None:
        msg = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.signature"
        result = sanitize_error_message(msg)
        assert "eyJhbGci" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_url_embedded_credentials_redacted(self) -> None:
        msg = "Connect: https://admin:hunter2@internal.corp/api/v2"
        result = sanitize_error_message(msg)
        assert "admin:hunter2" not in result
        assert "[REDACTED_CREDS_IN_URL]" in result

    def test_api_key_header_style(self) -> None:
        msg = "x-api-key: abcdef1234567890deadbeefcafebabe"
        result = sanitize_error_message(msg)
        assert "abcdef12345" not in result
        assert "[REDACTED_X_API_KEY]" in result

    def test_basic_auth_header(self) -> None:
        msg = "auth: Basic dXNlcm5hbWU6cGFzc3dvcmQxMjM="
        result = sanitize_error_message(msg)
        assert "dXNlcm5hbWU6" not in result
        assert "[REDACTED_BASIC_AUTH]" in result

    def test_secret_key_value_pair(self) -> None:
        msg = "secret=s3cr3t_k3y_v4lu3_here"
        result = sanitize_error_message(msg)
        assert "s3cr3t_k3y" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_password_assignment(self) -> None:
        msg = "password = SuperSecret!2024#Special"
        result = sanitize_error_message(msg)
        assert "SuperSecret" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_credential_patterns_alive(self) -> None:
        assert len(cred_patterns) >= 7


# ---------------------------------------------------------------------------
# 2. URL validation and sanitization
# ---------------------------------------------------------------------------


class TestURLValidation:
    """Verify URL fetch guards block hostile destinations."""

    def test_validate_fetch_url_https_public(self) -> None:
        assert validate_fetch_url("https://pypi.org/simple/requests/") is not None

    def test_validate_fetch_url_http_rejected(self) -> None:
        assert validate_fetch_url("http://example.com") is None

    def test_validate_fetch_url_empty_rejected(self) -> None:
        assert validate_fetch_url("") is None

    def test_validate_fetch_url_file_scheme_rejected(self) -> None:
        assert validate_fetch_url("file:///etc/passwd") is None

    def test_validate_fetch_url_ftp_scheme_rejected(self) -> None:
        assert validate_fetch_url("ftp://ftp.example.com/data") is None

    def test_fetch_policy_empty_hosts_raises(self) -> None:
        with pytest.raises(ValueError, match="allowed_hosts"):
            FetchPolicy(allowed_hosts=frozenset())

    def test_fetch_policy_default_schemes_https_only(self) -> None:
        policy = FetchPolicy(allowed_hosts=frozenset({"example.com"}))
        assert "https" in policy.allowed_schemes

    def test_fetch_policy_negative_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="max_bytes"):
            FetchPolicy(allowed_hosts=frozenset({"x.com"}), max_bytes=-1)

    def test_host_pattern_normalisation_wildcard(self) -> None:
        result = _normalise_host_pattern("*.example.com")
        assert result == "*.example.com"

    def test_host_pattern_normalisation_exact(self) -> None:
        result = _normalise_host_pattern("Example.COM.")
        assert result == "example.com"

    def test_host_matches_exact(self) -> None:
        assert _host_matches("example.com", frozenset({"example.com"})) is True

    def test_host_matches_wildcard(self) -> None:
        assert _host_matches("sub.example.com", frozenset({"*.example.com"})) is True

    def test_host_matches_star_all(self) -> None:
        assert _host_matches("anything.evil.com", frozenset({"*"})) is True

    def test_host_matches_no_match(self) -> None:
        assert _host_matches("evil.com", frozenset({"safe.com"})) is False

    def test_validate_url_credentials_raises(self) -> None:
        policy = FetchPolicy(allowed_hosts=frozenset({"example.com"}))
        with pytest.raises(UnsafeURLError, match="credentials"):
            _validate_url("https://u:p@example.com", policy)

    def test_validate_url_disallowed_scheme_raises(self) -> None:
        policy = FetchPolicy(allowed_hosts=frozenset({"example.com"}), allowed_schemes=frozenset({"https"}))
        with pytest.raises(UnsafeURLError, match="scheme"):
            _validate_url("http://example.com", policy)


# ---------------------------------------------------------------------------
# 3. Input validation edge cases
# ---------------------------------------------------------------------------


class TestInputValidationEdgeCases:
    """Verify path traversal, injection patterns, and adversarial inputs are blocked."""

    def test_path_traversal_double_encode(self) -> None:
        result = sanitize_path("foo/%2e%2e/bar")
        assert result is None or "%2e%2e" in (result or "")

    def test_path_traversal_null_byte(self) -> None:
        result = sanitize_path("safe\x00../etc/passwd")
        assert result is None or "../" not in (result or "")

    def test_path_traversal_backslash_windows(self) -> None:
        assert sanitize_path("..\\..\\windows\\system32") is None

    def test_path_traversal_leading_absolute_linux(self) -> None:
        assert sanitize_path("/etc/shadow") is None

    def test_path_traversal_leading_drv_windows(self) -> None:
        assert sanitize_path("C:\\Windows\\System32\\config\\SAM") is None

    def test_path_traversal_mixed_separators(self) -> None:
        assert sanitize_path("..\\../etc/hosts") is None

    def test_clean_path_with_spaces(self) -> None:
        result = sanitize_path("file name with spaces.txt")
        assert result is not None

    def test_nested_traversal_segments(self) -> None:
        assert sanitize_path("a/../../b/../../etc/passwd") is None

    def test_xss_script_tag_in_hostname_blocked_by_ssrf(self) -> None:
        assert host_is_blocked("<script>alert(1)</script>") is True

    def test_sql_injection_semicolons_in_path(self) -> None:
        result = sanitize_path("users'; DROP TABLE users; --")
        assert result is not None


# ---------------------------------------------------------------------------
# 4. Hash verification and integrity
# ---------------------------------------------------------------------------


class TestHashIntegrity:
    """Verify hashing and integrity patterns used across security modules."""

    def test_sha256_known_vector(self) -> None:
        digest = hashlib.sha256(b"gludd-integrity-check").hexdigest()
        assert len(digest) == 64
        expected = hashlib.sha256(b"gludd-integrity-check").hexdigest()
        assert digest == expected

    def test_sha256_different_inputs_produce_different_digests(self) -> None:
        a = hashlib.sha256(b"alpha").hexdigest()
        b = hashlib.sha256(b"beta").hexdigest()
        assert a != b

    def test_sha256_avalanche_property(self) -> None:
        d1 = hashlib.sha256(b"test").hexdigest()
        d2 = hashlib.sha256(b"test ").hexdigest()
        assert d1 != d2
        diff_count = sum(1 for c1, c2 in zip(d1, d2, strict=True) if c1 != c2)
        assert diff_count >= 20  # avalanche: roughly half the hex chars differ

    def test_cve_checker_returns_list(self) -> None:
        findings = check_known_cves(severity_threshold="low")
        assert isinstance(findings, list)
        for f in findings:
            assert isinstance(f, CveFinding)

    def test_cve_check_pass_threshold_high(self) -> None:
        assert cve_check_passes(severity_threshold="critical") is True


# ---------------------------------------------------------------------------
# 5. SSRF guardrail completeness
# ---------------------------------------------------------------------------


class TestSSRFGuardrail:
    """Verify the SSRF blocker catches all known bypass vectors."""

    def test_host_is_blocked_loopback_ipv4(self) -> None:
        assert host_is_blocked("127.0.0.1") is True

    def test_host_is_blocked_loopback_ipv6(self) -> None:
        assert host_is_blocked("::1") is True

    def test_host_is_blocked_localhost_name(self) -> None:
        assert host_is_blocked("localhost") is True

    def test_host_is_blocked_localhost_suffix(self) -> None:
        assert host_is_blocked("api.localhost") is True

    def test_host_is_blocked_metadata_aws(self) -> None:
        assert host_is_blocked("169.254.169.254") is True

    def test_host_is_blocked_metadata_alibaba(self) -> None:
        assert host_is_blocked("100.100.100.200") is True

    def test_host_is_blocked_metadata_gcp(self) -> None:
        assert host_is_blocked("metadata.google.internal") is True

    def test_host_is_blocked_private_rfc1918(self) -> None:
        assert host_is_blocked("10.0.0.1") is True
        assert host_is_blocked("192.168.1.1") is True

    def test_host_is_blocked_single_label(self) -> None:
        assert host_is_blocked("prometheus") is True

    def test_host_is_blocked_empty(self) -> None:
        assert host_is_blocked("") is True

    def test_host_is_blocked_null_byte_smuggling(self) -> None:
        assert host_is_blocked("localhost\x00.evil.com") is True

    def test_host_is_blocked_decimal_ip_loopback(self) -> None:
        assert _nonstandard_ip_blocked("2130706433") is True

    def test_host_is_blocked_trailing_dot_bypass(self) -> None:
        assert host_is_blocked("127.0.0.1.") is True

    def test_host_is_blocked_bracketed_ipv6(self) -> None:
        assert host_is_blocked("[::1]") is True

    def test_is_url_blocked_http_with_http_allowlist(self) -> None:
        assert is_url_blocked("http://example.com", scheme_allowlist={"http", "https"}) is False

    def test_is_url_blocked_localhost_url(self) -> None:
        assert is_url_blocked("https://localhost/api") is True

    def test_is_url_blocked_none_rejected(self) -> None:
        assert is_url_blocked("") is True

    def test_blocked_host_names_includes_all_cloud_providers(self) -> None:
        assert "metadata.google.internal" in BLOCKED_HOST_NAMES
        assert "metadata.azure.com" in BLOCKED_HOST_NAMES
        assert "localhost" in BLOCKED_HOST_NAMES

    def test_blocked_metadata_ips_covers_aws_alibaba(self) -> None:
        assert "169.254.169.254" in BLOCKED_METADATA_IPS
        assert "100.100.100.200" in BLOCKED_METADATA_IPS

    def test_ip_addr_is_blocked_rfc5737_test_net(self) -> None:
        import ipaddress

        assert _ip_addr_is_blocked(ipaddress.IPv4Address("192.0.2.1")) is True

    def test_ip_addr_is_blocked_multicast(self) -> None:
        import ipaddress

        assert _ip_addr_is_blocked(ipaddress.IPv4Address("224.0.0.1")) is True

    def test_ip_addr_is_blocked_unspecified(self) -> None:
        import ipaddress

        assert _ip_addr_is_blocked(ipaddress.IPv4Address("0.0.0.0")) is True


# ---------------------------------------------------------------------------
# 6. Fix-not-disable policy — edge cases
# ---------------------------------------------------------------------------


class TestFixNotDisableEdgeCases:
    """Cover edge cases and boundary conditions for the fix-not-disable policy."""

    def test_disable_patterns_immutable(self) -> None:
        assert isinstance(DISABLE_PATTERNS, frozenset)
        with pytest.raises((TypeError, AttributeError)):
            DISABLE_PATTERNS.add("test")  # type: ignore[attr-defined]

    def test_mixed_repair_and_disable_blocked_fail_closed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _ = policy.check_action("fix the bug by deleting the guardrail")
        assert allowed is False

    def test_neutral_language_allowed(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, _ = policy.check_action("review the output and log findings")
        assert allowed is True

    def test_empty_action_description(self) -> None:
        assert is_disabling_action("") is False

    def test_all_disable_keywords_are_lowercase(self) -> None:
        for pattern in DISABLE_PATTERNS:
            if pattern.startswith("#"):
                continue
            assert pattern == pattern.lower(), f"pattern {pattern!r} is not lowercase"


# ---------------------------------------------------------------------------
# 7. XML security limits
# ---------------------------------------------------------------------------


class TestXMLSecurity:
    """Verify XML parser enforces byte, depth, and node limits."""

    def test_xml_security_limits_defaults(self) -> None:
        limits = XmlSecurityLimits()
        assert limits.max_bytes == 4 * 1024 * 1024
        assert limits.max_depth == 64
        assert limits.max_nodes == 100_000

    def test_xml_security_limits_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            XmlSecurityLimits(max_bytes=-1)

    def test_xml_rejects_dtd(self) -> None:
        with pytest.raises(XmlSecurityError):
            parse_xml_string('<!DOCTYPE foo [<!ENTITY xxe "xxe">]><foo>&xxe;</foo>')

    def test_xml_rejects_external_entity(self) -> None:
        with pytest.raises(XmlSecurityError):
            parse_xml_string('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>')

    def test_xml_rejects_oversized_payload(self) -> None:
        limits = XmlSecurityLimits(max_bytes=10)
        with pytest.raises(XmlSecurityError, match="input_too_large"):
            validate_xml_payload(b"x" * 100, limits=limits)

    def test_xml_rejects_entity_declaration(self) -> None:
        with pytest.raises(XmlSecurityError, match="entity_forbidden"):
            validate_xml_payload(b"<!ENTITY foo 'bar'><root/>")

    def test_xml_rejects_dtd_declaration(self) -> None:
        with pytest.raises(XmlSecurityError, match="dtd_forbidden"):
            validate_xml_payload(b"<!DOCTYPE html><root/>")

    def test_xml_accepts_valid_small_payload(self) -> None:
        root = parse_xml_string("<root><child>hello</child></root>")
        assert root.tag == "root"
        assert root[0].tag == "child"
        assert root[0].text == "hello"

    def test_xml_deep_nesting_rejected(self) -> None:
        limits = XmlSecurityLimits(max_depth=3)
        deeply_nested = "<a>" + "<b>" * 50 + "</b>" * 50 + "</a>"
        with pytest.raises(XmlSecurityError, match="max_depth"):
            parse_xml_string(deeply_nested, limits=limits)


# ---------------------------------------------------------------------------
# 8. Permission spec security
# ---------------------------------------------------------------------------


class TestPermissionSpecSecurity:
    """Verify permission specs enforce least-privilege invariants."""

    def test_subagent_default_has_no_capabilities(self) -> None:
        spec = default_spec("subagent")
        assert len(spec.capabilities) == 0

    def test_unknown_agent_type_falls_back_to_subagent(self) -> None:
        spec = default_spec("nonexistent-agent-xyz")
        assert spec.agent_type == "subagent"
        assert len(spec.capabilities) == 0

    def test_build_spec_has_read_only(self) -> None:
        spec = default_spec("build")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]

    def test_primary_spec_has_read_only(self) -> None:
        spec = default_spec("primary")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]

    def test_human_admin_has_write(self) -> None:
        spec = default_human_spec("human-admin")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert "write" in cap.actions

    def test_human_viewer_restricted(self) -> None:
        spec = default_human_spec("human-viewer")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert "write" not in cap.actions
        assert "read" in cap.actions

    def test_unknown_human_role_falls_back_to_viewer(self) -> None:
        spec = default_human_spec("superuser")
        assert spec.agent_type == "human-viewer"

    def test_check_capability_grant(self) -> None:
        spec = default_human_spec("human-admin")
        assert check_capability(spec, "secret:openbao", "read") is True
        assert check_capability(spec, "secret:openbao", "write") is True

    def test_check_capability_deny_unknown(self) -> None:
        spec = default_spec("subagent")
        assert check_capability(spec, "secret:openbao", "read") is False

    def test_permission_spec_parser_roundtrip(self) -> None:
        yaml = """\
version: 1
agent_type: build
capabilities:
  - resource: secret:openbao
    actions: [read]
    constraints:
      openbao_paths: [secret/data/gludd/build/*]
denied: []
"""
        spec = PermissionSpecParser.parse(yaml)
        assert spec.version == 1
        assert spec.agent_type == "build"
        assert len(spec.capabilities) == 1

    def test_permission_spec_is_denied_empty_actions_blocks_all(self) -> None:
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:compute", actions=["destroy"])],
            denied=[Capability(resource="admin:compute", actions=[])],
        )
        assert spec.is_denied("admin:compute", "destroy") is True

    def test_intersection_narrows_actions(self) -> None:
        a = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "list"],
                    constraints={"openbao_paths": ["secret/data/gludd/*"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="operator",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/*"]},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        cap = result.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]

    def test_intersection_disjoint_resources_drop(self) -> None:
        a = PermissionSpec(
            agent_type="a",
            capabilities=[Capability(resource="admin:compute", actions=["destroy"])],
        )
        b = PermissionSpec(
            agent_type="b",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_union_denied_deduplicates(self) -> None:
        cap = Capability(resource="admin:compute", actions=["destroy"])
        merged = union_denied([cap], [cap])
        assert len(merged) == 1

    def test_psk_admin_spec_has_all_admin_capabilities(self) -> None:
        spec = _psk_admin_default_spec()
        assert check_capability(spec, "admin:account", "delete")
        assert check_capability(spec, "admin:sts", "issue")
        assert check_capability(spec, "admin:deploy", "write")

    def test_permission_subject_enum_values(self) -> None:
        assert PermissionSubject.AGENT == "agent"
        assert PermissionSubject.HUMAN == "human"
        assert PermissionSubject.STS_TOKEN == "sts_token"


# ---------------------------------------------------------------------------
# 9. Internal host redaction completeness
# ---------------------------------------------------------------------------


class TestInternalHostRedaction:
    """Verify all internal/private IPs and hostnames are redacted from error messages."""

    def test_redacts_private_10_range(self) -> None:
        result = sanitize_error_message("error: 10.42.7.1:8080")
        assert "10.42.7.1" not in result
        assert "[REDACTED_PRIVATE_IP]" in result

    def test_redacts_private_172_range(self) -> None:
        result = sanitize_error_message("error: 172.16.0.5")
        assert "172.16.0.5" not in result
        assert "[REDACTED_PRIVATE_IP]" in result

    def test_redacts_private_192_range(self) -> None:
        result = sanitize_error_message("error: 192.168.1.100")
        assert "192.168.1.100" not in result
        assert "[REDACTED_PRIVATE_IP]" in result

    def test_redacts_link_local(self) -> None:
        result = sanitize_error_message("error: 169.254.42.1")
        assert "169.254.42.1" not in result
        assert "[REDACTED_LINK_LOCAL_IP]" in result

    def test_redacts_gcp_metadata_host(self) -> None:
        result = sanitize_error_message("error: metadata.google.internal")
        assert "metadata.google.internal" not in result
        assert "[REDACTED_INTERNAL_HOST]" in result

    def test_redacts_instance_data_host(self) -> None:
        result = sanitize_error_message("error: instance-data.ec2.internal")
        assert "instance-data" not in result
        assert "[REDACTED_INTERNAL_HOST]" in result

    def test_redacts_localhost_with_port(self) -> None:
        result = sanitize_error_message("error: localhost:5432")
        assert "localhost" not in result
        assert "[REDACTED_INTERNAL_HOST]" in result

    def test_redacts_ipv6_loopback(self) -> None:
        result = sanitize_error_message("error: [::1]:9090")
        assert "::1" not in result
        assert "[REDACTED_LOOPBACK_IP]" in result

    def test_all_internal_host_patterns_defined(self) -> None:
        assert len(internal_host_patterns) == 14
