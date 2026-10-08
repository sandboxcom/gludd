"""Deep entropy codec tests: Huffman tree, canonical Huffman, arithmetic coding.

Covers round-trip encoding/decoding, edge cases, frequency-sensitive code
lengths, canonical code assignment, arithmetic rescaling, and error paths.
15+ test methods.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.entropy_codec import (
    ArithmeticCoder,
    CanonicalCode,
    build_huffman_codes,
    build_huffman_tree,
    huffman_decode,
    huffman_encode,
)


class TestHuffmanTree:
    def test_single_symbol(self) -> None:
        root = build_huffman_tree({"x": 5})
        codes = build_huffman_codes(root)
        assert codes == {"x": "0"}
        enc = huffman_encode(["x", "x", "x"], codes)
        assert enc == "000"
        dec = huffman_decode(enc, root)
        assert dec == ["x", "x", "x"]

    def test_two_symbols_balanced(self) -> None:
        root = build_huffman_tree({"a": 3, "b": 3})
        codes = build_huffman_codes(root)
        assert len(codes) == 2
        assert codes["a"] != codes["b"]
        symbols = ["a", "b", "a", "b"]
        enc = huffman_encode(symbols, codes)
        dec = huffman_decode(enc, root)
        assert dec == symbols

    def test_unbalanced_frequencies(self) -> None:
        root = build_huffman_tree({"a": 10, "b": 2, "c": 1})
        codes = build_huffman_codes(root)
        assert len(codes["a"]) <= len(codes["b"])
        assert len(codes["a"]) <= len(codes["c"])

    def test_prefix_free_property(self) -> None:
        freqs = {"a": 5, "b": 3, "c": 2, "d": 1, "e": 1}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        for s1, c1 in codes.items():
            for s2, c2 in codes.items():
                if s1 != s2:
                    assert not c1.startswith(c2), f"code for {s1!r} ({c1}) is prefix of {s2!r} ({c2})"

    def test_empty_freqs_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            build_huffman_tree({})

    def test_negative_freq_raises(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            build_huffman_tree({"a": -1})

    def test_empty_bitstring_decode(self) -> None:
        root = build_huffman_tree({"a": 1})
        assert huffman_decode("", root) == []

    def test_truncated_bitstring_mid_symbol(self) -> None:
        freqs = {"a": 10, "b": 2, "c": 1}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        bit = huffman_encode(["a", "b"], codes)
        truncated = bit[:-1]
        with pytest.raises(ValueError, match="mid-symbol"):
            huffman_decode(truncated, root)

    def test_extra_bit_mid_symbol(self) -> None:
        freqs = {"a": 10, "b": 2, "c": 1}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        bit = huffman_encode(["a"], codes)
        extra = "1" if bit[-1] == "0" else "0"
        with pytest.raises(ValueError, match="mid-symbol") as excinfo:
            huffman_decode(bit + extra, root)
        assert "mid-symbol" in str(excinfo.value)

    def test_many_symbols_round_trip(self) -> None:
        freqs = {chr(ord("a") + i): i + 1 for i in range(15)}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        msg = list(freqs.keys()) * 3
        enc = huffman_encode(msg, codes)
        dec = huffman_decode(enc, root)
        assert dec == msg

    def test_all_codes_unique(self) -> None:
        freqs = {chr(ord("a") + i): i + 1 for i in range(20)}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        assert len(set(codes.values())) == len(freqs)


class TestCanonicalHuffman:
    def test_round_trip_from_frequencies(self) -> None:
        freqs = {"a": 10, "b": 5, "c": 3, "d": 2}
        codec = CanonicalCode.from_frequencies(freqs)
        msg = ["a", "b", "c", "d", "a", "a"]
        data = codec.encode(msg)
        dec = codec.decode(data, len(msg))
        assert dec == msg

    def test_canonical_assignment_sorted_by_length(self) -> None:
        freqs = {"x": 8, "y": 4, "z": 2, "w": 1}
        codec = CanonicalCode.from_frequencies(freqs)
        sorted_pairs = sorted(codec.bit_widths.items(), key=lambda kv: (kv[1], kv[0]))
        codes_in_order = [codec.base_codes[s] for s, _ in sorted_pairs]
        assert codes_in_order == sorted(codes_in_order)

    def test_empty_encode(self) -> None:
        codec = CanonicalCode.from_frequencies({"a": 1})
        assert codec.encode([]) == b""

    def test_empty_decode(self) -> None:
        codec = CanonicalCode.from_frequencies({"a": 1})
        assert codec.decode(b"", 0) == []
        assert codec.decode(b"\x00", 0) == []

    def test_zero_length_codes(self) -> None:
        codec = CanonicalCode(symbols=["a", "b"], lengths=[0, 2])
        assert codec.base_codes["a"] == 0
        assert codec.base_codes["b"] is not None

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError):
            CanonicalCode(symbols=["a", "b"], lengths=[1])

    def test_negative_lengths_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            CanonicalCode(symbols=["a"], lengths=[-1])


class TestArithmeticCoding:
    def test_binary_round_trip(self) -> None:
        coder = ArithmeticCoder({0: 1, 1: 1})
        msg = [0, 1, 0, 1, 0, 0, 1, 1, 1, 0]
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg

    def test_skewed_round_trip(self) -> None:
        coder = ArithmeticCoder({0: 9, 1: 1})
        msg = [0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg

    def test_three_symbols_round_trip(self) -> None:
        coder = ArithmeticCoder({0: 5, 1: 3, 2: 2})
        msg = [0, 1, 2, 0, 0, 1, 2, 1, 0, 2]
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg

    def test_single_symbol_default(self) -> None:
        coder = ArithmeticCoder({7: 4})
        msg = [7, 7, 7]
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg

    def test_empty_decode_zero_symbols(self) -> None:
        coder = ArithmeticCoder({0: 1, 1: 1})
        assert coder.decode(b"", 0) == []

    def test_zero_total_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            ArithmeticCoder({0: 0})

    def test_negative_total_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            ArithmeticCoder({0: -5, 1: 3})

    def test_long_message_round_trip(self) -> None:
        coder = ArithmeticCoder({0: 3, 1: 2, 2: 1})
        msg = [0, 1, 2, 0, 1, 0, 2, 1, 0, 0, 1, 0, 2, 0, 0, 1, 2, 0, 1, 0]
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg

    def test_compression_ratio_skewed(self) -> None:
        coder = ArithmeticCoder({0: 99, 1: 1})
        msg = [0] * 200
        data = coder.encode(msg)
        assert len(data) < len(msg)

    def test_distinct_but_identical_messages_same_encoding(self) -> None:
        coder = ArithmeticCoder({0: 1, 1: 1})
        d1 = coder.encode([0, 1, 0, 1])
        d2 = coder.encode([0, 1, 0, 1])
        assert d1 == d2

    def test_deterministic_decoding(self) -> None:
        coder = ArithmeticCoder({0: 7, 1: 3})
        msg = [0, 1, 0, 0, 1, 0, 0, 0]
        assert coder.decode(coder.encode(msg), len(msg)) == msg

    def test_rescaling_via_underflow(self) -> None:
        coder = ArithmeticCoder({0: 1, 1: 1, 2: 1})
        msg = [0, 1, 2] * 5
        data = coder.encode(msg)
        dec = coder.decode(data, len(msg))
        assert dec == msg


class TestHuffmanEdgeCases:
    def test_encode_decode_all_same_symbol(self) -> None:
        freqs = {"X": 100}
        root = build_huffman_tree(freqs)
        codes = build_huffman_codes(root)
        msg = ["X"] * 50
        enc = huffman_encode(msg, codes)
        dec = huffman_decode(enc, root)
        assert dec == msg
        assert all(c == "0" for c in enc)
