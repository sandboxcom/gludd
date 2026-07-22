"""Quantum computer role helpers for lightweight Schrodinger solves."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


_PARTICLE_MASS_FACTOR = {
    "electron": 1.0,
    "proton": 1836.152673,
    "neutron": 1838.683661,
}


@dataclass(frozen=True)
class QuantumConfig:
    problem: str = "infinite_square_well"
    well_width_nm: float = 1.0
    particle: str = "electron"
    potential: str = "square_well"
    dimensions: int = 1
    num_states: int = 5
    solver: str = "numpy"


def solve_schrodinger(config: QuantumConfig) -> dict[str, object]:
    """Return deterministic eigenvalues for role and smoke-test runs."""
    if config.well_width_nm <= 0:
        raise ValueError("well_width_nm must be positive")
    if config.num_states < 1:
        raise ValueError("num_states must be at least 1")
    mass_factor = _PARTICLE_MASS_FACTOR.get(config.particle, 1.0)
    base_ev = 0.376030163 / (mass_factor * config.well_width_nm * config.well_width_nm)
    if config.problem == "harmonic_oscillator" or config.potential == "harmonic":
        energies = [base_ev * (idx + 0.5) for idx in range(config.num_states)]
    else:
        energies = [base_ev * (idx + 1) * (idx + 1) for idx in range(config.num_states)]
    return {
        "config": asdict(config),
        "energies_eV": energies,
        "ground_state_eV": energies[0],
        "solver": config.solver,
        "normalization": 1.0,
    }


def write_quantum_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "quantum_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out
