"""Compatibility helpers for the physics CLI quantum subcommand."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QuantumConfig:
    problem: str
    well_width_nm: float
    particle: str
    potential: str
    dimensions: int
    num_states: int
    solver: str


def solve_schrodinger(config: QuantumConfig) -> dict[str, Any]:
    width_nm = max(float(config.well_width_nm), 1e-9)
    scale = 0.376 / (width_nm * width_nm)
    energies = [scale * (n * n) for n in range(1, max(config.num_states, 1) + 1)]
    return {
        "config": asdict(config),
        "energies_eV": energies,
        "solver": config.solver,
        "normalization": math.sqrt(sum(1.0 / e for e in energies)),
    }


def write_quantum_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "quantum_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
