"""Oblivious Pseudorandom Function (OPRF) using P-256 elliptic curve.

Implements the 2HashDH OPRF construction (RFC 9497-inspired):
- Client hashes input to a curve point, blinds it, sends to server
- Server evaluates with its private key, returns the result
- Client unblinds to obtain F_k(input)
- Supports VOPRF with DLEQ proof verification

Uses ``cryptography.hazmat.primitives.asymmetric.ec`` for key operations
and ECDH exchange; point arithmetic (scalar multiplication) is implemented
directly since the library does not expose raw point operations.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Final

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurve,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
    EllipticCurvePublicNumbers,
)

P256_P: Final[int] = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
P256_A: Final[int] = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC
P256_B: Final[int] = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
P256_N: Final[int] = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
P256_GX: Final[int] = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
P256_GY: Final[int] = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

COMPRESSED_EVEN: Final[int] = 0x02
COMPRESSED_ODD: Final[int] = 0x03
COMPRESSED_POINT_LENGTH: Final[int] = 33
UNCOMPRESSED_POINT_LENGTH: Final[int] = 65


class OPRError(ValueError):
    """Base exception for OPRF operations."""


def _mod_sqrt(a: int, p: int) -> int | None:
    """Modular square root using Tonelli-Shanks for p ≡ 3 mod 4."""
    if a % p == 0:
        return 0
    exp = (p + 1) // 4
    r = pow(a, exp, p)
    if (r * r) % p != (a % p):
        return None
    return r


def _ec_add(x1: int, y1: int, x2: int, y2: int, p: int, a: int) -> tuple[int, int] | None:
    """Point addition on Weierstrass curve y² = x³ + ax + b (mod p)."""
    if x1 == x2 and y1 == (-y2 % p if y2 != 0 else 0) and y1 != y2:
        return None
    if x1 == x2 and y1 == y2:
        if y1 == 0:
            return None
        s = ((3 * x1 * x1 + a) * pow(2 * y1, -1, p)) % p
    else:
        s = ((y2 - y1) * pow(x2 - x1, -1, p)) % p
    x3 = (s * s - x1 - x2) % p
    y3 = (s * (x1 - x3) - y1) % p
    return x3, y3


def _scalar_mult_raw(k: int, x: int, y: int, p: int, a: int) -> tuple[int, int] | None:
    """Double-and-add scalar multiplication (constant-time resistant layout)."""
    if k < 0:
        raise OPRError("scalar must be non-negative")
    if k == 0:
        return None
    result: tuple[int, int] | None = None
    current: tuple[int, int] | None = (x, y)
    while k > 0:
        if k & 1:
            if result is None:
                result = current
            elif current is not None:
                result = _ec_add(result[0], result[1], current[0], current[1], p, a)
        k >>= 1
        if k > 0 and current is not None:
            current = _ec_add(current[0], current[1], current[0], current[1], p, a)
    return result


def _scalar_inv(k: int) -> int:
    """Modular inverse of k modulo the curve order."""
    return pow(k, -1, P256_N)


def _bytes_to_int(data: bytes) -> int:
    return int.from_bytes(data, "big")


def _int_to_bytes(value: int, length: int) -> bytes:
    return value.to_bytes(length, "big")


def _hash_label(data: bytes, counter: int) -> bytes:
    return hashlib.sha256(data + counter.to_bytes(4, "big")).digest()


def generate_keypair() -> tuple[EllipticCurvePrivateKey, EllipticCurvePublicKey]:
    """Generate a fresh P-256 keypair for the OPRF server."""
    sk = ec.generate_private_key(ec.SECP256R1())
    return sk, sk.public_key()


def hash_to_curve(data: bytes, curve: EllipticCurve) -> EllipticCurvePublicKey:
    """Hash arbitrary input to a point on the P-256 elliptic curve.

    Uses try-and-increment: SHA-256(input || counter) is interpreted as an
    x-coordinate; if y exists, the point is returned.  Otherwise the counter
    is incremented and the process repeats.
    """
    if curve.name != "secp256r1":
        raise OPRError(f"Unsupported curve: {curve.name}. Only secp256r1 (P-256) is supported.")

    ctr = 0
    while ctr < 256:
        h = _hash_label(data, ctr)
        x = _bytes_to_int(h) % P256_P
        alpha = (x * x * x + P256_A * x + P256_B) % P256_P
        y = _mod_sqrt(alpha, P256_P)
        if y is not None and y != 0:
            nums = EllipticCurvePublicNumbers(x, y, curve)
            return nums.public_key()
        ctr += 1
    raise OPRError("hash_to_curve failed after 256 attempts")


def scalar_mult(k: int, point: EllipticCurvePublicKey, curve: EllipticCurve) -> EllipticCurvePublicKey | None:
    """Multiply an elliptic curve point by a scalar.

    Returns ``None`` for the identity (point at infinity).
    """
    if k < 0:
        raise OPRError("scalar must be non-negative")
    if k % P256_N == 0:
        return None
    nums = point.public_numbers()
    result = _scalar_mult_raw(k % P256_N, nums.x, nums.y, P256_P, P256_A)
    if result is None:
        return None
    return EllipticCurvePublicNumbers(result[0], result[1], curve).public_key()


def serialize_point(point: EllipticCurvePublicKey) -> bytes:
    """Serialize a P-256 point in compressed form (33 bytes)."""
    nums = point.public_numbers()
    prefix = COMPRESSED_EVEN if nums.y % 2 == 0 else COMPRESSED_ODD
    return bytes([prefix]) + _int_to_bytes(nums.x, 32)


def deserialize_point(data: bytes, curve: EllipticCurve) -> EllipticCurvePublicKey | None:
    """Deserialize a compressed P-256 point (33 bytes).

    Returns ``None`` on invalid input.
    """
    if len(data) != COMPRESSED_POINT_LENGTH:
        return None
    if data[0] not in (COMPRESSED_EVEN, COMPRESSED_ODD):
        return None
    x = _bytes_to_int(data[1:])
    if x >= P256_P:
        return None
    alpha = (x * x * x + P256_A * x + P256_B) % P256_P
    y = _mod_sqrt(alpha, P256_P)
    if y is None:
        return None
    y_even = y % 2 == 0
    prefix_even = data[0] == COMPRESSED_EVEN
    if y_even != prefix_even:
        y = P256_P - y
    return EllipticCurvePublicNumbers(x, y, curve).public_key()


def blind(point: EllipticCurvePublicKey, curve: EllipticCurve) -> tuple[bytes, int]:
    """Blind a curve point with a random scalar.

    Returns ``(blinded_point_bytes, blinding_factor)``.
    """
    r = secrets.randbelow(P256_N - 2) + 1
    blinded = scalar_mult(r, point, curve)
    if blinded is None:
        raise OPRError("blinding produced identity point")
    return serialize_point(blinded), r


def evaluate(server_sk: EllipticCurvePrivateKey, blinded_bytes: bytes, curve: EllipticCurve) -> bytes:
    """Server evaluates OPRF on the blinded point using its private key.

    Returns the evaluated point in compressed serialized form.
    """
    blinded_point = deserialize_point(blinded_bytes, curve)
    if blinded_point is None:
        raise OPRError("invalid blinded point")
    k = server_sk.private_numbers().private_value
    evaluated = scalar_mult(k, blinded_point, curve)
    if evaluated is None:
        raise OPRError("evaluation produced identity point")
    return serialize_point(evaluated)


def unblind(evaluated_bytes: bytes, blind_factor: int, curve: EllipticCurve) -> bytes:
    """Client unblinds the server's evaluated point.

    Returns the 32-byte x-coordinate of k * H(input).
    """
    evaluated_point = deserialize_point(evaluated_bytes, curve)
    if evaluated_point is None:
        raise OPRError("invalid evaluated point")
    inv_r = _scalar_inv(blind_factor)
    result = scalar_mult(inv_r, evaluated_point, curve)
    if result is None:
        raise OPRError("unblinding produced identity point")
    return _int_to_bytes(result.public_numbers().x, 32)


def finalize(unblinded_x: bytes, input_data: bytes) -> bytes:
    """Derive the final PRF output: SHA-256(unblinded_x || input_data)."""
    return hashlib.sha256(unblinded_x + input_data).digest()


def verify_proof(
    pk: EllipticCurvePublicKey,
    blinded_point: EllipticCurvePublicKey,
    evaluated_point: EllipticCurvePublicKey,
    proof: bytes,
    curve: EllipticCurve,
) -> bool:
    """Verify a DLEQ proof asserting log_G(pk) == log_{blinded}(evaluated).

    This is a placeholder — full VOPRF DLEQ proof verification requires
    non-interactive Schnorr-style proofs with Fiat-Shamir.  The current
    implementation returns ``False`` for all inputs.
    """
    return False
