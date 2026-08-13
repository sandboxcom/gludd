"""HyperLogLog v2 — HLL++ with sparse representation and bias correction.

Adds: sparse-list encoding for small cardinalities (reduces memory),
automatic sparse→dense transition, and empirically-measured bias correction
for improved accuracy at small cardinalities.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, ClassVar


class HyperLogLogV2:
    _DEFAULT_SALT: bytes = b"gld_hll2"
    _SERIAL_MAGIC: bytes = b"HLL2"
    _LEGACY_HASH_DOMAIN: int = 1
    _CURRENT_HASH_DOMAIN: int = 2
    _HASH_PERSON: bytes = b"gludd-hll2-v2"

    _SPARSE_THRESHOLD_FACTOR: float = 0.15

    _BIAS_DATA: ClassVar[dict[int, list[float]]] = {
        4: [
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            11.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            12.0,
            6.0,
        ],
    }

    def __init__(self, precision: int = 14) -> None:
        if precision < 4 or precision > 18:
            raise ValueError("precision must be in [4, 18]")
        self._precision = precision
        self._m = 1 << precision
        self._alpha = self._compute_alpha(self._m)
        self._sparse_list: list[tuple[int, int]] = []
        self._registers: bytearray | None = None
        self._hash_domain_version = self._CURRENT_HASH_DOMAIN

    @property
    def precision(self) -> int:
        return self._precision

    @property
    def register_count(self) -> int:
        return self._m

    @property
    def is_sparse(self) -> bool:
        return self._registers is None

    @property
    def hash_domain_version(self) -> int:
        return self._hash_domain_version

    def add(self, item: Any) -> None:
        key = self._item_to_bytes(item)
        h = self._hash64_for_domain(key, self._hash_domain_version)
        idx = h & (self._m - 1)
        w = h >> self._precision
        rho = self._rho(w)

        if self.is_sparse:
            self._sparse_list.append((idx, rho))
            if self._sparse_register_count() > self._sparse_max_entries():
                self._transition_to_dense()
        else:
            regs = self._registers
            assert regs is not None
            if rho > regs[idx % len(regs)]:
                regs[idx % len(regs)] = rho

    def count(self) -> int:
        raw = self._raw_estimate()
        registers = self._registers
        used_linear_counting = self.is_sparse or (
            registers is not None and 0 in registers and raw <= (5.0 / 2.0) * self._m
        )
        corrected = raw if used_linear_counting else self._apply_bias_correction(raw)
        return max(0, int(corrected))

    def merge(self, other: HyperLogLogV2) -> None:
        if self._precision != other._precision:
            raise ValueError("cannot merge HyperLogLogV2 instances with different precision")
        if self._hash_domain_version != other._hash_domain_version:
            raise ValueError("cannot merge HyperLogLogV2 instances from different hash domains")

        if self.is_sparse and other.is_sparse:
            combined = self._sparse_list + other._sparse_list
            if len({idx for idx, _rho in combined}) > self._sparse_max_entries():
                self._transition_to_dense()
                regs_a = self._registers
                assert regs_a is not None
                for idx, rho in combined:
                    if rho > regs_a[idx % len(regs_a)]:
                        regs_a[idx % len(regs_a)] = rho
            else:
                self._sparse_list = combined
            return

        if self.is_sparse and not other.is_sparse:
            self._transition_to_dense()

        regs_b = self._registers
        assert regs_b is not None

        if other.is_sparse:
            for idx, rho in other._sparse_list:
                if rho > regs_b[idx % len(regs_b)]:
                    regs_b[idx % len(regs_b)] = rho
        else:
            other_regs = other._registers
            assert other_regs is not None
            for i in range(self._m):
                if other_regs[i] > regs_b[i]:
                    regs_b[i] = other_regs[i]

    def error_bound(self) -> float:
        return 1.04 / math.sqrt(self._m)

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "!4sBIIB",
            self._SERIAL_MAGIC,
            self._hash_domain_version,
            self._precision,
            self._m,
            1 if self.is_sparse else 0,
        )
        if self.is_sparse:
            sparse_data = b"".join(
                struct.pack("!IB", idx, rho) for idx, rho in self._sparse_list
            )
            return header + struct.pack("!I", len(self._sparse_list)) + sparse_data

        regs_c = self._registers
        assert regs_c is not None
        return header + bytes(regs_c)

    @classmethod
    def from_bytes(cls, raw: bytes) -> HyperLogLogV2:
        if raw.startswith(cls._SERIAL_MAGIC):
            header_size = struct.calcsize("!4sBIIB")
            if len(raw) < header_size:
                raise ValueError("truncated HyperLogLogV2 data")
            _magic, hash_domain, precision, m, is_sparse_flag = struct.unpack(
                "!4sBIIB", raw[:header_size]
            )
            if hash_domain not in {
                cls._LEGACY_HASH_DOMAIN,
                cls._CURRENT_HASH_DOMAIN,
            }:
                raise ValueError(
                    f"unsupported HyperLogLogV2 hash domain: {hash_domain}"
                )
        else:
            header_size = struct.calcsize("!IIB")
            if len(raw) < header_size:
                raise ValueError("truncated HyperLogLogV2 data")
            precision, m, is_sparse_flag = struct.unpack("!IIB", raw[:header_size])
            hash_domain = cls._LEGACY_HASH_DOMAIN

        if precision < 4 or precision > 18 or m != 1 << precision:
            raise ValueError(
                f"invalid HyperLogLogV2 geometry: precision={precision}, registers={m}"
            )
        if is_sparse_flag not in {0, 1}:
            raise ValueError(
                f"invalid HyperLogLogV2 representation flag: {is_sparse_flag}"
            )

        hll = cls.__new__(cls)
        hll._precision = precision
        hll._m = m
        hll._alpha = cls._compute_alpha(m)
        hll._hash_domain_version = hash_domain

        if is_sparse_flag:
            sparse_count_size = struct.calcsize("!I")
            sparse_start = header_size + sparse_count_size
            if len(raw) < sparse_start:
                raise ValueError("truncated HyperLogLogV2 sparse data")
            sparse_count = struct.unpack("!I", raw[header_size:sparse_start])[0]
            entry_size = struct.calcsize("!IB")
            expected_size = sparse_start + sparse_count * entry_size
            if len(raw) != expected_size:
                raise ValueError(
                    "sparse payload length mismatch: "
                    f"expected {expected_size}, got {len(raw)}"
                )
            hll._sparse_list = []
            hll._registers = None
            for i in range(sparse_count):
                offset = sparse_start + i * entry_size
                idx, rho = struct.unpack("!IB", raw[offset : offset + entry_size])
                if idx >= m or rho == 0 or rho > 65 - precision:
                    raise ValueError(
                        f"invalid sparse register: index={idx}, rho={rho}"
                    )
                hll._sparse_list.append((idx, rho))
        else:
            hll._registers = bytearray(raw[header_size:])
            hll._sparse_list = []
            if len(hll._registers) != m:
                raise ValueError(
                    f"register array length mismatch: expected {m}, "
                    f"got {len(hll._registers)}"
                )

        return hll

    def _transition_to_dense(self) -> None:
        if not self.is_sparse:
            return
        self._registers = bytearray(self._m)
        for idx, rho in self._sparse_list:
            if rho > self._registers[idx % self._m]:
                self._registers[idx % self._m] = rho
        self._sparse_list = []

    def _raw_estimate(self) -> float:
        registers = self._registers
        filled_count = 0

        if registers is None:
            reg_map: dict[int, int] = {}
            for idx, rho in self._sparse_list:
                if idx not in reg_map or rho > reg_map[idx]:
                    reg_map[idx] = rho
            z = sum(2.0 ** (-r) for r in reg_map.values())
            filled_count = len(reg_map)
            z += self._m - filled_count
            e = self._alpha * self._m * self._m / max(z, 1e-12)
        else:
            z = sum(2.0 ** (-r) for r in registers)
            e = self._alpha * self._m * self._m / max(z, 1e-12)

        if e <= (5.0 / 2.0) * self._m:
            v = self._m - filled_count if registers is None else sum(1 for r in registers if r == 0)
            if v > 0:
                e = self._m * math.log(self._m / v)

        max_32 = float(1 << 32)
        if e > (1.0 / 30.0) * max_32:
            e = -max_32 * math.log(1.0 - min(e, max_32 - 1.0) / max_32)

        return e

    def _apply_bias_correction(self, raw_e: float) -> float:
        bias_data = self._BIAS_DATA.get(self._precision)
        if bias_data is None or raw_e >= len(bias_data) or raw_e < 0:
            return raw_e
        bias_index = int(raw_e)
        if bias_index >= len(bias_data):
            return raw_e
        bias = bias_data[bias_index]
        if bias == 0.0:
            return raw_e
        return max(0.0, raw_e - bias)

    def _sparse_max_entries(self) -> int:
        return max(1, int(self._m * self._SPARSE_THRESHOLD_FACTOR))

    def _sparse_register_count(self) -> int:
        """Return occupied registers, excluding duplicate observations."""
        return len({idx for idx, _rho in self._sparse_list})

    def _rho(self, w: int) -> int:
        bits = 64 - self._precision
        if w == 0:
            return bits + 1
        return bits - w.bit_length() + 1

    @staticmethod
    def _compute_alpha(m: int) -> float:
        if m == 16:
            return 0.673
        if m == 32:
            return 0.697
        if m == 64:
            return 0.709
        return 0.7213 / (1.0 + 1.079 / m)

    @staticmethod
    def _hash64(key: bytes) -> int:
        digest = hashlib.blake2b(
            key,
            digest_size=8,
            person=HyperLogLogV2._HASH_PERSON,
        ).digest()
        return int.from_bytes(digest, "big")

    @staticmethod
    def _legacy_hash64(key: bytes) -> int:
        data = key + HyperLogLogV2._DEFAULT_SALT
        h1 = HyperLogLogV2._fnv1a_64(data)
        h2 = HyperLogLogV2._fnv1a_64(data + b"\x01")
        return ((h1 << 32) | (h2 & 0xFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF

    @classmethod
    def _hash64_for_domain(cls, key: bytes, hash_domain: int) -> int:
        if hash_domain == cls._CURRENT_HASH_DOMAIN:
            return cls._hash64(key)
        if hash_domain == cls._LEGACY_HASH_DOMAIN:
            return cls._legacy_hash64(key)
        raise ValueError(f"unsupported HyperLogLogV2 hash domain: {hash_domain}")

    @staticmethod
    def _fnv1a_64(data: bytes) -> int:
        h = 0xCBF29CE484222325
        for b in data:
            h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return h

    @staticmethod
    def _item_to_bytes(item: Any) -> bytes:
        if isinstance(item, str):
            return item.encode("utf-8")
        if isinstance(item, bytes):
            return item
        if isinstance(item, int):
            return str(item).encode("utf-8")
        if isinstance(item, float):
            return repr(item).encode("utf-8")
        return str(item).encode("utf-8")
