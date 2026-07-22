"""Spectroscopy role helpers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SpectroscopyConfig:
    technique: str = "uv_vis"
    wavelength_range_nm: tuple[float, float] = (200.0, 800.0)
    resolution_nm: float = 1.0
    solvent: str = "water"
    temperature_C: float = 25.0
    peak_detection_threshold: float = 0.1
    peaks: list[dict[str, float]] | None = None


def simulate_spectrum(config: SpectroscopyConfig) -> dict[str, Any]:
    wl_min, wl_max = config.wavelength_range_nm
    if wl_max <= wl_min:
        raise ValueError("wavelength range must be increasing")
    if config.resolution_nm <= 0:
        raise ValueError("resolution_nm must be positive")
    peaks = config.peaks or [
        {"center_nm": 280.0, "amplitude": 1.0, "sigma_nm": 5.0},
        {"center_nm": 380.0, "amplitude": 0.5, "sigma_nm": 8.0},
        {"center_nm": 550.0, "amplitude": 0.3, "sigma_nm": 10.0},
    ]
    n_points = max(2, int((wl_max - wl_min) / config.resolution_nm) + 1)
    wavelengths = [wl_min + idx * config.resolution_nm for idx in range(n_points)]
    intensities: list[float] = []
    for wl in wavelengths:
        value = 0.0
        for peak in peaks:
            center = float(peak["center_nm"])
            amp = float(peak["amplitude"])
            sigma = max(float(peak["sigma_nm"]), 1.0e-9)
            value += amp * math.exp(-0.5 * ((wl - center) / sigma) ** 2)
        intensities.append(round(value, 8))
    return {
        "config": asdict(config),
        "wavelengths_nm": wavelengths,
        "intensities": intensities,
        "peaks": peaks,
    }


def find_peaks(
    wavelengths_nm: list[float],
    intensities: list[float],
    threshold: float,
) -> list[dict[str, float]]:
    detected: list[dict[str, float]] = []
    for idx in range(1, len(intensities) - 1):
        current = intensities[idx]
        if current < threshold:
            continue
        if current >= intensities[idx - 1] and current >= intensities[idx + 1]:
            detected.append({"wavelength_nm": wavelengths_nm[idx], "intensity": current})
    return detected


def write_spectroscopy_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "spectroscopy_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out
