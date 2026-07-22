"""Recognized static-gap tests for materials science helpers."""

from __future__ import annotations

import pytest

from general_ludd.physics.materials_science import (
    calculate_specific_strength,
    compare_materials,
    compute_corrosion_rate,
    compute_dielectric_energy_density,
    compute_rule_of_mixtures,
    get_material_properties,
    recommend_material,
)


def test_material_lookup_and_comparison_report_missing_materials() -> None:
    aluminum = get_material_properties("Aluminum 6061-T6")
    comparison = compare_materials(["Aluminum 6061-T6", "Unknown alloy"])

    assert aluminum is not None
    assert aluminum["family"] == "metal"
    assert comparison[0]["found"] is True
    assert comparison[1] == {
        "name": "Unknown alloy",
        "found": False,
        "density_g_cm3": None,
        "tensile_strength_MPa": None,
        "youngs_modulus_GPa": None,
        "thermal_expansion_10_6_K": None,
    }


def test_recommend_material_filters_and_ranks_candidates() -> None:
    recommendations = recommend_material(
        {
            "preferred_family": "metal",
            "min_tensile_strength_MPa": 900,
            "max_density_g_cm3": 8.5,
            "max_service_temperature_C": 1000,
        }
    )

    assert [material["name"] for material in recommendations][:2] == [
        "Inconel 718",
        "Titanium Ti-6Al-4V",
    ]


def test_material_calculations_cover_mechanical_and_electrical_paths() -> None:
    assert calculate_specific_strength("Titanium Ti-6Al-4V") == pytest.approx(
        214.45,
        abs=0.01,
    )
    assert compute_rule_of_mixtures(0.6, 230.0, 3.5, orientation_factor=1.0) == pytest.approx(139.4)
    assert compute_dielectric_energy_density(2000.0, 10.0) == pytest.approx(0.8854)


def test_corrosion_rate_returns_zero_for_invalid_exposure_inputs() -> None:
    assert compute_corrosion_rate(0.5, area_cm2=0.0, time_hours=24.0, density_g_cm3=7.85) == 0.0
    assert compute_corrosion_rate(0.5, area_cm2=10.0, time_hours=0.0, density_g_cm3=7.85) == 0.0
