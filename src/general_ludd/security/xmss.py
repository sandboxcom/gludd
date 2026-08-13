"""Fail-closed compatibility boundary for RFC 8391 XMSS.

This module intentionally does not implement XMSS itself. The repository does not
ship a vetted RFC 8391 backend, and the former Python WOTS+/Merkle fallback had
exponential key-generation cost, incomplete authentication paths, and no
interoperability evidence. All key creation, signing, state, and serialization
operations therefore fail closed. Verification returns False without parsing
untrusted bytes.

The public function names remain available so callers receive a deterministic
capability error while a maintained, packaged backend is evaluated.
"""

from __future__ import annotations

from typing import Final, NoReturn

DEFAULT_HEIGHT: Final[int] = 10
DEFAULT_DIGEST: Final[str] = "SHA256"
_VALID_DIGESTS: Final[tuple[str, ...]] = (
    "SHA256",
    "SHA512",
    "SHAKE256",
    "SHAKE512",
)
_MIN_HEIGHT: Final[int] = 4
_MAX_HEIGHT: Final[int] = 20
_UNAVAILABLE: Final[str] = (
    "RFC 8391 XMSS backend is unavailable; no key material was generated"
)


class XMSSError(Exception):
    """Raised when an XMSS request cannot be completed safely."""


def _validate_height(height: int) -> None:
    if isinstance(height, bool) or not isinstance(height, int):
        raise XMSSError(
            f"Height must be an integer between {_MIN_HEIGHT} and {_MAX_HEIGHT}, "
            f"got {height!r}"
        )
    if not (_MIN_HEIGHT <= height <= _MAX_HEIGHT):
        raise XMSSError(
            f"Height must be an integer between {_MIN_HEIGHT} and {_MAX_HEIGHT}, "
            f"got {height}"
        )


def _validate_digest(digest_algorithm: str) -> None:
    if (
        not isinstance(digest_algorithm, str)
        or digest_algorithm not in _VALID_DIGESTS
    ):
        raise XMSSError(
            f"Invalid digest_algorithm {digest_algorithm!r}. "
            f"Must be one of {_VALID_DIGESTS}"
        )


def _raise_unavailable() -> NoReturn:
    raise XMSSError(_UNAVAILABLE)


def generate_xmss_keypair(
    height: int = DEFAULT_HEIGHT,
    digest_algorithm: str = DEFAULT_DIGEST,
) -> tuple[bytes, bytes]:
    """Validate parameters, then reject generation without a vetted backend."""
    _validate_height(height)
    _validate_digest(digest_algorithm)
    _raise_unavailable()


def xmss_sign(
    private_key_bytes: bytes,
    message: bytes | str,
) -> tuple[bytes, bytes]:
    """Reject signing because no vetted backend can update state atomically."""
    del private_key_bytes, message
    _raise_unavailable()


def xmss_verify(
    public_key_bytes: bytes,
    message: bytes | str,
    signature: bytes,
) -> bool:
    """Deny verification when no RFC 8391 parser/backend is available."""
    del public_key_bytes, message, signature
    return False


def xmss_signature_count(private_key_bytes: bytes) -> int:
    """Reject state inspection for keys from the removed unvetted format."""
    del private_key_bytes
    _raise_unavailable()


def xmss_remaining_signatures(
    private_key_bytes: bytes,
    height: int,
) -> int:
    """Validate height, then reject state inspection without a backend."""
    del private_key_bytes
    _validate_height(height)
    _raise_unavailable()


def serialize_private_key(private_key_bytes: bytes) -> bytes:
    """Reject serialization of the removed unvetted private-key format."""
    del private_key_bytes
    _raise_unavailable()


def deserialize_private_key(data: bytes) -> bytes:
    """Reject legacy or opaque private keys without a vetted decoder."""
    del data
    _raise_unavailable()


def serialize_public_key(public_key_bytes: bytes) -> bytes:
    """Reject serialization of the removed unvetted public-key format."""
    del public_key_bytes
    _raise_unavailable()


def deserialize_public_key(data: bytes) -> bytes:
    """Reject legacy or opaque public keys without a vetted decoder."""
    del data
    _raise_unavailable()
