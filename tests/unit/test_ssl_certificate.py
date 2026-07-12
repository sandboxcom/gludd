from __future__ import annotations

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from general_ludd.ssl.certificate import (
    build_chain,
    generate_csr,
    generate_key,
    parse_cert,
    self_sign,
    sign_csr,
    verify_chain,
)


class TestGenerateKey:
    def test_generates_rsa_key_default_size(self) -> None:
        key_pem = generate_key("rsa")
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert isinstance(key, rsa.RSAPrivateKey)
        assert key.key_size == 2048

    def test_generates_rsa_key_custom_size(self) -> None:
        key_pem = generate_key("rsa", key_size=4096)
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert isinstance(key, rsa.RSAPrivateKey)
        assert key.key_size == 4096

    def test_generates_ecdsa_key_default_curve(self) -> None:
        key_pem = generate_key("ecdsa")
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert hasattr(key, "curve")
        assert isinstance(key.curve.__class__.__name__, str)
        assert "SECP256R1" in str(type(key.curve))

    def test_generates_ecdsa_key_p256(self) -> None:
        key_pem = generate_key("ecdsa", key_size=256)
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert "256" in str(key.key_size)

    def test_generates_ecdsa_key_p384(self) -> None:
        key_pem = generate_key("ecdsa", key_size=384)
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert key.key_size == 384

    def test_generates_ecdsa_key_p521(self) -> None:
        key_pem = generate_key("ecdsa", key_size=521)
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert key.key_size == 521

    def test_generates_ed25519_key(self) -> None:
        key_pem = generate_key("ed25519")
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert isinstance(key, ed25519.Ed25519PrivateKey)

    def test_unknown_key_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown key_type"):
            generate_key("dsa")

    def test_unsupported_ecdsa_size_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported ECDSA"):
            generate_key("ecdsa", key_size=1024)


class TestGenerateCsr:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def test_generates_csr_basic(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "example.com"})
        csr = x509.load_pem_x509_csr(csr_pem)
        assert csr.is_signature_valid
        cn = csr.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        assert cn[0].value == "example.com"

    def test_generates_csr_with_multiple_subject_fields(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(
            key_pem,
            {"CN": "test.example.com", "O": "TestOrg", "C": "US"},
        )
        csr = x509.load_pem_x509_csr(csr_pem)
        o = csr.subject.get_attributes_for_oid(x509.oid.NameOID.ORGANIZATION_NAME)
        assert o[0].value == "TestOrg"
        c = csr.subject.get_attributes_for_oid(x509.oid.NameOID.COUNTRY_NAME)
        assert c[0].value == "US"

    def test_generates_csr_with_sans(self) -> None:
        key_pem = self._rsa_key()
        scn_pem = generate_csr(key_pem, {"CN": "primary.com"}, sans=["alt1.com", "alt2.com"])
        csr = x509.load_pem_x509_csr(scn_pem)
        san_ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = [n.value for n in san_ext.value if isinstance(n, x509.DNSName)]
        assert "alt1.com" in names
        assert "alt2.com" in names

    def test_generates_csr_with_key_usage(self) -> None:
        key_pem = self._rsa_key()
        scn_pem = generate_csr(key_pem, {"CN": "example.com"}, key_usage=["digital_signature", "key_encipherment"])
        csr = x509.load_pem_x509_csr(scn_pem)
        ku_ext = csr.extensions.get_extension_for_class(x509.KeyUsage)
        assert ku_ext.value.digital_signature is True
        assert ku_ext.value.key_encipherment is True
        assert ku_ext.value.key_cert_sign is False

    def test_generates_csr_with_extended_key_usage(self) -> None:
        key_pem = self._rsa_key()
        scn_pem = generate_csr(key_pem, {"CN": "example.com"}, extended_key_usage=["server_auth", "client_auth"])
        csr = x509.load_pem_x509_csr(scn_pem)
        eku_ext = csr.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        oids = [oi.dotted_string for oi in eku_ext.value]
        assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH.dotted_string in oids
        assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH.dotted_string in oids

    def test_unknown_subject_key_raises(self) -> None:
        key_pem = self._rsa_key()
        with pytest.raises(ValueError, match="Unknown subject key"):
            generate_csr(key_pem, {"badField": "value"})


class TestSelfSign:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def test_self_sign_produces_valid_cert(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "self.example.com"})
        cert_pem = self_sign(csr_pem, key_pem)
        cert = x509.load_pem_x509_certificate(cert_pem)
        assert cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value == "self.example.com"
        assert cert.issuer == cert.subject

    def test_self_sign_with_validity(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "short.example.com"})
        cert_pem = self_sign(csr_pem, key_pem, validity_days=30)
        cert = x509.load_pem_x509_certificate(cert_pem)
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert 29 <= delta.days <= 31

    def test_self_sign_preserves_sans(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "multi.example.com"}, sans=["www.example.com"])
        cert_pem = self_sign(csr_pem, key_pem)
        cert = x509.load_pem_x509_certificate(cert_pem)
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert any(isinstance(n, x509.DNSName) and n.value == "www.example.com" for n in san_ext.value)


class TestSignCsr:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def _ca(self):
        key_pem = generate_key("rsa", key_size=2048)
        csr_pem = generate_csr(key_pem, {"CN": "Test CA", "O": "CA Inc"}, key_usage=["key_cert_sign", "crl_sign"])
        cert_pem = self_sign(csr_pem, key_pem)
        return cert_pem, key_pem

    def test_sign_csr_with_ca(self) -> None:
        ca_cert_pem, ca_key_pem = self._ca()
        leaf_key = self._rsa_key()
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf.example.com"})
        leaf_cert_pem = sign_csr(leaf_csr, ca_cert_pem, ca_key_pem)
        leaf_cert = x509.load_pem_x509_certificate(leaf_cert_pem)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
        assert leaf_cert.issuer == ca_cert.subject
        cn = leaf_cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        assert cn[0].value == "leaf.example.com"


class TestParseCert:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def test_parse_returns_expected_fields(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(
            key_pem,
            {"CN": "parsed.example.com", "O": "ParseCo", "C": "US"},
            sans=["alt.parse.com"],
            key_usage=["digital_signature", "key_encipherment"],
            extended_key_usage=["server_auth"],
        )
        cert_pem = self_sign(csr_pem, key_pem)
        result = parse_cert(cert_pem)
        assert "serial_number" in result
        assert "subject" in result
        assert "issuer" in result
        assert "not_valid_before" in result
        assert "not_valid_after" in result
        assert "sans" in result
        assert "key_usage" in result
        assert "extended_key_usage" in result
        assert "commonName" in result["subject"]
        assert "parsed.example.com" in result["subject"]["commonName"]
        assert "alt.parse.com" in result["sans"]
        assert "digital_signature" in result["key_usage"]
        assert "server_auth" in result["extended_key_usage"]
        assert isinstance(result["serial_number"], int)
        assert datetime.datetime.fromisoformat(result["not_valid_before"])


class TestVerifyChain:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def _ca(self):
        key_pem = generate_key("rsa", key_size=2048)
        csr_pem = generate_csr(key_pem, {"CN": "Root CA"}, key_usage=["key_cert_sign", "crl_sign"])
        cert_pem = self_sign(csr_pem, key_pem)
        return cert_pem, key_pem

    def test_valid_two_cert_chain(self) -> None:
        ca_cert_pem, ca_key_pem = self._ca()
        leaf_key = self._rsa_key()
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert_pem = sign_csr(leaf_csr, ca_cert_pem, ca_key_pem)
        assert verify_chain([leaf_cert_pem, ca_cert_pem]) is True

    def test_wrong_order_fails(self) -> None:
        ca_cert_pem, ca_key_pem = self._ca()
        leaf_key = self._rsa_key()
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert_pem = sign_csr(leaf_csr, ca_cert_pem, ca_key_pem)
        assert verify_chain([ca_cert_pem, leaf_cert_pem]) is False

    def test_unrelated_certs_fail(self) -> None:
        ca_cert_pem, _ = self._ca()
        key2 = self._rsa_key()
        csr2 = generate_csr(key2, {"CN": "other"})
        cert2 = self_sign(csr2, key2)
        assert verify_chain([cert2, ca_cert_pem]) is False

    def test_single_cert_fails(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "alone"})
        cert_pem = self_sign(csr_pem, key_pem)
        assert verify_chain([cert_pem]) is False

    def test_empty_chain_fails(self) -> None:
        assert verify_chain([]) is False


class TestBuildChain:
    def _rsa_key(self) -> bytes:
        return generate_key("rsa", key_size=2048)

    def _ca(self):
        key_pem = generate_key("rsa", key_size=2048)
        csr_pem = generate_csr(key_pem, {"CN": "Root CA"}, key_usage=["key_cert_sign", "crl_sign"])
        cert_pem = self_sign(csr_pem, key_pem)
        return cert_pem, key_pem

    def test_builds_two_level_chain(self) -> None:
        ca_cert_pem, ca_key_pem = self._ca()
        leaf_key = self._rsa_key()
        leaf_csr = generate_csr(leaf_key, {"CN": "leaf"})
        leaf_cert_pem = sign_csr(leaf_csr, ca_cert_pem, ca_key_pem)
        chain = build_chain(leaf_cert_pem, [ca_cert_pem])
        assert len(chain) == 2
        leaf = x509.load_pem_x509_certificate(chain[0])
        assert leaf.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value == "leaf"
        ca = x509.load_pem_x509_certificate(chain[1])
        assert ca.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value == "Root CA"

    def test_builds_three_level_chain(self) -> None:
        root_cert_pem, root_key_pem = self._ca()
        int_key = self._rsa_key()
        int_csr = generate_csr(int_key, {"CN": "Intermediate CA"}, key_usage=["key_cert_sign", "crl_sign"])
        int_cert_pem = sign_csr(int_csr, root_cert_pem, root_key_pem)
        leaf_key = self._rsa_key()
        leaf_csr = generate_csr(leaf_key, {"CN": "deep"})
        leaf_cert_pem = sign_csr(leaf_csr, int_cert_pem, int_key)
        chain = build_chain(leaf_cert_pem, [int_cert_pem, root_cert_pem])
        assert len(chain) == 3
        names = [
            x509.load_pem_x509_certificate(c)
            .subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0]
            .value
            for c in chain
        ]
        assert names == ["deep", "Intermediate CA", "Root CA"]

    def test_single_cert_returns_one(self) -> None:
        key_pem = self._rsa_key()
        csr_pem = generate_csr(key_pem, {"CN": "alone"})
        cert_pem = self_sign(csr_pem, key_pem)
        chain = build_chain(cert_pem, [])
        assert len(chain) == 1
