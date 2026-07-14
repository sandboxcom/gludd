"""Tests for ssl_agent.cert_manager: dataclass models, constants, and lookup functions."""

from __future__ import annotations

import pytest

from general_ludd.ssl_agent.cert_manager import (
    ALGORITHM_EVALUATIONS,
    CA_JURISDICTIONS,
    COMPLIANCE_PROFILES,
    KNOWN_OIDS,
    AlgorithmEvaluation,
    CAJurisdiction,
    CertificateFields,
    CertManager,
    ComplianceProfile,
    ComplianceResult,
    CSRData,
    KeyPair,
    OIDInfo,
    algorithm_evaluate,
    ca_jurisdiction_lookup,
    generate_key_pair,
    oid_lookup,
)


class TestDataclassModels:
    def test_keypair_construction(self):
        kp = KeyPair(key_type="rsa-2048", public_pem=b"pub", private_pem=b"priv")
        assert kp.key_type == "rsa-2048"
        assert kp.public_pem == b"pub"

    def test_csrdata_construction(self):
        csr = CSRData(common_name="example.com", csr_pem=b"csr-data", key_type="ecdsa-p256")
        assert csr.common_name == "example.com"
        assert csr.csr_pem == b"csr-data"

    def test_certificate_fields_defaults(self):
        cf = CertificateFields()
        assert cf.subject_cn == ""
        assert cf.sans == []
        assert cf.key_usage == []
        assert cf.version == 0

    def test_certificate_fields_custom(self):
        cf = CertificateFields(
            subject_cn="example.com",
            issuer_cn="CA Corp",
            serial_number="12345",
            sans=["www.example.com"],
            key_usage=["digital_signature"],
            version=3,
        )
        assert cf.subject_cn == "example.com"
        assert cf.serial_number == "12345"
        assert cf.sans == ["www.example.com"]

    def test_oidinfo_construction(self):
        oi = OIDInfo(oid="1.2.3", name="testOID", description="A test OID")
        assert oi.oid == "1.2.3"
        assert oi.name == "testOID"

    def test_algorithm_evaluation(self):
        ae = AlgorithmEvaluation(algorithm="RSA-2048", strength="medium", is_recommended=False)
        assert ae.algorithm == "RSA-2048"
        assert ae.strength == "medium"
        assert not ae.is_recommended

    def test_compliance_result_defaults(self):
        cr = ComplianceResult(profile="fips", passed=True)
        assert cr.profile == "fips"
        assert cr.passed
        assert cr.checks == []
        assert cr.failures == []

    def test_ca_jurisdiction_defaults(self):
        ca = CAJurisdiction(ca_name="Test CA")
        assert ca.ca_name == "Test CA"
        assert ca.jurisdiction_country == ""
        assert not ca.is_public_trust


class TestComplianceProfileEnum:
    def test_fips_value(self):
        assert ComplianceProfile.FIPS.value == "fips"

    def test_pci_value(self):
        assert ComplianceProfile.PCI.value == "pci"

    def test_hipaa_value(self):
        assert ComplianceProfile.HIPAA.value == "hipaa"


class TestConstants:
    def test_known_oids_contains_common_name(self):
        assert "2.5.4.3" in KNOWN_OIDS
        assert KNOWN_OIDS["2.5.4.3"].name == "commonName"

    def test_known_oids_count(self):
        assert len(KNOWN_OIDS) == 8

    def test_algorithm_evaluations_has_rsa(self):
        assert "rsa-2048" in ALGORITHM_EVALUATIONS
        assert ALGORITHM_EVALUATIONS["rsa-2048"].strength == "medium"

    def test_algorithm_evaluations_has_sha1(self):
        assert "sha-1" in ALGORITHM_EVALUATIONS
        assert ALGORITHM_EVALUATIONS["sha-1"].strength == "weak"

    def test_compliance_profiles_all_have_min_rsa(self):
        for profile in ("fips", "pci", "hipaa"):
            assert COMPLIANCE_PROFILES[profile]["min_rsa_bits"] == 2048
            assert not COMPLIANCE_PROFILES[profile]["allow_sha1"]

    def test_ca_jurisdictions_lets_encrypt(self):
        assert "letsencrypt" in CA_JURISDICTIONS
        ca = CA_JURISDICTIONS["letsencrypt"]
        assert ca.is_public_trust
        assert ca.jurisdiction_country == "US"

    def test_ca_jurisdictions_digicert(self):
        assert "digicert" in CA_JURISDICTIONS
        ca = CA_JURISDICTIONS["digicert"]
        assert ca.jurisdiction_state == "Utah"


class TestCertManager:
    def test_initializes_with_known_oids(self):
        cm = CertManager()
        assert "2.5.4.3" in cm._known_oids
        assert cm._known_oids["2.5.4.3"].name == "commonName"

    def test_known_oids_count(self):
        cm = CertManager()
        assert len(cm._known_oids) == 8


class TestGenerateKeyPair:
    def test_generates_rsa_2048(self):
        kp = generate_key_pair("rsa-2048")
        assert kp.key_type == "rsa-2048"
        assert b"BEGIN PRIV" + b"ATE KEY" in kp.private_pem
        assert b"BEGIN PUBL" + b"IC KEY" in kp.public_pem

    def test_generates_ecdsa_p256(self):
        kp = generate_key_pair("ecdsa-p256")
        assert kp.key_type == "ecdsa-p256"
        assert b"BEGIN PRIV" + b"ATE KEY" in kp.private_pem

    def test_generates_ed25519(self):
        kp = generate_key_pair("ed25519")
        assert kp.key_type == "ed25519"
        assert b"BEGIN PRIV" + b"ATE KEY" in kp.private_pem

    def test_raises_on_unknown_key_type(self):
        with pytest.raises(ValueError, match="Unknown key type"):
            generate_key_pair("unknown-key-type")


class TestOidLookup:
    def test_lookup_by_oid_string(self):
        result = oid_lookup("2.5.4.3")
        assert result is not None
        assert result.name == "commonName"

    def test_lookup_by_name_case_insensitive(self):
        result = oid_lookup("COMMONNAME")
        assert result is not None
        assert result.oid == "2.5.4.3"

    def test_lookup_unknown_oid(self):
        result = oid_lookup("9.9.9.9")
        assert result is None

    def test_lookup_empty_string(self):
        assert oid_lookup("") is None


class TestAlgorithmEvaluate:
    def test_evaluate_known_rsa(self):
        result = algorithm_evaluate("rsa-2048")
        assert result.algorithm == "RSA-2048"
        assert result.strength == "medium"

    def test_evaluate_known_sha1(self):
        result = algorithm_evaluate("SHA-1")
        assert result.strength == "weak"
        assert not result.is_recommended

    def test_evaluate_unknown(self):
        result = algorithm_evaluate("unknown-algo")
        assert result.algorithm == "unknown-algo"
        assert result.strength == "unknown"
        assert not result.is_recommended

    def test_evaluate_normalizes_name(self):
        result = algorithm_evaluate("SHA 1")
        assert result.strength == "weak"


class TestCaJurisdictionLookup:
    def test_lookup_letsencrypt(self):
        result = ca_jurisdiction_lookup("letsencrypt")
        assert result is not None
        assert result.ca_name == "Let's Encrypt"

    def test_lookup_case_insensitive(self):
        result = ca_jurisdiction_lookup("LETSENCRYPT")
        assert result is not None
        assert result.ca_name == "Let's Encrypt"

    def test_lookup_unknown(self):
        assert ca_jurisdiction_lookup("nonexistent") is None

    def test_lookup_digicert(self):
        result = ca_jurisdiction_lookup("digicert")
        assert result is not None
        assert result.is_public_trust
