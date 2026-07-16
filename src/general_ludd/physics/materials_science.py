"""Materials science knowledge module.

Material properties, families, characterization techniques,
and material recommendation utilities.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict, cast

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
    NANOMATERIAL = "nanomaterial"
    BIOMATERIAL = "biomaterial"


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


class NanomaterialType(StrEnum):
    NANOPARTICLE = "nanoparticle"
    NANOTUBE = "nanotube"
    NANOWIRE = "nanowire"
    QUANTUM_DOT = "quantum_dot"
    NANOFILM = "nanofilm"
    NANOSHEET = "nanosheet"


class MagneticMaterialType(StrEnum):
    FERROMAGNETIC = "ferromagnetic"
    FERRIMAGNETIC = "ferrimagnetic"
    PARAMAGNETIC = "paramagnetic"
    DIAMAGNETIC = "diamagnetic"
    ANTIFERROMAGNETIC = "antiferromagnetic"


class OpticalProperty(StrEnum):
    TRANSPARENT = "transparent"
    TRANSLUCENT = "translucent"
    OPAQUE = "opaque"
    BIREFRINGENT = "birefringent"
    PHOTOLUMINESCENT = "photoluminescent"


class CompositeType(StrEnum):
    FIBER_REINFORCED = "fiber_reinforced"
    PARTICULATE = "particulate"
    LAMINAR = "laminar"
    METAL_MATRIX = "metal_matrix"
    CERAMIC_MATRIX = "ceramic_matrix"
    POLYMER_MATRIX = "polymer_matrix"


class CorrosionType(StrEnum):
    UNIFORM = "uniform"
    GALVANIC = "galvanic"
    CREVICE = "crevice"
    PITTING = "pitting"
    INTERGRANULAR = "intergranular"
    STRESS_CORROSION_CRACKING = "stress_corrosion_cracking"
    EROSION = "erosion"
    HYDROGEN_EMBRITTLEMENT = "hydrogen_embrittlement"


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


class PolymerProperties(TypedDict, total=False):
    name: str
    glass_transition_C: float
    melting_point_C: float
    density_g_cm3: float
    tensile_strength_MPa: float
    elongation_percent: float
    is_thermoplastic: bool
    is_thermoset: bool
    is_elastomer: bool
    biodegradable: bool


class CeramicProperties(TypedDict, total=False):
    name: str
    density_g_cm3: float
    youngs_modulus_GPa: float
    fracture_toughness_MPa_m05: float
    compressive_strength_MPa: float
    thermal_expansion_10_6_K: float
    thermal_conductivity_W_mK: float


class NanomaterialProperties(TypedDict, total=False):
    name: str
    nanomaterial_type: str
    particle_size_nm: float
    surface_area_m2_g: float
    band_gap_eV: float
    morphology: str


class BiomaterialProperties(TypedDict, total=False):
    name: str
    density_g_cm3: float
    youngs_modulus_GPa: float
    biocompatibility: str
    degradation_rate: str
    sterilization_method: str


class ElectronicMaterialProperties(TypedDict, total=False):
    name: str
    band_gap_eV: float
    electron_mobility_cm2_Vs: float
    resistivity_ohm_cm: float
    dielectric_constant: float
    breakdown_field_MV_m: float


class MagneticMaterialProperties(TypedDict, total=False):
    name: str
    magnetic_type: str
    saturation_magnetization_T: float
    coercivity_kA_m: float
    curie_temperature_C: float
    max_energy_product_kJ_m3: float


class OpticalMaterialProperties(TypedDict, total=False):
    name: str
    refractive_index: float
    transmission_range_um: str
    band_gap_eV: float
    optical_type: str


class CompositeProperties(TypedDict, total=False):
    name: str
    composite_type: str
    matrix: str
    reinforcement: str
    fiber_volume_fraction: float
    density_g_cm3: float
    tensile_strength_MPa: float


class CorrosionData(TypedDict, total=False):
    name: str
    environment: str
    corrosion_type: str
    corrosion_rate_mmpy: float
    protection_method: str
    galvanic_potential_V: float


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
    # -- Polymers (expanded) --
    {
        "name": "PET (Polyethylene Terephthalate)",
        "family": "polymer",
        "density_g_cm3": 1.38,
        "youngs_modulus_GPa": 3.0,
        "tensile_strength_MPa": 55,
        "hardness": "85 Rockwell M",
        "thermal_expansion_10_6_K": 65.0,
        "thermal_conductivity_W_mK": 0.24,
        "melting_point_C": 260,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "PTFE (Teflon)",
        "family": "polymer",
        "density_g_cm3": 2.20,
        "youngs_modulus_GPa": 0.5,
        "tensile_strength_MPa": 25,
        "hardness": "55 Shore D",
        "thermal_expansion_10_6_K": 100.0,
        "thermal_conductivity_W_mK": 0.25,
        "melting_point_C": 327,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "PEEK (Polyether Ether Ketone)",
        "family": "polymer",
        "density_g_cm3": 1.32,
        "youngs_modulus_GPa": 3.6,
        "tensile_strength_MPa": 100,
        "hardness": "90 Rockwell M",
        "thermal_expansion_10_6_K": 47.0,
        "thermal_conductivity_W_mK": 0.25,
        "melting_point_C": 343,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Nylon 6-6 (PA66)",
        "family": "polymer",
        "density_g_cm3": 1.14,
        "youngs_modulus_GPa": 2.8,
        "tensile_strength_MPa": 80,
        "hardness": "80 Rockwell M",
        "thermal_expansion_10_6_K": 80.0,
        "thermal_conductivity_W_mK": 0.25,
        "melting_point_C": 260,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Ceramics (expanded) --
    {
        "name": "Zirconia (ZrO2, 3Y-TZP)",
        "family": "ceramic",
        "density_g_cm3": 6.05,
        "youngs_modulus_GPa": 210,
        "tensile_strength_MPa": 600,
        "hardness": "1300 HV",
        "thermal_expansion_10_6_K": 10.5,
        "thermal_conductivity_W_mK": 2.5,
        "melting_point_C": 2715,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Silicon Nitride (Si3N4)",
        "family": "ceramic",
        "density_g_cm3": 3.20,
        "youngs_modulus_GPa": 310,
        "tensile_strength_MPa": 800,
        "hardness": "1600 HV",
        "thermal_expansion_10_6_K": 3.2,
        "thermal_conductivity_W_mK": 27,
        "melting_point_C": 1900,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Nanomaterials --
    {
        "name": "MWCNT (Multi-wall Carbon Nanotube)",
        "family": "nanomaterial",
        "density_g_cm3": 1.80,
        "youngs_modulus_GPa": 1000,
        "tensile_strength_MPa": 63000,
        "hardness": "N/A",
        "thermal_expansion_10_6_K": -0.5,
        "thermal_conductivity_W_mK": 3000,
        "melting_point_C": 3652,
        "electrical_conductivity_S_m": 1.0e5,
    },
    {
        "name": "Graphene (single layer)",
        "family": "nanomaterial",
        "density_g_cm3": 2.20,
        "youngs_modulus_GPa": 1000,
        "tensile_strength_MPa": 130000,
        "hardness": "N/A",
        "thermal_expansion_10_6_K": -8.0,
        "thermal_conductivity_W_mK": 5000,
        "melting_point_C": 4510,
        "electrical_conductivity_S_m": 1.0e8,
    },
    {
        "name": "TiO2 Nanoparticles (Anatase)",
        "family": "nanomaterial",
        "density_g_cm3": 3.90,
        "youngs_modulus_GPa": 230,
        "tensile_strength_MPa": 200,
        "hardness": "N/A",
        "thermal_expansion_10_6_K": 9.0,
        "thermal_conductivity_W_mK": 8,
        "melting_point_C": 1843,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Biomaterials --
    {
        "name": "Hydroxyapatite (HA)",
        "family": "biomaterial",
        "density_g_cm3": 3.16,
        "youngs_modulus_GPa": 80,
        "tensile_strength_MPa": 40,
        "hardness": "500 HV",
        "thermal_expansion_10_6_K": 13.0,
        "thermal_conductivity_W_mK": 1.25,
        "melting_point_C": 1614,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "PLGA (85:15 copolymer)",
        "family": "biomaterial",
        "density_g_cm3": 1.30,
        "youngs_modulus_GPa": 2.0,
        "tensile_strength_MPa": 45,
        "hardness": "N/A",
        "thermal_expansion_10_6_K": 70.0,
        "thermal_conductivity_W_mK": 0.20,
        "melting_point_C": 145,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Collagen (Type I, bovine)",
        "family": "biomaterial",
        "density_g_cm3": 1.05,
        "youngs_modulus_GPa": 0.05,
        "tensile_strength_MPa": 5,
        "hardness": "N/A",
        "thermal_expansion_10_6_K": 100.0,
        "thermal_conductivity_W_mK": 0.50,
        "melting_point_C": 40,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Electronic materials --
    {
        "name": "Gallium Nitride (GaN)",
        "family": "semiconductor",
        "density_g_cm3": 6.15,
        "youngs_modulus_GPa": 295,
        "tensile_strength_MPa": 400,
        "hardness": "1500 HK",
        "thermal_expansion_10_6_K": 5.6,
        "thermal_conductivity_W_mK": 130,
        "melting_point_C": 2500,
        "electrical_conductivity_S_m": 1.0e-6,
    },
    {
        "name": "ITO (Indium Tin Oxide)",
        "family": "semiconductor",
        "density_g_cm3": 7.14,
        "youngs_modulus_GPa": 116,
        "tensile_strength_MPa": 100,
        "hardness": "700 HK",
        "thermal_expansion_10_6_K": 8.5,
        "thermal_conductivity_W_mK": 10,
        "melting_point_C": 1800,
        "electrical_conductivity_S_m": 1.0e4,
    },
    # -- Magnetic materials --
    {
        "name": "NdFeB Magnet (N42)",
        "family": "metal",
        "density_g_cm3": 7.50,
        "youngs_modulus_GPa": 160,
        "tensile_strength_MPa": 80,
        "hardness": "570 HV",
        "thermal_expansion_10_6_K": 5.0,
        "thermal_conductivity_W_mK": 8,
        "melting_point_C": 1135,
        "electrical_conductivity_S_m": 7.0e5,
    },
    # -- Optical materials --
    {
        "name": "Fused Silica (SiO2)",
        "family": "glass",
        "density_g_cm3": 2.20,
        "youngs_modulus_GPa": 72,
        "tensile_strength_MPa": 50,
        "hardness": "600 HK",
        "thermal_expansion_10_6_K": 0.55,
        "thermal_conductivity_W_mK": 1.4,
        "melting_point_C": 1665,
        "electrical_conductivity_S_m": 0.0,
    },
    {
        "name": "Sapphire (Al2O3 single crystal)",
        "family": "ceramic",
        "density_g_cm3": 3.98,
        "youngs_modulus_GPa": 400,
        "tensile_strength_MPa": 400,
        "hardness": "2000 HK",
        "thermal_expansion_10_6_K": 5.3,
        "thermal_conductivity_W_mK": 35,
        "melting_point_C": 2053,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Composites --
    {
        "name": "GFRP (E-glass/Epoxy)",
        "family": "composite",
        "density_g_cm3": 1.85,
        "youngs_modulus_GPa": 40,
        "tensile_strength_MPa": 1000,
        "hardness": "80 Barcol",
        "thermal_expansion_10_6_K": 10.0,
        "thermal_conductivity_W_mK": 0.3,
        "melting_point_C": 200,
        "electrical_conductivity_S_m": 0.0,
    },
    # -- Metallurgy --
    {
        "name": "Inconel 718",
        "family": "metal",
        "density_g_cm3": 8.19,
        "youngs_modulus_GPa": 205,
        "tensile_strength_MPa": 1375,
        "hardness": "380 HB",
        "thermal_expansion_10_6_K": 13.0,
        "thermal_conductivity_W_mK": 11.4,
        "melting_point_C": 1336,
        "electrical_conductivity_S_m": 8.0e5,
    },
    {
        "name": "Brass (Cu-30Zn)",
        "family": "metal",
        "density_g_cm3": 8.53,
        "youngs_modulus_GPa": 105,
        "tensile_strength_MPa": 350,
        "hardness": "80 HB",
        "thermal_expansion_10_6_K": 20.0,
        "thermal_conductivity_W_mK": 120,
        "melting_point_C": 920,
        "electrical_conductivity_S_m": 1.5e7,
    },
]

CHARACTERIZATION_TECHNIQUES: list[CharacterizationMethod] = [
    {
        "name": "XRD",
        "technique": "XRD",
        "probe": "X-rays (Cu K-alpha)",
        "signal_measured": "diffraction pattern",
        "spatial_resolution": "mm to um",
        "typical_information": "crystal structure, phase identification, lattice parameters",
    },
    {
        "name": "SEM",
        "technique": "SEM",
        "probe": "electron beam (1-30 keV)",
        "signal_measured": "secondary/backscattered electrons",
        "spatial_resolution": "1-20 nm",
        "typical_information": "surface morphology, grain size, fracture surface",
    },
    {
        "name": "TEM",
        "technique": "TEM",
        "probe": "electron beam (80-300 keV)",
        "signal_measured": "transmitted electrons",
        "spatial_resolution": "0.05-0.2 nm",
        "typical_information": "atomic structure, dislocations, interfaces, nanoparticles",
    },
    {
        "name": "AFM",
        "technique": "AFM",
        "probe": "cantilever tip",
        "signal_measured": "tip deflection",
        "spatial_resolution": "0.1 nm vertical, 1 nm lateral",
        "typical_information": "surface topography, roughness, mechanical properties",
    },
    {
        "name": "DSC",
        "technique": "DSC",
        "probe": "heat flow",
        "signal_measured": "heat flow vs temperature",
        "spatial_resolution": "bulk (mg sample)",
        "typical_information": "Tg, Tm, crystallization, heat capacity, purity",
    },
    {
        "name": "TGA",
        "technique": "TGA",
        "probe": "temperature ramp",
        "signal_measured": "mass change vs temperature",
        "spatial_resolution": "bulk (mg sample)",
        "typical_information": "thermal stability, composition, filler content, decomposition",
    },
    {
        "name": "XPS",
        "technique": "XPS",
        "probe": "X-rays (Al K-alpha)",
        "signal_measured": "photoelectron kinetic energy",
        "spatial_resolution": "10 um - 1 mm",
        "typical_information": "elemental composition, chemical state, surface 1-10 nm",
    },
    {
        "name": "EDS",
        "technique": "EDS",
        "probe": "electron beam",
        "signal_measured": "characteristic X-rays",
        "spatial_resolution": "1 um",
        "typical_information": "elemental composition, elemental mapping",
    },
    {
        "name": "Nanoindentation",
        "technique": "NANOINDENTATION",
        "probe": "diamond tip",
        "signal_measured": "load vs displacement",
        "spatial_resolution": "nm depth, um lateral",
        "typical_information": "hardness, elastic modulus, creep",
    },
    {
        "name": "Tensile Test",
        "technique": "TENSILE_TEST",
        "probe": "uniaxial load",
        "signal_measured": "stress vs strain",
        "spatial_resolution": "macroscopic",
        "typical_information": "yield strength, UTS, elongation, Young's modulus",
    },
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
# Expanded reference datasets
# ---------------------------------------------------------------------------

POLYMER_DB: list[dict[str, object]] = [
    {
        "name": "Polyethylene (HDPE)",
        "glass_transition_C": -110,
        "melting_point_C": 135,
        "density_g_cm3": 0.95,
        "tensile_strength_MPa": 30,
        "elongation_percent": 600,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "Polycarbonate (PC)",
        "glass_transition_C": 147,
        "melting_point_C": 155,
        "density_g_cm3": 1.20,
        "tensile_strength_MPa": 65,
        "elongation_percent": 110,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "PET (Polyethylene Terephthalate)",
        "glass_transition_C": 75,
        "melting_point_C": 260,
        "density_g_cm3": 1.38,
        "tensile_strength_MPa": 55,
        "elongation_percent": 70,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "PTFE (Teflon)",
        "glass_transition_C": -97,
        "melting_point_C": 327,
        "density_g_cm3": 2.20,
        "tensile_strength_MPa": 25,
        "elongation_percent": 300,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "PEEK (Polyether Ether Ketone)",
        "glass_transition_C": 143,
        "melting_point_C": 343,
        "density_g_cm3": 1.32,
        "tensile_strength_MPa": 100,
        "elongation_percent": 50,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "Nylon 6-6 (PA66)",
        "glass_transition_C": 50,
        "melting_point_C": 260,
        "density_g_cm3": 1.14,
        "tensile_strength_MPa": 80,
        "elongation_percent": 60,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": False,
    },
    {
        "name": "PLA (Polylactic Acid)",
        "glass_transition_C": 60,
        "melting_point_C": 175,
        "density_g_cm3": 1.24,
        "tensile_strength_MPa": 50,
        "elongation_percent": 5,
        "is_thermoplastic": True,
        "is_thermoset": False,
        "is_elastomer": False,
        "biodegradable": True,
    },
    {
        "name": "Polyurethane (PU)",
        "glass_transition_C": -40,
        "melting_point_C": 180,
        "density_g_cm3": 1.20,
        "tensile_strength_MPa": 40,
        "elongation_percent": 500,
        "is_thermoplastic": False,
        "is_thermoset": False,
        "is_elastomer": True,
        "biodegradable": False,
    },
]

CERAMIC_DB: list[dict[str, object]] = [
    {
        "name": "Alumina (Al2O3)",
        "density_g_cm3": 3.95,
        "youngs_modulus_GPa": 370,
        "fracture_toughness_MPa_m05": 4.0,
        "compressive_strength_MPa": 2500,
        "thermal_expansion_10_6_K": 8.0,
        "thermal_conductivity_W_mK": 30,
    },
    {
        "name": "Silicon Carbide (SiC)",
        "density_g_cm3": 3.21,
        "youngs_modulus_GPa": 450,
        "fracture_toughness_MPa_m05": 4.5,
        "compressive_strength_MPa": 3900,
        "thermal_expansion_10_6_K": 4.0,
        "thermal_conductivity_W_mK": 120,
    },
    {
        "name": "Zirconia (ZrO2, 3Y-TZP)",
        "density_g_cm3": 6.05,
        "youngs_modulus_GPa": 210,
        "fracture_toughness_MPa_m05": 8.0,
        "compressive_strength_MPa": 2500,
        "thermal_expansion_10_6_K": 10.5,
        "thermal_conductivity_W_mK": 2.5,
    },
    {
        "name": "Silicon Nitride (Si3N4)",
        "density_g_cm3": 3.20,
        "youngs_modulus_GPa": 310,
        "fracture_toughness_MPa_m05": 6.0,
        "compressive_strength_MPa": 3500,
        "thermal_expansion_10_6_K": 3.2,
        "thermal_conductivity_W_mK": 27,
    },
    {
        "name": "Sapphire (Al2O3 single crystal)",
        "density_g_cm3": 3.98,
        "youngs_modulus_GPa": 400,
        "fracture_toughness_MPa_m05": 3.5,
        "compressive_strength_MPa": 3000,
        "thermal_expansion_10_6_K": 5.3,
        "thermal_conductivity_W_mK": 35,
    },
]

NANOMATERIAL_DB: list[dict[str, object]] = [
    {
        "name": "MWCNT",
        "nanomaterial_type": "nanotube",
        "particle_size_nm": 15.0,
        "surface_area_m2_g": 250,
        "band_gap_eV": 0.0,
        "morphology": "tubular, multi-walled",
    },
    {
        "name": "Graphene nanoplatelets",
        "nanomaterial_type": "nanosheet",
        "particle_size_nm": 5.0,
        "surface_area_m2_g": 500,
        "band_gap_eV": 0.0,
        "morphology": "platelet, 1-10 layers",
    },
    {
        "name": "TiO2 Nanoparticles (Anatase)",
        "nanomaterial_type": "nanoparticle",
        "particle_size_nm": 25.0,
        "surface_area_m2_g": 50,
        "band_gap_eV": 3.2,
        "morphology": "spherical",
    },
    {
        "name": "Gold Nanoparticles (AuNP)",
        "nanomaterial_type": "nanoparticle",
        "particle_size_nm": 10.0,
        "surface_area_m2_g": 30,
        "band_gap_eV": 0.0,
        "morphology": "spherical",
    },
    {
        "name": "CdSe Quantum Dots",
        "nanomaterial_type": "quantum_dot",
        "particle_size_nm": 4.0,
        "surface_area_m2_g": 100,
        "band_gap_eV": 2.1,
        "morphology": "spherical, core-shell",
    },
]

BIOMATERIAL_DB: list[dict[str, object]] = [
    {
        "name": "PEEK (medical grade)",
        "density_g_cm3": 1.32,
        "youngs_modulus_GPa": 3.6,
        "biocompatibility": "excellent",
        "degradation_rate": "none (permanent)",
        "sterilization_method": "autoclave, EtO, gamma",
    },
    {
        "name": "Hydroxyapatite (HA)",
        "density_g_cm3": 3.16,
        "youngs_modulus_GPa": 80,
        "biocompatibility": "excellent (osteoconductive)",
        "degradation_rate": "slow (years)",
        "sterilization_method": "autoclave, gamma",
    },
    {
        "name": "PLGA (85:15)",
        "density_g_cm3": 1.30,
        "youngs_modulus_GPa": 2.0,
        "biocompatibility": "good",
        "degradation_rate": "moderate (months)",
        "sterilization_method": "EtO, gamma",
    },
    {
        "name": "Collagen (Type I)",
        "density_g_cm3": 1.05,
        "youngs_modulus_GPa": 0.05,
        "biocompatibility": "excellent (native ECM)",
        "degradation_rate": "fast (weeks)",
        "sterilization_method": "EtO",
    },
    {
        "name": "Chitosan",
        "density_g_cm3": 0.60,
        "youngs_modulus_GPa": 0.01,
        "biocompatibility": "good (antibacterial)",
        "degradation_rate": "moderate (months)",
        "sterilization_method": "EtO, gamma",
    },
    {
        "name": "Titanium Ti-6Al-4V ELI",
        "density_g_cm3": 4.43,
        "youngs_modulus_GPa": 114,
        "biocompatibility": "excellent",
        "degradation_rate": "none (permanent)",
        "sterilization_method": "autoclave",
    },
]

ELECTRONIC_MATERIAL_DB: list[dict[str, object]] = [
    {
        "name": "Silicon (Si)",
        "band_gap_eV": 1.12,
        "electron_mobility_cm2_Vs": 1350,
        "resistivity_ohm_cm": 2.3e5,
        "dielectric_constant": 11.9,
        "breakdown_field_MV_m": 30,
    },
    {
        "name": "Gallium Nitride (GaN)",
        "band_gap_eV": 3.4,
        "electron_mobility_cm2_Vs": 440,
        "resistivity_ohm_cm": 1.0e6,
        "dielectric_constant": 9.0,
        "breakdown_field_MV_m": 330,
    },
    {
        "name": "Gallium Arsenide (GaAs)",
        "band_gap_eV": 1.43,
        "electron_mobility_cm2_Vs": 8500,
        "resistivity_ohm_cm": 1.0e7,
        "dielectric_constant": 12.9,
        "breakdown_field_MV_m": 40,
    },
    {
        "name": "ITO (Indium Tin Oxide)",
        "band_gap_eV": 3.5,
        "electron_mobility_cm2_Vs": 30,
        "resistivity_ohm_cm": 1.0e-4,
        "dielectric_constant": 9.0,
        "breakdown_field_MV_m": 50,
    },
    {
        "name": "Barium Titanate (BaTiO3)",
        "band_gap_eV": 3.2,
        "electron_mobility_cm2_Vs": 1,
        "resistivity_ohm_cm": 1.0e12,
        "dielectric_constant": 2000,
        "breakdown_field_MV_m": 10,
    },
]

MAGNETIC_MATERIAL_DB: list[dict[str, object]] = [
    {
        "name": "NdFeB (N42)",
        "magnetic_type": "ferromagnetic",
        "saturation_magnetization_T": 1.3,
        "coercivity_kA_m": 955,
        "curie_temperature_C": 310,
        "max_energy_product_kJ_m3": 318,
    },
    {
        "name": "Samarium Cobalt (SmCo5)",
        "magnetic_type": "ferromagnetic",
        "saturation_magnetization_T": 0.9,
        "coercivity_kA_m": 1700,
        "curie_temperature_C": 720,
        "max_energy_product_kJ_m3": 160,
    },
    {
        "name": "Strontium Ferrite",
        "magnetic_type": "ferrimagnetic",
        "saturation_magnetization_T": 0.4,
        "coercivity_kA_m": 250,
        "curie_temperature_C": 450,
        "max_energy_product_kJ_m3": 28,
    },
    {
        "name": "Alnico 5",
        "magnetic_type": "ferromagnetic",
        "saturation_magnetization_T": 1.25,
        "coercivity_kA_m": 50,
        "curie_temperature_C": 850,
        "max_energy_product_kJ_m3": 44,
    },
    {
        "name": "Mu-metal (Ni-Fe)",
        "magnetic_type": "ferromagnetic",
        "saturation_magnetization_T": 0.75,
        "coercivity_kA_m": 2,
        "curie_temperature_C": 400,
        "max_energy_product_kJ_m3": 0,
    },
]

OPTICAL_MATERIAL_DB: list[dict[str, object]] = [
    {
        "name": "Fused Silica",
        "refractive_index": 1.458,
        "transmission_range_um": "0.2-2.5",
        "band_gap_eV": 9.0,
        "optical_type": "transparent",
    },
    {
        "name": "Sapphire (Al2O3)",
        "refractive_index": 1.768,
        "transmission_range_um": "0.17-5.5",
        "band_gap_eV": 9.9,
        "optical_type": "transparent",
    },
    {
        "name": "Zinc Selenide (ZnSe)",
        "refractive_index": 2.403,
        "transmission_range_um": "0.5-20",
        "band_gap_eV": 2.7,
        "optical_type": "transparent",
    },
    {
        "name": "YAG (Y3Al5O12)",
        "refractive_index": 1.816,
        "transmission_range_um": "0.21-5.5",
        "band_gap_eV": 6.5,
        "optical_type": "transparent",
    },
    {
        "name": "Lithium Niobate (LiNbO3)",
        "refractive_index": 2.286,
        "transmission_range_um": "0.35-5.0",
        "band_gap_eV": 3.8,
        "optical_type": "birefringent",
    },
    {
        "name": "Borosilicate Glass",
        "refractive_index": 1.474,
        "transmission_range_um": "0.35-2.5",
        "band_gap_eV": 4.5,
        "optical_type": "transparent",
    },
]

COMPOSITE_DB: list[dict[str, object]] = [
    {
        "name": "Carbon Fiber Epoxy",
        "composite_type": "fiber_reinforced",
        "matrix": "epoxy",
        "reinforcement": "carbon fiber (T300)",
        "fiber_volume_fraction": 0.60,
        "density_g_cm3": 1.55,
        "tensile_strength_MPa": 1500,
    },
    {
        "name": "GFRP (E-glass/Epoxy)",
        "composite_type": "fiber_reinforced",
        "matrix": "epoxy",
        "reinforcement": "E-glass fiber",
        "fiber_volume_fraction": 0.50,
        "density_g_cm3": 1.85,
        "tensile_strength_MPa": 1000,
    },
    {
        "name": "Kevlar/Epoxy",
        "composite_type": "fiber_reinforced",
        "matrix": "epoxy",
        "reinforcement": "Kevlar-49",
        "fiber_volume_fraction": 0.55,
        "density_g_cm3": 1.38,
        "tensile_strength_MPa": 1380,
    },
    {
        "name": "SiC/SiC CMC",
        "composite_type": "ceramic_matrix",
        "matrix": "SiC",
        "reinforcement": "SiC fiber",
        "fiber_volume_fraction": 0.40,
        "density_g_cm3": 2.8,
        "tensile_strength_MPa": 200,
    },
    {
        "name": "Al-SiC MMC",
        "composite_type": "metal_matrix",
        "matrix": "aluminum 6061",
        "reinforcement": "SiC particles",
        "fiber_volume_fraction": 0.20,
        "density_g_cm3": 2.88,
        "tensile_strength_MPa": 350,
    },
]

CORROSION_DB: list[dict[str, object]] = [
    {
        "name": "Steel in seawater",
        "environment": "seawater",
        "corrosion_type": "uniform",
        "corrosion_rate_mmpy": 0.13,
        "protection_method": "sacrificial anode, coatings",
        "galvanic_potential_V": -0.60,
    },
    {
        "name": "Stainless 316L in seawater",
        "environment": "seawater",
        "corrosion_type": "crevice",
        "corrosion_rate_mmpy": 0.01,
        "protection_method": "cathodic protection, design",
        "galvanic_potential_V": -0.05,
    },
    {
        "name": "Aluminum 6061 in marine",
        "environment": "marine atmosphere",
        "corrosion_type": "pitting",
        "corrosion_rate_mmpy": 0.005,
        "protection_method": "anodizing, conversion coating",
        "galvanic_potential_V": -0.85,
    },
    {
        "name": "Stainless 304 in chloride",
        "environment": "chloride solution, 80C",
        "corrosion_type": "stress_corrosion_cracking",
        "corrosion_rate_mmpy": 0.5,
        "protection_method": "material selection, stress relief",
        "galvanic_potential_V": -0.10,
    },
    {
        "name": "Carbon steel in acidic",
        "environment": "0.1M HCl",
        "corrosion_type": "uniform",
        "corrosion_rate_mmpy": 2.5,
        "protection_method": "inhibitors, coatings, cathodic",
        "galvanic_potential_V": -0.55,
    },
    {
        "name": "Steel/Aluminum couple",
        "environment": "3.5% NaCl",
        "corrosion_type": "galvanic",
        "corrosion_rate_mmpy": 0.8,
        "protection_method": "isolation, insulation, coating",
        "galvanic_potential_V": -0.25,
    },
]

# ---------------------------------------------------------------------------
# Computation functions
# ---------------------------------------------------------------------------

def get_material_properties(material: str) -> MaterialProperties | None:
    """Look up a material's properties by name."""
    for m in MATERIALS_DB:
        if m["name"] == material:
            return m
    return None


def compare_materials(materials_list: list[str]) -> list[dict[str, object]]:
    """Compare key properties across multiple materials."""
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

    matches.sort(key=lambda m: float(cast(float, m["tensile_strength_MPa"])), reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Material property computation functions
# ---------------------------------------------------------------------------

def calculate_specific_strength(material_name: str) -> float | None:
    """Calculate specific strength (tensile_strength / density) in kN*m/kg.

    Args:
        material_name: Exact material name from the database.

    Returns:
        Specific strength in kN*m/kg, or None if material not found.
    """
    mat = get_material_properties(material_name)
    if mat is None:
        return None
    density = mat["density_g_cm3"]
    if density == 0:
        return None
    return mat["tensile_strength_MPa"] / density


# ---------------------------------------------------------------------------
# Polymer functions
# ---------------------------------------------------------------------------

def get_polymer_ranking(property_name: str) -> list[dict[str, object]]:
    """Rank polymers in POLYMER_DB by a given property (descending).

    Args:
        property_name: Column name to rank by (e.g. 'tensile_strength_MPa').

    Returns:
        List of {name, value} sorted descending by the property.
    """
    results: list[dict[str, object]] = []
    for p in POLYMER_DB:
        val = p.get(property_name)
        if val is not None:
            results.append({"name": p["name"], "value": val})
    results.sort(key=lambda r: float(str(r["value"])), reverse=True)
    return results


def filter_polymers(thermoplastic_only: bool = False,
                    biodegradable_only: bool = False) -> list[str]:
    """Filter POLYMER_DB by class flags.

    Returns:
        List of polymer names matching the given criteria.
    """
    results: list[str] = []
    for p in POLYMER_DB:
        if thermoplastic_only and not p.get("is_thermoplastic"):
            continue
        if biodegradable_only and not p.get("biodegradable"):
            continue
        results.append(str(p["name"]))
    return results


# ---------------------------------------------------------------------------
# Composite functions
# ---------------------------------------------------------------------------

def compute_rule_of_mixtures(vf: float, ef: float, em: float,
                              orientation_factor: float = 0.375) -> float:
    """Compute composite modulus using modified rule of mixtures.

    E_c = orientation_factor * vf * Ef + (1 - vf) * Em

    Args:
        vf: Fiber volume fraction (0 to 1).
        ef: Fiber modulus (GPa).
        em: Matrix modulus (GPa).
        orientation_factor: Alignment factor (1.0 = unidirectional,
            0.375 = random 2D, 0.2 = random 3D).

    Returns:
        Composite modulus in GPa.
    """
    return orientation_factor * vf * ef + (1 - vf) * em


# ---------------------------------------------------------------------------
# Corrosion functions
# ---------------------------------------------------------------------------

def compute_corrosion_rate(weight_loss_g: float, area_cm2: float,
                            time_hours: float, density_g_cm3: float) -> float:
    """Compute corrosion rate in mm/year (mmpy) from weight loss.

    Args:
        weight_loss_g: Mass loss in grams.
        area_cm2: Exposed surface area in cm^2.
        time_hours: Exposure time in hours.
        density_g_cm3: Material density in g/cm^3.

    Returns:
        Corrosion rate in mm/year.
    """
    if area_cm2 <= 0 or time_hours <= 0 or density_g_cm3 <= 0:
        return 0.0
    hours_per_year = 365.25 * 24
    return (weight_loss_g * 10 * hours_per_year) / (area_cm2 * time_hours * density_g_cm3)


def compute_galvanic_corrosion_risk(anode_potential_v: float,
                                      cathode_potential_v: float) -> dict[str, object]:
    """Assess galvanic corrosion risk from electrode potentials.

    Args:
        anode_potential_v: Electrode potential of the anodic material (V vs SHE).
        cathode_potential_v: Electrode potential of the cathodic material (V vs SHE).

    Returns:
        Dict with potential_difference_V and risk_level.
    """
    delta = abs(cathode_potential_v - anode_potential_v)
    if delta < 0.1:
        risk = "low"
    elif delta < 0.3:
        risk = "moderate"
    elif delta < 0.5:
        risk = "high"
    else:
        risk = "severe"
    return {"potential_difference_V": round(delta, 3), "risk_level": risk}


# ---------------------------------------------------------------------------
# Metallurgy functions
# ---------------------------------------------------------------------------

def compute_hall_petch_strength(d_grain_um: float, k_hall_petch: float,
                                  sigma0_MPa: float) -> float:
    """Compute yield strength from grain size using Hall-Petch relationship.

    sigma_y = sigma0 + k / sqrt(d)

    Args:
        d_grain_um: Grain diameter in micrometers.
        k_hall_petch: Hall-Petch coefficient (MPa*um^0.5).
        sigma0_MPa: Friction stress (MPa).

    Returns:
        Yield strength in MPa.
    """
    if d_grain_um <= 0:
        return sigma0_MPa
    return float(sigma0_MPa + k_hall_petch / (d_grain_um ** 0.5))


def compute_archard_wear_volume(normal_load_N: float, sliding_distance_m: float,
                                  hardness_Pa: float, wear_coefficient: float) -> float:
    """Compute wear volume using Archard's wear law.

    V = K * F * s / H

    Args:
        normal_load_N: Normal load in Newtons.
        sliding_distance_m: Sliding distance in meters.
        hardness_Pa: Material hardness in Pascals.
        wear_coefficient: Dimensionless wear coefficient.

    Returns:
        Wear volume in m^3.
    """
    if hardness_Pa <= 0:
        return 0.0
    return wear_coefficient * normal_load_N * sliding_distance_m / hardness_Pa


# ---------------------------------------------------------------------------
# Nanomaterial functions
# ---------------------------------------------------------------------------

def compute_band_gap_from_wavelength(wavelength_nm: float) -> float:
    """Compute band gap energy in eV from absorption wavelength.

    E(eV) = 1240 / wavelength(nm)

    Args:
        wavelength_nm: Absorption edge wavelength in nanometers.

    Returns:
        Band gap energy in eV.
    """
    if wavelength_nm <= 0:
        return 0.0
    return 1240.0 / wavelength_nm


def compute_surface_to_volume_ratio(particle_size_nm: float,
                                      shape_factor: float = 6.0) -> float:
    """Compute surface-to-volume ratio for a nanoparticle.

    S/V = shape_factor / particle_diameter

    Args:
        particle_size_nm: Characteristic particle dimension in nm.
        shape_factor: 6 for sphere, ~4 for cylinder, ~2 for sheet.

    Returns:
        Surface-to-volume ratio in 1/nm.
    """
    if particle_size_nm <= 0:
        return 0.0
    return shape_factor / particle_size_nm


# ---------------------------------------------------------------------------
# Electronic material functions
# ---------------------------------------------------------------------------

def compute_dielectric_energy_density(dielectric_constant: float,
                                        breakdown_field_MV_m: float) -> float:
    """Compute maximum electrostatic energy storage density.

    U = 0.5 * epsilon_0 * epsilon_r * E_breakdown^2
    epsilon_0 = 8.854e-12 F/m

    Args:
        dielectric_constant: Relative permittivity (epsilon_r).
        breakdown_field_MV_m: Breakdown field in MV/m.

    Returns:
        Energy density in J/cm^3.
    """
    epsilon_0 = 8.854e-12
    e_bd_v_m = breakdown_field_MV_m * 1e6
    energy_j_m3 = 0.5 * epsilon_0 * dielectric_constant * (e_bd_v_m ** 2)
    return energy_j_m3 / 1e6


def compute_conductivity_from_resistivity(resistivity_ohm_cm: float) -> float:
    """Convert electrical resistivity to conductivity.

    sigma (S/cm) = 1 / rho (ohm*cm)

    Args:
        resistivity_ohm_cm: Electrical resistivity in ohm*cm.

    Returns:
        Electrical conductivity in S/cm.
    """
    if resistivity_ohm_cm <= 0:
        return 0.0
    return 1.0 / resistivity_ohm_cm


# ---------------------------------------------------------------------------
# Magnetic material functions
# ---------------------------------------------------------------------------

def compute_max_energy_product(remanence_T: float, coercivity_kA_m: float) -> float:
    """Estimate maximum energy product (BH)max for a permanent magnet.

    (BH)max ~= Br * Hc / 4  (theoretical upper bound)

    Args:
        remanence_T: Remanent magnetization in Tesla.
        coercivity_kA_m: Coercivity in kA/m.

    Returns:
        Estimated (BH)max in kJ/m^3.
    """
    return remanence_T * coercivity_kA_m / 4.0


# ---------------------------------------------------------------------------
# Optical material functions
# ---------------------------------------------------------------------------

def compute_refractive_index_contrast(n1: float, n2: float) -> float:
    """Compute fractional refractive index contrast between two media.

    delta_n = |n1 - n2| / max(n1, n2)

    Args:
        n1: Refractive index of first medium.
        n2: Refractive index of second medium.

    Returns:
        Fractional index contrast.
    """
    denom = max(abs(n1), abs(n2))
    if denom <= 0:
        return 0.0
    return abs(n1 - n2) / denom


def compute_reflectivity_normal(n1: float, n2: float) -> float:
    """Compute normal-incidence reflectivity at an interface.

    R = ((n1 - n2) / (n1 + n2))^2

    Args:
        n1: Refractive index of incident medium.
        n2: Refractive index of transmitted medium.

    Returns:
        Reflectivity (0 to 1).
    """
    return ((n1 - n2) / (n1 + n2)) ** 2


# ---------------------------------------------------------------------------
# Thermal / mechanical functions
# ---------------------------------------------------------------------------

def compute_thermal_shock_resistance(tensile_strength_MPa: float,
                                       youngs_modulus_GPa: float,
                                       thermal_expansion_10_6_K: float,
                                       thermal_conductivity_W_mK: float) -> float:
    """Compute thermal shock resistance parameter R'.

    R' = (sigma * k) / (E * alpha)

    Args:
        tensile_strength_MPa: Tensile strength in MPa.
        youngs_modulus_GPa: Young's modulus in GPa.
        thermal_expansion_10_6_K: CTE in 10^-6 / K.
        thermal_conductivity_W_mK: Thermal conductivity in W/(m*K).

    Returns:
        Thermal shock resistance parameter (W/m).
    """
    if youngs_modulus_GPa <= 0 or thermal_expansion_10_6_K <= 0:
        return 0.0
    return (tensile_strength_MPa * thermal_conductivity_W_mK) / (
        youngs_modulus_GPa * thermal_expansion_10_6_K)


def compute_biocompatibility_score(youngs_modulus_GPa: float,
                                    degradation_rate_months: float | None = None,
                                    match_bone_modulus: bool = False) -> float:
    """Compute a mock biocompatibility score (0-10) for implant materials.

    Based on modulus matching to cortical bone (~15-20 GPa).

    Args:
        youngs_modulus_GPa: Material Young's modulus in GPa.
        degradation_rate_months: Degradation time in months, if applicable.
        match_bone_modulus: Whether to penalize modulus mismatch.

    Returns:
        Score from 0 (poor) to 10 (excellent).
    """
    score = 10.0
    if match_bone_modulus:
        ideal = 18.0
        deviation = abs(youngs_modulus_GPa - ideal) / ideal
        score -= deviation * 5.0
    if degradation_rate_months is not None:
        if degradation_rate_months < 1:
            score -= 3.0
        elif degradation_rate_months > 24:
            score -= 1.0
    return max(0.0, min(10.0, score))
