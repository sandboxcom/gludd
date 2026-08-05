"""
SPHINCS+ (FIPS 205 / SLH-DSA) — stateless hash-based signatures using pyspx.

Delegates to the pyspx C-extension bindings for the reference SPHINCS+
implementation. Uses the SLH-DSA-SHAKE-256s parameter set (NIST security
category 1, small/slow variant).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pyspx.shake_256s as _spx


@dataclass(slots=True, frozen=True)
class SphincsParams:
    """Compatibility parameter-set wrapper — sizes come from the pyspx backend."""

    n: int = 16

    @property
    def pk_bytes(self) -> int:
        return _spx.crypto_sign_PUBLICKEYBYTES

    @property
    def sk_bytes(self) -> int:
        return _spx.crypto_sign_SECRETKEYBYTES

    @property
    def sig_bytes(self) -> int:
        return _spx.crypto_sign_BYTES


_PARAMS_SLH_DSA_SHAKE_256s = SphincsParams()


class SphincsPlusError(Exception):
    """Base exception for SPHINCS+ operations."""


def slh_keygen(params: SphincsParams | None = None) -> tuple[bytes, bytes]:
    seed = os.urandom(_spx.crypto_sign_SEEDBYTES)
    return _spx.generate_keypair(seed)


def slh_sign(msg: bytes, sk: bytes, params: SphincsParams | None = None) -> bytes:
    return _spx.sign(msg, sk)


def slh_verify(msg: bytes, sig: bytes, pk: bytes, params: SphincsParams | None = None) -> bool:
    return _spx.verify(msg, sig, pk)


def keygen_small() -> tuple[bytes, bytes]:
    return slh_keygen()


def sign_small(msg: bytes, sk: bytes) -> bytes:
    return slh_sign(msg, sk)


def verify_small(msg: bytes, sig: bytes, pk: bytes) -> bool:
    return slh_verify(msg, sig, pk)
