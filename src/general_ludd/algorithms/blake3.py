"""BLAKE3 cryptographic hash function.

Pure-Python, stdlib only. Implements the BLAKE3 specification:
hash, keyed hash, key derivation, and XOF (extendable output).

BLAKE3 uses a Merkle tree of 1024-byte chunks, each chunk composed
of 16 blocks of 64 bytes. The compression function uses 7 rounds of
the BLAKE2 permutation on 16 x 32-bit state words.
"""

from __future__ import annotations

import struct

CHUNK_LEN: int = 1024
BLOCK_LEN: int = 64
OUT_LEN: int = 32
KEY_LEN: int = 32
WORD_SIZE: int = 4

IV: tuple[int, int, int, int, int, int, int, int] = (
    0x6A09E667,
    0xBB67AE85,
    0x3C6EF372,
    0xA54FF53A,
    0x510E527F,
    0x9B05688C,
    0x1F83D9AB,
    0x5BE0CD19,
)

MSG_PERMUTATION: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8),
    (3, 4, 9, 7, 10, 2, 12, 15, 6, 0, 8, 14, 5, 1, 13, 11),
    (10, 12, 14, 5, 15, 3, 0, 13, 7, 2, 11, 4, 9, 1, 6, 8),
    (9, 8, 10, 3, 1, 13, 11, 4, 14, 5, 12, 6, 15, 0, 2, 7),
    (0, 11, 7, 4, 12, 1, 14, 9, 3, 10, 15, 6, 13, 8, 2, 5),
    (5, 4, 3, 2, 1, 0, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14),
)

CHUNK_START: int = 1 << 0
CHUNK_END: int = 1 << 1
PARENT: int = 1 << 2
ROOT: int = 1 << 3
KEYED_HASH: int = 1 << 4
DERIVE_KEY_CONTEXT: int = 1 << 5
DERIVE_KEY_MATERIAL: int = 1 << 6


def _rotl32(v: int, c: int) -> int:
    return ((v << c) | (v >> (32 - c))) & 0xFFFFFFFF


def _bytes_to_words_le(b: bytes) -> list[int]:
    assert len(b) % WORD_SIZE == 0
    return list(struct.unpack_from(f"<{len(b) // WORD_SIZE}I", b))


def _words_to_bytes_le(w: list[int]) -> bytes:
    return struct.pack(f"<{len(w)}I", *w)


def _g(
    state: list[int],
    a: int,
    b: int,
    c: int,
    d: int,
    mx: int,
    my: int,
) -> None:
    state[a] = (state[a] + state[b] + mx) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b] + my) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 7)


def _round(state: list[int], message: list[int], perm: tuple[int, ...]) -> None:
    _g(state, 0, 4, 8, 12, message[perm[0]], message[perm[1]])
    _g(state, 1, 5, 9, 13, message[perm[2]], message[perm[3]])
    _g(state, 2, 6, 10, 14, message[perm[4]], message[perm[5]])
    _g(state, 3, 7, 11, 15, message[perm[6]], message[perm[7]])
    _g(state, 0, 5, 10, 15, message[perm[8]], message[perm[9]])
    _g(state, 1, 6, 11, 12, message[perm[10]], message[perm[11]])
    _g(state, 2, 7, 8, 13, message[perm[12]], message[perm[13]])
    _g(state, 3, 4, 9, 14, message[perm[14]], message[perm[15]])


def compress(
    chaining_value: list[int],
    block_words: list[int],
    counter: int,
    block_len: int,
    flags: int,
) -> list[int]:
    state = [
        chaining_value[0],
        chaining_value[1],
        chaining_value[2],
        chaining_value[3],
        chaining_value[4],
        chaining_value[5],
        chaining_value[6],
        chaining_value[7],
        IV[0],
        IV[1],
        IV[2],
        IV[3],
        counter & 0xFFFFFFFF,
        (counter >> 32) & 0xFFFFFFFF,
        block_len,
        flags,
    ]
    for perm in MSG_PERMUTATION:
        _round(state, block_words, perm)
    return [
        state[0] ^ state[8],
        state[1] ^ state[9],
        state[2] ^ state[10],
        state[3] ^ state[11],
        state[4] ^ state[12],
        state[5] ^ state[13],
        state[6] ^ state[14],
        state[7] ^ state[15],
    ]


def _hash_chunk(
    cv: list[int],
    data: bytes,
    chunk_counter: int,
    flags: int,
) -> list[int]:
    num_blocks = max((len(data) + BLOCK_LEN - 1) // BLOCK_LEN, 1)
    chunk_cv = [0] * 8
    for i in range(num_blocks):
        start = i * BLOCK_LEN
        block = data[start : start + BLOCK_LEN]
        block_words = _bytes_to_words_le(block.ljust(BLOCK_LEN, b"\x00"))
        blk_flags = flags
        if i == 0:
            blk_flags |= CHUNK_START
        if i == num_blocks - 1:
            blk_flags |= CHUNK_END
        blk = compress(cv, block_words, chunk_counter, len(block), blk_flags)
        for j in range(8):
            chunk_cv[j] ^= blk[j]
    return chunk_cv


def _parent_cv(
    left_child_cv: list[int],
    right_child_cv: list[int],
    key: list[int],
    flags: int,
) -> list[int]:
    block_words = left_child_cv + right_child_cv
    return compress(key, block_words, 0, BLOCK_LEN, PARENT | flags)


def _build_tree(chunks: list[list[int]], cv: list[int], flags: int) -> list[int]:
    if len(chunks) == 0:
        return _hash_chunk(cv, b"", 0, flags)
    if len(chunks) == 1:
        return compress(
            cv,
            chunks[0] + [0] * 8,
            0,
            BLOCK_LEN,
            flags | CHUNK_START | CHUNK_END | ROOT,
        )
    mid = len(chunks) // 2
    left = _build_tree(chunks[:mid], cv, flags)
    right = _build_tree(chunks[mid:], cv, flags)
    return _parent_cv(left, right, cv, flags)


class Blake3:
    """BLAKE3 hasher with incremental update, keyed hash, and XOF support."""

    def __init__(
        self,
        key: bytes | None = None,
        context: bytes | None = None,
        mode: str = "hash",
    ) -> None:
        if mode == "hash":
            self._key_words: list[int] = list(IV)
        elif mode == "keyed_hash":
            if key is None or len(key) != KEY_LEN:
                raise ValueError(f"Keyed hash requires {KEY_LEN}-byte key")
            self._key_words = _bytes_to_words_le(key)
        elif mode == "key_derivation":
            if context is None:
                raise ValueError("Key derivation requires context")
            ctx_hasher = Blake3(mode="keyed_hash", key=key if key else b"\x00" * KEY_LEN)
            ctx_hasher.update(context)
            self._key_words = _bytes_to_words_le(ctx_hasher.finalize(OUT_LEN))
        else:
            raise ValueError(f"Unknown mode: {mode}")

        self._mode = mode
        self._buf: bytearray = bytearray()
        self._chunks: list[list[int]] = []
        self._chunk_counter: int = 0
        self._total_bytes: int = 0
        self._root_cv: list[int] | None = None
        self._xof_buf: bytes = b""
        self._xof_offset: int = 0

    def update(self, data: bytes) -> Blake3:
        self._total_bytes += len(data)
        self._buf.extend(data)
        while len(self._buf) >= CHUNK_LEN:
            chunk = bytes(self._buf[:CHUNK_LEN])
            self._buf = self._buf[CHUNK_LEN:]
            chunk_cv = _hash_chunk(
                self._key_words,
                chunk,
                self._chunk_counter,
                self._chunk_flags(False),
            )
            self._chunks.append(chunk_cv)
            self._chunk_counter += 1
        return self

    def _chunk_flags(self, is_root: bool) -> int:
        flags = 0
        if self._mode == "keyed_hash":
            flags |= KEYED_HASH
        if self._mode == "key_derivation" and not is_root:
            flags |= DERIVE_KEY_CONTEXT
        if is_root:
            flags |= ROOT
            if self._mode == "key_derivation":
                flags |= DERIVE_KEY_MATERIAL
        return flags

    def _compute_root(self) -> list[int]:
        if self._root_cv is not None:
            return self._root_cv
        last_chunk = _hash_chunk(
            self._key_words,
            bytes(self._buf),
            self._chunk_counter,
            self._chunk_flags(False),
        )
        all_chunks = [*list(self._chunks), last_chunk]
        self._root_cv = _build_tree(all_chunks, self._key_words, self._chunk_flags(True))
        return self._root_cv

    def finalize(self, out_len: int = OUT_LEN) -> bytes:
        root_cv = self._compute_root()
        root_bytes = _words_to_bytes_le(root_cv)
        if out_len <= OUT_LEN:
            return root_bytes[:out_len]
        return self._xof_read(out_len)

    def _xof_read(self, length: int) -> bytes:
        root_cv = self._compute_root()
        output = bytearray()
        block_index = 0
        while len(output) < length:
            block_words = root_cv + [block_index & 0xFFFFFFFF, (block_index >> 32) & 0xFFFFFFFF] + [0] * 6
            flags = ROOT
            if self._mode == "keyed_hash":
                flags |= KEYED_HASH
            if self._mode == "key_derivation":
                flags |= DERIVE_KEY_MATERIAL
            out = compress(list(IV), block_words, block_index, BLOCK_LEN, flags)
            out_bytes = _words_to_bytes_le(out)
            remaining = length - len(output)
            output.extend(out_bytes[:remaining])
            block_index += 1
        return bytes(output)

    def digest(self, length: int = OUT_LEN) -> bytes:
        return self.finalize(length)

    def hexdigest(self, length: int = OUT_LEN) -> str:
        return self.digest(length).hex()


def blake3(
    data: bytes = b"",
    key: bytes | None = None,
    context: bytes | None = None,
    out_len: int = OUT_LEN,
    mode: str = "hash",
) -> bytes:
    h = Blake3(key=key, context=context, mode=mode)
    h.update(data)
    return h.digest(out_len)


def blake3_hex(
    data: bytes = b"",
    key: bytes | None = None,
    context: bytes | None = None,
    out_len: int = OUT_LEN,
    mode: str = "hash",
) -> str:
    return blake3(data, key=key, context=context, out_len=out_len, mode=mode).hex()


def keyed_hash(data: bytes, key: bytes) -> bytes:
    return blake3(data, key=key, mode="keyed_hash")


def derive_key(context: bytes, key_material: bytes, out_len: int = OUT_LEN) -> bytes:
    return blake3(key_material, context=context, mode="key_derivation", out_len=out_len)
