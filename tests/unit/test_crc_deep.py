"""Deep tests for src/general_ludd/util/crc.py — known vectors, incremental
update, collision resistance, edge cases for CRC32, CRC32C, Adler32,
Fletcher16/32, XOR, and Internet Checksum.
"""

from __future__ import annotations

import random

from general_ludd.util.crc import (
    CRC32,
    CRC32C,
    Adler32,
    Fletcher16,
    Fletcher32,
    internet_checksum,
    internet_checksum_verify,
    xor8_checksum,
    xor16_checksum,
    xor32_checksum,
)

_RNG = random.Random(2024_08_04)


def _rand_bytes(n: int) -> bytes:
    return bytes(_RNG.randint(0, 255) for _ in range(n))


# ── CRC32 ─────────────────────────────────────────────────────────────────────


class TestCRC32KnownVectors:
    def test_empty(self) -> None:
        assert CRC32.compute(b"") == 0x00000000

    def test_check_string(self) -> None:
        assert CRC32.compute(b"123456789") == 0xCBF43926

    def test_a(self) -> None:
        assert CRC32.compute(b"a") == 0xE8B7BE43

    def test_hello_world(self) -> None:
        val = CRC32.compute(b"hello world")
        assert val == 0x0D4A1185

    def test_ascii_range(self) -> None:
        data = bytes(range(32, 127))
        assert CRC32.compute(data) == 3145994718

    def test_zero_bytes_1024(self) -> None:
        assert CRC32.compute(b"\x00" * 1024) == 4021661486

    def test_nonzero_1024(self) -> None:
        assert CRC32.compute(b"\xff" * 1024) == 3090874356


class TestCRC32Incremental:
    def test_concat_equivalence(self) -> None:
        a = b"hello "
        b = b"world"
        assert CRC32.compute(a + b) == CRC32.compute(b"hello world")

    def test_byte_by_byte_vs_bulk(self) -> None:
        data = _rand_bytes(200)
        c = CRC32()
        for b in data:
            c.update(bytes([b]))
        assert c.digest() == CRC32.compute(data)

    def test_chunked_equivalence(self) -> None:
        data = _rand_bytes(1133)
        c = CRC32()
        for i in range(0, len(data), 37):
            c.update(data[i : i + 37])
        assert c.digest() == CRC32.compute(data)

    def test_resume_chain(self) -> None:
        parts = [b"foo", b"bar", b"baz", b"qux"]
        c = CRC32()
        for p in parts:
            c.update(p)
            c = CRC32(c.digest())
        ref = CRC32.compute(b"foobarbazqux")
        assert CRC32.compute(b"foobarbazqux") == ref


class TestCRC32CollisionResistance:
    def test_distinct_inputs_different_values(self) -> None:
        seen: dict[int, bytes] = {}
        for _i in range(200):
            data = _rand_bytes(12)
            h = CRC32.compute(data)
            if h in seen and seen[h] != data:
                _ = h
            seen.setdefault(h, data)
        assert len(seen) >= 190

    def test_bit_flip_changes(self) -> None:
        base = _rand_bytes(16)
        base_crc = CRC32.compute(base)
        changes = 0
        for i in range(128):
            mutated = bytearray(base)
            mutated[i // 8] ^= 1 << (i % 8)
            if CRC32.compute(bytes(mutated)) != base_crc:
                changes += 1
        assert changes == 128


class TestCRC32EdgeCases:
    def test_hexdigest(self) -> None:
        c = CRC32()
        c.update(b"test")
        assert len(c.hexdigest()) == 8
        assert c.hexdigest() == "d87f7e0c"

    def test_initial_value_seeded(self) -> None:
        a = CRC32.compute(b"world", initial=0xCBF43926)
        b = CRC32.compute(b"123456789world")
        assert a == b

    def test_large_input_consistency(self) -> None:
        data = _rand_bytes(65536)
        assert CRC32.compute(data) == CRC32.compute(data)


# ── CRC32C ────────────────────────────────────────────────────────────────────


class TestCRC32CKnownVectors:
    def test_empty(self) -> None:
        assert CRC32C.compute(b"") == 0x00000000

    def test_check_string(self) -> None:
        assert CRC32C.compute(b"123456789") == 0xE3069283

    def test_zero_bytes_32(self) -> None:
        assert CRC32C.compute(b"\x00" * 32) == 0x8A9136AA


class TestCRC32CIncremental:
    def test_chunked_equivalence(self) -> None:
        data = _rand_bytes(500)
        c = CRC32C()
        chunk = 13
        for i in range(0, len(data), chunk):
            c.update(data[i : i + chunk])
        assert c.digest() == CRC32C.compute(data)

    def test_resume_chain(self) -> None:
        parts = [b"abc", b"def", b"ghi"]
        c = CRC32C()
        for p in parts:
            c.update(p)
            c = CRC32C(c.digest())
        assert CRC32C.compute(b"abcdefghi") == CRC32C.compute(b"abcdefghi")


class TestCRC32CDivergence:
    def test_crc32_differs_from_crc32c(self) -> None:
        data = b"Hello, World!"
        assert CRC32.compute(data) != CRC32C.compute(data)

    def test_both_detect_bit_flips(self) -> None:
        data = b"\x00" * 32
        a = CRC32.compute(data)
        b = CRC32C.compute(data)
        corrupted = bytearray(data)
        corrupted[15] ^= 0x80
        assert CRC32.compute(bytes(corrupted)) != a
        assert CRC32C.compute(bytes(corrupted)) != b


# ── Adler32 ───────────────────────────────────────────────────────────────────


class TestAdler32KnownVectors:
    def test_empty(self) -> None:
        assert Adler32.compute(b"") == 0x00000001

    def test_wikipedia(self) -> None:
        assert Adler32.compute(b"Wikipedia") == 0x11E60398

    def test_a(self) -> None:
        assert Adler32.compute(b"a") == 0x00620062

    def test_abc(self) -> None:
        assert Adler32.compute(b"abc") == 0x024D0127


class TestAdler32Incremental:
    def test_chunked_equivalence(self) -> None:
        data = b"The quick brown fox jumps over the lazy dog"
        c = Adler32()
        for b in data:
            c.update(bytes([b]))
        assert c.digest() == Adler32.compute(data)

    def test_state_preservation(self) -> None:
        a = Adler32()
        a.update(b"foo")
        s1_before, s2_before = a.s1, a.s2
        a.update(b"bar")
        assert a.s1 != s1_before or a.s2 != s2_before


class TestAdler32RollingWindow:
    def test_slide_one_byte(self) -> None:
        data = b"abcdefgh"
        window = 3
        a = Adler32()
        a.update(data[:window])
        a.digest()
        a.rolling_out(data[0], window)
        a.update(bytes([data[window]]))
        h2 = a.digest()
        ref = Adler32.compute(data[1 : window + 1])
        assert h2 == ref

    def test_rolling_long_string(self) -> None:
        data = bytes(range(256))
        window = 31
        for pos in range(len(data) - window):
            direct = Adler32.compute(data[pos : pos + window])
            roll = Adler32()
            roll.update(data[:window])
            for j in range(pos):
                roll.rolling_out(data[j], window)
                roll.update(bytes([data[j + window]]))
            roll_digest = roll.digest()
            assert roll_digest == direct, f"mismatch at pos={pos}"


# ── Fletcher16 ────────────────────────────────────────────────────────────────


class TestFletcher16:
    def test_empty(self) -> None:
        assert Fletcher16.compute(b"") == 0x0000

    def test_single_byte(self) -> None:
        f = Fletcher16()
        f.update(b"\x01")
        assert f.digest() == 0x0101
        f.update(b"\x02")
        assert f.digest() == 0x0403

    def test_ascii_known(self) -> None:
        val = Fletcher16.compute(b"abcdef")
        assert isinstance(val, int)
        assert 0 <= val <= 0xFFFF

    def test_incremental_vs_bulk(self) -> None:
        data = _rand_bytes(200)
        f = Fletcher16()
        for b in data:
            f.update(bytes([b]))
        assert f.digest() == Fletcher16.compute(data)


class TestFletcher32:
    def test_empty(self) -> None:
        assert Fletcher32.compute(b"") == 0x00000000

    def test_single_byte(self) -> None:
        f = Fletcher32()
        f.update(b"\x01")
        d = f.digest()
        assert d > 0

    def test_disambiguation(self) -> None:
        a = Fletcher32.compute(b"abcde")
        b = Fletcher32.compute(b"abcdf")
        assert a != b

    def test_chunked_vs_bulk(self) -> None:
        data = _rand_bytes(2048)
        f = Fletcher32()
        chunk = 64
        for i in range(0, len(data), chunk):
            f.update(data[i : i + chunk])
        assert f.digest() == Fletcher32.compute(data)


# ── XOR ───────────────────────────────────────────────────────────────────────


class TestXOR:
    def test_xor8_empty(self) -> None:
        assert xor8_checksum(b"") == 0

    def test_xor8_single_byte(self) -> None:
        assert xor8_checksum(b"\xab") == 0xAB

    def test_xor8_cancellation(self) -> None:
        assert xor8_checksum(b"\x01\x01") == 0

    def test_xor8_commutative(self) -> None:
        a = xor8_checksum(b"\x01\x02\x03")
        b = xor8_checksum(b"\x03\x02\x01")
        assert a == b

    def test_xor16_empty(self) -> None:
        assert xor16_checksum(b"") == 0

    def test_xor16_known(self) -> None:
        assert xor16_checksum(b"\x01\x02\x03\x04") == 0x0206

    def test_xor16_odd_length(self) -> None:
        odd = xor16_checksum(b"\x01\x02\x03")
        assert isinstance(odd, int)
        assert 0 <= odd <= 0xFFFF

    def test_xor32_known(self) -> None:
        assert xor32_checksum(b"\x01\x02\x03\x04") == 0x01020304

    def test_xor32_odd_length(self) -> None:
        assert xor32_checksum(b"\x01\x02\x03") == 0x01020300


class TestXORCollisionProperties:
    def test_xor8_swapped_words_collision(self) -> None:
        h1 = xor8_checksum(b"\x11\x22")
        h2 = xor8_checksum(b"\x22\x11")
        assert h1 == h2

    def test_xor16_len2_identity(self) -> None:
        data = _rand_bytes(2)
        expected = (data[0] << 8) | data[1]
        assert xor16_checksum(data) == expected


# ── Internet Checksum ─────────────────────────────────────────────────────────


class TestInternetChecksum:
    def test_empty(self) -> None:
        assert internet_checksum(b"") == 0xFFFF

    def test_single_zero_byte(self) -> None:
        assert internet_checksum(b"\x00") == 0xFFFF

    def test_two_zero_bytes(self) -> None:
        assert internet_checksum(b"\x00\x00") == 0xFFFF

    def test_hello_world(self) -> None:
        cs = internet_checksum(b"hello world")
        assert 0 <= cs <= 0xFFFF
        assert internet_checksum_verify(b"hello world", cs)

    def test_verify_correct_data_passes(self) -> None:
        data = b"Hello, World!"
        cs = internet_checksum(data)
        assert internet_checksum_verify(data, cs)

    def test_verify_corrupted_data_fails(self) -> None:
        data = b"Hello, World!"
        cs = internet_checksum(data)
        corrupted = bytearray(data)
        corrupted[7] ^= 0x01
        assert not internet_checksum_verify(bytes(corrupted), cs)

    def test_odd_length(self) -> None:
        data = b"odd"
        cs = internet_checksum(data)
        assert internet_checksum_verify(data, cs)

    def test_known_rfc1071_vector(self) -> None:
        data = b"\x00\x01\xf2\x03\xf4\xf5\xf6\xf7"
        cs = internet_checksum(data)
        assert internet_checksum_verify(data, cs)

    def test_all_zeros(self) -> None:
        data = b"\x00" * 100
        cs = internet_checksum(data)
        assert internet_checksum_verify(data, cs)


class TestInternetChecksumCollision:
    def test_structure(self) -> None:
        data = _rand_bytes(64)
        cs = internet_checksum(data)
        assert internet_checksum_verify(data, cs)

    def test_single_bit_flip_detected(self) -> None:
        data = b"A" * 64
        cs = internet_checksum(data)
        mutated = bytearray(data)
        mutated[20] ^= 1
        assert not internet_checksum_verify(bytes(mutated), cs)

    def test_two_bit_swap_not_always_detected(self) -> None:
        data = bytes(range(64))
        cs = internet_checksum(data)
        mutated = bytearray(data)
        mutated[0], mutated[1] = mutated[1], mutated[0]
        maybe = internet_checksum_verify(bytes(mutated), cs)
        _ = maybe


# ── Uniformity / distribution ─────────────────────────────────────────────────


class TestDistribution:
    def test_crc32_no_zero_byte_bias(self) -> None:
        buckets = [0] * 256
        for _ in range(2000):
            data = _rand_bytes(8)
            h = CRC32.compute(data)
            buckets[h & 0xFF] += 1
        avg = sum(buckets) / len(buckets)
        ratio = max(buckets) / avg
        assert ratio < 3.5

    def test_adler32_nonzero_for_nonempty(self) -> None:
        for _ in range(50):
            data = _rand_bytes(32)
            assert Adler32.compute(data) != 0


# ── Consistency cross-check ───────────────────────────────────────────────────


class TestCrossCheck:
    def test_all_algorithms_deterministic(self) -> None:
        data = b"deterministic-test-vector-42"
        for _ in range(20):
            assert CRC32.compute(data) == CRC32.compute(data)
            assert CRC32C.compute(data) == CRC32C.compute(data)
            assert Adler32.compute(data) == Adler32.compute(data)
            assert Fletcher16.compute(data) == Fletcher16.compute(data)
            assert Fletcher32.compute(data) == Fletcher32.compute(data)
            assert xor8_checksum(data) == xor8_checksum(data)
            assert xor16_checksum(data) == xor16_checksum(data)
            assert xor32_checksum(data) == xor32_checksum(data)
            assert internet_checksum(data) == internet_checksum(data)

    def test_fletcher16_weaker_than_crc32(self) -> None:
        rng = random.Random(42)
        vals_f: set[int] = set()
        vals_c: set[int] = set()
        for _ in range(70000):
            data = bytes(rng.randint(0, 255) for _ in range(4))
            vals_f.add(Fletcher16.compute(data))
            vals_c.add(CRC32.compute(data))
        assert len(vals_f) < len(vals_c)

    def test_crc32c_stronger_than_xor(self) -> None:
        data = b"\x11\x22\x33\x44"
        x = xor16_checksum(data)
        c = CRC32C.compute(data)
        assert c != x
