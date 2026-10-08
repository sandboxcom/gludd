"""Security-collection SRP-6a adapter using srptools.

Uses RFC 5054 2048-bit safe-prime group and SHA-256.
Public API preserved; crypto delegated to srptools.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import cast

from srptools import SRPClientSession, SRPContext, SRPServerSession
from srptools.constants import PRIME_2048, PRIME_2048_GEN
from srptools.utils import hex_from, int_from_hex


class SRPError(ValueError):
    """Base exception for SRP operations."""


_SRP2048_N = int(PRIME_2048, 16)
_SRP2048_g = int(PRIME_2048_GEN, 16)


def _ctx(username: str = "user", password: str | None = None) -> SRPContext:
    return SRPContext(
        username,
        password=password,
        prime=PRIME_2048,
        generator=PRIME_2048_GEN,
        hash_func=hashlib.sha256,
        bits_random=256,
        bits_salt=256,
    )


_BOOT = _ctx()
_k: int = _BOOT._mult


def _to_hex(value: int) -> str:
    return cast(str, hex_from(value))


def _from_hex(h: str | bytes) -> int:
    return cast(int, int_from_hex(h))


def _bytes_from_hex(h: str | bytes) -> bytes:
    s = h.decode() if isinstance(h, bytes) else h
    return bytes.fromhex(s)


def _hton(value: int) -> bytes:
    return cast(bytes, _BOOT.pad(value))


def _hash(*args: bytes) -> int:
    return cast(int, _BOOT.hash(*args))


def _hash_bytes(*args: bytes) -> bytes:
    return cast(bytes, _BOOT.hash(*args, as_bytes=True))


def _private_x(username: str, password: str, salt: int) -> int:
    return cast(int, _ctx(username, password).get_common_password_hash(salt))


@dataclass(slots=True, frozen=True)
class SRPServerState:
    username: str
    salt: int
    verifier: int


@dataclass(slots=True, frozen=True)
class SRPSessionClient:
    username: str
    salt: int
    secret_ephemeral: int
    public_ephemeral: int
    session_key: int


@dataclass(slots=True, frozen=True)
class SRPSessionServer:
    username: str
    public_ephemeral: int
    secret_ephemeral: int
    session_key: int


def server_generate_salt() -> int:
    return int(_BOOT.generate_salt())


def server_compute_verifier(username: str, password: str, salt: int) -> int:
    x = _private_x(username, password, salt)
    return int(_BOOT.get_common_password_verifier(x))


def server_enroll(username: str, password: str) -> tuple[int, int]:
    ctx = _ctx(username, password)
    _, verifier_hex, salt_hex = ctx.get_user_data_triplet()
    return _from_hex(salt_hex), _from_hex(verifier_hex)


def client_generate_ephemeral() -> tuple[int, int]:
    a = int(_BOOT.generate_client_private())
    A = int(_BOOT.get_client_public(a))
    return a, A


def server_generate_ephemeral(verifier: int) -> tuple[int, int, int]:
    b = int(_BOOT.generate_server_private())
    B = int(_BOOT.get_server_public(verifier, b))
    return b, B, b


def compute_u(A: int, B: int) -> int:
    return int(_BOOT.get_common_secret(B, A))


def client_compute_session_key(
    username: str,
    password: str,
    salt: int,
    a: int,
    A: int,
    B: int,
) -> int:
    ctx = _ctx(username, password)
    if A % _SRP2048_N == 0:
        raise SRPError("A == 0 mod N")
    if B % _SRP2048_N == 0:
        raise SRPError("B == 0 mod N")
    u = int(ctx.get_common_secret(B, A))
    if u == 0:
        raise SRPError("u == 0 — abort")
    x = int(ctx.get_common_password_hash(salt))
    return int(ctx.get_client_premaster_secret(x, B, a, u))


def server_compute_session_key(
    verifier: int,
    b: int,
    A: int,
    B: int,
) -> int:
    if A % _SRP2048_N == 0:
        raise SRPError("A == 0 mod N")
    if B % _SRP2048_N == 0:
        raise SRPError("B == 0 mod N")
    u = int(_BOOT.get_common_secret(B, A))
    if u == 0:
        raise SRPError("u == 0 — abort")
    return int(_BOOT.get_server_premaster_secret(verifier, b, A, u))


def compute_client_proof(A: int, B: int, S: int) -> bytes:
    return _hash_bytes(_hton(A), _hton(B), _hton(S))


def compute_server_proof(A: int, M1: bytes, S: int) -> bytes:
    return _hash_bytes(_hton(A), M1, _hton(S))


def derive_session_key(S: int) -> bytes:
    return cast(bytes, _BOOT.get_common_session_key(S))


def full_client_flow(
    username: str,
    password: str,
    salt: int,
    B: int,
) -> tuple[int, bytes, bytes]:
    ctx = _ctx(username, password)
    session = SRPClientSession(ctx)
    session.process(_to_hex(B), _to_hex(salt))
    return (
        cast(int, int_from_hex(session.public)),
        _bytes_from_hex(session.key_proof),
        _bytes_from_hex(session.key),
    )


def full_server_flow(
    username: str,
    salt: int,
    verifier: int,
    A: int,
) -> tuple[int, int, bytes, bytes]:
    ctx = _ctx(username)
    session = SRPServerSession(ctx, _to_hex(verifier))
    session.process(_to_hex(A), _to_hex(salt))
    return (
        cast(int, int_from_hex(session.private)),
        cast(int, int_from_hex(session.public)),
        _bytes_from_hex(session.key_proof),
        _bytes_from_hex(session.key),
    )


def server_verify_proof(A: int, M1: bytes, S: int, expected_M1: bytes) -> bytes | None:
    if not secrets.compare_digest(M1, expected_M1):
        return None
    return compute_server_proof(A, M1, S)
