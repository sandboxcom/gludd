"""Create, inspect, and validate X.509 certificates and signing requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import (
    ec,
    ed448,
    ed25519,
    rsa,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_PrivateKeyTypes = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey


def _signature_hash(private_key: Any) -> hashes.SHA256 | None:
    """Return the hash required by a signing key family."""
    if isinstance(private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
        return None
    return hashes.SHA256()


def generate_key(key_type: str, key_size: int | None = None) -> bytes:
    """Generate an unencrypted private key in PKCS8 PEM form."""
    private_key: _PrivateKeyTypes
    if key_type == "rsa":
        size = key_size or 2048
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=size)
    elif key_type == "ecdsa":
        curve: ec.EllipticCurve
        if key_size == 256 or key_size is None:
            curve = ec.SECP256R1()
        elif key_size == 384:
            curve = ec.SECP384R1()
        elif key_size == 521:
            curve = ec.SECP521R1()
        else:
            raise ValueError(f"Unsupported ECDSA key_size: {key_size}. Use 256, 384, or 521.")
        private_key = ec.generate_private_key(curve)
    elif key_type == "ed25519":
        private_key = ed25519.Ed25519PrivateKey.generate()
    else:
        raise ValueError(f"Unknown key_type: {key_type!r}. Use 'rsa', 'ecdsa', or 'ed25519'.")

    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _load_private_key(key_pem: bytes) -> Any:
    return serialization.load_pem_private_key(key_pem, password=None)


def generate_csr(
    key_pem: bytes,
    subject: dict[str, str],
    sans: list[str] | None = None,
    key_usage: list[str] | None = None,
    extended_key_usage: list[str] | None = None,
) -> bytes:
    """Build a signed CSR, including CA constraints for certificate-signing keys."""
    private_key = _load_private_key(key_pem)

    name_attrs = [_name_attribute(k, v) for k, v in subject.items()]
    csr_builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(name_attrs))

    if sans:
        csr_builder = csr_builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san) for san in sans]),
            critical=False,
        )

    if key_usage:
        usage_flags: dict[str, str] = {
            "digital_signature": "digital_signature",
            "content_commitment": "content_commitment",
            "key_encipherment": "key_encipherment",
            "data_encipherment": "data_encipherment",
            "key_agreement": "key_agreement",
            "key_cert_sign": "key_cert_sign",
            "crl_sign": "crl_sign",
            "encipher_only": "encipher_only",
            "decipher_only": "decipher_only",
        }
        kwargs: dict[str, bool] = {
            "digital_signature": False,
            "content_commitment": False,
            "key_encipherment": False,
            "data_encipherment": False,
            "key_agreement": False,
            "key_cert_sign": False,
            "crl_sign": False,
            "encipher_only": False,
            "decipher_only": False,
        }
        for u in key_usage:
            if u in usage_flags:
                kwargs[u] = True
        if kwargs["encipher_only"] or kwargs["decipher_only"]:
            kwargs["key_agreement"] = True
        csr_builder = csr_builder.add_extension(x509.KeyUsage(**kwargs), critical=True)
        if kwargs["key_cert_sign"]:
            csr_builder = csr_builder.add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )

    if extended_key_usage:
        eku_map: dict[str, x509.ObjectIdentifier] = {
            "server_auth": ExtendedKeyUsageOID.SERVER_AUTH,
            "client_auth": ExtendedKeyUsageOID.CLIENT_AUTH,
            "code_signing": ExtendedKeyUsageOID.CODE_SIGNING,
            "email_protection": ExtendedKeyUsageOID.EMAIL_PROTECTION,
            "time_stamping": ExtendedKeyUsageOID.TIME_STAMPING,
            "ocsp_signing": ExtendedKeyUsageOID.OCSP_SIGNING,
        }
        oids = [eku_map[u] for u in extended_key_usage if u in eku_map]
        if oids:
            csr_builder = csr_builder.add_extension(x509.ExtendedKeyUsage(oids), critical=False)

    return csr_builder.sign(private_key, _signature_hash(private_key)).public_bytes(serialization.Encoding.PEM)


def _name_attribute(key: str, value: str) -> x509.NameAttribute[str]:
    oid_map: dict[str, x509.ObjectIdentifier] = {
        "CN": NameOID.COMMON_NAME,
        "commonName": NameOID.COMMON_NAME,
        "O": NameOID.ORGANIZATION_NAME,
        "organizationName": NameOID.ORGANIZATION_NAME,
        "OU": NameOID.ORGANIZATIONAL_UNIT_NAME,
        "organizationalUnitName": NameOID.ORGANIZATIONAL_UNIT_NAME,
        "C": NameOID.COUNTRY_NAME,
        "countryName": NameOID.COUNTRY_NAME,
        "ST": NameOID.STATE_OR_PROVINCE_NAME,
        "stateOrProvinceName": NameOID.STATE_OR_PROVINCE_NAME,
        "L": NameOID.LOCALITY_NAME,
        "localityName": NameOID.LOCALITY_NAME,
    }
    oid = oid_map.get(key)
    if oid is None:
        raise ValueError(f"Unknown subject key: {key!r}")
    return x509.NameAttribute(oid, value)


def _build_cert(
    subject_name: x509.Name,
    issuer_name: x509.Name,
    public_key: Any,
    signer_key: Any,
    validity_days: int,
    extensions: list[x509.Extension[x509.ExtensionType]] | x509.Extensions,
) -> bytes:
    now = datetime.now(UTC)
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject_name)
        .issuer_name(issuer_name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
    )
    for ext in extensions:
        cert_builder = cert_builder.add_extension(ext.value, critical=ext.critical)
    return cert_builder.sign(signer_key, _signature_hash(signer_key)).public_bytes(serialization.Encoding.PEM)


def self_sign(csr_pem: bytes, key_pem: bytes, validity_days: int = 365) -> bytes:
    """Issue a self-signed certificate from *csr_pem*."""
    csr = x509.load_pem_x509_csr(csr_pem)
    private_key = _load_private_key(key_pem)
    return _build_cert(
        subject_name=csr.subject,
        issuer_name=csr.subject,
        public_key=csr.public_key(),
        signer_key=private_key,
        validity_days=validity_days,
        extensions=list(csr.extensions),
    )


def sign_csr(
    csr_pem: bytes,
    ca_cert_pem: bytes,
    ca_key_pem: bytes,
    validity_days: int = 365,
) -> bytes:
    """Issue a certificate from a CSR using the supplied CA certificate and key."""
    csr = x509.load_pem_x509_csr(csr_pem)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    ca_key = _load_private_key(ca_key_pem)
    return _build_cert(
        subject_name=csr.subject,
        issuer_name=ca_cert.subject,
        public_key=csr.public_key(),
        signer_key=ca_key,
        validity_days=validity_days,
        extensions=csr.extensions,
    )


def parse_cert(cert_pem: bytes) -> dict[str, object]:
    """Return a serializable summary of a PEM certificate."""
    cert = x509.load_pem_x509_certificate(cert_pem)

    _oid_to_name: dict[str, str] = {
        "2.5.4.3": "commonName",
        "2.5.4.6": "countryName",
        "2.5.4.7": "localityName",
        "2.5.4.8": "stateOrProvinceName",
        "2.5.4.10": "organizationName",
        "2.5.4.11": "organizationalUnitName",
    }

    subject: dict[str, list[str]] = {}
    for attr in cert.subject:
        name = _oid_to_name.get(attr.oid.dotted_string, attr.oid.dotted_string)
        val = str(attr.value)
        if name in subject:
            subject[name].append(val)
        else:
            subject[name] = [val]

    issuer: dict[str, list[str]] = {}
    for attr in cert.issuer:
        name = _oid_to_name.get(attr.oid.dotted_string, attr.oid.dotted_string)
        val = str(attr.value)
        if name in issuer:
            issuer[name].append(val)
        else:
            issuer[name] = [val]

    sans: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in san_ext.value:
            if isinstance(name, x509.DNSName):
                sans.append(name.value)
    except x509.ExtensionNotFound:
        pass

    key_usage: list[str] = []
    try:
        ku_ext = cert.extensions.get_extension_for_class(x509.KeyUsage)
        ku = ku_ext.value
        if ku.digital_signature:
            key_usage.append("digital_signature")
        if ku.content_commitment:
            key_usage.append("content_commitment")
        if ku.key_encipherment:
            key_usage.append("key_encipherment")
        if ku.data_encipherment:
            key_usage.append("data_encipherment")
        if ku.key_agreement:
            key_usage.append("key_agreement")
        if ku.key_cert_sign:
            key_usage.append("key_cert_sign")
        if ku.crl_sign:
            key_usage.append("crl_sign")
        try:
            if ku.encipher_only:
                key_usage.append("encipher_only")
        except ValueError:
            pass
        try:
            if ku.decipher_only:
                key_usage.append("decipher_only")
        except ValueError:
            pass
    except x509.ExtensionNotFound:
        pass

    eku: list[str] = []
    eku_name_map: dict[x509.ObjectIdentifier, str] = {
        ExtendedKeyUsageOID.SERVER_AUTH: "server_auth",
        ExtendedKeyUsageOID.CLIENT_AUTH: "client_auth",
        ExtendedKeyUsageOID.CODE_SIGNING: "code_signing",
        ExtendedKeyUsageOID.EMAIL_PROTECTION: "email_protection",
        ExtendedKeyUsageOID.TIME_STAMPING: "time_stamping",
        ExtendedKeyUsageOID.OCSP_SIGNING: "ocsp_signing",
    }
    try:
        eku_ext = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        for oid in eku_ext.value:
            eku_name: str | None = eku_name_map.get(oid)
            if eku_name:
                eku.append(eku_name)
    except x509.ExtensionNotFound:
        pass

    return {
        "serial_number": cert.serial_number,
        "subject": subject,
        "issuer": issuer,
        "not_valid_before": cert.not_valid_before_utc.isoformat(),
        "not_valid_after": cert.not_valid_after_utc.isoformat(),
        "sans": sans,
        "key_usage": key_usage,
        "extended_key_usage": eku,
        "version": cert.version.value,
        "signature_algorithm_oid": cert.signature_algorithm_oid.dotted_string,
    }


def verify_chain(cert_chain: list[bytes]) -> bool:
    """Verify each leaf-first direct-issuance link without asserting trust."""
    if len(cert_chain) < 2:
        return False
    try:
        parsed = [x509.load_pem_x509_certificate(pem) for pem in cert_chain]
    except (ValueError, TypeError):
        return False
    for cert, issuer in pairwise(parsed):
        try:
            cert.verify_directly_issued_by(issuer)
        except (InvalidSignature, ValueError, TypeError):
            return False
    return True


def build_chain(leaf_cert: bytes, intermediates: list[bytes]) -> list[bytes]:
    """Build a leaf-first chain from unordered candidate intermediates."""
    certs: list[tuple[x509.Certificate, bytes]] = []
    for pem in [leaf_cert, *intermediates]:
        cert = x509.load_pem_x509_certificate(pem)
        certs.append((cert, pem))

    chain: list[bytes] = []
    current_pem = leaf_cert
    current = x509.load_pem_x509_certificate(current_pem)

    while True:
        chain.append(current_pem)
        found = False
        for cert, pem in certs:
            if cert.subject == current.issuer and pem != current_pem:
                current_pem = pem
                current = cert
                found = True
                break
        if not found:
            break

    return chain


@dataclass
class ValidationResult:
    """Describe chain policy status, diagnostics, and parsed certificate details."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    cert_details: list[dict[str, object]] = field(default_factory=list)


def _parse_or_none(pem: bytes) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(pem)
    except (ValueError, TypeError):
        return None


def _cert_details(cert: x509.Certificate) -> dict[str, object]:
    subject_cn = ""
    issuer_cn = ""
    try:
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if cn_attrs:
            subject_cn = str(cn_attrs[0].value)
    except Exception:
        pass
    try:
        cn_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
        if cn_attrs:
            issuer_cn = str(cn_attrs[0].value)
    except Exception:
        pass

    is_ca = False
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        is_ca = bc.value.ca
    except x509.ExtensionNotFound:
        pass

    return {
        "subject_cn": subject_cn,
        "issuer_cn": issuer_cn,
        "serial_number": cert.serial_number,
        "not_valid_before": cert.not_valid_before_utc.isoformat(),
        "not_valid_after": cert.not_valid_after_utc.isoformat(),
        "is_ca": is_ca,
    }


def _verify_signature(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    """Return whether *issuer* directly issued and signed *cert*."""
    try:
        cert.verify_directly_issued_by(issuer)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _check_expiry(cert: x509.Certificate, validation_time: datetime) -> list[str]:
    errors: list[str] = []
    if validation_time < cert.not_valid_before_utc:
        errors.append(f"certificate not yet valid: not_before={cert.not_valid_before_utc.isoformat()}")
    if validation_time > cert.not_valid_after_utc:
        errors.append(f"certificate expired: not_after={cert.not_valid_after_utc.isoformat()}")
    return errors


def _check_ca_constraints(cert: x509.Certificate, is_leaf: bool, position: int) -> list[str]:
    errors: list[str] = []
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        bc_val = bc.value
        if not is_leaf and not bc_val.ca:
            errors.append(f"cert at position {position}: issuer is not a CA (BasicConstraints ca=False)")
        if bc_val.ca and bc_val.path_length is not None:
            pass
    except x509.ExtensionNotFound:
        if not is_leaf:
            errors.append(f"cert at position {position}: issuer lacks BasicConstraints extension")
    return errors


def _check_key_usage(cert: x509.Certificate, is_leaf: bool, position: int) -> list[str]:
    errors: list[str] = []
    if is_leaf:
        return errors
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage)
        if not ku.value.key_cert_sign:
            errors.append(f"cert at position {position}: issuer key usage does not allow key_cert_sign")
    except x509.ExtensionNotFound:
        pass
    return errors


def _check_path_length(
    cert_chain: list[x509.Certificate],
) -> list[str]:
    errors: list[str] = []
    for i in range(len(cert_chain) - 1):
        issuer = cert_chain[i + 1]
        try:
            bc = issuer.extensions.get_extension_for_class(x509.BasicConstraints)
            if bc.value.path_length is not None and bc.value.ca:
                intervening_ca_count = len(cert_chain) - i - 3
                if intervening_ca_count > bc.value.path_length:
                    errors.append(
                        f"cert at position {i + 1}: path_length={bc.value.path_length} "
                        f"exceeded ({intervening_ca_count} intervening CAs)"
                    )
        except x509.ExtensionNotFound:
            pass
    return errors


def validate_chain(
    cert_chain: list[bytes],
    validation_time: datetime | None = None,
) -> ValidationResult:
    """Validate a leaf-first chain against its caller-supplied terminal anchor."""
    if not cert_chain:
        return ValidationResult(valid=False, errors=["empty certificate chain"])

    if len(cert_chain) < 2:
        cert = _parse_or_none(cert_chain[0])
        if cert is None:
            return ValidationResult(
                valid=False,
                errors=["failed to parse certificate"],
                cert_details=[],
            )
        return ValidationResult(
            valid=False,
            errors=["chain must contain at least 2 certificates"],
            cert_details=[_cert_details(cert)],
        )

    now = validation_time or datetime.now(UTC)
    errors: list[str] = []
    parsed: list[x509.Certificate] = []

    for i, pem in enumerate(cert_chain):
        cert = _parse_or_none(pem)
        if cert is None:
            errors.append(f"cert at position {i}: failed to parse PEM")
            parsed = []
            break
        parsed.append(cert)

    if errors:
        return ValidationResult(valid=False, errors=errors, cert_details=[])

    for i, cert in enumerate(parsed):
        is_leaf = i == 0
        expiry_errs = _check_expiry(cert, now)
        for e in expiry_errs:
            errors.append(f"cert at position {i}: {e}")
        ca_errs = _check_ca_constraints(cert, is_leaf, i)
        errors.extend(ca_errs)
        ku_errs = _check_key_usage(cert, is_leaf, i)
        errors.extend(ku_errs)

        if i < len(parsed) - 1:
            issuer = parsed[i + 1]
            if cert.issuer != issuer.subject:
                errors.append(f"cert at position {i}: issuer does not match subject of cert at position {i + 1}")
            if not _verify_signature(cert, issuer):
                errors.append(f"cert at position {i}: signature verification failed against cert at position {i + 1}")

    pl_errs = _check_path_length(parsed)
    errors.extend(pl_errs)

    details = [_cert_details(c) for c in parsed]

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        cert_details=details,
    )
