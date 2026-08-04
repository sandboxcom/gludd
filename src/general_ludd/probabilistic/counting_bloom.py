"""Counting Bloom Filter — probabilistic multiset with add/remove support.

Each slot stores a small integer counter (default 4 bits) instead of a single bit,
supporting item removal with bounded false-positive rate.
"""

from __future__ import annotations

import math
import struct
from typing import Any


class CountingBloomFilter:
    _DEFAULT_SALT: bytes = b"gld_cbf"

    def __init__(
        self,
        capacity: int,
        error_rate: float = 0.01,
        counter_bits: int = 4,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not (0 < error_rate < 1):
            raise ValueError("error_rate must be in (0, 1)")
        if counter_bits < 1 or counter_bits > 16:
            raise ValueError("counter_bits must be in [1, 16]")
        self._capacity = capacity
        self._error_rate = error_rate
        self._counter_bits = counter_bits
        self._counter_max = (1 << counter_bits) - 1
        self._size = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self._size = max(self._size, 8)
        self._hash_count = max(1, round((self._size / capacity) * math.log(2)))
        total_bits = self._size * counter_bits
        self._counters = bytearray((total_bits + 7) // 8)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def error_rate(self) -> float:
        return self._error_rate

    @property
    def counter_bits(self) -> int:
        return self._counter_bits

    @property
    def hash_count(self) -> int:
        return self._hash_count

    @property
    def slot_count(self) -> int:
        return self._size

    def add(self, item: Any) -> None:
        key = self._item_to_bytes(item)
        for i in range(self._hash_count):
            bit_idx = self._hash(key, i) % self._size
            val = self._get_counter(bit_idx)
            if val < self._counter_max:
                self._set_counter(bit_idx, val + 1)

    def remove(self, item: Any) -> None:
        key = self._item_to_bytes(item)
        for i in range(self._hash_count):
            bit_idx = self._hash(key, i) % self._size
            val = self._get_counter(bit_idx)
            if val > 0:
                self._set_counter(bit_idx, val - 1)

    def count(self, item: Any) -> int:
        key = self._item_to_bytes(item)
        min_val = self._counter_max
        for i in range(self._hash_count):
            bit_idx = self._hash(key, i) % self._size
            val = self._get_counter(bit_idx)
            if val < min_val:
                min_val = val
            if min_val == 0:
                return 0
        return min_val

    def contains(self, item: Any) -> bool:
        return self.count(item) > 0

    def merge(self, other: CountingBloomFilter) -> None:
        if (
            self._capacity != other._capacity
            or self._size != other._size
            or self._hash_count != other._hash_count
            or self._counter_bits != other._counter_bits
        ):
            raise ValueError("cannot merge counting bloom filters with different parameters")
        for i in range(self._size):
            a = self._get_counter(i)
            b = other._get_counter(i)
            new_val = min(a + b, self._counter_max)
            self._set_counter(i, new_val)

    def estimated_count(self) -> float:
        zeros = sum(1 for i in range(self._size) if self._get_counter(i) == 0)
        if zeros == self._size:
            return 0.0
        return -self._size / self._hash_count * math.log(zeros / self._size)

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!IIdII",
            self._capacity,
            self._size,
            self._error_rate,
            self._hash_count,
            self._counter_bits,
        )
        return header + bytes(self._counters)

    @classmethod
    def from_bytes(cls, raw: bytes) -> CountingBloomFilter:
        header_size = struct.calcsize("!IIdII")
        if len(raw) < header_size:
            raise ValueError("truncated counting bloom filter data")
        capacity, size, error_rate, hash_count, counter_bits = struct.unpack("!IIdII", raw[:header_size])
        cbf = cls.__new__(cls)
        cbf._capacity = capacity
        cbf._size = size
        cbf._error_rate = error_rate
        cbf._hash_count = hash_count
        cbf._counter_bits = counter_bits
        cbf._counter_max = (1 << counter_bits) - 1
        cbf._counters = bytearray(raw[header_size:])
        expected = ((size * counter_bits) + 7) // 8
        if len(cbf._counters) != expected:
            raise ValueError(f"counter array length mismatch: expected {expected}, got {len(cbf._counters)}")
        return cbf

    def _get_counter(self, idx: int) -> int:
        bit_start = idx * self._counter_bits
        val = 0
        for offset in range(self._counter_bits):
            byte_pos = (bit_start + offset) >> 3
            bit_pos = (bit_start + offset) & 7
            if self._counters[byte_pos] & (1 << bit_pos):
                val |= 1 << offset
        return val

    def _set_counter(self, idx: int, value: int) -> None:
        bit_start = idx * self._counter_bits
        for offset in range(self._counter_bits):
            byte_pos = (bit_start + offset) >> 3
            bit_pos = (bit_start + offset) & 7
            if value & (1 << offset):
                self._counters[byte_pos] |= 1 << bit_pos
            else:
                self._counters[byte_pos] &= ~(1 << bit_pos)

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

    @staticmethod
    def _hash(key: bytes, seed: int) -> int:
        data = key + CountingBloomFilter._DEFAULT_SALT
        seed_bytes = seed.to_bytes(4, "big")
        h1 = CountingBloomFilter._fnv1a(data)
        h2 = CountingBloomFilter._fnv1a(data + b"\x01" + seed_bytes)
        return (h1 + seed * h2) & 0x7FFFFFFF

    @staticmethod
    def _fnv1a(data: bytes) -> int:
        h = 0x811C9DC5
        for b in data:
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
        return h
