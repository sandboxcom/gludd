"""Organic synthesis planning helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MOLECULES = {
    "aspirin": {"name": "aspirin", "formula": "C9H8O4", "class": "salicylate"},
    "paracetamol": {"name": "paracetamol", "formula": "C8H9NO2", "class": "analgesic"},
}


class SynthesisConfig:
    def __init__(
        self,
        target_molecule: str,
        starting_material: str = "",
        solvent: str = "",
        catalyst: str = "",
        temperature_C: float = 25.0,
        reaction_time_min: float = 30.0,
    ) -> None:
        self.target_molecule = target_molecule
        self.starting_material = starting_material
        self.solvent = solvent
        self.catalyst = catalyst
        self.temperature_C = temperature_C
        self.reaction_time_min = reaction_time_min


def lookup_molecule(name: str) -> dict[str, Any]:
    return dict(MOLECULES.get(name.lower(), {"name": name, "formula": "unknown", "class": "unknown"}))


def predict_yield(config: SynthesisConfig) -> dict[str, float | str]:
    base = 72.0
    if config.catalyst:
        base += 8.0
    if 40.0 <= config.temperature_C <= 100.0:
        base += 5.0
    if config.reaction_time_min >= 15.0:
        base += 3.0
    return {"target": config.target_molecule, "adjusted_yield_pct": round(min(base, 95.0), 2)}


def retrosynthesis_analysis(target: str) -> dict[str, Any]:
    return {"target": target, "steps": ["identify functional groups", "select precursor", "choose coupling conditions"]}


def write_synthesis_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "synthesis_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
