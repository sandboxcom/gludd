"""Deep compression and encoding tests: gzip, zlib, brotli, LZ4, base64, hex,
run-length encoding, delta encoding.

Tests the Python standard library compression/encoding modules plus optional
third-party codecs (brotli, lz4) with graceful skip when unavailable.
"""

from __future__ import annotations

import base64
import gzip
import random
import zlib

import pytest

# — Optional third-party codecs —————————————————————————————————————————————
try:
    import brotli as _brotli  # type: ignore[import-untyped]
except ImportError:
    _brotli = None

try:
    import lz4  # type: ignore[import-untyped]
except ImportError:
    lz4 = None


# — Fixtures —————————————————————————————————————————————————————————————————
def _random_bytes(size: int = 1024, seed: int = 42) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(size))


def _random_ints(size: int = 256, seed: int = 42) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(-1000, 1000) for _ in range(size)]


# — Gzip round-trip ——————————————————————————————————————————————————————————


class TestGzip:
    def test_roundtrip_bytes(self) -> None:
        data = _random_bytes()
        compressed = gzip.compress(data)
        decompressed = gzip.decompress(compressed)
        assert decompressed == data

    def test_compress_levels_1_to_9(self) -> None:
        data = (b"Hello, General Ludd Agent! " * 256)[:4096]
        sizes: list[int] = []
        for level in range(1, 10):
            compressed = gzip.compress(data, compresslevel=level)
            decompressed = gzip.decompress(compressed)
            assert decompressed == data
            sizes.append(len(compressed))
        assert sizes[0] >= sizes[-1], f"higher levels should not increase size: {sizes}"

    def test_empty_payload(self) -> None:
        assert gzip.decompress(gzip.compress(b"")) == b""

    def test_large_repetitive_compresses_well(self) -> None:
        data = b"A" * 100_000
        compressed = gzip.compress(data, compresslevel=6)
        assert len(compressed) < len(data) // 10


# — Zlib round-trip ——————————————————————————————————————————————————————————


class TestZlib:
    def test_roundtrip_bytes(self) -> None:
        data = _random_bytes()
        compressed = zlib.compress(data)
        decompressed = zlib.decompress(compressed)
        assert decompressed == data

    def test_compress_levels(self) -> None:
        data = (b"Hello, General Ludd Agent! " * 256)[:4096]
        sizes: list[int] = []
        for level in range(1, 10):
            compressed = zlib.compress(data, level)
            decompressed = zlib.decompress(compressed)
            assert decompressed == data
            sizes.append(len(compressed))
        assert sizes[0] >= sizes[-1], f"higher levels should not increase size: {sizes}"

    def test_empty_payload(self) -> None:
        assert zlib.decompress(zlib.compress(b"")) == b""

    def test_large_deflate_inflate(self) -> None:
        data = _random_bytes(256 * 1024)
        assert zlib.decompress(zlib.compress(data)) == data


# — Gzip vs Zlib —————————————————————————————————————————————————————————————


class TestGzipVsZlib:
    def test_cross_compress_independence(self) -> None:
        data = _random_bytes()
        gz = gzip.compress(data)
        zl = zlib.compress(data)
        assert gz != zl, "gzip and zlib headers differ"
        with pytest.raises(gzip.BadGzipFile):
            gzip.decompress(zl)
        with pytest.raises(zlib.error):
            zlib.decompress(gz)

    def test_zlib_raw_deflate_shared_core(self) -> None:
        data = _random_bytes(2048)
        raw = zlib.compress(data, level=5)
        reinflated = zlib.decompress(raw)
        assert reinflated == data


# — Base64 encoding ——————————————————————————————————————————————————————————


class TestBase64:
    def test_encode_decode_roundtrip(self) -> None:
        for size in [0, 1, 2, 3, 64, 1023, 1024]:
            data = _random_bytes(size)
            encoded = base64.b64encode(data)
            decoded = base64.b64decode(encoded)
            assert decoded == data
            assert isinstance(encoded, bytes)

    def test_urlsafe_vs_standard(self) -> None:
        data = bytes(range(256))
        std = base64.b64encode(data)
        url = base64.urlsafe_b64encode(data)
        assert b"+" in std or b"/" in std
        assert b"+" not in url
        assert b"/" not in url
        assert base64.urlsafe_b64decode(url) == data

    def test_padding_roundtrip(self) -> None:
        data = b"\x00"
        encoded = base64.b64encode(data)
        assert encoded.endswith(b"==")
        assert base64.b64decode(encoded) == data

    def test_b85_encode_decode_roundtrip(self) -> None:
        data = _random_bytes(1024)
        encoded = base64.b85encode(data)
        decoded = base64.b85decode(encoded)
        assert decoded == data
        assert len(encoded) < len(data) * 1.3


# — Hex encoding —————————————————————————————————————————————————————————————


class TestHex:
    def test_encode_decode_roundtrip(self) -> None:
        for size in [0, 1, 16, 255, 256, 1024]:
            data = _random_bytes(size, seed=size)
            encoded = data.hex()
            decoded = bytes.fromhex(encoded)
            assert decoded == data
            assert len(encoded) == len(data) * 2

    def test_case_insensitive_decode(self) -> None:
        assert bytes.fromhex("0A0B0C") == b"\x0a\x0b\x0c"
        assert bytes.fromhex("0a0b0c") == b"\x0a\x0b\x0c"

    def test_hex_vs_base64_size(self) -> None:
        data = _random_bytes(1024)
        hex_enc = data.hex()
        b64_enc = base64.b64encode(data)
        assert len(hex_enc) == len(data) * 2
        assert len(b64_enc) < len(hex_enc)


# — Run-length encoding ——————————————————————————————————————————————————————


def run_length_encode(data: bytes) -> bytes:
    if not data:
        return b""
    result = bytearray()
    count = 1
    prev = data[0]
    for byte in data[1:]:
        if byte == prev and count < 255:
            count += 1
        else:
            result.append(count)
            result.append(prev)
            prev = byte
            count = 1
    result.append(count)
    result.append(prev)
    return bytes(result)


def run_length_decode(encoded: bytes) -> bytes:
    result = bytearray()
    for i in range(0, len(encoded), 2):
        count = encoded[i]
        byte = encoded[i + 1]
        result.extend([byte] * count)
    return bytes(result)


class TestRunLengthEncoding:
    def test_roundtrip(self) -> None:
        data = _random_bytes(1024)
        encoded = run_length_encode(data)
        decoded = run_length_decode(encoded)
        assert decoded == data

    def test_empty(self) -> None:
        assert run_length_encode(b"") == b""
        assert run_length_decode(b"") == b""

    def test_repetitive_compresses(self) -> None:
        data = b"A" * 200
        encoded = run_length_encode(data)
        assert len(encoded) == 2
        assert encoded[0] == 200
        assert encoded[1] == ord("A")

    def test_no_repeats_expands(self) -> None:
        data = bytes(range(100))
        encoded = run_length_encode(data)
        assert len(encoded) == 200


# — Delta encoding ———————————————————————————————————————————————————————————


def delta_encode(values: list[int]) -> list[int]:
    if not values:
        return []
    result = [values[0]]
    for i in range(1, len(values)):
        result.append(values[i] - values[i - 1])
    return result


def delta_decode(encoded: list[int]) -> list[int]:
    if not encoded:
        return []
    result = [encoded[0]]
    for delta in encoded[1:]:
        result.append(result[-1] + delta)
    return result


def delta_encode_bytes(data: bytes) -> bytes:
    if not data:
        return b""
    result = bytearray()
    result.append(data[0])
    for i in range(1, len(data)):
        diff = (data[i] - data[i - 1]) & 0xFF
        result.append(diff)
    return bytes(result)


def delta_decode_bytes(encoded: bytes) -> bytes:
    if not encoded:
        return b""
    result = bytearray()
    result.append(encoded[0])
    for delta in encoded[1:]:
        result.append((result[-1] + delta) & 0xFF)
    return bytes(result)


class TestDeltaEncoding:
    def test_int_roundtrip(self) -> None:
        values = _random_ints()
        encoded = delta_encode(values)
        decoded = delta_decode(encoded)
        assert decoded == values

    def test_int_empty(self) -> None:
        assert delta_encode([]) == []
        assert delta_decode([]) == []

    def test_int_single(self) -> None:
        assert delta_encode([42]) == [42]
        assert delta_decode([42]) == [42]

    def test_int_constant(self) -> None:
        values = [100] * 20
        encoded = delta_encode(values)
        assert encoded[0] == 100
        assert all(d == 0 for d in encoded[1:])

    def test_int_equivalence_under_sum(self) -> None:
        values = [3, 10, 2, -1, 8]
        encoded = delta_encode(values)
        assert encoded == [3, 7, -8, -3, 9]
        assert sum(values) == sum(delta_decode(encoded))

    def test_byte_roundtrip(self) -> None:
        data = _random_bytes(1024)
        encoded = delta_encode_bytes(data)
        decoded = delta_decode_bytes(encoded)
        assert decoded == data

    def test_byte_empty(self) -> None:
        assert delta_encode_bytes(b"") == b""
        assert delta_decode_bytes(b"") == b""


# — Brotli ———————————————————————————————————————————————————————————————————


@pytest.mark.skipif(_brotli is None, reason="brotli not installed")
class TestBrotli:
    def test_roundtrip(self) -> None:
        assert _brotli is not None
        data = _random_bytes(4096)
        compressed = _brotli.compress(data)
        decompressed = _brotli.decompress(compressed)
        assert decompressed == data

    def test_quality_levels(self) -> None:
        assert _brotli is not None
        data = _random_bytes(8192)
        sizes: list[int] = []
        for quality in [0, 3, 6, 11]:
            compressed = _brotli.compress(data, quality=quality)
            decompressed = _brotli.decompress(compressed)
            assert decompressed == data
            sizes.append(len(compressed))
        assert sizes[0] >= sizes[-1], "higher quality should not increase size"

    def test_empty_payload(self) -> None:
        assert _brotli is not None
        assert _brotli.decompress(_brotli.compress(b"")) == b""


# — LZ4 ——————————————————————————————————————————————————————————————————————


@pytest.mark.skipif(lz4 is None, reason="lz4 not installed")
class TestLz4:
    def test_roundtrip(self) -> None:
        assert lz4 is not None
        data = _random_bytes(4096)
        compressed = lz4.block.compress(data)
        decompressed = lz4.block.decompress(compressed, uncompressed_size=len(data))
        assert decompressed == data

    def test_empty_payload(self) -> None:
        assert lz4 is not None
        compressed = lz4.block.compress(b"")
        decompressed = lz4.block.decompress(compressed, uncompressed_size=0)
        assert decompressed == b""

    def test_speed_compress_decompress(self) -> None:
        assert lz4 is not None
        data = _random_bytes(1024 * 1024, seed=7)
        compressed = lz4.block.compress(data)
        assert len(compressed) < len(data) or len(data) < 256
        decompressed = lz4.block.decompress(compressed, uncompressed_size=len(data))
        assert decompressed == data
