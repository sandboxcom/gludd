"""Entropy coding algorithms: Huffman tree, canonical Huffman, arithmetic coding.

Pure-Python, stdlib only.  Each codec provides encode/decode on sequences of
hashable symbols, optionally driven by frequency counts or explicit code tables.
"""

from __future__ import annotations

import heapq
from typing import Generic, TypeVar

T = TypeVar("T")


class HuffmanNode(Generic[T]):
    """Represent ``HuffmanNode`` values."""
    __slots__ = ("left", "right", "symbol", "weight")

    def __init__(
        self,
        symbol: T | None = None,
        weight: float = 0.0,
        left: HuffmanNode[T] | None = None,
        right: HuffmanNode[T] | None = None,
    ) -> None:
        """Initialize a ``HuffmanNode`` instance."""
        self.symbol = symbol
        self.weight = weight
        self.left = left
        self.right = right

    def __lt__(self, other: HuffmanNode[T]) -> bool:
        """Compare this instance with another value."""
        return self.weight < other.weight


def build_huffman_tree(freqs: dict[T, int]) -> HuffmanNode[T]:
    """Build huffman tree."""
    if len(freqs) == 0:
        raise ValueError("frequency dictionary must be non-empty")
    heap: list[tuple[float, int, HuffmanNode[T]]] = []
    counter = 0
    for sym, count in freqs.items():
        if count < 0:
            raise ValueError(f"negative frequency for symbol {sym!r}")
        heapq.heappush(heap, (float(count), counter, HuffmanNode(symbol=sym, weight=float(count))))
        counter += 1
    if len(heap) == 1:
        _, _, solo = heap[0]
        return HuffmanNode(weight=solo.weight, left=solo)
    while len(heap) > 1:
        w1, _, n1 = heapq.heappop(heap)
        w2, _, n2 = heapq.heappop(heap)
        parent = HuffmanNode(weight=w1 + w2, left=n1, right=n2)
        heapq.heappush(heap, (parent.weight, counter, parent))
        counter += 1
    return heap[0][2]


def build_huffman_codes(root: HuffmanNode[T]) -> dict[T, str]:
    """Build huffman codes."""
    codes: dict[T, str] = {}
    _generate_codes(root, "", codes)
    return codes


def _generate_codes(node: HuffmanNode[T] | None, prefix: str, codes: dict[T, str]) -> None:
    if node is None:
        return
    if node.symbol is not None:
        codes[node.symbol] = prefix if prefix else "0"
    else:
        _generate_codes(node.left, prefix + "0", codes)
        _generate_codes(node.right, prefix + "1", codes)


def huffman_encode(symbols: list[T], codes: dict[T, str]) -> str:
    """Execute ``huffman_encode``."""
    return "".join(codes[sym] for sym in symbols)


def huffman_decode(bitstring: str, root: HuffmanNode[T]) -> list[T]:
    """Execute ``huffman_decode``."""
    if bitstring == "":
        return []
    result: list[T] = []
    current = root
    for bit in bitstring:
        next_node = current.left if bit == "0" else current.right
        if next_node is None:
            raise ValueError(f"invalid bitstring: dead-end at '{bit}'")
        current = next_node
        if current.symbol is not None:
            result.append(current.symbol)
            current = root
    if current is not root:
        raise ValueError("bitstring ended mid-symbol")
    return result


def _bitstring_to_bytes(bitstring: str) -> bytes:
    padding = (-len(bitstring)) % 8
    padded = bitstring + "0" * padding
    return bytes(int(padded[start : start + 8], 2) for start in range(0, len(padded), 8))


def _bytes_to_bitstring(data: bytes) -> str:
    return "".join(format(b, "08b") for b in data)


class CanonicalCode(Generic[T]):
    """Represent ``CanonicalCode`` values."""
    __slots__ = ("base_codes", "bit_widths", "lengths", "symbols")

    def __init__(self, symbols: list[T], lengths: list[int]):
        """Initialize a ``CanonicalCode`` instance."""
        if len(symbols) != len(lengths):
            raise ValueError("symbols and lengths must have the same length")
        if any(w < 0 for w in lengths):
            raise ValueError("code lengths must be non-negative")
        self.symbols = list(symbols)
        self.lengths = list(lengths)
        self.bit_widths: dict[T, int] = dict(zip(symbols, lengths, strict=False))
        self.base_codes: dict[T, int] = {}
        _canonical_assign(self.symbols, self.lengths, self.base_codes)

    @classmethod
    def from_frequencies(cls, freqs: dict[T, int]) -> CanonicalCode[T]:
        """Execute ``from_frequencies``."""
        if len(freqs) == 0:
            return cls([], [])
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        pairs = sorted(codes.items(), key=lambda x: (len(x[1]), x[0]))
        symbols = [s for s, _ in pairs]
        lengths = [len(c) for _, c in pairs]
        return cls(symbols, lengths)

    def encode(self, symbols: list[T]) -> bytes:
        """Encode the value."""
        bits = (format(self.base_codes[sym], f"0{self.bit_widths[sym]}b") for sym in symbols)
        return _bitstring_to_bytes("".join(bits))

    def decode(self, data: bytes, num_symbols: int) -> list[T]:
        """Decode the value."""
        if num_symbols == 0:
            return []
        length_sorted = sorted(
            [(s, w) for s, w in self.bit_widths.items() if w > 0],
            key=lambda x: x[1],
        )
        result: list[T] = []
        bitstring = _bytes_to_bitstring(data)
        pos = 0
        n = len(bitstring)
        while len(result) < num_symbols and pos + 1 <= n:
            found = False
            for sym, w in length_sorted:
                end = pos + w
                if end > n:
                    continue
                code = int(bitstring[pos:end], 2)
                if code == self.base_codes[sym]:
                    result.append(sym)
                    pos = end
                    found = True
                    break
            if not found:
                break
        return result


def _canonical_assign(
    symbols: list[T],
    lengths: list[int],
    base_codes: dict[T, int],
) -> None:
    idx = sorted(range(len(symbols)), key=lambda i: (lengths[i], symbols[i]))
    prev_code = 0
    prev_len = 0
    for i in idx:
        length = lengths[i]
        if length == 0:
            base_codes[symbols[i]] = 0
            continue
        prev_code = prev_code + 1 << length - prev_len if prev_len > 0 else 0
        base_codes[symbols[i]] = prev_code
        prev_len = length


class ArithmeticCoder:
    """Represent ``ArithmeticCoder`` values."""
    PRECISION = 32
    HALF = 1 << (PRECISION - 1)
    QUARTER = 1 << (PRECISION - 2)
    THREE_QUARTERS = 3 * QUARTER
    MAX_CODE = (1 << PRECISION) - 1

    def __init__(self, freqs: dict[int, int]):
        """Initialize a ``ArithmeticCoder`` instance."""
        total = sum(freqs.values())
        if total <= 0:
            raise ValueError("total frequency must be positive")
        self.freqs: dict[int, int] = dict(freqs)
        self.total = total
        self.low_bound: dict[int, int] = {}
        self.high_bound: dict[int, int] = {}
        cum = 0
        for sym in sorted(freqs.keys()):
            count = freqs[sym]
            self.low_bound[sym] = cum
            cum += count
            self.high_bound[sym] = cum

    def encode(self, symbols: list[int]) -> bytes:
        """Encode the value."""
        low = 0
        high = self.MAX_CODE
        pending_bits = 0
        out_bits: list[str] = []
        for sym in symbols:
            rng = high - low + 1
            sym_low = self.low_bound[sym]
            sym_high = self.high_bound[sym]
            high = low + (rng * sym_high) // self.total - 1
            low = low + (rng * sym_low) // self.total
            while True:
                if high < self.HALF:
                    out_bits.append("0")
                    out_bits.extend(["1"] * pending_bits)
                    pending_bits = 0
                    low = low * 2
                    high = high * 2 + 1
                elif low >= self.HALF:
                    out_bits.append("1")
                    out_bits.extend(["0"] * pending_bits)
                    pending_bits = 0
                    low = (low - self.HALF) * 2
                    high = (high - self.HALF) * 2 + 1
                elif low >= self.QUARTER and high < self.THREE_QUARTERS:
                    pending_bits += 1
                    low = (low - self.QUARTER) * 2
                    high = (high - self.QUARTER) * 2 + 1
                else:
                    break
        pending_bits += 1
        if low < self.QUARTER:
            out_bits.append("0")
            out_bits.extend(["1"] * pending_bits)
        else:
            out_bits.append("1")
            out_bits.extend(["0"] * pending_bits)
        bitstring = "".join(out_bits)
        bit_count = len(bitstring)
        header = bit_count.to_bytes(4, "big")
        return header + _bitstring_to_bytes(bitstring)

    def decode(self, data: bytes, num_symbols: int) -> list[int]:
        """Decode the value."""
        if num_symbols == 0:
            return []
        bit_count = int.from_bytes(data[:4], "big")
        payload = data[4:]
        bitstring_padded = _bytes_to_bitstring(payload)
        bitstring = bitstring_padded[:bit_count]
        if bit_count > len(bitstring_padded):
            raise ValueError(f"bit_count {bit_count} exceeds payload bits {len(bitstring_padded)}")
        value = 0
        init_bits = min(self.PRECISION, bit_count)
        for i in range(init_bits):
            value = (value << 1) | int(bitstring[i])
        if init_bits < self.PRECISION:
            value = value << (self.PRECISION - init_bits)
        low = 0
        high = self.MAX_CODE
        result: list[int] = []
        bit_pos = init_bits
        for _ in range(num_symbols):
            rng = high - low + 1
            scaled = ((value - low + 1) * self.total - 1) // rng
            found: int | None = None
            for sym in sorted(self.freqs.keys()):
                if self.low_bound[sym] <= scaled < self.high_bound[sym]:
                    found = sym
                    break
            if found is None:
                if scaled == self.total:
                    found = max(self.freqs.keys())
                else:
                    raise ValueError(f"decoding failure at position {len(result)}: scaled={scaled}, total={self.total}")
            sym = found
            result.append(sym)
            sym_low = self.low_bound[sym]
            sym_high = self.high_bound[sym]
            high = low + (rng * sym_high) // self.total - 1
            low = low + (rng * sym_low) // self.total
            while True:
                if high < self.HALF:
                    low = low * 2
                    high = high * 2 + 1
                    value = (value << 1) & self.MAX_CODE
                    if bit_pos < bit_count:
                        value |= int(bitstring[bit_pos])
                        bit_pos += 1
                elif low >= self.HALF:
                    low = (low - self.HALF) * 2
                    high = (high - self.HALF) * 2 + 1
                    value = ((value - self.HALF) << 1) & self.MAX_CODE
                    if bit_pos < bit_count:
                        value |= int(bitstring[bit_pos])
                        bit_pos += 1
                elif low >= self.QUARTER and high < self.THREE_QUARTERS:
                    low = (low - self.QUARTER) * 2
                    high = (high - self.QUARTER) * 2 + 1
                    value = ((value - self.QUARTER) << 1) & self.MAX_CODE
                    if bit_pos < bit_count:
                        value |= int(bitstring[bit_pos])
                        bit_pos += 1
                else:
                    break
        return result
