"""Bounded, fail-closed materials tolerance analysis.

The collection owns the canonical implementation used by its controller action
and by the core compatibility surface.  Evaluation is pure: it opens no files,
contacts no service, starts no process, and retains no state.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

STATE_OK = "ok"
STATE_FAIL_CLOSED = "fail_closed"
MAX_DIMENSION_PAIRS = 256
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_UNIT_CHARS = 32
TOLERANCE_OPERATIONS = frozenset(
    {
        "assembly",
        "process_capability",
        "rss",
        "thermal",
        "thermal_compensation",
        "worst_case",
    }
)

_ACTION_ARGS = frozenset({"operation", "request"})
_CHAIN_ARGS = frozenset({"dims", "unit"})
_THERMAL_ARGS = _CHAIN_ARGS | {"alpha_per_K", "delta_T_K"}
_PROCESS_ARGS = frozenset({"mean", "sigma", "spec_lower", "spec_upper"})
_ASSEMBLY_ARGS = frozenset(
    {"hole_nominal", "hole_tol", "shaft_nominal", "shaft_tol", "unit"}
)


class ToleranceModelError(ValueError):
    """Raised when a tolerance request cannot be evaluated safely."""

    def as_result(self) -> dict[str, object]:
        """Return a stable Ansible failure without echoing request content."""
        return {"changed": False, "failed": True, "msg": str(self)}


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToleranceModelError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ToleranceModelError(f"{field} must be a finite number")
    return number


def _unit(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ToleranceModelError("unit must be a non-empty string without padding")
    if len(value) > MAX_UNIT_CHARS:
        raise ToleranceModelError(
            f"unit must be at most {MAX_UNIT_CHARS} characters"
        )
    return value


def _payload_size(
    value: object,
    field: str,
    *,
    allow_nonfinite: bool = False,
) -> int:
    try:
        encoded = json.dumps(
            value,
            allow_nan=allow_nonfinite,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToleranceModelError(f"{field} must be strict JSON") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ToleranceModelError(
            f"{field} exceeds the {MAX_PAYLOAD_BYTES}-byte limit"
        )
    return len(encoded)


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ToleranceModelError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ToleranceModelError(f"{field} keys must be strings")
    result = dict(value)
    _payload_size(result, field, allow_nonfinite=True)
    return result


def _exact_fields(request: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unsupported = set(request) - allowed
    if {"correlation", "correlations", "covariance", "covariances"} & unsupported:
        raise ToleranceModelError(
            "covariance and correlation inputs are unsupported; RSS requires "
            "explicitly independent contributors"
        )
    if unsupported:
        raise ToleranceModelError("tolerance request contains unsupported fields")
    missing = allowed - set(request)
    if missing - {"mean"}:
        raise ToleranceModelError("tolerance request is missing required fields")


def _dimensions(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        raise ToleranceModelError("dims must be a list")
    if len(value) > MAX_DIMENSION_PAIRS:
        raise ToleranceModelError(
            f"dims must contain at most {MAX_DIMENSION_PAIRS} pairs"
        )
    dimensions: list[tuple[float, float]] = []
    for index, pair in enumerate(value):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ToleranceModelError(
                f"dims[{index}] must contain nominal and tolerance"
            )
        dimensions.append(
            (
                _finite_number(pair[0], f"dims[{index}].nominal"),
                _finite_number(pair[1], f"dims[{index}].tolerance"),
            )
        )
    return dimensions


def _assert_finite_output(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ToleranceModelError("tolerance analysis produced a non-finite output")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite_output(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite_output(item)


class ToleranceChain:
    """A linear chain of nominal dimensions and bilateral tolerances."""

    equation_id_worst = "worst-case: band = sum(|t_i|)"
    equation_id_rss = "rss: band = sqrt(sum(t_i^2)) (1-sigma statistical)"
    equation_id_thermal = "thermal: dL = alpha * L0 * dT"

    def __init__(self, dims: list[tuple[float, float]], unit: str) -> None:
        """Build a detached tolerance chain in one declared unit."""
        if unit is None or not str(unit).strip():
            raise ValueError("unit must be a non-empty string")
        self.dims = list(dims)
        self.unit = unit

    def _dimension_inputs(self) -> dict[str, object]:
        """Render the detached dimensional evidence shared by stack methods."""
        return {
            "dims": [
                {
                    "nominal": dimension[0],
                    "tolerance": dimension[1],
                    "unit": self.unit,
                }
                for dimension in self.dims
            ],
            "unit": self.unit,
        }

    def _stack_result(
        self,
        *,
        band_key: str,
        band: float,
        equation_id: str,
        assumptions: list[str],
    ) -> dict[str, Any]:
        """Build one traceable stack result without duplicating its envelope."""
        nominal = sum((dimension[0] for dimension in self.dims), 0.0)
        return {
            "state": STATE_OK if self.dims else STATE_FAIL_CLOSED,
            **({"reason": "empty tolerance chain"} if not self.dims else {}),
            "nominal": nominal,
            band_key: band,
            "upper": nominal + band,
            "lower": nominal - band,
            "unit": self.unit,
            "equation_id": equation_id,
            "inputs": self._dimension_inputs(),
            "assumptions": assumptions if self.dims else [],
        }

    def worst_case_stackup(self) -> dict[str, Any]:
        """Return the simultaneous-extremes dimensional envelope."""
        band = sum((abs(dimension[1]) for dimension in self.dims), 0.0)
        return self._stack_result(
            band_key="band",
            band=band,
            equation_id=self.equation_id_worst,
            assumptions=[
                "all contributors at their worst-case extreme simultaneously",
                "linear (one-dimensional) chain",
                "bilateral ± tolerances",
            ],
        )

    def rss_stackup(self) -> dict[str, Any]:
        """Return a one-sigma RSS band for independent contributors."""
        sigma_band = math.sqrt(
            sum(dimension[1] ** 2 for dimension in self.dims)
        )
        return self._stack_result(
            band_key="sigma_band",
            band=sigma_band,
            equation_id=self.equation_id_rss,
            assumptions=[
                "independent contributors (no correlation)",
                "normally distributed, ±1-sigma coverage",
                "process is in statistical control",
            ],
        )

    def thermal_expansion_delta(
        self,
        alpha_per_K: float,
        delta_T_K: float,
    ) -> dict[str, Any]:
        """Return free thermal expansion for the nominal chain length."""
        nominal = sum(dimension[0] for dimension in self.dims)
        delta = alpha_per_K * nominal * delta_T_K
        inputs: dict[str, Any] = {
            "alpha": {"value": alpha_per_K, "unit": "1/K"},
            "delta_T": {"value": delta_T_K, "unit": "K"},
            "L0": {"value": nominal, "unit": self.unit},
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

    def thermal_compensation(
        self,
        alpha_per_K: float,
        delta_T_K: float,
    ) -> dict[str, Any]:
        """Return room-temperature compensation for service growth."""
        growth = self.thermal_expansion_delta(alpha_per_K, delta_T_K)
        return {
            "state": STATE_OK,
            "compensation": -growth["delta"],
            "unit": self.unit,
            "equation_id": "thermal: compensation = -alpha * L0 * dT",
            "inputs": growth["inputs"],
            "assumptions": growth["assumptions"]
            + ["room-temperature inspection; part operates at delta_T"],
        }


def process_capability(
    spec_lower: float,
    spec_upper: float,
    sigma: float,
    mean: float | None = None,
) -> dict[str, Any]:
    """Return Cp and Cpk, or a fail-closed result for invalid bounds."""
    equation_id = (
        "cp/cpk: (USL-LSL)/(6*sigma), "
        "min((USL-mu),(mu-LSL))/(3*sigma)"
    )
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
    effective_mean = (
        (spec_upper + spec_lower) / 2.0 if mean is None else mean
    )
    cpk = min(
        (spec_upper - effective_mean) / (3.0 * sigma),
        (effective_mean - spec_lower) / (3.0 * sigma),
    )
    return {
        "state": STATE_OK,
        "Cp": cp,
        "Cpk": cpk,
        "equation_id": equation_id,
        "inputs": {**inputs, "effective_mean": effective_mean},
        "assumptions": [
            "process is in statistical control",
            "output is approximately normally distributed",
            "sigma is the within-subgroup short-term standard deviation",
        ],
    }


def assess_assembly(
    hole_nominal: float,
    hole_tol: float,
    shaft_nominal: float,
    shaft_tol: float,
    unit: str,
) -> dict[str, Any]:
    """Return the worst-case bilateral hole/shaft fit envelope."""
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
        "equation_id": (
            "assembly: clearance = hole - shaft (worst-case envelope)"
        ),
        "inputs": {
            "hole": {"nominal": hole_nominal, "tol": hole_tol, "unit": unit},
            "shaft": {
                "nominal": shaft_nominal,
                "tol": shaft_tol,
                "unit": unit,
            },
        },
        "assumptions": [
            "worst-case envelope (max material / least material combination)",
            "bilateral ± tolerances on both members",
            "cylindrical hole/shaft pair, coaxial",
        ],
    }


def action_arguments(args: object) -> tuple[str, dict[str, Any]]:
    """Select exactly the controller action's bounded public arguments."""
    selected = _mapping(args, "action arguments")
    if set(selected) != _ACTION_ARGS:
        raise ToleranceModelError("tolerance action requires operation and request")
    operation = selected["operation"]
    if not isinstance(operation, str) or operation not in TOLERANCE_OPERATIONS:
        raise ToleranceModelError("unsupported tolerance operation")
    return operation, _mapping(selected["request"], "request")


def evaluate_tolerance_model(
    operation: object,
    request: object,
) -> dict[str, Any]:
    """Validate and execute exactly one of the six pure analyses."""
    if not isinstance(operation, str) or operation not in TOLERANCE_OPERATIONS:
        raise ToleranceModelError("unsupported tolerance operation")
    selected = _mapping(request, "request")

    if operation in {"worst_case", "rss"}:
        _exact_fields(selected, _CHAIN_ARGS)
        chain = ToleranceChain(_dimensions(selected["dims"]), _unit(selected["unit"]))
        result = (
            chain.worst_case_stackup()
            if operation == "worst_case"
            else chain.rss_stackup()
        )
    elif operation in {"thermal", "thermal_compensation"}:
        _exact_fields(selected, _THERMAL_ARGS)
        chain = ToleranceChain(_dimensions(selected["dims"]), _unit(selected["unit"]))
        alpha = _finite_number(selected["alpha_per_K"], "alpha_per_K")
        delta_t = _finite_number(selected["delta_T_K"], "delta_T_K")
        result = (
            chain.thermal_expansion_delta(alpha, delta_t)
            if operation == "thermal"
            else chain.thermal_compensation(alpha, delta_t)
        )
    elif operation == "process_capability":
        _exact_fields(selected, _PROCESS_ARGS)
        raw_mean = selected.get("mean")
        mean = None if raw_mean is None else _finite_number(raw_mean, "mean")
        result = process_capability(
            _finite_number(selected["spec_lower"], "spec_lower"),
            _finite_number(selected["spec_upper"], "spec_upper"),
            _finite_number(selected["sigma"], "sigma"),
            mean,
        )
    else:
        _exact_fields(selected, _ASSEMBLY_ARGS)
        result = assess_assembly(
            _finite_number(selected["hole_nominal"], "hole_nominal"),
            _finite_number(selected["hole_tol"], "hole_tol"),
            _finite_number(selected["shaft_nominal"], "shaft_nominal"),
            _finite_number(selected["shaft_tol"], "shaft_tol"),
            _unit(selected["unit"]),
        )

    _assert_finite_output(result)
    _payload_size(result, "result")
    return result


__all__ = [
    "MAX_DIMENSION_PAIRS",
    "MAX_PAYLOAD_BYTES",
    "MAX_UNIT_CHARS",
    "STATE_FAIL_CLOSED",
    "STATE_OK",
    "TOLERANCE_OPERATIONS",
    "ToleranceChain",
    "ToleranceModelError",
    "action_arguments",
    "assess_assembly",
    "evaluate_tolerance_model",
    "process_capability",
]
