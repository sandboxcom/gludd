"""Lempel-Ziv compression family: LZ77, LZ78, LZW, DEFLATE, LZMA, BZ2.

Pure-Python, stdlib only.  Each algorithm provides compress/decompress on bytes.
LZ77 / LZ78 / LZW are self-contained EDUCATIONAL implementations — for production
use, prefer the stdlib wrappers (DEFLATE via zlib, LZMA via lzma, BZ2 via bz2).
"""

from __future__ import annotations

import bz2
import lzma
import struct
import zlib

# ── Educational LZ77 ─────────────────────────────────────────────────────────
# The implementations below faithfully model the classic LZ algorithms to aid
# understanding.  They are NOT optimized for speed or ratio; for real workloads,
# use the stdlib wrappers in the sections below (zlib for DEFLATE, lzma for
# LZMA/xz, bz2 for BZ2).


def lz77_compress(data: bytes, window_size: int = 4096, lookahead_size: int = 258) -> list[tuple[int, int, int]]:
    """Educational LZ77 sliding-window compressor (naive O(n²) search).

    Returns list of (offset, length, literal) tokens.  offset==0 + length==0
    encodes a single literal byte in the third field.  For production DEFLATE
    compression (LZ77 + Huffman), use `deflate_compress()` below.
    """
    tokens: list[tuple[int, int, int]] = []
    i = 0
    n = len(data)
    while i < n:
        best_len = 0
        best_offset = 0
        search_start = max(0, i - window_size)
        for j in range(search_start, i):
            match_len = 0
            max_match = min(lookahead_size, n - i)
            while match_len < max_match and data[j + match_len] == data[i + match_len]:
                match_len += 1
            if match_len > best_len:
                best_len = match_len
                best_offset = i - j
        if best_len >= 3:
            tokens.append((best_offset, best_len, 0))
            i += best_len
        else:
            tokens.append((0, 0, data[i]))
            i += 1
    return tokens


def lz77_decompress(tokens: list[tuple[int, int, int]]) -> bytes:
    """Educational LZ77 decompressor.  Reverses `lz77_compress()`."""
    out = bytearray()
    for offset, length, literal in tokens:
        if offset == 0 and length == 0:
            out.append(literal)
        else:
            start = len(out) - offset
            for _ in range(length):
                out.append(out[start])
                start += 1
    return bytes(out)


# ── Educational LZ78 ─────────────────────────────────────────────────────────


def lz78_compress(data: bytes) -> list[tuple[int, int]]:
    """Educational LZ78 dictionary compressor (index-byte pairs).

    Token (0, b) encodes literal byte b; (i, b) encodes dictionary phrase i
    followed by byte b.  For production, prefer `deflate_compress()` or
    `lzma_compress()` below.
    """
    dictionary: dict[bytes, int] = {}
    tokens: list[tuple[int, int]] = []
    w = b""
    for b in data:
        wb = w + bytes([b])
        if wb in dictionary:
            w = wb
        else:
            tokens.append((dictionary.get(w, 0), b))
            dictionary[wb] = len(dictionary) + 1
            w = b""
    if w:
        last_byte = w[-1]
        w_prefix = w[:-1]
        tokens.append((dictionary.get(w_prefix, 0), last_byte))
    return tokens


def lz78_decompress(tokens: list[tuple[int, int]]) -> bytes:
    """Educational LZ78 decompressor.  Reverses `lz78_compress()`."""
    dictionary: dict[int, bytes] = {}
    out = bytearray()
    for idx, b in tokens:
        phrase = bytes([b]) if idx == 0 else dictionary[idx] + bytes([b])
        out.extend(phrase)
        dictionary[len(dictionary) + 1] = phrase
    return bytes(out)


# ── Educational LZW ──────────────────────────────────────────────────────────

_CLEAR_CODE = 256
_INIT_SIZE = 256


def lzw_compress(data: bytes, max_bits: int = 16) -> list[int]:
    """Educational LZW compressor (dictionary codes, reset-on-full).

    Dictionary starts at 257 (single bytes 0-255, clear-code 256).  Grows to
    1<<max_bits; emits clear-code and resets when full.  For production,
    prefer `deflate_compress()` (LZW-like but with Huffman) below.
    """
    if not data:
        return []

    max_table = 1 << max_bits
    dictionary: dict[bytes, int] = {bytes([i]): i for i in range(_INIT_SIZE)}
    next_code = _INIT_SIZE + 1
    out: list[int] = []
    w = bytes([data[0]])

    for b in data[1:]:
        wb = w + bytes([b])
        if wb in dictionary:
            w = wb
        else:
            out.append(dictionary[w])
            if next_code < max_table:
                dictionary[wb] = next_code
                next_code += 1
            else:
                out.append(_CLEAR_CODE)
                dictionary = {bytes([i]): i for i in range(_INIT_SIZE)}
                next_code = _INIT_SIZE + 1
            w = bytes([b])

    out.append(dictionary[w])
    return out


def lzw_decompress(codes: list[int], max_bits: int = 16) -> bytes:
    """Educational LZW decompressor.  Reverses `lzw_compress()`."""
    if not codes:
        return b""

    max_table = 1 << max_bits
    dictionary: dict[int, bytes] = {i: bytes([i]) for i in range(_INIT_SIZE)}
    next_code = _INIT_SIZE + 1
    out = bytearray()
    w = bytes([codes[0]])
    out.extend(w)

    for k in codes[1:]:
        if k == _CLEAR_CODE:
            dictionary = {i: bytes([i]) for i in range(_INIT_SIZE)}
            next_code = _INIT_SIZE + 1
            w = b""
            continue

        if k in dictionary:
            entry = dictionary[k]
        elif k == next_code and w:
            entry = w + bytes([w[0]])
        else:
            raise ValueError(f"Invalid LZW code {k} at next_code={next_code}")

        out.extend(entry)
        if w and next_code < max_table:
            dictionary[next_code] = w + bytes([entry[0]])
            next_code += 1
        w = entry

    return bytes(out)


# ── DEFLATE (stdlib zlib) ────────────────────────────────────────────────────


def deflate_compress(data: bytes, level: int = 6, wbits: int = -15) -> bytes:
    """Compress data with DEFLATE via stdlib zlib.

    wbits=-15: raw DEFLATE (no header/trailer).  wbits=15: zlib-wrapped.
    wbits=31: gzip-wrapped.  level: 0 (none) to 9 (max).
    """
    compressor = zlib.compressobj(level, zlib.DEFLATED, wbits)
    return compressor.compress(data) + compressor.flush()


def deflate_decompress(data: bytes, wbits: int = -15) -> bytes:
    """Decompress DEFLATE data via stdlib zlib."""
    return zlib.decompress(data, wbits)


def deflate_decompress_auto(data: bytes) -> bytes:
    """Auto-detect DEFLATE format: tries raw, zlib-wrapped, then gzip-wrapped."""
    errors: list[str] = []
    for wbits, label in [(-15, "raw"), (15, "zlib"), (31, "gzip")]:
        try:
            return zlib.decompress(data, wbits)
        except zlib.error as e:
            errors.append(f"{label}: {e}")
    raise ValueError(f"Deflate decompress failed for all formats: {'; '.join(errors)}")


# ── LZMA (stdlib lzma) ───────────────────────────────────────────────────────


def lzma_compress(data: bytes, *, preset: int | None = None, check: int = lzma.CHECK_CRC32) -> bytes:
    """Compress data with LZMA (xz container) via stdlib lzma.

    preset: compression level 0-9 (default 6).  check: integrity check type.
    """
    return lzma.compress(data, preset=preset, check=check)


def lzma_decompress(data: bytes) -> bytes:
    """Decompress LZMA/xz data via stdlib lzma."""
    return lzma.decompress(data)


# ── BZ2 (stdlib bz2) ─────────────────────────────────────────────────────────


def bz2_compress(data: bytes, compresslevel: int = 9) -> bytes:
    """Compress data with BZ2 (Burrows-Wheeler + Huffman) via stdlib bz2."""
    return bz2.compress(data, compresslevel)


def bz2_decompress(data: bytes) -> bytes:
    """Decompress BZ2 data via stdlib bz2."""
    return bz2.decompress(data)


# ── LZ77 token serialization ─────────────────────────────────────────────────


def lz77_tokens_to_binary(tokens: list[tuple[int, int, int]]) -> bytes:
    """Pack LZ77 educational tokens into binary format (little-endian)."""
    out = bytearray()
    for offset, length, literal in tokens:
        out.extend(struct.pack("<HHB", offset, length, literal))
    return bytes(out)


def lz77_tokens_from_binary(data: bytes) -> list[tuple[int, int, int]]:
    """Unpack binary LZ77 educational tokens."""
    tokens: list[tuple[int, int, int]] = []
    for i in range(0, len(data) - 4, 5):
        offset, length, literal = struct.unpack_from("<HHB", data, i)
        tokens.append((offset, length, literal))
    return tokens
