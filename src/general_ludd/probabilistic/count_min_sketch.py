"""Count-Min Sketch — probabilistic frequency estimator with conservative update."""

from __future__ import annotations

import math
import struct
from typing import Any


class CountMinSketch:
    _DEFAULT_SALT: bytes = b"gld_cms"

    def __init__(
        self,
        width: int,
        depth: int,
        conservative: bool = False,
    ) -> None:
        if width < 1:
            raise ValueError("width must be >= 1")
        if depth < 1:
            raise ValueError("depth must be >= 1")
        self._width = width
        self._depth = depth
        self._conservative = conservative
        self._counters = [[0] * width for _ in range(depth)]

    @property
    def width(self) -> int:
        return self._width

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def conservative(self) -> bool:
        return self._conservative

    @classmethod
    def from_epsilon_delta(cls, epsilon: float, delta: float) -> CountMinSketch:
        if not (0 < epsilon < 1):
            raise ValueError("epsilon must be in (0, 1)")
        if not (0 < delta < 1):
            raise ValueError("delta must be in (0, 1)")
        width = math.ceil(math.e / epsilon)
        depth = math.ceil(math.log(1.0 / delta))
        return cls(width=width, depth=depth)

    def add(self, item: Any, count: int = 1) -> None:
        if count < 1:
            raise ValueError("count must be >= 1")
        key = self._item_to_bytes(item)
        if self._conservative:
            minimum = min(self._counters[i][self._hash(key, i) % self._width] for i in range(self._depth))
            for i in range(self._depth):
                idx = self._hash(key, i) % self._width
                current = self._counters[i][idx]
                if current == minimum:
                    self._counters[i][idx] += count
        else:
            for i in range(self._depth):
                idx = self._hash(key, i) % self._width
                self._counters[i][idx] += count

    def estimate(self, item: Any) -> int:
        key = self._item_to_bytes(item)
        return min(self._counters[i][self._hash(key, i) % self._width] for i in range(self._depth))

    def heavy_hitters(
        self,
        threshold: int,
        candidates: set[Any] | None = None,
    ) -> list[tuple[Any, int]]:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if candidates is None:
            return []
        results: list[tuple[Any, int]] = []
        for item in candidates:
            est = self.estimate(item)
            if est >= threshold:
                results.append((item, est))
        results.sort(key=lambda x: -x[1])
        return results

    def merge(self, other: CountMinSketch) -> None:
        if self._width != other._width or self._depth != other._depth:
            raise ValueError("cannot merge count-min sketches with different dimensions")
        for i in range(self._depth):
            for j in range(self._width):
                self._counters[i][j] += other._counters[i][j]

    def clear(self) -> None:
        for i in range(self._depth):
            self._counters[i] = [0] * self._width

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!II?",
            self._width,
            self._depth,
            self._conservative,
        )
        body = b""
        for row in self._counters:
            body += struct.pack(f"!{self._width}I", *row)
        return header + body

    @classmethod
    def from_bytes(cls, raw: bytes) -> CountMinSketch:
        header_size = struct.calcsize("!II?")
        if len(raw) < header_size:
            raise ValueError("truncated count-min sketch data")
        width, depth, conservative = struct.unpack("!II?", raw[:header_size])
        body = raw[header_size:]
        row_size = width * 4
        expected_body = row_size * depth
        if len(body) != expected_body:
            raise ValueError(f"body length mismatch: expected {expected_body}, got {len(body)}")
        cms = cls.__new__(cls)
        cms._width = width
        cms._depth = depth
        cms._conservative = conservative
        cms._counters = []
        for i in range(depth):
            offset = i * row_size
            row = list(struct.unpack(f"!{width}I", body[offset : offset + row_size]))
            cms._counters.append(row)
        return cms

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
        data = key + CountMinSketch._DEFAULT_SALT
        seed_bytes = seed.to_bytes(4, "big")
        h1 = CountMinSketch._fnv1a(data)
        h2 = CountMinSketch._fnv1a(data + b"\x01" + seed_bytes)
        return (h1 + seed * h2) & 0x7FFFFFFF

    @staticmethod
    def _fnv1a(data: bytes) -> int:
        h = 0x811C9DC5
        for b in data:
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
        return h
