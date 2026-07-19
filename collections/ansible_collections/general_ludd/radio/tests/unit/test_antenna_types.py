"""Tests for antenna_types module."""

from __future__ import annotations

import pytest

from plugins.module_utils.antenna_types import (
    ANTENNA_TYPES,
    antenna_info,
    types_for_frequency,
    design_antenna,
    radiation_pattern,
)


def test_antenna_types_is_non_empty():
    assert isinstance(ANTENNA_TYPES, list)
    assert len(ANTENNA_TYPES) >= 15


def test_every_antenna_has_required_keys():
    for a in ANTENNA_TYPES:
        assert "type" in a
        assert "display" in a
        assert "gain_dbi" in a
        assert isinstance(a["gain_dbi"], (int, float))
        assert "impedance_ohms" in a
        assert "polarization" in a
        assert "pattern" in a


def test_antenna_types_are_unique():
    names = [a["type"] for a in ANTENNA_TYPES]
    assert len(names) == len(set(names))


def test_antenna_info_known():
    info = antenna_info("dipole_half_wave")
    assert info is not None
    assert info["gain_dbi"] == 2.15
    assert info["impedance_ohms"] == 73.0


def test_antenna_info_yagi():
    info = antenna_info("yagi_3el")
    assert info is not None
    assert info["gain_dbi"] >= 6.0
    assert "directional" in info["pattern"]
    assert "design_freq_min_mhz" in info


def test_antenna_info_nonexistent():
    assert antenna_info("nonexistent_antenna") is None


def test_types_for_frequency_2m():
    result = types_for_frequency(146_000_000)
    assert len(result) > 0
    assert "dipole_half_wave" in result
    assert "vertical_quarter_wave" in result
    assert "yagi_3el" in result


def test_types_for_frequency_hf_40m():
    result = types_for_frequency(7_200_000)
    assert "dipole_half_wave" in result
    assert "vertical_quarter_wave" in result
    assert "loop_full_wave" in result


def test_types_for_frequency_ghz():
    result = types_for_frequency(5_800_000_000)
    assert "patch" in result
    assert "parabolic_reflector" in result


def test_types_for_frequency_vlf():
    result = types_for_frequency(100_000)
    assert result == []


def test_design_antenna_dipole():
    result = design_antenna("dipole_half_wave", 14.200)
    assert result["type"] == "dipole_half_wave"
    assert "element_length_m" in result
    assert result["element_length_m"] == pytest.approx(10.07, rel=0.05)
    assert result["element_length_feet"] == pytest.approx(32.96, rel=0.05)
    assert result["impedance_ohms"] == 73.0


def test_design_antenna_vertical():
    result = design_antenna("vertical_quarter_wave", 146.0)
    assert result["radiator_length_m"] == pytest.approx(0.488, rel=0.05)
    assert result["impedance_ohms"] == 36.0


def test_design_antenna_yagi_3el():
    result = design_antenna("yagi_3el", 144.2)
    assert "reflector_length_m" in result
    assert "driven_element_length_m" in result
    assert "director_length_m" in result
    assert result["reflector_length_m"] > result["director_length_m"]


def test_design_antenna_yagi_5el():
    result = design_antenna("yagi_5el", 50.125)
    assert len([k for k in result if "director" in k and "length" in k]) >= 3
    assert result["boom_length_m"] > 0


def test_design_antenna_loop():
    result = design_antenna("loop_full_wave", 28.400)
    assert result["circumference_m"] == pytest.approx(10.77, rel=0.05)
    assert result["impedance_ohms"] == 100.0


def test_design_antenna_ground_plane():
    result = design_antenna("ground_plane", 146.0)
    assert result["radial_angle_deg"] == 135
    assert result["impedance_ohms"] == 50.0


def test_design_antenna_dish():
    result = design_antenna("parabolic_reflector", 10489.0)
    assert "gain_dbi" in result
    assert "beamwidth_deg" in result
    assert result["gain_dbi"] > 30.0


def test_design_antenna_patch():
    result = design_antenna("patch", 2450.0)
    assert "patch_width_mm" in result
    assert "patch_length_mm" in result
    assert result["patch_width_mm"] > 0
    assert result["patch_length_mm"] > 0


def test_design_antenna_helical():
    result = design_antenna("helical_axial", 2400.0)
    assert result["turns"] == 8
    assert result["circumference_m"] > 0
    assert result["gain_dbi_approx"] > 8.0


def test_design_antenna_beverage():
    result = design_antenna("beverage", 7.150)
    assert result["length_wavelengths"] == 2.0
    assert result["impedance_ohms"] == 450.0


def test_design_antenna_discone():
    result = design_antenna("discone", 100.0)
    assert result["cone_angle_deg"] == 45.0
    assert result["impedance_ohms"] == 50.0


def test_design_antenna_unknown():
    result = design_antenna("flux_capacitor", 100.0)
    assert "error" in result


def test_radiation_pattern_omnidirectional():
    result = radiation_pattern("vertical_quarter_wave", elevation_deg=0.0, azimuth_deg=0.0)
    assert result["gain_dbi"] == pytest.approx(0.0, abs=0.5)


def test_radiation_pattern_omnidirectional_overhead_null():
    result = radiation_pattern("vertical_quarter_wave", elevation_deg=90.0, azimuth_deg=0.0)
    assert result["gain_dbi"] == pytest.approx(0.0, abs=0.1)


def test_radiation_pattern_dipole_broadside():
    result = radiation_pattern("dipole_half_wave", elevation_deg=0.0, azimuth_deg=0.0)
    assert result["gain_dbi"] == pytest.approx(2.15, abs=0.5)


def test_radiation_pattern_dipole_endfire():
    result = radiation_pattern("dipole_half_wave", elevation_deg=0.0, azimuth_deg=90.0)
    assert result["gain_dbi"] == pytest.approx(0.0, abs=0.1)


def test_radiation_pattern_yagi_on_axis():
    result = radiation_pattern("yagi_3el", elevation_deg=0.0, azimuth_deg=0.0)
    assert result["gain_dbi"] >= 5.0


def test_radiation_pattern_yagi_off_axis():
    result = radiation_pattern("yagi_3el", elevation_deg=0.0, azimuth_deg=60.0)
    assert result["gain_dbi"] < 3.0


def test_radiation_pattern_unknown_antenna():
    result = radiation_pattern("flux_capacitor", 0.0, 0.0)
    assert "error" in result


def test_radiation_pattern_returns_documented_keys():
    result = radiation_pattern("dipole_half_wave", 30.0, 45.0)
    for key in ("type", "elevation_deg", "azimuth_deg", "gain_dbi", "nominal_gain_dbi", "pattern_type"):
        assert key in result


def test_types_for_frequency_returns_list():
    result = types_for_frequency(146_520_000)
    assert isinstance(result, list)
