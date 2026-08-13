"""FROST and tECDSA threshold signatures backed by the ``cryptography`` library.

FROST (Flexible Round-Optimized Schnorr Threshold Signatures, RFC 9591):
  t-of-n threshold Schnorr signatures over secp256k1 and secp256r1.
  Two-round signing: commit -> sign_share -> aggregate.

tECDSA (Threshold ECDSA, trusted-dealer / aggregator-reconstruct model):
  t-of-n threshold ECDSA over secp256k1 and secp256r1.
  Shares the secret key via Shamir polynomial; the aggregator reconstructs
  nonce and key from additive/Lagrange shares to produce a standard ECDSA sig.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature,
)

_SECP256K1 = ec.SECP256K1()
_SECP256R1 = ec.SECP256R1()

_K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_R1_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

_K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_K1_A = 0
_K1_B = 7
_K1_Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_K1_Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

_R1_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_R1_A = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC
_R1_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_R1_Gx = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_R1_Gy = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5


def _curve_order(curve: ec.EllipticCurve) -> int:
    if isinstance(curve, ec.SECP256K1):
        return _K1_ORDER
    if isinstance(curve, ec.SECP256R1):
        return _R1_ORDER
    raise ThresholdError(f"Unsupported curve: {type(curve).__name__}")


def _curve_params(
    curve: ec.EllipticCurve,
) -> tuple[int, int, int, int, int]:
    if isinstance(curve, ec.SECP256K1):
        return (_K1_P, _K1_A, _K1_B, _K1_Gx, _K1_Gy)
    if isinstance(curve, ec.SECP256R1):
        return (_R1_P, _R1_A, _R1_B, _R1_Gx, _R1_Gy)
    raise ThresholdError(f"Unsupported curve: {type(curve).__name__}")


def _mod_inv(a: int, m: int) -> int:
    return pow(a, -1, m)


def _int_from_hash(data: bytes, order: int) -> int:
    return int.from_bytes(data, "big") % order


def _pubkey_to_point(pk: ec.EllipticCurvePublicKey) -> tuple[int, int]:
    nums = pk.public_numbers()
    return (nums.x, nums.y)


# ---- Point arithmetic (Weierstrass curve) ----


def _point_add(
    p1: tuple[int, int],
    p2: tuple[int, int],
    p: int,
    a: int,
) -> tuple[int, int]:
    x1, y1 = p1
    x2, y2 = p2
    if x1 == 0 and y1 == 0:
        return p2
    if x2 == 0 and y2 == 0:
        return p1
    if x1 == x2:
        if (y1 + y2) % p == 0:
            return (0, 0)
        lam = ((3 * x1 * x1 + a) * _mod_inv(2 * y1, p)) % p
    else:
        lam = ((y2 - y1) * _mod_inv(x2 - x1, p)) % p
    x3 = (lam * lam - x1 - x2) % p
    y3 = (lam * (x1 - x3) - y1) % p
    return (x3, y3)


def _point_mul(
    k: int,
    pt: tuple[int, int],
    p: int,
    a: int,
) -> tuple[int, int]:
    if k == 0:
        return (0, 0)
    if k < 0:
        return _point_mul(-k, (pt[0], (-pt[1]) % p), p, a)
    result = (0, 0)
    addend = pt
    while k:
        if k & 1:
            result = _point_add(result, addend, p, a)
        addend = _point_add(addend, addend, p, a)
        k >>= 1
    return result


def _scalar_mult_curve(k: int, curve: ec.EllipticCurve) -> tuple[int, int]:
    p, a, _b, gx, gy = _curve_params(curve)
    return _point_mul(k % _curve_order(curve), (gx, gy), p, a)


def _compressed_point_bytes(x: int, y: int) -> bytes:
    prefix = b"\x02" if y % 2 == 0 else b"\x03"
    return prefix + x.to_bytes(32, "big")


# ---- Public API types ----


class ThresholdError(Exception):
    """Base exception for threshold signature operations."""


@dataclass(slots=True)
class ThresholdKeyShare:
    """A single participant's key material in a threshold scheme."""

    index: int
    share: int
    verification_key: ec.EllipticCurvePublicKey


@dataclass(slots=True)
class ThresholdKeyGenResult:
    """Output of threshold key generation (trusted dealer)."""

    group_public_key: ec.EllipticCurvePublicKey
    group_private_key: ec.EllipticCurvePrivateKey | None
    threshold: int
    num_participants: int
    shares: dict[int, ThresholdKeyShare]
    curve: ec.EllipticCurve


# ---- Polynomial operations ----


def _poly_eval(coeffs: list[int], x: int, order: int) -> int:
    result = 0
    x_pow = 1
    for c in coeffs:
        result = (result + c * x_pow) % order
        x_pow = (x_pow * x) % order
    return result


def _lagrange_coeff(i: int, participants: list[int], order: int) -> int:
    num = 1
    den = 1
    for j in participants:
        if j == i:
            continue
        num = (num * j) % order
        den = (den * (j - i)) % order
    return (num * _mod_inv(den, order)) % order


# ---- Threshold key generation ----


def generate_threshold_keys(
    threshold: int,
    num_participants: int,
    curve: ec.EllipticCurve = _SECP256K1,
) -> ThresholdKeyGenResult:
    """Generate threshold key material using a trusted dealer (Shamir polynomial).

    Args:
        threshold: Minimum number of shares needed to sign (t).
        num_participants: Total number of participants (n).
        curve: Elliptic curve to use.

    Returns:
        ThresholdKeyGenResult with group key and per-participant shares.
    """
    if threshold < 1 or threshold > num_participants:
        raise ThresholdError(f"threshold ({threshold}) must be >=1 and <= num_participants ({num_participants})")

    order = _curve_order(curve)
    group_sk = ec.generate_private_key(curve, default_backend())
    group_pk = group_sk.public_key()
    secret = int.from_bytes(group_sk.private_numbers().private_value.to_bytes(32, "big"), "big")
    secret = secret % order

    coeffs = [secret] + [secrets.randbelow(order) for _ in range(1, threshold)]
    shares: dict[int, ThresholdKeyShare] = {}
    for i in range(1, num_participants + 1):
        si = _poly_eval(coeffs, i, order)
        si_bytes = si.to_bytes(32, "big")
        vk_sk = ec.derive_private_key(int.from_bytes(si_bytes, "big"), curve, default_backend())
        shares[i] = ThresholdKeyShare(index=i, share=si, verification_key=vk_sk.public_key())

    return ThresholdKeyGenResult(
        group_public_key=group_pk,
        group_private_key=group_sk,
        threshold=threshold,
        num_participants=num_participants,
        shares=shares,
        curve=curve,
    )


# ═══════════════════════════════════════════════════════════════════════════
# FROST — Flexible Round-Optimized Schnorr Threshold Signatures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class FrostCommitment:
    """Round-1 commitment from a FROST participant."""

    index: int
    D: tuple[int, int]
    E: tuple[int, int]
    d: int
    e: int


def frost_commit(
    share: ThresholdKeyShare,
    curve: ec.EllipticCurve = _SECP256K1,
) -> FrostCommitment:
    """FROST Round 1: generate nonce pair (d, e) and commitment (D, E)."""
    order = _curve_order(curve)
    d = secrets.randbelow(order)
    e = secrets.randbelow(order)
    D = _scalar_mult_curve(d, curve)
    E = _scalar_mult_curve(e, curve)
    return FrostCommitment(index=share.index, D=D, E=E, d=d, e=e)


def _hash_binding_factors(
    msg: bytes,
    commitments: list[FrostCommitment],
    order: int,
) -> dict[int, int]:
    encoded = b"".join(
        ci.index.to_bytes(8, "big") + _compressed_point_bytes(*ci.D) + _compressed_point_bytes(*ci.E)
        for ci in sorted(commitments, key=lambda c: c.index)
    )
    rho: dict[int, int] = {}
    for ci in commitments:
        h = hashlib.sha256(ci.index.to_bytes(8, "big") + msg + encoded).digest()
        rho[ci.index] = _int_from_hash(h, order)
    return rho


def _compute_group_commitment(
    commitments: list[FrostCommitment],
    binding_factors: dict[int, int],
    curve: ec.EllipticCurve,
) -> tuple[int, int]:
    p, a, _b, _gx, _gy = _curve_params(curve)
    R: tuple[int, int] = (0, 0)
    for ci in commitments:
        R = _point_add(R, ci.D, p, a)
    rho_E_sum: tuple[int, int] = (0, 0)
    for ci in commitments:
        rho = binding_factors[ci.index]
        scaled = _point_mul(rho, ci.E, p, a)
        rho_E_sum = _point_add(rho_E_sum, scaled, p, a)
    R = _point_add(R, rho_E_sum, p, a)
    return R


def _frost_challenge(
    R_bytes: bytes,
    group_pk: ec.EllipticCurvePublicKey,
    msg: bytes,
    curve: ec.EllipticCurve,
) -> int:
    order = _curve_order(curve)
    pk_pt = _pubkey_to_point(group_pk)
    pk_enc = _compressed_point_bytes(pk_pt[0], pk_pt[1])
    h = hashlib.sha256(R_bytes + pk_enc + msg).digest()
    return _int_from_hash(h, order)


def frost_sign_share(
    share: ThresholdKeyShare,
    msg: bytes,
    commitments: list[FrostCommitment],
    group_pk: ec.EllipticCurvePublicKey,
    binding_factors: dict[int, int] | None = None,
    curve: ec.EllipticCurve = _SECP256K1,
) -> int:
    """FROST Round 2: compute signature share z_i = d_i + rho_i * e_i + lambda_i * s_i * c."""
    order = _curve_order(curve)
    if binding_factors is None:
        binding_factors = _hash_binding_factors(msg, commitments, order)

    own = next(c for c in commitments if c.index == share.index)
    rho_i = binding_factors[share.index]
    indices = sorted([c.index for c in commitments])
    lambda_i = _lagrange_coeff(share.index, indices, order)

    R = _compute_group_commitment(commitments, binding_factors, curve)
    R_bytes = _compressed_point_bytes(*R)
    c_val = _frost_challenge(R_bytes, group_pk, msg, curve)

    z_i = (own.d + rho_i * own.e + lambda_i * share.share * c_val) % order
    return z_i


def frost_aggregate(
    commitments: list[FrostCommitment],
    binding_factors: dict[int, int],
    sign_shares: dict[int, int],
    curve: ec.EllipticCurve = _SECP256K1,
) -> tuple[bytes, int]:
    """Aggregate FROST signature shares. Returns (R_bytes, z)."""
    order = _curve_order(curve)
    R = _compute_group_commitment(commitments, binding_factors, curve)
    R_bytes = _compressed_point_bytes(*R)
    z = sum(sign_shares.values()) % order
    return R_bytes, z


def frost_verify(
    group_pk: ec.EllipticCurvePublicKey,
    msg: bytes,
    signature: tuple[bytes, int],
    curve: ec.EllipticCurve = _SECP256K1,
) -> bool:
    """Verify a FROST Schnorr threshold signature: z*G == R + c*PK."""
    R_bytes, z = signature
    order = _curve_order(curve)
    p, a, _b, gx, gy = _curve_params(curve)

    c_val = _frost_challenge(R_bytes, group_pk, msg, curve)

    zG = _point_mul(z % order, (gx, gy), p, a)

    pk_pt = _pubkey_to_point(group_pk)
    cPk = _point_mul(c_val, pk_pt, p, a)

    prefix = R_bytes[0:1]
    x_bytes = R_bytes[1:33]
    x_R = int.from_bytes(x_bytes, "big")
    y_sq = (pow(x_R, 3, p) + a * x_R + _b) % p
    y_R = pow(y_sq, (p + 1) // 4, p)
    if (y_R & 1) != (prefix == b"\x03"):
        y_R = (p - y_R) % p
    R = (x_R, y_R)
    R_plus_cPk = _point_add(R, cPk, p, a)

    return zG == R_plus_cPk


def frost_sign(
    group: ThresholdKeyGenResult,
    msg: bytes,
    participant_indices: list[int] | None = None,
) -> tuple[bytes, int]:
    """Full FROST signing protocol. Returns (R_bytes, z)."""
    if participant_indices is None:
        participant_indices = list(range(1, group.threshold + 1))
    if len(participant_indices) < group.threshold:
        raise ThresholdError(f"Need at least {group.threshold} participants, got {len(participant_indices)}")

    selected = [i for i in participant_indices if i in group.shares][: group.threshold]
    if len(selected) < group.threshold:
        raise ThresholdError(f"Only {len(selected)} valid participants; need {group.threshold}")

    curve = group.curve
    order = _curve_order(curve)
    commitments = [frost_commit(group.shares[idx], curve) for idx in selected]
    binding_factors = _hash_binding_factors(msg, commitments, order)

    sign_shares: dict[int, int] = {}
    for idx in selected:
        z_i = frost_sign_share(
            group.shares[idx],
            msg,
            commitments,
            group.group_public_key,
            binding_factors,
            curve,
        )
        sign_shares[idx] = z_i

    return frost_aggregate(commitments, binding_factors, sign_shares, curve)


# ═══════════════════════════════════════════════════════════════════════════
# tECDSA — Threshold ECDSA (trusted-dealer aggregator model)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class TEcdsaShare:
    """A participant's share for tECDSA signing."""

    index: int
    x_i: int
    X_i: ec.EllipticCurvePublicKey


def tedcsa_keygen(
    threshold: int,
    num_participants: int,
    curve: ec.EllipticCurve = _SECP256K1,
) -> tuple[ec.EllipticCurvePublicKey, dict[int, TEcdsaShare]]:
    """Generate tECDSA key shares using a trusted dealer.

    Returns (group_public_key, {index: TEcdsaShare}).
    """
    order = _curve_order(curve)
    sk = ec.generate_private_key(curve, default_backend())
    pk = sk.public_key()
    x = int.from_bytes(sk.private_numbers().private_value.to_bytes(32, "big"), "big") % order

    coeffs = [x] + [secrets.randbelow(order) for _ in range(1, threshold)]
    shares_dict: dict[int, TEcdsaShare] = {}
    for i in range(1, num_participants + 1):
        xi = _poly_eval(coeffs, i, order)
        xi_sk = ec.derive_private_key(xi, curve, default_backend())
        shares_dict[i] = TEcdsaShare(index=i, x_i=xi, X_i=xi_sk.public_key())

    return pk, shares_dict


@dataclass(slots=True)
class TEcdsaCommitment:
    """Round-1 nonce commitment for tECDSA."""

    index: int
    k_i: int
    R_i: tuple[int, int]


def tedcsa_commit(
    share: TEcdsaShare,
    curve: ec.EllipticCurve = _SECP256K1,
) -> TEcdsaCommitment:
    """tECDSA Round 1: generate nonce k_i and commitment R_i = k_i * G."""
    order = _curve_order(curve)
    k_i = secrets.randbelow(order)
    R_i = _scalar_mult_curve(k_i, curve)
    return TEcdsaCommitment(index=share.index, k_i=k_i, R_i=R_i)


def tedcsa_sign(
    shares: dict[int, TEcdsaShare],
    msg: bytes,
    participant_indices: list[int] | None = None,
    threshold: int = 1,
    curve: ec.EllipticCurve = _SECP256K1,
) -> bytes:
    """Full tECDSA signing protocol (trusted-aggregator model).

    The aggregator collects all nonce commitments k_i from each participant
    and reconstructs k = Σ k_i. It also reconstructs the secret key x from
    Lagrange-interpolated shares. Then it produces a standard ECDSA signature:
        s = k^(-1) * (hash(msg) + r * x) mod order

    Returns DER-encoded ECDSA signature bytes.
    """
    if participant_indices is None:
        participant_indices = sorted(shares.keys())[:threshold]
    if len(participant_indices) < threshold:
        raise ThresholdError(f"Need at least {threshold} participants, got {len(participant_indices)}")

    selected = [i for i in participant_indices if i in shares][:threshold]
    order = _curve_order(curve)
    p, a, _b, _gx, _gy = _curve_params(curve)

    commitments = [tedcsa_commit(shares[i], curve) for i in selected]
    indices = sorted([c.index for c in commitments])

    k = sum(c.k_i for c in commitments) % order
    inv_k = _mod_inv(k, order)

    R_agg: tuple[int, int] = (0, 0)
    for c in commitments:
        R_agg = _point_add(R_agg, c.R_i, p, a)
    r = R_agg[0] % order
    if r == 0:
        raise ThresholdError("Invalid signature: r == 0 (retry with different nonces)")

    x = sum(_lagrange_coeff(i, indices, order) * shares[i].x_i for i in indices) % order

    m_val = _int_from_hash(hashlib.sha256(msg).digest(), order)
    s = (inv_k * (m_val + r * x)) % order
    if s == 0:
        raise ThresholdError("Invalid signature: s == 0 (retry with different nonces)")

    return encode_dss_signature(r, s)


def tedcsa_verify(
    group_pk: ec.EllipticCurvePublicKey,
    msg: bytes,
    signature: bytes,
    curve: ec.EllipticCurve = _SECP256K1,
) -> bool:
    """Verify a tECDSA threshold signature using standard ECDSA verification."""
    try:
        group_pk.verify(signature, msg, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False
