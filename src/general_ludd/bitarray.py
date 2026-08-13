from __future__ import annotations

from collections.abc import Iterator


class BitArray:
    __slots__ = ("_bits", "_size")

    def __init__(self, source: int | list[bool] = 0) -> None:
        if isinstance(source, int):
            self._size = source
            self._bits = bytearray((source + 7) // 8)
        elif isinstance(source, list):
            self._size = len(source)
            byte_count = (self._size + 7) // 8
            self._bits = bytearray(byte_count)
            for i, v in enumerate(source):
                if v:
                    self._bits[i >> 3] |= 1 << (i & 7)
        else:
            raise TypeError(f"Invalid source type: {type(source)}")

    @classmethod
    def from_int(cls, value: int, size: int) -> BitArray:
        inst = cls.__new__(cls)
        inst._size = size
        byte_count = (size + 7) // 8
        inst._bits = bytearray(byte_count)
        for i in range(size):
            if value & (1 << i):
                inst._bits[i >> 3] |= 1 << (i & 7)
        return inst

    @classmethod
    def from_bytes(cls, data: bytes, size: int) -> BitArray:
        inst = cls.__new__(cls)
        inst._size = size
        byte_count = (size + 7) // 8
        inst._bits = bytearray(byte_count)
        for i in range(min(len(data), byte_count)):
            inst._bits[i] = data[i]
        return inst

    @classmethod
    def from_binary_string(cls, s: str) -> BitArray:
        inst = cls.__new__(cls)
        inst._size = len(s)
        byte_count = (inst._size + 7) // 8
        inst._bits = bytearray(byte_count)
        for i, ch in enumerate(s):
            if ch == "1":
                inst._bits[i >> 3] |= 1 << (i & 7)
        return inst

    def __len__(self) -> int:
        return self._size

    def __getitem__(self, index: int) -> bool:
        if index < 0 or index >= self._size:
            raise IndexError(f"index {index} out of range")
        return bool(self._bits[index >> 3] & (1 << (index & 7)))

    def __setitem__(self, index: int, value: bool) -> None:
        if index < 0 or index >= self._size:
            raise IndexError(f"index {index} out of range")
        if value:
            self._bits[index >> 3] |= 1 << (index & 7)
        else:
            self._bits[index >> 3] &= ~(1 << (index & 7))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BitArray):
            return NotImplemented
        return self._size == other._size and self._bits == other._bits

    def __copy__(self) -> BitArray:
        inst = self.__class__.__new__(self.__class__)
        inst._size = self._size
        inst._bits = self._bits.copy()
        return inst

    def __str__(self) -> str:
        return self.to_binary_string()

    def __repr__(self) -> str:
        return f"BitArray('{self.to_binary_string()}')"

    def __iter__(self) -> Iterator[bool]:
        for i in range(self._size):
            yield bool(self._bits[i >> 3] & (1 << (i & 7)))

    def __and__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        r = BitArray(self._size)
        for i in range(len(self._bits)):
            r._bits[i] = self._bits[i] & other._bits[i]
        return r

    def __or__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        r = BitArray(self._size)
        for i in range(len(self._bits)):
            r._bits[i] = self._bits[i] | other._bits[i]
        return r

    def __xor__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        r = BitArray(self._size)
        for i in range(len(self._bits)):
            r._bits[i] = self._bits[i] ^ other._bits[i]
        return r

    def __invert__(self) -> BitArray:
        r = BitArray(self._size)
        for i in range(len(self._bits)):
            r._bits[i] = ~self._bits[i] & 0xFF
        mask = _last_byte_mask(self._size)
        if mask:
            r._bits[-1] &= mask
        return r

    def __iand__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        for i in range(len(self._bits)):
            self._bits[i] &= other._bits[i]
        return self

    def __ior__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        for i in range(len(self._bits)):
            self._bits[i] |= other._bits[i]
        return self

    def __ixor__(self, other: BitArray) -> BitArray:
        self._validate_same_size(other)
        for i in range(len(self._bits)):
            self._bits[i] ^= other._bits[i]
        return self

    def __contains__(self, item: object) -> bool:
        if item is True:
            return self.any()
        if item is False:
            return self.count() < self._size
        return False

    def _validate_same_size(self, other: BitArray) -> None:
        if self._size != other._size:
            raise ValueError(f"length mismatch: {self._size} vs {other._size}")

    def set(self, index: int) -> None:
        if index < 0 or index >= self._size:
            raise IndexError(f"index {index} out of range")
        self._bits[index >> 3] |= 1 << (index & 7)

    def clear(self, index: int) -> None:
        if index < 0 or index >= self._size:
            raise IndexError(f"index {index} out of range")
        self._bits[index >> 3] &= ~(1 << (index & 7))

    def toggle(self, index: int) -> None:
        if index < 0 or index >= self._size:
            raise IndexError(f"index {index} out of range")
        self._bits[index >> 3] ^= 1 << (index & 7)

    def set_all(self) -> None:
        for i in range(len(self._bits)):
            self._bits[i] = 0xFF
        mask = _last_byte_mask(self._size)
        if mask:
            self._bits[-1] = mask

    def clear_all(self) -> None:
        for i in range(len(self._bits)):
            self._bits[i] = 0

    def set_range(self, start: int, end: int) -> None:
        if start > end:
            return
        if end < 0 or end > self._size:
            raise IndexError(f"range end {end} out of range")
        for i in range(start, end):
            self._bits[i >> 3] |= 1 << (i & 7)

    def clear_range(self, start: int, end: int) -> None:
        if start > end:
            return
        if end < 0 or end > self._size:
            raise IndexError(f"range end {end} out of range")
        for i in range(start, end):
            self._bits[i >> 3] &= ~(1 << (i & 7))

    def toggle_range(self, start: int, end: int) -> None:
        if start > end:
            return
        if end < 0 or end > self._size:
            raise IndexError(f"range end {end} out of range")
        for i in range(start, end):
            self._bits[i >> 3] ^= 1 << (i & 7)

    def count(self) -> int:
        return sum(_POPCOUNT[b] for b in self._bits)

    def any(self) -> bool:
        return any(b != 0 for b in self._bits)

    def all_set(self) -> bool:
        mask = _last_byte_mask(self._size)
        for b in self._bits[:-1]:
            if b != 0xFF:
                return False
        if mask:
            return self._bits[-1] == mask
        return self._size == 0 or self._bits[-1] == 0xFF

    def none(self) -> bool:
        return not self.any()

    def first_set(self) -> int | None:
        for i in range(self._size):
            if self._bits[i >> 3] & (1 << (i & 7)):
                return i
        return None

    def to_int(self) -> int:
        return int.from_bytes(self._bits, "little")

    def to_bytes(self) -> bytes:
        return bytes(self._bits)

    def to_binary_string(self) -> str:
        parts: list[str] = []
        for i in range(self._size):
            parts.append("1" if (self._bits[i >> 3] & (1 << (i & 7))) else "0")
        return "".join(parts) if parts else ""

    def resize(self, new_size: int) -> None:
        new_byte_count = (new_size + 7) // 8
        old = self._bits
        self._bits = bytearray(new_byte_count)
        for i in range(min(len(old), new_byte_count)):
            self._bits[i] = old[i]
        mask = _last_byte_mask(new_size)
        if mask:
            self._bits[-1] &= mask
        self._size = new_size


_POPCOUNT: list[int] = [bin(_i).count("1") for _i in range(256)]


def _last_byte_mask(size: int) -> int:
    rem = size % 8
    return (1 << rem) - 1 if rem else 0
