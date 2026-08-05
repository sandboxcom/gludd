"""SRP-6a (Secure Remote Password) protocol implementation.

Client/server key agreement that authenticates a user to a server
without ever sending the password over the wire.  Uses a 2048-bit
safe-prime group (RFC 5054) and SHA-256.

Core flow:
  Client (enroll):   x = H(salt | H(username | ":" | password))
                      v = g^x mod N  →  send (username, salt, v) to server
  Client (authenticate):
    1. A = g^a mod N  →  send (username, A) to server
    2. Receive (salt, B) from server
    3. u = H(A | B)
    4. S = (B - k * g^x)^(a + u * x) mod N
    5. M1 = H(A | B | S)  →  send to server
    6. Receive M2 from server, verify

Pure-Python, stdlib-only.  No password is ever transmitted in cleartext.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Final


class SRPError(ValueError):
    """Base exception for SRP operations."""


# ── RFC 5054 2048-bit group ────────────────────────────────────────────

_SRP2048_N_HEX = (
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050"
    "A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50"
    "E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8"
    "55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B"
    "CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748"
    "544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6"
    "AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB"
    "694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73"
)
_SRP2048_N: Final[int] = int(_SRP2048_N_HEX, 16)

_SRP2048_g: Final[int] = 2

_k: Final[int] = int.from_bytes(
    hashlib.sha256(
        _SRP2048_N.to_bytes((_SRP2048_N.bit_length() + 7) // 8, "big")
        + hashlib.sha256(_SRP2048_g.to_bytes((_SRP2048_g.bit_length() + 7) // 8, "big")).digest()
    ).digest(),
    "big",
)


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class SRPServerState:
    """Per-user state the server must store.

    Attributes:
        username: The identity string.
        salt: Random 32-byte salt, stored as int.
        verifier: v = g^x mod N (x derived from password).
    """

    username: str
    salt: int
    verifier: int


@dataclass(slots=True, frozen=True)
class SRPSessionClient:
    """Transient client-side session during authentication."""

    username: str
    salt: int
    secret_ephemeral: int  # a
    public_ephemeral: int  # A
    session_key: int  # S (raw, before KDF)


@dataclass(slots=True, frozen=True)
class SRPSessionServer:
    """Transient server-side session during authentication."""

    username: str
    public_ephemeral: int  # B
    secret_ephemeral: int  # b
    session_key: int  # S (raw, before KDF)


# ── Helpers ─────────────────────────────────────────────────────────────


def _hton(value: int) -> bytes:
    """Integer to big-endian bytes, length matching N."""
    return value.to_bytes((_SRP2048_N.bit_length() + 7) // 8, "big")


def _hash(*args: bytes) -> int:
    """SHA-256 of concatenated byte sequences, returned as int."""
    h = hashlib.sha256()
    for a in args:
        h.update(a)
    return int.from_bytes(h.digest(), "big")


def _hash_bytes(*args: bytes) -> bytes:
    """SHA-256 of concatenated byte sequences."""
    h = hashlib.sha256()
    for a in args:
        h.update(a)
    return h.digest()


def _private_x(username: str, password: str, salt: int) -> int:
    """x = SHA256(salt | SHA256(username | ":" | password))."""
    inner = hashlib.sha256(f"{username}:{password}".encode()).digest()
    salt_bytes = salt.to_bytes(32, "big")
    return int.from_bytes(hashlib.sha256(salt_bytes + inner).digest(), "big")


# ── Server: enrollment ──────────────────────────────────────────────────


def server_generate_salt() -> int:
    """Generate a random 256-bit salt."""
    return secrets.randbits(256)


def server_compute_verifier(username: str, password: str, salt: int) -> int:
    """v = g^x mod N where x = SHA256(salt | SHA256(username | ":" | password))."""
    x = _private_x(username, password, salt)
    return pow(_SRP2048_g, x, _SRP2048_N)


def server_enroll(username: str, password: str) -> tuple[int, int]:
    """Create salt and verifier for a new user. Returns (salt, verifier)."""
    salt = server_generate_salt()
    verifier = server_compute_verifier(username, password, salt)
    return salt, verifier


# ── Client: initiate authentication ─────────────────────────────────────


def client_generate_ephemeral() -> tuple[int, int]:
    """Generate client ephemeral keypair (a, A = g^a mod N)."""
    a = secrets.randbits(256) % (_SRP2048_N - 1) + 1
    A = pow(_SRP2048_g, a, _SRP2048_N)
    return a, A


# ── Server: respond to client hello ─────────────────────────────────────


def server_generate_ephemeral(verifier: int) -> tuple[int, int, int]:
    """Generate server ephemeral B = k*v + g^b mod N.

    Returns (secret b, public B, private b).
    """
    b = secrets.randbits(256) % (_SRP2048_N - 1) + 1
    B = (_k * verifier + pow(_SRP2048_g, b, _SRP2048_N)) % _SRP2048_N
    return b, B, b


# ── Shared: compute u, session key, proofs ──────────────────────────────


def compute_u(A: int, B: int) -> int:
    """u = SHA256(A | B)."""
    return _hash(_hton(A), _hton(B))


def client_compute_session_key(
    username: str,
    password: str,
    salt: int,
    a: int,
    A: int,
    B: int,
) -> int:
    """Client: S = (B - k * g^x)^(a + u * x) mod N."""
    if A % _SRP2048_N == 0:
        raise SRPError("Client public ephemeral A == 0 mod N")
    if B % _SRP2048_N == 0:
        raise SRPError("Server public ephemeral B == 0 mod N")
    u = compute_u(A, B)
    if u == 0:
        raise SRPError("u == 0 — abort")
    x = _private_x(username, password, salt)
    base = (B - _k * pow(_SRP2048_g, x, _SRP2048_N)) % _SRP2048_N
    exponent = (a + u * x) % _SRP2048_N
    return pow(base, exponent, _SRP2048_N)


def server_compute_session_key(
    verifier: int,
    b: int,
    A: int,
    B: int,
) -> int:
    """Server: S = (A * v^u)^b mod N."""
    if A % _SRP2048_N == 0:
        raise SRPError("Client public ephemeral A == 0 mod N")
    if B % _SRP2048_N == 0:
        raise SRPError("Server public ephemeral B == 0 mod N")
    u = compute_u(A, B)
    if u == 0:
        raise SRPError("u == 0 — abort")
    base = (A * pow(verifier, u, _SRP2048_N)) % _SRP2048_N
    return pow(base, b, _SRP2048_N)


def compute_client_proof(A: int, B: int, S: int) -> bytes:
    """M1 = SHA256(A | B | S)."""
    return _hash_bytes(_hton(A), _hton(B), _hton(S))


def compute_server_proof(A: int, M1: bytes, S: int) -> bytes:
    """M2 = SHA256(A | M1 | S)."""
    return _hash_bytes(_hton(A), M1, _hton(S))


def derive_session_key(S: int) -> bytes:
    """K = SHA256(S), the derived symmetric key."""
    return _hash_bytes(_hton(S))


# ── Full protocol flows (convenience) ───────────────────────────────────


def full_client_flow(
    username: str,
    password: str,
    salt: int,
    B: int,
) -> tuple[int, bytes, bytes]:
    """Run the complete client side of SRP authentication.

    Returns (A, M1, K) where K is the derived session key.
    """
    a, A = client_generate_ephemeral()
    _ = compute_u(A, B)
    S = client_compute_session_key(username, password, salt, a, A, B)
    M1 = compute_client_proof(A, B, S)
    K = derive_session_key(S)
    return A, M1, K


def full_server_flow(
    username: str,
    salt: int,
    verifier: int,
    A: int,
) -> tuple[int, int, bytes, bytes]:
    """Run the complete server side of SRP authentication.

    Returns (b, B, expected_M1, K).
    """
    b, B, _ = server_generate_ephemeral(verifier)
    _ = compute_u(A, B)
    S = server_compute_session_key(verifier, b, A, B)
    M1 = compute_client_proof(A, B, S)
    K = derive_session_key(S)
    return b, B, M1, K


def server_verify_proof(A: int, M1: bytes, S: int, expected_M1: bytes) -> bytes | None:
    """Verify client proof M1 and return server proof M2, or None."""
    if not secrets.compare_digest(M1, expected_M1):
        return None
    return compute_server_proof(A, M1, S)
