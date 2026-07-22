"""
thermodynamics -- Classical thermodynamics, statistical mechanics, and phase transitions.

Laws of thermodynamics, heat engines, entropy
Statistical mechanics: partition functions, ensembles, Boltzmann distribution
Phase transitions: critical points, order parameters

Functions:
    compute_carnot_efficiency(Th, Tc) -> float
    compute_partition_function(energies, T) -> float
    compute_entropy(probabilities) -> float
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

K_B = 1.380649e-23
N_A = 6.02214076e23
R = 8.314462618

LAWS_OF_THERMODYNAMICS: list[dict[str, str]] = [
    {
        "law": "zeroth",
        "statement": "If A is in thermal equilibrium with C and B is in thermal equilibrium with C, then A and B are in thermal equilibrium with each other.",
        "significance": "Defines temperature as a transitive equivalence relation. Foundation of thermometry.",
    },
    {
        "law": "first",
        "statement": "dU = dQ - dW. The change in internal energy of a closed system equals heat added minus work done by the system.",
        "significance": "Energy conservation. Internal energy U is a state function; Q and W are path functions.",
    },
    {
        "law": "second",
        "statement": "dS >= dQ/T. Entropy of an isolated system never decreases. No process is possible whose sole result is the transfer of heat from a colder to a hotter body (Clausius) or complete conversion of heat into work (Kelvin-Planck).",
        "significance": "Defines the arrow of time. Limits heat engine efficiency. All spontaneous processes increase total entropy.",
    },
    {
        "law": "third",
        "statement": "The entropy of a perfect crystal approaches zero as temperature approaches absolute zero.",
        "significance": "Sets absolute entropy scale. Unattainability of absolute zero in finite steps. Heat capacities vanish as T->0.",
    },
]

HEAT_ENGINE_CYCLES: list[dict[str, Any]] = [
    {
        "cycle": "carnot",
        "type": "theoretical_upper_bound",
        "processes": ["isothermal expansion", "adiabatic expansion", "isothermal compression", "adiabatic compression"],
        "efficiency_formula": "1 - T_cold / T_hot",
        "notes": "Maximum possible efficiency between two reservoirs. Reversible; all other cycles less efficient.",
    },
    {
        "cycle": "otto",
        "type": "IC_engine",
        "processes": ["isentropic compression", "isochoric heat addition", "isentropic expansion", "isochoric heat rejection"],
        "efficiency_formula": "1 - 1 / r^(gamma-1)",
        "typical_efficiency_pct": 25.0,
        "notes": "Four-stroke gasoline engine. Compression ratio r typically 8-12.",
    },
    {
        "cycle": "diesel",
        "type": "IC_engine",
        "processes": ["isentropic compression", "isobaric heat addition", "isentropic expansion", "isochoric heat rejection"],
        "efficiency_formula": "1 - (1/r^(gamma-1)) * (rc^gamma - 1)/(gamma*(rc - 1))",
        "typical_efficiency_pct": 35.0,
        "notes": "Compression-ignition. Higher compression ratio (14-25). Higher efficiency than Otto.",
    },
    {
        "cycle": "brayton",
        "type": "gas_turbine",
        "processes": ["isentropic compression", "isobaric heat addition", "isentropic expansion", "isobaric heat rejection"],
        "efficiency_formula": "1 - 1 / rp^((gamma-1)/gamma)",
        "typical_efficiency_pct": 35.0,
        "notes": "Jet engines and gas turbines. Combined cycle with Rankine bottoming cycle reaches >60%.",
    },
    {
        "cycle": "rankine",
        "type": "steam_turbine",
        "processes": ["isentropic pump compression", "isobaric heat addition (boiler)", "isentropic expansion (turbine)", "isobaric heat rejection (condenser)"],
        "efficiency_formula": "(h3 - h4 - (h2 - h1)) / (h3 - h2)",
        "typical_efficiency_pct": 35.0,
        "notes": "Steam power plants. Supercritical Rankine cycles approach 45-48%.",
    },
    {
        "cycle": "stirling",
        "type": "external_combustion",
        "processes": ["isothermal expansion", "isochoric cooling", "isothermal compression", "isochoric heating"],
        "efficiency_formula": "1 - T_cold / T_hot",
        "typical_efficiency_pct": 30.0,
        "notes": "External heat source. Can approach Carnot efficiency. Quiet, but low power density.",
    },
]

PHASE_TRANSITIONS: list[dict[str, Any]] = [
    {
        "material": "water",
        "transition": "liquid_to_gas",
        "critical_temperature_k": 647.1,
        "critical_pressure_bar": 220.6,
        "critical_density_kg_m3": 322.0,
        "triple_point_k": 273.16,
        "triple_point_pressure_pa": 611.657,
        "order": "first_order",
        "order_parameter": "density difference (liquid - vapor)",
    },
    {
        "material": "water",
        "transition": "solid_to_liquid",
        "melting_point_k": 273.15,
        "latent_heat_kj_kg": 334.0,
        "order": "first_order",
        "order_parameter": "density difference (solid - liquid)",
        "anomaly": "Water expands on freezing (less dense solid). Maximum density at 4 C.",
    },
    {
        "material": "iron_alpha",
        "transition": "ferromagnetic_to_paramagnetic",
        "curie_temperature_k": 1043.0,
        "order": "second_order",
        "order_parameter": "spontaneous magnetization M",
        "critical_exponents": {"beta": 0.327, "gamma": 1.33, "delta": 4.8},
    },
    {
        "material": "helium_4",
        "transition": "normal_to_superfluid",
        "lambda_point_k": 2.17,
        "order": "second_order",
        "order_parameter": "superfluid density",
        "notes": "Lambda transition. Specific heat diverges logarithmically (lambda shape).",
    },
    {
        "material": "carbon_dioxide",
        "transition": "liquid_to_gas",
        "critical_temperature_k": 304.2,
        "critical_pressure_bar": 73.8,
        "critical_density_kg_m3": 468.0,
        "order": "first_order",
    },
    {
        "material": "nitrogen",
        "transition": "liquid_to_gas",
        "critical_temperature_k": 126.2,
        "critical_pressure_bar": 34.0,
        "boiling_point_k": 77.36,
        "order": "first_order",
    },
    {
        "material": "lead_zirconate_titanate",
        "transition": "ferroelectric_to_paraelectric",
        "curie_temperature_k": 620.0,
        "order": "second_order",
        "order_parameter": "spontaneous polarization P",
    },
    {
        "material": "quartz",
        "transition": "alpha_to_beta",
        "transition_temperature_k": 846.0,
        "order": "first_order",
        "notes": "Structural phase transition. Rapid volume change near transition.",
    },
]

ENSEMBLE_TYPES: list[dict[str, Any]] = [
    {
        "ensemble": "microcanonical",
        "abbreviation": "NVE",
        "fixed_quantities": ["particle number N", "volume V", "energy E"],
        "thermodynamic_potential": "S = k_B * ln(W)",
        "partition_function_name": "Omega (number of microstates)",
        "notes": "Isolated system. All accessible microstates equally probable.",
    },
    {
        "ensemble": "canonical",
        "abbreviation": "NVT",
        "fixed_quantities": ["particle number N", "volume V", "temperature T"],
        "thermodynamic_potential": "F = -k_B * T * ln(Z)",
        "partition_function_name": "Z = sum_i exp(-E_i / k_B*T)",
        "notes": "System in contact with heat bath. Most commonly used ensemble.",
    },
    {
        "ensemble": "grand_canonical",
        "abbreviation": "muVT",
        "fixed_quantities": ["chemical potential mu", "volume V", "temperature T"],
        "thermodynamic_potential": "Omega = -k_B * T * ln(Xi)",
        "partition_function_name": "Xi = sum_N sum_i exp((-E_i + mu*N) / k_B*T)",
        "notes": "System with particle exchange. Used for open systems and phase equilibria.",
    },
    {
        "ensemble": "isothermal_isobaric",
        "abbreviation": "NPT",
        "fixed_quantities": ["particle number N", "pressure P", "temperature T"],
        "thermodynamic_potential": "G = -k_B * T * ln(Delta)",
        "partition_function_name": "Delta = sum_i sum_V exp((-E_i - P*V) / k_B*T)",
        "notes": "Constant pressure ensemble. Used for simulations at ambient conditions.",
    },
]


def compute_carnot_efficiency(th_k: float, tc_k: float) -> float:
    """Compute Carnot efficiency: eta = 1 - T_cold / T_hot."""
    if th_k <= 0 or tc_k <= 0:
        raise ValueError("Temperatures must be positive")
    if tc_k >= th_k:
        raise ValueError("Hot reservoir must be hotter than cold reservoir")
    return 1.0 - tc_k / th_k


def compute_partition_function(energies: list[float], t_k: float) -> float:
    """Compute canonical partition function Z = sum_i exp(-E_i / k_B*T)."""
    if t_k <= 0:
        raise ValueError("Temperature must be positive")
    z = 0.0
    for e in energies:
        z += math.exp(-e / (K_B * t_k))
    return z


def compute_entropy(probabilities: list[float]) -> float:
    """Compute Gibbs/Shannon entropy S = -k_B * sum_i p_i * ln(p_i) in J/K.
    If probabilities sum to ~1, returns entropy in J/K. Otherwise returns dimensionless."""
    total = sum(probabilities)
    if abs(total - 1.0) > 1e-12:
        normalized = [p / total for p in probabilities]
    else:
        normalized = list(probabilities)
    entropy_dimensionless = 0.0
    for p in normalized:
        if p > 0:
            entropy_dimensionless -= p * math.log(p)
    return entropy_dimensionless * K_B


def boltzmann_factor(energy: float, t_k: float) -> float:
    """Compute Boltzmann factor exp(-E / k_B * T)."""
    if t_k <= 0:
        raise ValueError("Temperature must be positive")
    return math.exp(-energy / (K_B * t_k))


def boltzmann_distribution(energies: list[float], t_k: float) -> list[float]:
    """Compute Boltzmann probabilities p_i = exp(-E_i/kBT) / Z for a set of energies."""
    z = compute_partition_function(energies, t_k)
    return [math.exp(-e / (K_B * t_k)) / z for e in energies]


def average_energy_canonical(energies: list[float], t_k: float) -> float:
    """Compute <E> = sum_i E_i * p_i where p_i is the Boltzmann distribution."""
    probs = boltzmann_distribution(energies, t_k)
    return sum(e * p for e, p in zip(energies, probs))


def heat_capacity_canonical(energies: list[float], t_k: float) -> float:
    """Compute heat capacity C_V = (<E^2> - <E>^2) / (k_B * T^2) in J/K."""
    probs = boltzmann_distribution(energies, t_k)
    avg_e = sum(e * p for e, p in zip(energies, probs))
    avg_e2 = sum(e * e * p for e, p in zip(energies, probs))
    variance = avg_e2 - avg_e * avg_e
    return variance / (K_B * t_k * t_k)


def free_energy(energies: list[float], t_k: float) -> float:
    """Compute Helmholtz free energy F = -k_B * T * ln(Z)."""
    z = compute_partition_function(energies, t_k)
    return -K_B * t_k * math.log(z)


def ideal_gas_pressure(n_moles: float, t_k: float, volume_m3: float) -> float:
    """Compute ideal gas pressure: P = nRT / V."""
    if volume_m3 <= 0:
        raise ValueError("Volume must be positive")
    return n_moles * R * t_k / volume_m3


def ideal_gas_internal_energy(n_moles: float, t_k: float, degrees_of_freedom: float = 3.0) -> float:
    """Compute ideal gas internal energy: U = (f/2) * n * R * T."""
    return (degrees_of_freedom / 2.0) * n_moles * R * t_k


def van_der_waals_pressure(n_moles: float, t_k: float, volume_m3: float, a: float, b: float) -> float:
    """Compute van der Waals pressure: P = nRT/(V-nb) - a*n^2/V^2."""
    if volume_m3 <= n_moles * b:
        raise ValueError("Volume too small for van der Waals equation")
    return n_moles * R * t_k / (volume_m3 - n_moles * b) - a * n_moles * n_moles / (volume_m3 * volume_m3)


def clausius_clapeyron(dp_dt: float, t_k: float, delta_v: float) -> float:
    """Compute latent heat from Clausius-Clapeyron: L = T * (dP/dT) * Delta_V."""
    return t_k * dp_dt * delta_v


def otto_efficiency(compression_ratio: float, gamma: float = 1.4) -> float:
    """Compute Otto cycle efficiency: eta = 1 - 1/r^(gamma-1)."""
    if compression_ratio <= 1.0:
        raise ValueError("Compression ratio must be > 1")
    return 1.0 - 1.0 / (compression_ratio ** (gamma - 1.0))


def diesel_efficiency(compression_ratio: float, cutoff_ratio: float, gamma: float = 1.4) -> float:
    """Compute Diesel cycle efficiency."""
    if compression_ratio <= 1.0:
        raise ValueError("Compression ratio must be > 1")
    term1 = cutoff_ratio ** gamma - 1.0
    term2 = gamma * (cutoff_ratio - 1.0)
    return 1.0 - (1.0 / (compression_ratio ** (gamma - 1.0))) * (term1 / term2)


def brayton_efficiency(pressure_ratio: float, gamma: float = 1.4) -> float:
    """Compute Brayton cycle efficiency: eta = 1 - 1/r_p^((gamma-1)/gamma)."""
    if pressure_ratio <= 1.0:
        raise ValueError("Pressure ratio must be > 1")
    return 1.0 - 1.0 / (pressure_ratio ** ((gamma - 1.0) / gamma))


def maxwell_boltzmann_speed_distribution(v: float, m: float, t_k: float) -> float:
    """Compute Maxwell-Boltzmann speed distribution f(v).
    f(v) = 4*pi * (m/(2*pi*k_B*T))^(3/2) * v^2 * exp(-m*v^2/(2*k_B*T))
    """
    a = m / (2.0 * K_B * t_k)
    return 4.0 * math.pi * (a / math.pi) ** 1.5 * v * v * math.exp(-a * v * v)


def most_probable_speed(m: float, t_k: float) -> float:
    """Compute most probable speed v_p = sqrt(2*k_B*T/m)."""
    return math.sqrt(2.0 * K_B * t_k / m)


def mean_speed(m: float, t_k: float) -> float:
    """Compute mean speed <v> = sqrt(8*k_B*T / (pi*m))."""
    return math.sqrt(8.0 * K_B * t_k / (math.pi * m))


def rms_speed(m: float, t_k: float) -> float:
    """Compute RMS speed v_rms = sqrt(3*k_B*T/m)."""
    return math.sqrt(3.0 * K_B * t_k / m)


def ising_mean_field_magnetization(t_k: float, tc_k: float) -> float:
    """Compute mean-field magnetization near Tc: m = sqrt(3*(Tc-T)/Tc) for T < Tc, else 0."""
    if t_k >= tc_k:
        return 0.0
    return math.sqrt(3.0 * (tc_k - t_k) / tc_k)


def landau_free_energy(order_parameter: float, t_k: float, tc_k: float, a0: float = 1.0, b: float = 1.0) -> float:
    """Compute Landau free energy: F = F0 + a0*(T-Tc)*m^2/2 + b*m^4/4."""
    return 0.5 * a0 * (t_k - tc_k) * order_parameter * order_parameter + 0.25 * b * order_parameter ** 4


def get_phase_transition_data(material: str) -> list[dict[str, Any]]:
    """Return all phase transition entries for a given material."""
    return [entry for entry in PHASE_TRANSITIONS if entry["material"] == material]


def get_engine_cycle(cycle_name: str) -> dict[str, Any] | None:
    """Return heat engine cycle data by name."""
    for cycle in HEAT_ENGINE_CYCLES:
        if cycle["cycle"] == cycle_name:
            return cycle
    return None


def all_engine_cycles() -> list[str]:
    """Return all known heat engine cycle names."""
    return [c["cycle"] for c in HEAT_ENGINE_CYCLES]


def get_ensemble(ensemble_name: str) -> dict[str, Any] | None:
    """Return ensemble description by name or abbreviation."""
    for ens in ENSEMBLE_TYPES:
        if ens["ensemble"] == ensemble_name or ens["abbreviation"] == ensemble_name:
            return ens
    return None


@dataclass(frozen=True)
class ThermoConfig:
    substance: str
    mass_kg: float
    initial_temp_C: float
    final_temp_C: float
    pressure_atm: float


_SPECIFIC_HEAT_KJ_KG_K = {
    "water": 4.184,
    "iron": 0.449,
}

_LATENT_HEAT_KJ_KG = {
    "water": {"vaporization": 2256.0, "fusion": 334.0},
    "iron": {"fusion": 247.0},
}


def compute_heat_transfer(config: ThermoConfig) -> dict[str, Any]:
    cp = _SPECIFIC_HEAT_KJ_KG_K.get(config.substance.lower(), 1.0)
    delta_t = config.final_temp_C - config.initial_temp_C
    heat_kj = config.mass_kg * cp * delta_t
    return {
        "config": asdict(config),
        "specific_heat_kJ_kg_K": cp,
        "delta_T_C": delta_t,
        "heat_transfer_kJ": round(heat_kj, 6),
    }


def compute_phase_change(config: ThermoConfig) -> dict[str, Any]:
    substance = config.substance.lower()
    transitions: list[dict[str, float | str]] = []
    if substance == "water" and config.initial_temp_C < 100.0 <= config.final_temp_C:
        transitions.append({
            "transition": "vaporization",
            "latent_heat_kJ": round(config.mass_kg * _LATENT_HEAT_KJ_KG["water"]["vaporization"], 6),
        })
    if substance == "water" and config.initial_temp_C < 0.0 <= config.final_temp_C:
        transitions.append({
            "transition": "fusion",
            "latent_heat_kJ": round(config.mass_kg * _LATENT_HEAT_KJ_KG["water"]["fusion"], 6),
        })
    return {"substance": config.substance, "transitions": transitions}


def compute_entropy_change(config: ThermoConfig) -> dict[str, float]:
    heat = compute_heat_transfer(config)["heat_transfer_kJ"] * 1000.0
    avg_temp_k = ((config.initial_temp_C + 273.15) + (config.final_temp_C + 273.15)) / 2.0
    entropy = 0.0 if avg_temp_k <= 0 else heat / avg_temp_k
    return {"entropy_change_J_K": round(entropy, 6)}


def write_thermo_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "thermo_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
