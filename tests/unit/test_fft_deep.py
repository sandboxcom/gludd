"""Deep FFT tests: Cooley-Tukey, bit-reversal, convolution, polynomial multiply.

Tests the modules in src/general_ludd/algorithms/fft.py.
"""

from __future__ import annotations

import cmath
import math

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils.fft import (
    convolve,
    fft,
    fft_freq,
    fft_shift,
    ifft,
    ifft_shift,
    polynomial_multiply,
    real_fft,
)

# ── fft / ifft ────────────────────────────────────────────────────────


class TestFFT:
    def test_identity_impulse(self):
        x = [1, 0, 0, 0, 0, 0, 0, 0]
        X = fft(x)
        for v in X:
            assert cmath.isclose(v, 1 + 0j)

    def test_ifft_inverts_fft(self):
        x = [complex(i % 7, (i * 3) % 11) for i in range(8)]
        X = fft(x)
        xr = ifft(X)
        for a, b in zip(x, xr, strict=False):
            assert cmath.isclose(a, b, rel_tol=1e-9)

    def test_fft_dc_component(self):
        x = [3.0 + 0j] * 8
        X = fft(x)
        assert cmath.isclose(X[0], 24 + 0j, rel_tol=1e-9)
        for i in range(1, 8):
            assert cmath.isclose(X[i], 0j, abs_tol=1e-9)

    def test_fft_single_sinusoid(self):
        n = 8
        x = [complex(math.cos(2 * math.pi * 2 * i / n), 0) for i in range(n)]
        X = fft(x)
        assert cmath.isclose(abs(X[2]), n / 2, rel_tol=1e-9)
        assert cmath.isclose(abs(X[6]), n / 2, rel_tol=1e-9)
        for k in (0, 1, 3, 4, 5, 7):
            if k != 2 and k != 6:
                assert cmath.isclose(abs(X[k]), 0, abs_tol=1e-9)

    def test_fft_n2(self):
        x = [1 + 0j, 2 + 0j]
        X = fft(x)
        assert cmath.isclose(X[0], 3 + 0j)
        assert cmath.isclose(X[1], -1 + 0j)

    def test_fft_n1(self):
        x = [5 + 3j]
        X = fft(x)
        assert X == [5 + 3j]

    def test_linearity(self):
        x = [complex(i, i * 2) for i in range(8)]
        y = [complex(i * 3, i + 1) for i in range(8)]
        alpha, beta = 2 + 1j, 1 - 3j
        lhs = fft([alpha * x[i] + beta * y[i] for i in range(8)])
        rhs = [alpha * a + beta * b for a, b in zip(fft(x), fft(y), strict=False)]
        for li, r in zip(lhs, rhs, strict=False):
            assert cmath.isclose(li, r, rel_tol=1e-9)

    def test_parseval(self):
        x = [complex(i % 5, (i * 7) % 13) for i in range(16)]
        X = fft(x)
        energy_time = sum(abs(v) ** 2 for v in x)
        energy_freq = sum(abs(v) ** 2 for v in X) / len(X)
        assert cmath.isclose(energy_time, energy_freq, rel_tol=1e-9)

    def test_shift_theorem(self):
        n = 16
        x = [complex(math.sin(2 * math.pi * i / n), 0) for i in range(n)]
        shift = 3
        y = x[shift:] + x[:shift]
        X = fft(x)
        Y = fft(y)
        for k in range(n):
            expected = X[k] * complex(
                math.cos(2 * math.pi * k * shift / n),
                math.sin(2 * math.pi * k * shift / n),
            )
            assert cmath.isclose(Y[k], expected, rel_tol=1e-9, abs_tol=1e-9)

    def test_conjugate_symmetry_real_input(self):
        n = 16
        x = [complex(math.cos(i * 0.7), 0) for i in range(n)]
        X = fft(x)
        for k in range(1, n):
            assert cmath.isclose(X[k], X[n - k].conjugate(), rel_tol=1e-9)


# ── fft_freq ──────────────────────────────────────────────────────────


class TestFFTFreq:
    def test_n8_sr1(self):
        bins = fft_freq(8, 1.0)
        assert bins == pytest.approx([0.0, 0.125, 0.25, 0.375, 0.5])

    def test_n4_sr100(self):
        bins = fft_freq(4, 100.0)
        assert bins == pytest.approx([0.0, 25.0, 50.0])

    def test_n16_length(self):
        bins = fft_freq(16)
        assert len(bins) == 9
        assert bins[0] == 0.0


# ── convolve ──────────────────────────────────────────────────────────


class TestConvolve:
    def test_simple_convolution(self):
        a = [1 + 0j, 2 + 0j, 3 + 0j]
        b = [1 + 0j, 1 + 0j]
        r = convolve(a, b)
        expected = [1, 3, 5, 3]
        for i, v in enumerate(expected):
            assert cmath.isclose(r[i], complex(v, 0), abs_tol=1e-9)

    def test_delta_identity(self):
        a = [complex(i, 0) for i in range(1, 6)]
        delta = [1 + 0j]
        r = convolve(a, delta)
        for x, y in zip(a, r, strict=False):
            assert cmath.isclose(x, y, abs_tol=1e-9)

    def test_commutativity(self):
        a = [complex(i % 7, 0) for i in range(5)]
        b = [complex((i * 3) % 11, 0) for i in range(4)]
        r1 = convolve(a, b)
        r2 = convolve(b, a)
        for x, y in zip(r1, r2, strict=False):
            assert cmath.isclose(x, y, abs_tol=1e-9)


# ── polynomial_multiply ───────────────────────────────────────────────


class TestPolynomialMultiply:
    def test_linear_times_linear(self):
        p = [1, 2]
        q = [3, 4]
        r = polynomial_multiply(p, q)
        assert len(r) == 3
        assert r == pytest.approx([3, 10, 8])

    def test_quadratic_times_quadratic(self):
        p = [1, 2, 1]
        q = [1, -2, 1]
        r = polynomial_multiply(p, q)
        assert len(r) == 5
        assert r == pytest.approx([1, 0, -2, 0, 1])

    def test_large_polynomials(self):
        p = [float(i % 5) for i in range(100)]
        q = [float((i * 3) % 7) for i in range(80)]
        r = polynomial_multiply(p, q)
        assert len(r) == 179
        assert all(isinstance(v, float) for v in r)

    def test_with_cubics(self):
        p = [-1, 0, 0, 1]
        q = [1, 1, 1, 1]
        r = polynomial_multiply(p, q)
        assert r == pytest.approx([-1, -1, -1, 0, 1, 1, 1])


# ── fft_shift / ifft_shift ────────────────────────────────────────────


class TestFFTShift:
    def test_shift_even(self):
        x = [1 + 0j, 2 + 0j, 3 + 0j, 4 + 0j]
        assert fft_shift(x) == [3 + 0j, 4 + 0j, 1 + 0j, 2 + 0j]

    def test_shift_odd(self):
        x = [1 + 0j, 2 + 0j, 3 + 0j]
        y = fft_shift(x)
        assert y == [3 + 0j, 1 + 0j, 2 + 0j]

    def test_shift_roundtrip_even(self):
        x = [complex(i, i + 1) for i in range(16)]
        assert fft_shift(fft_shift(x)) == x

    def test_ifft_shift_inverts(self):
        x = [complex(i, i * 2) for i in range(8)]
        assert ifft_shift(fft_shift(x)) == x
        x_odd = [complex(i, -i * 3) for i in range(9)]
        assert ifft_shift(fft_shift(x_odd)) == x_odd


# ── real_fft ──────────────────────────────────────────────────────────


class TestRealFFT:
    def test_dc_signal(self):
        x = [5.0] * 8
        X = real_fft(x)
        assert cmath.isclose(X[0], 40 + 0j, rel_tol=1e-9)
        for k in range(1, len(X)):
            assert cmath.isclose(X[k], 0j, abs_tol=1e-9)

    def test_single_sinusoid(self):
        n = 16
        x = [math.sin(2 * math.pi * 3 * i / n) for i in range(n)]
        X = real_fft(x)
        assert cmath.isclose(abs(X[3]), n / 2, rel_tol=1e-2)
        for k in range(len(X)):
            if k != 3:
                assert cmath.isclose(abs(X[k]), 0, abs_tol=1e-2)

    def test_power_two_length(self):
        x = [math.cos(i * 0.5) for i in range(32)]
        X = real_fft(x)
        assert len(X) == 17

    def test_odd_length_pads_to_pow2(self):
        X = real_fft([1.0, 2.0, 3.0])
        assert len(X) == 3
