"""Deep identity provider and auth tests.

Covers token validation edge cases, SAML role mapping and attribute
resolution, identity resolution (NameID formats, issuer chains), and
session management (rotation chains, corrupted cleanup, concurrent access).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.security.auth import (
    check_admin_token,
    check_bearer_token,
    load_auth_posture,
    require_auth_env,
    verify_psk,
)
from general_ludd.security.session_ttl import (
    SessionManager,
    SessionRecord,
    SessionValidation,
)
from general_ludd.xml_utils import parse_saml_assertion

# ---------------------------------------------------------------------------
# Saml identity fixtures
# ---------------------------------------------------------------------------

SAML_WITH_ATTRIBUTES = """<?xml version="1.0" encoding="UTF-8"?>
<saml2:Assertion
 xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
 ID="_attr001"
 IssueInstant="2099-01-01T00:00:00Z"
 Version="2.0">
 <saml2:Issuer>https://idp.corp.example.com</saml2:Issuer>
 <saml2:Subject>
  <saml2:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">
   xyz-persistent-id-001
  </saml2:NameID>
 </saml2:Subject>
 <saml2:Conditions
  NotBefore="2099-01-01T00:00:00Z"
  NotOnOrAfter="2099-12-31T23:59:59Z"/>
 <saml2:AttributeStatement>
  <saml2:Attribute Name="urn:oid:1.3.6.1.4.1.5923.1.1.1.6">
   <saml2:AttributeValue>student@example.com</saml2:AttributeValue>
  </saml2:Attribute>
  <saml2:Attribute Name="urn:oid:1.3.6.1.4.1.5923.1.1.1.1">
   <saml2:AttributeValue>employee</saml2:AttributeValue>
   <saml2:AttributeValue>admin</saml2:AttributeValue>
   <saml2:AttributeValue>developer</saml2:AttributeValue>
  </saml2:Attribute>
  <saml2:Attribute Name="urn:oid:1.3.6.1.4.1.5923.1.1.1.10">
   <saml2:AttributeValue>ou=engineering,dc=example,dc=com</saml2:AttributeValue>
  </saml2:Attribute>
  <saml2:Attribute Name="urn:oid:2.5.4.4">
   <saml2:AttributeValue>Smith</saml2:AttributeValue>
  </saml2:Attribute>
  <saml2:Attribute Name="urn:oid:2.5.4.42">
   <saml2:AttributeValue>John</saml2:AttributeValue>
  </saml2:Attribute>
 </saml2:AttributeStatement>
 <saml2:AuthnStatement AuthnInstant="2099-01-01T00:00:00Z">
  <saml2:AuthnContext>
   <saml2:AuthnContextClassRef>
    urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport
   </saml2:AuthnContextClassRef>
  </saml2:AuthnContext>
 </saml2:AuthnStatement>
</saml2:Assertion>"""

SAML_NO_ATTRIBUTES = """<?xml version="1.0" encoding="UTF-8"?>
<saml2:Assertion
 xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
 ID="_empty001"
 IssueInstant="2099-01-01T00:00:00Z"
 Version="2.0">
 <saml2:Issuer>https://idp.example.com</saml2:Issuer>
 <saml2:Subject>
  <saml2:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">
   anonymous
  </saml2:NameID>
 </saml2:Subject>
</saml2:Assertion>"""

SAML_MALFORMED = "<not>valid<xml>"

SAML_BARE_ASSERTION = """<?xml version="1.0" encoding="UTF-8"?>
<Assertion xmlns="urn:oasis:names:tc:SAML:2.0:assertion"
 ID="_bare001"
 IssueInstant="2099-01-01T00:00:00Z"
 Version="2.0">
 <Issuer>https://idp-legacy.example.com</Issuer>
 <Subject>
  <NameID>legacy-user</NameID>
 </Subject>
</Assertion>"""


# ---------------------------------------------------------------------------
# 1. Token validation — timing attack resistance and edge cases
# ---------------------------------------------------------------------------


class TestTokenValidationTiming:
    """verify_psk uses hmac.compare_digest for constant-time comparison."""

    def test_equal_length_strings_produce_correct_result(self) -> None:
        assert verify_psk("secret-key-12345", "secret-key-12345") is True
        assert verify_psk("secret-key-12345", "Secret-key-12345") is False

    def test_different_length_strings_do_not_crash(self) -> None:
        assert verify_psk("short", "a-very-long-key-that-exceeds-the-first") is False
        assert verify_psk("a-very-long-key-that-exceeds", "short") is False

    def test_single_character_keys(self) -> None:
        assert verify_psk("a", "a") is True
        assert verify_psk("a", "b") is False

    def test_unicode_keys_compare_correctly(self) -> None:
        assert verify_psk("caf\u00e9", "caf\u00e9") is True
        assert verify_psk("caf\u00e9", "cafe") is False

    def test_keys_with_whitespace_are_literal(self) -> None:
        assert verify_psk(" key ", " key ") is True
        assert verify_psk("key", " key ") is False
        assert verify_psk(" key ", "key") is False

    def test_unexpected_input_types(self) -> None:
        with pytest.raises((TypeError, AttributeError)):
            verify_psk(None, "key")  # type: ignore[arg-type]
        with pytest.raises((TypeError, AttributeError)):
            verify_psk("key", 123)  # type: ignore[arg-type]


class TestBearerTokenEdgeCases:
    """check_bearer_token handles malformed and edge-case headers."""

    def test_bearer_prefix_case_sensitive(self) -> None:
        assert check_bearer_token("bearer mytoken", "mytoken") is False
        assert check_bearer_token("BEARER mytoken", "mytoken") is False
        assert check_bearer_token(" Bearer mytoken", "mytoken") is False

    def test_bearer_with_extra_whitespace(self) -> None:
        assert check_bearer_token("Bearer   mytoken", "  mytoken") is False

    def test_multiple_spaces_in_token(self) -> None:
        assert check_bearer_token("Bearer token with spaces", "token with spaces") is True

    def test_empty_token_after_bearer(self) -> None:
        assert check_bearer_token("Bearer ", "any-secret") is False

    def test_bearer_with_trailing_newline(self) -> None:
        assert check_bearer_token("Bearer token\n", "token\n") is True


class TestAdminTokenEdgeCases:
    """check_admin_token handles env fallback and edge inputs."""

    def test_unicode_admin_token(self) -> None:
        assert check_admin_token("ad\u0127in-token", "ad\u0127in-token") is True

    def test_very_long_token(self) -> None:
        long_token = "x" * 4096
        assert check_admin_token(long_token, long_token) is True

    def test_env_fallback_with_stripped_whitespace(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ADMIN_TOKEN": "  env-value  "}, clear=True):
            assert check_admin_token("env-value") is False

    def test_empty_env_token_blocks_all(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ADMIN_TOKEN": ""}, clear=True):
            assert check_admin_token("anything") is False


# ---------------------------------------------------------------------------
# 2. Role mapping — SAML attribute → role resolution
# ---------------------------------------------------------------------------


class TestSamlRoleMapping:
    """Identity roles derived from SAML attribute assertions."""

    def test_multi_valued_role_attribute(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        attrs = result.get("attributes", {})
        roles = attrs.get("urn:oid:1.3.6.1.4.1.5923.1.1.1.1", [])
        assert "employee" in roles
        assert "admin" in roles
        assert "developer" in roles
        assert len(roles) == 3

    def test_email_attribute_resolves_identity(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        attrs = result.get("attributes", {})
        email = attrs.get("urn:oid:1.3.6.1.4.1.5923.1.1.1.6", [])
        assert email == ["student@example.com"]

    def test_organizational_unit_attribute(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        attrs = result.get("attributes", {})
        ou = attrs.get("urn:oid:1.3.6.1.4.1.5923.1.1.1.10", [])
        assert "ou=engineering,dc=example,dc=com" in ou

    def test_surname_and_given_name_attributes(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        attrs = result.get("attributes", {})
        assert attrs.get("urn:oid:2.5.4.4") == ["Smith"]
        assert attrs.get("urn:oid:2.5.4.42") == ["John"]

    def test_no_attribute_statement_yields_empty(self) -> None:
        result = parse_saml_assertion(SAML_NO_ATTRIBUTES)
        assert "attributes" not in result

    def test_attribute_map_is_empty_for_malformed_xml(self) -> None:
        result = parse_saml_assertion(SAML_MALFORMED)
        assert result == {}

    def test_authn_context_class_ref_resolves(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        authn = result.get("authn", {})
        assert "PasswordProtectedTransport" in authn.get("context", "")

    def test_authn_instant_is_present(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        authn = result.get("authn", {})
        assert authn.get("instant") == "2099-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 3. Identity resolution — NameID formats and issuer chains
# ---------------------------------------------------------------------------


class TestSamlIdentityResolution:
    """SAML Subject/NameID and Issuer extraction for identity resolution."""

    def test_persistent_nameid_format(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        subject = result.get("subject", {})
        assert subject["name_id"] == "xyz-persistent-id-001"
        assert "persistent" in subject["format"]

    def test_unspecified_nameid_format(self) -> None:
        result = parse_saml_assertion(SAML_NO_ATTRIBUTES)
        subject = result.get("subject", {})
        assert subject["name_id"] == "anonymous"
        assert "unspecified" in subject["format"]

    def test_issuer_extracted_correctly(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        assert result["issuer"] == "https://idp.corp.example.com"

    def test_bare_default_ns_assertion(self) -> None:
        result = parse_saml_assertion(SAML_BARE_ASSERTION)
        assert result["issuer"] == "https://idp-legacy.example.com"
        subject = result.get("subject", {})
        assert subject["name_id"] == "legacy-user"

    def test_missing_subject_does_not_crash(self) -> None:
        xml_no_subject = """<?xml version="1.0" encoding="UTF-8"?>
        <saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
         ID="_nosub" IssueInstant="2099-01-01T00:00:00Z" Version="2.0">
         <saml2:Issuer>https://idp.example.com</saml2:Issuer>
        </saml2:Assertion>"""
        result = parse_saml_assertion(xml_no_subject)
        assert "subject" not in result

    def test_conditions_not_before_and_not_on_or_after(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        conditions = result.get("conditions", {})
        assert conditions["not_before"] == "2099-01-01T00:00:00Z"
        assert conditions["not_on_or_after"] == "2099-12-31T23:59:59Z"

    def test_future_assertion_is_not_expired(self) -> None:
        result = parse_saml_assertion(SAML_WITH_ATTRIBUTES)
        assert result.get("expired") is not True


# ---------------------------------------------------------------------------
# 4. Session management — rotation, corruption, concurrency
# ---------------------------------------------------------------------------


@pytest.fixture
def session_mgr(tmp_path: Path) -> SessionManager:
    return SessionManager(state_dir=tmp_path / "sessions")


class TestSessionRotationDeep:
    """Deep rotation tests: chains, parent linkage, and post-rotation validation."""

    def test_double_rotation_chain(self, session_mgr: SessionManager) -> None:
        s1 = session_mgr.create_session(audience="api")
        s2 = session_mgr.rotate_session(s1.session_id)
        assert s2 is not None
        s3 = session_mgr.rotate_session(s2.session_id)
        assert s3 is not None
        assert s3.session_id != s2.session_id != s1.session_id

    def test_rotation_preserves_ttl_config(self, session_mgr: SessionManager) -> None:
        s1 = session_mgr.create_session(audience="api", absolute_ttl_seconds=500, idle_ttl_seconds=200)
        s2 = session_mgr.rotate_session(s1.session_id)
        assert s2 is not None
        record = session_mgr._load_record(s2.session_id)
        assert record is not None
        assert record.absolute_ttl_seconds == 500
        assert record.idle_ttl_seconds == 200

    def test_rotation_parent_link_set(self, session_mgr: SessionManager) -> None:
        s1 = session_mgr.create_session(audience="api")
        s2 = session_mgr.rotate_session(s1.session_id)
        assert s2 is not None
        record = session_mgr._load_record(s2.session_id)
        assert record is not None
        assert record.parent_session_id == s1.session_id

    def test_original_is_revoked_after_rotation_chain(self, session_mgr: SessionManager) -> None:
        s1 = session_mgr.create_session(audience="api")
        s2 = session_mgr.rotate_session(s1.session_id)
        assert s2 is not None
        session_mgr.rotate_session(s2.session_id)
        validation = session_mgr.validate_session(s1.session_id, audience="api")
        assert validation is SessionValidation.REVOKED
        validation2 = session_mgr.validate_session(s2.session_id, audience="api")
        assert validation2 is SessionValidation.REVOKED


class TestSessionCorruptionResilience:
    """Session manager handles disk corruption gracefully."""

    def test_corrupted_json_file_returns_unknown(self, session_mgr: SessionManager, tmp_path: Path) -> None:
        result = session_mgr.create_session(audience="api")
        session_file = tmp_path / "sessions" / f"{result.session_id}.json"
        session_file.write_text("this is not valid {{{ json")
        validation = session_mgr.validate_session(result.session_id, audience="api")
        assert validation is SessionValidation.UNKNOWN

    def test_missing_key_in_record_returns_none(self, session_mgr: SessionManager, tmp_path: Path) -> None:
        result = session_mgr.create_session(audience="api")
        session_file = tmp_path / "sessions" / f"{result.session_id}.json"
        session_file.write_text(json.dumps({"session_id": result.session_id}))
        record = session_mgr._load_record(result.session_id)
        assert record is None

    def test_cleanup_removes_corrupted_files(self, session_mgr: SessionManager, tmp_path: Path) -> None:
        corrupt_path = tmp_path / "sessions" / "corrupt.json"
        corrupt_path.write_text("garbage")
        removed = session_mgr.cleanup_expired()
        assert removed >= 1
        assert not corrupt_path.exists()

    def test_cleanup_handles_empty_directory(self, session_mgr: SessionManager) -> None:
        assert session_mgr.cleanup_expired() == 0

    def test_orphan_file_with_wrong_extension_ignored(self, session_mgr: SessionManager, tmp_path: Path) -> None:
        orphan = tmp_path / "sessions" / "something.lock"
        orphan.write_text("lock data")
        removed = session_mgr.cleanup_expired()
        assert removed == 0
        assert orphan.exists()


class TestSessionConcurrencyDeep:
    """SessionManager cross-worker shared-state edge cases."""

    def test_touch_on_revoked_session_fails(self, session_mgr: SessionManager) -> None:
        result = session_mgr.create_session(audience="api")
        session_mgr.revoke_session(result.session_id)
        assert session_mgr.touch_session(result.session_id) is False

    def test_touch_on_unknown_session_fails(self, session_mgr: SessionManager) -> None:
        assert session_mgr.touch_session("nonexistent-id-123") is False

    def test_two_managers_see_expiration_simultaneously(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "sessions"
        mgr1 = SessionManager(state_dir=dir1)
        mgr2 = SessionManager(state_dir=dir1)
        result = mgr1.create_session(audience="api", absolute_ttl_seconds=0)
        time.sleep(0.1)
        assert mgr1.validate_session(result.session_id, audience="api") is SessionValidation.EXPIRED
        assert mgr2.validate_session(result.session_id, audience="api") is SessionValidation.EXPIRED

    def test_idle_and_absolute_expiry_independent(self, session_mgr: SessionManager) -> None:
        result = session_mgr.create_session(audience="api", absolute_ttl_seconds=60, idle_ttl_seconds=0)
        time.sleep(0.1)
        validation = session_mgr.validate_session(result.session_id, audience="api")
        assert validation is SessionValidation.EXPIRED


class TestAuthPostureCombinatorial:
    """load_auth_posture exhaustively covers the PSK/require-auth matrix."""

    def test_psk_disable_and_allow_no_auth_both_true(self) -> None:
        ap = load_auth_posture(
            "worker",
            {
                "GLUDD_PSK_DISABLE": "1",
                "GLUDD_ALLOW_NO_AUTH": "true",
            },
        )
        assert ap.no_auth is True
        assert ap.require_auth is False

    def test_psk_disable_ignored_when_psk_provided(self) -> None:
        ap = load_auth_posture(
            "daemon",
            {
                "GLUDD_AUTH_PSK": "present",
                "GLUDD_PSK_DISABLE": "1",
            },
        )
        assert ap.psk == "present"
        assert ap.no_auth is False

    def test_require_auth_explicit_false_with_no_psk(self) -> None:
        ap = load_auth_posture("worker", {"GLUDD_REQUIRE_AUTH": "0"})
        assert ap.psk == ""
        assert ap.no_auth is True
        assert ap.require_auth is True

    def test_disable_flags_various_casings(self) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", "Yes", "ON"):
            ap = load_auth_posture("worker", {"GLUDD_PSK_DISABLE": val})
            assert ap.require_auth is False, f"failed for {val!r}"


class TestRequireAuthEnvEdgeCases:
    """require_auth_env handles all truthy/falsy inputs."""

    def test_all_falsy_values(self) -> None:
        for val in ("0", "false", "no", "off", "", "maybe", "   "):
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is False, f"failed for {val!r}"

    def test_mixed_case_truthy(self) -> None:
        assert require_auth_env({"GLUDD_REQUIRE_AUTH": "True"}) is True
        assert require_auth_env({"GLUDD_REQUIRE_AUTH": "YES"}) is True
        assert require_auth_env({"GLUDD_REQUIRE_AUTH": "ON"}) is True


class TestSessionRecordSerialization:
    """SessionRecord to/from dict round-trip with edge values."""

    def test_record_with_parent(self) -> None:
        record = SessionRecord(
            session_id="sid-1",
            audience="admin",
            created_at=1000.0,
            last_access=2000.0,
            absolute_ttl_seconds=3600,
            idle_ttl_seconds=900,
            revoked=False,
            parent_session_id="parent-sid-0",
        )
        data = record.to_dict()
        roundtripped = SessionRecord.from_dict(data)
        assert roundtripped.session_id == "sid-1"
        assert roundtripped.parent_session_id == "parent-sid-0"

    def test_record_without_parent(self) -> None:
        record = SessionRecord(
            session_id="sid-2",
            audience="api",
            created_at=500.0,
            last_access=600.0,
            absolute_ttl_seconds=1800,
            idle_ttl_seconds=300,
        )
        data = record.to_dict()
        roundtripped = SessionRecord.from_dict(data)
        assert roundtripped.parent_session_id is None

    def test_record_with_zero_ttl(self) -> None:
        record = SessionRecord(
            session_id="sid-3",
            audience="api",
            created_at=0.0,
            last_access=0.0,
            absolute_ttl_seconds=0,
            idle_ttl_seconds=0,
        )
        data = record.to_dict()
        roundtripped = SessionRecord.from_dict(data)
        assert roundtripped.absolute_ttl_seconds == 0
        assert roundtripped.idle_ttl_seconds == 0
