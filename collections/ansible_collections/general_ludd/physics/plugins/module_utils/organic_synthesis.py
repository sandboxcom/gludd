"""Compatibility helpers for organic synthesis CLI workflows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SynthesisConfig:
    target_molecule: str
    starting_material: str
    solvent: str
    catalyst: str
    temperature_C: float
    reaction_time_min: float


def lookup_molecule(name: str) -> dict[str, Any]:
    catalog = {
        "aspirin": {"formula": "C9H8O4", "iupac": "acetylsalicylic acid"},
        "paracetamol": {"formula": "C8H9NO2", "iupac": "acetaminophen"},
    }
    return {"name": name, **catalog.get(name, {"formula": "unknown"})}


def predict_yield(config: SynthesisConfig) -> dict[str, Any]:
    base = 72.0
    if config.catalyst:
        base += 8.0
    if 40.0 <= config.temperature_C <= 100.0:
        base += 5.0
    if config.reaction_time_min < 10.0:
        base -= 10.0
    return {"config": asdict(config), "adjusted_yield_pct": round(max(0.0, min(base, 98.0)), 2)}


def retrosynthesis_analysis(target_molecule: str) -> dict[str, Any]:
    return {"target": target_molecule, "steps": ["identify functional groups", "choose available precursor"]}


def write_synthesis_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "synthesis_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
