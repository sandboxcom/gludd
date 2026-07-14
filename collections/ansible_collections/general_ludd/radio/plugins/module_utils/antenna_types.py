"""
antenna_types -- Design equations and radiation-pattern data for common antennas.

Each entry:
    {
        "type": str,
        "gain_dbi": float,
        "impedance_ohms": float,
        "polarization": str,
        "pattern": str,
        "beamwidth_deg": float | None,
        "design_freq_min_mhz": float | None,
        "design_freq_max_mhz": float | None,
        "design_params": list[str],
        "design_equations": dict[str, str],
    }

Functions:
    design_antenna(type, frequency_mhz, impedance) -> dimensions dict
    radiation_pattern(antenna_type, elevation_deg, azimuth_deg) -> gain_dbi
"""

from __future__ import annotations

import math
from typing import Any

ANTENNA_TYPES: list[dict[str, Any]] = [
    {
        "type": "dipole_half_wave",
        "display": "Half-Wave Dipole",
        "gain_dbi": 2.15,
        "impedance_ohms": 73.0,
        "polarization": "linear (horizontal)",
        "pattern": "figure-eight (broadside)",
        "beamwidth_deg": 78.0,
        "design_freq_min_mhz": 1.8,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "length_m": "143.0 / frequency_mhz  (or 468 / frequency_mhz in feet)",
            "element_spacing_m": "N/A -- single element",
        },
    },
    {
        "type": "dipole_folded",
        "display": "Folded Dipole",
        "gain_dbi": 2.15,
        "impedance_ohms": 300.0,
        "polarization": "linear (horizontal)",
        "pattern": "figure-eight (broadside)",
        "beamwidth_deg": 78.0,
        "design_freq_min_mhz": 1.8,
        "design_freq_max_mhz": 1000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "length_m": "143.0 / frequency_mhz",
            "impedance_note": "300 ohm balanced; use 4:1 balun for 75 ohm coax",
        },
    },
    {
        "type": "yagi_3el",
        "display": "3-Element Yagi-Uda",
        "gain_dbi": 7.0,
        "impedance_ohms": 50.0,
        "polarization": "linear (horizontal or vertical)",
        "pattern": "directional forward lobe with F/B ~15 dB",
        "beamwidth_deg": 60.0,
        "design_freq_min_mhz": 7.0,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "reflector_length_m": "150.0 / frequency_mhz",
            "driven_element_length_m": "143.0 / frequency_mhz",
            "director_length_m": "138.0 / frequency_mhz",
            "reflector_spacing_m": "0.2 * (300.0 / frequency_mhz)",
            "director_spacing_m": "0.2 * (300.0 / frequency_mhz)",
        },
    },
    {
        "type": "yagi_5el",
        "display": "5-Element Yagi-Uda",
        "gain_dbi": 10.0,
        "impedance_ohms": 50.0,
        "polarization": "linear (horizontal or vertical)",
        "pattern": "narrow forward lobe with F/B ~20 dB",
        "beamwidth_deg": 45.0,
        "design_freq_min_mhz": 14.0,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "reflector_length_m": "153.0 / frequency_mhz",
            "driven_element_length_m": "143.0 / frequency_mhz",
            "director1_length_m": "138.0 / frequency_mhz",
            "director2_length_m": "135.0 / frequency_mhz",
            "director3_length_m": "133.0 / frequency_mhz",
            "reflector_spacing_m": "0.2 * (300.0 / frequency_mhz)",
            "d1_spacing_m": "0.15 * (300.0 / frequency_mhz)",
            "d2_spacing_m": "0.15 * (300.0 / frequency_mhz)",
            "d3_spacing_m": "0.15 * (300.0 / frequency_mhz)",
        },
    },
    {
        "type": "vertical_quarter_wave",
        "display": "Quarter-Wave Vertical (Monopole)",
        "gain_dbi": 0.0,
        "impedance_ohms": 36.0,
        "polarization": "linear (vertical)",
        "pattern": "omnidirectional (elevation: low-angle doughnut)",
        "beamwidth_deg": None,
        "design_freq_min_mhz": 1.8,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "radiator_length_m": "71.3 / frequency_mhz  (or 234 / frequency_mhz in feet)",
            "radial_count": "4+ (more = better; 16 typical for ground-mounted)",
            "radial_length_m": "71.3 / frequency_mhz",
        },
    },
    {
        "type": "ground_plane",
        "display": "Ground-Plane Vertical",
        "gain_dbi": 3.0,
        "impedance_ohms": 50.0,
        "polarization": "linear (vertical)",
        "pattern": "omnidirectional (low-angle doughnut)",
        "beamwidth_deg": None,
        "design_freq_min_mhz": 1.8,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz", "radial_angle_deg"],
        "design_equations": {
            "radiator_length_m": "71.3 / frequency_mhz",
            "radial_length_m": "73.0 / frequency_mhz",
            "radial_angle": "90-135 degrees (drooping raises impedance to 50 ohm)",
        },
    },
    {
        "type": "discone",
        "display": "Discone Antenna",
        "gain_dbi": 1.0,
        "impedance_ohms": 50.0,
        "polarization": "vertical",
        "pattern": "omnidirectional (elevation: broad upward coverage)",
        "beamwidth_deg": None,
        "design_freq_min_mhz": 25.0,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_low_mhz", "frequency_high_mhz"],
        "design_equations": {
            "disk_diameter_m": "71.3 / frequency_low_mhz",
            "cone_length_m": "71.3 / frequency_low_mhz",
            "cone_angle_deg": "30-60 (wider = lower impedance; 45 deg yields ~50 ohm)",
            "bandwidth_ratio": "typically 8:1 to 10:1",
        },
    },
    {
        "type": "loop_full_wave",
        "display": "Full-Wave Loop",
        "gain_dbi": 3.2,
        "impedance_ohms": 100.0,
        "polarization": "linear (orientation-dependent; horizontal loop = horizontal pol)",
        "pattern": "broadside bidirectional (quad loop element)",
        "beamwidth_deg": 80.0,
        "design_freq_min_mhz": 1.8,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_mhz"],
        "design_equations": {
            "circumference_m": "306.0 / frequency_mhz  (or 1005 / frequency_mhz in feet)",
            "shape": "circle, square, delta, or quad -- circumference is the key",
        },
    },
    {
        "type": "magnetic_loop",
        "display": "Small Magnetic Loop (Magloop)",
        "gain_dbi": -10.0,
        "impedance_ohms": 5.0,
        "polarization": "linear (plane of loop)",
        "pattern": "figure-eight in plane of loop; deep nulls on axis",
        "beamwidth_deg": None,
        "design_freq_min_mhz": 3.5,
        "design_freq_max_mhz": 30.0,
        "design_params": ["frequency_mhz", "loop_diameter_m"],
        "design_equations": {
            "max_diameter_m": "9.55 / frequency_mhz  (max 1/10 wavelength circumference)",
            "radiation_resistance_ohms": "31200 * (area_m2^2) / (wavelength_m^4)",
            "tuning_capacitor_pf": "1 / (4 * pi^2 * frequency_hz^2 * inductance_h)",
        },
    },
    {
        "type": "patch",
        "display": "Microstrip Patch Antenna",
        "gain_dbi": 6.0,
        "impedance_ohms": 50.0,
        "polarization": "linear (or circular with perturbation)",
        "pattern": "broadside hemispherical",
        "beamwidth_deg": 70.0,
        "design_freq_min_mhz": 300.0,
        "design_freq_max_mhz": 60000.0,
        "design_params": ["frequency_mhz", "substrate_er", "substrate_height_mm"],
        "design_equations": {
            "patch_width_mm": "(300000.0 / (frequency_mhz * sqrt((er + 1) / 2))) / 2",
            "patch_length_mm": "c / (2 * frequency_hz * sqrt(er_eff)) - 2 * delta_L",
            "er_eff": "(er+1)/2 + (er-1)/(2*sqrt(1 + 12*h/w))",
            "delta_L_mm": "0.412 * h * ((er_eff+0.3)*(w/h+0.264)) / ((er_eff-0.258)*(w/h+0.8))",
        },
    },
    {
        "type": "helical_axial",
        "display": "Axial-Mode Helical Antenna",
        "gain_dbi": 13.0,
        "impedance_ohms": 140.0,
        "polarization": "circular",
        "pattern": "directional end-fire; moderate beamwidth",
        "beamwidth_deg": 35.0,
        "design_freq_min_mhz": 100.0,
        "design_freq_max_mhz": 10000.0,
        "design_params": ["frequency_mhz", "turns", "turn_spacing"],
        "design_equations": {
            "circumference_m": "300.0 / frequency_mhz  (1 wavelength circumference; axial mode: 0.75-1.33 lambda)",
            "turn_spacing_m": "0.22 * circumference_m  (12-16 degree pitch angle)",
            "gain_dbi_approx": "10 * log10(15 * turns * circumference_lambda^2 * spacing_lambda)",
            "beamwidth_deg_approx": "52 / (circumference_lambda * sqrt(turns * spacing_lambda))",
        },
    },
    {
        "type": "log_periodic",
        "display": "Log-Periodic Dipole Array (LPDA)",
        "gain_dbi": 7.5,
        "impedance_ohms": 50.0,
        "polarization": "linear",
        "pattern": "directional; moderate gain over wide bandwidth",
        "beamwidth_deg": 50.0,
        "design_freq_min_mhz": 3.0,
        "design_freq_max_mhz": 3000.0,
        "design_params": ["frequency_low_mhz", "frequency_high_mhz", "num_elements"],
        "design_equations": {
            "scale_factor_tau": "typically 0.85-0.95; element lengths scale by tau",
            "spacing_factor_sigma": "typically 0.05-0.15; spacing = 0.5 * sigma * lambda_max",
            "longest_element_m": "150.0 / frequency_low_mhz",
            "shortest_element_m": "150.0 / frequency_high_mhz",
        },
    },
    {
        "type": "beverage",
        "display": "Beverage Antenna (Wave Antenna)",
        "gain_dbi": 0.0,
        "impedance_ohms": 450.0,
        "polarization": "linear (horizontal from ground wave; vertical from sky wave)",
        "pattern": "directional end-fire; very low angle; excellent F/B",
        "beamwidth_deg": 25.0,
        "design_freq_min_mhz": 0.5,
        "design_freq_max_mhz": 30.0,
        "design_params": ["length_m", "height_m", "termination_ohms"],
        "design_equations": {
            "optimal_length_m": "1-2 wavelengths at lowest freq; 2+ wavelengths for best directionality",
            "height_m": "3-6 above ground; lower = lower angle but more loss",
            "termination_ohms": "450 (match to antenna impedance; non-inductive; grounded far end)",
            "gain_note": "Unity gain but excellent S/N due to low noise pickup",
        },
    },
    {
        "type": "rhombic",
        "display": "Rhombic Antenna",
        "gain_dbi": 14.0,
        "impedance_ohms": 600.0,
        "polarization": "linear (horizontal)",
        "pattern": "bidirectional (unterminated); directional end-fire (terminated)",
        "beamwidth_deg": 15.0,
        "design_freq_min_mhz": 3.0,
        "design_freq_max_mhz": 30.0,
        "design_params": ["frequency_mhz", "leg_length_lambda", "tilt_angle_deg"],
        "design_equations": {
            "leg_length_m": "leg_length_lambda * (300.0 / frequency_mhz)",
            "height_m": "0.5-1.0 lambda above ground",
            "tilt_angle_deg": "90 - 2 * arcsin(0.371 / leg_length_lambda^0.5)",
            "termination_ohms": "600 (non-inductive; 300 ohm each leg pair)",
        },
    },
    {
        "type": "parabolic_reflector",
        "display": "Parabolic Reflector (Dish)",
        "gain_dbi": 25.0,
        "impedance_ohms": 50.0,
        "polarization": "feed-dependent (linear or circular)",
        "pattern": "highly directional pencil beam",
        "beamwidth_deg": 5.0,
        "design_freq_min_mhz": 1000.0,
        "design_freq_max_mhz": 100000.0,
        "design_params": ["frequency_mhz", "diameter_m", "efficiency"],
        "design_equations": {
            "gain_dbi": "10 * log10(efficiency * (pi * diameter_m / wavelength_m)^2)",
            "beamwidth_deg": "70 * wavelength_m / diameter_m  (3 dB beamwidth)",
            "focal_length_m": "diameter_m^2 / (16 * depth_m)  (prime focus)",
            "f_d_ratio": "0.3-0.5 typical for amateur; 0.25-0.35 for deep dishes",
        },
    },
]


def antenna_info(name: str) -> dict[str, Any] | None:
    """Return the entry for a named antenna type, or None."""
    for a in ANTENNA_TYPES:
        if a["type"] == name:
            return a
    return None


def types_for_frequency(freq_hz: float) -> list[str]:
    """Return antenna type names suitable for a given frequency in Hz."""
    freq_mhz = freq_hz / 1_000_000.0
    result = []
    for a in ANTENNA_TYPES:
        fmin = a.get("design_freq_min_mhz")
        fmax = a.get("design_freq_max_mhz")
        if fmin is not None and fmax is not None and fmin <= freq_mhz <= fmax:
            result.append(a["type"])
    return result


def design_antenna(
    antenna_type: str,
    frequency_mhz: float,
    impedance: float = 50.0,
) -> dict[str, Any]:
    """
    Return approximate physical dimensions for an antenna type at a given frequency.
    All dimensions in meters unless noted.
    """
    c = 299.792458
    wavelength_m = c / frequency_mhz
    half_wl_m = wavelength_m / 2.0
    quarter_wl_m = wavelength_m / 4.0

    info = antenna_info(antenna_type)
    if info is None:
        return {"error": f"Unknown antenna type: {antenna_type}"}

    if antenna_type == "dipole_half_wave":
        return {
            "type": "dipole_half_wave",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "element_length_m": round(143.0 / frequency_mhz, 4),
            "element_length_feet": round(468.0 / frequency_mhz, 4),
            "impedance_ohms": 73.0,
            "notes": "Each leg = element_length / 2. Trim for resonance (shorter = higher freq).",
        }

    if antenna_type == "vertical_quarter_wave":
        return {
            "type": "vertical_quarter_wave",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "radiator_length_m": round(71.3 / frequency_mhz, 4),
            "radiator_length_feet": round(234.0 / frequency_mhz, 4),
            "radial_length_m": round(71.3 / frequency_mhz, 4),
            "impedance_ohms": 36.0,
            "notes": "Requires ground plane or radials. Impedance ~36 ohm; use matching network for 50 ohm.",
        }

    if antenna_type == "yagi_3el":
        return {
            "type": "yagi_3el",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "reflector_length_m": round(150.0 / frequency_mhz, 4),
            "driven_element_length_m": round(143.0 / frequency_mhz, 4),
            "director_length_m": round(138.0 / frequency_mhz, 4),
            "reflector_spacing_m": round(0.2 * wavelength_m, 4),
            "director_spacing_m": round(0.2 * wavelength_m, 4),
            "boom_length_m": round(0.4 * wavelength_m, 4),
            "impedance_ohms": 50.0,
        }

    if antenna_type == "yagi_5el":
        wl = wavelength_m
        return {
            "type": "yagi_5el",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wl, 4),
            "reflector_length_m": round(153.0 / frequency_mhz, 4),
            "driven_element_length_m": round(143.0 / frequency_mhz, 4),
            "director1_length_m": round(138.0 / frequency_mhz, 4),
            "director2_length_m": round(135.0 / frequency_mhz, 4),
            "director3_length_m": round(133.0 / frequency_mhz, 4),
            "reflector_spacing_m": round(0.2 * wl, 4),
            "d1_spacing_m": round(0.15 * wl, 4),
            "d2_spacing_m": round(0.15 * wl, 4),
            "d3_spacing_m": round(0.15 * wl, 4),
            "boom_length_m": round(0.65 * wl, 4),
            "impedance_ohms": 50.0,
        }

    if antenna_type == "ground_plane":
        return {
            "type": "ground_plane",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "radiator_length_m": round(71.3 / frequency_mhz, 4),
            "radial_length_m": round(73.0 / frequency_mhz, 4),
            "radial_angle_deg": 135,
            "impedance_ohms": 50.0,
            "notes": "Drooping radials at ~135 deg from vertical bring impedance closer to 50 ohms.",
        }

    if antenna_type == "loop_full_wave":
        circumference = 306.0 / frequency_mhz
        return {
            "type": "loop_full_wave",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "circumference_m": round(circumference, 4),
            "circumference_feet": round(1005.0 / frequency_mhz, 4),
            "diameter_if_circle_m": round(circumference / math.pi, 4),
            "side_if_square_m": round(circumference / 4.0, 4),
            "impedance_ohms": 100.0,
        }

    if antenna_type == "discone":
        return {
            "type": "discone",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "disk_diameter_m": round(71.3 / frequency_mhz, 4),
            "cone_length_m": round(quarter_wl_m, 4),
            "cone_angle_deg": 45.0,
            "impedance_ohms": 50.0,
            "notes": "Wideband (8:1). Disk diam = 0.238*lambda; cone = 0.25*lambda.",
        }

    if antenna_type == "parabolic_reflector":
        diameter_m_in = 1.0
        eff = 0.60
        gain_dbi_calc = 10.0 * math.log10(eff * (math.pi * diameter_m_in / wavelength_m) ** 2)
        bw_deg = 70.0 * wavelength_m / diameter_m_in
        return {
            "type": "parabolic_reflector",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "diameter_m": diameter_m_in,
            "efficiency": eff,
            "gain_dbi": round(gain_dbi_calc, 1),
            "beamwidth_deg": round(bw_deg, 2),
            "f_d_ratio": 0.4,
            "focal_length_m": round(0.4 * diameter_m_in, 4),
            "notes": "Gain scales with diameter. Use diameter_m param for custom size.",
        }

    if antenna_type == "patch":
        er = 4.4
        h_mm = 1.6
        w_mm = (300000.0 / (frequency_mhz * math.sqrt((er + 1.0) / 2.0))) / 2.0
        er_eff = (er + 1) / 2 + (er - 1) / (2 * math.sqrt(1 + 12 * h_mm / w_mm))
        delta_l_mm = 0.412 * h_mm * (er_eff + 0.3) * (w_mm / h_mm + 0.264) / ((er_eff - 0.258) * (w_mm / h_mm + 0.8))
        l_mm = 300000.0 / (2.0 * frequency_mhz * math.sqrt(er_eff)) - 2.0 * delta_l_mm
        return {
            "type": "patch",
            "frequency_mhz": frequency_mhz,
            "wavelength_mm": round(300000.0 / frequency_mhz, 2),
            "substrate_er": er,
            "substrate_height_mm": h_mm,
            "patch_width_mm": round(w_mm, 2),
            "patch_length_mm": round(l_mm, 2),
            "er_eff": round(er_eff, 4),
            "impedance_ohms": 50.0,
        }

    if antenna_type == "helical_axial":
        turns = 8.0
        circumference_m = wavelength_m
        spacing_m = 0.22 * circumference_m
        gain_approx = 10.0 * math.log10(15.0 * turns * (circumference_m / wavelength_m) ** 2 * spacing_m / wavelength_m + 0.001)
        return {
            "type": "helical_axial",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "turns": turns,
            "circumference_m": round(circumference_m, 4),
            "turn_spacing_m": round(spacing_m, 4),
            "total_length_m": round(turns * spacing_m, 4),
            "gain_dbi_approx": round(gain_approx, 1),
            "impedance_ohms": 140.0,
        }

    if antenna_type == "beverage":
        length_m = 2.0 * wavelength_m
        return {
            "type": "beverage",
            "frequency_mhz": frequency_mhz,
            "wavelength_m": round(wavelength_m, 4),
            "length_m": round(length_m, 2),
            "length_wavelengths": 2.0,
            "height_m": 3.0,
            "termination_ohms": 450.0,
            "impedance_ohms": 450.0,
            "notes": "RX-only. Low-noise. Point toward desired direction; terminate far end.",
        }

    return {
        "type": antenna_type,
        "frequency_mhz": frequency_mhz,
        "wavelength_m": round(wavelength_m, 4),
        "impedance_ohms": impedance,
        "notes": "Generic design; see design_equations in antenna info for custom parameters.",
        "design_equations": info.get("design_equations", {}),
    }


def radiation_pattern(
    antenna_type: str,
    elevation_deg: float = 0.0,
    azimuth_deg: float = 0.0,
) -> dict[str, Any]:
    """
    Approximate gain in dBi for a given antenna type at specified angles.
    Uses simplified analytical radiation pattern models.
    """
    elevation_rad = math.radians(elevation_deg)
    azimuth_rad = math.radians(azimuth_deg)

    info = antenna_info(antenna_type)
    if info is None:
        return {"error": f"Unknown antenna type: {antenna_type}"}

    nominal_gain = info["gain_dbi"]
    pattern = info["pattern"]

    if "omnidirectional" in pattern:
        az_factor = 1.0
        if "doughnut" in pattern:
            el_factor = abs(math.cos(elevation_rad))
        else:
            el_factor = 1.0
        gain = nominal_gain * el_factor * az_factor

        return {
            "type": antenna_type,
            "elevation_deg": elevation_deg,
            "azimuth_deg": azimuth_deg,
            "gain_dbi": round(gain, 2),
            "nominal_gain_dbi": nominal_gain,
            "pattern_type": pattern,
            "elevation_factor": round(el_factor, 4),
            "azimuth_factor": round(az_factor, 4),
        }

    if "figure-eight" in pattern:
        az_factor = abs(math.cos(azimuth_rad))
        el_factor = abs(math.cos(elevation_rad))
        gain = nominal_gain * az_factor * el_factor
        return {
            "type": antenna_type,
            "elevation_deg": elevation_deg,
            "azimuth_deg": azimuth_deg,
            "gain_dbi": round(gain, 2),
            "nominal_gain_dbi": nominal_gain,
            "pattern_type": pattern,
            "elevation_factor": round(el_factor, 4),
            "azimuth_factor": round(az_factor, 4),
        }

    if "directional" in pattern or "pencil" in pattern:
        bw = info.get("beamwidth_deg", 60.0)
        bw_rad = math.radians(bw)
        az_deviation = azimuth_deg
        el_deviation = elevation_deg
        az_factor = max(0.0, math.cos(az_deviation * math.pi / bw_rad)) if bw_rad > 0 else 1.0
        el_factor = max(0.0, math.cos(el_deviation * math.pi / bw_rad)) if bw_rad > 0 else 1.0
        gain = nominal_gain * az_factor * el_factor
        return {
            "type": antenna_type,
            "elevation_deg": elevation_deg,
            "azimuth_deg": azimuth_deg,
            "gain_dbi": round(gain, 2),
            "nominal_gain_dbi": nominal_gain,
            "pattern_type": pattern,
            "beamwidth_deg": bw,
            "elevation_factor": round(el_factor, 4),
            "azimuth_factor": round(az_factor, 4),
        }

    return {
        "type": antenna_type,
        "elevation_deg": elevation_deg,
        "azimuth_deg": azimuth_deg,
        "gain_dbi": nominal_gain,
        "nominal_gain_dbi": nominal_gain,
        "pattern_type": pattern,
        "notes": "Pattern not specifically modeled; using nominal gain",
    }
