from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AlgorithmStatus(Enum):
    CURRENT = "current"
    DEPRECATED = "deprecated"
    LEGACY = "legacy"


class AlgorithmType(Enum):
    RSA = "rsa"
    EC = "ec"
    EDWARDS = "edwards"
    DSA = "dsa"
    DH = "dh"
    SYMMETRIC = "symmetric"
    HASH = "hash"


@dataclass
class AlgorithmInfo:
    name: str
    type: AlgorithmType
    key_sizes: list[int]
    security_bits: int
    status: AlgorithmStatus
    deprecation_date: str | None = None


@dataclass
class AlgorithmEval:
    algorithm: AlgorithmInfo
    score: int  # 0-100, higher = better
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    better: str
    reason: str
    score_difference: int


KNOWN_ALGORITHMS: dict[str, AlgorithmInfo] = {
    "RSA-1024": AlgorithmInfo(
        name="RSA-1024",
        type=AlgorithmType.RSA,
        key_sizes=[1024],
        security_bits=80,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2014-01-01",
    ),
    "RSA-2048": AlgorithmInfo(
        name="RSA-2048",
        type=AlgorithmType.RSA,
        key_sizes=[2048],
        security_bits=112,
        status=AlgorithmStatus.CURRENT,
        deprecation_date="2031-01-01",
    ),
    "RSA-3072": AlgorithmInfo(
        name="RSA-3072",
        type=AlgorithmType.RSA,
        key_sizes=[3072],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "RSA-4096": AlgorithmInfo(
        name="RSA-4096",
        type=AlgorithmType.RSA,
        key_sizes=[4096],
        security_bits=152,
        status=AlgorithmStatus.CURRENT,
    ),
    "ECDSA-P256": AlgorithmInfo(
        name="ECDSA-P256",
        type=AlgorithmType.EC,
        key_sizes=[256],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "ECDSA-P384": AlgorithmInfo(
        name="ECDSA-P384",
        type=AlgorithmType.EC,
        key_sizes=[384],
        security_bits=192,
        status=AlgorithmStatus.CURRENT,
    ),
    "ECDSA-P521": AlgorithmInfo(
        name="ECDSA-P521",
        type=AlgorithmType.EC,
        key_sizes=[521],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "Ed25519": AlgorithmInfo(
        name="Ed25519",
        type=AlgorithmType.EDWARDS,
        key_sizes=[256],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "Ed448": AlgorithmInfo(
        name="Ed448",
        type=AlgorithmType.EDWARDS,
        key_sizes=[456],
        security_bits=224,
        status=AlgorithmStatus.CURRENT,
    ),
    "DSA-1024": AlgorithmInfo(
        name="DSA-1024",
        type=AlgorithmType.DSA,
        key_sizes=[1024],
        security_bits=80,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2014-01-01",
    ),
    "DSA-2048": AlgorithmInfo(
        name="DSA-2048",
        type=AlgorithmType.DSA,
        key_sizes=[2048],
        security_bits=112,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2014-01-01",
    ),
    "SHA-1": AlgorithmInfo(
        name="SHA-1",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=80,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2017-01-01",
    ),
    "SHA-224": AlgorithmInfo(
        name="SHA-224",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=112,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA-256": AlgorithmInfo(
        name="SHA-256",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA-384": AlgorithmInfo(
        name="SHA-384",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=192,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA-512": AlgorithmInfo(
        name="SHA-512",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA3-256": AlgorithmInfo(
        name="SHA3-256",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA3-384": AlgorithmInfo(
        name="SHA3-384",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=192,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHA3-512": AlgorithmInfo(
        name="SHA3-512",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "MD5": AlgorithmInfo(
        name="MD5",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=0,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2008-01-01",
    ),
    "AES-128": AlgorithmInfo(
        name="AES-128",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[128],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "AES-192": AlgorithmInfo(
        name="AES-192",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[192],
        security_bits=192,
        status=AlgorithmStatus.CURRENT,
    ),
    "AES-256": AlgorithmInfo(
        name="AES-256",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[256],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "3DES": AlgorithmInfo(
        name="3DES",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[168],
        security_bits=112,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2023-12-31",
    ),
    "RC4": AlgorithmInfo(
        name="RC4",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[40, 56, 128],
        security_bits=0,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2015-01-01",
    ),
    "ChaCha20-Poly1305": AlgorithmInfo(
        name="ChaCha20-Poly1305",
        type=AlgorithmType.SYMMETRIC,
        key_sizes=[256],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "DH-1024": AlgorithmInfo(
        name="DH-1024",
        type=AlgorithmType.DH,
        key_sizes=[1024],
        security_bits=80,
        status=AlgorithmStatus.LEGACY,
        deprecation_date="2014-01-01",
    ),
    "DH-2048": AlgorithmInfo(
        name="DH-2048",
        type=AlgorithmType.DH,
        key_sizes=[2048],
        security_bits=112,
        status=AlgorithmStatus.CURRENT,
    ),
    "DH-3072": AlgorithmInfo(
        name="DH-3072",
        type=AlgorithmType.DH,
        key_sizes=[3072],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "DH-4096": AlgorithmInfo(
        name="DH-4096",
        type=AlgorithmType.DH,
        key_sizes=[4096],
        security_bits=152,
        status=AlgorithmStatus.CURRENT,
    ),
    "X25519": AlgorithmInfo(
        name="X25519",
        type=AlgorithmType.EDWARDS,
        key_sizes=[256],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "X448": AlgorithmInfo(
        name="X448",
        type=AlgorithmType.EDWARDS,
        key_sizes=[448],
        security_bits=224,
        status=AlgorithmStatus.CURRENT,
    ),
    "RSA-7680": AlgorithmInfo(
        name="RSA-7680",
        type=AlgorithmType.RSA,
        key_sizes=[7680],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "RSA-15360": AlgorithmInfo(
        name="RSA-15360",
        type=AlgorithmType.RSA,
        key_sizes=[15360],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHAKE128": AlgorithmInfo(
        name="SHAKE128",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=128,
        status=AlgorithmStatus.CURRENT,
    ),
    "SHAKE256": AlgorithmInfo(
        name="SHAKE256",
        type=AlgorithmType.HASH,
        key_sizes=[],
        security_bits=256,
        status=AlgorithmStatus.CURRENT,
    ),
}

COMPLIANCE_STANDARDS: dict[str, dict[str, bool]] = {
    "FIPS-140-3": {
        "RSA-1024": False,
        "RSA-2048": True,
        "RSA-3072": True,
        "RSA-4096": True,
        "ECDSA-P256": True,
        "ECDSA-P384": True,
        "ECDSA-P521": True,
        "Ed25519": True,
        "Ed448": True,
        "DSA-1024": False,
        "DSA-2048": False,
        "SHA-1": False,
        "SHA-224": True,
        "SHA-256": True,
        "SHA-384": True,
        "SHA-512": True,
        "SHA3-256": True,
        "SHA3-384": True,
        "SHA3-512": True,
        "MD5": False,
        "AES-128": True,
        "AES-192": True,
        "AES-256": True,
        "3DES": False,
        "RC4": False,
        "ChaCha20-Poly1305": False,
        "DH-1024": False,
        "DH-2048": True,
        "DH-3072": True,
        "DH-4096": True,
        "X25519": True,
        "X448": True,
        "RSA-7680": True,
        "RSA-15360": True,
        "SHAKE128": True,
        "SHAKE256": True,
    },
    "SOC2": {
        "RSA-1024": False,
        "RSA-2048": True,
        "RSA-3072": True,
        "RSA-4096": True,
        "ECDSA-P256": True,
        "ECDSA-P384": True,
        "ECDSA-P521": True,
        "Ed25519": True,
        "Ed448": True,
        "DSA-1024": False,
        "DSA-2048": False,
        "SHA-1": False,
        "SHA-224": False,
        "SHA-256": True,
        "SHA-384": True,
        "SHA-512": True,
        "SHA3-256": True,
        "SHA3-384": True,
        "SHA3-512": True,
        "MD5": False,
        "AES-128": True,
        "AES-192": True,
        "AES-256": True,
        "3DES": False,
        "RC4": False,
        "ChaCha20-Poly1305": True,
        "DH-1024": False,
        "DH-2048": True,
        "DH-3072": True,
        "DH-4096": True,
        "X25519": True,
        "X448": True,
        "RSA-7680": True,
        "RSA-15360": True,
        "SHAKE128": True,
        "SHAKE256": True,
    },
    "HIPAA": {
        "RSA-1024": False,
        "RSA-2048": True,
        "RSA-3072": True,
        "RSA-4096": True,
        "ECDSA-P256": True,
        "ECDSA-P384": True,
        "ECDSA-P521": True,
        "Ed25519": True,
        "Ed448": True,
        "DSA-1024": False,
        "DSA-2048": False,
        "SHA-1": False,
        "SHA-224": False,
        "SHA-256": True,
        "SHA-384": True,
        "SHA-512": True,
        "SHA3-256": True,
        "SHA3-384": True,
        "SHA3-512": True,
        "MD5": False,
        "AES-128": True,
        "AES-192": True,
        "AES-256": True,
        "3DES": False,
        "RC4": False,
        "ChaCha20-Poly1305": True,
        "DH-1024": False,
        "DH-2048": True,
        "DH-3072": True,
        "DH-4096": True,
        "X25519": True,
        "RSA-7680": True,
        "RSA-15360": True,
        "SHAKE128": True,
        "SHAKE256": True,
    },
    "PCI-DSS": {
        "RSA-1024": False,
        "RSA-2048": True,
        "RSA-3072": True,
        "RSA-4096": True,
        "ECDSA-P256": True,
        "ECDSA-P384": True,
        "ECDSA-P521": True,
        "Ed25519": True,
        "Ed448": True,
        "DSA-1024": False,
        "DSA-2048": False,
        "SHA-1": False,
        "SHA-224": False,
        "SHA-256": True,
        "SHA-384": True,
        "SHA-512": True,
        "SHA3-256": True,
        "SHA3-384": True,
        "SHA3-512": True,
        "MD5": False,
        "AES-128": True,
        "AES-192": True,
        "AES-256": True,
        "3DES": False,
        "RC4": False,
        "ChaCha20-Poly1305": True,
        "DH-1024": False,
        "DH-2048": True,
        "DH-3072": True,
        "DH-4096": True,
        "X25519": True,
        "X448": True,
        "RSA-7680": True,
        "RSA-15360": True,
        "SHAKE128": True,
        "SHAKE256": True,
    },
}


def evaluate_algorithm(name: str, key_size: int | None = None) -> AlgorithmEval:
    if name not in KNOWN_ALGORITHMS:
        raise ValueError(f"Unknown algorithm: {name}")

    algo = KNOWN_ALGORITHMS[name]
    warnings: list[str] = []
    recommendations: list[str] = []
    score = 0

    if algo.security_bits == 0:
        score = 0
        warnings.append(f"{algo.name} provides no meaningful security")
    else:
        if algo.status == AlgorithmStatus.CURRENT:
            score += 50
        elif algo.status == AlgorithmStatus.DEPRECATED:
            score += 30
        elif algo.status == AlgorithmStatus.LEGACY:
            score += 10

        sec_score = min(40, algo.security_bits // 6)
        score += sec_score

        if algo.security_bits < 112:
            warnings.append(
                f"Security bits ({algo.security_bits}) below NIST minimum (112)"
            )
            recommendations.append(
                "Upgrade to an algorithm with >=112 security bits"
            )
        elif algo.security_bits < 128:
            warnings.append(
                f"Security bits ({algo.security_bits}) below post-quantum safe threshold (128)"
            )
            recommendations.append(
                "Consider 128+ security bits for long-term protection"
            )

        if algo.type == AlgorithmType.EDWARDS and algo.status == AlgorithmStatus.CURRENT:
            score += 10

        if algo.type in (AlgorithmType.RSA, AlgorithmType.DSA, AlgorithmType.DH) and algo.security_bits < 112:
            recommendations.append(
                f"For {algo.type.value.upper()}, use 2048-bit keys minimum"
            )

    if algo.status == AlgorithmStatus.LEGACY:
        # Legacy algorithms are retained for migration diagnostics only. Keep
        # their score below the minimum acceptable deployment threshold even
        # when their nominal security-bit estimate is relatively high.
        score = min(score, 20)
        warnings.insert(
            0, f"{algo.name} is a legacy algorithm"
        )
        if algo.deprecation_date:
            warnings.append(f"Deprecated since {algo.deprecation_date}")
        recommendations.append(
            f"Do not use {algo.name} — migrate to a current alternative"
        )
    elif algo.status == AlgorithmStatus.DEPRECATED:
        warnings.insert(0, f"{algo.name} is deprecated")
        if algo.deprecation_date:
            warnings.append(f"Sunset scheduled: {algo.deprecation_date}")
        recommendations.append(f"Plan migration away from {algo.name}")

    if key_size is not None and key_size not in algo.key_sizes:
        warnings.append(
            f"Key size {key_size} is not a standard key size for {algo.name}"
        )

    # Legacy status is a deployment-level disqualifier. Raw security-bit
    # credit must not make a retired algorithm appear moderately acceptable.
    if algo.status == AlgorithmStatus.LEGACY:
        score = min(score, 20)

    score = max(score, 0)
    score = min(score, 100)

    return AlgorithmEval(
        algorithm=algo,
        score=score,
        warnings=warnings,
        recommendations=recommendations,
    )


def compare_algorithms(a_name: str, b_name: str) -> ComparisonResult:
    if a_name not in KNOWN_ALGORITHMS:
        raise ValueError(f"Unknown algorithm: {a_name}")
    if b_name not in KNOWN_ALGORITHMS:
        raise ValueError(f"Unknown algorithm: {b_name}")

    a_eval = evaluate_algorithm(a_name)
    b_eval = evaluate_algorithm(b_name)
    diff = a_eval.score - b_eval.score

    if diff == 0:
        return ComparisonResult(
            better="equal",
            reason=f"{a_name} and {b_name} have equivalent security scores ({a_eval.score})",
            score_difference=0,
        )

    better_name = a_name if diff > 0 else b_name
    abs_diff = abs(diff)

    reason_parts: list[str] = []

    if a_eval.algorithm.security_bits != b_eval.algorithm.security_bits:
        reason_parts.append(
            f"{better_name} has higher security bits "
            f"({a_eval.algorithm.security_bits if diff > 0 else b_eval.algorithm.security_bits} "
            f"vs {b_eval.algorithm.security_bits if diff > 0 else a_eval.algorithm.security_bits})"
        )

    if a_eval.algorithm.status != b_eval.algorithm.status:
        status_val = (
            a_eval.algorithm.status.value if diff > 0
            else b_eval.algorithm.status.value
        )
        reason_parts.append(f"{better_name} status is {status_val}")

    if not reason_parts:
        winner_score = a_eval.score if diff > 0 else b_eval.score
        loser_score = b_eval.score if diff > 0 else a_eval.score
        reason_parts.append(
            f"{better_name} scores higher ({winner_score} vs {loser_score})"
        )

    reason = "; ".join(reason_parts)

    return ComparisonResult(
        better=better_name,
        reason=reason,
        score_difference=abs_diff,
    )


def compliance_check(algorithm_name: str, standard: str) -> bool:
    standard_key = standard.upper()
    if standard_key not in COMPLIANCE_STANDARDS:
        raise ValueError(f"Unknown compliance standard: {standard}")

    if algorithm_name not in COMPLIANCE_STANDARDS[standard_key]:
        raise ValueError(
            f"Algorithm {algorithm_name} not evaluated for {standard_key}"
        )

    return COMPLIANCE_STANDARDS[standard_key][algorithm_name]
