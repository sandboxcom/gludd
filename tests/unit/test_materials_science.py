"""Tests for materials science knowledge module."""

from __future__ import annotations

from general_ludd.physics.materials_science import (
    BIOMATERIAL_DB,
    CERAMIC_DB,
    COMPOSITE_DB,
    CORROSION_DB,
    ELECTRONIC_MATERIAL_DB,
    MAGNETIC_MATERIAL_DB,
    MATERIALS_DB,
    NANOMATERIAL_DB,
    OPTICAL_MATERIAL_DB,
    POLYMER_DB,
    CompositeType,
    CorrosionType,
    MagneticMaterialType,
    MaterialFamily,
    NanomaterialType,
    OpticalProperty,
    calculate_specific_strength,
    compare_materials,
    compute_archard_wear_volume,
    compute_band_gap_from_wavelength,
    compute_biocompatibility_score,
    compute_conductivity_from_resistivity,
    compute_corrosion_rate,
    compute_dielectric_energy_density,
    compute_galvanic_corrosion_risk,
    compute_hall_petch_strength,
    compute_max_energy_product,
    compute_reflectivity_normal,
    compute_refractive_index_contrast,
    compute_rule_of_mixtures,
    compute_surface_to_volume_ratio,
    compute_thermal_shock_resistance,
    filter_polymers,
    get_material_properties,
    get_polymer_ranking,
    recommend_material,
)

# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

def test_material_family_includes_nanomaterial() -> None:
    assert MaterialFamily.NANOMATERIAL == "nanomaterial"


def test_material_family_includes_biomaterial() -> None:
    assert MaterialFamily.BIOMATERIAL == "biomaterial"


def test_nanomaterial_type_enum_values() -> None:
    assert NanomaterialType.NANOTUBE == "nanotube"
    assert NanomaterialType.QUANTUM_DOT == "quantum_dot"
    assert NanomaterialType.NANOSHEET == "nanosheet"


def test_magnetic_type_enum_values() -> None:
    assert MagneticMaterialType.FERROMAGNETIC == "ferromagnetic"
    assert MagneticMaterialType.DIAMAGNETIC == "diamagnetic"


def test_optical_property_enum() -> None:
    assert OpticalProperty.BIREFRINGENT == "birefringent"


def test_composite_type_enum() -> None:
    assert CompositeType.METAL_MATRIX == "metal_matrix"
    assert CompositeType.CERAMIC_MATRIX == "ceramic_matrix"


def test_corrosion_type_enum() -> None:
    assert CorrosionType.GALVANIC == "galvanic"
    assert CorrosionType.STRESS_CORROSION_CRACKING == "stress_corrosion_cracking"


# ---------------------------------------------------------------------------
# MATERIALS_DB expansion tests
# ---------------------------------------------------------------------------

def test_materials_db_has_polymers() -> None:
    polymers = [m for m in MATERIALS_DB if m["family"] == "polymer"]
    names = {m["name"] for m in polymers}
    assert "PEEK (Polyether Ether Ketone)" in names
    assert "PET (Polyethylene Terephthalate)" in names
    assert "PTFE (Teflon)" in names
    assert "Nylon 6-6 (PA66)" in names


def test_materials_db_has_nanomaterials() -> None:
    nanos = [m for m in MATERIALS_DB if m["family"] == "nanomaterial"]
    names = {m["name"] for m in nanos}
    assert "Graphene (single layer)" in names
    assert "MWCNT (Multi-wall Carbon Nanotube)" in names


def test_materials_db_has_biomaterials() -> None:
    bios = [m for m in MATERIALS_DB if m["family"] == "biomaterial"]
    names = {m["name"] for m in bios}
    assert "Hydroxyapatite (HA)" in names
    assert "Collagen (Type I, bovine)" in names


def test_materials_db_has_metallurgy_entries() -> None:
    assert get_material_properties("Inconel 718") is not None
    assert get_material_properties("Brass (Cu-30Zn)") is not None


def test_materials_db_has_gfrp() -> None:
    gfrp = get_material_properties("GFRP (E-glass/Epoxy)")
    assert gfrp is not None
    assert gfrp["density_g_cm3"] == 1.85


def test_materials_db_has_gan() -> None:
    gan = get_material_properties("Gallium Nitride (GaN)")
    assert gan is not None
    assert gan["melting_point_C"] == 2500
    assert gan["tensile_strength_MPa"] == 400


def test_materials_db_has_graphene_high_strength() -> None:
    g = get_material_properties("Graphene (single layer)")
    assert g is not None
    assert g["tensile_strength_MPa"] > 100000


# ---------------------------------------------------------------------------
# Reference dataset tests
# ---------------------------------------------------------------------------

def test_polymer_db_has_pla_biodegradable() -> None:
    pla = [p for p in POLYMER_DB if p["name"] == "PLA (Polylactic Acid)"]
    assert len(pla) == 1
    assert pla[0]["biodegradable"] is True


def test_ceramic_db_zirconia_toughness() -> None:
    zro2 = [c for c in CERAMIC_DB if "Zirconia" in str(c["name"])]
    assert len(zro2) == 1
    assert zro2[0]["fracture_toughness_MPa_m05"] >= 8.0


def test_nanomaterial_db_includes_quantum_dots() -> None:
    qds = [n for n in NANOMATERIAL_DB if "Quantum Dot" in str(n["name"])]
    assert len(qds) == 1
    assert qds[0]["nanomaterial_type"] == "quantum_dot"


def test_biomaterial_db_collagen() -> None:
    col = [b for b in BIOMATERIAL_DB if "Collagen" in str(b["name"])]
    assert len(col) == 1
    assert col[0]["biocompatibility"] == "excellent (native ECM)"


def test_electronic_material_db_batio3_dielectric() -> None:
    bt = [e for e in ELECTRONIC_MATERIAL_DB if "BaTiO3" in str(e["name"])]
    assert len(bt) == 1
    assert bt[0]["dielectric_constant"] >= 1000


def test_magnetic_material_db_ndfeb() -> None:
    nd = [m for m in MAGNETIC_MATERIAL_DB if "NdFeB" in str(m["name"])]
    assert len(nd) == 1
    assert nd[0]["max_energy_product_kJ_m3"] > 300


def test_optical_material_db_fused_silica() -> None:
    fs = [o for o in OPTICAL_MATERIAL_DB if o["name"] == "Fused Silica"]
    assert len(fs) == 1
    assert 1.45 < fs[0]["refractive_index"] < 1.47


def test_composite_db_includes_cmc() -> None:
    cmc = [c for c in COMPOSITE_DB if "SiC/SiC" in str(c["name"])]
    assert len(cmc) == 1
    assert cmc[0]["composite_type"] == "ceramic_matrix"


def test_corrosion_db_has_galvanic() -> None:
    gal = [c for c in CORROSION_DB if c["corrosion_type"] == "galvanic"]
    assert len(gal) == 1
    assert gal[0]["environment"] == "3.5% NaCl"


# ---------------------------------------------------------------------------
# Function tests
# ---------------------------------------------------------------------------

def test_calculate_specific_strength_titanium() -> None:
    ss = calculate_specific_strength("Titanium Ti-6Al-4V")
    assert ss is not None
    assert 200 < ss < 250  # ~214 kN*m/kg


def test_calculate_specific_strength_missing() -> None:
    assert calculate_specific_strength("Unobtanium") is None


def test_polymer_ranking_by_tensile_strength() -> None:
    ranked = get_polymer_ranking("tensile_strength_MPa")
    assert len(ranked) >= 5
    assert ranked[0]["name"] == "PEEK (Polyether Ether Ketone)"


def test_filter_polymers_thermoplastic() -> None:
    tp = filter_polymers(thermoplastic_only=True)
    assert "PEEK (Polyether Ether Ketone)" in tp
    assert "Polyurethane (PU)" not in tp


def test_filter_polymers_biodegradable() -> None:
    bd = filter_polymers(biodegradable_only=True)
    assert "PLA (Polylactic Acid)" in bd
    assert len(bd) == 1


def test_rule_of_mixtures_unidirectional() -> None:
    ec = compute_rule_of_mixtures(0.6, 230, 3.5, orientation_factor=1.0)
    assert 130 < ec < 150


def test_rule_of_mixtures_random_2d() -> None:
    ec = compute_rule_of_mixtures(0.6, 230, 3.5, orientation_factor=0.375)
    assert 45 < ec < 60


def test_corrosion_rate_calculation() -> None:
    cr = compute_corrosion_rate(0.5, 10.0, 720.0, 7.85)
    assert 0.5 < cr < 1.5


def test_corrosion_rate_zero_area() -> None:
    assert compute_corrosion_rate(0.5, 0, 100, 7.85) == 0.0


def test_galvanic_corrosion_risk_low() -> None:
    result = compute_galvanic_corrosion_risk(-0.60, -0.65)
    assert result["risk_level"] == "low"


def test_galvanic_corrosion_risk_severe() -> None:
    result = compute_galvanic_corrosion_risk(-1.6, 0.15)
    assert result["risk_level"] == "severe"
    assert result["potential_difference_V"] > 0.5


def test_hall_petch_strength() -> None:
    sigma = compute_hall_petch_strength(10.0, 600.0, 50.0)
    assert 200 < sigma < 250  # sigma0 + 600/sqrt(10) ~ 240


def test_hall_petch_zero_grain() -> None:
    sigma = compute_hall_petch_strength(0, 600.0, 50.0)
    assert sigma == 50.0


def test_archard_wear_volume() -> None:
    v = compute_archard_wear_volume(100, 1000, 2e9, 1e-3)
    assert 4e-8 < v < 6e-8


def test_archard_wear_zero_hardness() -> None:
    assert compute_archard_wear_volume(100, 1000, 0, 1e-4) == 0.0


def test_band_gap_from_wavelength_visible() -> None:
    eg = compute_band_gap_from_wavelength(550)
    assert 2.2 < eg < 2.3  # 1240/550 ~ 2.25 eV


def test_band_gap_zero_wavelength() -> None:
    assert compute_band_gap_from_wavelength(0) == 0.0


def test_surface_to_volume_ratio_sphere() -> None:
    sv = compute_surface_to_volume_ratio(10.0, 6.0)
    assert abs(sv - 0.6) < 1e-9


def test_dielectric_energy_density_batio3() -> None:
    u = compute_dielectric_energy_density(2000, 10)
    assert 0.8 < u < 1.0


def test_conductivity_from_resistivity() -> None:
    sigma = compute_conductivity_from_resistivity(1000)
    assert abs(sigma - 0.001) < 1e-9


def test_max_energy_product() -> None:
    bh = compute_max_energy_product(1.3, 955)
    assert 310 < bh < 315


def test_refractive_index_contrast() -> None:
    delta = compute_refractive_index_contrast(1.458, 2.403)
    assert 0.39 < delta < 0.41


def test_refractive_index_contrast_zero() -> None:
    assert compute_refractive_index_contrast(0, 0) == 0.0


def test_reflectivity_normal_fused_silica_air() -> None:
    r = compute_reflectivity_normal(1.0, 1.458)
    assert 0.03 < r < 0.04  # ~3.5%


def test_thermal_shock_resistance_alumina() -> None:
    r = compute_thermal_shock_resistance(300, 370, 8.0, 30)
    assert 2.5 < r < 3.5


def test_thermal_shock_zero_expansion() -> None:
    assert compute_thermal_shock_resistance(100, 200, 0, 50) == 0.0


def test_biocompatibility_score_exact_match() -> None:
    score = compute_biocompatibility_score(18.0, match_bone_modulus=True)
    assert 9.9 < score < 10.1  # near-perfect


def test_biocompatibility_score_poor_match() -> None:
    score = compute_biocompatibility_score(200, match_bone_modulus=True)
    assert score < 5.0  # large modulus mismatch


def test_biocompatibility_score_with_degradation() -> None:
    score = compute_biocompatibility_score(20, degradation_rate_months=0.5)
    assert score < 8.0


# ---------------------------------------------------------------------------
# Existing function regression tests
# ---------------------------------------------------------------------------

def test_get_material_found() -> None:
    props = get_material_properties("Aluminum 6061-T6")
    assert props is not None
    assert props["density_g_cm3"] == 2.70


def test_get_material_not_found() -> None:
    assert get_material_properties("Nonexistent") is None


def test_compare_materials() -> None:
    result = compare_materials(["Steel AISI 1045", "No Such Material"])
    assert len(result) == 2
    assert result[0]["found"] is True
    assert result[1]["found"] is False


def test_recommend_material_by_family() -> None:
    recs = recommend_material({"preferred_family": "polymer", "min_tensile_strength_MPa": 50})
    names = {r["name"] for r in recs}
    assert "PEEK (Polyether Ether Ketone)" in names
    assert "Polyethylene (HDPE)" not in names  # only 30 MPa


def test_recommend_material_by_density() -> None:
    recs = recommend_material({"max_density_g_cm3": 2.0, "min_tensile_strength_MPa": 500})
    names = {r["name"] for r in recs}
    assert "Carbon Fiber Epoxy" in names


def test_material_family_count() -> None:
    families = {m["family"] for m in MATERIALS_DB}
    assert "nanomaterial" in families
    assert "biomaterial" in families
    assert len(families) >= 9
