"""Unit tests for chemistry and materials science knowledge modules.

Covers physical_chemistry, analytical_chemistry, and materials_science
modules — verifying TypedDict shapes, Enum values, data list integrity,
and function correctness with edge cases.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from general_ludd.physics.analytical_chemistry import (
    CALIBRATION_STANDARDS,
    CHROMATOGRAPHY_METHODS,
    COMMON_FRAGMENTS,
    IONIZATION_METHODS,
    RETENTION_INDEX_REFERENCES,
    SPECTROSCOPY_METHODS,
    CalibrationStandard,
    ChromatographyMethod,
    ChromatographyType,
    FragmentPattern,
    IonizationMethod,
    MassAnalyzer,
    MassSpecPeak,
    SpectroscopyMethod,
    SpectroscopyType,
    calibrate_instrument,
    compute_retention_index,
    identify_from_mass_spectrum,
)
from general_ludd.physics.materials_science import (
    CHARACTERIZATION_TECHNIQUES,
    CRYSTAL_STRUCTURES,
    MATERIALS_DB,
    CharacterizationMethod,
    CharacterizationTechnique,
    CrystalStructure,
    MaterialFamily,
    MaterialProperties,
    MaterialRequirement,
    compare_materials,
    get_material_properties,
    recommend_material,
)
from general_ludd.physics.physical_chemistry import (
    BASIS_SETS,
    BATTERY_TYPES,
    CATALYSTS,
    PHASE_TRANSITIONS,
    THERMODYNAMIC_DATA,
    BasisSet,
    BatteryChemistry,
    ElectrochemicalCell,
    F,
    KineticData,
    PhaseDiagramType,
    QuantumMethod,
    R,
    ThermodynamicData,
    compute_arrhenius,
    compute_equilibrium_constant,
    compute_potential,
    compute_rate,
)

# ============================================================================
# physical_chemistry — Enums
# ============================================================================


def test_quantum_method_enum_has_expected_members():
    expected = {
        "HARTREE_FOCK", "DFT", "MP2", "CCSD", "CCSD_T",
        "CASSCF", "CI", "SEMI_EMPIRICAL",
    }
    actual = {m.name for m in QuantumMethod}
    assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"


def test_quantum_method_values_are_lowercase():
    for member in QuantumMethod:
        assert member.value == member.value.lower()


def test_phase_diagram_type_enum_has_expected_members():
    expected = {"BINARY", "TERNARY", "P_T", "ELLINGHAM"}
    actual = {m.name for m in PhaseDiagramType}
    assert actual == expected


# ============================================================================
# physical_chemistry — TypedDicts
# ============================================================================


def test_thermodynamic_data_typeddict_fields():
    hints = get_type_hints(ThermodynamicData)
    for field in ("species", "delta_H_f_kJ_mol", "S_J_mol_K",
                   "delta_G_f_kJ_mol", "Cp_J_mol_K"):
        assert field in hints, f"ThermodynamicData missing field: {field}"


def test_kinetic_data_typeddict_fields():
    hints = get_type_hints(KineticData)
    for field in ("reaction", "rate_constant", "activation_energy_kJ_mol",
                   "pre_exponential_factor", "order"):
        assert field in hints, f"KineticData missing field: {field}"


def test_electrochemical_cell_typeddict_fields():
    hints = get_type_hints(ElectrochemicalCell)
    for field in ("name", "anode", "cathode", "electrolyte",
                   "cell_voltage_V", "energy_density_Wh_kg"):
        assert field in hints, f"ElectrochemicalCell missing field: {field}"


def test_battery_chemistry_typeddict_fields():
    hints = get_type_hints(BatteryChemistry)
    for field in ("name", "chemistry_type", "nominal_voltage_V",
                   "specific_energy_Wh_kg", "cycle_life", "applications"):
        assert field in hints, f"BatteryChemistry missing field: {field}"


def test_basis_set_typeddict_fields():
    hints = get_type_hints(BasisSet)
    for field in ("name", "family", "description", "typical_atoms"):
        assert field in hints, f"BasisSet missing field: {field}"


# ============================================================================
# physical_chemistry — Constants
# ============================================================================


def test_gas_constant_r():
    assert R == 8.314


def test_faraday_constant_f():
    assert F == 96485.33212


# ============================================================================
# physical_chemistry — Data integrity
# ============================================================================


def test_thermodynamic_data_is_list():
    assert isinstance(THERMODYNAMIC_DATA, list)


def test_thermodynamic_data_non_empty():
    assert len(THERMODYNAMIC_DATA) > 0, "THERMODYNAMIC_DATA should be populated"


def test_thermodynamic_data_entries_all_fields():
    expected_fields = {"species", "delta_H_f_kJ_mol", "S_J_mol_K",
                       "delta_G_f_kJ_mol", "Cp_J_mol_K"}
    for entry in THERMODYNAMIC_DATA:
        assert expected_fields.issubset(entry.keys()), (
            f"Missing fields in {entry.get('species', '?')}: "
            f"{expected_fields - set(entry.keys())}"
        )


def test_thermodynamic_data_species_unique():
    species = [e["species"] for e in THERMODYNAMIC_DATA]
    assert len(species) == len(set(species)), "Duplicate species in THERMODYNAMIC_DATA"


def test_phase_transitions_is_list():
    assert isinstance(PHASE_TRANSITIONS, list)


def test_phase_transitions_non_empty():
    assert len(PHASE_TRANSITIONS) > 0


def test_phase_transitions_entries_have_required_keys():
    for entry in PHASE_TRANSITIONS:
        for key in ("material", "transition", "T_K", "delta_H_kJ_mol"):
            assert key in entry, f"Phase transition entry missing key: {key}"


def test_basis_sets_is_list():
    assert isinstance(BASIS_SETS, list)


def test_basis_sets_non_empty():
    assert len(BASIS_SETS) > 0


def test_basis_sets_all_fields():
    for entry in BASIS_SETS:
        for key in ("name", "family", "description", "typical_atoms"):
            assert key in entry, f"Basis set entry missing key: {key}"


def test_basis_sets_names_unique():
    names = [e["name"] for e in BASIS_SETS]
    assert len(names) == len(set(names)), "Duplicate basis set names"


def test_catalysts_is_list():
    assert isinstance(CATALYSTS, list)


def test_catalysts_non_empty():
    assert len(CATALYSTS) > 0


def test_catalysts_entries_have_required_keys():
    for entry in CATALYSTS:
        for key in ("name", "type", "applications", "support"):
            assert key in entry, f"Catalyst entry missing key: {key}"


def test_battery_types_is_list():
    assert isinstance(BATTERY_TYPES, list)


def test_battery_types_non_empty():
    assert len(BATTERY_TYPES) > 0


def test_battery_types_all_fields():
    for entry in BATTERY_TYPES:
        for key in ("name", "chemistry_type", "nominal_voltage_V",
                    "specific_energy_Wh_kg", "cycle_life", "applications"):
            assert key in entry, f"Battery entry missing key: {key}"


def test_battery_types_names_unique():
    names = [e["name"] for e in BATTERY_TYPES]
    assert len(names) == len(set(names)), "Duplicate battery type names"


# ============================================================================
# physical_chemistry — compute_equilibrium_constant
# ============================================================================


def test_equilibrium_constant_exothermic():
    dG = -30000.0
    T = 298.15
    K = compute_equilibrium_constant(dG, T)
    assert K > 1.0, f"Exothermic reaction should have K > 1, got {K}"


def test_equilibrium_constant_endothermic():
    dG = 30000.0
    T = 298.15
    K = compute_equilibrium_constant(dG, T)
    assert K < 1.0, f"Endothermic reaction should have K < 1, got {K}"


def test_equilibrium_constant_dG_zero():
    K = compute_equilibrium_constant(0.0, 298.15)
    assert K == 1.0, f"dG=0 should give K=1, got {K}"


def test_equilibrium_constant_known_reaction():
    dG_H2O = -237180.0
    K = compute_equilibrium_constant(dG_H2O, 298.15)
    assert 1e40 < K < 1e42, f"K for H2O formation at 298K should be ~1e41, got {K}"


def test_equilibrium_constant_temperature_zero_raises():
    with pytest.raises(ValueError, match="Temperature must be positive"):
        compute_equilibrium_constant(-10000.0, 0.0)


def test_equilibrium_constant_negative_temperature_raises():
    with pytest.raises(ValueError, match="Temperature must be positive"):
        compute_equilibrium_constant(-10000.0, -10.0)


# ============================================================================
# physical_chemistry — compute_rate
# ============================================================================


def test_rate_first_order():
    rate = compute_rate(0.01, 1.5, 1)
    assert rate == 0.015, f"rate should be 0.015, got {rate}"


def test_rate_second_order():
    rate = compute_rate(0.01, 2.0, 2)
    assert rate == 0.04, f"rate should be 0.04, got {rate}"


def test_rate_zero_order():
    rate = compute_rate(5.0, 100.0, 0)
    assert rate == 5.0, f"Zero order rate should equal rate constant, got {rate}"


def test_rate_concentration_zero():
    rate = compute_rate(0.01, 0.0, 1)
    assert rate == 0.0, f"Rate should be zero when concentration is zero, got {rate}"


def test_rate_negative_order_raises():
    with pytest.raises(ValueError, match="Reaction order must be non-negative"):
        compute_rate(0.01, 1.0, -1)


# ============================================================================
# physical_chemistry — compute_potential (Nernst)
# ============================================================================


def test_potential_standard_conditions():
    E = compute_potential(1.10, [1.0])
    assert abs(E - 1.10) < 1e-6, f"Under standard conditions E ~ E0, got {E}"


def test_potential_dilute_half_cell():
    E = compute_potential(0.34, [0.01], n_electrons=2)
    assert E < 0.34, f"Dilute half-cell should have lower potential, got {E}"


def test_potential_concentrated_half_cell():
    E_low = compute_potential(0.34, [0.01], n_electrons=2)
    E_high = compute_potential(0.34, [1.0], n_electrons=2)
    assert E_low < E_high, "Higher concentration should give higher potential"


def test_potential_zero_electrons_raises():
    with pytest.raises(ValueError, match="Number of electrons must be positive"):
        compute_potential(1.0, [1.0], n_electrons=0)


def test_potential_negative_electrons_raises():
    with pytest.raises(ValueError, match="Number of electrons must be positive"):
        compute_potential(1.0, [1.0], n_electrons=-1)


def test_potential_empty_concentrations_raises():
    with pytest.raises(ValueError, match="Concentrations list cannot be empty"):
        compute_potential(1.0, [])


def test_potential_negative_concentration_raises():
    with pytest.raises(ValueError, match="All concentrations must be positive"):
        compute_potential(1.0, [-1.0])


def test_potential_zero_concentration_raises():
    with pytest.raises(ValueError, match="All concentrations must be positive"):
        compute_potential(1.0, [0.0])


# ============================================================================
# physical_chemistry — compute_arrhenius
# ============================================================================


def test_arrhenius_room_temperature():
    k = compute_arrhenius(1e10, 50000.0, 298.15)
    assert k > 0


def test_arrhenius_higher_temp_increases_rate():
    k_low = compute_arrhenius(1e10, 50000.0, 298.15)
    k_high = compute_arrhenius(1e10, 50000.0, 500.0)
    assert k_high > k_low, "Higher temperature should increase rate constant"


def test_arrhenius_zero_activation():
    k = compute_arrhenius(1e10, 0.0, 300.0)
    assert abs(k - 1e10) < 1e-3, f"Zero Ea should give k = A, got {k}"


def test_arrhenius_temperature_zero_raises():
    with pytest.raises(ValueError, match="Temperature must be positive"):
        compute_arrhenius(1e10, 50000.0, 0.0)


# ============================================================================
# analytical_chemistry — Enums
# ============================================================================


def test_ionization_method_enum_members():
    expected = {"EI", "ESI", "MALDI", "APCI", "APPI", "CI"}
    actual = {m.name for m in IonizationMethod}
    assert actual == expected


def test_mass_analyzer_enum_members():
    expected = {"QUADRUPOLE", "TOF", "ORBITRAP", "ION_TRAP", "FT_ICR", "SECTOR"}
    actual = {m.name for m in MassAnalyzer}
    assert actual == expected


def test_chromatography_type_enum_members():
    expected = {"GC", "HPLC", "UPLC", "IC", "SEC", "AFFINITY"}
    actual = {m.name for m in ChromatographyType}
    assert actual == expected


def test_spectroscopy_type_enum_members():
    expected = {"UV_VIS", "FLUORESCENCE", "AAS", "ICP_OES", "ICP_MS", "IR", "RAMAN", "NMR"}
    actual = {m.name for m in SpectroscopyType}
    assert actual == expected


# ============================================================================
# analytical_chemistry — TypedDicts
# ============================================================================


def test_mass_spec_peak_typeddict_fields():
    hints = get_type_hints(MassSpecPeak)
    for field in ("mz", "intensity", "assignment", "delta_ppm"):
        assert field in hints, f"MassSpecPeak missing field: {field}"


def test_chromatography_method_typeddict_fields():
    hints = get_type_hints(ChromatographyMethod)
    for field in ("name", "technique", "stationary_phase", "mobile_phase",
                   "detector", "typical_analytes"):
        assert field in hints, f"ChromatographyMethod missing field: {field}"


def test_spectroscopy_method_typeddict_fields():
    hints = get_type_hints(SpectroscopyMethod)
    for field in ("name", "technique", "wavelength_range_nm",
                   "detection_limit_ppb", "typical_elements"):
        assert field in hints, f"SpectroscopyMethod missing field: {field}"


def test_fragment_pattern_typeddict_fields():
    hints = get_type_hints(FragmentPattern)
    for field in ("name", "mass_shift", "formula", "common_source"):
        assert field in hints, f"FragmentPattern missing field: {field}"


def test_calibration_standard_typeddict_fields():
    hints = get_type_hints(CalibrationStandard)
    for field in ("name", "certified_value", "uncertainty", "unit", "matrix"):
        assert field in hints, f"CalibrationStandard missing field: {field}"


# ============================================================================
# analytical_chemistry — Data integrity
# ============================================================================


def test_ionization_methods_is_list():
    assert isinstance(IONIZATION_METHODS, list)


def test_ionization_methods_non_empty():
    assert len(IONIZATION_METHODS) > 0


def test_ionization_methods_entries_have_required_keys():
    for entry in IONIZATION_METHODS:
        for key in ("name", "hardness", "fragments", "mass_range", "typical_use"):
            assert key in entry, f"Ionization method missing key: {key}"


def test_common_fragments_is_list():
    assert isinstance(COMMON_FRAGMENTS, list)


def test_common_fragments_non_empty():
    assert len(COMMON_FRAGMENTS) > 0


def test_common_fragments_all_fields():
    for entry in COMMON_FRAGMENTS:
        for key in ("name", "mass_shift", "formula", "common_source"):
            assert key in entry, f"Fragment entry missing key: {key}"


def test_chromatography_methods_is_list():
    assert isinstance(CHROMATOGRAPHY_METHODS, list)


def test_chromatography_methods_non_empty():
    assert len(CHROMATOGRAPHY_METHODS) > 0


def test_spectroscopy_methods_is_list():
    assert isinstance(SPECTROSCOPY_METHODS, list)


def test_spectroscopy_methods_non_empty():
    assert len(SPECTROSCOPY_METHODS) > 0


def test_calibration_standards_is_list():
    assert isinstance(CALIBRATION_STANDARDS, list)


def test_calibration_standards_non_empty():
    assert len(CALIBRATION_STANDARDS) > 0


def test_retention_index_references_is_list():
    assert isinstance(RETENTION_INDEX_REFERENCES, list)


def test_retention_index_references_non_empty():
    assert len(RETENTION_INDEX_REFERENCES) > 0


def test_retention_index_references_entries_have_required_keys():
    for entry in RETENTION_INDEX_REFERENCES:
        for key in ("alkane", "carbon_number", "retention_index", "boiling_point_C"):
            assert key in entry, f"Retention index reference missing key: {key}"


# ============================================================================
# analytical_chemistry — identify_from_mass_spectrum
# ============================================================================


def test_identify_empty_peaks():
    result = identify_from_mass_spectrum([])
    assert result == {"matched_fragments": [], "match_count": 0}


def test_identify_methyl_loss():
    peaks: list[MassSpecPeak] = [
        {"mz": 15.0, "intensity": 100.0, "assignment": "?", "delta_ppm": 0.0},
    ]
    result = identify_from_mass_spectrum(peaks)
    assert "methyl loss" in result["matched_fragments"]


def test_identify_water_loss():
    peaks: list[MassSpecPeak] = [
        {"mz": 18.0, "intensity": 80.0, "assignment": "?", "delta_ppm": 0.0},
    ]
    result = identify_from_mass_spectrum(peaks)
    assert "water loss" in result["matched_fragments"]


def test_identify_no_match():
    peaks: list[MassSpecPeak] = [
        {"mz": 999.0, "intensity": 10.0, "assignment": "?", "delta_ppm": 0.0},
    ]
    result = identify_from_mass_spectrum(peaks)
    assert result["match_count"] == 0


def test_identify_multiple_peaks():
    peaks: list[MassSpecPeak] = [
        {"mz": 15.0, "intensity": 100.0, "assignment": "?", "delta_ppm": 0.0},
        {"mz": 28.0, "intensity": 50.0, "assignment": "?", "delta_ppm": 0.0},
        {"mz": 45.0, "intensity": 20.0, "assignment": "?", "delta_ppm": 0.0},
    ]
    result = identify_from_mass_spectrum(peaks)
    assert result["match_count"] == 3


# ============================================================================
# analytical_chemistry — compute_retention_index
# ============================================================================


def test_retention_index_midpoint():
    ri = compute_retention_index(5.0, 3.0, 8.0, 10)
    assert 1000 < ri < 1100, f"Expected RI ~1047, got {ri}"


def test_retention_index_at_lower_boundary():
    ri = compute_retention_index(3.0, 3.0, 8.0, 10)
    assert ri == 1000.0, f"At lower boundary RI should be 1000, got {ri}"


def test_retention_index_at_upper_boundary():
    ri = compute_retention_index(8.0, 3.0, 8.0, 10)
    assert ri == 1100.0, f"At upper boundary RI should be 1100, got {ri}"


def test_retention_index_decimal_times():
    ri = compute_retention_index(4.2, 3.0, 8.5, 10)
    assert 1000 < ri < 1100


def test_retention_index_carbon_one():
    ri = compute_retention_index(2.0, 1.5, 3.0, 1)
    assert ri > 100


def test_retention_index_outside_range_raises():
    with pytest.raises(ValueError, match="not between references"):
        compute_retention_index(1.0, 3.0, 8.0, 10)


def test_retention_index_negative_n_low_raises():
    with pytest.raises(ValueError, match="Carbon number must be >= 1"):
        compute_retention_index(5.0, 3.0, 8.0, 0)


def test_retention_index_zero_time_raises():
    with pytest.raises(ValueError, match="All retention times must be positive"):
        compute_retention_index(0.0, 3.0, 8.0, 10)


# ============================================================================
# analytical_chemistry — calibrate_instrument
# ============================================================================


def test_calibrate_single_standard():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 10.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
    ]
    result = calibrate_instrument(standards, [5.0])
    assert result["calibration_valid"]
    assert result["slope"] == 0.5


def test_calibrate_multiple_standards():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 1.0, "uncertainty": 0.01, "unit": "mg/L", "matrix": "water"},
        {"name": "std2", "certified_value": 5.0, "uncertainty": 0.05, "unit": "mg/L", "matrix": "water"},
        {"name": "std3", "certified_value": 10.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
    ]
    readings = [2.1, 9.8, 20.1]
    result = calibrate_instrument(standards, readings)
    assert result["calibration_valid"]
    assert 1.9 < result["slope"] < 2.1
    assert result["r_squared"] > 0.99


def test_calibrate_imperfect_linearity():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 1.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
        {"name": "std2", "certified_value": 3.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
        {"name": "std3", "certified_value": 5.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
    ]
    readings = [1.0, 4.0, 6.0]
    result = calibrate_instrument(standards, readings)
    assert not result["calibration_valid"]


def test_calibrate_single_zero_standard():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 0.0, "uncertainty": 0.0, "unit": "mg/L", "matrix": "water"},
    ]
    result = calibrate_instrument(standards, [0.0])
    assert not result["calibration_valid"]
    assert result["slope"] == 0.0


def test_calibrate_empty_raises():
    with pytest.raises(ValueError, match="Standards and readings must be non-empty"):
        calibrate_instrument([], [])


def test_calibrate_length_mismatch_raises():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 1.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
    ]
    with pytest.raises(ValueError, match="Length mismatch"):
        calibrate_instrument(standards, [1.0, 2.0])


def test_calibrate_all_same_x_raises():
    standards: list[CalibrationStandard] = [
        {"name": "std1", "certified_value": 5.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
        {"name": "std2", "certified_value": 5.0, "uncertainty": 0.1, "unit": "mg/L", "matrix": "water"},
    ]
    with pytest.raises(ValueError, match="All standards have the same certified value"):
        calibrate_instrument(standards, [1.0, 2.0])


# ============================================================================
# materials_science — Enums
# ============================================================================


def test_material_family_enum_members():
    expected = {"METAL", "CERAMIC", "POLYMER", "COMPOSITE", "SEMICONDUCTOR", "GLASS", "NATURAL"}
    actual = {m.name for m in MaterialFamily}
    assert actual == expected


def test_characterization_technique_enum_members():
    expected = {"XRD", "SEM", "TEM", "AFM", "DSC", "TGA", "XPS", "EDS", "FTIR",
                "RAMAN", "NANOINDENTATION", "TENSILE_TEST"}
    actual = {m.name for m in CharacterizationTechnique}
    assert actual == expected


def test_crystal_structure_enum_members():
    expected = {"FCC", "BCC", "HCP", "DIAMOND", "PEROVSKITE", "AMORPHOUS", "SPINEL", "ZINCBLENDE"}
    actual = {m.name for m in CrystalStructure}
    assert actual == expected


# ============================================================================
# materials_science — TypedDicts
# ============================================================================


def test_material_properties_typeddict_fields():
    hints = get_type_hints(MaterialProperties)
    required = {
        "name", "family", "density_g_cm3", "youngs_modulus_GPa",
        "tensile_strength_MPa", "hardness", "thermal_expansion_10_6_K",
        "thermal_conductivity_W_mK", "melting_point_C", "electrical_conductivity_S_m",
    }
    assert required.issubset(hints), f"Missing: {required - set(hints)}"


def test_characterization_method_typeddict_fields():
    hints = get_type_hints(CharacterizationMethod)
    for field in ("name", "technique", "probe", "signal_measured",
                   "spatial_resolution", "typical_information"):
        assert field in hints, f"CharacterizationMethod missing field: {field}"


def test_material_requirement_typeddict_total_false():
    ri_hints = get_type_hints(MaterialRequirement)
    for field in ("min_tensile_strength_MPa", "max_density_g_cm3",
                  "preferred_family", "max_service_temperature_C"):
        assert field in ri_hints, f"MaterialRequirement missing field: {field}"


# ============================================================================
# materials_science — Data integrity
# ============================================================================


def test_materials_db_is_list():
    assert isinstance(MATERIALS_DB, list)


def test_materials_db_non_empty():
    assert len(MATERIALS_DB) > 0


def test_materials_db_names_unique():
    names = [m["name"] for m in MATERIALS_DB]
    assert len(names) == len(set(names)), "Duplicate material names"


def test_materials_db_all_families_represented():
    families = {m["family"] for m in MATERIALS_DB}
    for fam in ("metal", "ceramic", "polymer", "composite", "semiconductor", "glass", "natural"):
        assert fam in families, f"Material family {fam} not represented in MATERIALS_DB"


def test_materials_db_entries_have_all_fields():
    expected_fields = {
        "name", "family", "density_g_cm3", "youngs_modulus_GPa",
        "tensile_strength_MPa", "hardness", "thermal_expansion_10_6_K",
        "thermal_conductivity_W_mK", "melting_point_C", "electrical_conductivity_S_m",
    }
    for entry in MATERIALS_DB:
        missing = expected_fields - set(entry.keys())
        assert not missing, f"Material {entry.get('name', '?')} missing fields: {missing}"


def test_characterization_techniques_is_list():
    assert isinstance(CHARACTERIZATION_TECHNIQUES, list)


def test_characterization_techniques_non_empty():
    assert len(CHARACTERIZATION_TECHNIQUES) > 0


def test_crystal_structures_is_list():
    assert isinstance(CRYSTAL_STRUCTURES, list)


def test_crystal_structures_non_empty():
    assert len(CRYSTAL_STRUCTURES) > 0


# ============================================================================
# materials_science — get_material_properties
# ============================================================================


def test_get_material_properties_found():
    props = get_material_properties("Aluminum 6061-T6")
    assert props is not None
    assert props["name"] == "Aluminum 6061-T6"
    assert props["family"] == "metal"
    assert props["density_g_cm3"] == 2.70


def test_get_material_properties_not_found():
    props = get_material_properties("Nonexistentium")
    assert props is None


def test_get_material_properties_case_sensitive():
    props = get_material_properties("aluminum 6061-t6")
    assert props is None, "Material lookup is case-sensitive"


# ============================================================================
# materials_science — compare_materials
# ============================================================================


def test_compare_materials_all_found():
    result = compare_materials(["Aluminum 6061-T6", "Stainless Steel 316L"])
    assert len(result) == 2
    assert result[0]["found"] is True
    assert result[1]["found"] is True


def test_compare_materials_partial_found():
    result = compare_materials(["Aluminum 6061-T6", "FakeMaterial"])
    assert len(result) == 2
    assert result[0]["found"] is True
    assert result[1]["found"] is False
    assert result[1]["density_g_cm3"] is None


def test_compare_materials_empty_list():
    result = compare_materials([])
    assert result == []


def test_compare_materials_output_keys():
    result = compare_materials(["Copper C11000"])
    entry = result[0]
    for key in ("name", "found", "density_g_cm3", "tensile_strength_MPa",
                "youngs_modulus_GPa", "thermal_expansion_10_6_K"):
        assert key in entry, f"Compare output missing key: {key}"


# ============================================================================
# materials_science — recommend_material
# ============================================================================


def test_recommend_no_constraints():
    result = recommend_material({})
    assert len(result) == len(MATERIALS_DB)


def test_recommend_min_tensile_strength():
    result = recommend_material({"min_tensile_strength_MPa": 500.0})
    assert len(result) > 0
    for r in result:
        assert r["tensile_strength_MPa"] >= 500.0
    # Titanium should be top
    assert "Titanium" in result[0]["name"]


def test_recommend_family_filter():
    result = recommend_material({"preferred_family": "ceramic"})
    assert len(result) > 0
    for r in result:
        assert r["family"] == "ceramic"


def test_recommend_max_density():
    result = recommend_material({"max_density_g_cm3": 3.0})
    for r in result:
        assert r["density_g_cm3"] <= 3.0


def test_recommend_combined_constraints():
    result = recommend_material({
        "min_tensile_strength_MPa": 200.0,
        "max_density_g_cm3": 5.0,
        "preferred_family": "metal",
    })
    for r in result:
        assert r["tensile_strength_MPa"] >= 200.0
        assert r["density_g_cm3"] <= 5.0
        assert r["family"] == "metal"


def test_recommend_max_service_temperature():
    result = recommend_material({"max_service_temperature_C": 1000.0})
    for r in result:
        assert r["melting_point_C"] * 0.8 >= 1000.0


def test_recommend_sorted_by_strength():
    result = recommend_material({"preferred_family": "metal"})
    strengths = [float(r["tensile_strength_MPa"]) for r in result]
    assert strengths == sorted(strengths, reverse=True)


def test_recommend_no_matches():
    result = recommend_material({
        "min_tensile_strength_MPa": 10000.0,
    })
    assert result == []


def test_recommend_thermal_conductivity_filter():
    result = recommend_material({"min_thermal_conductivity_W_mK": 100.0})
    for r in result:
        assert get_material_properties(r["name"])["thermal_conductivity_W_mK"] >= 100.0
