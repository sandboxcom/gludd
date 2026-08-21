"""SPHINCS+-SHAKE-256s-simple stateless hash-based signatures.

The implementation is supplied by pqcrypto's PQClean-backed native module.
The historic ``slh_*`` public API is retained for compatibility, but its name
must not be read as a claim that this implementation is FIPS validated.
"""

from __future__ import annotations

from dataclasses import dataclass

from pqcrypto.sign import sphincs_shake_256s_simple as _spx


@dataclass(slots=True, frozen=True)
class SphincsParams:
    """Compatibility wrapper for the category-five SHAKE-256s parameter set."""

    n: int = 32

    @property
    def pk_bytes(self) -> int:
        """Return the public-key size in bytes."""
        return _spx.PUBLIC_KEY_SIZE

    @property
    def sk_bytes(self) -> int:
        """Return the secret-key size in bytes."""
        return _spx.SECRET_KEY_SIZE

    @property
    def sig_bytes(self) -> int:
        """Return the detached-signature size in bytes."""
        return _spx.SIGNATURE_SIZE


_PARAMS_SLH_DSA_SHAKE_256s = SphincsParams()


class SphincsPlusError(Exception):
    """Base exception for SPHINCS+ operations."""


def slh_keygen(params: SphincsParams | None = None) -> tuple[bytes, bytes]:
    """Generate a SHAKE-256s public and secret key pair."""
    return _spx.generate_keypair()


def slh_sign(msg: bytes, sk: bytes, params: SphincsParams | None = None) -> bytes:
    """Sign ``msg`` with a SHAKE-256s secret key."""
    return _spx.sign(sk, msg)


def slh_verify(msg: bytes, sig: bytes, pk: bytes, params: SphincsParams | None = None) -> bool:
    """Verify an exactly sized detached SHAKE-256s signature."""
    if len(sig) != _spx.SIGNATURE_SIZE:
        error = f"'sig' must be {_spx.SIGNATURE_SIZE} bytes long"
        raise ValueError(error)
    return _spx.verify(pk, msg, sig)


def keygen_small() -> tuple[bytes, bytes]:
    """Generate a key pair through the legacy small-parameter API."""
    return slh_keygen()


def sign_small(msg: bytes, sk: bytes) -> bytes:
    """Sign through the legacy small-parameter API."""
    return slh_sign(msg, sk)


def verify_small(msg: bytes, sig: bytes, pk: bytes) -> bool:
    """Verify through the legacy small-parameter API."""
    return slh_verify(msg, sig, pk)
