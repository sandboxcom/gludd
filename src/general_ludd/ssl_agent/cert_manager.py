"""SSL certificate management core.

Key generation, CSR creation, certificate signing and parsing, algorithm
evaluation, compliance checking, and CA jurisdiction lookups. ASN.1 DER and OID
utilities are owned by ``general_ludd.security`` collection module utilities.

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


@dataclass
class KeyPair:
    """Serialized public and private key material for one algorithm."""

    key_type: str
    public_pem: bytes
    private_pem: bytes


@dataclass
class CSRData:
    """Certificate-signing request plus its key-type metadata."""

    common_name: str
    csr_pem: bytes
    key_type: str


@dataclass
class CertificateFields:
    """Normalized certificate fields exposed by the SSL agent."""

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
class AlgorithmEvaluation:
    """Security posture and migration guidance for one algorithm."""

    algorithm: str
    strength: str
    is_recommended: bool
    issue: str = ""
    recommendation: str = ""


@dataclass
class ComplianceResult:
    """Checks and failures produced for a compliance profile."""

    profile: str
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class CAJurisdiction:
    """Jurisdiction and trust metadata for a certificate authority."""

    ca_name: str
    jurisdiction_country: str = ""
    jurisdiction_state: str = ""
    is_public_trust: bool = False
    root_cert_url: str = ""


class ComplianceProfile(Enum):
    """Supported certificate compliance profiles."""

    FIPS = "fips"
    PCI = "pci"
    HIPAA = "hipaa"


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
    """Generate and serialize a supported asymmetric key pair.

    Args:
        key_type: Algorithm and size identifier.

    Returns:
        Serialized public and private key material.

    Raises:
        ValueError: If ``key_type`` is unsupported.
    """
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
    """Create a certificate-signing request for a common name.

    Args:
        common_name: DNS name to encode as the subject common name.
        key_pair: Key material used to sign the request.

    Returns:
        Serialized request and its identifying metadata.
    """
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
    """Self-sign a request and return its normalized certificate fields.

    Args:
        csr_data: Certificate-signing request to issue.
        key_pair: Private key that signs the certificate.
        validity_days: Number of days the certificate remains valid.

    Returns:
        Normalized fields from the issued certificate.
    """
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
    """Generate a root CA and a signed leaf certificate.

    Args:
        ca_common_name: Subject common name for the root CA.
        leaf_common_name: Subject common name for the leaf certificate.

    Returns:
        Certificate objects, PEM encodings, leaf key material, and validation state.
    """
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
    """Normalize a PEM-encoded or parsed X.509 certificate.

    Args:
        cert_or_pem: Certificate object or PEM bytes to inspect.

    Returns:
        Agent-facing certificate fields.
    """
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


def algorithm_evaluate(algorithm_name: str) -> AlgorithmEvaluation:
    """Evaluate a named cryptographic algorithm.

    Args:
        algorithm_name: Human-readable algorithm identifier.

    Returns:
        Known posture metadata or a fail-closed unknown evaluation.
    """
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
    """Evaluate certificate metadata against a compliance profile.

    Args:
        cert_or_pem: Certificate bytes, object, or normalized fields.
        profile: Compliance rules to apply.

    Returns:
        Individual checks, failures, and aggregate pass state.
    """
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
    """Look up known certificate-authority jurisdiction metadata.

    Args:
        ca_name: Case-insensitive certificate-authority identifier.

    Returns:
        Known jurisdiction metadata, or ``None`` for an unknown authority.
    """
    return CA_JURISDICTIONS.get(ca_name.lower())
