"""Spectrum scanner engine for sweep orchestration, peak detection,
signal classification, and band occupancy analysis.

Core operations:
    - Frequency sweep simulation (configurable step, dwell, noise floor)
    - Peak detection with SNR threshold
    - Signal classification (modulation guess from bandwidth/frequency)
    - Band occupancy statistics (occupied Hz, peak count, utilization %)
    - Sweep result merging with deduplication
    - Signal lookup by frequency with tolerance

The SpectrumScanner class orchestrates: configure → sweep → classify → occupancy.
"""

from __future__ import annotations

import dataclasses
import math
import time
from typing import Any


@dataclasses.dataclass
class ScannerConfig:
    """Configuration for the spectrum scanner.

    Attributes:
        freq_start_hz: Start of sweep range in Hz.
        freq_end_hz: End of sweep range in Hz.
        step_hz: Frequency step size in Hz.
        dwell_ms: Dwell time per step in milliseconds.
        detection_threshold_db: Minimum SNR above noise floor to detect.
        noise_floor_dbm: Estimated or measured noise floor in dBm.
    """

    freq_start_hz: int = 1_000_000
    freq_end_hz: int = 30_000_000
    step_hz: int = 100_000
    dwell_ms: int = 10
    detection_threshold_db: float = 6.0
    noise_floor_dbm: float = -110.0

    def __post_init__(self) -> None:
        if self.freq_start_hz >= self.freq_end_hz:
            raise ValueError(f"freq_start_hz ({self.freq_start_hz}) must be less than freq_end_hz ({self.freq_end_hz})")
        if self.step_hz <= 0:
            raise ValueError(f"step_hz must be positive, got {self.step_hz}")

    @property
    def total_steps(self) -> int:
        return math.ceil((self.freq_end_hz - self.freq_start_hz) / self.step_hz)

    @property
    def total_sweep_time_ms(self) -> float:
        return self.total_steps * self.dwell_ms

    def freq_at_step(self, step: int) -> int:
        return self.freq_start_hz + step * self.step_hz

    def to_dict(self) -> dict[str, Any]:
        return {
            "freq_start_hz": self.freq_start_hz,
            "freq_end_hz": self.freq_end_hz,
            "step_hz": self.step_hz,
            "total_steps": self.total_steps,
            "dwell_ms": self.dwell_ms,
            "total_sweep_time_ms": self.total_sweep_time_ms,
            "detection_threshold_db": self.detection_threshold_db,
            "noise_floor_dbm": self.noise_floor_dbm,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScannerConfig:
        return cls(
            freq_start_hz=d.get("freq_start_hz", 1_000_000),
            freq_end_hz=d.get("freq_end_hz", 30_000_000),
            step_hz=d.get("step_hz", 100_000),
            dwell_ms=d.get("dwell_ms", 10),
            detection_threshold_db=d.get("detection_threshold_db", 6.0),
            noise_floor_dbm=d.get("noise_floor_dbm", -110.0),
        )


@dataclasses.dataclass
class SweepResult:
    """Result of a spectrum sweep.

    Attributes:
        freq_start_hz: Start of sweep range.
        freq_end_hz: End of sweep range.
        step_hz: Frequency step used.
        noise_floor_dbm: Noise floor during the sweep.
        scan_timestamp: Unix epoch when the sweep completed.
    """

    freq_start_hz: int | None = None
    freq_end_hz: int | None = None
    step_hz: int | None = None
    noise_floor_dbm: float | None = None
    scan_timestamp: float | None = None
    peaks: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    @property
    def total_peaks(self) -> int:
        return len(self.peaks)

    @property
    def strongest_peak(self) -> dict[str, Any] | None:
        if not self.peaks:
            return None
        return max(self.peaks, key=lambda p: p.get("power_dbm", float("-inf")))

    def add_peak(
        self,
        freq_hz: int,
        power_dbm: float,
        bandwidth_hz: int,
        snr_db: float | None = None,
        modulation_guess: str | None = None,
    ) -> None:
        peak: dict[str, Any] = {
            "freq_hz": freq_hz,
            "power_dbm": power_dbm,
            "bandwidth_hz": bandwidth_hz,
        }
        if snr_db is not None:
            peak["snr_db"] = snr_db
        if modulation_guess is not None:
            peak["modulation_guess"] = modulation_guess
        self.peaks.append(peak)

    def to_dict(self) -> dict[str, Any]:
        return {
            "freq_start_hz": self.freq_start_hz,
            "freq_end_hz": self.freq_end_hz,
            "step_hz": self.step_hz,
            "noise_floor_dbm": self.noise_floor_dbm,
            "scan_timestamp": self.scan_timestamp,
            "total_peaks": self.total_peaks,
            "peaks": [dict(p) for p in self.peaks],
        }


def simulate_spectrum_sweep(
    config: ScannerConfig,
    injected_signals: list[dict[str, Any]] | None = None,
) -> SweepResult:
    """Simulate a spectrum sweep with optional injected signals.

    For each frequency step, checks if any injected signal falls within the
    step bin and exceeds the detection threshold. Builds a SweepResult with
    detected peaks. Uses a simple power comparison (no actual FFT).

    Args:
        config: Scanner configuration.
        injected_signals: List of dicts with freq_hz, power_dbm, bandwidth_hz.

    Returns:
        SweepResult with detected peaks.
    """
    result = SweepResult(
        freq_start_hz=config.freq_start_hz,
        freq_end_hz=config.freq_end_hz,
        step_hz=config.step_hz,
        noise_floor_dbm=config.noise_floor_dbm,
        scan_timestamp=time.time(),
    )

    signals = injected_signals or []
    threshold = config.noise_floor_dbm + config.detection_threshold_db

    for step in range(config.total_steps):
        center = config.freq_at_step(step)
        half_step = config.step_hz // 2
        step_start = center - half_step
        step_end = center + half_step

        for sig in signals:
            sig_center = sig.get("freq_hz", 0)
            if step_start <= sig_center <= step_end and sig.get("power_dbm", -200) >= threshold:
                snr = sig.get("power_dbm", -200) - config.noise_floor_dbm
                result.add_peak(
                    freq_hz=sig_center,
                    power_dbm=sig.get("power_dbm", -200),
                    bandwidth_hz=sig.get("bandwidth_hz", config.step_hz),
                    snr_db=snr,
                    modulation_guess=sig.get("modulation_guess"),
                )

    return result


def classify_peaks(
    peaks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify detected peaks with best-guess modulation types.

    Uses bandwidth and frequency heuristics:
      - <500 Hz → CW
      - 500-3,000 Hz → SSB
      - 3,000-8,000 Hz → NBFM / digital voice
      - 8,000-16,000 Hz → FM / DMR / YSF
      - 16,000-100,000 Hz → Wideband FM / data
      - >100,000 Hz → Broadband (TV, LTE, etc.)

    If a peak already has a modulation_guess, it is preserved.
    """
    result: list[dict[str, Any]] = []
    for peak in peaks:
        classified = dict(peak)
        if classified.get("modulation_guess") is None:
            bw = classified.get("bandwidth_hz", 0)
            freq = classified.get("freq_hz", 0)

            if bw < 500:
                classified["modulation_guess"] = "CW"
            elif bw < 3_000:
                classified["modulation_guess"] = "SSB" if freq < 30_000_000 else "NFM"
            elif bw < 8_000:
                classified["modulation_guess"] = "NBFM"
            elif bw < 16_000:
                classified["modulation_guess"] = "FM"
            elif bw < 100_000:
                classified["modulation_guess"] = "WBFM"
            else:
                classified["modulation_guess"] = "BROADBAND"

        result.append(classified)
    return result


def compute_band_occupancy(
    start_hz: int,
    end_hz: int,
    peaks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute band occupancy statistics from detected peaks.

    Returns dict with total_hz, occupied_hz, occupancy_pct, num_peaks.
    Occupied bandwidth is the sum of individual peak bandwidths (without
    overlap correction — this is a simple occupancy estimator, not a
    full de-confliction).
    """
    total_hz = end_hz - start_hz
    occupied_hz = 0
    in_band = 0
    for peak in peaks:
        freq = peak.get("freq_hz", 0)
        if start_hz <= freq <= end_hz:
            in_band += 1
            bw = peak.get("bandwidth_hz", 0)
            if bw <= total_hz:
                occupied_hz += bw
            else:
                occupied_hz += total_hz

    occupancy_pct = (occupied_hz / total_hz * 100.0) if total_hz > 0 else 0.0
    return {
        "start_hz": start_hz,
        "end_hz": end_hz,
        "total_hz": total_hz,
        "occupied_hz": occupied_hz,
        "occupancy_pct": round(occupancy_pct, 2),
        "num_peaks": in_band,
    }


def find_peak_by_freq(
    peaks: list[dict[str, Any]],
    target_freq_hz: int,
    tolerance_hz: int = 10_000,
) -> dict[str, Any] | None:
    """Find the peak closest to a target frequency within tolerance."""
    if not peaks:
        return None
    best = None
    best_dist = float("inf")
    for peak in peaks:
        dist = abs(peak.get("freq_hz", 0) - target_freq_hz)
        if dist <= tolerance_hz and dist < best_dist:
            best = peak
            best_dist = dist
    return best


def merge_sweep_results(
    sweeps: list[SweepResult],
    dedup_tolerance_hz: int = 1_000,
) -> SweepResult:
    """Merge multiple sweep results into one, deduplicating overlapping peaks.

    Two peaks within dedup_tolerance_hz are considered the same signal;
    the stronger one is kept.
    """
    if not sweeps:
        return SweepResult()

    merged = SweepResult(
        freq_start_hz=sweeps[0].freq_start_hz,
        freq_end_hz=sweeps[-1].freq_end_hz,
        step_hz=sweeps[0].step_hz,
        scan_timestamp=time.time(),
    )

    all_peaks: list[dict[str, Any]] = []
    for sweep in sweeps:
        all_peaks.extend(sweep.peaks)

    deduped: list[dict[str, Any]] = []
    for peak in all_peaks:
        freq = peak.get("freq_hz", 0)
        existing = find_peak_by_freq(deduped, freq, dedup_tolerance_hz)
        if existing is not None:
            if peak.get("power_dbm", -200) > existing.get("power_dbm", -200):
                existing.update(peak)
        else:
            deduped.append(dict(peak))

    merged.peaks = deduped
    return merged


class SpectrumScanner:
    """Orchestrates spectrum scanning: sweep, classify, analyze occupancy.

    Usage::

        cfg = ScannerConfig(freq_start_hz=144_000_000, freq_end_hz=148_000_000, step_hz=25_000)
        scanner = SpectrumScanner(cfg)
        sweep = scanner.sweep()
        classified = scanner.classify(sweep)
        occ = scanner.occupancy(sweep, 144_000_000, 148_000_000)
    """

    def __init__(self, config: ScannerConfig | None = None) -> None:
        self.config = config or ScannerConfig()

    def sweep(
        self,
        injected_signals: list[dict[str, Any]] | None = None,
    ) -> SweepResult:
        """Run a spectrum sweep (simulated).

        In a real SDR-backed deployment, this would drive the hardware.
        For testing and simulation, injected_signals are used.
        """
        return simulate_spectrum_sweep(self.config, injected_signals)

    def classify(self, sweep: SweepResult) -> list[dict[str, Any]]:
        """Classify modulation types for all peaks in a sweep."""
        return classify_peaks(sweep.peaks)

    def occupancy(
        self,
        sweep: SweepResult,
        band_start_hz: int,
        band_end_hz: int,
    ) -> dict[str, Any]:
        """Compute band occupancy from a sweep."""
        return compute_band_occupancy(band_start_hz, band_end_hz, sweep.peaks)

    def to_dict(self, sweep: SweepResult) -> dict[str, Any]:
        """Serialize scanner config and sweep result for JSON export."""
        return {
            "config": self.config.to_dict(),
            **sweep.to_dict(),
        }


__all__ = [
    "ScannerConfig",
    "SpectrumScanner",
    "SweepResult",
    "classify_peaks",
    "compute_band_occupancy",
    "find_peak_by_freq",
    "merge_sweep_results",
    "simulate_spectrum_sweep",
]
