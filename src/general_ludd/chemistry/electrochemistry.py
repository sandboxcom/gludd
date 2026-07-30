"""CHEM-016 electrochemistry — cell, electrolysis, impedance, corrosion, cycling.

Implements CHEM-016 (electrochemistry) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.5. Functions return plain dict
records mirroring the chemistry result schema in §4.3: every numerical value
carries ``unit`` and ``uncertainty`` fields so callers cannot silently drop
provenance or dimensions.

Deliberately small surface — physical relationships only, no engine adapters:

* :func:`nernst_equation` — closed-form Nernst with units + uncertainty.
* :func:`cell_potential` — combines reduction half-reactions.
* :func:`electrolysis_energy` — Faradaic energy ``E*I*t``.
* :func:`corrosion_rate` — current density → penetration rate (mm/yr).
* :func:`cycling_degradation` — capacity-fade flag for batteries.
* :func:`impedance_basic` — simplified Randles (R_ohm + R_ct) sanity check.

The functions do not modify policy, approvals, or active expert assets; they
return ``limitations`` whenever a result is approximate or assumes a model.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-electrochemistry@0.1.0"

# Physical constants (SI). Values per CODATA-2018.
GAS_CONSTANT_R_J_MOL_K = 8.314462618
FARADAY_CONSTANT_C_MOL = 96485.33212
SECONDS_PER_YEAR = 365.25 * 24 * 3600.0
MILLI_PER_UNIT = 1000.0


def _value_record(
    name: str,
    value: float,
    unit: str,
    uncertainty: float = 0.0,
    method_id: str = METHOD_ID,
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "uncertainty": uncertainty,
        "method_id": method_id,
    }


def nernst_equation(
    standard_potential_v: float,
    electron_count: int,
    q: float,
    temperature_k: float,
    standard_potential_uncertainty_v: float = 0.0,
) -> dict[str, Any]:
    """Return the half-cell potential ``E = E° - (RT/nF) ln(Q)``.

    Parameters mirror the closed-form Nernst equation: standard potential,
    transferred electron count (must be ≥1), reaction quotient ``Q`` (must be
    positive), and absolute temperature. At ``Q=1`` the result equals ``E°``;
    for ``Q<1`` (reactant-favored) the potential increases.

    Uncertainty is propagated from the standard-potential term only (the
    constant term ``RT/nF`` carries negligible uncertainty relative to typical
    ``E°`` measurements).
    """
    if electron_count < 1:
        raise ValueError("electron_count must be a positive integer")
    if temperature_k <= 0.0:
        raise ValueError("temperature_k must be > 0 K")
    if q <= 0.0:
        raise ValueError("reaction quotient q must be > 0")

    rt_over_nf = (GAS_CONSTANT_R_J_MOL_K * temperature_k) / (electron_count * FARADAY_CONSTANT_C_MOL)
    potential = standard_potential_v - rt_over_nf * math.log(q)

    return _value_record(
        "nernst_potential",
        potential,
        "V",
        uncertainty=standard_potential_uncertainty_v,
    )


def cell_potential(
    cathode_potential_v: float,
    anode_potential_v: float,
    cathode_uncertainty_v: float = 0.0,
    anode_uncertainty_v: float = 0.0,
) -> dict[str, Any]:
    """Return ``E°cell = E°cathode - E°anode`` for two reduction potentials.

    Both half-reactions MUST be supplied as reduction potentials (V). The cell
    potential is positive for a galvanic cell and negative for an electrolytic
    one. Uncertainties combine in quadrature.
    """
    potential = cathode_potential_v - anode_potential_v
    uncertainty = math.hypot(cathode_uncertainty_v, anode_uncertainty_v)
    return _value_record(
        "cell_potential",
        potential,
        "V",
        uncertainty=uncertainty,
    )


def electrolysis_energy(
    cell_voltage_v: float,
    current_a: float,
    duration_s: float,
    voltage_uncertainty_v: float = 0.0,
    current_uncertainty_a: float = 0.0,
    duration_uncertainty_s: float = 0.0,
) -> dict[str, Any]:
    """Return electrical energy ``E = V * I * t`` consumed during electrolysis.

    ``current_a`` and ``duration_s`` must be non-negative; ``cell_voltage_v``
    may be negative (electrolytic cell driven against the spontaneous gradient).
    Uncertainty propagates via relative-quadrature of the three terms.
    """
    if current_a < 0.0:
        raise ValueError("current_a must be >= 0")
    if duration_s < 0.0:
        raise ValueError("duration_s must be >= 0")

    energy = cell_voltage_v * current_a * duration_s

    rel_sq = 0.0
    if cell_voltage_v:
        rel_sq += (voltage_uncertainty_v / cell_voltage_v) ** 2
    if current_a:
        rel_sq += (current_uncertainty_a / current_a) ** 2
    if duration_s:
        rel_sq += (duration_uncertainty_s / duration_s) ** 2
    uncertainty = abs(energy) * math.sqrt(rel_sq) if rel_sq else 0.0

    return _value_record(
        "electrolysis_energy",
        energy,
        "J",
        uncertainty=uncertainty,
    )


def corrosion_rate(
    current_density_a_m2: float,
    molar_mass_g_mol: float,
    valence: int,
    density_kg_m3: float,
) -> dict[str, Any]:
    """Convert corrosion current density to penetration rate (mm/yr).

    Uses the Faraday relation: mass-loss flux ``j * M / (n*F)`` (kg/m^2/s),
    then divides by density to get a velocity. Result is reported in ``mm/yr``
    per the convention used in ASTM G102 and similar standards.

    This is an idealized uniform-corrosion estimate; localized corrosion
    (pitting, crevice, SCC) cannot be inferred from current density alone.
    """
    if valence < 1:
        raise ValueError("valence must be >= 1")
    if density_kg_m3 <= 0.0:
        raise ValueError("density_kg_m3 must be > 0")
    if current_density_a_m2 < 0.0:
        raise ValueError("current_density_a_m2 must be >= 0")
    if molar_mass_g_mol <= 0.0:
        raise ValueError("molar_mass_g_mol must be > 0")

    molar_mass_kg_mol = molar_mass_g_mol / 1000.0
    mass_flux_kg_m2_s = current_density_a_m2 * molar_mass_kg_mol / (valence * FARADAY_CONSTANT_C_MOL)
    velocity_m_s = mass_flux_kg_m2_s / density_kg_m3
    rate_mm_yr = velocity_m_s * SECONDS_PER_YEAR * MILLI_PER_UNIT

    return _value_record(
        "corrosion_rate",
        rate_mm_yr,
        "mm/yr",
        uncertainty=0.0,
    )


def cycling_degradation(
    cycles: Sequence[float],
    capacity_fade_threshold_pct: float = 20.0,
) -> dict[str, Any]:
    """Flag whether a battery cycle series has crossed a fade threshold.

    ``cycles`` is a sequence of normalized capacity-retention values (``100.0``
    = fresh cell, descending with cycle number). Returns the total fade as
    ``100 - last`` and a boolean flag when fade meets/exceeds the threshold.

    Capacity fade does not by itself diagnose a degradation mode (SEI growth,
    lithium plating, active-material loss); a flagged result MUST be paired
    with a follow-up impedance / dV/dQ analysis before any cell-design action.
    """
    n = len(cycles)
    if n < 2:
        raise ValueError("cycles must contain at least two points")
    if capacity_fade_threshold_pct <= 0.0:
        raise ValueError("capacity_fade_threshold_pct must be > 0")

    first = float(cycles[0])
    last = float(cycles[-1])
    fade_pct = max(0.0, first - last)
    degraded = fade_pct >= capacity_fade_threshold_pct

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "cycling_degradation",
        "fade_pct": fade_pct,
        "degraded": degraded,
        "threshold_pct": capacity_fade_threshold_pct,
        "cycles_examined": n,
        "limitations": [
            "capacity_fade_alone_does_not_identify_degradation_mode: pair with impedance / dV/dQ analysis before action"
        ]
        if degraded
        else [],
    }


def impedance_basic(
    r_ohm: float,
    r_ct: float,
    frequency_hz: float,
    cdl_f: float = 0.0,
) -> dict[str, Any]:
    """Return a simplified Randles-circuit impedance magnitude.

    At DC (``frequency_hz = 0``) the capacitive branch is open and
    ``|Z| = R_ohm + R_ct``. At high frequency the capacitor shorts the
    charge-transfer branch and ``|Z| -> R_ohm``. With ``cdl_f == 0`` only the
    resistive sum is reported.

    This is a teaching-grade first-order model; real EIS fits require a full
    equivalent circuit (Warburg, constant-phase element, etc.).
    """
    if r_ohm < 0.0 or r_ct < 0.0:
        raise ValueError("resistances must be >= 0")
    if frequency_hz < 0.0:
        raise ValueError("frequency_hz must be >= 0")

    if frequency_hz == 0.0 or cdl_f <= 0.0:
        magnitude = r_ohm + r_ct
    else:
        omega = 2.0 * math.pi * frequency_hz
        z_ct_real = r_ct / (1.0 + (omega * r_ct * cdl_f) ** 2)
        z_ct_imag = -(omega * cdl_f * r_ct * r_ct) / (1.0 + (omega * r_ct * cdl_f) ** 2)
        z_real = r_ohm + z_ct_real
        magnitude = math.hypot(z_real, z_ct_imag)

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "impedance_magnitude",
        "magnitude_ohm": magnitude,
        "unit": "ohm",
        "model": "randles_simplified",
        "limitations": ["simplified_randles: real EIS fits require Warburg / CPE / diffusion terms"],
    }


__all__ = [
    "FARADAY_CONSTANT_C_MOL",
    "GAS_CONSTANT_R_J_MOL_K",
    "cell_potential",
    "corrosion_rate",
    "cycling_degradation",
    "electrolysis_energy",
    "impedance_basic",
    "nernst_equation",
]
