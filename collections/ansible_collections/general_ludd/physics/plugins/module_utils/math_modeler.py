"""Mathematical modeling role helpers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean


@dataclass(frozen=True)
class MathModelConfig:
    model_type: str = "ode_first_order"
    equation: str = "dy/dt = -k * y"
    initial_conditions: dict[str, float] | None = None
    parameters: dict[str, float] | None = None
    time_range: tuple[float, float] = (0.0, 10.0)
    time_steps: int = 100


def solve_ode_exponential_decay(config: MathModelConfig) -> dict[str, object]:
    if config.time_steps < 2:
        raise ValueError("time_steps must be at least 2")
    t0, t1 = config.time_range
    if t1 <= t0:
        raise ValueError("time_range must be increasing")
    y0 = (config.initial_conditions or {}).get("y0", 1.0)
    rate = (config.parameters or {}).get("k", 0.5)
    dt = (t1 - t0) / (config.time_steps - 1)
    t_values = [t0 + idx * dt for idx in range(config.time_steps)]
    y_values = [y0 * math.exp(-rate * (t - t0)) for t in t_values]
    half_life = math.log(2.0) / rate if rate > 0 else math.inf
    return {
        "config": asdict(config),
        "t_values": t_values,
        "y_values": y_values,
        "half_life": half_life,
    }


def compute_statistics(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("values must be non-empty")
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return {
        "mean": avg,
        "min": min(values),
        "max": max(values),
        "variance": variance,
        "stddev": math.sqrt(variance),
    }


def solve_linear_regression(xs: list[float], ys: list[float]) -> dict[str, float]:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("xs and ys must have matching length at least 2")
    x_avg = mean(xs)
    y_avg = mean(ys)
    denom = sum((x - x_avg) ** 2 for x in xs)
    if denom == 0:
        raise ValueError("xs must not all be equal")
    slope = sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, ys)) / denom
    intercept = y_avg - slope * x_avg
    return {"slope": slope, "intercept": intercept}


def write_math_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "math_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out
