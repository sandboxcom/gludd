"""CHEM-013 thermodynamics, kinetics, and process models (Phase C).

Implements CHEM-013 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.5.
Each model carries units, conditions, and propagated uncertainty per spec §10
("numerical work: units, significant figures, uncertainty propagation,
conservation, limiting cases, convergence, sensitivity"). Functions:

* ``equilibrium_constant`` — ΔG° = -RT ln K (concentration or pressure basis).
* ``arrhenius_rate`` — k = A·exp(-Ea/RT) with optional Ea uncertainty.
* ``check_phase_stability`` — pick stable phase from triple-point-style bounds.
* ``mass_balance_check`` — verify reactant vs product mass conservation.
* ``energy_balance_check`` — verify closed-system energy conservation.
* ``limiting_reactant`` — lowest moles-per-coefficient species wins.
* ``ideal_gas_law`` — solve PV=nRT for the missing variable.

This module delegates formula parsing and molar mass to
``general_ludd.chemistry.core`` rather than re-implementing them.
"""

from __future__ import annotations

import importlib.util
import math
import os
from types import ModuleType
from typing import Any

_CORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "core.py",
)


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_thermo", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-thermo-kinetics@0.1.0"
GAS_CONSTANT_J_PER_MOL_K = 8.314462618

# Triple-point-style bounds for phase stability. T_melt and T_boil at 1 atm.
# Values are deliberately coarse — sufficient for textbook checks, not for
# engineering-grade EOS work. Unknown substances return degraded.
PHASE_BOUNDS: dict[str, dict[str, float]] = {
    "water": {"t_melt_K": 273.15, "t_boil_K": 373.15},
    "ethanol": {"t_melt_K": 159.0, "t_boil_K": 351.45},
    "methanol": {"t_melt_K": 175.5, "t_boil_K": 337.8},
    "acetone": {"t_melt_K": 178.5, "t_boil_K": 329.4},
    "benzene": {"t_melt_K": 278.7, "t_boil_K": 353.3},
    "ammonia": {"t_melt_K": 195.4, "t_boil_K": 239.8},
    "carbon dioxide": {"t_melt_K": 216.6, "t_boil_K": 194.7},  # sublimes at 1 atm
    "oxygen": {"t_melt_K": 54.36, "t_boil_K": 90.19},
    "nitrogen": {"t_melt_K": 63.15, "t_boil_K": 77.36},
    "hydrogen": {"t_melt_K": 13.99, "t_boil_K": 20.27},
}


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _value_record(
    name: str,
    value: float,
    unit: str,
    uncertainty: float = 0.0,
    **extra: Any,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "name": name,
        "value": value,
        "unit": unit,
        "uncertainty": uncertainty,
        "method_id": METHOD_ID,
    }
    rec.update(extra)
    return rec


# ---------------------------------------------------------------------------
# Equilibrium constant (ΔG° = -RT ln K)
# ---------------------------------------------------------------------------


def equilibrium_constant(
    delta_g_kJ_per_mol: float,
    temperature_K: float,
    basis: str = "concentration",
    delta_g_uncertainty_kJ_per_mol: float = 0.0,
) -> dict[str, Any]:
    """Return K = exp(-ΔG° / RT).

    ``basis`` is one of ``"concentration"`` (Kc) or ``"pressure"`` (Kp). Both are
    dimensionless in the thermodynamic convention; the field is recorded so a
    downstream reader does not conflate them. Raises ``ValueError`` for
    non-positive temperatures.
    """
    if temperature_K <= 0.0:
        raise ValueError(f"temperature must be positive, got {temperature_K}")
    if basis not in {"concentration", "pressure"}:
        raise ValueError(f"basis must be 'concentration' or 'pressure', got {basis!r}")

    rt = GAS_CONSTANT_J_PER_MOL_K * temperature_K
    dg_j = delta_g_kJ_per_mol * 1000.0
    k_value = math.exp(-dg_j / rt)

    unc = 0.0
    if delta_g_uncertainty_kJ_per_mol > 0.0:
        # dK/d(ΔG) = -K/(RT); linear propagation in J/mol space.
        dg_unc_j = delta_g_uncertainty_kJ_per_mol * 1000.0
        unc = abs(k_value * dg_unc_j / rt)

    return _value_record(
        "equilibrium_constant",
        k_value,
        "dimensionless",
        uncertainty=unc,
        basis=basis,
        temperature_K=temperature_K,
    )


# ---------------------------------------------------------------------------
# Arrhenius rate constant
# ---------------------------------------------------------------------------


def arrhenius_rate(
    pre_exponential: float,
    activation_energy_kJ_per_mol: float,
    temperature_K: float,
    activation_uncertainty_kJ_per_mol: float = 0.0,
    unit: str = "1/s",
) -> dict[str, Any]:
    """Return k = A·exp(-Ea / RT) with optional Ea uncertainty."""
    if temperature_K <= 0.0:
        raise ValueError(f"temperature must be positive, got {temperature_K}")
    if pre_exponential < 0.0:
        raise ValueError(f"pre-exponential factor must be non-negative, got {pre_exponential}")

    rt = GAS_CONSTANT_J_PER_MOL_K * temperature_K
    ea_j = activation_energy_kJ_per_mol * 1000.0
    k_value = pre_exponential * math.exp(-ea_j / rt)

    unc = 0.0
    if activation_uncertainty_kJ_per_mol > 0.0:
        ea_unc_j = activation_uncertainty_kJ_per_mol * 1000.0
        # dk/dEa = -A·exp(-Ea/RT) / RT = -k / RT
        unc = abs(k_value * ea_unc_j / rt)

    return _value_record(
        "rate_constant",
        k_value,
        unit,
        uncertainty=unc,
        temperature_K=temperature_K,
    )


# ---------------------------------------------------------------------------
# Phase stability
# ---------------------------------------------------------------------------


def check_phase_stability(
    substance: str,
    temperature_K: float,
    pressure_Pa: float = 101325.0,
) -> dict[str, Any]:
    """Return the stable phase (solid/liquid/gas) at the given (T, P).

    Uses the textbook triple-point bounds in :data:`PHASE_BOUNDS`. The pressure
    is recorded as a condition but only temperature participates in the
    comparison — this is not a Clausius-Clapeyron integration. Unknown
    substances return ``degraded`` with an ``unknown-substance`` limitation.
    """
    bounds = PHASE_BOUNDS.get(substance.lower())
    conditions = [
        {"name": "temperature", "value": temperature_K, "unit": "K"},
        {"name": "pressure", "value": pressure_Pa, "unit": "Pa"},
    ]
    if bounds is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "degraded",
            "stable_phase": None,
            "conditions": conditions,
            "limitations": [f"unknown-substance: no phase bounds registered for {substance!r}"],
            "verification": [
                {"check": "phase_bounds_available", "status": "fail"},
            ],
            "errors": [_err("chem.unknown_substance", f"no phase data for {substance!r}")],
        }

    t_melt = bounds["t_melt_K"]
    t_boil = bounds["t_boil_K"]
    if t_boil < t_melt:
        # Substance sublimes at 1 atm (e.g. CO2): treat as solid below t_boil, gas above.
        stable = "gas" if temperature_K >= t_boil else "solid"
    elif temperature_K < t_melt:
        stable = "solid"
    elif temperature_K < t_boil:
        stable = "liquid"
    else:
        stable = "gas"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "stable_phase": stable,
        "conditions": conditions,
        "limitations": [],
        "verification": [
            {"check": "phase_bounds_available", "status": "pass"},
            {"check": "phase_within_recorded_bounds", "status": "pass"},
        ],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Mass and energy balance
# ---------------------------------------------------------------------------


def mass_balance_check(
    reactants: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify mass conservation across reactants and products.

    Each species dict has ``formula`` and ``moles``. Mass = moles * molar_mass.
    """
    if not reactants and not products:
        return _empty_balance("mass_balance", status="refused", reason="no reactants or products")

    def total_mass(species: list[dict[str, Any]]) -> float:
        total = 0.0
        for sp in species:
            mm = _core.molar_mass(str(sp["formula"]))["value"]
            total += float(sp["moles"]) * mm
        return total

    m_r = total_mass(reactants)
    m_p = total_mass(products)
    # Tolerance: 1e-6 relative or absolute 1e-9 g, whichever is larger.
    tol = max(1e-9, 1e-6 * max(abs(m_r), abs(m_p), 1.0))
    balanced = math.isclose(m_r, m_p, rel_tol=0.0, abs_tol=tol)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded" if balanced else "failed",
        "reactant_mass_g": m_r,
        "product_mass_g": m_p,
        "verification": [
            {
                "check": "mass_balance",
                "status": "pass" if balanced else "fail",
                "delta_g": m_r - m_p,
            },
        ],
        "errors": [] if balanced else [_err("chem.mass_imbalance", "reactant and product masses disagree")],
    }


def energy_balance_check(
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify energy conservation across inputs and outputs of a process.

    Each entry has ``energy_kJ``. Closed systems must sum to zero.
    """
    e_in = sum(float(s.get("energy_kJ", 0.0)) for s in inputs)
    e_out = sum(float(s.get("energy_kJ", 0.0)) for s in outputs)
    tol = max(1e-9, 1e-6 * max(abs(e_in), abs(e_out), 1.0))
    balanced = math.isclose(e_in, e_out, rel_tol=0.0, abs_tol=tol)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded" if balanced else "failed",
        "input_energy_kJ": e_in,
        "output_energy_kJ": e_out,
        "verification": [
            {
                "check": "energy_balance",
                "status": "pass" if balanced else "fail",
                "delta_kJ": e_in - e_out,
            },
        ],
        "errors": [] if balanced else [_err("chem.energy_imbalance", "input and output energies disagree")],
    }


def _empty_balance(check_name: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": status,
        "verification": [{"check": check_name, "status": "fail", "note": reason}],
        "errors": [_err("chem.empty_balance", reason)],
    }


# ---------------------------------------------------------------------------
# Limiting reactant
# ---------------------------------------------------------------------------


def limiting_reactant(reactants: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the species with the smallest ``moles / coefficient`` ratio.

    Each entry has ``formula``, ``moles``, and ``coefficient``. The
    coefficient is the integer stoichiometric coefficient in the balanced
    reaction (e.g. for ``2 H2 + O2 -> ...``, H2 has coefficient 2).
    """
    if not reactants:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": _new_id(),
            "status": "refused",
            "limiting_reactant": None,
            "verification": [{"check": "reactants_present", "status": "fail"}],
            "errors": [_err("chem.no_reactants", "reactants list is empty")],
        }

    best_formula: str | None = None
    best_ratio = math.inf
    ratios: dict[str, float] = {}
    for sp in reactants:
        formula = str(sp["formula"])
        moles = float(sp["moles"])
        coeff = float(sp.get("coefficient", 1))
        if coeff <= 0:
            raise ValueError(f"coefficient must be positive for {formula!r}, got {coeff}")
        ratio = moles / coeff
        ratios[formula] = ratio
        if ratio < best_ratio:
            best_ratio = ratio
            best_formula = formula

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "status": "succeeded",
        "limiting_reactant": best_formula,
        "extent_of_reaction_mol": best_ratio,
        "per_reactant_ratio": ratios,
        "verification": [
            {"check": "reactants_present", "status": "pass"},
            {"check": "limiting_reactant_resolved", "status": "pass"},
        ],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Ideal gas law (PV = nRT)
# ---------------------------------------------------------------------------


def ideal_gas_law(
    pressure_Pa: float | None,
    volume_m3: float | None,
    moles: float,
    temperature_K: float,
) -> dict[str, Any]:
    """Solve ``PV = nRT`` for whichever of pressure or volume is ``None``.

    Currently supports solving for pressure or volume only; both being None or
    both being specified is an error. Returns a value record in the requested
    unit.
    """
    if temperature_K <= 0.0:
        raise ValueError(f"temperature must be positive, got {temperature_K}")
    if moles < 0.0:
        raise ValueError(f"moles must be non-negative, got {moles}")
    if (pressure_Pa is None) == (volume_m3 is None):
        raise ValueError("exactly one of pressure_Pa and volume_m3 must be None")

    nrt = moles * GAS_CONSTANT_J_PER_MOL_K * temperature_K

    if pressure_Pa is None:
        if volume_m3 is None or volume_m3 == 0.0:
            raise ValueError("volume must be non-zero to solve for pressure")
        value = nrt / volume_m3
        return _value_record(
            "pressure",
            value,
            "Pa",
            temperature_K=temperature_K,
            moles=moles,
        )
    if pressure_Pa == 0.0:
        raise ValueError("pressure must be non-zero to solve for volume")
    value = nrt / pressure_Pa
    return _value_record(
        "volume",
        value,
        "m^3",
        temperature_K=temperature_K,
        moles=moles,
    )


__all__ = [
    "GAS_CONSTANT_J_PER_MOL_K",
    "PHASE_BOUNDS",
    "arrhenius_rate",
    "check_phase_stability",
    "energy_balance_check",
    "equilibrium_constant",
    "ideal_gas_law",
    "limiting_reactant",
    "mass_balance_check",
]
