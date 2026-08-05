"""Deep tests for Number Theoretic Transform: NTT, INTT, convolution under modulus."""

from __future__ import annotations

import random

import pytest

from general_ludd.algorithms.ntt import (
    MOD,
    intt,
    ntt,
    ntt_convolve,
    ntt_multiply,
    primitive_root,
)

# ── shared helpers ──────────────────────────────────────────────────


def _naive_convolve(a: list[int], b: list[int], mod: int = MOD) -> list[int]:
    n, m = len(a), len(b)
    result = [0] * (n + m - 1)
    for i in range(n):
        for j in range(m):
            result[i + j] = (result[i + j] + a[i] * b[j]) % mod
    return result


def _mod_pow(base: int, exp: int, mod: int) -> int:
    result = 1
    base %= mod
    while exp:
        if exp & 1:
            result = (result * base) % mod
        base = (base * base) % mod
        exp >>= 1
    return result


# ── primitive root ──────────────────────────────────────────────────


class TestPrimitiveRoot:
    def test_primitive_root_is_primitive(self) -> None:
        g = primitive_root(MOD)
        assert _mod_pow(g, MOD - 1, MOD) == 1
        assert _mod_pow(g, (MOD - 1) // 2, MOD) != 1

    def test_primitive_root_for_other_mods(self) -> None:
        mods = [1004535809, 469762049, 167772161]
        for m in mods:
            g = primitive_root(m)
            assert _mod_pow(g, m - 1, m) == 1
            assert _mod_pow(g, (m - 1) // 2, m) != 1

    def test_primitive_root_small_mod(self) -> None:
        g = primitive_root(17)
        seen = set()
        for k in range(1, 17):
            seen.add(_mod_pow(g, k, 17))
        assert len(seen) == 16


# ── NTT / INTT identity ─────────────────────────────────────────────


class TestNttIdentity:
    @pytest.mark.parametrize("size", [1, 2, 4, 8, 16, 32, 64, 128])
    def test_ntt_intt_roundtrip(self, size: int) -> None:
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        result = intt(ntt(a), size)
        assert result == a

    def test_intt_ntt_roundtrip(self) -> None:
        a = [random.randint(0, MOD - 1) for _ in range(8)]
        result = ntt(intt(a, len(a)))
        assert result == a

    def test_double_ntt_intt_is_identity(self) -> None:
        size = 16
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        result = intt(ntt(ntt(intt(a, size))), size)
        assert result == a


# ── linearity ───────────────────────────────────────────────────────


class TestNttLinearity:
    def test_ntt_additive(self) -> None:
        size = 8
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        b = [random.randint(0, MOD - 1) for _ in range(size)]
        sum_ntt = ntt([(a[i] + b[i]) % MOD for i in range(size)])
        ntt_sum = [(ntt(a)[i] + ntt(b)[i]) % MOD for i in range(size)]
        assert sum_ntt == ntt_sum

    def test_ntt_scalar_multiplication(self) -> None:
        size = 16
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        c = 42
        scaled = ntt([(x * c) % MOD for x in a])
        expected = [(x * c) % MOD for x in ntt(a)]
        assert scaled == expected

    def test_intt_additive(self) -> None:
        size = 8
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        b = [random.randint(0, MOD - 1) for _ in range(size)]
        sum_intt = intt([(a[i] + b[i]) % MOD for i in range(size)], size)
        intt_sum = [(intt(a, size)[i] + intt(b, size)[i]) % MOD for i in range(size)]
        assert sum_intt == intt_sum


# ── convolution ─────────────────────────────────────────────────────


class TestConvolution:
    def test_vs_naive_small(self) -> None:
        a = [1, 2, 3]
        b = [4, 5]
        expected = _naive_convolve(a, b)
        result = ntt_convolve(a, b)
        assert result == expected

    def test_vs_naive_medium(self) -> None:
        a = [random.randint(0, 100) for _ in range(8)]
        b = [random.randint(0, 100) for _ in range(5)]
        expected = _naive_convolve(a, b)
        result = ntt_convolve(a, b)
        assert result == expected

    def test_vs_naive_large(self) -> None:
        random.seed(42)
        a = [random.randint(0, 1000) for _ in range(64)]
        b = [random.randint(0, 1000) for _ in range(32)]
        expected = _naive_convolve(a, b)
        result = ntt_convolve(a, b)
        assert result == expected

    def test_identity_impulse(self) -> None:
        a = [random.randint(0, MOD - 1) for _ in range(16)]
        impulse = [1] + [0] * 15
        result = ntt_convolve(a, impulse)
        assert result[: len(a)] == a

    def test_commutative(self) -> None:
        a = [random.randint(0, 100) for _ in range(8)]
        b = [random.randint(0, 100) for _ in range(6)]
        assert ntt_convolve(a, b) == ntt_convolve(b, a)

    def test_associative(self) -> None:
        a = [1, 2]
        b = [3, 4]
        c = [5, 6]
        left = ntt_convolve(ntt_convolve(a, b), c)
        right = ntt_convolve(a, ntt_convolve(b, c))
        assert left == right

    def test_single_element_sequences(self) -> None:
        assert ntt_convolve([7], [3]) == [21 % MOD]

    def test_zero_padding_result_length(self) -> None:
        a = [1, 2]
        b = [3, 4, 5]
        result = ntt_convolve(a, b)
        assert len(result) == len(a) + len(b) - 1

    def test_all_zeros(self) -> None:
        a = [0] * 8
        b = [random.randint(0, MOD - 1) for _ in range(4)]
        assert ntt_convolve(a, b) == [0] * (len(a) + len(b) - 1)


# ── polynomial multiplication ───────────────────────────────────────


class TestPolynomialMultiply:
    def test_simple(self) -> None:
        p = [1, 1]
        q = [1, 1]
        result = ntt_multiply(p, q)
        assert result == [1, 2, 1]

    def test_quadratic(self) -> None:
        p = [1, 2, 3]
        q = [3, 2, 1]
        result = ntt_multiply(p, q)
        assert result == [3, 8, 14, 8, 3]

    def test_modulus_wrap(self) -> None:
        p = [MOD - 1, 1]
        q = [1, 1]
        result = ntt_multiply(p, q)
        expected = [(MOD - 1), 0, 1]
        assert result == expected


# ── edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_element_roundtrip(self) -> None:
        for val in [0, 1, MOD - 1, MOD // 2]:
            assert intt(ntt([val]), 1) == [val]

    def test_alternating_sign(self) -> None:
        size = 8
        a = [1, MOD - 1] * (size // 2)
        result = intt(ntt(a), size)
        assert result == a

    def test_max_values(self) -> None:
        size = 4
        a = [MOD - 1] * size
        result = intt(ntt(a), size)
        assert result == a

    def test_geometric_series(self) -> None:
        size = 8
        m = 3
        a = [pow(m, i, MOD) for i in range(size)]
        result = intt(ntt(a), size)
        assert result == a


# ── involution (double NTT = scaled reversal) ───────────────────────


class TestInvolution:
    def test_double_ntt_is_scaled_reversal(self) -> None:
        size = 16
        a = [random.randint(0, MOD - 1) for _ in range(size)]
        doubled = ntt(ntt(a))
        expected = [(a[(-i) % size] * size) % MOD for i in range(size)]
        assert doubled == expected

    def test_double_ntt_mod_1004535809(self) -> None:
        mod = 1004535809
        size = 8
        a = [random.randint(0, mod - 1) for _ in range(size)]
        doubled = ntt(ntt(a, mod), mod)
        expected = [(a[(-i) % size] * size) % mod for i in range(size)]
        assert doubled == expected


# ── batch stability ─────────────────────────────────────────────────


class TestBatchStability:
    def test_many_small_transforms(self) -> None:
        random.seed(123)
        for size in [1, 2, 4, 8]:
            for _ in range(50):
                a = [random.randint(0, MOD - 1) for _ in range(size)]
                assert intt(ntt(a), size) == a

    def test_deterministic(self) -> None:
        a = list(range(16))
        result1 = ntt(a)
        result2 = ntt(a)
        assert result1 == result2


# ── convolution with different moduli ───────────────────────────────


class TestConvolutionOtherMods:
    def test_mod_1004535809(self) -> None:
        mod = 1004535809
        a = [1, 2, 3]
        b = [4, 5]
        result = ntt_convolve(a, b, mod)
        assert result == _naive_convolve(a, b, mod)

    def test_mod_469762049(self) -> None:
        mod = 469762049
        a = [random.randint(0, 100) for _ in range(8)]
        b = [random.randint(0, 100) for _ in range(4)]
        result = ntt_convolve(a, b, mod)
        assert result == _naive_convolve(a, b, mod)
