"""Cuckoo Filter — probabilistic multiset with counting support and item removal.

Uses cuckoo hashing with fingerprints in a compact hash table. Supports
add, remove, and contains operations with configurable fingerprint size.
Each bucket holds multiple entries, and items are relocated via cuckoo
kicking when a bucket is full.
"""

from __future__ import annotations

import math
import random
import struct
from typing import Any


class CuckooFilter:
    _DEFAULT_SALT: bytes = b"gld_ckf"
    _MAX_KICKS: int = 500

    def __init__(
        self,
        capacity: int,
        error_rate: float = 0.01,
        bucket_size: int = 4,
        seed: int = 0,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not (0 < error_rate < 1):
            raise ValueError("error_rate must be in (0, 1)")
        if bucket_size < 2:
            raise ValueError("bucket_size must be >= 2")
        self._capacity = capacity
        self._error_rate = error_rate
        self._bucket_size = bucket_size
        num_buckets = max(2, math.ceil(capacity / bucket_size))
        self._num_buckets = self._next_power_of_two(num_buckets)
        self._fingerprint_bits = max(4, math.ceil(-math.log2(error_rate)))
        self._fingerprint_mask = (1 << self._fingerprint_bits) - 1
        self._table = bytearray(
            self._num_buckets * self._bucket_size * self._fingerprint_bits // 8
            + ((self._num_buckets * self._bucket_size * self._fingerprint_bits) % 8 > 0)
        )
        self._count = 0
        self._rng = random.Random(seed)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def error_rate(self) -> float:
        return self._error_rate

    @property
    def bucket_size(self) -> int:
        return self._bucket_size

    @property
    def num_buckets(self) -> int:
        return self._num_buckets

    @property
    def fingerprint_bits(self) -> int:
        return self._fingerprint_bits

    @property
    def size(self) -> int:
        return self._count

    def add(self, item: Any) -> bool:
        key = self._item_to_bytes(item)
        fp = self._fingerprint(key)
        i1 = self._index_hash(key)
        i2 = self._alt_index(i1, fp)
        if self._insert(fp, i1):
            self._count += 1
            return True
        if self._insert(fp, i2):
            self._count += 1
            return True
        idx = i1 if self._rng.randint(0, 1) == 0 else i2
        for _ in range(self._MAX_KICKS):
            fp, old_fp = self._swap_fingerprint(fp, idx), fp
            if old_fp == 0:
                return False
            idx = self._alt_index(idx, fp)
            if self._insert(fp, idx):
                self._count += 1
                return True
        return False

    def remove(self, item: Any) -> bool:
        key = self._item_to_bytes(item)
        fp = self._fingerprint(key)
        i1 = self._index_hash(key)
        i2 = self._alt_index(i1, fp)
        if self._delete(fp, i1):
            self._count -= 1
            return True
        if self._delete(fp, i2):
            self._count -= 1
            return True
        return False

    def contains(self, item: Any) -> bool:
        key = self._item_to_bytes(item)
        fp = self._fingerprint(key)
        i1 = self._index_hash(key)
        i2 = self._alt_index(i1, fp)
        return self._lookup(fp, i1) or self._lookup(fp, i2)

    def load_factor(self) -> float:
        return self._count / (self._num_buckets * self._bucket_size)

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!IIIII",
            self._capacity,
            self._num_buckets,
            self._bucket_size,
            self._fingerprint_bits,
            self._count,
        )
        return header + bytes(self._table)

    @classmethod
    def from_bytes(cls, raw: bytes) -> CuckooFilter:
        header_size = struct.calcsize("!IIIII")
        if len(raw) < header_size:
            raise ValueError("truncated cuckoo filter data")
        capacity, num_buckets, bucket_size, fingerprint_bits, count = struct.unpack("!IIIII", raw[:header_size])
        cf = cls.__new__(cls)
        cf._capacity = capacity
        cf._num_buckets = num_buckets
        cf._bucket_size = bucket_size
        cf._fingerprint_bits = fingerprint_bits
        cf._fingerprint_mask = (1 << fingerprint_bits) - 1
        cf._table = bytearray(raw[header_size:])
        cf._count = count
        cf._rng = random.Random(0)
        expected = num_buckets * bucket_size * fingerprint_bits // 8 + (
            (num_buckets * bucket_size * fingerprint_bits) % 8 > 0
        )
        if len(cf._table) != expected:
            raise ValueError(f"table length mismatch: expected {expected}, got {len(cf._table)}")
        return cf

    def _fingerprint(self, key: bytes) -> int:
        h = self._hash64(key)
        fp = h & self._fingerprint_mask
        if fp == 0:
            fp = 1
        return fp

    def _index_hash(self, key: bytes) -> int:
        h = self._hash64(key)
        return h >> 32 & (self._num_buckets - 1)

    def _alt_index(self, idx: int, fp: int) -> int:
        h = self._hash_fp(fp.to_bytes(4, "big"))
        return (idx ^ (h & (self._num_buckets - 1))) & (self._num_buckets - 1)

    def _insert(self, fp: int, bucket_idx: int) -> bool:
        for slot in range(self._bucket_size):
            existing = self._get_entry(bucket_idx, slot)
            if existing == 0:
                self._set_entry(bucket_idx, slot, fp)
                return True
        return False

    def _delete(self, fp: int, bucket_idx: int) -> bool:
        for slot in range(self._bucket_size):
            if self._get_entry(bucket_idx, slot) == fp:
                self._set_entry(bucket_idx, slot, 0)
                return True
        return False

    def _lookup(self, fp: int, bucket_idx: int) -> bool:
        return any(self._get_entry(bucket_idx, slot) == fp for slot in range(self._bucket_size))

    def _swap_fingerprint(self, fp: int, bucket_idx: int) -> int:
        slot = self._rng.randint(0, self._bucket_size - 1)
        old = self._get_entry(bucket_idx, slot)
        self._set_entry(bucket_idx, slot, fp)
        return old

    def _get_entry(self, bucket_idx: int, slot: int) -> int:
        bit_start = (bucket_idx * self._bucket_size + slot) * self._fingerprint_bits
        val = 0
        for offset in range(self._fingerprint_bits):
            byte_pos = (bit_start + offset) >> 3
            bit_pos = (bit_start + offset) & 7
            if self._table[byte_pos] & (1 << bit_pos):
                val |= 1 << offset
        return val

    def _set_entry(self, bucket_idx: int, slot: int, value: int) -> None:
        bit_start = (bucket_idx * self._bucket_size + slot) * self._fingerprint_bits
        for offset in range(self._fingerprint_bits):
            byte_pos = (bit_start + offset) >> 3
            bit_pos = (bit_start + offset) & 7
            if value & (1 << offset):
                self._table[byte_pos] |= 1 << bit_pos
            else:
                self._table[byte_pos] &= ~(1 << bit_pos)

    @staticmethod
    def _next_power_of_two(n: int) -> int:
        p = 1
        while p < n:
            p <<= 1
        return p

    @staticmethod
    def _hash64(key: bytes) -> int:
        data = key + CuckooFilter._DEFAULT_SALT
        h = 0xCBF29CE484222325
        for b in data:
            h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        h2 = 0xCBF29CE484222325
        for b in data + b"\x01":
            h2 = ((h2 ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return ((h << 32) | (h2 & 0xFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF

    @staticmethod
    def _hash_fp(key: bytes) -> int:
        h = 0x811C9DC5
        for b in key + CuckooFilter._DEFAULT_SALT + b"_alt":
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
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
