"""Elliptic curve cryptography: Weierstrass curves, point ops, ECDH, ECDSA.

Pure-Python, stdlib only. Default curve: secp256k1.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass


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
        return ECPoint(None, None, self)


@dataclass(slots=True)
class ECPoint:
    """A point on an elliptic curve, or the identity (None, None)."""

    x: int | None
    y: int | None
    curve: ECCurve

    def __post_init__(self) -> None:
        if (self.x is None) != (self.y is None):
            raise ECCError("point must have both x and y, or neither (identity)")

    @property
    def is_identity(self) -> bool:
        return self.x is None

    def __eq__(self, other: object) -> bool:
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
        if self.is_identity:
            return self
        y: int = self.y  # type: ignore[assignment]
        return ECPoint(self.x, (-y) % self.curve.p, self.curve)

    def _double(self) -> ECPoint:
        if self.is_identity:
            return self
        x: int = self.x  # type: ignore[assignment]
        y: int = self.y  # type: ignore[assignment]
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
        sx: int = self.x  # type: ignore[assignment]
        sy: int = self.y  # type: ignore[assignment]
        ox: int = other.x  # type: ignore[assignment]
        oy: int = other.y  # type: ignore[assignment]
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
        if not isinstance(other, ECPoint):
            return NotImplemented
        if self.curve.p != other.curve.p:
            raise ECCError("points on different curves")
        return self._add(other)

    def __rmul__(self, scalar: object) -> ECPoint:
        if not isinstance(scalar, int):
            return NotImplemented
        return self * scalar

    def __mul__(self, scalar: int) -> ECPoint:
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
        if self.is_identity:
            return True
        p = self.curve.p
        lhs = (self.y * self.y - self.x * self.x * self.x - self.curve.a * self.x - self.curve.b) % p  # type: ignore[operator]
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


def generate_keypair(curve: ECCurve | None = None) -> ECKeyPair:
    if curve is None:
        curve = SECP256K1
    private = secrets.randbelow(curve.n - 1) + 1
    public = curve.identity
    G = ECPoint(curve.Gx, curve.Gy, curve)
    public = G * private
    return ECKeyPair(private=private, public=public, curve=curve)


def ecdh_shared_secret(private: int, public: ECPoint) -> bytes:
    S = public * private
    if S.is_identity:
        raise ECCError("shared secret is identity point")
    return S.x.to_bytes((S.x.bit_length() + 7) // 8, "big")  # type: ignore[union-attr]


def ecdsa_sign(msg_hash: bytes, private: int, curve: ECCurve | None = None) -> tuple[int, int]:
    if curve is None:
        curve = SECP256K1
    n = curve.n
    G = ECPoint(curve.Gx, curve.Gy, curve)
    z = int.from_bytes(msg_hash, "big") % n
    while True:
        k = _nonce_rfc6979(msg_hash, private, curve)
        R = G * k
        r = R.x % n  # type: ignore[union-attr]
        if r == 0:
            continue
        k_inv = pow(k, -1, n)
        s = (k_inv * (z + r * private)) % n
        if s == 0:
            continue
        break
    return (r, s)


def ecdsa_verify(msg_hash: bytes, signature: tuple[int, int], public: ECPoint) -> bool:
    r, s = signature
    curve = public.curve
    n = curve.n
    if not (1 <= r < n and 1 <= s < n):
        return False
    z = int.from_bytes(msg_hash, "big") % n
    s_inv = pow(s, -1, n)
    u1 = (z * s_inv) % n
    u2 = (r * s_inv) % n
    G = ECPoint(curve.Gx, curve.Gy, curve)
    R = G * u1 + public * u2
    if R.is_identity:
        return False
    return (R.x % n) == r  # type: ignore[union-attr]


def _nonce_rfc6979(msg_hash: bytes, private: int, curve: ECCurve) -> int:
    n = curve.n
    qlen = n.bit_length()
    holen = 32
    rolen = (qlen + 7) // 8
    bx = private.to_bytes(rolen, "big") + msg_hash[:rolen]
    v = b"\x01" * holen
    k = b"\x00" * holen
    k = hmac.new(k, v + b"\x00" + bx, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + bx, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        t = b""
        while len(t) < rolen:
            v = hmac.new(k, v, hashlib.sha256).digest()
            t += v
        k_candidate = int.from_bytes(t[:rolen], "big")
        if 1 <= k_candidate < n:
            return k_candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()
