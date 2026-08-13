"""HyperLogLog — cardinality estimation with tunable error rate.

Space-efficient distinct-element counting using stochastic averaging.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any


class HyperLogLog:
    _DEFAULT_SALT: bytes = b"gld_hll"
    _SERIAL_MAGIC: bytes = b"HLL1"
    _LEGACY_HASH_DOMAIN: int = 1
    _CURRENT_HASH_DOMAIN: int = 2
    _HASH_PERSON: bytes = b"gludd-hll-v2"

    def __init__(self, precision: int = 14) -> None:
        if precision < 4 or precision > 18:
            raise ValueError("precision must be in [4, 18]")
        self._precision = precision
        self._m = 1 << precision
        self._registers = bytearray(self._m)
        self._alpha = self._compute_alpha(self._m)
        self._hash_domain_version = self._CURRENT_HASH_DOMAIN

    @property
    def precision(self) -> int:
        return self._precision

    @property
    def register_count(self) -> int:
        return self._m

    @property
    def hash_domain_version(self) -> int:
        return self._hash_domain_version

    def add(self, item: Any) -> None:
        key = self._item_to_bytes(item)
        h = self._hash64_for_domain(key, self._hash_domain_version)
        idx = h & (self._m - 1)
        w = h >> self._precision
        rho = self._rho(w)
        if rho > self._registers[idx]:
            self._registers[idx] = rho

    def count(self) -> int:
        z = sum(2.0 ** (-r) for r in self._registers)
        e = self._alpha * self._m * self._m / z
        if e <= (5.0 / 2.0) * self._m:
            v = sum(1 for r in self._registers if r == 0)
            if v > 0:
                e = self._m * math.log(self._m / v)
        if e > (1.0 / 30.0) * (1 << 32):
            e = -(1 << 32) * math.log(1.0 - min(e, (1 << 32) - 1.0) / (1 << 32))
        return int(e)

    def merge(self, other: HyperLogLog) -> None:
        if self._precision != other._precision:
            raise ValueError("cannot merge HyperLogLog instances with different precision")
        if self._hash_domain_version != other._hash_domain_version:
            raise ValueError("cannot merge HyperLogLog instances from different hash domains")
        for i in range(self._m):
            if other._registers[i] > self._registers[i]:
                self._registers[i] = other._registers[i]

    def error_bound(self) -> float:
        return 1.04 / math.sqrt(self._m)

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!4sBII",
            self._SERIAL_MAGIC,
            self._hash_domain_version,
            self._precision,
            self._m,
        )
        return header + bytes(self._registers)

    @classmethod
    def from_bytes(cls, raw: bytes) -> HyperLogLog:
        if raw.startswith(cls._SERIAL_MAGIC):
            header_size = struct.calcsize("!4sBII")
            if len(raw) < header_size:
                raise ValueError("truncated HyperLogLog data")
            _magic, hash_domain, precision, m = struct.unpack(
                "!4sBII", raw[:header_size]
            )
            if hash_domain not in {
                cls._LEGACY_HASH_DOMAIN,
                cls._CURRENT_HASH_DOMAIN,
            }:
                raise ValueError(f"unsupported HyperLogLog hash domain: {hash_domain}")
        else:
            header_size = struct.calcsize("!II")
            if len(raw) < header_size:
                raise ValueError("truncated HyperLogLog data")
            precision, m = struct.unpack("!II", raw[:header_size])
            hash_domain = cls._LEGACY_HASH_DOMAIN

        if precision < 4 or precision > 18 or m != 1 << precision:
            raise ValueError(
                f"invalid HyperLogLog geometry: precision={precision}, registers={m}"
            )
        registers = bytearray(raw[header_size:])
        if len(registers) != m:
            raise ValueError(
                f"register array length mismatch: expected {m}, got {len(registers)}"
            )

        hll = cls.__new__(cls)
        hll._precision = precision
        hll._m = m
        hll._registers = registers
        hll._alpha = cls._compute_alpha(m)
        hll._hash_domain_version = hash_domain
        return hll

    def _rho(self, w: int) -> int:
        bits = 64 - self._precision
        if w == 0:
            return bits + 1
        return bits - w.bit_length() + 1

    @staticmethod
    def _compute_alpha(m: int) -> float:
        if m == 16:
            return 0.673
        if m == 32:
            return 0.697
        if m == 64:
            return 0.709
        return 0.7213 / (1.0 + 1.079 / m)

    @staticmethod
    def _hash64(key: bytes) -> int:
        digest = hashlib.blake2b(
            key,
            digest_size=8,
            person=HyperLogLog._HASH_PERSON,
        ).digest()
        return int.from_bytes(digest, "big")

    @staticmethod
    def _legacy_hash64(key: bytes) -> int:
        data = key + HyperLogLog._DEFAULT_SALT
        h1 = HyperLogLog._fnv1a_64(data)
        h2 = HyperLogLog._fnv1a_64(data + b"\x01")
        return ((h1 << 32) | (h2 & 0xFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF

    @classmethod
    def _hash64_for_domain(cls, key: bytes, hash_domain: int) -> int:
        if hash_domain == cls._CURRENT_HASH_DOMAIN:
            return cls._hash64(key)
        if hash_domain == cls._LEGACY_HASH_DOMAIN:
            return cls._legacy_hash64(key)
        raise ValueError(f"unsupported HyperLogLog hash domain: {hash_domain}")

    @staticmethod
    def _fnv1a_64(data: bytes) -> int:
        h = 0xCBF29CE484222325
        for b in data:
            h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return h

    @staticmethod
    def _item_to_bytes(item: Any) -> bytes:
        if isinstance(item, str):
            return item.encode("utf-8")
        if isinstance(item, bytes):
            return item
        if isinstance(item, int):
            return str(item).encode("utf-8")
        if isinstance(item, float):
            return repr(item).encode("utf-8")
        return str(item).encode("utf-8")
