"""Deep tests for LZ77, LZ78, LZW, DEFLATE, LZMA, BZ2: round-trip correctness,
edge cases, repetition sensitivity, dictionary growth, sliding-window
behavior, token serialization, and interoperability invariants.

Educational LZ77/LZ78/LZW remain tested for correctness; new stdlib wrappers
(zlib DEFLATE, lzma LZMA, bz2 BZ2) are tested alongside.
"""

from __future__ import annotations

import random
import zlib

import pytest

from general_ludd.algorithms.lz import (
    bz2_compress,
    bz2_decompress,
    deflate_compress,
    deflate_decompress,
    deflate_decompress_auto,
    lz77_compress,
    lz77_decompress,
    lz77_tokens_from_binary,
    lz77_tokens_to_binary,
    lz78_compress,
    lz78_decompress,
    lzma_compress,
    lzma_decompress,
    lzw_compress,
    lzw_decompress,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _generate_bytes(size: int, seed: int = 42) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(size))


def _repeating_pattern(pattern: bytes, repeats: int) -> bytes:
    return pattern * repeats


# ── Educational LZ77 ─────────────────────────────────────────────────────────


def test_lz77_empty():
    assert lz77_decompress(lz77_compress(b"")) == b""


def test_lz77_single_byte():
    assert lz77_decompress(lz77_compress(b"A")) == b"A"


def test_lz77_no_repetition():
    data = b"abcdefghijklmnopqrstuvwxyz"
    assert lz77_decompress(lz77_compress(data)) == data


def test_lz77_repeated_string():
    data = b"abcabcabcabc"
    assert lz77_decompress(lz77_compress(data)) == data


def test_lz77_long_run():
    data = b"A" * 1000
    result = lz77_decompress(lz77_compress(data))
    assert result == data
    assert len(result) == 1000


def test_lz77_phrase_repetition():
    data = b"hello world hello world hello world"
    assert lz77_decompress(lz77_compress(data)) == data


def test_lz77_binary_repetition():
    data = bytes([0xAA, 0xBB] * 200)
    assert lz77_decompress(lz77_compress(data)) == data


def test_lz77_random_data_roundtrip():
    for size in [1, 10, 100, 500, 2000]:
        data = _generate_bytes(size, seed=size)
        assert lz77_decompress(lz77_compress(data, window_size=8192)) == data, f"failed at size={size}"


def test_lz77_small_window():
    data = b"XY" * 200 + b"ABCD" * 10
    assert lz77_decompress(lz77_compress(data, window_size=64)) == data


# ── Educational LZ78 ─────────────────────────────────────────────────────────


def test_lz78_empty():
    assert lz78_decompress(lz78_compress(b"")) == b""


def test_lz78_single_byte():
    assert lz78_decompress(lz78_compress(b"A")) == b"A"


def test_lz78_varied():
    data = b"abbabbabbac"
    assert lz78_decompress(lz78_compress(data)) == data


def test_lz78_repeated_blocks():
    data = b"AAAA" * 100 + b"BBBB" * 100 + b"CCCC" * 100
    result = lz78_decompress(lz78_compress(data))
    assert result == data
    assert len(result) == len(data)


def test_lz78_dictionary_building():
    data = b"abracadabra"
    tokens = lz78_compress(data)
    assert len(tokens) > 0
    assert all(isinstance(t, tuple) and len(t) == 2 for t in tokens)
    assert lz78_decompress(tokens) == data


def test_lz78_random_roundtrip():
    for size in [1, 10, 50, 200, 1000]:
        data = _generate_bytes(size, seed=size + 100)
        assert lz78_decompress(lz78_compress(data)) == data, f"failed at size={size}"


def test_lz78_all_unique():
    data = bytes(range(256))
    assert lz78_decompress(lz78_compress(data)) == data


# ── Educational LZW ──────────────────────────────────────────────────────────


def test_lzw_empty():
    assert lzw_decompress(lzw_compress(b"")) == b""


def test_lzw_single_byte():
    assert lzw_decompress(lzw_compress(b"A")) == b"A"


def test_lzw_ascii_text():
    data = b"TOBEORNOTTOBEORTOBEORNOT"
    assert lzw_decompress(lzw_compress(data)) == data


def test_lzw_repeated_pattern():
    data = b"YOYOYOYOYOYO" * 50
    result = lzw_decompress(lzw_compress(data))
    assert result == data
    assert len(result) == len(data)


def test_lzw_mixed_binary():
    data = bytes([0x00, 0xFF] * 120 + [0x55] * 80 + [0xAA, 0xBB, 0xCC] * 40)
    assert lzw_decompress(lzw_compress(data)) == data


def test_lzw_compresses_repeated_data():
    data = b"A" * 5000
    codes = lzw_compress(data)
    assert len(codes) < 1000


def test_lzw_random_roundtrip():
    for size in [1, 10, 50, 200, 1000, 5000]:
        data = _generate_bytes(size, seed=size + 200)
        result = lzw_decompress(lzw_compress(data))
        assert result == data, f"failed at size={size}"


def test_lzw_9bit_max():
    data = b"A" * 200 + b"B" * 200
    codes = lzw_compress(data, max_bits=9)
    assert lzw_decompress(codes, max_bits=9) == data


def test_lzw_all_bytes_range():
    data = bytes(range(256)) * 5
    assert lzw_decompress(lzw_compress(data)) == data


# ── DEFLATE (stdlib zlib) ────────────────────────────────────────────────────


def test_deflate_compress_decompress_roundtrip():
    original = b"hello world " * 100
    compressed = deflate_compress(original)
    assert isinstance(compressed, bytes)
    assert len(compressed) < len(original)
    assert deflate_decompress(compressed) == original


def test_deflate_compress_empty():
    assert deflate_decompress(deflate_compress(b"")) == b""


def test_deflate_compress_single_byte():
    original = b"X"
    assert deflate_decompress(deflate_compress(original)) == original


def test_deflate_compress_random():
    for size in [1, 10, 100, 1000, 5000]:
        data = _generate_bytes(size, seed=size + 300)
        result = deflate_decompress(deflate_compress(data))
        assert result == data, f"failed at size={size}"


def test_deflate_compress_zlib_wrapped():
    original = b"zlib wrapped " * 50
    compressed = deflate_compress(original, wbits=15)
    assert deflate_decompress(compressed, wbits=15) == original


def test_deflate_compress_gzip_wrapped():
    original = b"gzip wrapped " * 50
    compressed = deflate_compress(original, wbits=31)
    assert deflate_decompress(compressed, wbits=31) == original


def test_deflate_decompress_raw():
    original = b"hello world " * 100
    compressed = zlib.compress(original)
    raw = compressed[2:-4]
    assert deflate_decompress(raw) == original


def test_deflate_decompress_auto_raw():
    original = b"test data " * 50
    compressed = zlib.compress(original)
    raw = compressed[2:-4]
    assert deflate_decompress_auto(raw) == original


def test_deflate_decompress_auto_zlib():
    original = b"zlib wrapped " * 30
    compressed = zlib.compress(original)
    assert deflate_decompress_auto(compressed) == original


def test_deflate_decompress_auto_empty():
    original = b"x" * 500
    compressed = zlib.compress(original)
    assert deflate_decompress_auto(compressed) == original


def test_deflate_decompress_auto_gzip():
    import gzip
    import io

    original = b"gzip test " * 100
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(original)
    compressed = buf.getvalue()
    assert deflate_decompress_auto(compressed) == original


def test_deflate_decompress_auto_invalid():
    with pytest.raises(ValueError, match="Deflate decompress failed"):
        deflate_decompress_auto(b"not valid compressed data at all!!!")


# ── LZMA (stdlib lzma) ───────────────────────────────────────────────────────


def test_lzma_compress_decompress_roundtrip():
    original = b"hello world " * 100
    compressed = lzma_compress(original)
    assert isinstance(compressed, bytes)
    assert lzma_decompress(compressed) == original


def test_lzma_compress_empty():
    assert lzma_decompress(lzma_compress(b"")) == b""


def test_lzma_compress_single_byte():
    assert lzma_decompress(lzma_compress(b"Z")) == b"Z"


def test_lzma_compress_random():
    for size in [1, 50, 200, 1000, 5000]:
        data = _generate_bytes(size, seed=size + 400)
        result = lzma_decompress(lzma_compress(data))
        assert result == data, f"failed at size={size}"


def test_lzma_compress_repeated_data_ratio():
    original = b"A" * 10000
    compressed = lzma_compress(original)
    assert len(compressed) < 200


def test_lzma_compress_preset_levels():
    original = b"pattern " * 200
    for preset in [0, 3, 6, 9]:
        compressed = lzma_compress(original, preset=preset)
        assert lzma_decompress(compressed) == original


# ── BZ2 (stdlib bz2) ─────────────────────────────────────────────────────────


def test_bz2_compress_decompress_roundtrip():
    original = b"hello world " * 100
    compressed = bz2_compress(original)
    assert isinstance(compressed, bytes)
    assert bz2_decompress(compressed) == original


def test_bz2_compress_empty():
    assert bz2_decompress(bz2_compress(b"")) == b""


def test_bz2_compress_single_byte():
    assert bz2_decompress(bz2_compress(b"Q")) == b"Q"


def test_bz2_compress_random():
    for size in [1, 50, 200, 1000, 5000]:
        data = _generate_bytes(size, seed=size + 500)
        result = bz2_decompress(bz2_compress(data))
        assert result == data, f"failed at size={size}"


def test_bz2_compress_levels():
    original = b"level test " * 200
    for level in [1, 5, 9]:
        compressed = bz2_compress(original, compresslevel=level)
        assert bz2_decompress(compressed) == original


# ── LZ77 token serialization ─────────────────────────────────────────────────


def test_lz77_tokens_binary_roundtrip():
    data = b"abcabcabc" * 50
    tokens = lz77_compress(data)
    packed = lz77_tokens_to_binary(tokens)
    unpacked = lz77_tokens_from_binary(packed)
    assert lz77_decompress(unpacked) == data


def test_lz77_tokens_binary_empty():
    packed = lz77_tokens_to_binary([])
    assert packed == b""
    unpacked = lz77_tokens_from_binary(b"")
    assert unpacked == []


# ── cross-algorithm invariants ───────────────────────────────────────────────


def test_all_algorithms_agree_on_same_input():
    data = b"abc" * 100
    assert lz77_decompress(lz77_compress(data)) == data
    assert lz78_decompress(lz78_compress(data)) == data
    assert lzw_decompress(lzw_compress(data)) == data


def test_lz77_and_deflate_agree_on_compressed_small():
    original = b"hello " * 40
    lz77_result = lz77_decompress(lz77_compress(original))
    assert lz77_result == original
    c = zlib.compress(original)
    assert deflate_decompress(c[2:-4]) == original


def test_lz77_reproduces_exact_length():
    for n in [1, 7, 13, 100, 513, 1000]:
        data = _generate_bytes(n, seed=n)
        assert len(lz77_decompress(lz77_compress(data))) == n


def test_lzw_reproduces_exact_length():
    for n in [1, 8, 16, 200, 1024]:
        data = _generate_bytes(n, seed=n + 50)
        assert len(lzw_decompress(lzw_compress(data))) == n


def test_all_stdlib_wrappers_agree():
    original = b"cross check " * 100
    assert deflate_decompress(deflate_compress(original)) == original
    assert lzma_decompress(lzma_compress(original)) == original
    assert bz2_decompress(bz2_compress(original)) == original


if __name__ == "__main__":
    pytest.main([__file__])
