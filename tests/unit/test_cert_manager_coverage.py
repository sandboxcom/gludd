"""Typed algorithm and representation coverage for certificate management."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

from general_ludd.ssl_agent import cert_manager
from general_ludd.ssl_agent.cert_manager import (
    CertificateFields,
    ComplianceProfile,
    _ProxyPrivateKey,
    _verify_chain,
    cert_parse,
    compliance_check,
)


class _KeyDouble:
    """Record proxy key calls and return deterministic values."""

    key_size = 4096

    def public_bytes(self, encoding: object, fmt: object) -> bytes:
        """Return a public-key marker."""
        assert encoding is serialization.Encoding.PEM
        assert fmt is serialization.PublicFormat.SubjectPublicKeyInfo
        return b"public"

    def private_bytes(self, encoding: object, fmt: object, encryption: object) -> bytes:
        """Return a private-key marker."""
        assert encoding is serialization.Encoding.PEM
        assert fmt is serialization.PrivateFormat.PKCS8
        assert isinstance(encryption, serialization.NoEncryption)
        return b"private"

    def public_key(self) -> _KeyDouble:
        """Return the public-key test double."""
        return self

    def sign(self, data: bytes, pad: object, algorithm: object) -> bytes:
        """Return a signature marker."""
        assert (data, pad, algorithm) == (b"data", "pad", "algorithm")
        return b"signature"


def _signed_chain(ca_key: Any) -> tuple[x509.Certificate, x509.Certificate]:
    """Build a CA and RSA leaf signed by ``ca_key``."""
    now = datetime.datetime.now(datetime.UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Coverage CA")])
    algorithm = None if isinstance(ca_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, algorithm)
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leaf.example")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("leaf.example")]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, algorithm)
    )
    return ca_cert, leaf_cert


def test_private_and_public_key_proxies_delegate_operations() -> None:
    """Preserve serialization, signing, public-key, and key-size behavior."""
    key = _KeyDouble()
    proxy = _ProxyPrivateKey(key)
    assert proxy.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8) == b"private"
    assert proxy.public_key().public_bytes() == b"public"
    assert proxy.sign(b"data", "pad", "algorithm") == b"signature"
    assert proxy.key_size == 4096


@pytest.mark.parametrize(
    "ca_key",
    [ec.generate_private_key(ec.SECP256R1()), ed25519.Ed25519PrivateKey.generate()],
)
def test_verify_chain_supports_ec_and_ed25519(ca_key: Any) -> None:
    """Verify leaf signatures from each supported non-RSA CA type."""
    ca_cert, leaf_cert = _signed_chain(ca_key)
    assert _verify_chain(ca_cert, leaf_cert) is True


def test_verify_chain_rejects_unsupported_and_invalid_signatures() -> None:
    """Fail closed for unsupported public keys and invalid signatures."""
    unsupported = cast(x509.Certificate, SimpleNamespace(public_key=lambda: object()))
    assert _verify_chain(unsupported, cast(x509.Certificate, object())) is False

    ca_cert, _leaf = _signed_chain(ec.generate_private_key(ec.SECP256R1()))
    _other_ca, other_leaf = _signed_chain(ec.generate_private_key(ec.SECP256R1()))
    assert _verify_chain(ca_cert, other_leaf) is False


def test_parse_extensions() -> None:
    """Parse SAN and key-usage fields from a certificate object."""
    _ca, leaf = _signed_chain(ec.generate_private_key(ec.SECP256R1()))
    fields = cert_parse(leaf)
    assert fields.sans == ["leaf.example"]
    assert fields.key_usage == ["digital_signature", "key_encipherment", "key_cert_sign"]


def test_compliance_summary_key_types_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate proxy summaries for RSA, elliptic, unknown, and SHA-1 cases."""
    rsa_fields = CertificateFields(public_key_algorithm="RSAPublicKey", signature_algorithm="sha1WithRSA")
    rsa_result = compliance_check(rsa_fields, ComplianceProfile.FIPS)
    assert rsa_result.passed is False
    assert any("SHA-1" in failure for failure in rsa_result.failures)

    elliptic = CertificateFields(public_key_algorithm="EllipticCurvePublicKey", signature_algorithm="ecdsa")
    assert compliance_check(elliptic, ComplianceProfile.FIPS).checks[0]["value"] == 256

    unknown = CertificateFields(public_key_algorithm="Unknown", signature_algorithm="unknown")
    assert any("Key size 0" in failure for failure in compliance_check(unknown, ComplianceProfile.FIPS).failures)

    monkeypatch.setitem(cert_manager.COMPLIANCE_PROFILES["fips"], "recommend_sha256_plus", False)
    result = compliance_check(rsa_fields, ComplianceProfile.FIPS)
    assert all(check["check"] != "recommend_sha256_plus" for check in result.checks)


@pytest.mark.parametrize(
    "public_key",
    [ec.generate_private_key(ec.SECP256R1()).public_key(), object()],
)
def test_compliance_certificate_public_key_fallbacks(public_key: object) -> None:
    """Size elliptic keys directly and other supported key families conservatively."""
    certificate = cast(
        x509.Certificate,
        SimpleNamespace(
            public_key=lambda: public_key,
            signature_algorithm_oid=SimpleNamespace(_name="sha256"),
        ),
    )

    result = compliance_check(certificate, ComplianceProfile.FIPS)

    assert result.checks[0]["value"] == 256
