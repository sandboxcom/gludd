"""Spectroscopy simulation helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


class SpectroscopyConfig:
    def __init__(
        self,
        technique: str = "uv_vis",
        wavelength_range_nm: tuple[float, float] = (200.0, 800.0),
        resolution_nm: float = 1.0,
        solvent: str = "water",
        temperature_C: float = 25.0,
        peak_detection_threshold: float = 0.1,
        peaks: list[dict[str, float]] | None = None,
    ) -> None:
        self.technique = technique
        self.wavelength_range_nm = wavelength_range_nm
        self.resolution_nm = resolution_nm
        self.solvent = solvent
        self.temperature_C = temperature_C
        self.peak_detection_threshold = peak_detection_threshold
        self.peaks = peaks or []


def simulate_spectrum(config: SpectroscopyConfig) -> dict[str, Any]:
    start, end = config.wavelength_range_nm
    step = max(config.resolution_nm, 0.1)
    count = max(int((end - start) / step) + 1, 2)
    wavelengths = [round(start + i * step, 3) for i in range(count)]
    peaks = config.peaks or [{"center_nm": 280.0, "amplitude": 1.0, "sigma_nm": 5.0}]
    intensities: list[float] = []
    for wl in wavelengths:
        value = 0.0
        for peak in peaks:
            center = float(peak.get("center_nm", 280.0))
            amplitude = float(peak.get("amplitude", 1.0))
            sigma = max(float(peak.get("sigma_nm", 5.0)), 0.1)
            value += amplitude * math.exp(-0.5 * ((wl - center) / sigma) ** 2)
        intensities.append(round(value, 6))
    return {"wavelengths_nm": wavelengths, "intensities": intensities, "technique": config.technique}


def find_peaks(wavelengths: list[float], intensities: list[float], threshold: float) -> list[dict[str, float]]:
    peaks: list[dict[str, float]] = []
    for idx, intensity in enumerate(intensities):
        left = intensities[idx - 1] if idx else -1.0
        right = intensities[idx + 1] if idx + 1 < len(intensities) else -1.0
        if intensity >= threshold and intensity >= left and intensity >= right:
            peaks.append({"wavelength_nm": wavelengths[idx], "intensity": intensity})
    return peaks


def write_spectroscopy_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "spectroscopy_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
