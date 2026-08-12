"""Traceable alloy screening and ranking helpers.

The selector is intentionally small and deterministic.  Its registry contains
nominal handbook-grade values for representative engineering alloys; selection
results label that tier rather than presenting the values as supplier or lot
certificates.  Hard constraints fail closed when an alloy or a required value
is unknown.
"""

from __future__ import annotations

import math
from typing import Any, TypeGuard

_DATA_TIER = "handbook"
_COMPOSITION_TOLERANCE = 0.05


ALLOY_DATA: dict[str, dict[str, Any]] = {
    "aa6061_t6": {
        "family": "aluminum_alloy",
        "base_element": "Al",
        "density": 2.70,
        "cost_index": 1.0,
        "yield_strength_mpa": 276.0,
        "youngs_modulus_gpa": 68.9,
        "ultimate_strength_mpa": 310.0,
        "temp_min_kelvin": None,
        "temp_max_kelvin": 450.0,
        "cryogenic_compatible": True,
        "environment": ["atmospheric", "fresh_water", "marine_limited"],
        "composition": {"Al": 0.974, "Mg": 0.010, "Si": 0.006, "Cu": 0.003, "Cr": 0.003},
        "data_tier": _DATA_TIER,
    },
    "aisi_1045": {
        "family": "carbon_steel",
        "base_element": "Fe",
        "density": 7.85,
        "cost_index": 0.8,
        "yield_strength_mpa": 310.0,
        "youngs_modulus_gpa": 200.0,
        "ultimate_strength_mpa": 565.0,
        "temp_min_kelvin": 200.0,
        "temp_max_kelvin": 600.0,
        "cryogenic_compatible": False,
        "environment": ["atmospheric", "vacuum"],
        "composition": {"Fe": 0.985, "C": 0.0045, "Mn": 0.008, "Si": 0.0025},
        "data_tier": _DATA_TIER,
    },
    "316l_stainless": {
        "family": "stainless_steel",
        "base_element": "Fe",
        "density": 8.0,
        "cost_index": 3.0,
        "yield_strength_mpa": 290.0,
        "youngs_modulus_gpa": 193.0,
        "ultimate_strength_mpa": 558.0,
        "temp_min_kelvin": 4.0,
        "temp_max_kelvin": 870.0,
        "cryogenic_compatible": True,
        "environment": ["marine", "atmospheric", "chemical_acidic", "chemical_basic"],
        "composition": {"Fe": 0.655, "Cr": 0.175, "Ni": 0.120, "Mo": 0.025, "Mn": 0.020, "Si": 0.005},
        "data_tier": _DATA_TIER,
    },
    "inconel_718": {
        "family": "nickel_superalloy",
        "base_element": "Ni",
        "density": 8.19,
        "cost_index": 12.0,
        "yield_strength_mpa": 1034.0,
        "youngs_modulus_gpa": 205.0,
        "ultimate_strength_mpa": 1275.0,
        "temp_min_kelvin": 4.0,
        "temp_max_kelvin": 920.0,
        "cryogenic_compatible": True,
        "environment": ["high_temp_oxidizing", "marine", "chemical_acidic"],
        "composition": {"Ni": 0.525, "Cr": 0.190, "Fe": 0.180, "Nb": 0.051, "Mo": 0.030, "Ti": 0.010, "Al": 0.005},
        "data_tier": _DATA_TIER,
    },
    "ti_6al4v": {
        "family": "titanium_alloy",
        "base_element": "Ti",
        "density": 4.43,
        "cost_index": 8.0,
        "yield_strength_mpa": 880.0,
        "youngs_modulus_gpa": 113.8,
        "ultimate_strength_mpa": 950.0,
        "temp_min_kelvin": 4.0,
        "temp_max_kelvin": 620.0,
        "cryogenic_compatible": True,
        "environment": ["marine", "atmospheric", "chemical_acidic", "aerospace"],
        "composition": {"Ti": 0.895, "Al": 0.060, "V": 0.040, "Fe": 0.005},
        "data_tier": _DATA_TIER,
    },
    "monel_400": {
        "family": "nickel_copper",
        "base_element": "Ni",
        "density": 8.80,
        "cost_index": 7.0,
        "yield_strength_mpa": 240.0,
        "youngs_modulus_gpa": 179.0,
        "ultimate_strength_mpa": 550.0,
        "temp_min_kelvin": 4.0,
        "temp_max_kelvin": 510.0,
        "cryogenic_compatible": True,
        "environment": ["marine", "chemical_acidic", "chemical_basic"],
        "composition": {"Ni": 0.660, "Cu": 0.315, "Fe": 0.015, "Mn": 0.010},
        "data_tier": _DATA_TIER,
    },
    "az31b_mg": {
        "family": "magnesium_alloy",
        "base_element": "Mg",
        "density": 1.77,
        "cost_index": 2.5,
        "yield_strength_mpa": 200.0,
        "youngs_modulus_gpa": 45.0,
        "ultimate_strength_mpa": 260.0,
        "temp_min_kelvin": 200.0,
        "temp_max_kelvin": 370.0,
        "cryogenic_compatible": False,
        "environment": ["atmospheric", "vacuum"],
        "composition": {"Mg": 0.960, "Al": 0.030, "Zn": 0.010},
        "data_tier": _DATA_TIER,
    },
    "hastelloy_c276": {
        "family": "nickel_superalloy",
        "base_element": "Ni",
        "density": 8.89,
        "cost_index": 14.0,
        "yield_strength_mpa": 355.0,
        "youngs_modulus_gpa": 205.0,
        "ultimate_strength_mpa": 790.0,
        "temp_min_kelvin": 4.0,
        "temp_max_kelvin": 830.0,
        "cryogenic_compatible": True,
        "environment": ["marine", "chemical_acidic", "high_temp_oxidizing"],
        "composition": {"Ni": 0.570, "Mo": 0.160, "Cr": 0.155, "Fe": 0.055, "W": 0.040, "Co": 0.020},
        "data_tier": _DATA_TIER,
    },
}


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nan_or_inf(value: object) -> bool:
    """Return whether *value* is a non-finite real number."""
    return _is_number(value) and not math.isfinite(float(value))


def _filter_result(alloy_id: str, compatible: bool, state: str, reason: str = "") -> dict[str, Any]:
    return {
        "alloy_id": alloy_id,
        "compatible": compatible,
        "state": state,
        "reason": reason,
    }


def filter_by_environment(candidates: list[str], environment: str) -> list[dict[str, Any]]:
    """Evaluate candidate compatibility with a named service environment."""
    requested = environment.strip().lower()
    results: list[dict[str, Any]] = []
    for alloy_id in candidates:
        data = ALLOY_DATA.get(alloy_id)
        if data is None:
            results.append(_filter_result(alloy_id, False, "insufficient_data", "unknown alloy"))
            continue
        environments = {str(item).strip().lower() for item in data.get("environment", [])}
        compatible = requested in environments
        results.append(
            _filter_result(
                alloy_id,
                compatible,
                "ok" if compatible else "incompatible_environment",
                "" if compatible else f"environment {requested!r} is not supported",
            )
        )
    return results


def filter_by_temperature(
    candidates: list[str],
    min_kelvin: float,
    max_kelvin: float,
) -> list[dict[str, Any]]:
    """Evaluate whether each alloy covers the complete requested range."""
    valid_numbers = _is_number(min_kelvin) and _is_number(max_kelvin)
    invalid_range = (
        not valid_numbers
        or _is_nan_or_inf(min_kelvin)
        or _is_nan_or_inf(max_kelvin)
        or min_kelvin > max_kelvin
    )
    if invalid_range:
        return [
            _filter_result(alloy_id, False, "invalid_input", "temperature range is invalid")
            for alloy_id in candidates
        ]

    results: list[dict[str, Any]] = []
    for alloy_id in candidates:
        data = ALLOY_DATA.get(alloy_id)
        if data is None:
            results.append(_filter_result(alloy_id, False, "insufficient_data", "unknown alloy"))
            continue
        lower = data.get("temp_min_kelvin")
        upper = data.get("temp_max_kelvin")
        lower_ok = lower is None or (_is_number(lower) and float(lower) <= min_kelvin)
        upper_ok = upper is None or (_is_number(upper) and float(upper) >= max_kelvin)
        compatible = lower_ok and upper_ok
        results.append(
            _filter_result(
                alloy_id,
                compatible,
                "ok" if compatible else "out_of_range",
                "" if compatible else "requested temperature range is outside alloy limits",
            )
        )
    return results


def filter_by_cost_index(candidates: list[str], max_cost_index: float) -> list[dict[str, Any]]:
    """Evaluate candidate cost indices against an inclusive maximum."""
    invalid = (
        not _is_number(max_cost_index)
        or math.isnan(float(max_cost_index))
        or max_cost_index == float("-inf")
        or max_cost_index < 0
    )
    if invalid:
        return [
            _filter_result(alloy_id, False, "invalid_input", "max_cost_index is invalid")
            for alloy_id in candidates
        ]

    results: list[dict[str, Any]] = []
    for alloy_id in candidates:
        data = ALLOY_DATA.get(alloy_id)
        if data is None:
            results.append(_filter_result(alloy_id, False, "insufficient_data", "unknown alloy"))
            continue
        cost = data.get("cost_index")
        if not _is_number(cost) or _is_nan_or_inf(cost) or float(cost) < 0:
            results.append(_filter_result(alloy_id, False, "insufficient_data", "cost_index is unavailable"))
            continue
        compatible = float(cost) <= max_cost_index
        results.append(
            _filter_result(
                alloy_id,
                compatible,
                "ok" if compatible else "over_budget",
                "" if compatible else "cost_index exceeds the requested maximum",
            )
        )
    return results


def alloy_composition_tolerance(alloy_id: str, measured: dict[str, float]) -> dict[str, Any]:
    """Compare measured fractions with the registered composition.

    Only elements present in both mappings are compared, allowing partial
    assays.  The tolerance is five percentage points in absolute mass fraction.
    """
    data = ALLOY_DATA.get(alloy_id)
    if data is None:
        return {
            "alloy_id": alloy_id,
            "within_tolerance": False,
            "state": "insufficient_data",
            "violations": ["unknown alloy"],
        }
    if not measured:
        return {
            "alloy_id": alloy_id,
            "within_tolerance": False,
            "state": "invalid_input",
            "violations": ["measured composition is empty"],
        }

    invalid_values = [
        element
        for element, fraction in measured.items()
        if not _is_number(fraction) or _is_nan_or_inf(fraction) or fraction < 0
    ]
    if invalid_values:
        return {
            "alloy_id": alloy_id,
            "within_tolerance": False,
            "state": "invalid_input",
            "violations": [f"invalid fraction for {element}" for element in invalid_values],
        }

    reference: dict[str, float] = data.get("composition", {})
    recognized_sum = sum(float(value) for element, value in measured.items() if element in reference)
    if recognized_sum > 1.0 + 1e-9:
        return {
            "alloy_id": alloy_id,
            "within_tolerance": False,
            "state": "invalid_input",
            "violations": [f"recognized composition sum exceeds 1.0: {recognized_sum:.6g}"],
        }

    violations = [
        f"{element} differs by {abs(float(fraction) - reference[element]):.6g}"
        for element, fraction in measured.items()
        if element in reference and abs(float(fraction) - reference[element]) > _COMPOSITION_TOLERANCE
    ]
    within_tolerance = not violations
    return {
        "alloy_id": alloy_id,
        "within_tolerance": within_tolerance,
        "state": "ok" if within_tolerance else "out_of_tolerance",
        "violations": violations,
        "tolerance": _COMPOSITION_TOLERANCE,
    }


def _performance_value(data: dict[str, Any], index_type: str) -> float | None:
    density = data.get("density")
    if not _is_number(density) or _is_nan_or_inf(density) or float(density) <= 0:
        return None
    property_name = {
        "specific_strength": "yield_strength_mpa",
        "specific_stiffness": "youngs_modulus_gpa",
    }.get(index_type)
    if property_name is None:
        return None
    value = data.get(property_name)
    if not _is_number(value) or _is_nan_or_inf(value):
        return None
    return float(value) / float(density)


def _criterion_score(data: dict[str, Any], criterion: str) -> float:
    performance = _performance_value(data, criterion)
    if performance is not None:
        return performance
    if criterion == "cost_index":
        cost = data.get("cost_index")
        if _is_number(cost) and math.isfinite(float(cost)) and float(cost) > 0:
            return 1.0 / float(cost)
    if criterion == "temp_range":
        lower = data.get("temp_min_kelvin")
        upper = data.get("temp_max_kelvin")
        if _is_number(lower) and _is_number(upper):
            return max(0.0, float(upper) - float(lower))
    return 0.0


def compare_alloys(candidates: list[str], criteria: list[str]) -> dict[str, Any]:
    """Rank alloys by the sum of requested, traceable criterion scores."""
    rankings: list[dict[str, Any]] = []
    for alloy_id in candidates:
        data = ALLOY_DATA.get(alloy_id)
        if data is None:
            rankings.append(
                {
                    "alloy_id": alloy_id,
                    "state": "insufficient_data",
                    "scores": {criterion: 0.0 for criterion in criteria},
                    "composite_score": None,
                }
            )
            continue
        scores = {criterion: _criterion_score(data, criterion) for criterion in criteria}
        rankings.append(
            {
                "alloy_id": alloy_id,
                "state": "ok",
                "scores": scores,
                "composite_score": sum(scores.values()),
                "data_tier": data.get("data_tier", _DATA_TIER),
            }
        )

    rankings.sort(
        key=lambda entry: float(entry["composite_score"]) if entry["composite_score"] is not None else float("-inf"),
        reverse=True,
    )
    verdict = "candidate" if any(entry["state"] == "ok" for entry in rankings) else "insufficient_data"
    return {"criteria": list(criteria), "rankings": rankings, "verdict": verdict}


def rank_by_performance_index(candidates: list[str], index_type: str) -> dict[str, Any]:
    """Rank alloys by specific strength or specific stiffness."""
    if index_type not in {"specific_strength", "specific_stiffness"}:
        return {"index_type": index_type, "entries": [], "verdict": "invalid_index"}

    entries: list[dict[str, Any]] = []
    for alloy_id in candidates:
        data = ALLOY_DATA.get(alloy_id)
        value = _performance_value(data, index_type) if data is not None else None
        entries.append(
            {
                "alloy_id": alloy_id,
                "index_value": value,
                "state": "ok" if value is not None else "insufficient_data",
                "data_tier": data.get("data_tier", _DATA_TIER) if data is not None else None,
            }
        )
    entries.sort(
        key=lambda entry: float(entry["index_value"]) if entry["index_value"] is not None else float("-inf"),
        reverse=True,
    )
    return {"index_type": index_type, "entries": entries, "verdict": "ranked"}


def _invalid_requirement(requirements: dict[str, Any]) -> str | None:
    minimum_yield = requirements.get("min_yield_mpa")
    if minimum_yield is not None and (
        not _is_number(minimum_yield) or _is_nan_or_inf(minimum_yield) or minimum_yield < 0
    ):
        return "min_yield_mpa must be a finite non-negative number"

    maximum_cost = requirements.get("max_cost_index")
    if maximum_cost is not None and (
        not _is_number(maximum_cost)
        or math.isnan(float(maximum_cost))
        or maximum_cost == float("-inf")
        or maximum_cost < 0
    ):
        return "max_cost_index must be a non-negative number"

    min_temp = requirements.get("min_temp_kelvin")
    max_temp = requirements.get("max_temp_kelvin")
    for name, value in (("min_temp_kelvin", min_temp), ("max_temp_kelvin", max_temp)):
        if value is not None and (not _is_number(value) or _is_nan_or_inf(value)):
            return f"{name} must be finite"
    if min_temp is not None and max_temp is not None and min_temp > max_temp:
        return "temperature range is inverted"
    return None


def _candidate_violations(alloy_id: str, data: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    environment = requirements.get("environment")
    if isinstance(environment, str):
        requested = environment.strip().lower()
        available = {str(item).strip().lower() for item in data.get("environment", [])}
        if requested not in available:
            violations.append(f"environment {requested!r} is not supported")

    if requirements.get("cryogenic_required") is True and data.get("cryogenic_compatible") is not True:
        violations.append("cryogenic service is not supported")

    minimum_yield = requirements.get("min_yield_mpa")
    if minimum_yield is not None:
        strength = data.get("yield_strength_mpa")
        if not _is_number(strength) or float(strength) < float(minimum_yield):
            violations.append("yield strength is below the requested minimum")

    maximum_cost = requirements.get("max_cost_index")
    if maximum_cost is not None:
        cost = data.get("cost_index")
        if not _is_number(cost) or float(cost) > float(maximum_cost):
            violations.append("cost_index exceeds the requested maximum")

    min_temp = requirements.get("min_temp_kelvin")
    max_temp = requirements.get("max_temp_kelvin")
    alloy_min = data.get("temp_min_kelvin")
    alloy_max = data.get("temp_max_kelvin")
    if min_temp is not None and (
        (_is_number(alloy_min) and float(alloy_min) > float(min_temp))
        or (_is_number(alloy_max) and float(alloy_max) < float(min_temp))
    ):
        violations.append("minimum operating temperature is outside alloy limits")
    if max_temp is not None and _is_number(alloy_max) and float(alloy_max) < float(max_temp):
        violations.append("maximum operating temperature is outside alloy limits")
    return violations


def _composite_margin(data: dict[str, Any], requirements: dict[str, Any]) -> float:
    """Return a deterministic best-first score for already-screened alloys."""
    score = _performance_value(data, "specific_strength") or 0.0
    minimum_yield = requirements.get("min_yield_mpa")
    strength = data.get("yield_strength_mpa")
    if _is_number(minimum_yield) and float(minimum_yield) > 0 and _is_number(strength):
        score += (float(strength) - float(minimum_yield)) / float(minimum_yield)
    maximum_cost = requirements.get("max_cost_index")
    cost = data.get("cost_index")
    if (
        _is_number(maximum_cost)
        and math.isfinite(float(maximum_cost))
        and float(maximum_cost) > 0
        and _is_number(cost)
    ):
        score += (float(maximum_cost) - float(cost)) / float(maximum_cost)
    return score


def select_alloy(
    requirements: dict[str, Any],
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Screen alloys against hard requirements and rank surviving candidates."""
    invalid_reason = _invalid_requirement(requirements)
    if invalid_reason is not None:
        return {
            "state": "invalid_input",
            "verdict": "invalid_input",
            "reason": invalid_reason,
            "candidates": [],
        }

    min_temp = requirements.get("min_temp_kelvin")
    if requirements.get("cryogenic_required") is True and _is_number(min_temp) and float(min_temp) > 200.0:
        return {
            "state": "infeasible",
            "verdict": "infeasible",
            "reason": "cryogenic service conflicts with the minimum operating temperature",
            "candidates": [],
        }

    candidate_ids = list(ALLOY_DATA) if candidates is None else list(candidates)
    results: list[dict[str, Any]] = []
    for alloy_id in candidate_ids:
        data = ALLOY_DATA.get(alloy_id)
        if data is None:
            results.append(
                {
                    "alloy_id": alloy_id,
                    "state": "rejected",
                    "reason": "unknown alloy",
                    "violations": ["unknown alloy"],
                }
            )
            continue

        violations = _candidate_violations(alloy_id, data, requirements)
        if violations:
            results.append(
                {
                    "alloy_id": alloy_id,
                    "state": "rejected",
                    "reason": "; ".join(violations),
                    "violations": violations,
                    "data_tier": data.get("data_tier", _DATA_TIER),
                }
            )
            continue
        results.append(
            {
                "alloy_id": alloy_id,
                "state": "survived",
                "reason": "ok",
                "violations": [],
                "data_tier": data.get("data_tier", _DATA_TIER),
                "composite_margin": _composite_margin(data, requirements),
            }
        )

    results.sort(
        key=lambda entry: (
            entry["state"] == "survived",
            float(entry.get("composite_margin", float("-inf"))),
        ),
        reverse=True,
    )
    has_survivor = any(entry["state"] == "survived" for entry in results)
    return {
        "state": "ok" if has_survivor else "infeasible",
        "verdict": "candidate" if has_survivor else "infeasible",
        "candidates": results,
    }


__all__ = [
    "ALLOY_DATA",
    "alloy_composition_tolerance",
    "compare_alloys",
    "filter_by_cost_index",
    "filter_by_environment",
    "filter_by_temperature",
    "rank_by_performance_index",
    "select_alloy",
]
