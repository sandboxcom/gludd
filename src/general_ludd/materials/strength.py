"""Analytical strength checks (spec MATE-001 section 4.7).

Provides closed-form margin calculations for the primary static and fatigue
failure modes: tension, compression, shear, bending, Euler buckling, thermal
stress, and baseline S-N fatigue. Every result carries the equation id, inputs
with units, assumptions, and uncertainty so downstream code can satisfy
MATE-DEC-004 (calculation traceability) and MATE-SAFE-003 (no fabricated
precision).

The functions accept a *property record* (``capacity_prop``) rather than a
material id so they remain decoupled from the material registry and are
trivially unit-testable with handcalc values.
"""

from __future__ import annotations

import math
from typing import Any

STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_INSUFFICIENT = "insufficient_data"
STATE_FAIL_CLOSED = "fail_closed"

_THERMAL_LINEAR_MODEL_MAX_ABS_DELTA_T_K = 10_000.0


def _extract_capacity(prop: dict[str, Any]) -> float | None:
    """Return the numeric capacity from a property record (handles both
    ``value`` and ``value_or_range`` keys)."""
    v = prop.get("value")
    if v is None:
        v = prop.get("value_or_range")
    # ``bool`` is an ``int`` subclass in Python, but accepting True as 1 MPa
    # would turn a schema/type error into a plausible-looking calculation.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    numeric = float(v)
    if math.isfinite(numeric):
        return numeric
    return None


def _stress_check(
    capacity_prop: dict[str, Any],
    applied_MPa: float,
    failure_mode: str,
    equation_id: str,
    extra_inputs: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    *,
    allow_zero_applied: bool = False,
) -> dict[str, Any]:
    """Core (capacity - applied) / applied margin computation shared by the
    direct-stress checks (tension, compression, shear, bending extreme fiber).

    Returns a verdict dict with margin, state, capacity, applied, unit,
    uncertainty, equation_id, inputs, and assumptions. Returns state
    ``insufficient_data`` when capacity is missing/non-numeric and
    ``fail_closed`` when the applied load is invalid or non-positive. A caller
    may explicitly allow zero for a load case where zero is a valid result
    (for example, zero temperature change).
    """
    capacity = _extract_capacity(capacity_prop)
    unit = capacity_prop.get("unit", "MPa")
    uncertainty = capacity_prop.get("uncertainty", 0.0)

    inputs: dict[str, Any] = {
        "capacity": {
            "value": capacity,
            "unit": unit,
            "uncertainty": uncertainty,
        },
        "applied_stress": {"value": applied_MPa, "unit": "MPa"},
    }
    if extra_inputs:
        inputs.update(extra_inputs)

    base: dict[str, Any] = {
        "failure_mode": failure_mode,
        "equation_id": equation_id,
        "inputs": inputs,
        "assumptions": assumptions or [],
        "unit": unit,
        "uncertainty": uncertainty,
    }

    applied_is_finite = (
        not isinstance(applied_MPa, bool)
        and isinstance(applied_MPa, (int, float))
        and math.isfinite(float(applied_MPa))
    )
    applied_is_admissible = applied_is_finite and (
        applied_MPa > 0 or (allow_zero_applied and applied_MPa == 0)
    )

    # Invalid loading is the stronger safety signal and therefore takes
    # precedence over an unavailable capacity. This preserves fail-closed
    # behavior when more than one input is bad.
    if not applied_is_admissible:
        base.update(
            {
                "margin": None,
                "state": STATE_FAIL_CLOSED,
                "reason": "applied stress must be a finite, non-boolean positive number",
                "capacity": capacity,
                "applied": applied_MPa,
            }
        )
        return base

    if capacity is None or capacity <= 0:
        base.update(
            {
                "margin": None,
                "state": STATE_INSUFFICIENT,
                "reason": "capacity value missing or non-positive",
                "capacity": capacity,
                "applied": applied_MPa,
            }
        )
        return base

    if applied_MPa == 0:
        base.update(
            {
                "margin": None,
                "state": STATE_PASS,
                "reason": "zero applied stress",
                "capacity": capacity,
                "applied": applied_MPa,
            }
        )
        return base

    margin = (capacity - applied_MPa) / applied_MPa
    base.update(
        {
            "margin": margin,
            "state": STATE_PASS if margin > 0 else STATE_FAIL,
            "reason": "",
            "capacity": capacity,
            "applied": applied_MPa,
        }
    )
    return base


# ---------------------------------------------------------------------------
# Direct stress checks
# ---------------------------------------------------------------------------


def check_tension(capacity_prop: dict[str, Any], applied_stress_MPa: float) -> dict[str, Any]:
    """Axial tension margin against yield/ultimate capacity."""
    return _stress_check(
        capacity_prop,
        applied_stress_MPa,
        failure_mode="tensile_yield",
        equation_id="axial: sigma=P/A vs sigma_allowable",
    )


def check_compression(capacity_prop: dict[str, Any], applied_stress_MPa: float) -> dict[str, Any]:
    """Axial compression margin (crushing/crushing mode; see
    :func:`check_buckling_euler` for slender-column stability)."""
    return _stress_check(
        capacity_prop,
        applied_stress_MPa,
        failure_mode="compression_failure",
        equation_id="axial: sigma=P/A vs sigma_compressive",
    )


def check_shear(capacity_prop: dict[str, Any], applied_stress_MPa: float) -> dict[str, Any]:
    """Direct shear margin against shear allowable."""
    return _stress_check(
        capacity_prop,
        applied_stress_MPa,
        failure_mode="shear_failure",
        equation_id="shear: tau=V/A vs tau_allowable",
    )


def check_bending(
    capacity_prop: dict[str, Any],
    applied_moment_Nmm: float,
    c_mm: float,
    I_mm4: float,
) -> dict[str, Any]:
    """Extreme-fiber bending margin: sigma = M*c/I vs yield.

    Units: M in N*mm, c in mm, I in mm^4 → sigma in MPa (= N/mm^2).
    """
    geometry_values = (applied_moment_Nmm, c_mm, I_mm4)
    geometry_is_valid = all(
        not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in geometry_values
    )
    if not geometry_is_valid or I_mm4 <= 0 or c_mm <= 0:
        return _stress_check(
            capacity_prop,
            0.0,
            failure_mode="bending_yield",
            equation_id="bending: sigma=M*c/I",
            extra_inputs={
                "moment": {"value": applied_moment_Nmm, "unit": "N*mm"},
                "distance_c": {"value": c_mm, "unit": "mm"},
                "moment_of_inertia": {"value": I_mm4, "unit": "mm^4"},
            },
            assumptions=["section geometry invalid"],
        )

    sigma_MPa = applied_moment_Nmm * c_mm / I_mm4
    return _stress_check(
        capacity_prop,
        sigma_MPa,
        failure_mode="bending_yield",
        equation_id="bending: sigma=M*c/I",
        extra_inputs={
            "moment": {"value": applied_moment_Nmm, "unit": "N*mm"},
            "distance_c": {"value": c_mm, "unit": "mm"},
            "moment_of_inertia": {"value": I_mm4, "unit": "mm^4"},
            "computed_stress": {"value": sigma_MPa, "unit": "MPa"},
        },
    )


# ---------------------------------------------------------------------------
# Elastic buckling (Euler)
# ---------------------------------------------------------------------------


def check_buckling_euler(
    E_MPa: float,
    I_mm4: float,
    L_mm: float,
    K: float,
    applied_force_N: float,
) -> dict[str, Any]:
    """Euler column buckling margin: P_cr = pi^2 * E * I / (K*L)^2.

    K is the effective-length factor (1.0 pinned-pinned, 0.5 fixed-fixed,
    2.0 cantilever, 0.7 fixed-pinned). Returns margin = (P_cr - P) / P.
    """
    equation_id = "euler: P_cr=pi^2*E*I/(K*L)^2"
    inputs: dict[str, Any] = {
        "E": {"value": E_MPa, "unit": "MPa"},
        "I": {"value": I_mm4, "unit": "mm^4"},
        "L": {"value": L_mm, "unit": "mm"},
        "K": K,
        "applied_force": {"value": applied_force_N, "unit": "N"},
    }

    if E_MPa <= 0 or I_mm4 <= 0 or L_mm <= 0 or K <= 0:
        return {
            "failure_mode": "euler_buckling",
            "margin": None,
            "state": STATE_FAIL_CLOSED,
            "reason": "E, I, L, K must all be positive",
            "capacity": None,
            "applied": applied_force_N,
            "unit": "N",
            "uncertainty": 0.0,
            "equation_id": equation_id,
            "inputs": inputs,
            "assumptions": ["elastic buckling only; no plasticity"],
        }

    p_cr = math.pi**2 * E_MPa * I_mm4 / (K * L_mm) ** 2

    if not isinstance(applied_force_N, (int, float)) or applied_force_N <= 0:
        return {
            "failure_mode": "euler_buckling",
            "margin": None,
            "state": STATE_FAIL_CLOSED,
            "reason": "applied force must be positive",
            "capacity": p_cr,
            "applied": applied_force_N,
            "unit": "N",
            "uncertainty": 0.0,
            "equation_id": equation_id,
            "inputs": inputs,
            "assumptions": ["elastic buckling only; no plasticity"],
        }

    margin = (p_cr - applied_force_N) / applied_force_N
    return {
        "failure_mode": "euler_buckling",
        "margin": margin,
        "state": STATE_PASS if margin > 0 else STATE_FAIL,
        "reason": "",
        "capacity": p_cr,
        "applied": applied_force_N,
        "unit": "N",
        "uncertainty": 0.0,
        "equation_id": equation_id,
        "inputs": inputs,
        "assumptions": [
            "elastic buckling only; no plasticity",
            "prismatic straight column",
            f"effective length factor K={K}",
        ],
    }


# ---------------------------------------------------------------------------
# Thermal stress (fully constrained)
# ---------------------------------------------------------------------------


def check_thermal_stress(
    E_MPa: float,
    alpha_per_K: float,
    delta_T_K: float,
    capacity_prop: dict[str, Any],
) -> dict[str, Any]:
    """Thermal stress margin for a fully constrained member:
    sigma_thermal = E * alpha * delta_T.

    A positive delta_T (heating) produces compressive stress; the magnitude
    is compared against the compressive/yield capacity.
    """
    equation_id = "thermal: sigma=E*alpha*dT (fully constrained)"
    sigma_thermal = E_MPa * alpha_per_K * delta_T_K

    inputs: dict[str, Any] = {
        "E": {"value": E_MPa, "unit": "MPa"},
        "alpha": {"value": alpha_per_K, "unit": "1/K"},
        "delta_T": {"value": delta_T_K, "unit": "K"},
        "computed_thermal_stress": {"value": sigma_thermal, "unit": "MPa"},
    }

    result = _stress_check(
        capacity_prop,
        abs(sigma_thermal),
        failure_mode="thermal_stress",
        equation_id=equation_id,
        extra_inputs=inputs,
        assumptions=[
            "fully constrained (no expansion permitted)",
            "uniform temperature change",
            "mechanical properties constant over delta_T range",
        ],
        allow_zero_applied=True,
    )
    if (
        not isinstance(delta_T_K, bool)
        and isinstance(delta_T_K, (int, float))
        and math.isfinite(float(delta_T_K))
        and abs(delta_T_K) > _THERMAL_LINEAR_MODEL_MAX_ABS_DELTA_T_K
    ):
        result.update(
            {
                "margin": None,
                "state": STATE_FAIL,
                "reason": "temperature change exceeds the constant-property linear model applicability limit",
            }
        )
        result["assumptions"].append(
            f"linear thermal-stress model limited to |delta_T| <= {_THERMAL_LINEAR_MODEL_MAX_ABS_DELTA_T_K:g} K"
        )
    return result


# ---------------------------------------------------------------------------
# Fatigue: baseline S-N (Basquin / Shigley)
# ---------------------------------------------------------------------------


def check_fatigue_sn(
    S_ut_MPa: float,
    applied_amplitude_MPa: float,
    cycles: int,
    S_e_MPa: float | None = None,
) -> dict[str, Any]:
    """Baseline fatigue margin using an S-N curve.

    If ``S_e_MPa`` (endurance limit at 10^6 cycles) is not supplied, it is
    estimated as 0.5 * S_ut (steel baseline per Shigley). The estimate is
    flagged in assumptions and carries wide uncertainty (MATE-SAFE-003).

    For N <= 10^3: allowable = 0.9 * S_ut (low-cycle fatigue cutoff).
    For N >= 10^6: allowable = S_e (endurance limit).
    Between: log-log interpolation (Basquin).
    """
    equation_id = "fatigue: basquin S-N (10^3..10^6 log-log)"
    assumptions: list[str] = []
    uncertainty_fraction = 0.05
    endurance_was_estimated = S_e_MPa is None

    if S_e_MPa is None:
        S_e_MPa = 0.5 * S_ut_MPa
        assumptions.append(f"endurance limit estimated as 0.5*S_ut={S_e_MPa:.1f} MPa (steel baseline)")
        uncertainty_fraction = 0.15

    if cycles <= 1_000:
        allowable = 0.9 * S_ut_MPa
        n_label = "N <= 10^3 (LCF cutoff at 0.9*S_ut)"
    elif not endurance_was_estimated:
        # A supplied endurance value is higher-tier evidence than the generic
        # two-point curve. Apply it conservatively throughout the HCF regime
        # instead of interpolating a value that exceeds the measured limit.
        allowable = S_e_MPa
        n_label = "N > 10^3 (measured endurance limit, conservative HCF regime)"
    elif cycles >= 1_000_000:
        allowable = S_e_MPa
        n_label = "N >= 10^6 (endurance regime)"
    else:
        log_n = math.log10(cycles)
        log_s_hi = math.log10(0.9 * S_ut_MPa)
        log_s_lo = math.log10(S_e_MPa)
        log_s = log_s_hi + (log_n - 3.0) * (log_s_lo - log_s_hi) / (6.0 - 3.0)
        allowable = 10.0**log_s
        n_label = f"N={cycles} (finite-life Basquin interpolation)"

    applied_is_finite = (
        not isinstance(applied_amplitude_MPa, bool)
        and isinstance(applied_amplitude_MPa, (int, float))
        and math.isfinite(float(applied_amplitude_MPa))
    )
    if endurance_was_estimated:
        # Keep the baseline uncertainty tied to the inferred strength while
        # widening it when the decision-driving amplitude is larger.
        applied_basis = float(applied_amplitude_MPa) if applied_is_finite else 0.0
        uncertainty_basis = max(0.5 * allowable, applied_basis)
    else:
        uncertainty_basis = allowable
    uncertainty = uncertainty_basis * uncertainty_fraction

    inputs: dict[str, Any] = {
        "S_ut": {"value": S_ut_MPa, "unit": "MPa"},
        "cycles": cycles,
        "S_e": {
            "value": S_e_MPa,
            "unit": "MPa",
            "source": "measured" if not any("estimated" in a for a in assumptions) else "estimated",
        },
        "applied_amplitude": {"value": applied_amplitude_MPa, "unit": "MPa"},
        "allowable_strength": {"value": allowable, "unit": "MPa"},
    }

    if (
        not applied_is_finite
        or applied_amplitude_MPa <= 0
    ):
        return {
            "failure_mode": "fatigue_failure",
            "margin": None,
            "state": STATE_FAIL_CLOSED,
            "reason": "applied amplitude must be positive",
            "capacity": allowable,
            "applied": applied_amplitude_MPa,
            "unit": "MPa",
            "uncertainty": uncertainty,
            "equation_id": equation_id,
            "inputs": inputs,
            "assumptions": [*assumptions, "fully reversed loading (mean stress = 0)", n_label],
        }

    margin = (allowable - applied_amplitude_MPa) / applied_amplitude_MPa
    return {
        "failure_mode": "fatigue_failure",
        "margin": margin,
        "state": STATE_PASS if margin > 0 else STATE_FAIL,
        "reason": "",
        "capacity": allowable,
        "applied": applied_amplitude_MPa,
        "unit": "MPa",
        "uncertainty": uncertainty,
        "equation_id": equation_id,
        "inputs": inputs,
        "assumptions": [*assumptions, "fully reversed loading (mean stress = 0)", n_label],
    }


__all__ = [
    "STATE_FAIL",
    "STATE_FAIL_CLOSED",
    "STATE_INSUFFICIENT",
    "STATE_PASS",
    "check_bending",
    "check_buckling_euler",
    "check_compression",
    "check_fatigue_sn",
    "check_shear",
    "check_tension",
    "check_thermal_stress",
]
