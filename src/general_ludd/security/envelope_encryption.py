"""Envelope encryption with DEK/KEK hierarchy, AES-GCM, and key rotation.

Provides:

* :func:`generate_kek` / :func:`generate_dek` — CSPRNG key material.
* :func:`wrap_key` / :func:`unwrap_key` — AES-GCM key wrapping (KEK wraps DEK).
* :class:`KEKStore` — abstract key store; :class:`InMemoryKEKStore` for testing.
* :class:`EnvelopeEncryptor` — encrypt plaintext under a fresh DEK, wrap the DEK
  with the active KEK, and return an :class:`EncryptedBlob`.
* :class:`EncryptedBlob` — self-describing ciphertext with JSON serialisation.
* :class:`KeyRotationResult` — outcome of a KEK rotation.
* :func:`create_envelope_encryptor` — factory that reads ``GLUDD_ENVELOPE_*`` env vars.

Architecture::

    plaintext ──► AES-GCM(DEK, nonce) ──► ciphertext + tag
                        │
    DEK ──► AES-GCM(wrap)(KEK, nonce') ──► wrapped_dek

The ciphertext carries *wrapped_dek* (never the raw DEK).  To decrypt, the
caller must possess the correct KEK version to unwrap the DEK, then use it to
verify + decrypt the payload.  Rotating the KEK invalidates the old wrapping
but re-wrapping the blob under the new KEK is cheap.
"""

from __future__ import annotations

import abc
import base64
import json
import os
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_KEK_BYTES: Final[int] = 32
DEFAULT_DEK_BYTES: Final[int] = 32
NONCE_BYTES: Final[int] = 12
TAG_BYTES: Final[int] = 16
WRAP_PAYLOAD_VERSION: Final[int] = 1


# ---------------------------------------------------------------------------
# Low-level key material
# ---------------------------------------------------------------------------


def generate_kek(num_bytes: int = DEFAULT_KEK_BYTES) -> bytes:
    """Return a cryptographically random KEK."""
    if num_bytes < 16:
        raise ValueError("KEK must be at least 16 bytes")
    return secrets.token_bytes(num_bytes)


def generate_dek(num_bytes: int = DEFAULT_DEK_BYTES) -> bytes:
    """Return a cryptographically random DEK."""
    if num_bytes < 16:
        raise ValueError("DEK must be at least 16 bytes")
    return secrets.token_bytes(num_bytes)


# ---------------------------------------------------------------------------
# Key wrapping (KEK wraps/unwraps DEK)
# ---------------------------------------------------------------------------


class KeyUnwrapError(Exception):
    """The wrapped DEK could not be unwrapped with the provided KEK."""


class TamperDetected(Exception):
    """AEAD authentication failed — ciphertext or metadata has been tampered."""


def _check_kek_length(kek: bytes) -> None:
    if len(kek) < 16 or len(kek) > 32:
        raise ValueError(f"KEK must be 16-32 bytes, got {len(kek)}")


def wrap_key(dek: bytes, kek: bytes) -> bytes:
    """Wrap *dek* under *kek* using AES-GCM (deterministic key-wrapping mode).

    Returns: ``version(4) || IV(12) || ciphertext || tag(16)``.
    """
    _check_kek_length(kek)
    nonce = secrets.token_bytes(NONCE_BYTES)
    aead = AESGCM(kek)
    ciphertext = aead.encrypt(nonce, dek, None)
    return struct.pack(">I", WRAP_PAYLOAD_VERSION) + nonce + ciphertext


def unwrap_key(wrapped: bytes, kek: bytes) -> bytes:
    """Unwrap *wrapped* (output of :func:`wrap_key`) using *kek*.

    Raises:
        KeyUnwrapError: if the KEK cannot decrypt the wrapped payload.
        ValueError: if the payload is malformed.
    """
    _check_kek_length(kek)
    offset = 0
    if len(wrapped) < 4 + NONCE_BYTES + TAG_BYTES:
        raise KeyUnwrapError("wrapped DEK payload too short")

    version = struct.unpack(">I", wrapped[offset : offset + 4])[0]
    offset += 4
    if version != WRAP_PAYLOAD_VERSION:
        raise KeyUnwrapError(f"unsupported wrap version {version}")

    nonce = wrapped[offset : offset + NONCE_BYTES]
    offset += NONCE_BYTES
    ciphertext = wrapped[offset:]

    aead = AESGCM(kek)
    try:
        return aead.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise KeyUnwrapError("failed to unwrap DEK") from exc


# ---------------------------------------------------------------------------
# EncryptedBlob — self-describing ciphertext
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EncryptedBlob:
    """An encrypted payload with its wrapped DEK and AEAD metadata."""

    kek_version: int
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes
    tag: bytes

    def to_json(self) -> str:
        return json.dumps(
            {
                "kek_version": self.kek_version,
                "wrapped_dek": base64.b64encode(self.wrapped_dek).decode("ascii"),
                "nonce": base64.b64encode(self.nonce).decode("ascii"),
                "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
                "tag": base64.b64encode(self.tag).decode("ascii"),
            }
        )

    @classmethod
    def from_json(cls, data: str) -> EncryptedBlob:
        raw = json.loads(data)
        required = {"kek_version", "wrapped_dek", "nonce", "ciphertext", "tag"}
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"EncryptedBlob JSON missing fields: {missing}")
        return cls(
            kek_version=int(raw["kek_version"]),
            wrapped_dek=base64.b64decode(raw["wrapped_dek"]),
            nonce=base64.b64decode(raw["nonce"]),
            ciphertext=base64.b64decode(raw["ciphertext"]),
            tag=base64.b64decode(raw["tag"]),
        )


# ---------------------------------------------------------------------------
# KEK store abstraction
# ---------------------------------------------------------------------------


class KEKStore(abc.ABC):
    """Durable store for versioned KEK material."""

    @abc.abstractmethod
    def save(self, version: int, kek: bytes, expires_at: float = 0.0) -> None: ...

    @abc.abstractmethod
    def load(self, version: int) -> bytes | None: ...

    @abc.abstractmethod
    def list_versions(self) -> list[int]: ...

    @abc.abstractmethod
    def delete(self, version: int) -> None: ...

    @abc.abstractmethod
    def active_version(self) -> int: ...

    @abc.abstractmethod
    def expiry_of(self, version: int) -> float | None: ...


class InMemoryKEKStore(KEKStore):
    """In-memory KEK store for testing and single-process use."""

    def __init__(self) -> None:
        self._keys: dict[int, tuple[bytes, float]] = {}
        self._lock = threading.Lock()

    def save(self, version: int, kek: bytes, expires_at: float = 0.0) -> None:
        with self._lock:
            self._keys[version] = (kek, expires_at)

    def load(self, version: int) -> bytes | None:
        with self._lock:
            entry = self._keys.get(version)
            return entry[0] if entry is not None else None

    def list_versions(self) -> list[int]:
        with self._lock:
            return sorted(self._keys.keys())

    def delete(self, version: int) -> None:
        with self._lock:
            self._keys.pop(version, None)

    def active_version(self) -> int:
        versions = self.list_versions()
        return max(versions) if versions else 0

    def expiry_of(self, version: int) -> float | None:
        with self._lock:
            entry = self._keys.get(version)
            return entry[1] if entry is not None else None


# ---------------------------------------------------------------------------
# Key rotation result
# ---------------------------------------------------------------------------


@dataclass
class KeyRotationResult:
    success: bool
    new_version: int = 0
    prior_version: int = 0
    rotated_at: float | None = None
    expires_at: float | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# EnvelopeEncryptor
# ---------------------------------------------------------------------------


class EnvelopeEncryptor:
    """Encrypt data under a fresh DEK, wrapped with the active KEK.

    Supports KEK rotation, re-wrapping of existing blobs, and batch re-wrap.
    """

    def __init__(self, kek_store: KEKStore) -> None:
        self._kek_store = kek_store
        self._lock = threading.RLock()

    # -- encrypt / decrypt --------------------------------------------------

    def encrypt(self, plaintext: bytes) -> EncryptedBlob:
        """Encrypt *plaintext* under a fresh DEK wrapped with the active KEK."""
        with self._lock:
            active_version = self._kek_store.active_version()
            if active_version == 0:
                raise ValueError("no active KEK available; rotate first")

            kek = self._kek_store.load(active_version)
            if kek is None:
                raise ValueError(f"active KEK version {active_version} not found in store")
            expiry = self._kek_store.expiry_of(active_version)
            if expiry and expiry > 0 and time.time() >= expiry:
                raise ValueError(f"active KEK version {active_version} has expired")

            dek = generate_dek()
            nonce = secrets.token_bytes(NONCE_BYTES)
            aead = AESGCM(dek)
            ciphertext_and_tag = aead.encrypt(nonce, plaintext, None)
            ciphertext = ciphertext_and_tag[:-TAG_BYTES]
            tag = ciphertext_and_tag[-TAG_BYTES:]

            wrapped_dek = wrap_key(dek, kek)

            return EncryptedBlob(
                kek_version=active_version,
                wrapped_dek=wrapped_dek,
                nonce=nonce,
                ciphertext=ciphertext,
                tag=tag,
            )

    def decrypt(self, blob: EncryptedBlob) -> bytes:
        """Decrypt *blob* by unwrapping its DEK and verifying AEAD."""
        with self._lock:
            kek = self._kek_store.load(blob.kek_version)
            if kek is None:
                raise KeyUnwrapError(f"KEK version {blob.kek_version} not found")

            try:
                dek = unwrap_key(blob.wrapped_dek, kek)
            except KeyUnwrapError:
                raise

            aead = AESGCM(dek)
            try:
                return aead.decrypt(blob.nonce, blob.ciphertext + blob.tag, None)
            except Exception as exc:
                raise TamperDetected("AEAD verification failed") from exc

    # -- key rotation -------------------------------------------------------

    def rotate_kek(self, ttl_seconds: int = 3600) -> KeyRotationResult:
        """Generate a new KEK, store it as the active version, return result."""
        with self._lock:
            prior = self._kek_store.active_version()
            new_version = prior + 1
            new_kek = generate_kek()
            now = time.time()
            self._kek_store.save(version=new_version, kek=new_kek, expires_at=now + ttl_seconds)
            return KeyRotationResult(
                success=True,
                new_version=new_version,
                prior_version=prior,
                rotated_at=now,
                expires_at=now + ttl_seconds,
            )

    def current_kek_version(self) -> int:
        with self._lock:
            return self._kek_store.active_version()

    # -- re-wrap ------------------------------------------------------------

    def rewrap(self, blob: EncryptedBlob) -> EncryptedBlob:
        """Re-wrap *blob*'s DEK with the current active KEK.

        If the blob is already wrapped with the current KEK, return it unchanged.
        """
        with self._lock:
            active = self._kek_store.active_version()
            if blob.kek_version == active:
                return blob

            current_kek = self._kek_store.load(active)
            if current_kek is None:
                raise ValueError(f"active KEK version {active} not found")

            old_kek = self._kek_store.load(blob.kek_version)
            if old_kek is None:
                raise KeyUnwrapError(f"original KEK version {blob.kek_version} not available")

            dek = unwrap_key(blob.wrapped_dek, old_kek)
            new_wrapped = wrap_key(dek, current_kek)

            return EncryptedBlob(
                kek_version=active,
                wrapped_dek=new_wrapped,
                nonce=blob.nonce,
                ciphertext=blob.ciphertext,
                tag=blob.tag,
            )

    def rewrap_batch(self, blobs: list[EncryptedBlob]) -> list[EncryptedBlob]:
        """Re-wrap every blob in *blobs*."""
        return [self.rewrap(b) for b in blobs]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_envelope_encryptor(
    kek_store: KEKStore | None = None,
) -> EnvelopeEncryptor:
    """Factory that reads ``GLUDD_ENVELOPE_*`` env vars for configuration."""
    store = kek_store if kek_store is not None else InMemoryKEKStore()

    initial_kek = os.environ.get("GLUDD_ENVELOPE_KEK_B64")
    if initial_kek:
        store.save(version=1, kek=base64.b64decode(initial_kek), expires_at=0.0)

    return EnvelopeEncryptor(kek_store=store)
