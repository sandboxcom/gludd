"""Discrete wavelet transform: Haar, Daubechies D4.

Pure-Python, stdlib only. Forward and inverse transforms with
multi-level cascade decomposition.

All transforms require input length to be a power of 2.
"""

from __future__ import annotations

import math

# ── Haar wavelet ───────────────────────────────────────────────────────


def _haar_decomposition_coeffs() -> tuple[list[float], list[float]]:
    """Return Haar low-pass and high-pass filter coefficients."""
    s = 1.0 / math.sqrt(2)
    low = [s, s]
    high = [s, -s]
    return low, high


def _haar_reconstruction_coeffs() -> tuple[list[float], list[float]]:
    low, high = _haar_decomposition_coeffs()
    return low, high


# ── Daubechies D4 wavelet ──────────────────────────────────────────────


def _daubechies4_decomposition_coeffs() -> tuple[list[float], list[float]]:
    """Return Daubechies D4 low-pass and high-pass filter coefficients.

    Low-pass filter h (scaling function coefficients).
    High-pass filter g derived via g[k] = (-1)^k * h[3 - k].
    """
    sqrt3 = math.sqrt(3)
    denom = 4.0 * math.sqrt(2)
    h0 = (1.0 + sqrt3) / denom
    h1 = (3.0 + sqrt3) / denom
    h2 = (3.0 - sqrt3) / denom
    h3 = (1.0 - sqrt3) / denom
    low = [h0, h1, h2, h3]
    high = [h3, -h2, h1, -h0]
    return low, high


def _daubechies4_reconstruction_coeffs() -> tuple[list[float], list[float]]:
    low, high = _daubechies4_decomposition_coeffs()
    return low, high


# ── Convolution with stride ────────────────────────────────────────────


def _convolve_stride(signal: list[float], filt: list[float], stride: int) -> list[float]:
    """Convolve signal with filter, downsampling by stride (typically 2).

    Periodic boundary extension for filter length > 1.
    """
    n = len(signal)
    f_len = len(filt)
    result: list[float] = []
    for i in range(0, n, stride):
        acc = 0.0
        for k in range(f_len):
            idx = (i + k) % n
            acc += signal[idx] * filt[k]
        result.append(acc)
    return result


# ── Single-level DWT ───────────────────────────────────────────────────


def dwt(signal: list[float], wavelet: str = "haar") -> tuple[list[float], list[float]]:
    """Single-level discrete wavelet transform.

    Returns (approximation, detail) coefficients, each half the length
    of the input signal.  Input length must be a power of 2.

    wavelet: "haar" or "db4" (Daubechies D4).
    """
    n = len(signal)
    if n & (n - 1) != 0:
        raise ValueError(f"Signal length must be a power of 2, got {n}")

    if wavelet == "haar":
        low, high = _haar_decomposition_coeffs()
    elif wavelet == "db4":
        low, high = _daubechies4_decomposition_coeffs()
    else:
        raise ValueError(f"Unknown wavelet: {wavelet}")

    approx = _convolve_stride(signal, low, 2)
    detail = _convolve_stride(signal, high, 2)
    return approx, detail


def idwt(approx: list[float], detail: list[float], wavelet: str = "haar") -> list[float]:
    """Single-level inverse discrete wavelet transform.

    Reconstructs the original signal from approximation and detail
    coefficients via upsampling and synthesis filtering.

    Input coefficient arrays must have equal length.
    """
    if len(approx) != len(detail):
        raise ValueError(f"Approximation and detail must have equal length, got {len(approx)} and {len(detail)}")

    if wavelet == "haar":
        low, high = _haar_reconstruction_coeffs()
    elif wavelet == "db4":
        low, high = _daubechies4_reconstruction_coeffs()
    else:
        raise ValueError(f"Unknown wavelet: {wavelet}")

    f_len = len(low)
    n_coeff = len(approx)
    n_out = 2 * n_coeff
    result: list[float] = [0.0] * n_out
    for i in range(n_coeff):
        for k in range(f_len):
            idx = (2 * i + k) % n_out
            result[idx] += approx[i] * low[k] + detail[i] * high[k]
    return result


# ── Multi-level cascade (pyramid decomposition) ────────────────────────


def dwt_cascade(signal: list[float], levels: int, wavelet: str = "haar") -> list[list[float]]:
    """Multi-level DWT decomposition (pyramid).

    Returns a list of coefficient bands: [approx_level_N, detail_N,
    detail_{N-1}, ..., detail_1], where each successive approx is
    further decomposed.

    levels: number of decomposition levels (must be >= 1).
            The signal length must be >= 2^levels.
    """
    n = len(signal)
    if n & (n - 1) != 0:
        raise ValueError(f"Signal length must be a power of 2, got {n}")
    if levels < 1:
        raise ValueError(f"Levels must be >= 1, got {levels}")
    if n < (1 << levels):
        raise ValueError(f"Signal length {n} too small for {levels} levels (need >= {1 << levels})")

    coeffs: list[list[float]] = []
    current = list(signal)
    for _ in range(levels):
        approx, detail = dwt(current, wavelet)
        coeffs.append(detail)
        current = approx
    coeffs.append(current)
    coeffs.reverse()
    return coeffs


def idwt_cascade(coeffs: list[list[float]], wavelet: str = "haar") -> list[float]:
    """Multi-level inverse DWT (reconstruction from pyramid).

    coeffs: [approx_level_N, detail_N, detail_{N-1}, ..., detail_1]
    """
    if len(coeffs) < 2:
        raise ValueError(f"Need at least 2 coefficient bands, got {len(coeffs)}")

    approx = list(coeffs[0])
    for i in range(1, len(coeffs)):
        detail = coeffs[i]
        approx = idwt(approx, detail, wavelet)
    return approx


# ── Energy / utility ───────────────────────────────────────────────────


def coefficient_energy(coeffs: list[list[float]]) -> float:
    """Total energy (sum of squares) of all coefficient bands."""
    total = 0.0
    for band in coeffs:
        for v in band:
            total += v * v
    return total


def wavelet_synthesis_matrix(wavelet: str, length: int) -> list[list[float]]:
    """Build the synthesis (reconstruction) matrix for the given wavelet.

    The matrix is (length x length).  Applying it to the stacked
    coefficient vector reconstructs the original signal.
    This explicitly verifies perfect reconstruction as a matrix product.

    wavelet: "haar" or "db4".
    length: must be a power of 2.
    """
    if length & (length - 1) != 0:
        raise ValueError(f"Length must be a power of 2, got {length}")

    if wavelet == "haar":
        low, high = _haar_reconstruction_coeffs()
        f_len = 2
    elif wavelet == "db4":
        low, high = _daubechies4_reconstruction_coeffs()
        f_len = 4
    else:
        raise ValueError(f"Unknown wavelet: {wavelet}")

    half = length // 2
    matrix: list[list[float]] = [[0.0] * length for _ in range(length)]

    for col in range(half):
        for k in range(f_len):
            row = (col * 2 + k) % length
            matrix[row][col] += low[k]
            matrix[row][col + half] += high[k]

    return matrix
