"""Deep BLAKE3 hash tests: hashing, incremental hashing, keyed hash,
key derivation, XOF output, and consistency across modes.

Uses blake3 PyPI package.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.blake3 import (
    CHUNK_LEN,
    KEY_LEN,
    Blake3,
    blake3,
    blake3_hex,
    derive_key,
    keyed_hash,
)


def _b(s: str) -> bytes:
    return s.encode()


class TestEmptyHash:
    def test_empty_hash_is_32_bytes(self) -> None:
        h = blake3(b"")
        assert len(h) == 32

    def test_empty_hash_hex_is_64_chars(self) -> None:
        h = blake3_hex(b"")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_hash_is_deterministic(self) -> None:
        a = blake3(b"")
        b = blake3(b"")
        assert a == b

    def test_empty_hash_not_all_zeros(self) -> None:
        h = blake3(b"")
        assert h != b"\x00" * 32


class TestOneShotHashing:
    def test_short_input_deterministic(self) -> None:
        a = blake3(b"hello")
        b = blake3(b"hello")
        assert a == b
        assert len(a) == 32

    def test_different_inputs_different_hashes(self) -> None:
        h1 = blake3(b"hello")
        h2 = blake3(b"world")
        assert h1 != h2

    def test_single_byte(self) -> None:
        h = blake3(b"\x00")
        assert len(h) == 32

    def test_sixty_three_bytes(self) -> None:
        h = blake3(b"a" * 63)
        assert len(h) == 32

    def test_exactly_one_block(self) -> None:
        h = blake3(b"a" * 64)
        assert len(h) == 32

    def test_exactly_one_chunk(self) -> None:
        h = blake3(b"a" * CHUNK_LEN)
        assert len(h) == 32

    def test_two_chunks(self) -> None:
        h = blake3(b"a" * (CHUNK_LEN * 2))
        assert len(h) == 32

    def test_many_chunks(self) -> None:
        h = blake3(b"a" * (CHUNK_LEN * 10))
        assert len(h) == 32

    def test_chunk_plus_one_byte(self) -> None:
        h = blake3(b"a" * (CHUNK_LEN + 1))
        assert len(h) == 32


class TestIncrementalHashing:
    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"hello world",
            b"a" * 63,
            b"a" * 64,
            b"a" * 65,
            b"a" * 127,
            b"a" * 128,
            b"a" * 500,
            b"a" * CHUNK_LEN,
            b"a" * (CHUNK_LEN + 1),
            b"a" * (CHUNK_LEN * 2),
            b"a" * (CHUNK_LEN * 3 + 7),
        ],
    )
    def test_incremental_matches_oneshot(self, data: bytes) -> None:
        oneshot = blake3(data)
        h = Blake3()
        for i in range(0, len(data), 77):
            h.update(data[i : i + 77])
        incremental = h.digest()
        assert incremental == oneshot

    def test_byte_by_byte(self) -> None:
        data = b"hello world, this is a test of byte-by-byte hashing"
        oneshot = blake3(data)
        h = Blake3()
        for b in data:
            h.update(bytes([b]))
        assert h.digest() == oneshot

    def test_empty_incremental(self) -> None:
        h = Blake3()
        assert h.digest() == blake3(b"")


class TestKeyedHash:
    def test_keyed_hash_different_from_plain(self) -> None:
        key = b"k" * KEY_LEN
        data = b"test data"
        assert keyed_hash(data, key) != blake3(data)

    def test_keyed_hash_empty(self) -> None:
        key = b"a" * KEY_LEN
        h = keyed_hash(b"", key)
        assert len(h) == 32

    def test_keyed_hash_deterministic(self) -> None:
        key = b"\x01" * KEY_LEN
        a = keyed_hash(b"abc", key)
        b = keyed_hash(b"abc", key)
        assert a == b

    def test_different_keys_different_hashes(self) -> None:
        k1 = b"\x01" * KEY_LEN
        k2 = b"\x02" * KEY_LEN
        assert keyed_hash(b"abc", k1) != keyed_hash(b"abc", k2)

    def test_wrong_key_length_raises(self) -> None:
        with pytest.raises(ValueError):
            Blake3(key=b"short", mode="keyed_hash")


class TestKeyDerivation:
    def test_derive_key_returns_requested_length(self) -> None:
        for length in [16, 32, 64, 128]:
            dk = derive_key(b"ctx", b"material", out_len=length)
            assert len(dk) == length

    def test_different_contexts_different_keys(self) -> None:
        dk1 = derive_key(b"ctx1", b"material")
        dk2 = derive_key(b"ctx2", b"material")
        assert dk1 != dk2

    def test_different_materials_different_keys(self) -> None:
        dk1 = derive_key(b"ctx", b"material1")
        dk2 = derive_key(b"ctx", b"material2")
        assert dk1 != dk2

    def test_derive_key_deterministic(self) -> None:
        a = derive_key(b"ctx", b"material")
        b = derive_key(b"ctx", b"material")
        assert a == b

    def test_derive_key_no_context_raises(self) -> None:
        with pytest.raises(ValueError):
            Blake3(context=None, mode="key_derivation")


class TestDigestLengths:
    def test_short_digest(self) -> None:
        d = Blake3().update(b"hello").digest(16)
        assert len(d) == 16

    def test_default_digest_is_32(self) -> None:
        d = Blake3().update(b"hello").digest()
        assert len(d) == 32

    def test_xof_long_output(self) -> None:
        d = Blake3().update(b"hello").digest(64)
        assert len(d) == 64

    def test_xof_past_one_block(self) -> None:
        d = Blake3().update(b"hello").digest(100)
        assert len(d) == 100

    def test_xof_deterministic(self) -> None:
        a = Blake3().update(b"hello").digest(128)
        b = Blake3().update(b"hello").digest(128)
        assert a == b

    def test_xof_first_32_consistent_across_calls(self) -> None:
        a = Blake3().update(b"hello").digest(64)
        b = Blake3().update(b"hello").digest(64)
        assert a == b
        assert a[:32] == b[:32]

    def test_xof_prefix_match(self) -> None:
        d64 = Blake3().update(b"hello").digest(64)
        d96 = Blake3().update(b"hello").digest(96)
        assert d96[:64] == d64

    def test_hexdigest_short(self) -> None:
        h = Blake3().update(b"test").hexdigest(1)
        assert len(h) == 2


class TestHexDigest:
    def test_hexdigest_format(self) -> None:
        h = Blake3().update(b"test").hexdigest()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hexdigest_matches_digest(self) -> None:
        h = Blake3().update(b"test")
        assert h.hexdigest() == h.digest().hex()


class TestFluidAPI:
    def test_update_returns_self(self) -> None:
        h = Blake3()
        assert h.update(b"abc") is h

    def test_chained_updates(self) -> None:
        h = Blake3().update(b"hello").update(b" ").update(b"world")
        assert h.digest() == blake3(b"hello world")


class TestEdgeCases:
    def test_null_bytes(self) -> None:
        h = blake3(b"\x00" * 1000)
        assert len(h) == 32

    def test_all_0xff(self) -> None:
        h = blake3(b"\xff" * 2000)
        assert len(h) == 32

    def test_repeated_pattern(self) -> None:
        h = blake3(b"abc" * 1000)
        assert len(h) == 32

    def test_binary_data(self) -> None:
        data = bytes(range(256)) * 4
        h = blake3(data)
        assert len(h) == 32

    def test_non_ascii(self) -> None:
        h = blake3("café".encode())
        assert len(h) == 32
