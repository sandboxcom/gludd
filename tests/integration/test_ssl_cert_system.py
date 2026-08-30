"""Integration tests for the SSL certificate management system.

Tests key generation, CSR/self-signing, CA chains, certificate parsing,
algorithm evaluation, compliance checks, CA jurisdiction lookup, the collection
ASN.1 boundary, and the end-to-end agent flow.
"""

from __future__ import annotations

import datetime

import pytest
from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
    encode_der,
    generate_oid,
    lookup_oid,
    parse_der,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from general_ludd.ssl_agent import (
    ComplianceProfile,
    algorithm_evaluate,
    ca_jurisdiction_lookup,
    cert_parse,
    compliance_check,
    generate_ca_chain,
    generate_csr,
    generate_key_pair,
    self_sign_cert,
    ssl_agent_flow,
)


class TestKeyGeneration:
    def test_generate_rsa_2048(self) -> None:
        key = generate_key_pair("rsa-2048")
        assert key.key_type == "rsa-2048"
        assert key.public_pem.startswith(b"-----BEGIN PUBLIC KEY-----")
        assert key.private_pem.startswith(b"-----BEGIN PRIVATE KEY-----")

        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        assert isinstance(private_key, rsa.RSAPrivateKey)
        assert private_key.key_size == 2048

    def test_generate_ecdsa_p256(self) -> None:
        key = generate_key_pair("ecdsa-p256")
        assert key.key_type == "ecdsa-p256"

        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        assert isinstance(private_key, ec.EllipticCurvePrivateKey)
        assert private_key.key_size == 256

    def test_generate_ed25519(self) -> None:
        key = generate_key_pair("ed25519")
        assert key.key_type == "ed25519"

        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        assert isinstance(private_key, ed25519.Ed25519PrivateKey)

    def test_unknown_key_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown key type"):
            generate_key_pair("unknown-type")

    def test_rsa_private_key_can_sign(self) -> None:
        key = generate_key_pair("rsa-2048")
        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        public_key = private_key.public_key()

        message = b"test message for signing"
        signature = private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())

    def test_ecdsa_private_key_can_sign(self) -> None:
        key = generate_key_pair("ecdsa-p256")
        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        public_key = private_key.public_key()

        message = b"test message for ecdsa signing"
        signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))

    def test_ed25519_private_key_can_sign(self) -> None:
        key = generate_key_pair("ed25519")
        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        public_key = private_key.public_key()

        message = b"test message for ed25519 signing"
        signature = private_key.sign(message)
        public_key.verify(signature, message)


class TestCSRAndSelfSign:
    def test_generate_csr_rsa(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("example.com", key)

        assert csr.common_name == "example.com"
        assert csr.key_type == "rsa-2048"
        assert csr.csr_pem.startswith(b"-----BEGIN CERTIFICATE REQUEST-----")

    def test_generate_csr_ecdsa(self) -> None:
        key = generate_key_pair("ecdsa-p256")
        csr = generate_csr("ecdsa.example.com", key)

        assert csr.common_name == "ecdsa.example.com"
        assert csr.key_type == "ecdsa-p256"

        loaded = x509.load_pem_x509_csr(csr.csr_pem)
        assert isinstance(loaded, x509.CertificateSigningRequest)

    def test_self_sign_rsa_certificate(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("selfsigned.example.com", key)
        fields = self_sign_cert(csr, key, validity_days=90)

        assert fields.subject_cn == "selfsigned.example.com"
        assert fields.issuer_cn == "selfsigned.example.com"
        assert fields.version >= 1

    def test_self_sign_produces_parseable_cert(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("parseable.example.com", key)
        fields = self_sign_cert(csr, key, validity_days=30)

        assert fields.not_before
        assert fields.not_after
        assert fields.serial_number
        assert int(fields.serial_number) > 0

    def test_validity_period_is_correct(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("validity.example.com", key)
        fields = self_sign_cert(csr, key, validity_days=7)

        not_before = datetime.datetime.fromisoformat(fields.not_before)
        not_after = datetime.datetime.fromisoformat(fields.not_after)
        delta = not_after - not_before
        assert 6 <= delta.days <= 8


class TestCAChain:
    def test_generate_ca_chain_creates_two_certs(self) -> None:
        chain = generate_ca_chain("Test Root CA", "test.example.com")

        assert "ca_cert" in chain
        assert "leaf_cert" in chain
        assert "ca_cert_pem" in chain
        assert "leaf_cert_pem" in chain
        assert isinstance(chain["ca_cert"], x509.Certificate)
        assert isinstance(chain["leaf_cert"], x509.Certificate)

    def test_leaf_issuer_is_ca_subject(self) -> None:
        chain = generate_ca_chain("My Root CA", "leaf.example.com")

        ca_cert = chain["ca_cert"]
        leaf_cert = chain["leaf_cert"]
        assert leaf_cert.issuer == ca_cert.subject

    def test_ca_has_basic_constraints_ca_true(self) -> None:
        chain = generate_ca_chain("Root CA", "leaf2.example.com")

        ca_cert = chain["ca_cert"]
        ext = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert ext.value.ca is True

    def test_leaf_has_basic_constraints_ca_false(self) -> None:
        chain = generate_ca_chain("Root CA", "leaf3.example.com")

        leaf_cert = chain["leaf_cert"]
        ext = leaf_cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert ext.value.ca is False

    def test_chain_valid_flag_is_true(self) -> None:
        chain = generate_ca_chain("Root CA", "leaf4.example.com")
        assert chain["chain_valid"] is True

    def test_chain_produces_parseable_fields(self) -> None:
        chain = generate_ca_chain("Root CA", "leaf5.example.com")
        fields = cert_parse(chain["leaf_cert"])
        assert fields.subject_cn == "leaf5.example.com"
        assert fields.issuer_cn == "Root CA"


class TestCertificateParsing:
    def test_parse_extracts_subject_cn(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("parse-test.example.com", key)
        fields = self_sign_cert(csr, key)

        assert fields.subject_cn == "parse-test.example.com"
        assert fields.issuer_cn == "parse-test.example.com"

    def test_parse_extracts_serial_number(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("serial.example.com", key)
        fields = self_sign_cert(csr, key)

        assert fields.serial_number
        serial_int = int(fields.serial_number)
        assert serial_int > 0

    def test_parse_extracts_validity(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("validity-parse.example.com", key)
        fields = self_sign_cert(csr, key)

        not_before = datetime.datetime.fromisoformat(fields.not_before)
        not_after = datetime.datetime.fromisoformat(fields.not_after)
        assert not_before < not_after
        assert not_after < datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=400)

    def test_parse_extracts_signature_algorithm(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("sig-algo.example.com", key)
        fields = self_sign_cert(csr, key)

        assert fields.signature_algorithm
        assert len(fields.signature_algorithm) > 0

    def test_parse_extracts_public_key_algorithm(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("pk-algo.example.com", key)
        fields = self_sign_cert(csr, key)

        assert fields.public_key_algorithm
        assert len(fields.public_key_algorithm) > 0

    def test_parse_extracts_version(self) -> None:
        key = generate_key_pair("rsa-2048")
        csr = generate_csr("version.example.com", key)
        fields = self_sign_cert(csr, key)

        assert fields.version >= 1


class TestSecurityCollectionASN1:
    def test_fqcn_der_roundtrip(self) -> None:
        structure = {
            "type": "SEQUENCE",
            "children": [
                {"type": "OID", "value": "2.5.4.3"},
                {"type": "UTF8String", "value": "integration.example"},
            ],
        }

        assert parse_der(encode_der(structure)) == {
            "type": "SEQUENCE",
            "class": "UNIVERSAL",
            "children": [
                {"type": "OID", "class": "UNIVERSAL", "value": "2.5.4.3"},
                {
                    "type": "UTF8String",
                    "class": "UNIVERSAL",
                    "value": "integration.example",
                },
            ],
        }

    def test_lookup_known_oid_by_dotted_string(self) -> None:
        result = lookup_oid("2.5.4.3")
        assert result["name"] == "commonName"
        assert result["oid"] == "2.5.4.3"

    def test_lookup_organization_name(self) -> None:
        result = lookup_oid("2.5.4.10")
        assert result["name"] == "organizationName"

    def test_lookup_ec_public_key(self) -> None:
        result = lookup_oid("1.2.840.10045.2.1")
        assert result["name"] == "ecPublicKey"

    def test_lookup_unknown_oid_is_explicit(self) -> None:
        result = lookup_oid("9.9.9.9.9.9.9.9")
        assert result == {
            "oid": "9.9.9.9.9.9.9.9",
            "name": "unknown",
            "description": "Unknown OID",
        }

    def test_generate_oid_uses_requested_parent(self) -> None:
        oid = generate_oid("1.2.840.113549.1.1", "integration")
        assert oid.startswith("1.2.840.113549.1.1.")


class TestAlgorithmEvaluation:
    def test_evaluate_rsa_2048(self) -> None:
        result = algorithm_evaluate("rsa-2048")
        assert result.algorithm == "RSA-2048"
        assert result.strength == "medium"
        assert result.is_recommended is False

    def test_evaluate_sha1(self) -> None:
        result = algorithm_evaluate("sha-1")
        assert result.algorithm == "SHA-1"
        assert result.strength == "weak"
        assert result.is_recommended is False
        assert len(result.issue) > 0
        assert len(result.recommendation) > 0

    def test_evaluate_unknown_algorithm(self) -> None:
        result = algorithm_evaluate("nonexistent-algo")
        assert result.algorithm == "nonexistent-algo"
        assert result.strength == "unknown"
        assert result.is_recommended is False

    def test_rsa_2048_has_recommendation(self) -> None:
        result = algorithm_evaluate("rsa-2048")
        assert result.recommendation
        assert len(result.recommendation) > 0

    def test_sha1_has_issue(self) -> None:
        result = algorithm_evaluate("sha-1")
        assert result.issue
        assert "collision" in result.issue.lower() or "broken" in result.issue.lower()


class TestComplianceCheck:
    def _make_test_cert(self, cn: str) -> tuple[bytes, x509.Certificate]:
        key = generate_key_pair("rsa-2048")
        private_key = serialization.load_pem_private_key(
            key.private_pem, password=None
        )
        csr = generate_csr(cn, key)
        csr_obj = x509.load_pem_x509_csr(csr.csr_pem)
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, cn)])
            )
            .issuer_name(
                x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, cn)])
            )
            .public_key(csr_obj.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.UTC))
            .not_valid_after(
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .sign(private_key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        return cert_pem, cert

    def test_fips_check_passes_rsa_2048_sha256(self) -> None:
        cert_pem, _cert = self._make_test_cert("fips-test.example.com")
        result = compliance_check(cert_pem, ComplianceProfile.FIPS)
        assert result.profile == "fips"
        assert result.passed is True
        assert len(result.failures) == 0

    def test_pci_check_passes_rsa_2048_sha256(self) -> None:
        cert_pem, _cert = self._make_test_cert("pci-test.example.com")
        result = compliance_check(cert_pem, ComplianceProfile.PCI)
        assert result.profile == "pci"
        assert result.passed is True

    def test_hipaa_check_passes_rsa_2048_sha256(self) -> None:
        cert_pem, _cert = self._make_test_cert("hipaa-test.example.com")
        result = compliance_check(cert_pem, ComplianceProfile.HIPAA)
        assert result.profile == "hipaa"
        assert result.passed is True

    def test_checks_include_key_size(self) -> None:
        cert_pem, _cert = self._make_test_cert("keysize-test.example.com")
        result = compliance_check(cert_pem, ComplianceProfile.FIPS)
        key_size_checks = [c for c in result.checks if "key_size" in c["check"]]
        assert len(key_size_checks) == 1
        assert key_size_checks[0]["passed"] is True

    def test_compliance_result_has_checks(self) -> None:
        cert_pem, _cert = self._make_test_cert("checks-test.example.com")
        result = compliance_check(cert_pem, ComplianceProfile.PCI)
        assert len(result.checks) >= 2


class TestCAJurisdictionLookup:
    def test_lookup_lets_encrypt(self) -> None:
        result = ca_jurisdiction_lookup("letsencrypt")
        assert result is not None
        assert result.ca_name == "Let's Encrypt"
        assert result.jurisdiction_country == "US"
        assert result.jurisdiction_state == "California"
        assert result.is_public_trust is True

    def test_lookup_digicert(self) -> None:
        result = ca_jurisdiction_lookup("digicert")
        assert result is not None
        assert result.ca_name == "DigiCert"
        assert result.jurisdiction_country == "US"
        assert result.jurisdiction_state == "Utah"
        assert result.is_public_trust is True

    def test_lookup_unknown_ca_returns_none(self) -> None:
        result = ca_jurisdiction_lookup("nonexistent-ca")
        assert result is None

    def test_letsencrypt_has_root_cert_url(self) -> None:
        result = ca_jurisdiction_lookup("letsencrypt")
        assert result is not None
        assert result.root_cert_url
        assert "http" in result.root_cert_url

    def test_digicert_has_root_cert_url(self) -> None:
        result = ca_jurisdiction_lookup("digicert")
        assert result is not None
        assert result.root_cert_url
        assert "http" in result.root_cert_url


class TestEndToEndAgentFlow:
    def test_agent_flow_returns_all_components(self) -> None:
        result = ssl_agent_flow(
            common_name="e2e.example.com",
            key_type="rsa-2048",
            validity_days=365,
        )

        assert result.common_name == "e2e.example.com"
        assert result.key_pair is not None
        assert result.cert_fields is not None
        assert result.algorithm_eval is not None
        assert len(result.compliance_results) >= 1
        assert len(result.ca_jurisdictions) == 2
        assert result.chain_verified is True

    def test_agent_flow_artifacts_contain_keys(self) -> None:
        result = ssl_agent_flow(
            common_name="artifacts.example.com",
            key_type="ecdsa-p256",
        )

        assert "public_key.pem" in result.artifacts
        assert "private_key.pem" in result.artifacts
        assert "csr.pem" in result.artifacts
        assert "ca_cert.pem" in result.artifacts
        assert "leaf_cert.pem" in result.artifacts
        assert result.artifacts["public_key.pem"].startswith(
            b"-----BEGIN PUBLIC KEY-----"
        )

    def test_agent_flow_ecdsa_key(self) -> None:
        result = ssl_agent_flow(
            common_name="ecdsa-flow.example.com",
            key_type="ecdsa-p256",
        )

        assert result.key_pair.key_type == "ecdsa-p256"

    def test_agent_flow_ed25519_key(self) -> None:
        result = ssl_agent_flow(
            common_name="ed25519-flow.example.com",
            key_type="ed25519",
        )

        assert result.key_pair.key_type == "ed25519"

    def test_agent_flow_custom_profiles(self) -> None:
        result = ssl_agent_flow(
            common_name="custom-profiles.example.com",
            profiles=["fips"],
        )

        assert len(result.compliance_results) == 1
        assert result.compliance_results[0].profile == "fips"

    def test_agent_flow_custom_ca_names(self) -> None:
        result = ssl_agent_flow(
            common_name="custom-ca.example.com",
            ca_names=["letsencrypt"],
        )

        assert len(result.ca_jurisdictions) == 1
        assert result.ca_jurisdictions[0].ca_name == "Let's Encrypt"

    def test_agent_flow_chain_verification(self) -> None:
        result = ssl_agent_flow(
            common_name="chain-verify.example.com",
            key_type="rsa-2048",
        )

        assert result.chain_verified is True

    def test_agent_flow_algorithm_eval_in_result(self) -> None:
        result = ssl_agent_flow(
            common_name="algo-eval.example.com",
            key_type="rsa-2048",
        )

        assert result.algorithm_eval["algorithm"] == "RSA-2048"
        assert result.algorithm_eval["strength"] == "medium"
        assert result.algorithm_eval["is_recommended"] is False
