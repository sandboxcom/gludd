"""Physics-collection FFT adapters backed by ``numpy.fft``.

All functions delegate to numpy's battle-tested FFT implementation.
"""

from __future__ import annotations

from typing import cast

import numpy as np


def _next_power_of_two(n: int) -> int:
    return 1 << (n - 1).bit_length()


def fft(a: list[complex], invert: bool = False) -> list[complex]:
    arr = np.array(a, dtype=complex)
    result = np.fft.ifft(arr) if invert else np.fft.fft(arr)
    return cast("list[complex]", result.tolist())


def ifft(a: list[complex]) -> list[complex]:
    return fft(a, invert=True)


def fft_freq(n: int, sample_rate: float = 1.0) -> list[float]:
    return cast("list[float]", np.fft.rfftfreq(n, d=1.0 / sample_rate).tolist())


def convolve(a: list[complex], b: list[complex]) -> list[complex]:
    n = _next_power_of_two(len(a) + len(b) - 1)
    fa = np.array(a + [0j] * (n - len(a)), dtype=complex)
    fb = np.array(b + [0j] * (n - len(b)), dtype=complex)
    result = np.fft.ifft(np.fft.fft(fa) * np.fft.fft(fb))
    return cast("list[complex]", result[: len(a) + len(b) - 1].tolist())


def polynomial_multiply(p: list[float], q: list[float]) -> list[float]:
    p_c = [complex(v, 0) for v in p]
    q_c = [complex(v, 0) for v in q]
    conv = convolve(p_c, q_c)
    return [c.real for c in conv]


def fft_shift(a: list[complex]) -> list[complex]:
    return cast("list[complex]", np.fft.fftshift(np.array(a, dtype=complex)).tolist())


def ifft_shift(a: list[complex]) -> list[complex]:
    return cast("list[complex]", np.fft.ifftshift(np.array(a, dtype=complex)).tolist())


def real_fft(x: list[float]) -> list[complex]:
    n = _next_power_of_two(len(x))
    x_padded = np.array(x + [0.0] * (n - len(x)), dtype=float)
    return cast("list[complex]", np.fft.rfft(x_padded).tolist())
