"""Elliptic curve cryptography backed by the `cryptography` library.

Key generation, ECDH shared secret, and ECDSA sign/verify delegate to
``cryptography.hazmat.primitives.asymmetric.ec``.  Point arithmetic
(_double, _add, scalar multiplication) stays pure-Python because
cryptography does not expose raw point operations.
"""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import secrets
from dataclasses import dataclass

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives.asymmetric import ec as _ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)


class ECCError(Exception):
    """Base exception for ECC operations."""


@dataclass(slots=True)
class ECCurve:
    """Weierstrass curve y² ≡ x³ + ax + b (mod p) with generator G of order n."""

    p: int
    a: int
    b: int
    Gx: int
    Gy: int
    n: int

    @property
    def identity(self) -> ECPoint:
        """Execute ``identity``."""
        return ECPoint(None, None, self)


@dataclass(slots=True)
class ECPoint:
    """A point on an elliptic curve, or the identity (None, None)."""

    x: int | None
    y: int | None
    curve: ECCurve

    def __post_init__(self) -> None:
        """Validate the initialized instance."""
        if (self.x is None) != (self.y is None):
            raise ECCError("point must have both x and y, or neither (identity)")

    @property
    def is_identity(self) -> bool:
        """Return whether is identity."""
        return self.x is None

    def _coordinates(self) -> tuple[int, int]:
        """Return finite coordinates, rejecting the identity point."""
        if self.x is None or self.y is None:
            raise ECCError("identity point has no finite coordinates")
        return self.x, self.y

    def __eq__(self, other: object) -> bool:
        """Compare this instance with another value."""
        if not isinstance(other, ECPoint):
            return NotImplemented
        if self.curve.p != other.curve.p:
            return False
        if self.is_identity:
            return other.is_identity
        if other.is_identity:
            return False
        return (
            self.x == other.x and self.y == other.y and self.curve.a == other.curve.a and self.curve.b == other.curve.b
        )

    def __neg__(self) -> ECPoint:
        """Return the negated value."""
        if self.is_identity:
            return self
        x, y = self._coordinates()
        return ECPoint(x, (-y) % self.curve.p, self.curve)

    def _double(self) -> ECPoint:
        if self.is_identity:
            return self
        x, y = self._coordinates()
        p = self.curve.p
        lam = (3 * x * x + self.curve.a) * pow(2 * y, -1, p) % p
        x3 = (lam * lam - 2 * x) % p
        y3 = (lam * (x - x3) - y) % p
        return ECPoint(x3, y3, self.curve)

    def _add(self, other: ECPoint) -> ECPoint:
        if self.is_identity:
            return other
        if other.is_identity:
            return self
        sx, sy = self._coordinates()
        ox, oy = other._coordinates()
        if sx == ox:
            if (sy + oy) % self.curve.p == 0:
                return self.curve.identity
            return self._double()
        p = self.curve.p
        lam = (oy - sy) * pow(ox - sx, -1, p) % p
        x3 = (lam * lam - sx - ox) % p
        y3 = (lam * (sx - x3) - sy) % p
        return ECPoint(x3, y3, self.curve)

    def __add__(self, other: object) -> ECPoint:
        """Add another value."""
        if not isinstance(other, ECPoint):
            return NotImplemented
        if self.curve.p != other.curve.p:
            raise ECCError("points on different curves")
        return self._add(other)

    def __rmul__(self, scalar: object) -> ECPoint:
        """Multiply by another value."""
        if not isinstance(scalar, int):
            return NotImplemented
        return self * scalar

    def __mul__(self, scalar: int) -> ECPoint:
        """Multiply by another value."""
        if scalar == 0:
            return self.curve.identity
        if scalar < 0:
            return (-self) * (-scalar)
        result = self.curve.identity
        addend = self
        k = scalar
        while k:
            if k & 1:
                result = result._add(addend)
            addend = addend._double()
            k >>= 1
        return result

    def on_curve(self) -> bool:
        """Execute ``on_curve``."""
        if self.is_identity:
            return True
        p = self.curve.p
        x, y = self._coordinates()
        lhs = (y * y - x * x * x - self.curve.a * x - self.curve.b) % p
        return lhs == 0


SECP256K1 = ECCurve(
    p=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F,
    a=0,
    b=7,
    Gx=0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    Gy=0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
    n=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141,
)


@dataclass(slots=True)
class ECKeyPair:
    """An elliptic curve key pair."""

    private: int
    public: ECPoint
    curve: ECCurve


def _is_secp256k1(curve: ECCurve) -> bool:
    return (
        curve.p == SECP256K1.p
        and curve.a == SECP256K1.a
        and curve.b == SECP256K1.b
        and curve.Gx == SECP256K1.Gx
        and curve.Gy == SECP256K1.Gy
        and curve.n == SECP256K1.n
    )


def _crypto_curve(curve: ECCurve) -> _ec.EllipticCurve:
    if _is_secp256k1(curve):
        return _ec.SECP256K1()
    raise ECCError(
        "cryptography backend does not support this custom curve; "
        "only secp256k1 is supported by the cryptography backend"
    )


def _private_key_from_int(private_int: int, curve: ECCurve) -> _ec.EllipticCurvePrivateKey:
    crypto_curve = _crypto_curve(curve)
    return _ec.derive_private_key(private_int, crypto_curve, default_backend())


def _public_key_from_point(point: ECPoint) -> _ec.EllipticCurvePublicKey:
    crypto_curve = _crypto_curve(point.curve)
    if point.x is None or point.y is None:
        raise ECCError("cannot convert identity point to public key")
    return _ec.EllipticCurvePublicNumbers(
        point.x,
        point.y,
        crypto_curve,
    ).public_key(default_backend())


def _hash_alg_for_msg(msg_hash: bytes) -> _hashes.HashAlgorithm:
    size = len(msg_hash)
    if size <= 32:
        return _hashes.SHA256()
    if size <= 48:
        return _hashes.SHA384()
    return _hashes.SHA512()


def generate_keypair(curve: ECCurve | None = None) -> ECKeyPair:
    """Generate keypair."""
    if curve is None:
        curve = SECP256K1

    if _is_secp256k1(curve):
        crypto_curve = _ec.SECP256K1()
        priv = _ec.generate_private_key(crypto_curve, default_backend())
        priv_int = priv.private_numbers().private_value
        pub_nums = priv.public_key().public_numbers()
        public = ECPoint(pub_nums.x, pub_nums.y, curve)
        return ECKeyPair(private=priv_int, public=public, curve=curve)

    private = secrets.randbelow(curve.n - 1) + 1
    G = ECPoint(curve.Gx, curve.Gy, curve)
    public = G * private
    return ECKeyPair(private=private, public=public, curve=curve)


def ecdh_shared_secret(private: int, public: ECPoint) -> bytes:
    """Execute ``ecdh_shared_secret``."""
    if _is_secp256k1(public.curve):
        priv_key = _private_key_from_int(private, public.curve)
        pub_key = _public_key_from_point(public)
        return priv_key.exchange(_ec.ECDH(), pub_key)

    S = public * private
    if S.is_identity:
        raise ECCError("shared secret is identity point")
    x, _ = S._coordinates()
    return x.to_bytes((x.bit_length() + 7) // 8, "big")


def ecdsa_sign(msg_hash: bytes, private: int, curve: ECCurve | None = None) -> tuple[int, int]:
    """Execute ``ecdsa_sign``."""
    if curve is None:
        curve = SECP256K1

    if _is_secp256k1(curve):
        priv_key = _private_key_from_int(private, curve)
        hash_alg = _hash_alg_for_msg(msg_hash)
        der_sig = priv_key.sign(msg_hash, _ec.ECDSA(Prehashed(hash_alg)))
        r, s = decode_dss_signature(der_sig)
        return (r, s)

    return _ecdsa_sign_fallback(msg_hash, private, curve)


def ecdsa_verify(msg_hash: bytes, signature: tuple[int, int], public: ECPoint) -> bool:
    """Execute ``ecdsa_verify``."""
    r, s = signature
    curve = public.curve
    n = curve.n

    if not (1 <= r < n and 1 <= s < n):
        return False

    if _is_secp256k1(curve):
        pub_key = _public_key_from_point(public)
        hash_alg = _hash_alg_for_msg(msg_hash)
        der_sig = encode_dss_signature(r, s)
        try:
            pub_key.verify(der_sig, msg_hash, _ec.ECDSA(Prehashed(hash_alg)))
        except Exception:
            return False
        return True

    return _ecdsa_verify_fallback(msg_hash, (r, s), public)


# ── Fallback implementations for non-secp256k1 curves ──────────────────


def _nonce_rfc6979(
    msg_hash: bytes,
    private: int,
    curve: ECCurve,
    candidate_index: int = 0,
) -> int:
    n = curve.n
    qlen = n.bit_length()
    holen = 32
    rolen = (qlen + 7) // 8
    bx = private.to_bytes(rolen, "big") + msg_hash[:rolen]
    v = b"\x01" * holen
    k = b"\x00" * holen
    k = _hmac.new(k, v + b"\x00" + bx, _hashlib.sha256).digest()
    v = _hmac.new(k, v, _hashlib.sha256).digest()
    k = _hmac.new(k, v + b"\x01" + bx, _hashlib.sha256).digest()
    v = _hmac.new(k, v, _hashlib.sha256).digest()
    valid_candidates = 0
    while True:
        t = b""
        while len(t) < rolen:
            v = _hmac.new(k, v, _hashlib.sha256).digest()
            t += v
        k_candidate = int.from_bytes(t[:rolen], "big")
        excess_bits = rolen * 8 - qlen
        if excess_bits > 0:
            k_candidate >>= excess_bits
        if 1 <= k_candidate < n:
            if valid_candidates == candidate_index:
                return k_candidate
            valid_candidates += 1
        k = _hmac.new(k, v + b"\x00", _hashlib.sha256).digest()
        v = _hmac.new(k, v, _hashlib.sha256).digest()


def _ecdsa_sign_fallback(msg_hash: bytes, private: int, curve: ECCurve) -> tuple[int, int]:
    n = curve.n
    G = ECPoint(curve.Gx, curve.Gy, curve)
    z = int.from_bytes(msg_hash, "big") % n
    candidate_index = 0
    while True:
        k = _nonce_rfc6979(msg_hash, private, curve, candidate_index)
        candidate_index += 1
        R = G * k
        assert R.x is not None
        r = R.x % n
        if r == 0:
            continue
        k_inv = pow(k, -1, n)
        s = (k_inv * (z + r * private)) % n
        if s == 0:
            continue
        break
    return (r, s)


def _ecdsa_verify_fallback(msg_hash: bytes, signature: tuple[int, int], public: ECPoint) -> bool:
    r, s = signature
    curve = public.curve
    n = curve.n
    z = int.from_bytes(msg_hash, "big") % n
    s_inv = pow(s, -1, n)
    u1 = (z * s_inv) % n
    u2 = (r * s_inv) % n
    G = ECPoint(curve.Gx, curve.Gy, curve)
    R = G * u1 + public * u2
    if R.is_identity:
        return False
    assert R.x is not None
    return (R.x % n) == r
