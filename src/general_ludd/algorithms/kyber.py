"""ML-KEM provider boundary backed by maintained PQClean implementations.

The legacy Kyber names remain for source compatibility. New code should
describe the algorithms as ML-KEM-512, ML-KEM-768, and ML-KEM-1024, as
standardized by FIPS 203.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

BACKEND = "pqcrypto"


class KyberError(Exception):
    """Fail-closed error raised by the ML-KEM provider boundary."""


class _KemBackend(Protocol):
    ALGORITHM: str
    PUBLIC_KEY_SIZE: int
    SECRET_KEY_SIZE: int
    CIPHERTEXT_SIZE: int
    PLAINTEXT_SIZE: int

    def generate_keypair(self) -> tuple[bytes, bytes]: ...

    def encrypt(self, public_key: bytes) -> tuple[bytes, bytes]: ...

    def decrypt(self, secret_key: bytes, ciphertext: bytes) -> bytes: ...


@dataclass(slots=True, frozen=True)
class KyberParams:
    """Legacy-compatible parameter descriptor for one FIPS 203 ML-KEM set."""

    k: int
    eta1: int
    eta2: int
    du: int
    dv: int

    @property
    def algorithm(self) -> str:
        """Return the canonical FIPS 203 algorithm name."""
        return _backend_for(self).ALGORITHM

    @property
    def pk_bytes(self) -> int:
        """Return the exact public-key size in bytes."""
        return _backend_for(self).PUBLIC_KEY_SIZE

    @property
    def sk_bytes(self) -> int:
        """Return the exact secret-key size in bytes."""
        return _backend_for(self).SECRET_KEY_SIZE

    @property
    def ct_bytes(self) -> int:
        """Return the exact ciphertext size in bytes."""
        return _backend_for(self).CIPHERTEXT_SIZE

    @property
    def ss_bytes(self) -> int:
        """Return the exact shared-secret size in bytes."""
        return _backend_for(self).PLAINTEXT_SIZE


PARAMS_512 = KyberParams(k=2, eta1=3, eta2=2, du=10, dv=4)
PARAMS_768 = KyberParams(k=3, eta1=2, eta2=2, du=10, dv=4)
PARAMS_1024 = KyberParams(k=4, eta1=2, eta2=2, du=11, dv=5)

def _load_backend(module_name: str) -> _KemBackend:
    module = import_module(f"pqcrypto.kem.{module_name}")
    required = (
        "ALGORITHM",
        "PUBLIC_KEY_SIZE",
        "SECRET_KEY_SIZE",
        "CIPHERTEXT_SIZE",
        "PLAINTEXT_SIZE",
        "generate_keypair",
        "encrypt",
        "decrypt",
    )
    missing = tuple(name for name in required if not hasattr(module, name))
    if missing:
        joined = ", ".join(missing)
        raise KyberError(f"{module_name} provider is missing required attributes: {joined}")
    return cast(_KemBackend, module)


_BACKENDS: dict[KyberParams, _KemBackend] = {
    PARAMS_512: _load_backend("ml_kem_512"),
    PARAMS_768: _load_backend("ml_kem_768"),
    PARAMS_1024: _load_backend("ml_kem_1024"),
}


def _backend_for(params: KyberParams) -> _KemBackend:
    if not isinstance(params, KyberParams):
        raise KyberError("ML-KEM parameters must be a KyberParams value")
    backend = _BACKENDS.get(params)
    if backend is None:
        raise KyberError(f"unsupported ML-KEM parameter set: {params!r}")
    return backend


def _require_bytes(value: object, *, label: str, expected: int) -> bytes:
    if not isinstance(value, bytes):
        raise KyberError(f"{label} must be bytes")
    if len(value) != expected:
        raise KyberError(f"{label} must be exactly {expected} bytes, got {len(value)}")
    return value


def _validate_backend_output(value: object, *, label: str, expected: int) -> bytes:
    if not isinstance(value, bytes) or len(value) != expected:
        raise KyberError(f"{label} backend output violated the {expected}-byte contract")
    return value


def keygen(params: KyberParams = PARAMS_512) -> tuple[bytes, bytes]:
    """Generate a FIPS 203 public/secret key pair for params."""
    backend = _backend_for(params)
    try:
        public_key, secret_key = backend.generate_keypair()
    except Exception as exc:
        raise KyberError(f"{backend.ALGORITHM} key generation failed") from exc
    return (
        _validate_backend_output(public_key, label="public key", expected=backend.PUBLIC_KEY_SIZE),
        _validate_backend_output(secret_key, label="secret key", expected=backend.SECRET_KEY_SIZE),
    )


def encapsulate(
    public_key: bytes,
    params: KyberParams = PARAMS_512,
) -> tuple[bytes, bytes]:
    """Encapsulate a fresh shared secret to a validated public key."""
    backend = _backend_for(params)
    checked_key = _require_bytes(
        public_key,
        label="public key",
        expected=backend.PUBLIC_KEY_SIZE,
    )
    try:
        ciphertext, shared_secret = backend.encrypt(checked_key)
    except Exception as exc:
        raise KyberError(f"{backend.ALGORITHM} encapsulation failed") from exc
    return (
        _validate_backend_output(
            ciphertext,
            label="ciphertext",
            expected=backend.CIPHERTEXT_SIZE,
        ),
        _validate_backend_output(
            shared_secret,
            label="shared secret",
            expected=backend.PLAINTEXT_SIZE,
        ),
    )


def decapsulate(
    ciphertext: bytes,
    secret_key: bytes,
    params: KyberParams = PARAMS_512,
) -> bytes:
    """Decapsulate with FIPS 203 implicit-rejection semantics."""
    backend = _backend_for(params)
    checked_ciphertext = _require_bytes(
        ciphertext,
        label="ciphertext",
        expected=backend.CIPHERTEXT_SIZE,
    )
    checked_key = _require_bytes(
        secret_key,
        label="secret key",
        expected=backend.SECRET_KEY_SIZE,
    )
    try:
        shared_secret = backend.decrypt(checked_key, checked_ciphertext)
    except Exception as exc:
        raise KyberError(f"{backend.ALGORITHM} decapsulation failed") from exc
    return _validate_backend_output(
        shared_secret,
        label="shared secret",
        expected=backend.PLAINTEXT_SIZE,
    )


def keygen_512() -> tuple[bytes, bytes]:
    """Generate an ML-KEM-512 public/secret key pair."""
    return keygen(PARAMS_512)


def keygen_768() -> tuple[bytes, bytes]:
    """Generate an ML-KEM-768 public/secret key pair."""
    return keygen(PARAMS_768)


def keygen_1024() -> tuple[bytes, bytes]:
    """Generate an ML-KEM-1024 public/secret key pair."""
    return keygen(PARAMS_1024)


def encapsulate_512(public_key: bytes) -> tuple[bytes, bytes]:
    """Encapsulate a shared secret with an ML-KEM-512 public key."""
    return encapsulate(public_key, PARAMS_512)


def encapsulate_768(public_key: bytes) -> tuple[bytes, bytes]:
    """Encapsulate a shared secret with an ML-KEM-768 public key."""
    return encapsulate(public_key, PARAMS_768)


def encapsulate_1024(public_key: bytes) -> tuple[bytes, bytes]:
    """Encapsulate a shared secret with an ML-KEM-1024 public key."""
    return encapsulate(public_key, PARAMS_1024)


def decapsulate_512(ciphertext: bytes, secret_key: bytes) -> bytes:
    """Decapsulate an ML-KEM-512 ciphertext."""
    return decapsulate(ciphertext, secret_key, PARAMS_512)


def decapsulate_768(ciphertext: bytes, secret_key: bytes) -> bytes:
    """Decapsulate an ML-KEM-768 ciphertext."""
    return decapsulate(ciphertext, secret_key, PARAMS_768)


def decapsulate_1024(ciphertext: bytes, secret_key: bytes) -> bytes:
    """Decapsulate an ML-KEM-1024 ciphertext."""
    return decapsulate(ciphertext, secret_key, PARAMS_1024)


__all__ = [
    "BACKEND",
    "PARAMS_512",
    "PARAMS_768",
    "PARAMS_1024",
    "KyberError",
    "KyberParams",
    "decapsulate",
    "decapsulate_512",
    "decapsulate_768",
    "decapsulate_1024",
    "encapsulate",
    "encapsulate_512",
    "encapsulate_768",
    "encapsulate_1024",
    "keygen",
    "keygen_512",
    "keygen_768",
    "keygen_1024",
]
