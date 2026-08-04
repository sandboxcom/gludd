"""Compressed bitmap index with Roaring Bitmap operations.

A two-level compressed bitmap for efficient set membership, logical
operations, and serialization — suitable for column-store indexing,
inverted indices, and fast Boolean query evaluation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass
class _Container:
    key: int
    bitmap: int = 0

    def copy(self) -> _Container:
        return _Container(key=self.key, bitmap=self.bitmap)


class BitmapIndex:
    """Compressed bitmap supporting set/add, logical ops, cardinality, serialization."""

    def __init__(self) -> None:
        self._containers: dict[int, _Container] = {}

    def add(self, value: int) -> None:
        if value < 0:
            raise ValueError("BitmapIndex supports non-negative integers only")
        container_key = value >> 16
        offset = value & 0xFFFF
        container = self._containers.get(container_key)
        if container is None:
            container = _Container(key=container_key)
            self._containers[container_key] = container
        container.bitmap |= 1 << offset

    def remove(self, value: int) -> None:
        if value < 0:
            raise ValueError("BitmapIndex supports non-negative integers only")
        container_key = value >> 16
        offset = value & 0xFFFF
        container = self._containers.get(container_key)
        if container is not None:
            container.bitmap &= ~(1 << offset)
            if container.bitmap == 0:
                del self._containers[container_key]

    def contains(self, value: int) -> bool:
        if value < 0:
            return False
        container_key = value >> 16
        offset = value & 0xFFFF
        container = self._containers.get(container_key)
        if container is None:
            return False
        return bool(container.bitmap & (1 << offset))

    def __contains__(self, value: int) -> bool:
        return self.contains(value)

    def __len__(self) -> int:
        return self.cardinality()

    def __iter__(self) -> Iterator[int]:
        return iter(self.to_set())

    def cardinality(self) -> int:
        total = 0
        for container in self._containers.values():
            total += container.bitmap.bit_count()
        return total

    def to_set(self) -> set[int]:
        result: set[int] = set()
        for container in self._containers.values():
            key = container.key
            bitmap = container.bitmap
            base = key << 16
            pos = 0
            while bitmap:
                if bitmap & 1:
                    result.add(base | pos)
                bitmap >>= 1
                pos += 1
        return result

    def __and__(self, other: BitmapIndex) -> BitmapIndex:
        result = BitmapIndex()
        for key, container in self._containers.items():
            if key in other._containers:
                combined = container.bitmap & other._containers[key].bitmap
                if combined:
                    result._containers[key] = _Container(key=key, bitmap=combined)
        return result

    def __or__(self, other: BitmapIndex) -> BitmapIndex:
        result = BitmapIndex()
        all_keys = set(self._containers.keys()) | set(other._containers.keys())
        for key in all_keys:
            left = self._containers[key].bitmap if key in self._containers else 0
            right = other._containers[key].bitmap if key in other._containers else 0
            combined = left | right
            if combined:
                result._containers[key] = _Container(key=key, bitmap=combined)
        return result

    def __xor__(self, other: BitmapIndex) -> BitmapIndex:
        result = BitmapIndex()
        all_keys = set(self._containers.keys()) | set(other._containers.keys())
        for key in all_keys:
            left = self._containers[key].bitmap if key in self._containers else 0
            right = other._containers[key].bitmap if key in other._containers else 0
            combined = left ^ right
            if combined:
                result._containers[key] = _Container(key=key, bitmap=combined)
        return result

    def __invert__(self) -> BitmapIndex:
        result = BitmapIndex()
        for key, container in self._containers.items():
            inverted = ~container.bitmap & 0xFFFF
            if inverted:
                result._containers[key] = _Container(key=key, bitmap=inverted)
        return result

    def __sub__(self, other: BitmapIndex) -> BitmapIndex:
        result = BitmapIndex()
        for key, container in self._containers.items():
            other_bitmap = other._containers[key].bitmap if key in other._containers else 0
            diff = container.bitmap & ~other_bitmap
            if diff:
                result._containers[key] = _Container(key=key, bitmap=diff)
        return result

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BitmapIndex):
            return NotImplemented
        if self.cardinality() != other.cardinality():
            return False
        for key, container in self._containers.items():
            if key not in other._containers:
                return False
            if container.bitmap != other._containers[key].bitmap:
                return False
        return True

    def copy(self) -> BitmapIndex:
        result = BitmapIndex()
        for key, container in self._containers.items():
            result._containers[key] = container.copy()
        return result

    def clear(self) -> None:
        self._containers.clear()

    def is_empty(self) -> bool:
        return len(self._containers) == 0

    def container_count(self) -> int:
        return len(self._containers)

    def to_bytes(self) -> bytes:
        parts: list[bytes] = []
        count = len(self._containers)
        parts.append(count.to_bytes(4, "big"))
        for key, container in self._containers.items():
            parts.append(key.to_bytes(2, "big"))
            byte_len = (container.bitmap.bit_length() + 7) // 8
            parts.append(byte_len.to_bytes(2, "big"))
            if byte_len > 0:
                parts.append(container.bitmap.to_bytes(byte_len, "big"))
        return b"".join(parts)

    @classmethod
    def from_bytes(cls, data: bytes) -> BitmapIndex:
        if len(data) < 4:
            raise ValueError("data too short for BitmapIndex header")
        count = int.from_bytes(data[:4], "big")
        result = cls()
        offset = 4
        for _ in range(count):
            if offset + 4 > len(data):
                raise ValueError("data truncated in container entries")
            key = int.from_bytes(data[offset : offset + 2], "big")
            byte_len = int.from_bytes(data[offset + 2 : offset + 4], "big")
            offset += 4
            if byte_len > 0:
                if offset + byte_len > len(data):
                    raise ValueError("data truncated in container bitmap")
                bitmap = int.from_bytes(data[offset : offset + byte_len], "big")
                offset += byte_len
            else:
                bitmap = 0
            if bitmap:
                result._containers[key] = _Container(key=key, bitmap=bitmap)
        return result

    def bulk_add(self, values: list[int]) -> None:
        for v in values:
            self.add(v)

    @classmethod
    def from_iterable(cls, values: list[int]) -> BitmapIndex:
        result = cls()
        result.bulk_add(values)
        return result
