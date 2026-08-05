"""Ed25519 digital signatures (RFC 8032): key generation, signing, verification.

Pure-Python, stdlib only.  Uses extended twisted Edwards coordinates.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

P = 2**255 - 19
D = 37095705934669439343138083508754565189542113879843219016388785533085940283555
Q = 2**252 + 27742317777372353535851937790883648493

Bx = 15112221349535800772501151409588531511454012693041857206046113283949847762202
By = 46316835694926478169428394003475163141307993866256225615783033603165251855960

SQRT_M1 = pow(2, (P - 1) // 4, P)


class Ed25519Error(Exception):
    pass


def modinv(a: int) -> int:
    return pow(a, P - 2, P)


def xrecover(y: int) -> int:
    xx = (y * y - 1) * modinv(D * y * y + 1)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * SQRT_M1) % P
    if x % 2 != 0:
        x = P - x
    return x


@dataclass(slots=True)
class EDPoint:
    x: int
    y: int
    z: int
    t: int

    @staticmethod
    def identity() -> EDPoint:
        return EDPoint(0, 1, 1, 0)

    def is_identity(self) -> bool:
        return self.x == 0 and self.y == self.z

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EDPoint):
            return NotImplemented
        xn = (self.x * other.z) % P
        yn = (self.y * other.z) % P
        xo = (other.x * self.z) % P
        yo = (other.y * self.z) % P
        return xn == xo and yn == yo

    def _affine(self) -> tuple[int, int]:
        if self.z == 0:
            return (0, 0)
        zinv = modinv(self.z)
        return ((self.x * zinv) % P, (self.y * zinv) % P)

    def __neg__(self) -> EDPoint:
        return EDPoint((-self.x) % P, self.y, self.z, (-self.t) % P)

    def __add__(self, other: object) -> EDPoint:
        if not isinstance(other, EDPoint):
            return NotImplemented
        if self.is_identity():
            return other
        if other.is_identity():
            return self

        x1, y1, z1, t1 = self.x, self.y, self.z, self.t
        x2, y2, z2, t2 = other.x, other.y, other.z, other.t

        A = (y1 - x1) * (y2 - x2) % P
        B = (y1 + x1) * (y2 + x2) % P
        C = t1 * 2 * D * t2 % P
        Dcoord = z1 * 2 * z2 % P
        E = B - A
        F = Dcoord - C
        G = Dcoord + C
        H = B + A

        return EDPoint((E * F) % P, (G * H) % P, (F * G) % P, (E * H) % P)

    def _double(self) -> EDPoint:
        if self.is_identity():
            return self
        x1, y1, z1 = self.x, self.y, self.z

        A = x1 * x1 % P
        B = y1 * y1 % P
        C = 2 * z1 * z1 % P
        D = -A
        E = (x1 + y1) * (x1 + y1) - A - B
        G = D + B
        F = G - C
        H = D - B

        return EDPoint((E * F) % P, (G * H) % P, (F * G) % P, (E * H) % P)

    def __mul__(self, scalar: int) -> EDPoint:
        if scalar == 0:
            return EDPoint.identity()
        if scalar < 0:
            return (-self) * (-scalar)
        result = EDPoint.identity()
        addend = self
        while scalar:
            if scalar & 1:
                result = result + addend
            addend = addend._double()
            scalar >>= 1
        return result

    def __rmul__(self, scalar: int) -> EDPoint:
        return self * scalar


def from_affine(x: int, y: int) -> EDPoint:
    x = x % P
    y = y % P
    return EDPoint(x, y, 1, (x * y) % P)


B = from_affine(Bx, By)


def encode_point(pt: EDPoint) -> bytes:
    zinv = modinv(pt.z)
    x = (pt.x * zinv) % P
    y = (pt.y * zinv) % P
    buf = bytearray(y.to_bytes(32, "little"))
    buf[31] |= (x & 1) << 7
    return bytes(buf)


def decode_point(s: bytes) -> EDPoint:
    if len(s) != 32:
        raise Ed25519Error(f"Point encoding must be 32 bytes, got {len(s)}")
    buf = bytearray(s)
    sign = (buf[31] >> 7) & 1
    buf[31] &= 0x7F
    y = int.from_bytes(buf, "little")
    if y >= P:
        raise Ed25519Error("y coordinate out of range")
    x = xrecover(y)
    if (x & 1) != sign:
        x = P - x
    return from_affine(x, y)


def _scalar_clamp(scalar: bytes) -> int:
    s = bytearray(scalar[:32])
    s[0] &= 248
    s[31] &= 127
    s[31] |= 64
    return int.from_bytes(s, "little")


def _ensure_bytes(data: bytes | str) -> bytes:
    return data.encode() if isinstance(data, str) else data


@dataclass(slots=True)
class Ed25519KeyPair:
    private_bytes: bytes
    public_bytes: bytes
    secret_scalar: int
    public_point: EDPoint

    @classmethod
    def generate(cls) -> Ed25519KeyPair:
        return cls.from_seed(secrets.token_bytes(32))

    @classmethod
    def from_seed(cls, seed: bytes) -> Ed25519KeyPair:
        if len(seed) != 32:
            raise Ed25519Error(f"Seed must be 32 bytes, got {len(seed)}")
        h = hashlib.sha512(seed).digest()
        a = _scalar_clamp(h[:32])
        A = B * a
        return cls(private_bytes=seed, public_bytes=encode_point(A), secret_scalar=a, public_point=A)

    def sign(self, message: bytes | str) -> bytes:
        msg_bytes = _ensure_bytes(message)
        h = hashlib.sha512(self.private_bytes).digest()
        a = _scalar_clamp(h[:32])
        prefix = h[32:]
        r = int.from_bytes(hashlib.sha512(prefix + msg_bytes).digest(), "little") % Q
        R = B * r
        Renc = encode_point(R)
        k = int.from_bytes(hashlib.sha512(Renc + self.public_bytes + msg_bytes).digest(), "little") % Q
        S = (r + k * a) % Q
        return Renc + S.to_bytes(32, "little")


def verify(public_key: bytes, message: bytes | str, signature: bytes) -> bool:
    if len(public_key) != 32:
        return False
    if len(signature) != 64:
        return False
    try:
        A = decode_point(public_key)
    except Ed25519Error:
        return False
    try:
        R = decode_point(signature[:32])
    except Ed25519Error:
        return False
    S = int.from_bytes(signature[32:], "little")
    if S >= Q:
        return False
    msg_bytes = _ensure_bytes(message)
    k = int.from_bytes(hashlib.sha512(signature[:32] + public_key + msg_bytes).digest(), "little") % Q
    SB = B * S
    Rplus = R + A * k
    return Rplus == SB


def derive_public_from_private(private_key: bytes) -> bytes:
    if len(private_key) != 32:
        raise Ed25519Error(f"Private key must be 32 bytes, got {len(private_key)}")
    return Ed25519KeyPair.from_seed(private_key).public_bytes


def generate_keypair() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


def sign(private_key: bytes, message: bytes | str) -> bytes:
    if len(private_key) != 32:
        raise Ed25519Error(f"Private key must be 32 bytes, got {len(private_key)}")
    return Ed25519KeyPair.from_seed(private_key).sign(message)


D_pos = 121665 * modinv(121666) % P


def is_on_curve(x: int, y: int) -> bool:
    lhs = (-x * x + y * y) % P
    rhs = (1 - D_pos * x * x * y * y) % P
    return lhs == rhs


def point_add(p1: EDPoint, p2: EDPoint) -> EDPoint:
    return p1 + p2


def scalar_mult(k: int, p: EDPoint) -> EDPoint:
    return p * k
