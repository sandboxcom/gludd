"""Compatibility helpers for particle experiment CLI workflows."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ParticleConfig:
    beam_energy_GeV: float
    target: str
    beam: str
    detector: str
    luminosity_inv_fb: float
    analysis_channel: str


def compute_cross_section(config: ParticleConfig) -> dict[str, Any]:
    channel_factor = max(len(config.analysis_channel), 1) / 100.0
    cross_section_pb = round(max(config.beam_energy_GeV, 0.0) * channel_factor, 6)
    expected_events = int(cross_section_pb * max(config.luminosity_inv_fb, 0.0) * 1000)
    return {
        "config": asdict(config),
        "cross_section_pb": cross_section_pb,
        "expected_events": expected_events,
    }


def analyze_decay_chain(
    particle: str,
    lifetime_s: float,
    branching_ratios: dict[str, float] | None = None,
) -> dict[str, Any]:
    ratios = branching_ratios or {}
    total_branching = round(sum(float(v) for v in ratios.values()), 6)
    width_hz = math.inf if lifetime_s <= 0 else 1.0 / lifetime_s
    return {
        "particle": particle,
        "lifetime_s": lifetime_s,
        "width_hz": width_hz,
        "branching_ratios": ratios,
        "total_branching": total_branching,
    }


def write_particle_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "particle_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
