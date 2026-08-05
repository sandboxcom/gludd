"""Verifiable Random Function (Ed25519-signature based, deterministic).

Uses Ed25519 (RFC 8032) from the ``cryptography`` library. The VRF proof
is an Ed25519 signature on the input *alpha*, which is deterministic by
construction. The VRF output is BLAKE2b-512 of the proof.

This provides:
- **Uniqueness**: Ed25519 signatures are deterministic (RFC 8032 §5.1).
- **Verifiability**: Anyone with the public key can verify the signature.
- **Pseudorandomness**: The output is a BLAKE2b hash of the signature.
- **Collision resistance**: Inherited from SHA-512 and BLAKE2b.

API
---

* :func:`generate_keypair` — fresh Ed25519 keypair.
* :func:`generate_keypair_from_seed` — deterministic from 32-byte seed.
* :func:`prove` — produce a VRF proof for *alpha*.
* :func:`verify` — verify proof, return VRF output (64 bytes).
* :func:`proof_to_hash` — extract VRF output from a trusted proof.
* :func:`encode_proof` / :func:`decode_proof` — 96-byte serialisation.
"""

from __future__ import annotations

import hashlib
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey as _CryptoPrivateKey,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey as _CryptoPublicKey,
)

SIGNATURE_BYTES: Final[int] = 64
KEY_BYTES: Final[int] = 32
PROOF_BYTES: Final[int] = 64
VRF_OUTPUT_BYTES: Final[int] = 64
SERIALIZED_PROOF_BYTES: Final[int] = 64


class VRFError(Exception):
    """Error in VRF operations (invalid key, malformed proof, etc.)."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh VRF keypair (Ed25519).

    Returns:
        ``(secret_key, public_key)`` — each 32 bytes.
    """
    crypto_key = _CryptoPrivateKey.generate()
    sk = crypto_key.private_bytes_raw()
    pk = crypto_key.public_key().public_bytes_raw()
    return (sk, pk)


def generate_keypair_from_seed(seed: bytes) -> tuple[bytes, bytes]:
    """Generate a deterministic VRF keypair from a 32-byte seed.

    Args:
        seed: Exactly 32 bytes of entropy.

    Returns:
        ``(secret_key, public_key)`` — each 32 bytes.

    Raises:
        VRFError: If *seed* is not exactly 32 bytes.
    """
    if len(seed) != 32:
        raise VRFError(f"Seed must be 32 bytes, got {len(seed)}")
    crypto_key = _CryptoPrivateKey.from_private_bytes(seed)
    sk = crypto_key.private_bytes_raw()
    pk = crypto_key.public_key().public_bytes_raw()
    return (sk, pk)


def prove(sk: bytes, alpha: bytes | str) -> bytes:
    """Produce a VRF proof for *alpha* (64-byte Ed25519 signature).

    Args:
        sk: 32-byte Ed25519 secret key.
        alpha: The VRF input message (``bytes`` or ``str``).

    Returns:
        64-byte deterministic Ed25519 signature on *alpha*.

    Raises:
        VRFError: If *sk* is not exactly 32 bytes.
    """
    if len(sk) != 32:
        raise VRFError(f"Private key must be 32 bytes, got {len(sk)}")
    if isinstance(alpha, str):
        alpha = alpha.encode()
    crypto_key = _CryptoPrivateKey.from_private_bytes(sk)
    return crypto_key.sign(alpha)


def verify(pk: bytes, alpha: bytes | str, proof: bytes) -> bytes | None:
    """Verify a VRF proof and return the VRF output.

    Args:
        pk: 32-byte Ed25519 public key.
        alpha: Original VRF input.
        proof: 64-byte proof as returned by :func:`prove`.

    Returns:
        64-byte VRF output (BLAKE2b-512 of proof) if valid, or ``None``.
    """
    if len(pk) != KEY_BYTES:
        return None
    if len(proof) != SIGNATURE_BYTES:
        return None
    if isinstance(alpha, str):
        alpha = alpha.encode()
    try:
        pub = _CryptoPublicKey.from_public_bytes(pk)
        pub.verify(proof, alpha)
    except (InvalidSignature, ValueError, TypeError):
        return None
    return hashlib.blake2b(proof, digest_size=VRF_OUTPUT_BYTES).digest()


def proof_to_hash(proof: bytes) -> bytes:
    """Extract the VRF output hash directly from a trusted proof.

    Use only when the proof is already known to be valid.
    For untrusted proofs, use :func:`verify` instead.
    """
    if len(proof) != SIGNATURE_BYTES:
        raise VRFError(f"Proof must be {SIGNATURE_BYTES} bytes, got {len(proof)}")
    return hashlib.blake2b(proof, digest_size=VRF_OUTPUT_BYTES).digest()


def encode_proof(proof: bytes) -> bytes:
    """Identity — the proof is already 64 bytes. Provided for API symmetry."""
    if len(proof) != SIGNATURE_BYTES:
        raise VRFError(f"Proof must be {SIGNATURE_BYTES} bytes, got {len(proof)}")
    return proof


def decode_proof(data: bytes) -> bytes:
    """Validate a serialized proof's length. Provided for API symmetry.

    Raises:
        VRFError: If *data* is not exactly 64 bytes.
    """
    if len(data) != SIGNATURE_BYTES:
        raise VRFError(f"Serialized proof must be {SIGNATURE_BYTES} bytes, got {len(data)}")
    return data
