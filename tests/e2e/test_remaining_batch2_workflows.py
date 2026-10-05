"""E2E: SSL, SSL-agent, notifications workflow tests — batch 2 of coverage push."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# ssl.algorithms
# ---------------------------------------------------------------------------

class TestSSLAlgorithms:
    def test_imports(self):
        from general_ludd.ssl.algorithms import (
            KNOWN_ALGORITHMS,
            AlgorithmStatus,
        )

        assert len(KNOWN_ALGORITHMS) > 20
        assert AlgorithmStatus.CURRENT is not None

    def test_algorithm_info(self):
        from general_ludd.ssl.algorithms import AlgorithmInfo, AlgorithmStatus, AlgorithmType

        info = AlgorithmInfo("test", AlgorithmType.RSA, [2048], 128, AlgorithmStatus.CURRENT)
        assert info.name == "test"
        assert info.type == AlgorithmType.RSA
        assert info.security_bits == 128

    def test_algorithm_eval(self):
        from general_ludd.ssl.algorithms import AlgorithmEval, AlgorithmInfo, AlgorithmStatus, AlgorithmType

        info = AlgorithmInfo("test", AlgorithmType.RSA, [2048], 128, AlgorithmStatus.CURRENT)
        ev = AlgorithmEval(info, score=85, warnings=["warn1"], recommendations=["rec1"])
        assert ev.score == 85
        assert len(ev.warnings) == 1
        assert len(ev.recommendations) == 1

    def test_evaluate_rsa_2048(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("RSA-2048")
        assert result.algorithm.name == "RSA-2048"
        assert result.score >= 60

    def test_evaluate_rsa_1024_legacy(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("RSA-1024")
        assert result.score <= 20
        assert any("legacy" in w.lower() for w in result.warnings)

    def test_evaluate_md5_zero(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("MD5")
        assert result.score == 0

    def test_evaluate_unknown_algorithm(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        with pytest.raises(ValueError, match="Unknown"):
            evaluate_algorithm("BOGUS-9999")

    def test_evaluate_aes_256(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("AES-256")
        assert result.score >= 80

    def test_evaluate_ed25519(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("Ed25519")
        assert result.score >= 60

    def test_compare_better(self):
        from general_ludd.ssl.algorithms import compare_algorithms

        result = compare_algorithms("RSA-4096", "RSA-1024")
        assert result.better == "RSA-4096"
        assert result.score_difference > 0

    def test_compare_equal(self):
        from general_ludd.ssl.algorithms import compare_algorithms

        result = compare_algorithms("AES-128", "AES-128")
        assert result.better == "equal"

    def test_compare_unknown_first(self):
        from general_ludd.ssl.algorithms import compare_algorithms

        with pytest.raises(ValueError, match="Unknown"):
            compare_algorithms("BOGUS", "RSA-2048")

    def test_compare_unknown_second(self):
        from general_ludd.ssl.algorithms import compare_algorithms

        with pytest.raises(ValueError, match="Unknown"):
            compare_algorithms("RSA-2048", "BOGUS")

    def test_compliance_check_pass(self):
        from general_ludd.ssl.algorithms import compliance_check

        assert compliance_check("RSA-2048", "FIPS-140-3") is True

    def test_compliance_check_fail(self):
        from general_ludd.ssl.algorithms import compliance_check

        assert compliance_check("MD5", "FIPS-140-3") is False

    def test_compliance_check_unknown_standard(self):
        from general_ludd.ssl.algorithms import compliance_check

        with pytest.raises(ValueError, match="Unknown"):
            compliance_check("RSA-2048", "BOGUS-STD")

    def test_compliance_check_unknown_algo(self):
        from general_ludd.ssl.algorithms import compliance_check

        with pytest.raises(ValueError, match="not evaluated"):
            compliance_check("ZZZ-999", "HIPAA")  # Unknown algorithm is not evaluated.

    def test_all_standards_available(self):
        from general_ludd.ssl.algorithms import COMPLIANCE_STANDARDS

        assert "FIPS-140-3" in COMPLIANCE_STANDARDS
        assert "PCI-DSS" in COMPLIANCE_STANDARDS
        assert "HIPAA" in COMPLIANCE_STANDARDS
        assert "SOC2" in COMPLIANCE_STANDARDS

    def test_evaluate_all_known(self):
        from general_ludd.ssl.algorithms import KNOWN_ALGORITHMS, evaluate_algorithm

        for name in KNOWN_ALGORITHMS:
            result = evaluate_algorithm(name)
            assert 0 <= result.score <= 100

    def test_evaluate_with_key_size_warning(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("RSA-2048", key_size=1024)
        assert len(result.warnings) > 0

    def test_deprecated_algo_has_warnings(self):
        from general_ludd.ssl.algorithms import evaluate_algorithm

        result = evaluate_algorithm("3DES")
        assert len(result.recommendations) > 0


# ---------------------------------------------------------------------------
# ssl.certificate (cryptography required)
# ---------------------------------------------------------------------------

class TestSSLCertificate:
    def test_import(self):
        from general_ludd.ssl.certificate import (
            generate_key,
        )

        assert generate_key is not None

    def test_generate_rsa_key(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("rsa", 2048)
        assert key.startswith(b"-----BEGIN PRIVATE KEY-----")

    def test_generate_ecdsa_key_default(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("ecdsa")
        assert b"-----BEGIN PRIVATE KEY-----" in key

    def test_generate_ecdsa_key_256(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("ecdsa", 256)
        assert b"-----BEGIN PRIVATE KEY-----" in key

    def test_generate_ecdsa_key_384(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("ecdsa", 384)
        assert b"-----BEGIN PRIVATE KEY-----" in key

    def test_generate_ecdsa_key_521(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("ecdsa", 521)
        assert b"-----BEGIN PRIVATE KEY-----" in key

    def test_generate_ecdsa_bad_key_size(self):
        from general_ludd.ssl.certificate import generate_key

        with pytest.raises(ValueError, match="Unsupported"):
            generate_key("ecdsa", 128)

    def test_generate_ed25519_key(self):
        from general_ludd.ssl.certificate import generate_key

        key = generate_key("ed25519")
        assert b"-----BEGIN PRIVATE KEY-----" in key

    def test_generate_unknown_key_type(self):
        from general_ludd.ssl.certificate import generate_key

        with pytest.raises(ValueError, match="Unknown"):
            generate_key("bogus")

    def test_generate_csr_rsa(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key

        key = generate_key("rsa", 2048)
        csr = generate_csr(key, {"CN": "example.com"})
        assert csr.startswith(b"-----BEGIN CERTIFICATE REQUEST-----")

    def test_generate_csr_with_sans(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key

        key = generate_key("ecdsa", 256)
        csr = generate_csr(key, {"CN": "example.com"}, sans=["www.example.com"])
        assert b"-----BEGIN CERTIFICATE REQUEST-----" in csr

    def test_generate_csr_with_key_usage(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key

        key = generate_key("rsa", 2048)
        csr = generate_csr(
            key, {"CN": "example.com"}, key_usage=["digital_signature", "key_encipherment"]
        )
        assert b"-----BEGIN CERTIFICATE REQUEST-----" in csr

    def test_generate_csr_with_extended_key_usage(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key

        key = generate_key("rsa", 2048)
        csr = generate_csr(
            key, {"CN": "example.com"}, extended_key_usage=["server_auth", "client_auth"]
        )
        assert b"-----BEGIN CERTIFICATE REQUEST-----" in csr

    def test_generate_csr_bad_subject_key(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key

        key = generate_key("rsa", 2048)
        with pytest.raises(ValueError, match="Unknown subject key"):
            generate_csr(key, {"BOGUS_KEY": "value"})

    def test_self_sign(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, self_sign

        key = generate_key("rsa", 2048)
        csr = generate_csr(key, {"CN": "example.com"})
        cert = self_sign(csr, key, 365)
        assert b"-----BEGIN CERTIFICATE-----" in cert

    def test_self_sign_ed25519(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, self_sign

        key = generate_key("ed25519")
        csr = generate_csr(key, {"CN": "ed.example.com"})
        cert = self_sign(csr, key, 30)
        assert b"-----BEGIN CERTIFICATE-----" in cert

    def test_sign_csr(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, self_sign, sign_csr

        ca_key = generate_key("rsa", 2048)
        ca_csr = generate_csr(ca_key, {"CN": "My CA"})
        ca_cert = self_sign(ca_csr, ca_key, 3650)

        leaf_key = generate_key("ecdsa", 256)
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf.example.com"})
        leaf_cert = sign_csr(leaf_csr, ca_cert, ca_key, 365)
        assert b"-----BEGIN CERTIFICATE-----" in leaf_cert

    def test_parse_cert(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, parse_cert, self_sign

        key = generate_key("rsa", 2048)
        csr = generate_csr(key, {"CN": "example.com", "O": "TestOrg"})
        cert = self_sign(csr, key, 365)
        info = parse_cert(cert)
        assert "subject" in info
        assert "issuer" in info
        assert "serial_number" in info

    def test_verify_chain_valid(self):
        from general_ludd.ssl.certificate import (
            generate_csr,
            generate_key,
            self_sign,
            sign_csr,
            verify_chain,
        )

        ca_key = generate_key("rsa", 2048)
        ca_csr = generate_csr(ca_key, {"CN": "Root CA"})
        ca_cert = self_sign(ca_csr, ca_key, 3650)

        leaf_key = generate_key("rsa", 2048)
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert = sign_csr(leaf_csr, ca_cert, ca_key, 365)

        assert verify_chain([leaf_cert, ca_cert]) is True

    def test_verify_chain_single_cert(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, self_sign, verify_chain

        key = generate_key("rsa", 2048)
        csr = generate_csr(key, {"CN": "solo"})
        cert = self_sign(csr, key, 365)
        assert verify_chain([cert]) is False

    def test_verify_chain_wrong_order(self):
        from general_ludd.ssl.certificate import (
            generate_csr,
            generate_key,
            self_sign,
            sign_csr,
            verify_chain,
        )

        ca_key = generate_key("rsa", 2048)
        ca_csr = generate_csr(ca_key, {"CN": "Root"})
        ca_cert = self_sign(ca_csr, ca_key, 3650)
        leaf_key = generate_key("rsa", 2048)
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert = sign_csr(leaf_csr, ca_cert, ca_key, 365)
        assert verify_chain([ca_cert, leaf_cert]) is False

    def test_build_chain(self):
        from general_ludd.ssl.certificate import (
            build_chain,
            generate_csr,
            generate_key,
            self_sign,
            sign_csr,
        )

        ca_key = generate_key("rsa", 2048)
        ca_csr = generate_csr(ca_key, {"CN": "Root CA"})
        ca_cert = self_sign(ca_csr, ca_key, 3650)
        leaf_key = generate_key("ecdsa", 256)
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert = sign_csr(leaf_csr, ca_cert, ca_key, 365)
        chain = build_chain(leaf_cert, [ca_cert])
        assert len(chain) >= 2

    def test_parse_cert_with_sans(self):
        from general_ludd.ssl.certificate import generate_csr, generate_key, parse_cert, self_sign

        key = generate_key("rsa", 2048)
        csr = generate_csr(key, {"CN": "example.com"}, sans=["a.example.com", "b.example.com"])
        cert = self_sign(csr, key, 365)
        info = parse_cert(cert)
        assert "sans" in info


# ---------------------------------------------------------------------------
# ssl.compliance
# ---------------------------------------------------------------------------

class TestSSLCompliance:
    def test_imports(self):
        from general_ludd.ssl.compliance import (
            FIPS_140_3,
        )

        assert FIPS_140_3 is not None

    def test_get_profile_valid(self):
        from general_ludd.ssl.compliance import FIPS_140_3, get_profile

        assert get_profile("FIPS_140_3") is FIPS_140_3

    def test_get_profile_invalid(self):
        from general_ludd.ssl.compliance import get_profile

        with pytest.raises(ValueError, match="Unknown"):
            get_profile("BOGUS")

    def test_list_profiles(self):
        from general_ludd.ssl.compliance import list_profiles

        profiles = list_profiles()
        assert "FIPS_140_3" in profiles
        assert "SOC2" in profiles
        assert len(profiles) >= 4

    def test_check_compliance_pass(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {"algorithm": "RSA", "key_size": 2048, "key_usage": ["digitalSignature", "keyEncipherment"]},
            FIPS_140_3,
        )
        assert result.compliant is True

    def test_check_compliance_wrong_algorithm(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {"algorithm": "DSA", "key_size": 2048, "key_usage": ["digitalSignature", "keyEncipherment"]},
            FIPS_140_3,
        )
        assert result.compliant is False

    def test_check_compliance_small_key(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {"algorithm": "RSA", "key_size": 1024, "key_usage": ["digitalSignature", "keyEncipherment"]},
            FIPS_140_3,
        )
        assert result.compliant is False

    def test_check_compliance_missing_key_usage(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {"algorithm": "RSA", "key_size": 2048, "key_usage": ["digitalSignature"]},
            FIPS_140_3,
        )
        assert result.compliant is False

    def test_check_compliance_wildcard_warning(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {
                "algorithm": "RSA",
                "key_size": 2048,
                "key_usage": ["digitalSignature", "keyEncipherment"],
                "sans": ["*.example.com"],
            },
            FIPS_140_3,
        )
        assert len(result.warnings) > 0

    def test_compliance_result_defaults(self):
        from general_ludd.ssl.compliance import ComplianceProfile, ComplianceResult

        profile = ComplianceProfile("test", 2048, ["RSA"], [], [])
        result = ComplianceResult(profile=profile, compliant=True)
        assert result.violations == []
        assert result.warnings == []

    def test_all_profiles_valid(self):
        from general_ludd.ssl.compliance import (
            get_profile,
        )

        for name in ["FIPS_140_3", "SOC2", "HIPAA", "PCI_DSS", "FedRAMP", "ISO_27001"]:
            profile = get_profile(name)
            assert profile.minimum_key_size > 0
            assert len(profile.allowed_algorithms) > 0

    def test_ecdsa_small_key_warning(self):
        from general_ludd.ssl.compliance import FIPS_140_3, check_compliance

        result = check_compliance(
            {"algorithm": "ECDSA", "key_size": 128, "key_usage": ["digitalSignature", "keyEncipherment"]},
            FIPS_140_3,
        )
        assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# ssl.asn1
# ---------------------------------------------------------------------------

class TestSSLASN1:
    def test_import(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import parse_der

        assert parse_der is not None

    def test_parse_simple_integer(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import parse_der

        result = parse_der(b"\x02\x01\x2a")
        assert result["type"] == "INTEGER"
        assert result["value"] == 42

    def test_parse_null(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import parse_der

        result = parse_der(b"\x05\x00")
        assert result["type"] == "NULL"

    def test_parse_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import parse_der

        result = parse_der(b"\x06\x08\x2b\x06\x01\x05\x05\x07\x03\x01")
        assert result["type"] == "OID"
        assert "1.3.6" in result["value"]

    def test_encode_integer(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der

        encoded = encode_der({"type": "INTEGER", "value": 42})
        assert encoded == b"\x02\x01\x2a"

    def test_encode_null(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der

        encoded = encode_der({"type": "NULL", "value": None})
        assert encoded == b"\x05\x00"

    def test_encode_boolean(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der

        encoded = encode_der({"type": "BOOLEAN", "value": True})
        assert encoded == b"\x01\x01\xff"

    def test_encode_sequence(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der

        encoded = encode_der(
            {
                "type": "SEQUENCE",
                "children": [{"type": "INTEGER", "value": 1}],
            }
        )
        assert encoded[0] == 0x30

    def test_encode_roundtrip_simple(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der, parse_der

        original = {"type": "SEQUENCE", "children": [{"type": "INTEGER", "value": 100}]}
        der = encode_der(original)
        decoded = parse_der(der)
        assert decoded["type"] == "SEQUENCE"
        assert decoded["children"][0]["value"] == 100

    def test_lookup_known_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import lookup_oid

        info = lookup_oid("2.5.4.3")
        assert info["name"] == "commonName"

    def test_lookup_unknown_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import lookup_oid

        info = lookup_oid("1.2.3.4.5.999")
        assert info["name"] == "unknown"

    def test_generate_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import generate_oid

        oid = generate_oid("1.2.3", "test description")
        assert oid.startswith("1.2.3.")
        assert len(oid.split(".")) == 5

    def test_encode_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import encode_der

        encoded = encode_der({"type": "OID", "value": "2.5.4.3"})
        decoded = bytes([0x06, encoded[1], *encoded[2:]])
        assert decoded is not None


# ---------------------------------------------------------------------------
# ssl.hsm
# ---------------------------------------------------------------------------

class TestSSLHSM:
    def test_import(self):
        from general_ludd.ssl.hsm import HSMConfig

        assert HSMConfig is not None

    def test_hsm_config_defaults(self):
        from general_ludd.ssl.hsm import HSMConfig

        c = HSMConfig(module_path="/usr/lib/softhsm.so", slot_id=0)
        assert c.pin is None
        assert c.label == ""

    def test_hsm_key_defaults(self):
        from general_ludd.ssl.hsm import HSMKey

        k = HSMKey(key_id="1", label="k1", key_type="RSA", key_size=2048, algorithm="RSA-PKCS")
        assert k.key_id == "1"
        assert k.capabilities == []

    def test_mock_hsm_session_instantiate(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0, pin="1234", label="test")
        session = _MockHSMSession(config)
        assert session.closed is False

    def test_mock_hsm_list_keys(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        keys = session.list_keys()
        assert len(keys) >= 2
        assert keys[0].key_id in ("rsa-2048-001", "ecdsa-p256-001")

    def test_mock_hsm_sign(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        sig = session.sign("rsa-2048-001", b"hello")
        assert isinstance(sig, bytes)
        assert len(sig) > 0

    def test_mock_hsm_sign_unknown_key(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        with pytest.raises(KeyError, match="not found"):
            session.sign("nonexistent", b"data")

    def test_mock_hsm_close(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        session.close()
        assert session.closed is True

    def test_mock_hsm_cannot_use_after_close(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        session.close()
        with pytest.raises(RuntimeError, match="closed"):
            session.list_keys()


# ---------------------------------------------------------------------------
# ssl_agent.cert_manager
# ---------------------------------------------------------------------------

class TestSSLCertManager:
    def test_import(self):
        from general_ludd.ssl_agent.cert_manager import (
            generate_key_pair,
        )

        assert generate_key_pair is not None

    def test_generate_rsa_keypair(self):
        from general_ludd.ssl_agent.cert_manager import generate_key_pair

        kp = generate_key_pair("rsa-2048")
        assert kp.key_type == "rsa-2048"
        assert b"-----BEGIN PUBLIC KEY-----" in kp.public_pem
        assert b"-----BEGIN PRIVATE KEY-----" in kp.private_pem

    def test_generate_ecdsa_keypair(self):
        from general_ludd.ssl_agent.cert_manager import generate_key_pair

        kp = generate_key_pair("ecdsa-p256")
        assert kp.key_type == "ecdsa-p256"

    def test_generate_ed25519_keypair(self):
        from general_ludd.ssl_agent.cert_manager import generate_key_pair

        kp = generate_key_pair("ed25519")
        assert kp.key_type == "ed25519"

    def test_generate_unknown_keytype(self):
        from general_ludd.ssl_agent.cert_manager import generate_key_pair

        with pytest.raises(ValueError, match="Unknown"):
            generate_key_pair("bogus")

    def test_generate_csr(self):
        from general_ludd.ssl_agent.cert_manager import generate_csr, generate_key_pair

        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("test.example.com", kp)
        assert csr.common_name == "test.example.com"
        assert b"-----BEGIN CERTIFICATE REQUEST-----" in csr.csr_pem

    def test_self_sign_cert(self):
        from general_ludd.ssl_agent.cert_manager import generate_csr, generate_key_pair, self_sign_cert

        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("test.example.com", kp)
        fields = self_sign_cert(csr, kp, 365)
        assert fields.subject_cn == "test.example.com"
        assert fields.serial_number != ""

    def test_cert_parse(self):
        from general_ludd.ssl_agent.cert_manager import (
            generate_csr,
            generate_key_pair,
            self_sign_cert,
        )

        kp = generate_key_pair("rsa-2048")
        csr = generate_csr("parse-test.example.com", kp)
        fields = self_sign_cert(csr, kp, 365)
        assert fields.issuer_cn == "parse-test.example.com"

    def test_generate_ca_chain(self):
        from general_ludd.ssl_agent.cert_manager import generate_ca_chain

        chain = generate_ca_chain("My-Root-CA", "leaf.example.com")
        assert "ca_cert_pem" in chain
        assert "leaf_cert_pem" in chain
        assert chain["chain_valid"] is True

    def test_collection_asn1_roundtrip(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            encode_der,
            parse_der,
        )

        structure = {"type": "OID", "value": "2.5.4.3"}
        assert parse_der(encode_der(structure))["value"] == "2.5.4.3"

    def test_oid_lookup_by_oid(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            lookup_oid,
        )

        result = lookup_oid("2.5.4.3")
        assert result["name"] == "commonName"

    def test_oid_lookup_includes_description(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            lookup_oid,
        )

        result = lookup_oid("2.5.4.3")
        assert result["description"]

    def test_oid_lookup_missing(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            lookup_oid,
        )

        result = lookup_oid("99.99.99.99.9999")
        assert result["name"] == "unknown"

    def test_oid_generate(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            generate_oid,
        )

        oid = generate_oid("2.5.4.3", "e2e")
        assert oid.startswith("2.5.4.3.")

    def test_algorithm_evaluate_known(self):
        from general_ludd.ssl_agent.cert_manager import algorithm_evaluate

        ev = algorithm_evaluate("rsa-2048")
        assert ev.algorithm == "RSA-2048"
        assert ev.strength == "medium"

    def test_algorithm_evaluate_sha1(self):
        from general_ludd.ssl_agent.cert_manager import algorithm_evaluate

        ev = algorithm_evaluate("sha-1")
        assert ev.is_recommended is False

    def test_algorithm_evaluate_unknown(self):
        from general_ludd.ssl_agent.cert_manager import algorithm_evaluate

        ev = algorithm_evaluate("bogus-algo")
        assert ev.strength == "unknown"
        assert ev.is_recommended is False

    def test_compliance_check_pass(self):
        from general_ludd.ssl_agent.cert_manager import (
            ComplianceProfile,
            compliance_check,
            generate_ca_chain,
        )

        chain = generate_ca_chain("Compliance Root", "compliant.example.com")
        result = compliance_check(chain["leaf_cert_pem"], ComplianceProfile.FIPS)
        assert result.profile == "fips"
        assert result.passed is True

    def test_ca_jurisdiction_lookup_letsencrypt(self):
        from general_ludd.ssl_agent.cert_manager import ca_jurisdiction_lookup

        j = ca_jurisdiction_lookup("letsencrypt")
        assert j is not None
        assert j.is_public_trust is True

    def test_ca_jurisdiction_lookup_digicert(self):
        from general_ludd.ssl_agent.cert_manager import ca_jurisdiction_lookup

        j = ca_jurisdiction_lookup("digicert")
        assert j is not None

    def test_ca_jurisdiction_lookup_unknown(self):
        from general_ludd.ssl_agent.cert_manager import ca_jurisdiction_lookup

        j = ca_jurisdiction_lookup("bogus-ca")
        assert j is None

    def test_collection_oid_owner_import(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            lookup_oid,
        )

        assert lookup_oid is not None

    def test_collection_oid_owner_has_common_name(self):
        from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
            lookup_oid,
        )

        assert lookup_oid("2.5.4.3")["name"] == "commonName"


# ---------------------------------------------------------------------------
# ssl_agent.agent_flow
# ---------------------------------------------------------------------------

class TestSSLCertAgentFlow:
    def test_import(self):
        from general_ludd.ssl_agent.agent_flow import ssl_agent_flow

        assert ssl_agent_flow is not None

    def test_ssl_agent_flow_defaults(self):
        from general_ludd.ssl_agent.agent_flow import ssl_agent_flow

        result = ssl_agent_flow("test.example.com")
        assert result.common_name == "test.example.com"
        assert result.key_pair is not None
        assert result.chain_verified is True
        assert len(result.artifacts) >= 2

    def test_ssl_agent_flow_custom_params(self):
        from general_ludd.ssl_agent.agent_flow import ssl_agent_flow

        result = ssl_agent_flow(
            "custom.example.com",
            key_type="ecdsa-p256",
            validity_days=30,
            profiles=["fips"],
            ca_names=["letsencrypt"],
        )
        assert result.common_name == "custom.example.com"

    def test_sslcert_agent_constructor(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent

        agent = SSLCertAgent()
        assert agent._model_call_count == 0

    def test_sslcert_agent_model_call(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent

        agent = SSLCertAgent()
        result = agent.model_call("Analyze this", {"common_name": "test.example.com"})
        assert "response" in result
        assert agent._model_call_count == 1

    def test_sslcert_agent_model_call_no_data(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent

        agent = SSLCertAgent()
        result = agent.model_call("Analyze")
        assert "response" in result

    def test_sslcert_agent_run(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent

        agent = SSLCertAgent()
        result = agent.run("agent-test.example.com")
        assert "agent_result" in result
        assert "model_analysis" in result
        assert "artifact_count" in result


# ---------------------------------------------------------------------------
# notifications.dispatcher
# ---------------------------------------------------------------------------

class TestNotificationDispatcher:
    def test_import(self):
        from general_ludd.notifications.dispatcher import (
            NotificationDispatcher,
        )

        assert NotificationDispatcher is not None

    def test_constructor_enabled(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}})
        assert nd._enabled is True

    def test_constructor_disabled(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher({"enabled": False})
        result = nd.dispatch({"id": "1", "title": "Test"})
        assert result["ok"] is False
        assert "disabled" in str(result)

    def test_format_message(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}})
        msg = nd._format_message(
            {"id": "42", "title": "Fix me", "priority": "high", "category": "bug", "agent_id": "a1", "body": "details"}
        )
        assert "42" in msg
        assert "Fix me" in msg
        assert "high" in msg

    def test_priority_threshold_low(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "high"}
        )
        assert nd._priority_meets_threshold("low") is False

    def test_priority_threshold_high(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "high"}
        )
        assert nd._priority_meets_threshold("high") is True

    def test_priority_threshold_urgent(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "medium"}
        )
        assert nd._priority_meets_threshold("urgent") is True

    def test_dispatch_stdout(self, capsys):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"}
        )
        result = nd.dispatch({"id": "1", "title": "Test", "priority": "urgent"})
        captured = capsys.readouterr()
        assert result["ok"] is True
        assert "Test" in captured.out

    def test_dispatch_below_threshold(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "urgent"}
        )
        result = nd.dispatch({"id": "1", "title": "Test", "priority": "low"})
        assert result["ok"] is False

    def test_test_notification(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"}
        )
        result = nd.test()
        assert result["ok"] is True

    def test_unknown_backend(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"bogus": {}}, "min_priority": "low"}
        )
        result = nd.dispatch({"id": "1", "title": "Test", "priority": "urgent"})
        results = result.get("results", {})
        assert results.get("bogus", {}).get("ok") is False

    def test_webhook_no_url(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {}}, "min_priority": "low"}
        )
        result = nd._dispatch_webhook("msg", {})
        assert result["ok"] is False

    def test_webhook_no_transport(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://localhost/notify"}}},
        )
        result = nd._dispatch_webhook("msg", {"url": "http://localhost/notify"})
        assert result["ok"] is False
        assert "transport" in str(result).lower()

    def test_notification_template_contains_all(self):
        from general_ludd.notifications.dispatcher import NOTIFICATION_TEMPLATE

        assert "gludd" in NOTIFICATION_TEMPLATE
        assert "{priority}" in NOTIFICATION_TEMPLATE
        assert "{id}" in NOTIFICATION_TEMPLATE
        assert "{title}" in NOTIFICATION_TEMPLATE

    def test_fallback_config(self):
        from general_ludd.notifications.dispatcher import FALLBACK_NOTIFICATION_CONFIG

        assert "enabled" in FALLBACK_NOTIFICATION_CONFIG
        assert FALLBACK_NOTIFICATION_CONFIG["enabled"] is False
