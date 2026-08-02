"""Signal analysis engine for IQ sample processing, FFT analysis, signal
detection, SNR estimation, and bandwidth estimation.

Core operations:
    - FFT magnitude spectrum computation
    - Noise floor estimation via percentile statistics
    - Peak detection with configurable SNR threshold
    - Adjacent-bin signal grouping (bandwidth estimation)
    - Center frequency estimation
    - Power normalization

The SignalAnalyzer class orchestrates the full pipeline: IQ samples in,
detected signals out, with JSON-exportable results.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from typing import Any


@dataclasses.dataclass
class AnalyzerConfig:
    """Configuration for the SignalAnalyzer engine.

    Attributes:
        sample_rate_hz: ADC sample rate in Hz.
        fft_size: FFT bin count (power of 2 recommended).
        noise_percentile: Percentile used for noise floor estimation (0-100).
        detection_threshold_db: Minimum SNR above noise floor to detect.
        min_bandwidth_hz: Minimum bandwidth to report for a signal.
        max_bandwidth_hz: Maximum bandwidth to consider (caps grouping).
    """

    sample_rate_hz: int = 2_048_000
    fft_size: int = 1024
    noise_percentile: float = 25.0
    detection_threshold_db: float = 6.0
    min_bandwidth_hz: int = 100
    max_bandwidth_hz: int = 1_000_000

    @property
    def freq_resolution_hz(self) -> float:
        return self.sample_rate_hz / self.fft_size

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_rate_hz": self.sample_rate_hz,
            "fft_size": self.fft_size,
            "noise_percentile": self.noise_percentile,
            "detection_threshold_db": self.detection_threshold_db,
            "min_bandwidth_hz": self.min_bandwidth_hz,
            "max_bandwidth_hz": self.max_bandwidth_hz,
            "freq_resolution_hz": self.freq_resolution_hz,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnalyzerConfig:
        return cls(
            sample_rate_hz=d.get("sample_rate_hz", 2_048_000),
            fft_size=d.get("fft_size", 1024),
            noise_percentile=d.get("noise_percentile", 25.0),
            detection_threshold_db=d.get("detection_threshold_db", 6.0),
            min_bandwidth_hz=d.get("min_bandwidth_hz", 100),
            max_bandwidth_hz=d.get("max_bandwidth_hz", 1_000_000),
        )


def compute_fft_magnitude(
    iq_samples: list[complex],
    sample_rate_hz: int,
    fft_size: int | None = None,
) -> tuple[list[float], list[float]]:
    """Compute power spectrum in dB from complex IQ samples.

    Uses a DFT implementation (no NumPy dependency). Returns (magnitude_dB, freq_bins).

    Returns:
        Tuple of (magnitude_db list, freq_bins_hz list) each of length fft_size//2.
    """
    if not iq_samples:
        return [], []

    n = fft_size if fft_size is not None else len(iq_samples)
    if n < 2:
        return [], []

    samples = iq_samples[:n]
    if len(samples) < n:
        samples = samples + [complex(0, 0)] * (n - len(samples))

    freq_bins = [i * sample_rate_hz / n for i in range(n // 2)]
    magnitude_db: list[float] = []

    for k in range(n // 2):
        real = 0.0
        imag = 0.0
        for t, sample in enumerate(samples):
            angle = -2.0 * math.pi * k * t / n
            real += sample.real * math.cos(angle) - sample.imag * math.sin(angle)
            imag += sample.real * math.sin(angle) + sample.imag * math.cos(angle)
        power = (real * real + imag * imag) / (n * n)
        db = 10.0 * math.log10(max(power, 1e-20))
        magnitude_db.append(db)

    return magnitude_db, freq_bins


def compute_noise_floor(
    magnitude_db: list[float],
    percentile: float = 25.0,
) -> float:
    """Estimate noise floor as a given percentile of magnitude values.

    Lower percentile values (e.g. 10-30) avoid contamination from signal bins.
    """
    if not magnitude_db:
        return 0.0
    if len(magnitude_db) == 1:
        return magnitude_db[0]
    sorted_vals = sorted(magnitude_db)
    idx = int(percentile / 100.0 * (len(sorted_vals) - 1))
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


def detect_signals(
    magnitude_db: list[float],
    freq_bins: list[float],
    noise_floor_db: float,
    threshold_db: float = 6.0,
) -> list[dict[str, Any]]:
    """Detect signals above noise floor + threshold.

    Adjacent bins above threshold are grouped into single signal entries.
    Returns list of signal dicts with freq_hz, power_dbm, bandwidth_hz, snr_db.
    """
    if not magnitude_db or not freq_bins or len(magnitude_db) != len(freq_bins):
        return []

    threshold = noise_floor_db + threshold_db
    above_idx = [i for i, mag in enumerate(magnitude_db) if mag >= threshold]

    if not above_idx:
        return []

    groups: list[list[int]] = []
    current: list[int] = [above_idx[0]]
    for i in range(1, len(above_idx)):
        if above_idx[i] == above_idx[i - 1] + 1:
            current.append(above_idx[i])
        else:
            groups.append(current)
            current = [above_idx[i]]
    groups.append(current)

    signals: list[dict[str, Any]] = []
    for group in groups:
        group_freqs = [freq_bins[i] for i in group]
        group_mags = [magnitude_db[i] for i in group]
        center_freq = estimate_center_freq(group_freqs)
        bw = estimate_bandwidth(group, estimate_freq_resolution(freq_bins))
        if bw == 0:
            bw = estimate_freq_resolution(freq_bins)
        max_power = max(group_mags)
        snr = estimate_snr(max_power, noise_floor_db)
        signals.append(
            {
                "freq_hz": center_freq,
                "power_dbm": max_power,
                "bandwidth_hz": bw,
                "snr_db": snr,
            }
        )

    return signals


def estimate_snr(signal_power_dbm: float, noise_floor_dbm: float) -> float:
    """Compute signal-to-noise ratio in dB."""
    return signal_power_dbm - noise_floor_dbm


def estimate_bandwidth(bin_indices: list[int], freq_resolution_hz: float) -> int:
    """Estimate occupied bandwidth from contiguous bin indices."""
    if not bin_indices:
        return 0
    return len(bin_indices) * int(freq_resolution_hz)


def estimate_center_freq(freq_bins: list[float]) -> int:
    """Estimate center frequency from a list of frequency bin values."""
    if not freq_bins:
        return 0
    if len(freq_bins) == 1:
        return int(freq_bins[0])
    low = freq_bins[0]
    high = freq_bins[-1]
    return int((low + high) / 2)


def estimate_freq_resolution(freq_bins: list[float]) -> float:
    """Estimate frequency resolution from adjacent bin spacing."""
    if len(freq_bins) < 2:
        return 1.0
    gaps = [freq_bins[i + 1] - freq_bins[i] for i in range(len(freq_bins) - 1)]
    return statistics.median(gaps)


def normalize_power(powers: list[float]) -> list[float]:
    """Normalize power values to [0, 1] range."""
    if not powers:
        return []
    if len(powers) == 1:
        return [0.0]
    p_min = min(powers)
    p_max = max(powers)
    if p_max == p_min:
        return [0.0] * len(powers)
    return [(p - p_min) / (p_max - p_min) for p in powers]


class SignalAnalyzer:
    """Orchestrates IQ sample analysis: FFT → noise floor → signal detection.

    Usage::

        cfg = AnalyzerConfig(sample_rate_hz=2_000_000, fft_size=1024)
        analyzer = SignalAnalyzer(cfg)
        result = analyzer.analyze(iq_samples)
        for sig in result["detected_signals"]:
            print(f"{sig['freq_hz']} Hz @ {sig['power_dbm']} dBm")
    """

    def __init__(self, config: AnalyzerConfig | None = None) -> None:
        self.config = config or AnalyzerConfig()

    def analyze(self, iq_samples: list[complex]) -> dict[str, Any]:
        """Run the full analysis pipeline on IQ samples.

        Returns:
            Dict with fft_magnitude_db, freq_bins, noise_floor_dbm,
            detected_signals, and config.
        """
        cfg = self.config
        mag_db, freq_bins = compute_fft_magnitude(iq_samples, cfg.sample_rate_hz, cfg.fft_size)
        noise_floor = compute_noise_floor(mag_db, cfg.noise_percentile)
        signals = detect_signals(mag_db, freq_bins, noise_floor, cfg.detection_threshold_db)

        return {
            "fft_magnitude_db": mag_db,
            "freq_bins": freq_bins,
            "noise_floor_dbm": noise_floor,
            "detected_signals": signals,
        }

    def to_dict(self, result: dict[str, Any]) -> dict[str, Any]:
        """Convert analyzer result to JSON-serializable dict."""
        return {
            "fft_magnitude_db": result.get("fft_magnitude_db", []),
            "freq_bins": result.get("freq_bins", []),
            "noise_floor_dbm": result.get("noise_floor_dbm"),
            "detected_signals": list(result.get("detected_signals", [])),
            "config": self.config.to_dict(),
        }


__all__ = [
    "AnalyzerConfig",
    "SignalAnalyzer",
    "compute_fft_magnitude",
    "compute_noise_floor",
    "detect_signals",
    "estimate_bandwidth",
    "estimate_center_freq",
    "estimate_snr",
    "normalize_power",
]
