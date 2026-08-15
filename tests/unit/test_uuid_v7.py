from __future__ import annotations

import uuid as _uuid

import pytest

from general_ludd.util.uuid_v7 import (
    _crockford_encode,
    extract_timestamp,
    extract_ulid_timestamp,
    is_valid_ulid,
    is_valid_uuid,
    parse_ulid,
    ulid,
    uuid4,
    uuid7,
    uuid8,
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class TestUuid4:
    def test_returns_uuid_with_version_4(self) -> None:
        u = uuid4()

        assert isinstance(u, _uuid.UUID)
        assert u.version == 4

    def test_generates_unique_values(self) -> None:
        values = {uuid4() for _ in range(1000)}

        assert len(values) == 1000


class TestUuid7:
    def test_default_generates_version_7(self) -> None:
        u = uuid7()

        assert isinstance(u, _uuid.UUID)
        assert u.version == 7

    def test_explicit_timestamp_round_trips(self) -> None:
        ms = 1_700_000_000_000

        u = uuid7(timestamp_ms=ms)

        assert extract_timestamp(u) == ms

    def test_timestamp_zero_boundary(self) -> None:
        u = uuid7(timestamp_ms=0)

        assert extract_timestamp(u) == 0
        assert u.version == 7

    def test_max_timestamp_boundary(self) -> None:
        ms = (1 << 48) - 1

        u = uuid7(timestamp_ms=ms)

        assert extract_timestamp(u) == ms

    def test_variant_bits_are_rfc4122(self) -> None:
        u = uuid7(timestamp_ms=42)

        clock_seq_hi = u.fields[3]
        assert (clock_seq_hi & 0xC0) == 0x80

    def test_generates_unique_values_with_same_timestamp(self) -> None:
        values = {uuid7(timestamp_ms=123456) for _ in range(500)}

        assert len(values) == 500


class TestUuid8:
    def test_full_length_custom_bytes(self) -> None:
        a = bytes(range(6))
        b = bytes(range(4))
        c = bytes(range(6))

        u = uuid8(a, b, c)

        assert u.version == 8
        assert u.fields[5] == int.from_bytes(c, "big")

    def test_short_inputs_are_zero_padded(self) -> None:
        a = b"\x01"
        b = b"\x02"
        c = b"\x03"

        u = uuid8(a, b, c)

        assert u.version == 8
        assert u.fields[5] == int.from_bytes(c.ljust(6, b"\x00"), "big")
        assert (u.fields[3] & 0xC0) == 0x80

    def test_empty_inputs_still_yield_version_8(self) -> None:
        u = uuid8(b"", b"", b"")

        assert u.version == 8
        assert u.fields == (0, 0, 0x8000, 0x80, 0, 0)

    def test_oversized_inputs_are_truncated(self) -> None:
        a = b"\xaa" * 64
        b = b"\xbb" * 64
        c = b"\xcc" * 64

        u = uuid8(a, b, c)

        assert u.version == 8
        assert u.fields[5] == int.from_bytes(b"\xcc" * 6, "big")


class TestUlid:
    def test_default_generates_26_char_crockford(self) -> None:
        value = ulid()

        assert len(value) == 26
        assert all(ch in _CROCKFORD for ch in value)

    def test_explicit_timestamp_round_trips(self) -> None:
        ms = 1_700_000_000_000

        value = ulid(timestamp_ms=ms)

        assert extract_ulid_timestamp(value) == ms

    def test_timestamp_zero_boundary(self) -> None:
        value = ulid(timestamp_ms=0)

        assert extract_ulid_timestamp(value) == 0

    def test_max_timestamp_boundary(self) -> None:
        ms = (1 << 48) - 1

        value = ulid(timestamp_ms=ms)

        assert extract_ulid_timestamp(value) == ms

    def test_generates_unique_values(self) -> None:
        values = {ulid(timestamp_ms=5) for _ in range(500)}

        assert len(values) == 500


class TestParseUlid:
    def test_parses_valid_ulid(self) -> None:
        value = ulid(timestamp_ms=123456)

        decoded = parse_ulid(value)

        assert (decoded >> 80) & ((1 << 48) - 1) == 123456

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="must be 26 characters"):
            parse_ulid("SHORT")

    def test_rejects_invalid_character(self) -> None:
        bad = "I" + "0" * 25

        with pytest.raises(ValueError, match="Invalid ULID character"):
            parse_ulid(bad)

    def test_parse_then_encode_round_trip(self) -> None:
        raw = b"\x01\x23\x45\x67\x89\xab" + bytes(range(10))

        encoded = _crockford_encode(raw)
        decoded = parse_ulid(encoded)

        assert decoded == int.from_bytes(raw, "big")


class TestIsValidUlid:
    def test_valid_ulid(self) -> None:
        assert is_valid_ulid(ulid())

    def test_wrong_length_rejected(self) -> None:
        assert not is_valid_ulid("ABC")

    def test_invalid_char_rejected(self) -> None:
        assert not is_valid_ulid("I" + "0" * 25)

    def test_non_string_rejected(self) -> None:
        assert not is_valid_ulid(None)
        assert not is_valid_ulid(123)


class TestIsValidUuid:
    def test_none_rejected(self) -> None:
        assert not is_valid_uuid(None)

    def test_valid_uuid_accepted(self) -> None:
        assert is_valid_uuid(str(uuid4()))

    def test_invalid_string_rejected(self) -> None:
        assert not is_valid_uuid("not-a-uuid")
        assert not is_valid_uuid("")

    def test_version_filter_matches(self) -> None:
        assert is_valid_uuid(str(uuid7()), version=7)

    def test_version_filter_mismatch_rejected(self) -> None:
        assert not is_valid_uuid(str(uuid4()), version=7)

    def test_version_filter_with_invalid_value(self) -> None:
        assert not is_valid_uuid("garbage", version=7)


class TestExtractTimestamp:
    def test_v7_timestamp(self) -> None:
        u = uuid7(timestamp_ms=987654321)

        assert extract_timestamp(u) == 987654321

    def test_v1_timestamp_is_derived(self) -> None:
        u = _uuid.uuid1()

        ts = extract_timestamp(u)

        assert ts is not None
        assert ts > 0

    def test_v4_returns_none(self) -> None:
        assert extract_timestamp(uuid4()) is None


class TestExtractUlidTimestamp:
    def test_round_trip(self) -> None:
        value = ulid(timestamp_ms=424242)

        assert extract_ulid_timestamp(value) == 424242

    def test_invalid_input_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid ULID"):
            extract_ulid_timestamp("I" + "0" * 25)

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid ULID"):
            extract_ulid_timestamp("SHORT")
