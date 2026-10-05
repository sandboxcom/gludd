"""Physics-collection discrete wavelet transforms using PyWavelets.

Haar and Daubechies D4 wavelets with single- and multi-level
decomposition and reconstruction.  All transforms require input
length to be a power of 2.
"""

from __future__ import annotations

import numpy as np
import pywt
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def _to_signal(data: list[float]) -> FloatArray:
    return np.array(data, dtype=np.float64)


def _from_signal(arr: FloatArray | list[float]) -> list[float]:
    return [float(value) for value in np.asarray(arr, dtype=np.float64).ravel()]


def _check_power_of_two(n: int, label: str = "Signal length") -> None:
    if n < 1 or n & (n - 1) != 0:
        raise ValueError(f"{label} must be a power of 2, got {n}")


_WAVELET_MAP: dict[str, str] = {"haar": "haar", "db4": "db2"}


def _resolve_wavelet(name: str) -> pywt.Wavelet:
    pywt_name = _WAVELET_MAP.get(name)
    if pywt_name is None:
        raise ValueError(f"Unknown wavelet: {name}")
    return pywt.Wavelet(pywt_name)


# ── Filter coefficient helpers ───────────────────────────────────────


def _haar_decomposition_coeffs() -> tuple[list[float], list[float]]:
    w = _resolve_wavelet("haar")
    dec_lo, dec_hi, _rec_lo, _rec_hi = w.filter_bank
    return _from_signal(dec_lo), _from_signal(dec_hi)


def _haar_reconstruction_coeffs() -> tuple[list[float], list[float]]:
    w = _resolve_wavelet("haar")
    _dec_lo, _dec_hi, rec_lo, rec_hi = w.filter_bank
    return _from_signal(rec_lo), _from_signal(rec_hi)


def _daubechies4_decomposition_coeffs() -> tuple[list[float], list[float]]:
    w = _resolve_wavelet("db4")
    dec_lo, dec_hi, _rec_lo, _rec_hi = w.filter_bank
    return _from_signal(dec_lo), _from_signal(dec_hi)


def _daubechies4_reconstruction_coeffs() -> tuple[list[float], list[float]]:
    w = _resolve_wavelet("db4")
    _dec_lo, _dec_hi, rec_lo, rec_hi = w.filter_bank
    return _from_signal(rec_lo), _from_signal(rec_hi)


# ── Convolution with stride ──────────────────────────────────────────


def _convolve_stride(signal: list[float], filt: list[float], stride: int) -> list[float]:
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


# ── Single-level DWT ─────────────────────────────────────────────────


def dwt(signal: list[float], wavelet: str = "haar") -> tuple[list[float], list[float]]:
    """Return one periodized approximation/detail decomposition level."""
    _check_power_of_two(len(signal))
    w = _resolve_wavelet(wavelet)
    arr = _to_signal(signal)
    cA, cD = pywt.dwt(arr, w, mode="periodization")
    return _from_signal(cA), _from_signal(cD)


def idwt(approx: list[float], detail: list[float], wavelet: str = "haar") -> list[float]:
    """Reconstruct one signal level from equal-size coefficient bands."""
    if len(approx) != len(detail):
        raise ValueError(f"Approximation and detail must have equal length, got {len(approx)} and {len(detail)}")
    w = _resolve_wavelet(wavelet)
    cA = _to_signal(approx)
    cD = _to_signal(detail)
    result = pywt.idwt(cA, cD, w, mode="periodization")
    return _from_signal(result)


# ── Multi-level cascade (pyramid decomposition) ──────────────────────


def dwt_cascade(signal: list[float], levels: int, wavelet: str = "haar") -> list[list[float]]:
    """Return a multilevel periodized decomposition in ``wavedec`` order."""
    n = len(signal)
    _check_power_of_two(n)
    if levels < 1:
        raise ValueError(f"Levels must be >= 1, got {levels}")
    if n < (1 << levels):
        raise ValueError(f"Signal length {n} too small for {levels} levels (need >= {1 << levels})")
    w = _resolve_wavelet(wavelet)
    approximation = _to_signal(signal)
    details: list[FloatArray] = []
    for _ in range(levels):
        raw_approximation, raw_detail = pywt.dwt(approximation, w, mode="periodization")
        approximation = np.asarray(raw_approximation, dtype=np.float64)
        detail = np.asarray(raw_detail, dtype=np.float64)
        details.append(detail)
    return [_from_signal(approximation), *(_from_signal(detail) for detail in reversed(details))]


def idwt_cascade(coeffs: list[list[float]], wavelet: str = "haar") -> list[float]:
    """Reconstruct a signal from multilevel coefficients in ``wavedec`` order."""
    if len(coeffs) < 2:
        raise ValueError(f"Need at least 2 coefficient bands, got {len(coeffs)}")
    w = _resolve_wavelet(wavelet)
    arrs: list[FloatArray] = [_to_signal(c) for c in coeffs]
    result = np.asarray(pywt.waverec(arrs, w, mode="periodization"), dtype=np.float64)
    return _from_signal(result)


# ── Energy / utility ─────────────────────────────────────────────────


def coefficient_energy(coeffs: list[list[float]]) -> float:
    """Return the sum of squared values across every coefficient band."""
    total = 0.0
    for band in coeffs:
        for v in band:
            total += v * v
    return total


def wavelet_synthesis_matrix(wavelet: str, length: int) -> list[list[float]]:
    """Return the periodized single-level inverse-transform matrix."""
    _check_power_of_two(length, "Length")
    w = _resolve_wavelet(wavelet)
    half = length // 2
    basis = np.eye(half, dtype=np.float64)
    zeros = np.zeros_like(basis)
    approximation_columns = pywt.idwt(basis, zeros, w, mode="periodization", axis=-1).T
    detail_columns = pywt.idwt(zeros, basis, w, mode="periodization", axis=-1).T
    matrix = np.concatenate((approximation_columns, detail_columns), axis=1)
    return [[float(value) for value in row] for row in matrix]
