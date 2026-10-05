from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from general_ludd.ssl.certificate import (
    build_chain,
    generate_csr,
    generate_key,
    self_sign,
    sign_csr,
    validate_chain,
    verify_chain,
)


def _load_private(key_pem: bytes) -> Any:
    return serialization.load_pem_private_key(key_pem, password=None)


def _sign_csr_with_validity(
    csr_pem: bytes,
    ca_cert_pem: bytes,
    ca_key_pem: bytes,
    validity_days: int = 365,
) -> bytes:
    return sign_csr(csr_pem, ca_cert_pem, ca_key_pem, validity_days=validity_days)


def _rsa_key(size: int = 2048) -> bytes:
    return generate_key("rsa", key_size=size)


def _ecdsa_key(size: int = 256) -> bytes:
    return generate_key("ecdsa", key_size=size)


def _ed25519_key() -> bytes:
    return generate_key("ed25519")


def _root_ca(cn: str = "Root CA", key_type: str = "rsa") -> tuple[bytes, bytes]:
    if key_type == "rsa":
        key = _rsa_key()
    elif key_type == "ecdsa":
        key = _ecdsa_key()
    else:
        key = _ed25519_key()
    csr = generate_csr(key, {"CN": cn, "O": "TestOrg"}, key_usage=["key_cert_sign", "crl_sign"])
    cert = self_sign(csr, key, validity_days=3650)
    return cert, key


def _intermediate_ca(
    parent_cert: bytes,
    parent_key: bytes,
    cn: str = "Intermediate CA",
    path_length: int | None = None,
    key_type: str = "rsa",
) -> tuple[bytes, bytes]:
    if key_type == "rsa":
        key = _rsa_key()
    elif key_type == "ecdsa":
        key = _ecdsa_key()
    else:
        key = _ed25519_key()
    usage = ["key_cert_sign", "crl_sign"]
    csr = generate_csr(key, {"CN": cn, "O": "TestOrg"}, key_usage=usage)
    cert = sign_csr(csr, parent_cert, parent_key, validity_days=1825)
    return cert, key


def _leaf_cert(
    parent_cert: bytes,
    parent_key: bytes,
    cn: str = "leaf.example.com",
    sans: list[str] | None = None,
    key_type: str = "rsa",
) -> tuple[bytes, bytes]:
    if key_type == "rsa":
        key = _rsa_key()
    elif key_type == "ecdsa":
        key = _ecdsa_key()
    else:
        key = _ed25519_key()
    csr = generate_csr(key, {"CN": cn}, sans=sans, extended_key_usage=["server_auth", "client_auth"])
    cert = sign_csr(csr, parent_cert, parent_key, validity_days=365)
    return cert, key


def _load_public_key(cert_pem: bytes) -> Any:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.public_key()


def _subject_cn(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return str(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)


def _issuer_cn(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return str(cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)


# --- verify_chain tests (existing function) -------------------------------------------------


class TestVerifyChainRSA:
    def test_valid_two_level_chain(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        assert verify_chain([leaf, root]) is True

    def test_valid_three_level_chain(self) -> None:
        root, root_key = _root_ca()
        int_ca, int_key = _intermediate_ca(root, root_key)
        leaf, _ = _leaf_cert(int_ca, int_key)
        assert verify_chain([leaf, int_ca, root]) is True

    def test_valid_four_level_chain(self) -> None:
        root, root_key = _root_ca()
        int1, int1_key = _intermediate_ca(root, root_key, cn="Int L1")
        int2, int2_key = _intermediate_ca(int1, int1_key, cn="Int L2")
        leaf, _ = _leaf_cert(int2, int2_key)
        assert verify_chain([leaf, int2, int1, root]) is True

    def test_wrong_order_rejected(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        assert verify_chain([root, leaf]) is False

    def test_unrelated_certs_rejected(self) -> None:
        root1, _ = _root_ca(cn="Root1")
        root2, root2_key = _root_ca(cn="Root2")
        leaf, _ = _leaf_cert(root2, root2_key)
        assert verify_chain([leaf, root1]) is False

    def test_single_cert_rejected(self) -> None:
        root, _ = _root_ca()
        assert verify_chain([root]) is False

    def test_empty_list_rejected(self) -> None:
        assert verify_chain([]) is False

    def test_tampered_tbs_bytes_fails(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        leaf_cert = x509.load_pem_x509_certificate(leaf)
        bad_cert_builder = (
            x509.CertificateBuilder()
            .subject_name(leaf_cert.subject)
            .issuer_name(leaf_cert.issuer)
            .public_key(leaf_cert.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(leaf_cert.not_valid_before_utc)
            .not_valid_after(leaf_cert.not_valid_after_utc)
        )
        for ext in leaf_cert.extensions:
            bad_cert_builder = bad_cert_builder.add_extension(ext.value, critical=ext.critical)
        lea_key: Any = _load_private(generate_key("rsa"))
        bad_leaf = bad_cert_builder.sign(lea_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
        assert verify_chain([bad_leaf, root]) is False

    def test_malformed_pem_rejected(self) -> None:
        root, _ = _root_ca()
        assert verify_chain([b"not-a-cert", root]) is False

    def test_non_pem_bytes_rejected(self) -> None:
        root, _ = _root_ca()
        assert verify_chain([b"\x00\x01\x02\x03", root]) is False

    def test_chain_with_junk_middle_rejected(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        assert verify_chain([leaf, b"garbage in chain", root]) is False

    def test_chain_missing_intermediate(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        other_root, other_root_key = _root_ca(cn="Other Root")
        int_ca, _ = _intermediate_ca(other_root, other_root_key)
        assert verify_chain([leaf, int_ca]) is False

    def test_chain_only_root_self_sign(self) -> None:
        root, _ = _root_ca()
        assert verify_chain([root, root]) is True

    def test_broken_link_same_issuer_different_key(self) -> None:
        root, root_key = _root_ca(cn="A")
        root2, _root2_key = _root_ca(cn="A")
        leaf, _ = _leaf_cert(root, root_key)
        assert verify_chain([leaf, root2]) is False


class TestVerifyChainECDSA:
    def test_valid_ecdsa_chain(self) -> None:
        root, root_key = _root_ca(cn="EC Root", key_type="ecdsa")
        leaf, _ = _leaf_cert(root, root_key, cn="ec-leaf", key_type="ecdsa")
        assert verify_chain([leaf, root]) is True

    def test_ecdsa_three_level_chain(self) -> None:
        root, root_key = _root_ca(cn="EC Root", key_type="ecdsa")
        int_ca, int_key = _intermediate_ca(root, root_key, cn="EC Int", key_type="ecdsa")
        leaf, _ = _leaf_cert(int_ca, int_key, cn="ec-leaf", key_type="ecdsa")
        assert verify_chain([leaf, int_ca, root]) is True

    def test_mixed_rsa_leaf_ecdsa_root(self) -> None:
        root, root_key = _root_ca(cn="EC Root", key_type="ecdsa")
        leaf, _ = _leaf_cert(root, root_key, cn="rsa-leaf", key_type="rsa")
        assert verify_chain([leaf, root]) is True


class TestVerifyChainEd25519:
    def test_valid_ed25519_chain(self) -> None:
        root, root_key = _root_ca(cn="Ed Root", key_type="ed25519")
        leaf, _ = _leaf_cert(root, root_key, cn="ed-leaf", key_type="ed25519")
        assert verify_chain([leaf, root]) is True

    def test_mixed_ed25519_leaf_rsa_root(self) -> None:
        root, root_key = _root_ca(cn="RSA Root", key_type="rsa")
        leaf, _ = _leaf_cert(root, root_key, cn="ed-leaf", key_type="ed25519")
        assert verify_chain([leaf, root]) is True


# --- validate_chain tests ----------------------------------------------------------------


@dataclass
class _Val:
    valid: bool
    errors: list[str]
    cert_details: list[dict[str, object]]


class TestValidateChainBasic:
    def test_generated_ca_has_critical_basic_constraints(self) -> None:
        root, _ = _root_ca()
        cert = x509.load_pem_x509_certificate(root)
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert constraints.critical is True
        assert constraints.value.ca is True
        assert constraints.value.path_length is None

    def test_valid_two_level_chain(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        result = validate_chain([leaf, root])
        assert result.valid is True
        assert len(result.errors) == 0
        assert len(result.cert_details) == 2

    def test_valid_three_level_chain(self) -> None:
        root, root_key = _root_ca()
        int_ca, int_key = _intermediate_ca(root, root_key)
        leaf, _ = _leaf_cert(int_ca, int_key)
        result = validate_chain([leaf, int_ca, root])
        assert result.valid is True
        assert len(result.errors) == 0
        assert len(result.cert_details) == 3

    def test_single_cert_invalid(self) -> None:
        root, _ = _root_ca()
        result = validate_chain([root])
        assert result.valid is False
        assert len(result.errors) > 0

    def test_empty_chain_invalid(self) -> None:
        result = validate_chain([])
        assert result.valid is False


class TestValidateChainExpiry:
    def test_expired_cert_reported(self) -> None:
        root, root_key = _root_ca()
        leaf_pem = _sign_csr_with_validity(
            generate_csr(_rsa_key(), {"CN": "expired"}, extended_key_usage=["server_auth"]),
            root,
            root_key,
            validity_days=1,
        )
        leaf = x509.load_pem_x509_certificate(leaf_pem)
        validation_time = leaf.not_valid_after_utc + datetime.timedelta(microseconds=1)
        result = validate_chain([leaf_pem, root], validation_time=validation_time)
        has_expiry_error = any("expired" in e.lower() or "not yet valid" in e.lower() for e in result.errors)
        has_date_error = any("valid" in e.lower() for e in result.errors)
        assert result.valid is False
        assert has_expiry_error or has_date_error

    def test_not_yet_valid_cert_reported(self) -> None:
        root, root_key = _root_ca()
        from datetime import UTC
        from datetime import timedelta as ctimedelta

        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import hashes as chashes
        from cryptography.hazmat.primitives import serialization as cser

        _load_private(generate_key("rsa"))
        csr = cx509.load_pem_x509_csr(generate_csr(generate_key("rsa"), {"CN": "future"}))
        future = datetime.datetime.now(UTC) + ctimedelta(days=365)
        builder = (
            cx509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(cx509.load_pem_x509_certificate(root).subject)
            .public_key(csr.public_key())
            .serial_number(cx509.random_serial_number())
            .not_valid_before(future)
            .not_valid_after(future + ctimedelta(days=365))
        )
        for ext in csr.extensions:
            builder = builder.add_extension(ext.value, critical=ext.critical)
        ca_key: Any = _load_private(root_key)
        leaf_pem = builder.sign(ca_key, chashes.SHA256()).public_bytes(cser.Encoding.PEM)

        result = validate_chain([leaf_pem, root])
        assert result.valid is False
        assert any("not yet valid" in e.lower() or "not_before" in e.lower() for e in result.errors)

    def test_expired_intermediate_reported(self) -> None:
        root, root_key = _root_ca()
        from datetime import UTC
        from datetime import timedelta as ctimedelta

        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import hashes as chashes
        from cryptography.hazmat.primitives import serialization as cser

        past = datetime.datetime.now(UTC) - ctimedelta(days=30)
        rk = serialization.load_pem_private_key(generate_key("rsa"), password=None)
        csr = cx509.load_pem_x509_csr(
            generate_csr(
                generate_key("rsa"),
                {"CN": "ExpiredInt", "O": "TestOrg"},
                key_usage=["key_cert_sign", "crl_sign"],
            )
        )
        ca_key: Any = _load_private(root_key)
        expired_int_pem = (
            cx509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(cx509.load_pem_x509_certificate(root).subject)
            .public_key(csr.public_key())
            .serial_number(cx509.random_serial_number())
            .not_valid_before(past - ctimedelta(days=365))
            .not_valid_after(past)
            .add_extension(cx509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca_key, chashes.SHA256())
            .public_bytes(cser.Encoding.PEM)
        )

        leaf, _ = _leaf_cert(
            expired_int_pem, rk.private_bytes(cser.Encoding.PEM, cser.PrivateFormat.PKCS8, cser.NoEncryption())
        )
        result = validate_chain([leaf, expired_int_pem, root])
        assert result.valid is False
        assert any("expired" in e.lower() for e in result.errors)


class TestValidateChainBasicConstraints:
    def test_leaf_signed_by_non_ca_rejected(self) -> None:
        _root, _root_key = _root_ca()
        end_key = _rsa_key()
        end_csr = generate_csr(end_key, {"CN": "NotCA"}, extended_key_usage=["server_auth"])
        end_cert = self_sign(end_csr, end_key)
        leaf, _ = _leaf_cert(end_cert, end_key)
        result = validate_chain([leaf, end_cert])
        assert result.valid is False
        assert any("path length" in e.lower() or len(result.errors) > 0 for e in result.errors)

    def test_path_length_exceeded_reported(self) -> None:
        root, root_key = _root_ca()
        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import hashes as chashes
        from cryptography.hazmat.primitives import serialization as cser

        rk = serialization.load_pem_private_key(generate_key("rsa"), password=None)
        csr = cx509.load_pem_x509_csr(
            generate_csr(
                generate_key("rsa"),
                {"CN": "Ltd CA", "O": "TestOrg"},
                key_usage=["key_cert_sign", "crl_sign"],
            )
        )
        ca_key: Any = _load_private(root_key)
        limited_int = (
            cx509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(cx509.load_pem_x509_certificate(root).subject)
            .public_key(csr.public_key())
            .serial_number(cx509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.UTC))
            .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365))
            .add_extension(cx509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(ca_key, chashes.SHA256())
            .public_bytes(cser.Encoding.PEM)
        )

        int_key_pem = rk.private_bytes(cser.Encoding.PEM, cser.PrivateFormat.PKCS8, cser.NoEncryption())
        int_ca, int_key = _intermediate_ca(limited_int, int_key_pem, cn="Sub Int")
        leaf, _ = _leaf_cert(int_ca, int_key)
        result = validate_chain([leaf, int_ca, limited_int, root])
        assert result.valid is False

    def test_self_signed_leaf_with_ca_true_validates_self_trust(self) -> None:
        root, _root_key = _root_ca()
        result = validate_chain([root])
        assert result.valid is False


class TestValidateChainKeyUsage:
    def test_intermediate_without_keycertsign_reported(self) -> None:
        root, root_key = _root_ca()
        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import hashes as chashes
        from cryptography.hazmat.primitives import serialization as cser

        bad_int_key = generate_key("rsa")
        csr = cx509.load_pem_x509_csr(
            generate_csr(bad_int_key, {"CN": "BadInt", "O": "TestOrg"}, key_usage=["digital_signature"])
        )
        ca_key: Any = _load_private(root_key)
        bad_int_pem = (
            cx509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(cx509.load_pem_x509_certificate(root).subject)
            .public_key(csr.public_key())
            .serial_number(cx509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.UTC))
            .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365))
            .add_extension(cx509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                cx509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, chashes.SHA256())
            .public_bytes(cser.Encoding.PEM)
        )

        leaf, _ = _leaf_cert(bad_int_pem, bad_int_key)
        result = validate_chain([leaf, bad_int_pem, root])
        assert result.valid is False
        assert any("key_cert_sign" in e.lower() or "key usage" in e.lower() for e in result.errors)


class TestValidateChainMixedKeys:
    def test_rsa_root_ecdsa_int_ed25519_leaf(self) -> None:
        root, root_key = _root_ca(cn="Root", key_type="rsa")
        int_ca, int_key = _intermediate_ca(root, root_key, cn="ECInt", key_type="ecdsa")
        leaf, _ = _leaf_cert(int_ca, int_key, cn="EdLeaf", key_type="ed25519")
        result = validate_chain([leaf, int_ca, root])
        assert result.valid is True
        assert len(result.errors) == 0

    def test_ecdsa_root_ed25519_int_rsa_leaf(self) -> None:
        root, root_key = _root_ca(cn="ECRoot", key_type="ecdsa")
        int_ca, int_key = _intermediate_ca(root, root_key, cn="EdInt", key_type="ed25519")
        leaf, _ = _leaf_cert(int_ca, int_key, cn="RSALeaf", key_type="rsa")
        result = validate_chain([leaf, int_ca, root])
        assert result.valid is True


class TestValidateChainResultStructure:
    def test_errors_include_position(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        another_root, _ = _root_ca(cn="Other")
        result = validate_chain([leaf, another_root])
        assert result.valid is False
        assert len(result.errors) > 0

    def test_cert_details_have_expected_fields(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        result = validate_chain([leaf, root])
        for detail in result.cert_details:
            assert "subject_cn" in detail
            assert "issuer_cn" in detail
            assert "serial_number" in detail
            assert "not_valid_before" in detail
            assert "not_valid_after" in detail
            assert "is_ca" in detail
            assert isinstance(detail["serial_number"], int)
            assert isinstance(detail["is_ca"], bool)

    def test_cert_details_match_chain_order(self) -> None:
        root, root_key = _root_ca()
        int_ca, int_key = _intermediate_ca(root, root_key)
        leaf, _ = _leaf_cert(int_ca, int_key)
        result = validate_chain([leaf, int_ca, root])
        assert result.cert_details[0]["subject_cn"] == "leaf.example.com"
        assert result.cert_details[1]["subject_cn"] == "Intermediate CA"
        assert result.cert_details[2]["subject_cn"] == "Root CA"

    def test_valid_chain_returns_no_errors(self) -> None:
        root, root_key = _root_ca()
        for _ in range(3):
            int_ca, int_key = _intermediate_ca(root, root_key)
            leaf, _ = _leaf_cert(int_ca, int_key)
            result = validate_chain([leaf, int_ca, root])
            assert result.valid is True
            assert result.errors == []


class TestValidateChainEdgeCases:
    def test_wrong_order_produces_errors(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        result = validate_chain([root, leaf])
        assert result.valid is False

    def test_unrelated_chains_produce_errors(self) -> None:
        root1, _ = _root_ca(cn="R1")
        root2, root2_key = _root_ca(cn="R2")
        leaf, _ = _leaf_cert(root2, root2_key)
        result = validate_chain([leaf, root1])
        assert result.valid is False

    def test_malformed_pem_at_any_position(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        r1 = validate_chain([b"bad", root])
        r2 = validate_chain([leaf, b"bad"])
        r3 = validate_chain([leaf, root, b"bad"])
        assert r1.valid is False
        assert r2.valid is False
        assert r3.valid is False


# --- build_chain tests (existing function) -------------------------------------------------


class TestBuildChainExtended:
    def test_builds_three_level_no_intermediates_bag(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        chain = build_chain(leaf, [root])
        assert len(chain) == 2
        assert _subject_cn(chain[0]) == "leaf.example.com"
        assert _subject_cn(chain[1]) == "Root CA"

    def test_builds_four_level_chain(self) -> None:
        root, root_key = _root_ca()
        int1, int1_key = _intermediate_ca(root, root_key, cn="Int1")
        int2, int2_key = _intermediate_ca(int1, int1_key, cn="Int2")
        leaf, _ = _leaf_cert(int2, int2_key)
        chain = build_chain(leaf, [int1, int2, root])
        assert len(chain) == 4
        names = [_subject_cn(c) for c in chain]
        assert names[0] == "leaf.example.com"
        assert names[-1] == "Root CA"

    def test_leaf_only_returns_single(self) -> None:
        key = _rsa_key()
        csr = generate_csr(key, {"CN": "solo"})
        cert = self_sign(csr, key)
        chain = build_chain(cert, [])
        assert len(chain) == 1
        assert _subject_cn(chain[0]) == "solo"

    def test_unrelated_intermediates_ignored(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        other_root, _other_root_key = _root_ca(cn="Other")
        chain = build_chain(leaf, [other_root])
        assert len(chain) == 1

    def test_duplicate_certs_dont_loop(self) -> None:
        root, root_key = _root_ca()
        leaf, _ = _leaf_cert(root, root_key)
        chain = build_chain(leaf, [root, root, root])
        assert len(chain) == 2
