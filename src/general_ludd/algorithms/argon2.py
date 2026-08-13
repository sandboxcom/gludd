"""Argon2id password hashing — RFC 9106.

Uses argon2-cffi library.
"""

from __future__ import annotations

import base64
import os
from typing import NoReturn

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exc
from argon2.low_level import Type

TIME_COST = 2
MEMORY_COST = 256
PARALLELISM = 1
HASH_LEN = 32
SALT_LEN = 16


class Argon2Error(Exception):
    """Base exception for Argon2id operations."""


def _raise(msg: str) -> NoReturn:
    raise Argon2Error(msg)


def _b64enc(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def generate_salt(length: int = SALT_LEN) -> str:
    if length < 1:
        _raise(f"salt length must be >= 1, got {length}")
    return _b64enc(os.urandom(length)).rstrip("=")


def _make_ph(
    time_cost: int,
    memory_cost: int,
    parallelism: int,
    hash_len: int,
) -> PasswordHasher:
    try:
        return PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            type=Type.ID,
        )
    except (ValueError, TypeError) as e:
        _raise(str(e))


def argon2id_hash(
    password: str,
    salt: str,
    time_cost: int = TIME_COST,
    memory_cost: int = MEMORY_COST,
    parallelism: int = PARALLELISM,
    hash_len: int = HASH_LEN,
) -> str:
    if time_cost < 1:
        _raise(f"time_cost must be >= 1, got {time_cost}")
    if memory_cost < 8 * parallelism:
        _raise(f"memory_cost must be >= {8 * parallelism} KiB, got {memory_cost}")
    if parallelism < 1:
        _raise(f"parallelism must be >= 1, got {parallelism}")
    if hash_len < 4:
        _raise(f"hash_len must be >= 4, got {hash_len}")
    if len(salt) == 0:
        _raise("salt must not be empty")

    salt_bytes = _b64dec(salt)
    ph = _make_ph(time_cost, memory_cost, parallelism, hash_len)
    try:
        return ph.hash(password, salt=salt_bytes)
    except argon2_exc.HashingError as e:
        _raise(str(e))


def argon2id_verify(password: str, encoded_hash: str) -> bool:
    ph = PasswordHasher()
    try:
        return ph.verify(encoded_hash, password)
    except argon2_exc.VerifyMismatchError:
        return False
    except (
        argon2_exc.InvalidHashError,
        argon2_exc.VerificationError,
    ) as e:
        _raise(str(e))
