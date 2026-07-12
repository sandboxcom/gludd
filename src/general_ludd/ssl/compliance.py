from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComplianceProfile:
    name: str
    minimum_key_size: int
    allowed_algorithms: list[str]
    required_key_usage: list[str]
    audit_requirements: list[str]
    version: str = "1.0"
    description: str = ""


@dataclass
class ComplianceResult:
    profile: ComplianceProfile
    compliant: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


FIPS_140_3 = ComplianceProfile(
    name="FIPS_140_3",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "Ed25519", "AES-128", "AES-256", "SHA-256", "SHA-384", "SHA-512"],
    required_key_usage=["digitalSignature", "keyEncipherment"],
    audit_requirements=["FIPS 140-3 validated module", "NIST SP 800-57 key management"],
    description="FIPS 140-3 cryptographic module validation standard",
)

SOC2 = ComplianceProfile(
    name="SOC2",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "AES-128", "AES-256", "SHA-256", "SHA-384", "SHA-512", "Ed25519"],
    required_key_usage=["digitalSignature"],
    audit_requirements=["Annual SOC 2 Type II audit", "Key rotation policy"],
    description="SOC 2 service organization controls",
)

HIPAA = ComplianceProfile(
    name="HIPAA",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "AES-256", "SHA-256", "SHA-384", "SHA-512"],
    required_key_usage=["digitalSignature", "keyEncipherment", "dataEncipherment"],
    audit_requirements=["HIPAA Security Rule compliance", "Annual risk assessment", "BAAs in place"],
    description="HIPAA health information security",
)

PCI_DSS = ComplianceProfile(
    name="PCI_DSS",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "AES-128", "AES-256", "SHA-256", "SHA-384", "SHA-512"],
    required_key_usage=["digitalSignature", "keyEncipherment"],
    audit_requirements=["PCI DSS v4.0 assessment", "Quarterly ASV scans", "Penetration testing"],
    description="PCI DSS payment card data security",
)

FedRAMP = ComplianceProfile(
    name="FedRAMP",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "Ed25519", "AES-128", "AES-256", "SHA-256", "SHA-384", "SHA-512"],
    required_key_usage=["digitalSignature", "keyEncipherment"],
    audit_requirements=["FedRAMP authorization", "FIPS 140-2/3 validated crypto", "Continuous monitoring"],
    description="FedRAMP federal cloud security",
)

ISO_27001 = ComplianceProfile(
    name="ISO_27001",
    minimum_key_size=2048,
    allowed_algorithms=["RSA", "ECDSA", "Ed25519", "AES-128", "AES-256", "SHA-256", "SHA-384", "SHA-512"],
    required_key_usage=["digitalSignature"],
    audit_requirements=["ISO 27001 certification", "Annual surveillance audit", "ISMS documentation"],
    description="ISO/IEC 27001 information security management",
)

_PROFILES: dict[str, ComplianceProfile] = {
    "FIPS_140_3": FIPS_140_3,
    "SOC2": SOC2,
    "HIPAA": HIPAA,
    "PCI_DSS": PCI_DSS,
    "FedRAMP": FedRAMP,
    "ISO_27001": ISO_27001,
}


def get_profile(name: str) -> ComplianceProfile:
    profile = _PROFILES.get(name)
    if profile is None:
        raise ValueError(f"Unknown compliance profile: '{name}'. Available: {list(_PROFILES)}")
    return profile


def list_profiles() -> list[str]:
    return list(_PROFILES.keys())


def check_compliance(cert_info: dict[str, Any], profile: ComplianceProfile) -> ComplianceResult:
    violations: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []

    algorithm: str = cert_info.get("algorithm", "")
    key_size: int = cert_info.get("key_size", 0)
    key_usage: list[str] = cert_info.get("key_usage", [])

    _RSA_LIKE = {"RSA", "DSA"}
    if algorithm in _RSA_LIKE and key_size < profile.minimum_key_size:
        violations.append(
            f"Key size {key_size} below minimum {profile.minimum_key_size} for {profile.name}"
        )

    if algorithm and algorithm not in profile.allowed_algorithms:
        violations.append(
            f"Algorithm '{algorithm}' not in allowed list for {profile.name}: {profile.allowed_algorithms}"
        )

    for usage in profile.required_key_usage:
        if usage not in key_usage:
            violations.append(
                f"Required key usage '{usage}' missing from certificate key_usage: {key_usage}"
            )

    if algorithm == "RSA" and key_size < 2048 and key_size < profile.minimum_key_size:
        recommendations.append(
            f"Upgrade RSA key from {key_size}-bit to at least {profile.minimum_key_size}-bit"
        )

    if algorithm == "ECDSA" and key_size < 256:
        warnings.append(f"ECDSA key size {key_size} is below NIST P-256 minimum")

    cert_sans: list[str] = cert_info.get("sans", [])
    if cert_sans and any(san.startswith("*.") for san in cert_sans):
        warnings.append("Wildcard certificates may not meet strict compliance interpretations")

    compliant = len(violations) == 0

    return ComplianceResult(
        profile=profile,
        compliant=compliant,
        violations=violations,
        warnings=warnings,
        recommendations=recommendations,
    )
