"""Bloom filter — space-efficient probabilistic set with tunable false positive rate."""

from __future__ import annotations

import math
import struct
from typing import Any, ClassVar


class BloomFilter:
    _DEFAULT_SALT: ClassVar[bytes] = b"gld_bloom"

    def __init__(self, capacity: int, error_rate: float) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not (0 < error_rate < 1):
            raise ValueError("error_rate must be in (0, 1)")
        self._capacity = capacity
        self._error_rate = error_rate
        self._size = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self._size = max(self._size, 8)
        self._hash_count = max(1, round((self._size / capacity) * math.log(2)))
        self._bits = bytearray((self._size + 7) // 8)

    # --- public read-only properties -------------------------------------------

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def error_rate(self) -> float:
        return self._error_rate

    @property
    def size(self) -> int:
        return self._size

    @property
    def hash_count(self) -> int:
        return self._hash_count

    # --- core operations -------------------------------------------------------

    def add(self, item: Any) -> None:
        key = self._item_to_bytes(item)
        for i in range(self._hash_count):
            bit = self._hash(key, i) % self._size
            self._bits[bit >> 3] |= 1 << (bit & 7)

    def contains(self, item: Any) -> bool:
        key = self._item_to_bytes(item)
        for i in range(self._hash_count):
            bit = self._hash(key, i) % self._size
            if not (self._bits[bit >> 3] & (1 << (bit & 7))):
                return False
        return True

    # --- serialization ---------------------------------------------------------

    def to_bytes(self) -> bytes:
        header = struct.pack("!IIdI", self._capacity, self._size, self._error_rate, self._hash_count)
        return header + bytes(self._bits)

    @classmethod
    def from_bytes(cls, raw: bytes) -> BloomFilter:
        header_size = struct.calcsize("!IIdI")
        if len(raw) < header_size:
            raise ValueError("truncated bloom filter data")
        capacity, size, error_rate, hash_count = struct.unpack("!IIdI", raw[:header_size])
        bf = cls.__new__(cls)
        bf._capacity = capacity
        bf._size = size
        bf._error_rate = error_rate
        bf._hash_count = hash_count
        bf._bits = bytearray(raw[header_size:])
        expected = (size + 7) // 8
        if len(bf._bits) != expected:
            raise ValueError(f"bit array length mismatch: expected {expected}, got {len(bf._bits)}")
        return bf

    # --- merge -----------------------------------------------------------------

    def merge(self, other: BloomFilter) -> None:
        if self._capacity != other._capacity or self._size != other._size or self._hash_count != other._hash_count:
            raise ValueError("cannot merge bloom filters with different parameters")
        for i in range(len(self._bits)):
            self._bits[i] |= other._bits[i]

    # --- pickle support --------------------------------------------------------

    def __getstate__(self) -> Any:
        return self.to_bytes()

    def __setstate__(self, state: bytes) -> None:
        restored = type(self).from_bytes(state)
        self._capacity = restored._capacity
        self._size = restored._size
        self._error_rate = restored._error_rate
        self._hash_count = restored._hash_count
        self._bits = restored._bits

    # --- internal helpers ------------------------------------------------------

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

    @classmethod
    def _hash(cls, key: bytes, seed: int) -> int:
        data = key + cls._DEFAULT_SALT
        seed_bytes = seed.to_bytes(4, "big")
        h1 = cls._fnv1a(data)
        h2 = cls._fnv1a(data + b"\x01" + seed_bytes)
        return (h1 + seed * h2) & 0xFFFFFFFF

    @staticmethod
    def _fnv1a(data: bytes) -> int:
        h = 0x811C9DC5
        for b in data:
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
        return h
