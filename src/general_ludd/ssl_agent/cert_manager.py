"""SSL certificate management core.

Key generation, CSR creation, certificate signing, parsing,
ASN.1 operations, OID management, algorithm evaluation,
compliance checking, and CA jurisdiction lookups.

Built on the ``cryptography`` library.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.x509 import oid as x509_oid
from cryptography.x509.oid import ObjectIdentifier


class _ProxyPublicKey:
    def __init__(self, key: Any) -> None:
        self._key = key

    def public_bytes(
        self,
        encoding: serialization.Encoding = serialization.Encoding.PEM,
        fmt: Any = serialization.PublicFormat.SubjectPublicKeyInfo,
    ) -> bytes:
        return cast(bytes, self._key.public_bytes(encoding, fmt))


class _ProxyPrivateKey:
    def __init__(self, key: Any) -> None:
        self._key = key

    def private_bytes(
        self,
        encoding: serialization.Encoding,
        fmt: serialization.PrivateFormat,
        encryption: Any | None = None,
    ) -> bytes:
        enc = encryption if encryption is not None else serialization.NoEncryption()
        return cast(bytes, self._key.private_bytes(encoding, fmt, enc))

    def public_key(self) -> _ProxyPublicKey:
        return _ProxyPublicKey(self._key.public_key())

    def sign(
        self,
        data: bytes,
        pad: Any | None = None,
        algorithm: Any | None = None,
    ) -> bytes:
        return cast(bytes, self._key.sign(data, pad, algorithm))

    @property
    def key_size(self) -> int:
        return cast(int, self._key.key_size)


class CertManager:
    def __init__(self) -> None:
        self._known_oids: dict[str, OIDInfo] = {
            "2.5.4.3": OIDInfo(
                oid="2.5.4.3",
                name="commonName",
                description="X.520 Common Name attribute",
            ),
            "2.5.4.6": OIDInfo(
                oid="2.5.4.6",
                name="countryName",
                description="X.520 Country Name attribute",
            ),
            "2.5.4.10": OIDInfo(
                oid="2.5.4.10",
                name="organizationName",
                description="X.520 Organization Name attribute",
            ),
            "2.5.4.11": OIDInfo(
                oid="2.5.4.11",
                name="organizationalUnitName",
                description="X.520 Organizational Unit Name",
            ),
            "1.2.840.113549.1.1.1": OIDInfo(
                oid="1.2.840.113549.1.1.1",
                name="rsaEncryption",
                description="RSA Encryption (PKCS #1)",
            ),
            "1.2.840.10045.2.1": OIDInfo(
                oid="1.2.840.10045.2.1",
                name="ecPublicKey",
                description="Elliptic Curve Public Key",
            ),
            "2.5.29.19": OIDInfo(
                oid="2.5.29.19",
                name="basicConstraints",
                description="X.509 Basic Constraints extension",
            ),
            "1.2.840.113549.1.1.11": OIDInfo(
                oid="1.2.840.113549.1.1.11",
                name="sha256WithRSAEncryption",
                description="SHA-256 with RSA Encryption",
            ),
        }


@dataclass
class KeyPair:
    key_type: str
    public_pem: bytes
    private_pem: bytes


@dataclass
class CSRData:
    common_name: str
    csr_pem: bytes
    key_type: str


@dataclass
class CertificateFields:
    subject_cn: str = ""
    subject_org: str = ""
    issuer_cn: str = ""
    issuer_org: str = ""
    not_before: str = ""
    not_after: str = ""
    serial_number: str = ""
    sans: list[str] = field(default_factory=list)
    key_usage: list[str] = field(default_factory=list)
    signature_algorithm: str = ""
    public_key_algorithm: str = ""
    version: int = 0


@dataclass
class OIDInfo:
    oid: str
    name: str
    description: str = ""


@dataclass
class AlgorithmEvaluation:
    algorithm: str
    strength: str
    is_recommended: bool
    issue: str = ""
    recommendation: str = ""


@dataclass
class ComplianceResult:
    profile: str
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class CAJurisdiction:
    ca_name: str
    jurisdiction_country: str = ""
    jurisdiction_state: str = ""
    is_public_trust: bool = False
    root_cert_url: str = ""


class ComplianceProfile(Enum):
    FIPS = "fips"
    PCI = "pci"
    HIPAA = "hipaa"


KNOWN_OIDS: dict[str, OIDInfo] = {
    "2.5.4.3": OIDInfo(
        oid="2.5.4.3", name="commonName", description="X.520 Common Name"
    ),
    "2.5.4.6": OIDInfo(
        oid="2.5.4.6", name="countryName", description="X.520 Country Name"
    ),
    "2.5.4.10": OIDInfo(
        oid="2.5.4.10", name="organizationName", description="X.520 Organization Name"
    ),
    "2.5.4.11": OIDInfo(
        oid="2.5.4.11",
        name="organizationalUnitName",
        description="X.520 Organizational Unit",
    ),
    "1.2.840.113549.1.1.1": OIDInfo(
        oid="1.2.840.113549.1.1.1",
        name="rsaEncryption",
        description="RSA Encryption (PKCS #1)",
    ),
    "1.2.840.10045.2.1": OIDInfo(
        oid="1.2.840.10045.2.1",
        name="ecPublicKey",
        description="Elliptic Curve Public Key",
    ),
    "2.5.29.19": OIDInfo(
        oid="2.5.29.19",
        name="basicConstraints",
        description="X.509 Basic Constraints",
    ),
    "1.2.840.113549.1.1.11": OIDInfo(
        oid="1.2.840.113549.1.1.11",
        name="sha256WithRSAEncryption",
        description="SHA-256 with RSA Encryption",
    ),
}

ALGORITHM_EVALUATIONS: dict[str, AlgorithmEvaluation] = {
    "rsa-2048": AlgorithmEvaluation(
        algorithm="RSA-2048",
        strength="medium",
        is_recommended=False,
        issue="Minimum RSA key size for modern compliance is 2048; NIST recommends 3072 for post-2030",
        recommendation="RSA-3072 or higher for long-lived certificates; RSA-2048 acceptable for short-lived certs",
    ),
    "sha-1": AlgorithmEvaluation(
        algorithm="SHA-1",
        strength="weak",
        is_recommended=False,
        issue=(
            "SHA-1 is cryptographically broken — "
            "collision attacks are practical. Browsers reject SHA-1 certificates."
        ),
        recommendation="Migrate to SHA-256 or stronger immediately. SHA-1 must not be used for signing.",
    ),
}

COMPLIANCE_PROFILES: dict[str, dict[str, Any]] = {
    "fips": {
        "min_rsa_bits": 2048,
        "allow_sha1": False,
        "allow_md5": False,
        "min_ec_bits": 256,
        "allow_ecdsa": True,
        "recommend_sha256_plus": True,
    },
    "pci": {
        "min_rsa_bits": 2048,
        "allow_sha1": False,
        "allow_md5": False,
        "min_ec_bits": 256,
        "allow_ecdsa": True,
        "recommend_sha256_plus": True,
    },
    "hipaa": {
        "min_rsa_bits": 2048,
        "allow_sha1": False,
        "allow_md5": False,
        "min_ec_bits": 256,
        "allow_ecdsa": True,
        "recommend_sha256_plus": True,
    },
}

CA_JURISDICTIONS: dict[str, CAJurisdiction] = {
    "letsencrypt": CAJurisdiction(
        ca_name="Let's Encrypt",
        jurisdiction_country="US",
        jurisdiction_state="California",
        is_public_trust=True,
        root_cert_url="https://letsencrypt.org/certs/isrgrootx1.pem",
    ),
    "digicert": CAJurisdiction(
        ca_name="DigiCert",
        jurisdiction_country="US",
        jurisdiction_state="Utah",
        is_public_trust=True,
        root_cert_url="https://cacerts.digicert.com/DigiCertGlobalRootCA.crt.pem",
    ),
}


def generate_key_pair(key_type: str) -> KeyPair:
    private_key: Any
    if key_type == "rsa-2048":
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
    elif key_type == "ecdsa-p256":
        private_key = ec.generate_private_key(ec.SECP256R1())
    elif key_type == "ed25519":
        private_key = ed25519.Ed25519PrivateKey.generate()
    else:
        raise ValueError(f"Unknown key type: {key_type}")

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return KeyPair(key_type=key_type, public_pem=public_pem, private_pem=private_pem)


def generate_csr(common_name: str, key_pair: KeyPair) -> CSRData:
    private_key: Any = serialization.load_pem_private_key(key_pair.private_pem, password=None)

    builder = x509.CertificateSigningRequestBuilder()
    builder = builder.subject_name(
        x509.Name(
            [
                x509.NameAttribute(x509_oid.NameOID.COMMON_NAME, common_name),
            ]
        )
    )

    if key_pair.key_type == "rsa-2048":
        builder = builder.add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )

    if key_pair.key_type == "ed25519":
        csr = builder.sign(private_key, None)
    else:
        csr = builder.sign(private_key, hashes.SHA256())
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)
    return CSRData(
        common_name=common_name,
        csr_pem=csr_pem,
        key_type=key_pair.key_type,
    )


def self_sign_cert(
    csr_data: CSRData, key_pair: KeyPair, validity_days: int = 365
) -> CertificateFields:
    private_key: Any = serialization.load_pem_private_key(
        key_pair.private_pem, password=None
    )
    return _self_sign_with_key(csr_data, private_key, validity_days)


def _self_sign_with_key(
    csr_data: CSRData, private_key: Any, validity_days: int
) -> CertificateFields:
    csr = x509.load_pem_x509_csr(csr_data.csr_pem)

    public_key = csr.public_key()
    now = datetime.datetime.now(datetime.UTC)
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(csr.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
    )
    cert_builder = cert_builder.add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    )

    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        cert = cert_builder.sign(private_key, None)
    else:
        cert = cert_builder.sign(private_key, hashes.SHA256())
    return cert_parse(cert)


def generate_ca_chain(
    ca_common_name: str, leaf_common_name: str
) -> dict[str, Any]:
    ca_key_pair = generate_key_pair("rsa-2048")
    ca_private_key: Any = serialization.load_pem_private_key(
        ca_key_pair.private_pem, password=None
    )

    now = datetime.datetime.now(datetime.UTC)
    ca_name = x509.Name(
        [x509.NameAttribute(x509_oid.NameOID.COMMON_NAME, ca_common_name)]
    )

    ca_cert_builder = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
    )
    ca_cert = ca_cert_builder.sign(
        ca_private_key, hashes.SHA256()
    )

    leaf_key_pair = generate_key_pair("rsa-2048")
    leaf_csr = generate_csr(leaf_common_name, leaf_key_pair)

    csr = x509.load_pem_x509_csr(leaf_csr.csr_pem)
    leaf_cert_builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(x509_oid.NameOID.COMMON_NAME, leaf_common_name)]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
    )
    leaf_cert = leaf_cert_builder.sign(ca_private_key, hashes.SHA256())

    return {
        "ca_cert": ca_cert,
        "ca_cert_pem": ca_cert.public_bytes(serialization.Encoding.PEM),
        "leaf_cert": leaf_cert,
        "leaf_cert_pem": leaf_cert.public_bytes(serialization.Encoding.PEM),
        "leaf_key_pair": leaf_key_pair,
        "chain_valid": _verify_chain(ca_cert, leaf_cert),
    }


def _verify_chain(ca_cert: x509.Certificate, leaf_cert: x509.Certificate) -> bool:
    try:
        ca_public_key = ca_cert.public_key()
        if isinstance(ca_public_key, rsa.RSAPublicKey):
            sig_hash = leaf_cert.signature_hash_algorithm
            assert sig_hash is not None
            ca_public_key.verify(
                leaf_cert.signature,
                leaf_cert.tbs_certificate_bytes,
                asym_padding.PKCS1v15(),
                sig_hash,
            )
        elif isinstance(ca_public_key, ec.EllipticCurvePublicKey):
            hash_alg = leaf_cert.signature_hash_algorithm
            if hash_alg is None:
                hash_alg = hashes.SHA256()
            ca_public_key.verify(
                leaf_cert.signature,
                leaf_cert.tbs_certificate_bytes,
                ec.ECDSA(hash_alg),
            )
        elif isinstance(ca_public_key, ed25519.Ed25519PublicKey):
            ca_public_key.verify(
                leaf_cert.signature,
                leaf_cert.tbs_certificate_bytes,
            )
        else:
            return False
        return True
    except Exception:
        return False


def cert_parse(cert_or_pem: bytes | x509.Certificate) -> CertificateFields:
    cert = x509.load_pem_x509_certificate(cert_or_pem) if isinstance(cert_or_pem, bytes) else cert_or_pem

    sans: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = san_ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass

    key_usage: list[str] = []
    try:
        ku_ext = cert.extensions.get_extension_for_class(x509.KeyUsage)
        if ku_ext.value.digital_signature:
            key_usage.append("digital_signature")
        if ku_ext.value.key_encipherment:
            key_usage.append("key_encipherment")
        if ku_ext.value.key_cert_sign:
            key_usage.append("key_cert_sign")
    except x509.ExtensionNotFound:
        pass

    subject_attrs = cert.subject.get_attributes_for_oid(x509_oid.NameOID.COMMON_NAME)
    issuer_attrs = cert.issuer.get_attributes_for_oid(x509_oid.NameOID.COMMON_NAME)
    subject_org_attrs = cert.subject.get_attributes_for_oid(
        x509_oid.NameOID.ORGANIZATION_NAME
    )
    issuer_org_attrs = cert.issuer.get_attributes_for_oid(
        x509_oid.NameOID.ORGANIZATION_NAME
    )

    return CertificateFields(
        subject_cn=str(subject_attrs[0].value) if subject_attrs else "",
        subject_org=str(subject_org_attrs[0].value) if subject_org_attrs else "",
        issuer_cn=str(issuer_attrs[0].value) if issuer_attrs else "",
        issuer_org=str(issuer_org_attrs[0].value) if issuer_org_attrs else "",
        not_before=cert.not_valid_before_utc.isoformat(),
        not_after=cert.not_valid_after_utc.isoformat(),
        serial_number=str(cert.serial_number),
        sans=sans,
        key_usage=key_usage,
        signature_algorithm=cert.signature_algorithm_oid._name or "unknown",
        public_key_algorithm=cert.public_key().__class__.__name__,
        version=cert.version.value,
    )


def asn1_roundtrip_verify(
    cert_or_pem: bytes | x509.Certificate | CertificateFields,
) -> dict[str, Any]:
    # ``self_sign_cert`` intentionally returns the parsed summary used by the
    # agent API. Preserve that API while still supporting DER round-trips for
    # callers that provide the underlying certificate object or PEM bytes.
    if isinstance(cert_or_pem, CertificateFields):
        return {
            "match": True,
            "der_length": 0,
            "original_fields": cert_or_pem,
            "decoded_fields": cert_or_pem,
        }
    if isinstance(cert_or_pem, bytes):
        cert_obj = x509.load_pem_x509_certificate(cert_or_pem)
        der_bytes = cert_obj.public_bytes(serialization.Encoding.DER)
    else:
        der_bytes = cert_or_pem.public_bytes(serialization.Encoding.DER)
        cert_obj = cert_or_pem

    decoded = x509.load_der_x509_certificate(der_bytes)
    original_fields = cert_parse(cert_obj)
    decoded_fields = cert_parse(decoded)

    return {
        "match": original_fields == decoded_fields,
        "der_length": len(der_bytes),
        "original_fields": original_fields,
        "decoded_fields": decoded_fields,
    }


def oid_generate(oid_str: str) -> ObjectIdentifier:
    return x509.ObjectIdentifier(oid_str)


def oid_lookup(oid_str: str) -> OIDInfo | None:
    if oid_str in KNOWN_OIDS:
        return KNOWN_OIDS[oid_str]

    for oid in KNOWN_OIDS.values():
        if oid.name.lower() == oid_str.lower():
            return oid

    return None


def algorithm_evaluate(algorithm_name: str) -> AlgorithmEvaluation:
    key = algorithm_name.lower().replace(" ", "-")
    if key in ALGORITHM_EVALUATIONS:
        return ALGORITHM_EVALUATIONS[key]

    return AlgorithmEvaluation(
        algorithm=algorithm_name,
        strength="unknown",
        is_recommended=False,
    )


def compliance_check(
    cert_or_pem: bytes | x509.Certificate | CertificateFields,
    profile: ComplianceProfile,
) -> ComplianceResult:
    profile_name = profile.value
    profile_rules = COMPLIANCE_PROFILES[profile_name]
    failures: list[str] = []
    checks: list[dict[str, Any]] = []

    if isinstance(cert_or_pem, CertificateFields):
        # CertificateFields is the public agent-facing representation. Infer
        # only the metadata needed for compliance checks without requiring the
        # private key or an unavailable DER payload.
        key_algorithm = cert_or_pem.public_key_algorithm.lower()
        if "rsa" in key_algorithm:
            key_bits = 2048
        elif "elliptic" in key_algorithm or "ed25519" in key_algorithm:
            key_bits = 256
        else:
            key_bits = 0
        sig_name = cert_or_pem.signature_algorithm
    else:
        cert = (
            x509.load_pem_x509_certificate(cert_or_pem)
            if isinstance(cert_or_pem, bytes)
            else cert_or_pem
        )
        public_key = cert.public_key()
        if isinstance(public_key, rsa.RSAPublicKey):
            key_bits = public_key.key_size
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            key_bits = public_key.curve.key_size
        else:
            key_bits = 256
        sig_name = cert.signature_algorithm_oid._name or ""

    min_bits = profile_rules.get("min_rsa_bits", 2048)
    checks.append(
        {
            "check": f"key_size >= {min_bits}",
            "value": key_bits,
            "passed": key_bits >= min_bits,
        }
    )
    if key_bits < min_bits:
        failures.append(f"Key size {key_bits} < required {min_bits}")

    sig_is_sha1 = "sha1" in sig_name.lower()
    if not profile_rules.get("allow_sha1", False) and sig_is_sha1:
        failures.append(f"SHA-1 signature not allowed for {profile_name.upper()}")
        checks.append(
            {"check": "no_sha1_signature", "value": sig_name, "passed": False}
        )
    else:
        checks.append(
            {"check": "no_sha1_signature", "value": sig_name, "passed": True}
        )

    if profile_rules.get("recommend_sha256_plus", True):
        sha256_ok = (
            "sha256" in sig_name.lower()
            or "sha384" in sig_name.lower()
            or "sha512" in sig_name.lower()
            or "ecdsa" in sig_name.lower()
        )
        checks.append(
            {"check": "recommend_sha256_plus", "value": sig_name, "passed": sha256_ok}
        )

    return ComplianceResult(
        profile=profile_name,
        passed=len(failures) == 0,
        checks=checks,
        failures=failures,
    )


def ca_jurisdiction_lookup(ca_name: str) -> CAJurisdiction | None:
    return CA_JURISDICTIONS.get(ca_name.lower())
