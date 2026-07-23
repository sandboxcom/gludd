"""Mathematical modeling helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


class MathModelConfig:
    def __init__(
        self,
        model_type: str = "ode_first_order",
        equation: str = "dy/dt = -k*y",
        initial_conditions: dict[str, float] | None = None,
        parameters: dict[str, float] | None = None,
        time_range: tuple[float, float] = (0.0, 10.0),
        time_steps: int = 100,
    ) -> None:
        self.model_type = model_type
        self.equation = equation
        self.initial_conditions = initial_conditions or {"y0": 1.0}
        self.parameters = parameters or {"k": 0.5}
        self.time_range = time_range
        self.time_steps = time_steps


def solve_ode_exponential_decay(config: MathModelConfig) -> dict[str, Any]:
    start, end = config.time_range
    steps = max(int(config.time_steps), 2)
    k = float(config.parameters.get("k", 0.5))
    y0 = float(config.initial_conditions.get("y0", 1.0))
    times = [start + (end - start) * i / (steps - 1) for i in range(steps)]
    values = [y0 * math.exp(-k * (t - start)) for t in times]
    return {"t_values": times, "y_values": values, "half_life": math.log(2.0) / k if k > 0 else math.inf}


def compute_statistics(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {"mean": mean(values), "std": pstdev(values), "min": min(values), "max": max(values)}


def solve_linear_regression(points: list[tuple[float, float]]) -> dict[str, float]:
    n = len(points)
    if n == 0:
        return {"slope": 0.0, "intercept": 0.0}
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    slope = 0.0 if denom == 0 else (n * sxy - sx * sy) / denom
    return {"slope": slope, "intercept": (sy - slope * sx) / n}


def write_math_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "math_model_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
