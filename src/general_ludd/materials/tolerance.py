"""Tolerance modeling for spec MATE-001 section 3 (``tolerance_model`` role) and section 4.7.

Provides:

  - :class:`ToleranceChain` — linear dimensional-chain analysis with both
    worst-case (linear) and statistical (RSS, root-sum-of-squares) stack-up,
    plus thermal expansion delta and dimensional compensation.
  - :func:`process_capability` — Cp / Cpk indices from spec limits and process
    sigma. Cp assumes a centered process; Cpk accounts for mean shift.
  - :func:`assess_assembly` — clearance / interference classification between a
    hole and shaft given their bilateral tolerances.

Every result is a verdict dict mirroring :mod:`general_ludd.materials.strength`:
``equation_id`` + ``inputs`` (with units) + ``assumptions`` + explicit ``state``
so downstream code can satisfy MATE-DEC-004 (calculation traceability) and
MATE-SAFE-003 (no fabricated precision). Missing or degenerate inputs return
``state = "fail_closed"`` rather than a silently-wrong number.
"""

from __future__ import annotations

import math
from typing import Any

STATE_OK = "ok"
STATE_FAIL_CLOSED = "fail_closed"


# ---------------------------------------------------------------------------
# ToleranceChain
# ---------------------------------------------------------------------------


class ToleranceChain:
    """A linear dimensional chain of (nominal, bilateral_tolerance) pairs.

    ``dims[i] = (nominal_i, t_i)`` means dimension ``i`` is ``nominal_i ± t_i``
    in the given ``unit``. All dimensions share the same unit; callers convert
    beforehand via :mod:`general_ludd.materials.units`.
    """

    equation_id_worst = "worst-case: band = sum(|t_i|)"
    equation_id_rss = "rss: band = sqrt(sum(t_i^2)) (1-sigma statistical)"
    equation_id_thermal = "thermal: dL = alpha * L0 * dT"

    def __init__(self, dims: list[tuple[float, float]], unit: str) -> None:
        """Build a tolerance chain from (nominal, tolerance) pairs and a unit."""
        if unit is None or not str(unit).strip():
            raise ValueError("unit must be a non-empty string")
        self.dims: list[tuple[float, float]] = list(dims)
        self.unit: str = unit

    def worst_case_stackup(self) -> dict[str, Any]:
        """Linear sum: nominal_total ± sum(|t_i|).

        Every contributor is assumed at its extreme simultaneously. This is the
        conservative bound; real assemblies are usually much tighter (see
        :meth:`rss_stackup`).
        """
        if not self.dims:
            return {
                "state": STATE_FAIL_CLOSED,
                "reason": "empty tolerance chain",
                "nominal": 0.0,
                "band": 0.0,
                "upper": 0.0,
                "lower": 0.0,
                "unit": self.unit,
                "equation_id": self.equation_id_worst,
                "inputs": {"dims": [], "unit": self.unit},
                "assumptions": [],
            }
        nominal = sum(d[0] for d in self.dims)
        band = sum(abs(d[1]) for d in self.dims)
        return {
            "state": STATE_OK,
            "nominal": nominal,
            "band": band,
            "upper": nominal + band,
            "lower": nominal - band,
            "unit": self.unit,
            "equation_id": self.equation_id_worst,
            "inputs": {
                "dims": [{"nominal": d[0], "tolerance": d[1], "unit": self.unit} for d in self.dims],
                "unit": self.unit,
            },
            "assumptions": [
                "all contributors at their worst-case extreme simultaneously",
                "linear (one-dimensional) chain",
                "bilateral ± tolerances",
            ],
        }

    def rss_stackup(self) -> dict[str, Any]:
        """Statistical stack-up assuming independent, normally-distributed contributors.

        The returned ``upper``/``lower`` are ±1-sigma around the nominal. For
        a ~99.73% band multiply ``sigma_band`` by 3; we keep it at 1-sigma to
        avoid fabricating a coverage factor the caller did not specify.
        """
        if not self.dims:
            return {
                "state": STATE_FAIL_CLOSED,
                "reason": "empty tolerance chain",
                "nominal": 0.0,
                "sigma_band": 0.0,
                "upper": 0.0,
                "lower": 0.0,
                "unit": self.unit,
                "equation_id": self.equation_id_rss,
                "inputs": {"dims": [], "unit": self.unit},
                "assumptions": [],
            }
        nominal = sum(d[0] for d in self.dims)
        sigma_band = math.sqrt(sum((d[1]) ** 2 for d in self.dims))
        return {
            "state": STATE_OK,
            "nominal": nominal,
            "sigma_band": sigma_band,
            "upper": nominal + sigma_band,
            "lower": nominal - sigma_band,
            "unit": self.unit,
            "equation_id": self.equation_id_rss,
            "inputs": {
                "dims": [{"nominal": d[0], "tolerance": d[1], "unit": self.unit} for d in self.dims],
                "unit": self.unit,
            },
            "assumptions": [
                "independent contributors (no correlation)",
                "normally distributed, ±1-sigma coverage",
                "process is in statistical control",
            ],
        }

    def thermal_expansion_delta(self, alpha_per_K: float, delta_T_K: float) -> dict[str, Any]:
        """Free thermal expansion: ``dL = alpha * L0 * dT`` summed over the chain.

        ``L0`` is the total nominal length. A positive ``delta_T_K`` (heating)
        produces a positive ``delta`` (growth). Negative ``delta_T_K`` produces
        contraction.
        """
        L0 = sum(d[0] for d in self.dims)
        delta = alpha_per_K * L0 * delta_T_K
        inputs: dict[str, Any] = {
            "alpha": {"value": alpha_per_K, "unit": "1/K"},
            "delta_T": {"value": delta_T_K, "unit": "K"},
            "L0": {"value": L0, "unit": self.unit},
            "computed_delta": {"value": delta, "unit": self.unit},
        }
        return {
            "state": STATE_OK,
            "delta": delta,
            "unit": self.unit,
            "equation_id": self.equation_id_thermal,
            "inputs": inputs,
            "assumptions": [
                "free expansion (unconstrained)",
                "alpha constant over delta_T range",
                "uniform temperature change through body",
            ],
        }

    def thermal_compensation(self, alpha_per_K: float, delta_T_K: float) -> dict[str, Any]:
        """Dimensional compensation so a part fits at the service temperature.

        If a part will grow by ``+delta`` when heated, it must be manufactured
        ``-delta`` shorter at room temperature to net out at zero. The returned
        ``compensation`` is the negative of :meth:`thermal_expansion_delta`.
        """
        growth = self.thermal_expansion_delta(alpha_per_K, delta_T_K)
        return {
            "state": STATE_OK,
            "compensation": -growth["delta"],
            "unit": self.unit,
            "equation_id": "thermal: compensation = -alpha * L0 * dT",
            "inputs": growth["inputs"],
            "assumptions": growth["assumptions"]
            + [
                "room-temperature inspection; part operates at delta_T",
            ],
        }


# ---------------------------------------------------------------------------
# Process capability
# ---------------------------------------------------------------------------


def process_capability(
    spec_lower: float,
    spec_upper: float,
    sigma: float,
    mean: float | None = None,
) -> dict[str, Any]:
    """Cp and Cpk indices for a process.

    - ``Cp = (USL - LSL) / (6 * sigma)`` — potential capability, assumes a
      centered process.
    - ``Cpk = min((USL - mean) / (3*sigma), (mean - LSL) / (3*sigma))`` — actual
      capability accounting for mean shift. If ``mean`` is omitted it is assumed
      centered at ``(USL + LSL) / 2`` so ``Cpk == Cp``.

    Returns ``state = "fail_closed"`` when sigma is non-positive or the spec
    limits are reversed (per MATE-SAFE-006: never fabricate a number from an
    invalid input).
    """
    equation_id = "cp/cpk: (USL-LSL)/(6*sigma), min((USL-mu),(mu-LSL))/(3*sigma)"
    inputs: dict[str, Any] = {
        "spec_lower": spec_lower,
        "spec_upper": spec_upper,
        "sigma": sigma,
        "mean": mean,
    }

    if not math.isfinite(sigma) or sigma <= 0:
        return {
            "state": STATE_FAIL_CLOSED,
            "reason": "sigma must be a positive finite number",
            "Cp": None,
            "Cpk": None,
            "equation_id": equation_id,
            "inputs": inputs,
            "assumptions": [],
        }

    if spec_upper <= spec_lower:
        return {
            "state": STATE_FAIL_CLOSED,
            "reason": "spec_upper must exceed spec_lower",
            "Cp": None,
            "Cpk": None,
            "equation_id": equation_id,
            "inputs": inputs,
            "assumptions": [],
        }

    cp = (spec_upper - spec_lower) / (6.0 * sigma)
    mu = (spec_upper + spec_lower) / 2.0 if mean is None else mean
    cpk = min((spec_upper - mu) / (3.0 * sigma), (mu - spec_lower) / (3.0 * sigma))

    return {
        "state": STATE_OK,
        "Cp": cp,
        "Cpk": cpk,
        "equation_id": equation_id,
        "inputs": {**inputs, "effective_mean": mu},
        "assumptions": [
            "process is in statistical control",
            "output is approximately normally distributed",
            "sigma is the within-subgroup short-term standard deviation",
        ],
    }


# ---------------------------------------------------------------------------
# Assembly clearance / interference
# ---------------------------------------------------------------------------


def assess_assembly(
    hole_nominal: float,
    hole_tol: float,
    shaft_nominal: float,
    shaft_tol: float,
    unit: str,
) -> dict[str, Any]:
    """Worst-case clearance between a hole and a shaft given bilateral tolerances on each.

    Clearance = hole_dimension - shaft_dimension. Positive = clearance fit;
    negative = interference fit. ``min_clearance`` uses the smallest hole with
    the largest shaft; ``max_clearance`` uses the largest hole with the smallest
    shaft (the worst-case bounds).
    """
    equation_id = "assembly: clearance = hole - shaft (worst-case envelope)"
    hole_min = hole_nominal - abs(hole_tol)
    hole_max = hole_nominal + abs(hole_tol)
    shaft_min = shaft_nominal - abs(shaft_tol)
    shaft_max = shaft_nominal + abs(shaft_tol)
    min_clearance = round(hole_min - shaft_max, 12)
    max_clearance = round(hole_max - shaft_min, 12)

    if min_clearance > 0:
        fit_class = "clearance"
    elif max_clearance < 0:
        fit_class = "interference"
    else:
        fit_class = "transition"

    return {
        "state": STATE_OK,
        "fit_class": fit_class,
        "min_clearance": min_clearance,
        "max_clearance": max_clearance,
        "unit": unit,
        "equation_id": equation_id,
        "inputs": {
            "hole": {"nominal": hole_nominal, "tol": hole_tol, "unit": unit},
            "shaft": {"nominal": shaft_nominal, "tol": shaft_tol, "unit": unit},
        },
        "assumptions": [
            "worst-case envelope (max material / least material combination)",
            "bilateral ± tolerances on both members",
            "cylindrical hole/shaft pair, coaxial",
        ],
    }


__all__ = [
    "STATE_FAIL_CLOSED",
    "STATE_OK",
    "ToleranceChain",
    "assess_assembly",
    "process_capability",
]
