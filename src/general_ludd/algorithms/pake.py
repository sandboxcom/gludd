"""PAKE protocols: SPAKE2+ (RFC 9383) and OPAQUE (RFC 9381).

SPAKE2+ is a balanced PAKE where both sides know the password.
OPAQUE is an asymmetric PAKE where the server stores a password verifier.

Implemented atop ``cryptography`` for ECDH, hashing, HKDF, and HMAC;
raw point arithmetic (add, scalar-mul, negation) is computed directly
so SPAKE2+ blinding factors (w0*M, w1*N) can be mixed into public shares.
"""

from __future__ import annotations

import hashlib
import hmac as _stdlib_hmac
import os
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any, Final

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class PAKEError(Exception):
    """Base exception for PAKE operations."""


# ── NIST curve parameters (P-256, P-384, P-521) ──────────────────────────

_P256 = {
    "p": 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
    "a": 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
    "b": 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
    "gx": 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    "gy": 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
    "n": 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
}

_P384 = {
    "p": 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF,
    "a": 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFC,
    "b": 0xB3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875AC656398D8A2ED19D2A85C8EDD3EC2AEF,
    "gx": 0xAA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A385502F25DBF55296C3A545E3872760AB7,
    "gy": 0x3617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C00A60B1CE1D7E819D7A431D7C90EA0E5F,
    "n": 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973,
}

_P521_FIELD_MODULUS: Final[int] = (1 << 521) - 1
_P521_HEX = {
    "b": (
        "0x0051953EB9618E1C9A1F929A21A0B68540EEA2DA725B99B315F3B8B489918EF10"
        "9E156193951EC7E937B1652C0BD3BB1BF073573DF883D2C34F1EF451FD46B503F00"
    ),
    "gx": (
        "0x00C6858E06B70404E9CD9E3ECB662395B4429C648139053FB521F828AF606B4D3D"
        "BAA14B5E77EFE75928FE1DC127A2FFA8DE3348B3C1856A429BF97E7E31C2E5BD66"
    ),
    "gy": (
        "0x011839296A789A3BC0045C8A5FB42C7D1BD998F54449579B446817AFBD17273E66"
        "2C97EE72995EF42640C550B9013FAD0761353C7086A272C24088BE94769FD16650"
    ),
    "n": (
        "0x01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
        "FA51868783BF2F966B7FCC0148F709A5D03BB5C9B8899C47AEBB6FB71E91386409"
    ),
}
_P521: dict[str, int] = {
    "p": _P521_FIELD_MODULUS,
    "a": _P521_FIELD_MODULUS - 3,
    **{k: int(v, 16) for k, v in _P521_HEX.items()},
}

_GROUP_PARAMS: Final[dict[str, dict[str, int]]] = {
    "P-256": _P256,
    "P-384": _P384,
    "P-521": _P521,
}


# ── SPAKE2+ (RFC 9383) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SPAKE2PlusGroup:
    """An elliptic curve group for SPAKE2+."""

    name: str
    hash_name: str
    point_bytes: int
    scalar_bytes: int

    @staticmethod
    def P256() -> SPAKE2PlusGroup:
        """Return the standard P-256 group configuration."""
        return SPAKE2PlusGroup(name="P-256", hash_name="sha256", point_bytes=65, scalar_bytes=32)

    @staticmethod
    def P384() -> SPAKE2PlusGroup:
        """Return the standard P-384 group configuration."""
        return SPAKE2PlusGroup(name="P-384", hash_name="sha384", point_bytes=97, scalar_bytes=48)

    @staticmethod
    def P521() -> SPAKE2PlusGroup:
        """Return the standard P-521 group configuration."""
        return SPAKE2PlusGroup(name="P-521", hash_name="sha512", point_bytes=133, scalar_bytes=66)


def _group_params(group: SPAKE2PlusGroup) -> dict[str, int]:
    if group.name not in _GROUP_PARAMS:
        raise PAKEError(f"Unknown curve: {group.name}")
    return _GROUP_PARAMS[group.name]


def _ec_curve_obj(group: SPAKE2PlusGroup) -> ec.EllipticCurve:
    if group.name == "P-256":
        return ec.SECP256R1()
    if group.name == "P-384":
        return ec.SECP384R1()
    if group.name == "P-521":
        return ec.SECP521R1()
    raise PAKEError(f"Unsupported curve: {group.name}")


def _hkdf_derive(
    ikm: bytes,
    salt: bytes,
    info: bytes,
    length: int,
    hash_name: str,
) -> bytes:
    if hash_name == "sha256":
        algo: hashes.HashAlgorithm = hashes.SHA256()
    elif hash_name == "sha384":
        algo = hashes.SHA384()
    elif hash_name == "sha512":
        algo = hashes.SHA512()
    else:
        raise PAKEError(f"Unsupported hash: {hash_name}")
    return HKDF(algorithm=algo, length=length, salt=salt, info=info).derive(ikm)


def _ec_public_bytes(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )


def _ec_public_from_bytes(data: bytes, group: SPAKE2PlusGroup) -> ec.EllipticCurvePublicKey:
    curve = _ec_curve_obj(group)
    return ec.EllipticCurvePublicKey.from_encoded_point(curve, data)


# ── Raw point arithmetic on NIST curves ──────────────────────────────────


def _mod_inv(a: int, p: int) -> int:
    return pow(a, -1, p)


def _point_neg(x: int, y: int, p: int) -> tuple[int, int]:
    return (x, (-y) % p)


def _point_add(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    a: int,
    p: int,
) -> tuple[int, int]:
    if x1 == 0 and y1 == 0:
        return (x2, y2)
    if x2 == 0 and y2 == 0:
        return (x1, y1)
    if x1 == x2:
        if (y1 + y2) % p == 0:
            return (0, 0)
        return _point_double(x1, y1, a, p)
    lam = ((y2 - y1) * _mod_inv(x2 - x1, p)) % p
    x3 = (lam * lam - x1 - x2) % p
    y3 = (lam * (x1 - x3) - y1) % p
    return (x3, y3)


def _point_double(x: int, y: int, a: int, p: int) -> tuple[int, int]:
    if y == 0:
        return (0, 0)
    lam = ((3 * x * x + a) * _mod_inv(2 * y, p)) % p
    x3 = (lam * lam - 2 * x) % p
    y3 = (lam * (x - x3) - y) % p
    return (x3, y3)


def _point_mul(k: int, x: int, y: int, a: int, p: int) -> tuple[int, int]:
    rx, ry = 0, 0
    bx, by = x % p, y % p
    k = k % p
    while k > 0:
        if k & 1:
            rx, ry = _point_add(rx, ry, bx, by, a, p)
        bx, by = _point_double(bx, by, a, p)
        k >>= 1
    return (rx, ry)


def _point_to_bytes(x: int, y: int, byte_len: int) -> bytes:
    return b"\x04" + x.to_bytes(byte_len, "big") + y.to_bytes(byte_len, "big")


_HASH_TO_POINT_MAX_ATTEMPTS: Final[int] = 256


def _hash_to_point(data: bytes, params: dict[str, int], byte_len: int) -> tuple[int, int]:
    """Map a digest to a curve point with a bounded try-and-increment search."""
    p = params["p"]
    a = params["a"]
    b = params["b"]

    for ctr in range(_HASH_TO_POINT_MAX_ATTEMPTS):
        h = hashlib.sha256(data + ctr.to_bytes(4, "big")).digest()
        x = int.from_bytes(h[:byte_len], "big") % p
        rhs = (pow(x, 3, p) + a * x + b) % p
        y = pow(rhs, (p + 1) // 4, p)
        if (y * y) % p == rhs:
            return (x, y % p)
    raise PAKEError(f"hash-to-point mapping failed after {_HASH_TO_POINT_MAX_ATTEMPTS} attempts")


# ── SPAKE2+ protocol classes ─────────────────────────────────────────────


class SPAKE2PlusServer:
    """SPAKE2+ server side."""

    def __init__(
        self,
        group: SPAKE2PlusGroup,
        password: bytes,
        server_id: bytes,
        client_id: bytes,
        context: bytes,
    ) -> None:
        """Initialize the server with peer identities and shared context."""
        if not server_id:
            raise PAKEError("server_id must not be empty")
        if not client_id:
            raise PAKEError("client_id must not be empty")
        self._group = group
        self._password = password
        self._server_id = server_id
        self._client_id = client_id
        self._context = context
        self._curve = _ec_curve_obj(group)
        self._params = _group_params(group)
        self._byte_len = (self._params["p"].bit_length() + 7) // 8

        self._x: int | None = None
        self._X_bytes: bytes | None = None
        self._M: tuple[int, int] | None = None
        self._N: tuple[int, int] | None = None
        self._shared: bytes | None = None
        self._started = False

    def _derive_w0_w1_m_n(self) -> None:
        self._params["p"]
        self._params["a"]

        h0 = hashlib.sha256(self._password + self._context + self._client_id + self._server_id + b"w0").digest()
        h1 = hashlib.sha256(self._password + self._context + self._client_id + self._server_id + b"w1").digest()

        w0 = int.from_bytes(h0, "big") % self._params["n"]
        w1 = int.from_bytes(h1, "big") % self._params["n"]

        self._M = _hash_to_point(h0, self._params, self._byte_len)
        self._N = _hash_to_point(h1, self._params, self._byte_len)
        self._w0 = w0
        self._w1 = w1

    def start(self) -> bytes:
        """Create and return the server's first protocol message."""
        self._derive_w0_w1_m_n()
        assert self._M is not None

        p = self._params["p"]
        a = self._params["a"]
        gx = self._params["gx"]
        gy = self._params["gy"]
        n = self._params["n"]

        self._x = _secrets.randbelow(n - 1) + 1
        gxp, gyp = _point_mul(self._x, gx, gy, a, p)
        w0Mx, w0My = _point_mul(self._w0, self._M[0], self._M[1], a, p)
        Xx, Xy = _point_add(gxp, gyp, w0Mx, w0My, a, p)

        self._X_bytes = _point_to_bytes(Xx, Xy, self._byte_len)
        self._started = True
        return self._X_bytes

    def finish(self, client_msg: bytes) -> bytes:
        """Validate the client message and derive the shared secret."""
        if not self._started or self._x is None or self._X_bytes is None or self._N is None:
            raise PAKEError("call start() before finish()")
        assert self._N is not None and self._w1 is not None

        p = self._params["p"]
        a = self._params["a"]
        self._params["n"]

        if len(client_msg) != 1 + 2 * self._byte_len or client_msg[0] != 4:
            raise PAKEError("invalid client message format")
        Yx = int.from_bytes(client_msg[1 : 1 + self._byte_len], "big")
        Yy = int.from_bytes(client_msg[1 + self._byte_len :], "big")

        neg_w1Nx, neg_w1Ny = _point_neg(*_point_mul(self._w1, self._N[0], self._N[1], a, p), p)
        unmasked_x, unmasked_y = _point_add(Yx, Yy, neg_w1Nx, neg_w1Ny, a, p)
        Zx, _Zy = _point_mul(self._x, unmasked_x, unmasked_y, a, p)

        Z_bytes = Zx.to_bytes(self._byte_len, "big")

        transcript = (
            len(self._client_id).to_bytes(2, "big")
            + self._client_id
            + len(self._server_id).to_bytes(2, "big")
            + self._server_id
            + len(self._context).to_bytes(2, "big")
            + self._context
            + self._X_bytes
            + client_msg
        )

        self._shared = _hkdf_derive(Z_bytes, b"", transcript, self._group.scalar_bytes, self._group.hash_name)
        return self._shared


class SPAKE2PlusClient:
    """SPAKE2+ client side."""

    def __init__(
        self,
        group: SPAKE2PlusGroup,
        password: bytes,
        client_id: bytes,
        server_id: bytes,
        context: bytes,
    ) -> None:
        """Initialize the client with peer identities and shared context."""
        if not client_id:
            raise PAKEError("client_id must not be empty")
        if not server_id:
            raise PAKEError("server_id must not be empty")
        self._group = group
        self._password = password
        self._client_id = client_id
        self._server_id = server_id
        self._context = context
        self._params = _group_params(group)
        self._byte_len = (self._params["p"].bit_length() + 7) // 8

        self._y: int | None = None
        self._Y_bytes: bytes | None = None
        self._shared: bytes | None = None

    def _derive_w0_w1_m_n(self) -> tuple[int, int, tuple[int, int], tuple[int, int]]:
        h0 = hashlib.sha256(self._password + self._context + self._client_id + self._server_id + b"w0").digest()
        h1 = hashlib.sha256(self._password + self._context + self._client_id + self._server_id + b"w1").digest()

        w0 = int.from_bytes(h0, "big") % self._params["n"]
        w1 = int.from_bytes(h1, "big") % self._params["n"]

        M = _hash_to_point(h0, self._params, self._byte_len)
        N = _hash_to_point(h1, self._params, self._byte_len)

        return w0, w1, M, N

    def finish(self, server_msg: bytes) -> bytes:
        """Validate the server message and return the client response."""
        w0, w1, M, N = self._derive_w0_w1_m_n()

        p = self._params["p"]
        a = self._params["a"]
        gx = self._params["gx"]
        gy = self._params["gy"]
        n = self._params["n"]

        if len(server_msg) != 1 + 2 * self._byte_len or server_msg[0] != 4:
            raise PAKEError("invalid server message format")
        Xx = int.from_bytes(server_msg[1 : 1 + self._byte_len], "big")
        Xy = int.from_bytes(server_msg[1 + self._byte_len :], "big")

        self._y = _secrets.randbelow(n - 1) + 1
        gyp_x, gyp_y = _point_mul(self._y, gx, gy, a, p)
        w1Nx, w1Ny = _point_mul(w1, N[0], N[1], a, p)
        Yx, Yy = _point_add(gyp_x, gyp_y, w1Nx, w1Ny, a, p)

        self._Y_bytes = _point_to_bytes(Yx, Yy, self._byte_len)

        neg_w0Mx, neg_w0My = _point_neg(*_point_mul(w0, M[0], M[1], a, p), p)
        unmasked_x, unmasked_y = _point_add(Xx, Xy, neg_w0Mx, neg_w0My, a, p)
        Zx, _Zy = _point_mul(self._y, unmasked_x, unmasked_y, a, p)

        Z_bytes = Zx.to_bytes(self._byte_len, "big")

        transcript = (
            len(self._client_id).to_bytes(2, "big")
            + self._client_id
            + len(self._server_id).to_bytes(2, "big")
            + self._server_id
            + len(self._context).to_bytes(2, "big")
            + self._context
            + server_msg
            + self._Y_bytes
        )

        self._shared = _hkdf_derive(Z_bytes, b"", transcript, self._group.scalar_bytes, self._group.hash_name)
        return self._Y_bytes

    def get_shared_secret(self) -> bytes:
        """Return the completed exchange's shared secret."""
        if self._shared is None:
            raise PAKEError("key exchange not complete")
        return self._shared


# ── OPAQUE (RFC 9381) ────────────────────────────────────────────────────


def _encode_three_fields(a: bytes, b: bytes, c: bytes) -> bytes:
    result = bytearray()
    for item in (a, b, c):
        result += len(item).to_bytes(2, "big")
        result += item
    return bytes(result)


def _parse_three_fields(data: bytes) -> tuple[bytes | None, bytes | None, bytes | None]:
    fields: list[bytes] = []
    pos = 0
    for _ in range(3):
        if pos + 2 > len(data):
            return (None, None, None)
        length = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
        if pos + length > len(data):
            return (None, None, None)
        fields.append(data[pos : pos + length])
        pos += length
    return (fields[0], fields[1], fields[2])


_CURVE_OBJ_MAP: Final[dict[str, ec.EllipticCurve]] = {
    "P-256": ec.SECP256R1(),
    "P-384": ec.SECP384R1(),
    "P-521": ec.SECP521R1(),
}

_HASH_FOR_CURVE: Final[dict[str, str]] = {
    "P-256": "sha256",
    "P-384": "sha384",
    "P-521": "sha512",
    "ed25519": "sha512",
}


@dataclass
class OPAQUEConfig:
    """Normalize an OPAQUE curve and derive its non-user-selectable hash."""

    curve: str = "P-256"
    hash_name: str = field(init=False, default="sha256")

    def __post_init__(self) -> None:
        """Normalize the curve and derive its approved hash algorithm."""
        upper = self.curve.upper()
        if upper in ("P-256", "P-384", "P-521"):
            self.curve = upper
        else:
            self.curve = self.curve.lower()
        if self.curve not in _HASH_FOR_CURVE:
            raise PAKEError(f"Unsupported curve: {self.curve}")
        self.hash_name = _HASH_FOR_CURVE[self.curve]


def _opaque_ec_keygen(curve_name: str) -> tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey]:
    curve = _CURVE_OBJ_MAP[curve_name]
    priv = ec.generate_private_key(curve)
    return priv, priv.public_key()


def _opaque_ed25519_keygen() -> tuple[_ed.Ed25519PrivateKey, _ed.Ed25519PublicKey]:
    priv = _ed.Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def _oprf_blind_evaluate(
    seed: bytes,
    password: bytes,
    user_id: bytes,
    config: OPAQUEConfig,
) -> bytes:
    h = hashlib.new(config.hash_name, seed + password + user_id).digest()
    return h


class OPAQUERegistration:
    """OPAQUE registration: creates a password-verifier record from a password."""

    @staticmethod
    def register(
        config: OPAQUEConfig,
        password: bytes,
        user_id: bytes,
        server_id: bytes,
    ) -> dict[str, Any]:
        """Create an OPAQUE verifier record for one user and server."""
        oprf_seed = os.urandom(32)

        if config.curve == "ed25519":
            _, spub = _opaque_ed25519_keygen()
            server_public_bytes = spub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        else:
            _, spub = _opaque_ec_keygen(config.curve)  # type: ignore[assignment]
            server_public_bytes = spub.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )

        rwd = _oprf_blind_evaluate(oprf_seed, password, user_id, config)

        envelope = _hkdf_derive(
            ikm=rwd,
            salt=b"OPAQUE-ENVELOPE",
            info=user_id + server_id + server_public_bytes,
            length=64,
            hash_name=config.hash_name,
        )

        return {
            "envelope": envelope,
            "server_public_key": server_public_bytes,
            "oprf_seed": oprf_seed,
            "user_id": user_id,
            "server_id": server_id,
        }


class OPAQUEClient:
    """OPAQUE client side — initiates login with password."""

    def __init__(
        self,
        config: OPAQUEConfig,
        password: bytes,
        user_id: bytes,
        server_id: bytes,
    ) -> None:
        """Initialize a client login exchange."""
        self._config = config
        self._password = password
        self._user_id = user_id
        self._server_id = server_id
        self._shared: bytes | None = None
        self._started = False

    def start(self) -> bytes:
        """Start the exchange and return the encoded user identity."""
        self._started = True
        return self._user_id

    def finish(self, server_msg: bytes) -> bytes:
        """Verify the server record and return the client public key."""
        if not self._started:
            raise PAKEError("call start() before finish()")

        oprf_seed, server_public_bytes, expected_envelope = _parse_three_fields(server_msg)
        if oprf_seed is None or server_public_bytes is None or expected_envelope is None:
            raise PAKEError("invalid server message format")

        rwd = _oprf_blind_evaluate(oprf_seed, self._password, self._user_id, self._config)

        envelope = _hkdf_derive(
            ikm=rwd,
            salt=b"OPAQUE-ENVELOPE",
            info=self._user_id + self._server_id + server_public_bytes,
            length=64,
            hash_name=self._config.hash_name,
        )

        if not _stdlib_hmac.compare_digest(envelope, expected_envelope):
            raise PAKEError("envelope verification failed — wrong password or corrupt record")

        if self._config.curve == "ed25519":
            cpriv = _ed.Ed25519PrivateKey.generate()
            cpub = cpriv.public_key()
            client_bytes = cpub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        else:
            cpriv = ec.generate_private_key(_CURVE_OBJ_MAP[self._config.curve])  # type: ignore[assignment]
            cpub = cpriv.public_key()
            client_bytes = cpub.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )

        session_hash = hashlib.new(
            self._config.hash_name,
            client_bytes + server_public_bytes + envelope,
        ).digest()

        self._shared = _hkdf_derive(
            ikm=envelope + session_hash,
            salt=b"",
            info=b"OPAQUE-SESSION",
            length=32,
            hash_name=self._config.hash_name,
        )

        return client_bytes

    def get_shared_secret(self) -> bytes:
        """Return the completed login exchange's shared secret."""
        if self._shared is None:
            raise PAKEError("key exchange not complete")
        return self._shared


class OPAQUEServer:
    """OPAQUE server side — responds to client login with stored record."""

    def __init__(self, config: OPAQUEConfig, record: dict[str, Any]) -> None:
        """Initialize a server login exchange from a verifier record."""
        self._config = config
        self._record = record
        self._shared: bytes | None = None
        self._started = False

    def start(self, client_msg: bytes) -> bytes:
        """Start the exchange and return the encoded verifier material."""
        self._started = True
        oprf_seed: bytes = self._record["oprf_seed"]
        server_pub_bytes: bytes = self._record["server_public_key"]
        envelope: bytes = self._record["envelope"]

        return _encode_three_fields(oprf_seed, server_pub_bytes, envelope)

    def finish(self, client_msg: bytes) -> bytes:
        """Process the client public key and derive the shared secret."""
        if not self._started:
            raise PAKEError("call start() before finish()")

        envelope: bytes = self._record["envelope"]
        server_pub_bytes: bytes = self._record["server_public_key"]

        session_hash = hashlib.new(
            self._config.hash_name,
            client_msg + server_pub_bytes + envelope,
        ).digest()

        self._shared = _hkdf_derive(
            ikm=envelope + session_hash,
            salt=b"",
            info=b"OPAQUE-SESSION",
            length=32,
            hash_name=self._config.hash_name,
        )

        return self._shared

    def get_shared_secret(self) -> bytes:
        """Return the completed login exchange's shared secret."""
        if self._shared is None:
            raise PAKEError("key exchange not complete")
        return self._shared
