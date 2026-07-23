"""Compatibility helpers for math-model CLI workflows."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MathModelConfig:
    model_type: str
    equation: str
    initial_conditions: dict[str, float]
    parameters: dict[str, float]
    time_range: tuple[float, float]
    time_steps: int


def solve_ode_exponential_decay(config: MathModelConfig) -> dict[str, Any]:
    start, end = config.time_range
    steps = max(config.time_steps, 1)
    y0 = float(config.initial_conditions.get("y0", 1.0))
    k = float(config.parameters.get("k", 1.0))
    times = [start + (end - start) * i / steps for i in range(steps + 1)]
    y_values = [y0 * math.exp(-k * (t - start)) for t in times]
    return {
        "config": asdict(config),
        "times": times,
        "y_values": y_values,
        "half_life": math.log(2.0) / k if k > 0 else math.inf,
    }


def solve_linear_regression(x_values: list[float], y_values: list[float]) -> dict[str, float]:
    n = len(x_values)
    if n == 0 or n != len(y_values):
        return {"slope": 0.0, "intercept": 0.0}
    xbar = sum(x_values) / n
    ybar = sum(y_values) / n
    denom = sum((x - xbar) ** 2 for x in x_values)
    slope = 0.0 if denom == 0 else sum((x - xbar) * (y - ybar) for x, y in zip(x_values, y_values)) / denom
    return {"slope": slope, "intercept": ybar - slope * xbar}


def compute_statistics(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0}
    return {"mean": sum(values) / len(values), "min": min(values), "max": max(values)}


def write_math_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "math_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
