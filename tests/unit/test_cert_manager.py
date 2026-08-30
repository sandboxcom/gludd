"""Structural tests for ssl_agent/cert_manager.py — SSL certificate management core."""

from __future__ import annotations

import pytest

from general_ludd.ssl_agent.cert_manager import (
    ALGORITHM_EVALUATIONS,
    CA_JURISDICTIONS,
    COMPLIANCE_PROFILES,
    AlgorithmEvaluation,
    CAJurisdiction,
    CertificateFields,
    ComplianceProfile,
    ComplianceResult,
    CSRData,
    KeyPair,
    algorithm_evaluate,
    ca_jurisdiction_lookup,
    cert_parse,
    compliance_check,
    generate_ca_chain,
    generate_csr,
    generate_key_pair,
    self_sign_cert,
)


class TestDataClasses:
    def test_key_pair_fields(self) -> None:
        kp = KeyPair(key_type="rsa-2048", public_pem=b"pub", private_pem=b"priv")
        assert kp.key_type == "rsa-2048"
        assert kp.public_pem == b"pub"
        assert kp.private_pem == b"priv"

    def test_csr_data_fields(self) -> None:
        csr = CSRData(common_name="example.com", csr_pem=b"csr", key_type="ed25519")
        assert csr.common_name == "example.com"
        assert csr.key_type == "ed25519"

    def test_certificate_fields_defaults(self) -> None:
        cf = CertificateFields()
        assert cf.subject_cn == ""
        assert cf.sans == []
        assert cf.key_usage == []
        assert cf.version == 0

    def test_algorithm_evaluation_fields(self) -> None:
        ae = AlgorithmEvaluation(
            algorithm="RSA-2048", strength="medium", is_recommended=False
        )
        assert ae.algorithm == "RSA-2048"
        assert ae.is_recommended is False

    def test_compliance_result_fields(self) -> None:
        cr = ComplianceResult(
            profile="fips", passed=True, checks=[], failures=[]
        )
        assert cr.profile == "fips"
        assert cr.passed is True

    def test_ca_jurisdiction_fields(self) -> None:
        cj = CAJurisdiction(ca_name="Let's Encrypt", is_public_trust=True)
        assert cj.ca_name == "Let's Encrypt"
        assert cj.is_public_trust is True


class TestComplianceProfile:
    def test_values(self) -> None:
        assert ComplianceProfile.FIPS.value == "fips"
        assert ComplianceProfile.PCI.value == "pci"
        assert ComplianceProfile.HIPAA.value == "hipaa"


class TestModuleConstants:
    def test_algorithm_evaluations_is_dict(self) -> None:
        assert isinstance(ALGORITHM_EVALUATIONS, dict)
        assert "rsa-2048" in ALGORITHM_EVALUATIONS

    def test_compliance_profiles_has_fips(self) -> None:
        assert "fips" in COMPLIANCE_PROFILES
        assert COMPLIANCE_PROFILES["fips"]["min_rsa_bits"] == 2048

    def test_ca_jurisdictions_has_letsencrypt(self) -> None:
        assert "letsencrypt" in CA_JURISDICTIONS
        assert CA_JURISDICTIONS["letsencrypt"].is_public_trust is True


class TestGenerateKeyPair:
    def test_rsa_2048(self) -> None:
        kp = generate_key_pair("rsa-2048")
        assert kp.key_type == "rsa-2048"
        assert b"BEGIN RSA PRIVATE KEY" in kp.private_pem or b"BEGIN PRIVATE KEY" in kp.private_pem
        assert b"-----BEGIN PUBLIC KEY-----" in kp.public_pem

    def test_ecdsa_p256(self) -> None:
        kp = generate_key_pair("ecdsa-p256")
        assert kp.key_type == "ecdsa-p256"
        assert len(kp.private_pem) > 0

    def test_ed25519(self) -> None:
        kp = generate_key_pair("ed25519")
        assert kp.key_type == "ed25519"
        assert len(kp.public_pem) > 0

    def test_unknown_key_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown key type"):
            generate_key_pair("unknown-type")


class TestGenerateCSR:
    def test_rsa_csr(self) -> None:
        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("test.example.com", kp)
        assert csr.common_name == "test.example.com"
        assert csr.key_type == "rsa-2048"
        assert b"-----BEGIN CERTIFICATE REQUEST-----" in csr.csr_pem

    def test_ed25519_csr(self) -> None:
        kp = generate_key_pair("ed25519")
        csr = generate_csr("ed.test", kp)
        assert csr.common_name == "ed.test"


class TestSelfSignCert:
    def test_self_sign_ed25519(self) -> None:
        kp = generate_key_pair("ed25519")
        csr = generate_csr("parse-test.example.com", kp)
        fields = self_sign_cert(csr, kp, validity_days=30)
        assert fields.subject_cn == "parse-test.example.com"
        assert fields.issuer_cn == "parse-test.example.com"
        assert isinstance(fields.serial_number, str)
        assert len(fields.serial_number) > 0

    def test_self_sign_rsa(self) -> None:
        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("rsa-cert.example.com", kp)
        fields = self_sign_cert(csr, kp, validity_days=1)
        assert fields.subject_cn == "rsa-cert.example.com"
        assert len(fields.not_before) > 0
        assert len(fields.not_after) > 0

    def test_cert_fields_has_key_usage(self) -> None:
        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("ku.example.com", kp)
        fields = self_sign_cert(csr, kp, validity_days=1)
        assert isinstance(fields.key_usage, list)
        assert isinstance(fields.sans, list)


class TestCertParse:
    def test_parse_self_signed(self) -> None:
        chain = generate_ca_chain("Parse CA", "leaf.local")
        cert_pem = chain["leaf_cert_pem"]
        parsed = cert_parse(cert_pem)
        assert isinstance(parsed, CertificateFields)
        assert parsed.subject_cn == "leaf.local"

    def test_parse_rsa_cert(self) -> None:
        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("rsa-cert.example.com", kp)
        fields = self_sign_cert(csr, kp, validity_days=1)
        assert fields.subject_cn == "rsa-cert.example.com"


class TestAlgorithmEvaluate:
    def test_known_algorithm(self) -> None:
        result = algorithm_evaluate("sha-1")
        assert result.algorithm == "SHA-1"
        assert result.strength == "weak"
        assert result.is_recommended is False

    def test_known_rsa_2048(self) -> None:
        result = algorithm_evaluate("rsa-2048")
        assert result.strength == "medium"

    def test_unknown_algorithm(self) -> None:
        result = algorithm_evaluate("fake-algo")
        assert result.strength == "unknown"
        assert result.is_recommended is False


class TestComplianceCheck:
    def test_fips_profile_on_pem(self) -> None:
        chain = generate_ca_chain("FIPS CA", "fips.local")
        result = compliance_check(chain["leaf_cert_pem"], ComplianceProfile.FIPS)
        assert isinstance(result, ComplianceResult)
        assert result.profile == "fips"


class TestCaJurisdictionLookup:
    def test_letsencrypt(self) -> None:
        result = ca_jurisdiction_lookup("letsencrypt")
        assert result is not None
        assert result.ca_name == "Let's Encrypt"
        assert result.jurisdiction_country == "US"

    def test_digicert(self) -> None:
        result = ca_jurisdiction_lookup("digicert")
        assert result is not None
        assert result.is_public_trust is True

    def test_unknown_ca(self) -> None:
        assert ca_jurisdiction_lookup("unknown-ca") is None


class TestGenerateCaChain:
    def test_generates_chain(self) -> None:
        result = generate_ca_chain("My Root CA", "leaf.local")
        assert "ca_cert" in result
        assert "leaf_cert" in result
        assert "ca_cert_pem" in result
        assert "leaf_cert_pem" in result
        assert "leaf_key_pair" in result
        assert "chain_valid" in result

    def test_chain_is_valid(self) -> None:
        result = generate_ca_chain("Test CA", "test.local")
        assert result["chain_valid"] is True
