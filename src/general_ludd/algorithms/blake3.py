"""BLAKE3 cryptographic hash function.

Uses the blake3 PyPI package (Rust-backed).
"""

from __future__ import annotations

import blake3 as _blake3

CHUNK_LEN: int = 1024
BLOCK_LEN: int = 64
OUT_LEN: int = 32
KEY_LEN: int = 32


class Blake3:
    """BLAKE3 hasher with incremental update, keyed hash, and XOF support."""

    def __init__(
        self,
        key: bytes | None = None,
        context: bytes | None = None,
        mode: str = "hash",
    ) -> None:
        if mode == "hash":
            self._hasher = _blake3.blake3()
        elif mode == "keyed_hash":
            if key is None or len(key) != KEY_LEN:
                raise ValueError(f"Keyed hash requires {KEY_LEN}-byte key")
            self._hasher = _blake3.blake3(key=key)
        elif mode == "key_derivation":
            if context is None:
                raise ValueError("Key derivation requires context")
            self._hasher = _blake3.blake3(derive_key_context=context.decode("utf-8"))
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def update(self, data: bytes) -> Blake3:
        self._hasher.update(data)
        return self

    def digest(self, length: int = OUT_LEN) -> bytes:
        return self._hasher.digest(length)

    def hexdigest(self, length: int = OUT_LEN) -> str:
        return self._hasher.hexdigest(length)

    def finalize(self, out_len: int = OUT_LEN) -> bytes:
        return self.digest(out_len)


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
