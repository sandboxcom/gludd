"""Particle experiment role helpers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


_CHANNEL_FACTORS = {
    "H_to_ZZ_to_4l": 1.0,
    "H_to_gamma_gamma": 1.8,
    "ttbar": 8.5,
}


@dataclass(frozen=True)
class ParticleConfig:
    beam_energy_GeV: float = 13.6
    target: str = "proton"
    beam: str = "proton"
    detector: str = "generic_4pi"
    luminosity_inv_fb: float = 139.0
    analysis_channel: str = "H_to_ZZ_to_4l"


def compute_cross_section(config: ParticleConfig) -> dict[str, float | str | dict[str, object]]:
    if config.beam_energy_GeV <= 0:
        raise ValueError("beam_energy_GeV must be positive")
    if config.luminosity_inv_fb < 0:
        raise ValueError("luminosity_inv_fb must be non-negative")
    channel_factor = _CHANNEL_FACTORS.get(config.analysis_channel, 1.0)
    energy_factor = math.log1p(config.beam_energy_GeV)
    detector_factor = 1.05 if config.detector in {"atlas", "cms"} else 1.0
    cross_section_pb = round(0.0125 * energy_factor * channel_factor * detector_factor, 6)
    expected_events = round(cross_section_pb * config.luminosity_inv_fb * 1000.0, 3)
    return {
        "config": asdict(config),
        "cross_section_pb": cross_section_pb,
        "expected_events": expected_events,
        "channel": config.analysis_channel,
    }


def analyze_decay_chain(
    decay_particle: str,
    decay_lifetime_s: float,
    branching_ratios: dict[str, float],
) -> dict[str, object]:
    if decay_lifetime_s <= 0:
        raise ValueError("decay_lifetime_s must be positive")
    total_br = sum(branching_ratios.values()) if branching_ratios else 1.0
    width_ev = 6.582119569e-16 / decay_lifetime_s
    return {
        "particle": decay_particle,
        "lifetime_s": decay_lifetime_s,
        "width_eV": width_ev,
        "branching_ratios": branching_ratios,
        "branching_sum": total_br,
    }


def write_particle_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "particle_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out
