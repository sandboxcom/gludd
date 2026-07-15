#!/usr/bin/env python3
"""
antenna_design — Antenna element calculator for common amateur/ISM types.

Usage:
    python antenna_design.py --type dipole --freq FREQ_HZ
        [--polarization POL] [--impedance OHMS] [--material MAT]
        [--output-dir DIR]

Output: JSON with physical dimensions, radiation pattern data, feed parameters.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SPEED_OF_LIGHT_MS = 299_792_458.0
VELOCITY_FACTORS: dict[str, float] = {
    "copper": 0.95,
    "aluminum": 0.95,
    "steel": 0.88,
    "stainless_steel": 0.85,
    "bare_wire": 0.95,
    "insulated_wire": 0.80,
    "air": 1.0,
    "pcb_fr4": 0.65,
}
WIRE_GAUGE_MM: dict[int, float] = {
    6: 4.115,
    8: 3.264,
    10: 2.588,
    12: 2.053,
    14: 1.628,
    16: 1.291,
    18: 1.024,
    20: 0.812,
    22: 0.644,
    24: 0.511,
}


@dataclass
class AntennaDimensions:
    type: str = "dipole"
    freq_hz: int = 144_000_000
    wavelength_m: float = 0.0
    half_wavelength_m: float = 0.0
    quarter_wavelength_m: float = 0.0
    velocity_factor: float = 0.95
    material: str = "copper"
    conductor_diameter_m: float = 0.002
    element_length_m: float = 0.0
    impedance_ohms: float = 73.0
    bandwidth_hz: float = 0.0
    gain_dbi: float = 2.15
    swr_typical: float = 1.0
    polarization: str = "vertical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "freq_hz": self.freq_hz,
            "freq_mhz": round(self.freq_hz / 1_000_000.0, 4),
            "wavelength_m": round(self.wavelength_m, 4),
            "half_wavelength_m": round(self.half_wavelength_m, 4),
            "quarter_wavelength_m": round(self.quarter_wavelength_m, 4),
            "velocity_factor": self.velocity_factor,
            "material": self.material,
            "conductor_diameter_mm": round(self.conductor_diameter_m * 1000.0, 3),
            "element_length_m": round(self.element_length_m, 4),
            "element_length_in": round(self.element_length_m * 39.3701, 2),
            "impedance_ohms": round(self.impedance_ohms, 1),
            "bandwidth_hz": round(self.bandwidth_hz, 0),
            "bandwidth_pct": round(self.bandwidth_hz / self.freq_hz * 100.0, 2) if self.freq_hz else 0.0,
            "gain_dbi": round(self.gain_dbi, 2),
            "swr_typical": round(self.swr_typical, 2),
            "polarization": self.polarization,
        }


def _compute_k_factor(length_to_diameter: float) -> float:
    if length_to_diameter <= 0:
        return 1.0
    return 0.978 + 0.0022 * math.log10(length_to_diameter) if length_to_diameter < 1e9 else 0.96


def design_dipole(
    freq_hz: int,
    velocity_factor: float = 0.95,
    impedance_ohms: float = 50.0,
    conductor_diameter_m: float = 0.002,
    polarization: str = "vertical",
    material: str = "copper",
) -> AntennaDimensions:
    wavelength = SPEED_OF_LIGHT_MS * velocity_factor / freq_hz
    half_wl = wavelength / 2.0
    k = _compute_k_factor(half_wl / conductor_diameter_m)
    element_length = half_wl * k

    diam_to_lambda = conductor_diameter_m / wavelength
    z0 = 73.0
    if diam_to_lambda > 0:
        z0 = 73.0 - 40.0 * math.log10(diam_to_lambda * 100.0)
    z0 = max(z0, 30.0)

    return AntennaDimensions(
        type="dipole",
        freq_hz=freq_hz,
        wavelength_m=wavelength,
        half_wavelength_m=wavelength / 2.0,
        quarter_wavelength_m=wavelength / 4.0,
        velocity_factor=velocity_factor,
        material=material,
        conductor_diameter_m=conductor_diameter_m,
        element_length_m=element_length,
        impedance_ohms=z0,
        bandwidth_hz=freq_hz * 0.05,
        gain_dbi=2.15,
        swr_typical=1.5,
        polarization=polarization,
    )


def design_yagi(
    freq_hz: int,
    velocity_factor: float = 0.95,
    impedance_ohms: float = 50.0,
    conductor_diameter_m: float = 0.004,
    polarization: str = "horizontal",
    material: str = "aluminum",
) -> dict[str, Any]:
    wavelength = SPEED_OF_LIGHT_MS * velocity_factor / freq_hz
    half_wl = wavelength / 2.0

    reflector_length = half_wl * 1.05
    driven_length = half_wl * 0.95
    director_length = half_wl * 0.90
    spacing = wavelength * 0.2

    elements = [
        {"name": "reflector", "length_m": round(reflector_length, 4), "length_in": round(reflector_length * 39.3701, 2), "position_m": 0.0},
        {"name": "driven_element", "length_m": round(driven_length, 4), "length_in": round(driven_length * 39.3701, 2), "position_m": round(spacing, 4)},
        {"name": "director_1", "length_m": round(director_length, 4), "length_in": round(director_length * 39.3701, 2), "position_m": round(spacing * 2, 4)},
    ]

    dims = AntennaDimensions(
        type="yagi",
        freq_hz=freq_hz,
        wavelength_m=wavelength,
        half_wavelength_m=wavelength / 2.0,
        quarter_wavelength_m=wavelength / 4.0,
        velocity_factor=velocity_factor,
        material=material,
        conductor_diameter_m=conductor_diameter_m,
        element_length_m=driven_length,
        impedance_ohms=impedance_ohms,
        bandwidth_hz=freq_hz * 0.02,
        gain_dbi=7.15,
        swr_typical=1.3,
        polarization=polarization,
    )

    result = dims.to_dict()
    result["elements"] = elements
    result["boom_length_m"] = round(spacing * 3, 4)
    result["boom_length_in"] = round(spacing * 3 * 39.3701, 2)
    result["radiation_pattern"] = {
        "beamwidth_h_deg": 65,
        "beamwidth_v_deg": 55,
        "f_b_ratio_db": 15.0,
    }
    return result


def design_loop(
    freq_hz: int,
    velocity_factor: float = 0.95,
    impedance_ohms: float = 50.0,
    conductor_diameter_m: float = 0.002,
    polarization: str = "horizontal",
    material: str = "copper",
) -> dict[str, Any]:
    wavelength = SPEED_OF_LIGHT_MS * velocity_factor / freq_hz
    circumference = wavelength * 1.02
    diameter = circumference / math.pi
    radius = diameter / 2.0

    dims = AntennaDimensions(
        type="loop",
        freq_hz=freq_hz,
        wavelength_m=wavelength,
        half_wavelength_m=wavelength / 2.0,
        quarter_wavelength_m=wavelength / 4.0,
        velocity_factor=velocity_factor,
        material=material,
        conductor_diameter_m=conductor_diameter_m,
        element_length_m=circumference,
        impedance_ohms=impedance_ohms,
        bandwidth_hz=freq_hz * 0.03,
        gain_dbi=3.65,
        swr_typical=1.2,
        polarization=polarization,
    )

    result = dims.to_dict()
    result["loop_diameter_m"] = round(diameter, 4)
    result["loop_diameter_in"] = round(diameter * 39.3701, 2)
    result["loop_radius_m"] = round(radius, 4)
    result["circumference_m"] = round(circumference, 4)
    result["required_capacitor_pf"] = round(1.0 / (2.0 * math.pi * freq_hz * impedance_ohms) * 1e12 * 0.3, 1)
    result["matching"] = {
        "type": "gamma_match",
        "tap_point_pct": 25,
    }
    result["radiation_pattern"] = {
        "beamwidth_h_deg": 80,
        "beamwidth_v_deg": 80,
        "f_b_ratio_db": 0.0,
    }
    return result


def design_patch(
    freq_hz: int,
    velocity_factor: float = 0.65,
    impedance_ohms: float = 50.0,
    conductor_diameter_m: float = 0.0,
    polarization: str = "vertical",
    material: str = "pcb_fr4",
) -> dict[str, Any]:
    wavelength = SPEED_OF_LIGHT_MS * velocity_factor / freq_hz
    patch_width = wavelength / 2.0
    epsilon_eff = (4.4 + 1.0) / 2.0 + (4.4 - 1.0) / 2.0 * 1.0 / math.sqrt(1.0 + 12.0 * 0.0016 / patch_width)
    delta_l = 0.0016 * 0.412 * ((epsilon_eff + 0.3) * (patch_width / 0.0016 + 0.264)) / ((epsilon_eff - 0.258) * (patch_width / 0.0016 + 0.8))
    patch_length = wavelength / (2.0 * math.sqrt(epsilon_eff)) - 2.0 * delta_l

    feed_inset = patch_length * 0.22

    dims = AntennaDimensions(
        type="patch",
        freq_hz=freq_hz,
        wavelength_m=wavelength,
        half_wavelength_m=wavelength / 2.0,
        quarter_wavelength_m=wavelength / 4.0,
        velocity_factor=velocity_factor,
        material=material,
        conductor_diameter_m=conductor_diameter_m or 0.0001,
        element_length_m=patch_length,
        impedance_ohms=impedance_ohms,
        bandwidth_hz=freq_hz * 0.05,
        gain_dbi=6.0,
        swr_typical=1.5,
        polarization=polarization,
    )

    result = dims.to_dict()
    result["substrate"] = {
        "material": "FR-4",
        "dielectric_constant": 4.4,
        "thickness_mm": 1.6,
        "loss_tangent": 0.02,
    }
    result["patch_width_m"] = round(patch_width, 4)
    result["patch_width_mm"] = round(patch_width * 1000.0, 2)
    result["patch_length_m"] = round(patch_length, 4)
    result["patch_length_mm"] = round(patch_length * 1000.0, 2)
    result["ground_plane_mm"] = round(max(patch_width, patch_length) * 1000.0 * 2.0, 1)
    result["feed_inset_mm"] = round(feed_inset * 1000.0, 2)
    result["feed_line_width_mm"] = 3.09
    result["impedance_50ohm_line_width_mm"] = 3.09
    result["radiation_pattern"] = {
        "beamwidth_h_deg": 80,
        "beamwidth_v_deg": 70,
        "gain_above_ground_dbi": 8.0,
    }
    return result


def design_discone(
    freq_hz: int,
    velocity_factor: float = 0.95,
    impedance_ohms: float = 50.0,
    conductor_diameter_m: float = 0.002,
    polarization: str = "vertical",
    material: str = "steel",
) -> dict[str, Any]:
    wavelength = SPEED_OF_LIGHT_MS / freq_hz
    disc_diameter = wavelength * 0.67
    cone_height = wavelength * 0.33
    cone_base_diameter = wavelength * 0.25
    cone_angle_deg = math.degrees(math.atan(cone_base_diameter / (2.0 * cone_height)))
    gap_mm = 2.0 + freq_hz / 500_000_000.0

    dims = AntennaDimensions(
        type="discone",
        freq_hz=freq_hz,
        wavelength_m=wavelength,
        half_wavelength_m=wavelength / 2.0,
        quarter_wavelength_m=wavelength / 4.0,
        velocity_factor=velocity_factor,
        material=material,
        conductor_diameter_m=conductor_diameter_m,
        element_length_m=disc_diameter,
        impedance_ohms=impedance_ohms,
        bandwidth_hz=freq_hz * 4.0,
        gain_dbi=1.8,
        swr_typical=2.0,
        polarization=polarization,
    )

    result = dims.to_dict()
    result["disc_diameter_mm"] = round(disc_diameter * 1000.0, 1)
    result["disc_diameter_in"] = round(disc_diameter * 39.3701, 2)
    result["cone_height_mm"] = round(cone_height * 1000.0, 1)
    result["cone_base_diameter_mm"] = round(cone_base_diameter * 1000.0, 1)
    result["cone_angle_deg"] = round(cone_angle_deg, 1)
    result["disc_cone_gap_mm"] = round(gap_mm, 1)
    result["bandwidth_ratio"] = "4:1 typical (up to 10:1 achieved)"
    result["radiation_pattern"] = {
        "pattern_type": "omnidirectional in azimuth",
        "elevation_max_deg": 0,
        "polarization": "vertical",
    }
    return result


DESIGNERS = {
    "dipole": design_dipole,
    "yagi": design_yagi,
    "loop": design_loop,
    "patch": design_patch,
    "discone": design_discone,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Antenna element design calculator")
    parser.add_argument("--type", choices=sorted(DESIGNERS), required=True, help="Antenna type")
    parser.add_argument("--freq", type=int, default=144_000_000, help="Center frequency in Hz")
    parser.add_argument("--polarization", type=str, choices=["vertical", "horizontal"], default="vertical")
    parser.add_argument("--impedance", type=float, default=50.0, help="Target impedance in ohms")
    parser.add_argument("--material", type=str, default="copper",
                        choices=["copper", "aluminum", "steel", "stainless_steel", "bare_wire", "insulated_wire", "pcb_fr4"])
    parser.add_argument("--gauge", type=int, default=14, choices=[6, 8, 10, 12, 14, 16, 18, 20, 22, 24],
                        help="AWG wire gauge for conductor diameter")
    parser.add_argument("--output-dir", type=str, default="/tmp/gludd-antenna-design", help="Output directory")
    args = parser.parse_args()

    velocity_factor = VELOCITY_FACTORS.get(args.material, 0.95)
    conductor_diameter_m = WIRE_GAUGE_MM.get(args.gauge, 1.628) / 1000.0

    designer = DESIGNERS[args.type]
    kwargs: dict[str, Any] = {
        "freq_hz": args.freq,
        "velocity_factor": velocity_factor,
        "impedance_ohms": args.impedance,
        "conductor_diameter_m": conductor_diameter_m,
        "polarization": args.polarization,
        "material": args.material,
    }

    try:
        result = designer(**kwargs)
        if isinstance(result, AntennaDimensions):
            result = result.to_dict()
    except Exception as exc:
        result = {"error": str(exc), "type": args.type, "freq_hz": args.freq}

    result["design_notes"] = [
        "All dimensions are for free-space. Reduce lengths by 2-5% for near-ground installation.",
        "Add a balun or choke at the feedpoint for coax-fed antennas.",
    ]
    if args.type == "yagi":
        result["design_notes"].append("Adjust element lengths and spacing during tuning for optimal SWR.")
    elif args.type == "patch":
        result["design_notes"].append("FR-4 substrate assumed. Adjust patch width for circular polarization if desired.")
    elif args.type == "discone":
        result["design_notes"].append("Gap between disc and cone is critical for broadband performance.")

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, "antenna_design.json")
    output = json.dumps(result, indent=2)
    try:
        with open(output_path, "w") as f:
            f.write(output)
    except OSError:
        pass

    print(output)


if __name__ == "__main__":
    main()
