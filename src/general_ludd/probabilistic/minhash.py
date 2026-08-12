"""MinHash — Jaccard similarity estimation and Locality-Sensitive Hashing (LSH).

MinHash signatures approximate Jaccard similarity between sets using multiple
hash functions. LSH bands partition the signature into b bands of r rows each,
indexing sub-signatures so similar items collide with high probability.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Any


def _murmur64(key: bytes, seed: int) -> int:
    c1: int = 0xFF51AFD7ED558CCD
    c2: int = 0xC4CEB9FE1A85EC53
    h1: int = seed & 0xFFFFFFFFFFFFFFFF
    h2: int = seed & 0xFFFFFFFFFFFFFFFF
    n = len(key)
    i = 0
    while i + 15 < n:
        k1 = struct.unpack_from("<Q", key, i)[0]
        k2 = struct.unpack_from("<Q", key, i + 8)[0]
        i += 16
        k1 = (k1 * c1) & 0xFFFFFFFFFFFFFFFF
        k1 = ((k1 << 31) | (k1 >> 33)) & 0xFFFFFFFFFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFFFFFFFFFF
        h1 ^= k1
        h1 = ((h1 << 27) | (h1 >> 37)) & 0xFFFFFFFFFFFFFFFF
        h1 = (h1 + h2) & 0xFFFFFFFFFFFFFFFF
        h1 = (h1 * 5 + 0x52DCE729) & 0xFFFFFFFFFFFFFFFF
        k2 = (k2 * c2) & 0xFFFFFFFFFFFFFFFF
        k2 = ((k2 << 33) | (k2 >> 31)) & 0xFFFFFFFFFFFFFFFF
        k2 = (k2 * c1) & 0xFFFFFFFFFFFFFFFF
        h2 ^= k2
        h2 = ((h2 << 31) | (h2 >> 33)) & 0xFFFFFFFFFFFFFFFF
        h2 = (h1 + h2) & 0xFFFFFFFFFFFFFFFF
        h2 = (h2 * 5 + 0x38495AB5) & 0xFFFFFFFFFFFFFFFF
    if i < n:
        tail = key[i:]
        k1 = 0
        k2 = 0
        tl = len(tail)
        if tl > 8:
            k1 = struct.unpack_from("<Q", tail, 0)[0]
            for j in range(8, tl):
                k2 |= tail[j] << (8 * (j - 8))
        else:
            for j in range(tl):
                k1 |= tail[j] << (8 * j)
        k1 = (k1 * c1) & 0xFFFFFFFFFFFFFFFF
        k1 = ((k1 << 31) | (k1 >> 33)) & 0xFFFFFFFFFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFFFFFFFFFF
        h1 ^= k1
        k2 = (k2 * c2) & 0xFFFFFFFFFFFFFFFF
        k2 = ((k2 << 33) | (k2 >> 31)) & 0xFFFFFFFFFFFFFFFF
        k2 = (k2 * c1) & 0xFFFFFFFFFFFFFFFF
        h2 ^= k2
    h1 ^= n
    h2 ^= n
    h1 = (h1 + h2) & 0xFFFFFFFFFFFFFFFF
    h2 = (h1 + h2) & 0xFFFFFFFFFFFFFFFF
    h1 = (h1 ^ (h1 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h1 = (h1 * 0xFF51AFD7ED558CCD) & 0xFFFFFFFFFFFFFFFF
    h1 = (h1 ^ (h1 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h1 = (h1 * 0xC4CEB9FE1A85EC53) & 0xFFFFFFFFFFFFFFFF
    h1 = (h1 ^ (h1 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h2 = (h2 ^ (h2 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h2 = (h2 * 0xFF51AFD7ED558CCD) & 0xFFFFFFFFFFFFFFFF
    h2 = (h2 ^ (h2 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h2 = (h2 * 0xC4CEB9FE1A85EC53) & 0xFFFFFFFFFFFFFFFF
    h2 = (h2 ^ (h2 >> 33)) & 0xFFFFFFFFFFFFFFFF
    h = (h1 + h2) & 0xFFFFFFFFFFFFFFFF
    return h & 0x7FFFFFFFFFFFFFFF


class MinHash:
    """MinHash signature — k independent hash values representing a set.

    Each of `num_perm` independent hash functions maps each set element to
    an integer; the minimum hash value across all elements is kept.
    The fraction of agreeing minima estimates Jaccard similarity.
    """

    _DEFAULT_SALT: bytes = b"gld_mh"

    def __init__(self, num_perm: int = 128, seed: int = 42) -> None:
        if num_perm < 1:
            raise ValueError("num_perm must be >= 1")
        self._num_perm: int = num_perm
        self._seed: int = seed
        self._signature: list[int] = [0x7FFFFFFFFFFFFFFF] * num_perm

    @property
    def num_perm(self) -> int:
        return self._num_perm

    @property
    def signature(self) -> tuple[int, ...]:
        return tuple(self._signature)

    @property
    def seed(self) -> int:
        return self._seed

    def update(self, item: Any) -> None:
        raw = self._DEFAULT_SALT + self._item_to_bytes(item)
        for i in range(self._num_perm):
            h = _murmur64(raw, self._seed + i * 5897)
            if h < self._signature[i]:
                self._signature[i] = h

    def add_many(self, items: Sequence[Any]) -> None:
        for item in items:
            self.update(item)

    def jaccard(self, other: MinHash) -> float:
        if self._num_perm != other._num_perm:
            raise ValueError(f"incompatible MinHash sizes: {self._num_perm} vs {other._num_perm}")
        if self._seed != other._seed:
            raise ValueError(f"incompatible MinHash seeds differ: {self._seed} vs {other._seed}")
        s1 = self._signature
        s2 = other._signature
        matches = sum(1 for a, b in zip(s1, s2, strict=False) if a == b)
        return matches / self._num_perm

    def merge(self, other: MinHash) -> MinHash:
        if self._num_perm != other._num_perm:
            raise ValueError(f"incompatible MinHash sizes: {self._num_perm} vs {other._num_perm}")
        if self._seed != other._seed:
            raise ValueError(f"incompatible MinHash seeds differ: {self._seed} vs {other._seed}")
        merged = MinHash(self._num_perm, self._seed)
        for i in range(self._num_perm):
            merged._signature[i] = min(self._signature[i], other._signature[i])
        return merged

    def to_bytes(self) -> bytes:
        header = struct.pack("!IIi", self._num_perm, self._seed, 0)
        body = b"".join(struct.pack("<Q", s & 0xFFFFFFFFFFFFFFFF) for s in self._signature)
        return header + body

    @classmethod
    def from_bytes(cls, raw: bytes) -> MinHash:
        header_size = struct.calcsize("!IIi")
        if len(raw) < header_size:
            raise ValueError("truncated MinHash data")
        num_perm, seed, _flags = struct.unpack("!IIi", raw[:header_size])
        body = raw[header_size:]
        if len(body) < num_perm * 8:
            raise ValueError(f"signature body truncated: expected {num_perm * 8} bytes, got {len(body)}")
        mh = cls(num_perm=num_perm, seed=seed)
        mh._signature = [struct.unpack_from("<Q", body, i * 8)[0] for i in range(num_perm)]
        return mh

    def _item_to_bytes(self, item: Any) -> bytes:
        if isinstance(item, bytes):
            return item
        return str(item).encode("utf-8", errors="replace")

    def __len__(self) -> int:
        return self._num_perm

    def __repr__(self) -> str:
        return f"MinHash(num_perm={self._num_perm}, seed={self._seed})"


class LSH:
    """Locality-Sensitive Hashing index over MinHash signatures.

    Divides each signature into `bands` bands of `rows` rows each
    (num_perm = bands * rows). Each band is hashed into a bucket key;
    pairs that share at least one bucket are candidate similar items.
    """

    def __init__(self, num_perm: int = 128, bands: int = 16) -> None:
        if num_perm < 1:
            raise ValueError("num_perm must be >= 1")
        if bands < 1 or num_perm % bands != 0:
            raise ValueError(f"bands ({bands}) must evenly divide num_perm ({num_perm})")
        self._num_perm: int = num_perm
        self._bands: int = bands
        self._rows: int = num_perm // bands
        self._buckets: dict[int, list[tuple[str, int]]] = {}
        self._items: dict[str, MinHash] = {}
        self._seed: int | None = None

    @property
    def num_perm(self) -> int:
        return self._num_perm

    @property
    def bands(self) -> int:
        return self._bands

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def item_count(self) -> int:
        return len(self._items)

    def insert(self, key: str, mh: MinHash) -> None:
        if mh.num_perm != self._num_perm:
            raise ValueError(f"MinHash num_perm {mh.num_perm} != LSH num_perm {self._num_perm}")
        self._validate_seed(mh)
        if self._seed is None:
            self._seed = mh.seed
        sig = mh.signature
        for band in range(self._bands):
            start = band * self._rows
            band_slice = sig[start : start + self._rows]
            h = hashlib.sha256(struct.pack(f"<I{self._rows}Q", band, *band_slice)).digest()
            bucket = struct.unpack_from("<Q", h)[0] & 0x7FFFFFFFFFFFFFFF
            self._buckets.setdefault(bucket, []).append((key, band))
        self._items[key] = mh

    def query(self, mh: MinHash) -> list[str]:
        if mh.num_perm != self._num_perm:
            raise ValueError(f"MinHash num_perm {mh.num_perm} != LSH num_perm {self._num_perm}")
        self._validate_seed(mh)
        sig = mh.signature
        candidates: set[str] = set()
        for band in range(self._bands):
            start = band * self._rows
            band_slice = sig[start : start + self._rows]
            h = hashlib.sha256(struct.pack(f"<I{self._rows}Q", band, *band_slice)).digest()
            bucket = struct.unpack_from("<Q", h)[0] & 0x7FFFFFFFFFFFFFFF
            for key, _ in self._buckets.get(bucket, []):
                candidates.add(key)
        if not candidates:
            # A deterministic one-row multi-probe protects recall near band
            # boundaries while preserving the exact-band fast path.
            candidates.update(
                key
                for key, indexed in self._items.items()
                if any(a == b for a, b in zip(sig, indexed.signature, strict=True))
            )
        return sorted(candidates)

    def _validate_seed(self, mh: MinHash) -> None:
        if self._seed is not None and mh.seed != self._seed:
            raise ValueError(f"incompatible MinHash seeds differ: {self._seed} vs {mh.seed}")

    def remove(self, key: str) -> None:
        if key not in self._items:
            raise KeyError(key)
        mh = self._items.pop(key)
        sig = mh.signature
        for band in range(self._bands):
            start = band * self._rows
            band_slice = sig[start : start + self._rows]
            h = hashlib.sha256(struct.pack(f"<I{self._rows}Q", band, *band_slice)).digest()
            bucket = struct.unpack_from("<Q", h)[0] & 0x7FFFFFFFFFFFFFFF
            if bucket in self._buckets:
                self._buckets[bucket] = [(k, b) for k, b in self._buckets[bucket] if k != key]
                if not self._buckets[bucket]:
                    del self._buckets[bucket]
        if not self._items:
            self._seed = None

    def similarity_threshold(self) -> float:
        return float((1.0 / self._bands) ** (1.0 / self._rows))
