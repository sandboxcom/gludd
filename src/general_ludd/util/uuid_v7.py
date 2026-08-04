from __future__ import annotations

import os
import time
import uuid as _uuid


def uuid4() -> _uuid.UUID:
    return _uuid.uuid4()


def uuid7(timestamp_ms: int | None = None) -> _uuid.UUID:
    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)

    rand_bytes = os.urandom(10)
    rand_a = (rand_bytes[0] << 4) | (rand_bytes[1] >> 4)
    rand_b = int.from_bytes(rand_bytes[1:], "big") & ((1 << 74) - 1)

    time_low = (ms >> 16) & 0xFFFFFFFF
    time_mid = ms & 0xFFFF
    time_hi_version = 0x7000 | (rand_a & 0xFFF)

    clock_seq_hi_variant = 0x80 | ((rand_b >> 56) & 0x3F)
    clock_seq_low = (rand_b >> 48) & 0xFF
    node = rand_b & 0xFFFFFFFFFFFF

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


def _uuid7_raw() -> tuple[int, ...]:
    ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    rand_a = (rand_bytes[0] << 4) | (rand_bytes[1] >> 4)
    rand_b = int.from_bytes(rand_bytes[1:], "big") & ((1 << 74) - 1)
    return (ms, rand_a, rand_b)


def uuid8(custom_a: bytes, custom_b: bytes, custom_c: bytes) -> _uuid.UUID:
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
    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    time_part = ms.to_bytes(6, "big")
    rand_part = os.urandom(10)
    raw = time_part + rand_part
    return _crockford_encode(raw)


def parse_ulid(ulid_str: str) -> int:
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
    if value is None:
        return False
    try:
        u = _uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return not (version is not None and u.version != version)


def is_valid_ulid(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 26:
        return False
    return all(ch in _CROCKFORD for ch in value)


def extract_timestamp(u: _uuid.UUID) -> int | None:
    if u.version == 7:
        fields = u.fields
        ts = (fields[0] << 16) | fields[1]
        return ts
    if u.version == 1:
        return int((u.time - 0x01B21DD213814000) / 10000)
    return None


def extract_ulid_timestamp(ulid_str: str) -> int:
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
