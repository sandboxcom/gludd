"""FFT algorithms: Cooley-Tukey, bit-reversal, convolution, polynomial multiply.

Pure-Python, stdlib only. Complex-valued FFT and real-valued convolution.
"""

from __future__ import annotations

import math


def _bit_reverse(x: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (x & 1)
        x >>= 1
    return result


def bit_reversal_permutation(n: int) -> list[int]:
    """Return the bit-reversal permutation indices for length n (must be power of 2)."""
    bits = n.bit_length() - 1
    return [_bit_reverse(i, bits) for i in range(n)]


def _next_power_of_two(n: int) -> int:
    return 1 << (n - 1).bit_length()


def fft(a: list[complex], invert: bool = False) -> list[complex]:
    """Cooley-Tukey in-place (logical) FFT.  Length must be a power of 2.

    When invert=True computes the inverse FFT (divided by n).
    """
    n = len(a)
    a = a[:]

    bits = n.bit_length() - 1
    rev = [_bit_reverse(i, bits) for i in range(n)]
    for i in range(n):
        if i < rev[i]:
            a[i], a[rev[i]] = a[rev[i]], a[i]

    length = 2
    while length <= n:
        ang = 2 * math.pi / length * (-1 if invert else 1)
        wlen = complex(math.cos(ang), math.sin(ang))
        for i in range(0, n, length):
            w = 1 + 0j
            half = length // 2
            for j in range(half):
                u = a[i + j]
                v = a[i + j + half] * w
                a[i + j] = u + v
                a[i + j + half] = u - v
                w *= wlen
        length <<= 1

    if invert:
        for i in range(n):
            a[i] /= n

    return a


def ifft(a: list[complex]) -> list[complex]:
    """Inverse FFT (Cooley-Tukey)."""
    return fft(a, invert=True)


def fft_freq(n: int, sample_rate: float = 1.0) -> list[float]:
    """Return frequency bins for an n-point FFT (n must be power of 2)."""
    return [i * sample_rate / n for i in range(n // 2 + 1)]


def convolve(a: list[complex], b: list[complex]) -> list[complex]:
    """Convolve two sequences via FFT: ifft(fft(a_padded) * fft(b_padded))."""
    n = _next_power_of_two(len(a) + len(b) - 1)
    fa = fft(a + [0j] * (n - len(a)))
    fb = fft(b + [0j] * (n - len(b)))
    fc = [fa[i] * fb[i] for i in range(n)]
    result = ifft(fc)
    return result[: len(a) + len(b) - 1]


def polynomial_multiply(p: list[float], q: list[float]) -> list[float]:
    """Multiply two polynomials (coefficient lists, ascending powers) via FFT."""
    p_c = [complex(v, 0) for v in p]
    q_c = [complex(v, 0) for v in q]
    conv = convolve(p_c, q_c)
    return [c.real for c in conv]


def fft_shift(a: list[complex]) -> list[complex]:
    """Shift zero-frequency component to centre of spectrum."""
    n = len(a)
    half = n // 2
    return a[half:] + a[:half]


def ifft_shift(a: list[complex]) -> list[complex]:
    """Inverse of fft_shift (shift zero-frequency back to start)."""
    n = len(a)
    half = (n + 1) // 2
    return a[half:] + a[:half]


def real_fft(x: list[float]) -> list[complex]:
    """FFT of real-valued signal — returns first n//2+1 bins (Hermitian-symmetric).

    Pads to power-of-two, computes full FFT, returns unique half.
    """
    n = _next_power_of_two(len(x))
    x_padded = [complex(v, 0) for v in x] + [0j] * (n - len(x))
    X = fft(x_padded)
    return X[: n // 2 + 1]
