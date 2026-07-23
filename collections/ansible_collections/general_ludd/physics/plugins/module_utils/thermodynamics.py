"""Thermodynamics helper calculations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SPECIFIC_HEAT_KJ_KG_K = {"water": 4.184, "iron": 0.449, "aluminum": 0.897}
LATENT_HEAT_KJ_KG = {"water": 2256.0, "iron": 6090.0, "aluminum": 10500.0}


class ThermoConfig:
    def __init__(
        self,
        substance: str = "water",
        mass_kg: float = 1.0,
        initial_temp_C: float = 25.0,
        final_temp_C: float = 100.0,
        pressure_atm: float = 1.0,
    ) -> None:
        self.substance = substance
        self.mass_kg = mass_kg
        self.initial_temp_C = initial_temp_C
        self.final_temp_C = final_temp_C
        self.pressure_atm = pressure_atm


def _cp(config: ThermoConfig) -> float:
    return SPECIFIC_HEAT_KJ_KG_K.get(config.substance.lower(), 1.0)


def compute_heat_transfer(config: ThermoConfig) -> dict[str, float]:
    delta = config.final_temp_C - config.initial_temp_C
    heat = config.mass_kg * _cp(config) * delta
    return {"delta_T_C": delta, "heat_transfer_kJ": round(heat, 6)}


def compute_phase_change(config: ThermoConfig) -> dict[str, Any]:
    latent = LATENT_HEAT_KJ_KG.get(config.substance.lower(), 1000.0)
    return {"substance": config.substance, "latent_heat_kJ": round(config.mass_kg * latent, 6)}


def compute_entropy_change(config: ThermoConfig) -> dict[str, float]:
    t1 = max(config.initial_temp_C + 273.15, 1.0)
    t2 = max(config.final_temp_C + 273.15, 1.0)
    heat_j = compute_heat_transfer(config)["heat_transfer_kJ"] * 1000.0
    avg_t = (t1 + t2) / 2.0
    return {"entropy_change_J_K": round(heat_j / avg_t, 6)}


def write_thermo_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "thermodynamics_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
