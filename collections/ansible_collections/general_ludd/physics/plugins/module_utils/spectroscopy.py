"""Compatibility helpers for spectroscopy CLI workflows."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SpectroscopyConfig:
    technique: str
    wavelength_range_nm: tuple[float, float]
    resolution_nm: float
    solvent: str
    temperature_C: float
    peak_detection_threshold: float
    peaks: list[dict[str, float]]


def simulate_spectrum(config: SpectroscopyConfig) -> dict[str, Any]:
    start, end = config.wavelength_range_nm
    step = max(config.resolution_nm, 0.1)
    count = max(1, int((end - start) / step) + 1)
    wavelengths = [round(start + i * step, 6) for i in range(count)]
    intensities: list[float] = []
    for wl in wavelengths:
        value = 0.0
        for peak in config.peaks:
            center = float(peak.get("center_nm", wl))
            amp = float(peak.get("amplitude", 1.0))
            sigma = max(float(peak.get("sigma_nm", 1.0)), 0.1)
            value += amp * math.exp(-((wl - center) ** 2) / (2.0 * sigma * sigma))
        intensities.append(round(value, 6))
    return {
        "config": asdict(config),
        "wavelengths_nm": wavelengths,
        "intensities": intensities,
    }


def find_peaks(
    wavelengths_nm: list[float],
    intensities: list[float],
    threshold: float,
) -> list[dict[str, float]]:
    peaks: list[dict[str, float]] = []
    for idx, value in enumerate(intensities):
        left = intensities[idx - 1] if idx > 0 else float("-inf")
        right = intensities[idx + 1] if idx < len(intensities) - 1 else float("-inf")
        if value >= threshold and value >= left and value >= right:
            peaks.append({"wavelength_nm": wavelengths_nm[idx], "intensity": value})
    return peaks


def write_spectroscopy_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "spectroscopy_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
