"""HPKE (RFC 9180) -- Hybrid Public Key Encryption, Base mode.

Implements RFC 9180 §5.1 Base mode using X25519, HKDF-SHA256,
and AES-GCM via the `cryptography` library.

Provides:

* :func:`generate_key_pair` — X25519 key pair generation.
* :func:`hpke_seal` / :func:`hpke_open` — single-shot Base-mode encrypt/decrypt.
* :class:`HPKESender` / :class:`HPKERecipient` — multi-shot contexts with export.
* :class:`HPKEEncryptedBlob` — self-describing encap + ciphertext.
* :class:`HPKE_Suite` — supported cipher-suite enumeration.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENAP_LEN: Final[int] = 32
Nh: Final[int] = 32
Nn: Final[int] = 12


# ---------------------------------------------------------------------------
# Suite identifiers (RFC 9180 §7)
# ---------------------------------------------------------------------------


class HPKE_Suite(enum.Enum):
    DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_128_GCM = (0x0020, 0x0001, 0x0001)
    DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_256_GCM = (0x0020, 0x0001, 0x0002)

    def __init__(self, kem_id: int, kdf_id: int, aead_id: int) -> None:
        self._kem_id = kem_id
        self._kdf_id = kdf_id
        self._aead_id = aead_id

    @property
    def kem_id(self) -> int:
        return self._kem_id  # type: ignore[return]

    @property
    def kdf_id(self) -> int:
        return self._kdf_id  # type: ignore[return]

    @property
    def aead_id(self) -> int:
        return self._aead_id  # type: ignore[return]

    @property
    def Nk(self) -> int:
        return 16 if self._aead_id == 0x0001 else 32

    def suite_id(self) -> bytes:
        return b"HPKE" + struct.pack(">HHH", self._kem_id, self._kdf_id, self._aead_id)

    def kem_suite_id(self) -> bytes:
        return b"KEM" + struct.pack(">H", self._kem_id)


DEFAULT_SUITE: Final = HPKE_Suite.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_256_GCM


# ---------------------------------------------------------------------------
# HKDF helpers
# ---------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    if salt == b"":
        salt = b"\x00" * Nh
    return _hmac_sha256(salt, ikm)


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    if length > 255 * Nh:
        raise ValueError(f"requested length {length} exceeds HKDF-SHA256 maximum")
    n = (length + Nh - 1) // Nh
    t = b""
    t_prev = b""
    for i in range(1, n + 1):
        t_prev = _hmac_sha256(prk, t_prev + info + bytes([i]))
        t += t_prev
    return t[:length]


def _labeled_extract(salt: bytes, label: bytes, ikm: bytes, sid: bytes) -> bytes:
    labeled_ikm = b"HPKE-v1" + sid + label + ikm
    return _hkdf_extract(salt, labeled_ikm)


def _labeled_expand(prk: bytes, label: bytes, info: bytes, length: int, sid: bytes) -> bytes:
    labeled_info = struct.pack(">H", length) + b"HPKE-v1" + sid + label + info
    return _hkdf_expand(prk, labeled_info, length)


# ---------------------------------------------------------------------------
# DHKEM(X25519, HKDF-SHA256) — RFC 9180 §4.1
# ---------------------------------------------------------------------------


def _kem_extract_and_expand(dh: bytes, kem_context: bytes, suite: HPKE_Suite) -> bytes:
    sid = suite.kem_suite_id()
    prk = _labeled_extract(b"", b"eae_prk", dh, sid)
    return _labeled_expand(prk, b"shared_secret", kem_context, Nh, sid)


# ---------------------------------------------------------------------------
# HPKE Key Schedule — RFC 9180 §5.2
# ---------------------------------------------------------------------------


def _key_schedule(
    mode: int,
    shared_secret: bytes,
    info: bytes,
    suite: HPKE_Suite,
) -> tuple[bytes, bytes, bytes]:
    sid = suite.suite_id()
    psk_id_hash = _labeled_extract(b"", b"psk_id_hash", b"", sid)
    info_hash = _labeled_extract(b"", b"info_hash", info, sid)
    key_schedule_context = bytes([mode]) + psk_id_hash + info_hash

    secret = _labeled_extract(shared_secret, b"secret", b"", sid)

    key = _labeled_expand(secret, b"key", key_schedule_context, suite.Nk, sid)
    base_nonce = _labeled_expand(secret, b"base_nonce", key_schedule_context, Nn, sid)
    exporter_secret = _labeled_expand(secret, b"exp", key_schedule_context, Nh, sid)

    return key, base_nonce, exporter_secret


# ---------------------------------------------------------------------------
# XOR helper for nonce
# ---------------------------------------------------------------------------


def _xor_12(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=False))


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_key_pair() -> tuple[X25519PrivateKey, X25519PublicKey]:
    priv = X25519PrivateKey.generate()
    return priv, priv.public_key()


# ---------------------------------------------------------------------------
# HPKEEncryptedBlob
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HPKEEncryptedBlob:
    encap: bytes
    ciphertext: bytes

    def to_combined(self) -> bytes:
        return self.encap + self.ciphertext

    @classmethod
    def from_combined(cls, data: bytes, encap_len: int = ENAP_LEN) -> HPKEEncryptedBlob:
        if len(data) < encap_len + 1:
            raise ValueError(f"combined blob too short: {len(data)} bytes (need at least {encap_len + 1})")
        return cls(encap=data[:encap_len], ciphertext=data[encap_len:])


# ---------------------------------------------------------------------------
# Context state dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ContextState:
    key: bytes
    base_nonce: bytes
    exporter_secret: bytes
    encap: bytes
    suite: HPKE_Suite
    seq: int = 0


# ---------------------------------------------------------------------------
# Multi-shot sender context
# ---------------------------------------------------------------------------


class HPKESender:
    def __init__(
        self,
        recipient_public_key: X25519PublicKey,
        info: bytes = b"",
        suite: HPKE_Suite | None = None,
        aad: bytes = b"",
    ) -> None:
        self._suite = suite or DEFAULT_SUITE
        self._aad = aad
        self._st = _setup_base_s(recipient_public_key, info, self._suite)

    @property
    def encap(self) -> bytes:
        return self._st.encap

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = _xor_12(self._st.base_nonce, struct.pack(">Q", self._st.seq).rjust(12, b"\x00"))
        self._st.seq += 1
        if self._st.seq == 0:
            raise ValueError("message limit exceeded")
        aead = AESGCM(self._st.key)
        return aead.encrypt(nonce, plaintext, self._aad)

    def export(self, label: bytes, length: int) -> bytes:
        sid = self._st.suite.suite_id()
        return _labeled_expand(self._st.exporter_secret, b"sec", label, length, sid)


# ---------------------------------------------------------------------------
# Multi-shot recipient context
# ---------------------------------------------------------------------------


class HPKERecipient:
    def __init__(
        self,
        recipient_private_key: X25519PrivateKey,
        encap: bytes,
        info: bytes = b"",
        suite: HPKE_Suite | None = None,
        aad: bytes = b"",
    ) -> None:
        self._suite = suite or DEFAULT_SUITE
        self._aad = aad
        self._st = _setup_base_r(recipient_private_key, encap, info, self._suite)

    def decrypt(self, ciphertext: bytes) -> bytes:
        nonce = _xor_12(self._st.base_nonce, struct.pack(">Q", self._st.seq).rjust(12, b"\x00"))
        self._st.seq += 1
        if self._st.seq == 0:
            raise ValueError("message limit exceeded")
        aead = AESGCM(self._st.key)
        return aead.decrypt(nonce, ciphertext, self._aad)

    def export(self, label: bytes, length: int) -> bytes:
        sid = self._st.suite.suite_id()
        return _labeled_expand(self._st.exporter_secret, b"sec", label, length, sid)


# ---------------------------------------------------------------------------
# Internal: SetupBaseS / SetupBaseR (RFC 9180 §5.1)
# ---------------------------------------------------------------------------


def _setup_base_s(
    recipient_public_key: X25519PublicKey,
    info: bytes,
    suite: HPKE_Suite,
) -> _ContextState:
    skE = X25519PrivateKey.generate()
    pkE = skE.public_key()

    dh = skE.exchange(recipient_public_key)
    enc = pkE.public_bytes_raw()
    pkRm = recipient_public_key.public_bytes_raw()

    kem_context = enc + pkRm
    shared_secret = _kem_extract_and_expand(dh, kem_context, suite)
    key, base_nonce, exporter_secret = _key_schedule(0x00, shared_secret, info, suite)

    return _ContextState(
        key=key,
        base_nonce=base_nonce,
        exporter_secret=exporter_secret,
        encap=enc,
        suite=suite,
    )


def _setup_base_r(
    recipient_private_key: X25519PrivateKey,
    encap: bytes,
    info: bytes,
    suite: HPKE_Suite,
) -> _ContextState:
    pkE = X25519PublicKey.from_public_bytes(encap)
    dh = recipient_private_key.exchange(pkE)

    pkRm = recipient_private_key.public_key().public_bytes_raw()
    kem_context = encap + pkRm
    shared_secret = _kem_extract_and_expand(dh, kem_context, suite)
    key, base_nonce, exporter_secret = _key_schedule(0x00, shared_secret, info, suite)

    return _ContextState(
        key=key,
        base_nonce=base_nonce,
        exporter_secret=exporter_secret,
        encap=encap,
        suite=suite,
    )
