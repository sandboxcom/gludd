"""Argon2id password hashing — RFC 9106.

Pure-Python, stdlib only.  Implements Argon2id variant with Blake2b internally.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import NoReturn

U64_MAX = (1 << 64) - 1

BLOCK_SIZE = 1024
QWORDS_IN_BLOCK = BLOCK_SIZE // 8  # 128
ARGON2_VERSION = 0x13
ARGON2_PREHASH_DIGEST_LENGTH = 64
ARGON2_SYNC_POINTS = 4

BLAKE2B_IV: list[int] = [
    0x6A09E667F3BCC908,
    0xBB67AE8584CAA73B,
    0x3C6EF372FE94F82B,
    0xA54FF53A5F1D36F1,
    0x510E527FADE682D1,
    0x9B05688C2B3E6C1F,
    0x1F83D9ABFB41BD6B,
    0x5BE0CD19137E2179,
]

BLAKE2B_SIGMA: list[list[int]] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
]

TIME_COST = 2
MEMORY_COST = 256  # KiB (fast pure-Python; increase for production)
PARALLELISM = 1
HASH_LEN = 32
SALT_LEN = 16


class Argon2Error(Exception):
    """Base exception for Argon2id operations."""


def _raise(msg: str) -> NoReturn:
    raise Argon2Error(msg)


def _le64(value: int) -> bytes:
    return struct.pack("<Q", value & U64_MAX)


def _le32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def _u64s_from_bytes(data: bytes) -> list[int]:
    return [struct.unpack_from("<Q", data, i)[0] for i in range(0, len(data), 8)]


def _b64enc(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def _rotr64(value: int, n: int) -> int:
    return ((value >> n) | (value << (64 - n))) & U64_MAX


def _blake2b_G(state: bytearray, a: int, b: int, c: int, d: int, x: int, y: int) -> None:
    va = int.from_bytes(state[a * 8 : (a + 1) * 8], "little")
    vb = int.from_bytes(state[b * 8 : (b + 1) * 8], "little")
    vc = int.from_bytes(state[c * 8 : (c + 1) * 8], "little")
    vd = int.from_bytes(state[d * 8 : (d + 1) * 8], "little")

    va = (va + vb + x) & U64_MAX
    vd = _rotr64(vd ^ va, 32)
    vc = (vc + vd) & U64_MAX
    vb = _rotr64(vb ^ vc, 24)
    va = (va + vb + y) & U64_MAX
    vd = _rotr64(vd ^ va, 16)
    vc = (vc + vd) & U64_MAX
    vb = _rotr64(vb ^ vc, 63)

    state[a * 8 : (a + 1) * 8] = va.to_bytes(8, "little")
    state[b * 8 : (b + 1) * 8] = vb.to_bytes(8, "little")
    state[c * 8 : (c + 1) * 8] = vc.to_bytes(8, "little")
    state[d * 8 : (d + 1) * 8] = vd.to_bytes(8, "little")


def _blake2b_round(state: bytearray, r: int, message: list[int]) -> None:
    s = BLAKE2B_SIGMA[r % 10]
    _blake2b_G(state, 0, 4, 8, 12, message[s[0]], message[s[1]])
    _blake2b_G(state, 1, 5, 9, 13, message[s[2]], message[s[3]])
    _blake2b_G(state, 2, 6, 10, 14, message[s[4]], message[s[5]])
    _blake2b_G(state, 3, 7, 11, 15, message[s[6]], message[s[7]])
    _blake2b_G(state, 0, 5, 10, 15, message[s[8]], message[s[9]])
    _blake2b_G(state, 1, 6, 11, 12, message[s[10]], message[s[11]])
    _blake2b_G(state, 2, 7, 8, 13, message[s[12]], message[s[13]])
    _blake2b_G(state, 3, 4, 9, 14, message[s[14]], message[s[15]])


def _permute_block(block: bytearray) -> None:
    """Argon2 permutation P — column-then-diagonal Blake2b rounds."""
    for i in range(8):
        col = bytearray(block[i * 128 : (i + 1) * 128])
        msg = list(struct.unpack("<16Q", bytes(col)))
        _blake2b_round(col, 0, msg)
        block[i * 128 : (i + 1) * 128] = col

    for i in range(8):
        diag = bytearray(128)
        for j in range(16):
            ci = (i + j) % 8
            off = ci * 128 + j * 8
            val = struct.unpack_from("<Q", block, off)[0]
            struct.pack_into("<Q", diag, j * 8, val)
        msg = list(struct.unpack("<16Q", bytes(diag)))
        _blake2b_round(diag, 1, msg)
        for j in range(16):
            ci = (i + j) % 8
            struct.pack_into("<Q", block, ci * 128 + j * 8, struct.unpack_from("<Q", diag, j * 8)[0])


def _compress(X: bytes, Y: bytes) -> bytes:
    R = bytearray(len(X))
    for i in range(len(X)):
        R[i] = X[i] ^ Y[i]

    _permute_block(R)
    _permute_block(R)

    result = bytearray(len(X))
    for i in range(len(X)):
        result[i] = R[i] ^ X[i]
    return bytes(result)


def _blake2b_long(data: bytes, outlen: int) -> bytes:
    if outlen <= 64:
        return hashlib.blake2b(data, digest_size=outlen).digest()

    out = bytearray()
    to_produce = outlen
    buf0 = hashlib.blake2b(data, digest_size=64).digest()
    out.extend(buf0[:to_produce])
    to_produce -= len(buf0[:to_produce])

    while to_produce > 0:
        buf0 = hashlib.blake2b(buf0, digest_size=64).digest()
        out.extend(buf0[:to_produce])
        to_produce -= len(buf0[:to_produce])

    return bytes(out)


def _variable_length_hash_le32(data: bytes, outlen: int) -> bytes:
    return bytes(struct.pack("<I", outlen) + data)


def _argon2_hash(
    password: bytes,
    salt: bytes,
    time_cost: int,
    memory_cost: int,
    parallelism: int,
    hash_len: int,
    version: int = ARGON2_VERSION,
    secret: bytes = b"",
    associated: bytes = b"",
    argon2_type: int = 2,  # 2 = Argon2id
) -> bytes:
    if time_cost < 1:
        _raise(f"time_cost must be >= 1, got {time_cost}")
    if memory_cost < 8 * parallelism:
        _raise(f"memory_cost must be >= {8 * parallelism} KiB")
    if parallelism < 1:
        _raise(f"parallelism must be >= 1, got {parallelism}")
    if hash_len < 4:
        _raise(f"hash_len must be >= 4, got {hash_len}")
    if len(salt) == 0:
        _raise("salt must not be empty")

    lanes = parallelism
    segment_length = max(memory_cost // (lanes * ARGON2_SYNC_POINTS), 1)
    memory_blocks = segment_length * lanes * ARGON2_SYNC_POINTS

    # Build pre-hash input (RFC 9106 §3.4)
    H0_input = bytearray()
    H0_input.extend(_le32(lanes))
    H0_input.extend(_le32(hash_len))
    H0_input.extend(_le32(memory_cost))
    H0_input.extend(_le32(time_cost))
    H0_input.extend(_le32(version))
    H0_input.extend(_le32(argon2_type))
    H0_input.extend(_le32(len(password)))
    H0_input.extend(password)
    H0_input.extend(_le32(len(salt)))
    H0_input.extend(salt)
    H0_input.extend(_le32(len(secret)))
    H0_input.extend(secret)
    H0_input.extend(_le32(len(associated)))
    H0_input.extend(associated)

    H0 = hashlib.blake2b(bytes(H0_input), digest_size=ARGON2_PREHASH_DIGEST_LENGTH).digest()

    # Initialize memory — first two blocks of each lane
    memory: list[bytearray] = [bytearray(BLOCK_SIZE) for _ in range(memory_blocks)]

    for li in range(lanes):
        block_input = bytearray(72)
        struct.pack_into("<Q", block_input, 0, li)
        struct.pack_into("<Q", block_input, 8, 0)
        block_input[16 : 16 + ARGON2_PREHASH_DIGEST_LENGTH] = H0

        first_block = _blake2b_long(bytes(block_input), BLOCK_SIZE)
        memory[li * memory_blocks // lanes] = bytearray(first_block)

        block_input[8:16] = _le64(1)
        second_block = _blake2b_long(bytes(block_input), BLOCK_SIZE)
        memory[li * memory_blocks // lanes + 1] = bytearray(second_block)

    # Fill memory
    for tpass in range(time_cost):
        for sl in range(ARGON2_SYNC_POINTS):
            for li in range(lanes):
                lane_offset = li * segment_length * ARGON2_SYNC_POINTS
                seg_start = lane_offset + sl * segment_length
                seg_end = seg_start + segment_length

                for idx in range(seg_start, seg_end):
                    prev_idx = idx - 1
                    if idx == seg_start:
                        prev_idx = lane_offset + (sl - 1) * segment_length + segment_length - 1
                        if sl == 0:
                            prev_idx = lane_offset + (ARGON2_SYNC_POINTS - 1) * segment_length + segment_length - 1
                    if tpass == 0 and sl == 0 and idx - seg_start < 2:
                        continue

                    prev_block = bytes(memory[prev_idx])

                    if tpass == 0 and sl < 2:
                        j1 = struct.unpack_from("<Q", prev_block, 0)[0]
                        j2 = struct.unpack_from("<Q", prev_block, 8)[0]
                        ref_lane = (j2 >> 32) % lanes
                        ref_area = 0
                        if idx % lanes == ref_lane:
                            ref_area = lane_offset + sl * segment_length
                        else:
                            ref_seg = (j1 >> 32) % ARGON2_SYNC_POINTS
                            ref_area = ref_lane * segment_length * ARGON2_SYNC_POINTS + ref_seg * segment_length
                        ref_idx = ref_area + (j1 & 0xFFFFFFFF) % min(idx - seg_start + 1, segment_length)
                        if ref_idx >= memory_blocks:
                            ref_idx = memory_blocks - 1
                    else:
                        j1 = struct.unpack_from("<Q", prev_block, 0)[0]
                        j2 = struct.unpack_from("<Q", prev_block, 8)[0]
                        ref_lane = (j2 >> 32) % lanes
                        ref_area = lane_offset + sl * segment_length
                        ref_idx = ref_area + (j1 & 0xFFFFFFFF) % min(idx - seg_start + 1, segment_length)
                        if ref_idx >= memory_blocks:
                            ref_idx = memory_blocks - 1

                    ref_block = bytes(memory[ref_idx])
                    memory[idx] = bytearray(_compress(prev_block, ref_block))

    # Finalize — XOR last block of each lane
    result_block = bytearray(BLOCK_SIZE)
    for li in range(lanes):
        last_idx = (li + 1) * memory_blocks // lanes - 1
        for bi in range(BLOCK_SIZE):
            result_block[bi] ^= memory[last_idx][bi]

    tag = hashlib.blake2b(bytes(result_block), digest_size=hash_len).digest()
    return tag


def argon2id_hash(
    password: str,
    salt: str,
    time_cost: int = TIME_COST,
    memory_cost: int = MEMORY_COST,
    parallelism: int = PARALLELISM,
    hash_len: int = HASH_LEN,
) -> str:
    if time_cost < 1:
        _raise(f"time_cost must be >= 1, got {time_cost}")
    if memory_cost < 8 * parallelism:
        _raise(f"memory_cost must be >= {8 * parallelism} KiB, got {memory_cost}")
    if parallelism < 1:
        _raise(f"parallelism must be >= 1, got {parallelism}")
    if hash_len < 4:
        _raise(f"hash_len must be >= 4, got {hash_len}")
    if len(salt) == 0:
        _raise("salt must not be empty")

    pwd_bytes = password.encode("utf-8")
    salt_bytes = _b64dec(salt)

    raw = _argon2_hash(
        password=pwd_bytes,
        salt=salt_bytes,
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=hash_len,
    )

    encoded_hash = _b64enc(raw).rstrip("=")
    return f"$argon2id$v={ARGON2_VERSION}$m={memory_cost},t={time_cost},p={parallelism}${salt}${encoded_hash}"


def _decode_hash(encoded: str) -> tuple[int, dict[str, int], str, str]:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[1] != "argon2id":
        _raise("Invalid Argon2 hash format")
    try:
        version = int(parts[2].split("=")[1])
    except (IndexError, ValueError):
        _raise("Invalid Argon2 hash format — bad version")
    try:
        params = {}
        for item in parts[3].split(","):
            k, v = item.split("=")
            params[k] = int(v)
    except (ValueError, KeyError):
        _raise("Invalid Argon2 hash format — bad params")
    salt = parts[4]
    raw_hash = parts[5]
    return version, params, salt, raw_hash


def argon2id_verify(password: str, encoded_hash: str) -> bool:
    version, params, salt, raw_hash = _decode_hash(encoded_hash)

    raw = _argon2_hash(
        password=password.encode("utf-8"),
        salt=_b64dec(salt),
        time_cost=params["t"],
        memory_cost=params["m"],
        parallelism=params["p"],
        hash_len=HASH_LEN,
        version=version,
    )

    padded = raw_hash + "=" * ((4 - len(raw_hash) % 4) % 4)
    expected = _b64dec(padded)
    return raw == expected


def generate_salt(length: int = SALT_LEN) -> str:
    if length < 1:
        _raise(f"salt length must be >= 1, got {length}")
    return _b64enc(os.urandom(length)).rstrip("=")
