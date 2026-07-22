"""Organic synthesis role helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_MOLECULES: dict[str, dict[str, Any]] = {
    "aspirin": {
        "formula": "C9H8O4",
        "molar_mass_g_mol": 180.16,
        "functional_groups": ["ester", "carboxylic acid"],
    },
    "paracetamol": {
        "formula": "C8H9NO2",
        "molar_mass_g_mol": 151.16,
        "functional_groups": ["amide", "phenol"],
    },
}


@dataclass(frozen=True)
class SynthesisConfig:
    target_molecule: str = "aspirin"
    starting_material: str = "salicylic_acid"
    solvent: str = "acetic_anhydride"
    catalyst: str = "sulfuric_acid"
    temperature_C: float = 85.0
    reaction_time_min: float = 15.0


def lookup_molecule(name: str) -> dict[str, Any]:
    data = _MOLECULES.get(name.lower())
    if data is None:
        return {"name": name, "known": False}
    return {"name": name.lower(), "known": True, **data}


def predict_yield(config: SynthesisConfig) -> dict[str, float | str | dict[str, object]]:
    if config.reaction_time_min <= 0:
        raise ValueError("reaction_time_min must be positive")
    temp_bonus = max(-20.0, min(12.0, (config.temperature_C - 25.0) * 0.12))
    time_bonus = max(0.0, min(8.0, config.reaction_time_min * 0.15))
    catalyst_bonus = 5.0 if config.catalyst else 0.0
    base = 62.0 if config.target_molecule == "aspirin" else 58.0
    adjusted = max(0.0, min(98.0, base + temp_bonus + time_bonus + catalyst_bonus))
    return {
        "config": asdict(config),
        "base_yield_pct": base,
        "adjusted_yield_pct": round(adjusted, 2),
        "limiting_factor": "temperature" if temp_bonus < 0 else "none",
    }


def retrosynthesis_analysis(target_molecule: str) -> dict[str, object]:
    if target_molecule == "aspirin":
        steps = ["salicylic_acid", "acetylation", "aspirin"]
    elif target_molecule == "paracetamol":
        steps = ["4-aminophenol", "acetylation", "paracetamol"]
    else:
        steps = [target_molecule]
    return {"target": target_molecule, "steps": steps, "step_count": len(steps)}


def write_synthesis_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "synthesis_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out
