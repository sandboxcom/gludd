"""RFC 9562 UUID and ULID helpers with monotonic UUIDv7 generation."""

from __future__ import annotations

import os
import threading
import time
import uuid as _uuid

_UUID7_COUNTER_BITS = 42
_UUID7_COUNTER_MAX = (1 << _UUID7_COUNTER_BITS) - 1
_UUID7_COUNTER_SEED_MASK = (1 << (_UUID7_COUNTER_BITS - 1)) - 1
_UUID7_LOCK = threading.Lock()
_uuid7_last_timestamp_ms: int | None = None
_uuid7_last_counter = 0


def uuid4() -> _uuid.UUID:
    """Return a cryptographically random UUIDv4."""
    return _uuid.uuid4()


def uuid7(timestamp_ms: int | None = None) -> _uuid.UUID:
    """Return a time-ordered UUIDv7 with same-millisecond monotonicity.

    The 42-bit counter and 32-bit random tail follow RFC 9562 section 6.2
    Method 1 and the CPython UUIDv7 layout. Explicit timestamps remain exact;
    live clock rollback reuses the prior timestamp and advances the counter.
    """
    global _uuid7_last_counter, _uuid7_last_timestamp_ms

    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    if not 0 <= ms < (1 << 48):
        raise ValueError("timestamp_ms must fit in 48 unsigned bits")

    with _UUID7_LOCK:
        if timestamp_ms is None and _uuid7_last_timestamp_ms is not None and ms < _uuid7_last_timestamp_ms:
            ms = _uuid7_last_timestamp_ms

        if ms == _uuid7_last_timestamp_ms:
            counter = _uuid7_last_counter + 1
            if counter > _UUID7_COUNTER_MAX:
                ms += 1
                counter, tail = _uuid7_counter_and_tail()
            else:
                tail = int.from_bytes(os.urandom(4), "big")
        else:
            counter, tail = _uuid7_counter_and_tail()

        _uuid7_last_timestamp_ms = ms
        _uuid7_last_counter = counter

    counter_hi = (counter >> 30) & 0xFFF
    counter_lo = counter & 0x3FFFFFFF
    value = ms << 80
    value |= 0x7 << 76
    value |= counter_hi << 64
    value |= 0x2 << 62
    value |= counter_lo << 32
    value |= tail & 0xFFFFFFFF
    return _uuid.UUID(int=value)


def _uuid7_counter_and_tail() -> tuple[int, int]:
    random_bits = int.from_bytes(os.urandom(10), "big")
    counter = (random_bits >> 32) & _UUID7_COUNTER_SEED_MASK
    tail = random_bits & 0xFFFFFFFF
    return counter, tail


def uuid8(custom_a: bytes, custom_b: bytes, custom_c: bytes) -> _uuid.UUID:
    """Return a UUIDv8 assembled from three caller-provided byte fields."""
    a = custom_a[:6].ljust(6, b"\x00")
    b = custom_b[:4].ljust(4, b"\x00")
    c = custom_c[:6].ljust(6, b"\x00")

    time_low = int.from_bytes(a[:4], "big")
    time_mid = int.from_bytes(a[4:6], "big")
    time_hi_version = 0x8000 | (int.from_bytes(b[:2], "big") & 0x0FFF)

    clock_seq_hi_variant = 0x80 | (b[2] & 0x3F)
    clock_seq_low = b[3]

    node = int.from_bytes(c[:6], "big")

    return _uuid.UUID(
        fields=(
            time_low,
            time_mid,
            time_hi_version,
            clock_seq_hi_variant,
            clock_seq_low,
            node,
        )
    )


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _crockford_encode(data: bytes) -> str:
    result: list[str] = []
    bits = int.from_bytes(data, "big")
    for _ in range(26):
        result.append(_CROCKFORD[bits & 0x1F])
        bits >>= 5
    result.reverse()
    return "".join(result)


def ulid(timestamp_ms: int | None = None) -> str:
    """Return a Crockford-base32 ULID for the supplied or current timestamp."""
    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    time_part = ms.to_bytes(6, "big")
    rand_part = os.urandom(10)
    raw = time_part + rand_part
    return _crockford_encode(raw)


def parse_ulid(ulid_str: str) -> int:
    """Decode one canonical 26-character ULID into its integer value."""
    if len(ulid_str) != 26:
        raise ValueError(f"ULID must be 26 characters, got {len(ulid_str)}")
    decoded = 0
    for ch in ulid_str:
        val = _CROCKFORD.find(ch)
        if val == -1:
            raise ValueError(f"Invalid ULID character: {ch!r}")
        decoded = (decoded << 5) | val
    return decoded


def is_valid_uuid(value: str | None, version: int | None = None) -> bool:
    """Return whether a string is a UUID of the optional requested version."""
    if value is None:
        return False
    try:
        u = _uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return not (version is not None and u.version != version)


def is_valid_ulid(value: str) -> bool:
    """Return whether a string is a canonical 26-character Crockford ULID."""
    if not isinstance(value, str) or len(value) != 26:
        return False
    return all(ch in _CROCKFORD for ch in value)


def extract_timestamp(u: _uuid.UUID) -> int | None:
    """Extract Unix milliseconds from UUIDv7 or UUIDv1, when available."""
    if u.version == 7:
        fields = u.fields
        ts = (fields[0] << 16) | fields[1]
        return ts
    if u.version == 1:
        return int((u.time - 0x01B21DD213814000) / 10000)
    return None


def extract_ulid_timestamp(ulid_str: str) -> int:
    """Extract Unix milliseconds from a validated ULID string."""
    if not is_valid_ulid(ulid_str):
        raise ValueError(f"Invalid ULID: {ulid_str!r}")
    decoded = parse_ulid(ulid_str)
    return (decoded >> 80) & ((1 << 48) - 1)


__all__ = [
    "extract_timestamp",
    "extract_ulid_timestamp",
    "is_valid_ulid",
    "is_valid_uuid",
    "parse_ulid",
    "ulid",
    "uuid4",
    "uuid7",
    "uuid8",
]
