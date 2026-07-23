"""Mapper-visible tests for the materials science physics module."""

from __future__ import annotations

import pytest

from general_ludd.physics.materials_science import (
    calculate_specific_strength,
    compute_archard_wear_volume,
    compute_band_gap_from_wavelength,
    compute_biocompatibility_score,
    compute_corrosion_rate,
    compute_galvanic_corrosion_risk,
    compute_reflectivity_normal,
    compute_rule_of_mixtures,
    compute_surface_to_volume_ratio,
    get_material_properties,
    recommend_material,
)


def test_material_lookup_and_recommendation_cover_high_strength_composites() -> None:
    carbon_epoxy = get_material_properties("Carbon Fiber Epoxy")
    recommendations = recommend_material({
        "max_density_g_cm3": 2.0,
        "min_tensile_strength_MPa": 900.0,
    })

    assert carbon_epoxy is not None
    assert carbon_epoxy["family"] == "composite"
    assert any(item["name"] == "Carbon Fiber Epoxy" for item in recommendations)


def test_specific_strength_returns_none_for_unknown_materials() -> None:
    assert calculate_specific_strength("No Such Alloy") is None
    assert calculate_specific_strength("Titanium Ti-6Al-4V") == pytest.approx(214.4469, rel=1e-4)


def test_composite_and_nanoparticle_helpers_cover_nominal_values() -> None:
    assert compute_rule_of_mixtures(0.6, 230.0, 3.5, orientation_factor=1.0) == pytest.approx(139.4)
    assert compute_surface_to_volume_ratio(20.0) == pytest.approx(0.3)
    assert compute_band_gap_from_wavelength(620.0) == pytest.approx(2.0)


def test_corrosion_helpers_cover_zero_and_risk_boundaries() -> None:
    assert compute_corrosion_rate(1.0, 0.0, 24.0, 7.85) == 0.0
    assert compute_galvanic_corrosion_risk(-0.85, -0.05) == {
        "potential_difference_V": 0.8,
        "risk_level": "severe",
    }


def test_optical_wear_and_biocompatibility_helpers_return_bounded_values() -> None:
    assert compute_reflectivity_normal(1.0, 1.5) == pytest.approx(0.04)
    assert compute_archard_wear_volume(100.0, 1000.0, 2.0e9, 1.0e-3) == pytest.approx(5.0e-8)
    assert compute_biocompatibility_score(200.0, degradation_rate_months=0.5, match_bone_modulus=True) == 0.0
