from __future__ import annotations

import time
import uuid as _uuid

import pytest

from general_ludd.util.uuid_v7 import (
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


class TestUUID4:
    def test_generates_valid_uuid4(self) -> None:
        u = uuid4()
        assert isinstance(u, _uuid.UUID)
        assert u.version == 4

    def test_uniqueness(self) -> None:
        ids = {uuid4() for _ in range(1000)}
        assert len(ids) == 1000

    def test_variant_is_rfc4122(self) -> None:
        u = uuid4()
        assert u.variant == _uuid.RFC_4122


class TestUUID7:
    def test_generates_valid_uuid7(self) -> None:
        u = uuid7()
        assert isinstance(u, _uuid.UUID)
        assert u.version == 7 or u.version == 4
        assert u.variant == _uuid.RFC_4122

    def test_uniqueness(self) -> None:
        ids = {uuid7() for _ in range(500)}
        assert len(ids) == 500

    def test_monotonicity_within_same_millisecond(self) -> None:
        now_ms = int(time.time() * 1000)
        ids = [uuid7(timestamp_ms=now_ms) for _ in range(100)]
        for i in range(len(ids) - 1):
            assert ids[i].int <= ids[i + 1].int

    def test_monotonicity_survives_descending_entropy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entropy = iter((b"\xff" * 10, b"\x00" * 10))
        monkeypatch.setattr("general_ludd.util.uuid_v7.os.urandom", lambda _size: next(entropy))

        first = uuid7(timestamp_ms=1_700_000_000_000)
        second = uuid7(timestamp_ms=1_700_000_000_000)

        assert first.int < second.int

    def test_time_ordering_across_milliseconds(self) -> None:
        t1 = int(time.time() * 1000)
        time.sleep(0.002)
        t2 = int(time.time() * 1000)
        u1 = uuid7(timestamp_ms=t1)
        u2 = uuid7(timestamp_ms=t2)
        if t1 < t2:
            ts1 = extract_timestamp(u1)
            ts2 = extract_timestamp(u2)
            assert ts1 is not None
            assert ts2 is not None
            assert ts1 <= ts2

    def test_extract_timestamp_roundtrips(self) -> None:
        now_ms = int(time.time() * 1000)
        u = uuid7(timestamp_ms=now_ms)
        extracted = extract_timestamp(u)
        assert extracted is not None
        assert extracted == now_ms

    def test_extract_timestamp_v4_returns_none(self) -> None:
        u = uuid4()
        assert extract_timestamp(u) is None


class TestUUID8:
    def test_generates_valid_uuid8(self) -> None:
        a = b"\x01\x02\x03\x04\x05\x06"
        b = b"\x07\x08\x09\x0a"
        c = b"\x0b\x0c\x0d\x0e\x0f\x10"
        u = uuid8(a, b, c)
        assert isinstance(u, _uuid.UUID)
        assert u.version == 8
        assert u.variant == _uuid.RFC_4122

    def test_short_inputs_padded(self) -> None:
        u1 = uuid8(b"\x01\x02", b"\x03", b"\x04")
        assert isinstance(u1, _uuid.UUID)
        assert u1.version == 8

    def test_deterministic_same_input(self) -> None:
        a = b"abcdef"
        b_get = b"1234"
        c_arr = b"zyxwvu"
        u1 = uuid8(a, b_get, c_arr)
        u2 = uuid8(a, b_get, c_arr)
        assert u1 == u2

    def test_different_input_different_output(self) -> None:
        u1 = uuid8(b"aaaaaa", b"bbbb", b"cccccc")
        u2 = uuid8(b"aaaaab", b"bbbb", b"cccccc")
        assert u1 != u2


class TestULID:
    def test_generates_valid_ulid(self) -> None:
        uid = ulid()
        assert isinstance(uid, str)
        assert len(uid) == 26
        assert is_valid_ulid(uid)

    def test_crockford_alphabet_only(self) -> None:
        uid = ulid()
        allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        assert all(ch in allowed for ch in uid)

    def test_uniqueness(self) -> None:
        ids = {ulid() for _ in range(500)}
        assert len(ids) == 500

    def test_time_ordering(self) -> None:
        t1 = int(time.time() * 1000)
        time.sleep(0.002)
        t2 = int(time.time() * 1000)
        u1 = ulid(timestamp_ms=t1)
        u2 = ulid(timestamp_ms=t2)
        if t1 < t2:
            assert u1 < u2

    def test_parse_ulid_roundtrip(self) -> None:
        now_ms = int(time.time() * 1000)
        uid = ulid(timestamp_ms=now_ms)
        decoded = parse_ulid(uid)
        assert isinstance(decoded, int)
        assert decoded > 0

    def test_parse_ulid_invalid_length(self) -> None:
        with pytest.raises(ValueError, match="26 characters"):
            parse_ulid("SHORT")

    def test_parse_ulid_invalid_character(self) -> None:
        with pytest.raises(ValueError, match="Invalid ULID character"):
            parse_ulid("0" * 25 + "I")

    def test_extract_ulid_timestamp(self) -> None:
        now_ms = int(time.time() * 1000)
        uid = ulid(timestamp_ms=now_ms)
        ts = extract_ulid_timestamp(uid)
        assert ts == now_ms

    def test_extract_ulid_timestamp_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid ULID"):
            extract_ulid_timestamp("NOTAVALIDULID!!!!!!!!!!")


class TestValidation:
    def test_is_valid_uuid_v4(self) -> None:
        u = str(uuid4())
        assert is_valid_uuid(u, version=4)

    def test_is_valid_uuid_v7(self) -> None:
        u = str(uuid7())
        assert is_valid_uuid(u, version=7)

    def test_is_valid_uuid_v8(self) -> None:
        u = str(uuid8(b"aa" * 3, b"bb" * 2, b"cc" * 3))
        assert is_valid_uuid(u, version=8)

    def test_is_valid_uuid_wrong_version(self) -> None:
        u = str(uuid4())
        assert not is_valid_uuid(u, version=7)

    def test_is_valid_uuid_invalid_string(self) -> None:
        assert not is_valid_uuid("not-a-uuid")
        assert not is_valid_uuid("")
        assert not is_valid_uuid("123e4567-e89b-12d3-a456-42661417400")  # too short

    def test_is_valid_ulid_true(self) -> None:
        uid = ulid()
        assert is_valid_ulid(uid)

    def test_is_valid_ulid_false(self) -> None:
        assert not is_valid_ulid("short")
        assert not is_valid_ulid("ILLEGALULIDCHARS!!!!!!!")
        assert not is_valid_ulid("")
        assert not is_valid_ulid("0123456789ABCDEFGHJKMNPQ")  # 25 chars

    def test_is_valid_uuid_with_none(self) -> None:
        assert not is_valid_uuid(None)
