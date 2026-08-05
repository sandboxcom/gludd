"""Lempel-Ziv compression family: LZ77, LZ78, LZW, DEFLATE decompress.

Pure-Python, stdlib only.  Each algorithm provides compress/decompress on bytes.
LZ77 / LZ78 / LZW are self-contained educational implementations.
DEFLATE decompress wraps stdlib `zlib` for production use.
"""

from __future__ import annotations

import struct
import zlib

# ── LZ77 ────────────────────────────────────────────────────────────────────


def lz77_compress(data: bytes, window_size: int = 4096, lookahead_size: int = 258) -> list[tuple[int, int, int]]:
    """Compress data with LZ77 sliding-window.

    Returns list of (offset, length, _) tokens.  Tokens with offset==0 and
    length==0 encode a single literal byte in the third field.  Tokens with
    offset>0 encode a back-reference of the given length.
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
    """Decompress LZ77 tokens back to original bytes."""
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


# ── LZ78 ────────────────────────────────────────────────────────────────────


def lz78_compress(data: bytes) -> list[tuple[int, int]]:
    """Compress data with LZ78 dictionary. Returns list of (index, byte)."""
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
    """Decompress LZ78 tokens back to original bytes."""
    dictionary: dict[int, bytes] = {}
    out = bytearray()
    for idx, b in tokens:
        phrase = bytes([b]) if idx == 0 else dictionary[idx] + bytes([b])
        out.extend(phrase)
        dictionary[len(dictionary) + 1] = phrase
    return bytes(out)


# ── LZW ─────────────────────────────────────────────────────────────────────

_CLEAR_CODE = 256
_INIT_SIZE = 256


def lzw_compress(data: bytes, max_bits: int = 16) -> list[int]:
    """Compress data with LZW. Returns list of dictionary codes.

    Dictionary starts at 257 (single bytes 0-255, clear-code 256).
    Grows to 1<<max_bits; emits clear-code and resets when full.
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
    """Decompress LZW codes back to original bytes."""
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


# ── DEFLATE decompress (stdlib wrapper) ─────────────────────────────────────


def deflate_decompress(data: bytes, wbits: int = -15) -> bytes:
    """Decompress DEFLATE data using stdlib zlib.

    wbits: -15 for raw DEFLATE (no header/trailer), 15 for zlib, 31 for gzip.
    """
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


# ── LZ77 token serialization ────────────────────────────────────────────────


def lz77_tokens_to_binary(tokens: list[tuple[int, int, int]]) -> bytes:
    """Pack LZ77 tokens into binary format (little-endian)."""
    out = bytearray()
    for offset, length, literal in tokens:
        out.extend(struct.pack("<HHB", offset, length, literal))
    return bytes(out)


def lz77_tokens_from_binary(data: bytes) -> list[tuple[int, int, int]]:
    """Unpack binary LZ77 tokens."""
    tokens: list[tuple[int, int, int]] = []
    for i in range(0, len(data) - 4, 5):
        offset, length, literal = struct.unpack_from("<HHB", data, i)
        tokens.append((offset, length, literal))
    return tokens
