"""Compatibility quantum solver utilities for the physics collection."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HBAR = 1.054571817e-34
E_CHARGE = 1.602176634e-19
ELECTRON_MASS = 9.1093837015e-31
PARTICLE_MASSES_KG = {
    "electron": ELECTRON_MASS,
    "proton": 1.67262192369e-27,
    "neutron": 1.67492749804e-27,
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


def _particle_mass(name: str) -> float:
    return PARTICLE_MASSES_KG.get(name.lower(), ELECTRON_MASS)


def _square_well_energy_ev(n: int, width_nm: float, mass_kg: float, dimensions: int) -> float:
    width_m = max(width_nm, 1e-12) * 1e-9
    base = (n * n * math.pi * math.pi * HBAR * HBAR) / (2.0 * mass_kg * width_m * width_m)
    return base * max(dimensions, 1) / E_CHARGE


def solve_schrodinger(config: QuantumConfig) -> dict[str, Any]:
    """Return deterministic eigenstate data for supported educational problems."""
    states = max(int(config.num_states), 1)
    mass = _particle_mass(config.particle)
    energies = [
        _square_well_energy_ev(n, config.well_width_nm, mass, config.dimensions)
        for n in range(1, states + 1)
    ]
    if config.problem == "harmonic_oscillator":
        omega = 1.0e15
        energies = [(n + 0.5) * HBAR * omega / E_CHARGE for n in range(states)]

    wavefunctions = [
        {
            "state": index + 1,
            "normalization": "sqrt(2/L)",
            "form": f"sin({index + 1}*pi*x/L)",
        }
        for index in range(states)
    ]
    return {
        "config": asdict(config),
        "energies_eV": energies,
        "wavefunctions": wavefunctions,
        "ground_state_eV": energies[0],
        "solver": config.solver,
    }


def write_quantum_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "quantum_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
