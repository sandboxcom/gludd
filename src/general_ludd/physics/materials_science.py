"""Materials science knowledge module.

Material properties, families, characterization techniques,
and material recommendation utilities.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MaterialFamily(StrEnum):
    METAL = "metal"
    CERAMIC = "ceramic"
    POLYMER = "polymer"
    COMPOSITE = "composite"
    SEMICONDUCTOR = "semiconductor"
    GLASS = "glass"
    NATURAL = "natural"


class CharacterizationTechnique(StrEnum):
    XRD = "xrd"
    SEM = "sem"
    TEM = "tem"
    AFM = "afm"
    DSC = "dsc"
    TGA = "tga"
    XPS = "xps"
    EDS = "eds"
    FTIR = "ftir"
    RAMAN = "raman"
    NANOINDENTATION = "nanoindentation"
    TENSILE_TEST = "tensile_test"


class CrystalStructure(StrEnum):
    FCC = "fcc"
    BCC = "bcc"
    HCP = "hcp"
    DIAMOND = "diamond"
    PEROVSKITE = "perovskite"
    AMORPHOUS = "amorphous"
    SPINEL = "spinel"
    ZINCBLENDE = "zincblende"


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class MaterialProperties(TypedDict):
    name: str
    family: str
    density_g_cm3: float
    youngs_modulus_GPa: float
    tensile_strength_MPa: float
    hardness: str
    thermal_expansion_10_6_K: float
    thermal_conductivity_W_mK: float
    melting_point_C: float
    electrical_conductivity_S_m: float


class CharacterizationMethod(TypedDict):
    name: str
    technique: str
    probe: str
    signal_measured: str
    spatial_resolution: str
    typical_information: str


class MaterialRequirement(TypedDict, total=False):
    min_tensile_strength_MPa: float
    max_density_g_cm3: float
    min_thermal_conductivity_W_mK: float
    max_thermal_expansion_10_6_K: float
    max_service_temperature_C: float
    preferred_family: str


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

MATERIALS_DB: list[MaterialProperties] = [
    {
        "name": "Steel AISI 1045",
        "family": "metal",
        "density_g_cm3": 7.85,
        "youngs_modulus_GPa": 200,
        "tensile_strength_MPa": 565,
        "hardness": "170 HB",
        "thermal_expansion_10_6_K": 11.5,
        "thermal_conductivity_W_mK": 50,
        "melting_point_C": 1460,
        "electrical_conductivity_S_m": 5.0e6,
    },
    {
        "name": "Stainless Steel 316L",
        "family": "metal",
        "density_g_cm3": 8.00,
        "youngs_modulus_GPa": 193,
        "tensile_strength_MPa": 560,
        "hardness": "180 HB",
        "thermal_expansion_10_6_K": 16.0,
        "thermal_conductivity_W_mK": 15,
        "melting_point_C": 1400,
        "electrical_conductivity_S_m": 1.35e6,
    },
    {
        "name": "Aluminum 6061-T6",
        "family": "metal",
        "density_g_cm3": 2.70,
        "youngs_modulus_GPa": 69,
        "tensile_strength_MPa": 310,
        "hardness": "95 HB",
        "thermal_expansion_10_6_K": 23.6,
        "thermal_conductivity_W_mK": 167,
        "melting_point_C": 585,
        "electrical_conductivity_S_m": 2.5e7,
    },
    {
        "name": "Titanium Ti-6Al-4V",
        "family": "metal",
        "density_g_cm3": 4.43,
        "youngs_modulus_GPa": 114,
        "tensile_strength_MPa": 950,
        "hardness": "350 HB",
        "thermal_expansion_10_6_K": 8.6,
        "thermal_conductivity_W_mK": 7,
        "melting_point_C": 1660,
        "electrical_conductivity_S_m": 5.8e5,
    },
    {
        "name": "Copper C11000",
        "family": "metal",
        "density_g_cm3": 8.94,
        "youngs_modulus_GPa": 117,
        "tensile_strength_MPa": 220,
        "hardness": "45 HB",
        "thermal_expansion_10_6_K": 17.0,
        "thermal_conductivity_W_mK": 390,
        "melting_point_C": 1085,
        "electrical_conductivity_S_m": 5.8e7,
    },
    {
        "name": "Alumina (Al2O3)",
        "family": "ceramic",
        "density_g_cm3": 3.95,
        "youngs_modulus_GPa": 370,
        "tensile_strength_MPa": 300,
        "hardness": "1500 HV",
        "thermal_expansion_10_6_K": 8.0,
        "thermal_conductivity_W_mK": 30,
        "melting_point_C": 2072,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Silicon Carbide (SiC)",
        "family": "ceramic",
        "density_g_cm3": 3.21,
        "youngs_modulus_GPa": 450,
        "tensile_strength_MPa": 400,
        "hardness": "2800 HV",
        "thermal_expansion_10_6_K": 4.0,
        "thermal_conductivity_W_mK": 120,
        "melting_point_C": 2730,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Polyethylene (HDPE)",
        "family": "polymer",
        "density_g_cm3": 0.95,
        "youngs_modulus_GPa": 1.0,
        "tensile_strength_MPa": 30,
        "hardness": "65 Shore D",
        "thermal_expansion_10_6_K": 120.0,
        "thermal_conductivity_W_mK": 0.45,
        "melting_point_C": 135,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Polycarbonate (PC)",
        "family": "polymer",
        "density_g_cm3": 1.20,
        "youngs_modulus_GPa": 2.4,
        "tensile_strength_MPa": 65,
        "hardness": "75 Rockwell M",
        "thermal_expansion_10_6_K": 68.0,
        "thermal_conductivity_W_mK": 0.20,
        "melting_point_C": 155,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Carbon Fiber Epoxy",
        "family": "composite",
        "density_g_cm3": 1.55,
        "youngs_modulus_GPa": 140,
        "tensile_strength_MPa": 1500,
        "hardness": "80 Rockwell M",
        "thermal_expansion_10_6_K": -0.1,
        "thermal_conductivity_W_mK": 5,
        "melting_point_C": 350,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Silicon (single crystal)",
        "family": "semiconductor",
        "density_g_cm3": 2.33,
        "youngs_modulus_GPa": 130,
        "tensile_strength_MPa": 350,
        "hardness": "1150 HK",
        "thermal_expansion_10_6_K": 2.6,
        "thermal_conductivity_W_mK": 150,
        "melting_point_C": 1414,
        "electrical_conductivity_S_m": 1.0e-6,
    },
    {
        "name": "Gallium Arsenide (GaAs)",
        "family": "semiconductor",
        "density_g_cm3": 5.32,
        "youngs_modulus_GPa": 86,
        "tensile_strength_MPa": 200,
        "hardness": "750 HK",
        "thermal_expansion_10_6_K": 5.7,
        "thermal_conductivity_W_mK": 55,
        "melting_point_C": 1238,
        "electrical_conductivity_S_m": 1.0e-7,
    },
    {
        "name": "Soda-lime Glass",
        "family": "glass",
        "density_g_cm3": 2.50,
        "youngs_modulus_GPa": 70,
        "tensile_strength_MPa": 50,
        "hardness": "550 HK",
        "thermal_expansion_10_6_K": 8.5,
        "thermal_conductivity_W_mK": 1.0,
        "melting_point_C": 600,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Oak (Quercus)",
        "family": "natural",
        "density_g_cm3": 0.75,
        "youngs_modulus_GPa": 11,
        "tensile_strength_MPa": 90,
        "hardness": "6000 N Janka",
        "thermal_expansion_10_6_K": 30.0,
        "thermal_conductivity_W_mK": 0.17,
        "melting_point_C": 250,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Borosilicate Glass",
        "family": "glass",
        "density_g_cm3": 2.23,
        "youngs_modulus_GPa": 63,
        "tensile_strength_MPa": 70,
        "hardness": "480 HK",
        "thermal_expansion_10_6_K": 3.3,
        "thermal_conductivity_W_mK": 1.2,
        "melting_point_C": 820,
        "electrical_conductivity_S_m": 0.0,
    },
]

CHARACTERIZATION_TECHNIQUES: list[CharacterizationMethod] = [
    {"name": "XRD", "technique": "XRD", "probe": "X-rays (Cu K-alpha)", "signal_measured": "diffraction pattern", "spatial_resolution": "mm to um", "typical_information": "crystal structure, phase identification, lattice parameters"},
    {"name": "SEM", "technique": "SEM", "probe": "electron beam (1-30 keV)", "signal_measured": "secondary/backscattered electrons", "spatial_resolution": "1-20 nm", "typical_information": "surface morphology, grain size, fracture surface"},
    {"name": "TEM", "technique": "TEM", "probe": "electron beam (80-300 keV)", "signal_measured": "transmitted electrons", "spatial_resolution": "0.05-0.2 nm", "typical_information": "atomic structure, dislocations, interfaces, nanoparticles"},
    {"name": "AFM", "technique": "AFM", "probe": "cantilever tip", "signal_measured": "tip deflection", "spatial_resolution": "0.1 nm vertical, 1 nm lateral", "typical_information": "surface topography, roughness, mechanical properties"},
    {"name": "DSC", "technique": "DSC", "probe": "heat flow", "signal_measured": "heat flow vs temperature", "spatial_resolution": "bulk (mg sample)", "typical_information": "Tg, Tm, crystallization, heat capacity, purity"},
    {"name": "TGA", "technique": "TGA", "probe": "temperature ramp", "signal_measured": "mass change vs temperature", "spatial_resolution": "bulk (mg sample)", "typical_information": "thermal stability, composition, filler content, decomposition"},
    {"name": "XPS", "technique": "XPS", "probe": "X-rays (Al K-alpha)", "signal_measured": "photoelectron kinetic energy", "spatial_resolution": "10 um - 1 mm", "typical_information": "elemental composition, chemical state, surface 1-10 nm"},
    {"name": "EDS", "technique": "EDS", "probe": "electron beam", "signal_measured": "characteristic X-rays", "spatial_resolution": "1 um", "typical_information": "elemental composition, elemental mapping"},
    {"name": "Nanoindentation", "technique": "NANOINDENTATION", "probe": "diamond tip", "signal_measured": "load vs displacement", "spatial_resolution": "nm depth, um lateral", "typical_information": "hardness, elastic modulus, creep"},
    {"name": "Tensile Test", "technique": "TENSILE_TEST", "probe": "uniaxial load", "signal_measured": "stress vs strain", "spatial_resolution": "macroscopic", "typical_information": "yield strength, UTS, elongation, Young's modulus"},
]

CRYSTAL_STRUCTURES: list[dict[str, object]] = [
    {"name": "Copper", "structure": "FCC", "lattice_parameter_A": 3.615, "atoms_per_cell": 4},
    {"name": "Iron (alpha)", "structure": "BCC", "lattice_parameter_A": 2.866, "atoms_per_cell": 2},
    {"name": "Titanium", "structure": "HCP", "lattice_parameter_A": 2.951, "atoms_per_cell": 6},
    {"name": "Diamond", "structure": "DIAMOND", "lattice_parameter_A": 3.567, "atoms_per_cell": 8},
    {"name": "BaTiO3", "structure": "PEROVSKITE", "lattice_parameter_A": 4.006, "atoms_per_cell": 5},
    {"name": "SiO2 (glass)", "structure": "AMORPHOUS", "lattice_parameter_A": 0.0, "atoms_per_cell": 0},
    {"name": "MgAl2O4", "structure": "SPINEL", "lattice_parameter_A": 8.083, "atoms_per_cell": 56},
    {"name": "GaAs", "structure": "ZINCBLENDE", "lattice_parameter_A": 5.653, "atoms_per_cell": 8},
]


# ---------------------------------------------------------------------------
# Computation functions
# ---------------------------------------------------------------------------

def get_material_properties(material: str) -> MaterialProperties | None:
    """Look up a material's properties by name.

    Args:
        material: Material name (case-sensitive, e.g. 'Aluminum 6061-T6').

    Returns:
        MaterialProperties dict if found, None otherwise.
    """
    for m in MATERIALS_DB:
        if m["name"] == material:
            return m
    return None


def compare_materials(materials_list: list[str]) -> list[dict[str, object]]:
    """Compare key properties across multiple materials.

    Args:
        materials_list: List of material names to compare.

    Returns:
        List of dicts with name, density, tensile_strength, youngs_modulus,
        and thermal_expansion for each material found. Missing materials
        are included with null values.
    """
    results: list[dict[str, object]] = []
    for name in materials_list:
        props = get_material_properties(name)
        if props is None:
            results.append({
                "name": name,
                "found": False,
                "density_g_cm3": None,
                "tensile_strength_MPa": None,
                "youngs_modulus_GPa": None,
                "thermal_expansion_10_6_K": None,
            })
        else:
            results.append({
                "name": name,
                "found": True,
                "density_g_cm3": props["density_g_cm3"],
                "tensile_strength_MPa": props["tensile_strength_MPa"],
                "youngs_modulus_GPa": props["youngs_modulus_GPa"],
                "thermal_expansion_10_6_K": props["thermal_expansion_10_6_K"],
            })
    return results


def recommend_material(requirements: MaterialRequirement) -> list[dict[str, object]]:
    """Recommend materials that satisfy the given requirements.

    Filters the materials database against specified constraints and
    returns ranked candidates sorted by tensile strength descending.

    Args:
        requirements: MaterialRequirement dict with optional constraints:
            min_tensile_strength_MPa, max_density_g_cm3,
            min_thermal_conductivity_W_mK, max_thermal_expansion_10_6_K,
            max_service_temperature_C (using 80% of melting point as proxy),
            preferred_family.

    Returns:
        List of matching MaterialProperties as dicts, sorted by tensile strength.
    """
    matches: list[dict[str, object]] = []

    min_strength = requirements.get("min_tensile_strength_MPa")
    max_density = requirements.get("max_density_g_cm3")
    min_thermal_cond = requirements.get("min_thermal_conductivity_W_mK")
    max_thermal_exp = requirements.get("max_thermal_expansion_10_6_K")
    max_temp = requirements.get("max_service_temperature_C")
    family = requirements.get("preferred_family")

    for mat in MATERIALS_DB:
        if family is not None and mat["family"] != family:
            continue
        if min_strength is not None and mat["tensile_strength_MPa"] < min_strength:
            continue
        if max_density is not None and mat["density_g_cm3"] > max_density:
            continue
        if min_thermal_cond is not None and mat["thermal_conductivity_W_mK"] < min_thermal_cond:
            continue
        if max_thermal_exp is not None and mat["thermal_expansion_10_6_K"] > max_thermal_exp:
            continue
        if max_temp is not None and mat["melting_point_C"] * 0.8 < max_temp:
            continue
        matches.append({
            "name": mat["name"],
            "family": mat["family"],
            "density_g_cm3": mat["density_g_cm3"],
            "tensile_strength_MPa": mat["tensile_strength_MPa"],
            "youngs_modulus_GPa": mat["youngs_modulus_GPa"],
            "melting_point_C": mat["melting_point_C"],
        })

    matches.sort(key=lambda m: float(m["tensile_strength_MPa"]), reverse=True)
    return matches
