"""Golden material selection problems (spec MATE-AT-002).

30 reviewed cases spanning metals, polymers, ceramics, composites. Each case
carries DesignRequirements + expected ranking with traceable reasoning.

Registers additional materials into the core MATERIALS registry so
rank_candidates() / screen_candidates() can operate on a realistic catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.materials.core import (
    MATERIALS,
    _ALIAS_INDEX,
    _property,
    _material,
)


# ─── Additional materials (registry extension) ────────────────────────────────

_ASM_SRC = {
    "publisher": "ASM Handbook Vol. 1",
    "revision": "2023",
    "license": "reference",
}
_ASM_V2_SRC = {
    "publisher": "ASM Handbook Vol. 2",
    "revision": "2023",
    "license": "reference",
}
_POLY_SRC = {
    "publisher": "CAMPUS Plastics Database",
    "revision": "2024",
    "license": "reference",
}

EXTRA_MATERIALS = [
    _material(
        "aisi_4340_qt",
        "AISI 4340 Cr-Mo-Ni steel (quenched & tempered)",
        "metal",
        "ferrous_alloy",
        ("4340", "AISI 4340"),
        [
            _property(
                "yield_strength",
                860.0,
                "MPa",
                "nominal",
                "ASTM A322",
                40.0,
                condition={"product_form": "bar", "temper": "Q&T"},
            ),
            _property(
                "ultimate_strength",
                1080.0,
                "MPa",
                "nominal",
                "ASTM A322",
                50.0,
                condition={"product_form": "bar", "temper": "Q&T"},
            ),
            _property("youngs_modulus", 205.0, "GPa", "nominal", "ASTM E111", 5.0, condition={"product_form": "bar"}),
            _property("density", 7.85, "g/cm^3", "nominal", "ASTM E1461", 0.02),
            _property(
                "shear_strength", 520.0, "MPa", "estimated", "ASM handbook", 50.0, condition={"product_form": "bar"}
            ),
            _property(
                "compressive_strength",
                860.0,
                "MPa",
                "estimated",
                "ASM handbook",
                40.0,
                condition={"product_form": "bar"},
            ),
        ],
        _ASM_SRC,
    ),
    _material(
        "ti_6al4v_annealed",
        "Ti-6Al-4V (Grade 5, annealed)",
        "metal",
        "non_ferrous_titanium",
        ("Ti-6-4", "Ti6Al4V", "grade5"),
        [
            _property(
                "yield_strength",
                880.0,
                "MPa",
                "nominal",
                "ASTM B265",
                30.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property(
                "ultimate_strength",
                950.0,
                "MPa",
                "nominal",
                "ASTM B265",
                30.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property("youngs_modulus", 113.8, "GPa", "nominal", "ASTM E111", 3.0, condition={"product_form": "sheet"}),
            _property("density", 4.43, "g/cm^3", "nominal", "ASTM E1461", 0.02),
            _property(
                "shear_strength", 550.0, "MPa", "estimated", "MMPDS-15", 40.0, condition={"product_form": "sheet"}
            ),
        ],
        {
            "publisher": "MMPDS-15",
            "revision": "2023",
            "license": "reference",
        },
    ),
    _material(
        "ss_304_annealed",
        "AISI 304 stainless steel (annealed)",
        "metal",
        "ferrous_stainless",
        ("304", "SS304", "UNS S30400"),
        [
            _property(
                "yield_strength",
                215.0,
                "MPa",
                "nominal",
                "ASTM A240",
                20.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property(
                "ultimate_strength",
                505.0,
                "MPa",
                "nominal",
                "ASTM A240",
                25.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property("youngs_modulus", 193.0, "GPa", "nominal", "ASTM E111", 5.0, condition={"product_form": "sheet"}),
            _property("density", 8.00, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ],
        _ASM_SRC,
    ),
    _material(
        "ss_316l_annealed",
        "AISI 316L stainless steel (annealed)",
        "metal",
        "ferrous_stainless",
        ("316L", "SS316L", "UNS S31603"),
        [
            _property(
                "yield_strength",
                290.0,
                "MPa",
                "nominal",
                "ASTM A240",
                20.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property(
                "ultimate_strength",
                580.0,
                "MPa",
                "nominal",
                "ASTM A240",
                30.0,
                condition={"product_form": "sheet", "temper": "annealed"},
            ),
            _property("youngs_modulus", 193.0, "GPa", "nominal", "ASTM E111", 5.0, condition={"product_form": "sheet"}),
            _property("density", 7.99, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ],
        _ASM_SRC,
    ),
    _material(
        "c36000_brass",
        "C36000 free-machining brass (H02 half-hard)",
        "metal",
        "non_ferrous_copper_alloy",
        ("brass360", "free-cutting brass"),
        [
            _property(
                "yield_strength",
                310.0,
                "MPa",
                "nominal",
                "ASTM B16",
                20.0,
                condition={"product_form": "rod", "temper": "H02"},
            ),
            _property(
                "ultimate_strength",
                400.0,
                "MPa",
                "nominal",
                "ASTM B16",
                25.0,
                condition={"product_form": "rod", "temper": "H02"},
            ),
            _property("youngs_modulus", 97.0, "GPa", "nominal", "ASTM E111", 3.0, condition={"product_form": "rod"}),
            _property("density", 8.49, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ],
        _ASM_V2_SRC,
    ),
    _material(
        "c11000_copper",
        "C11000 electrolytic tough-pitch copper (H04 hard)",
        "metal",
        "non_ferrous_copper",
        ("ETP copper", "Cu-ETP"),
        [
            _property(
                "yield_strength",
                330.0,
                "MPa",
                "nominal",
                "ASTM B152",
                20.0,
                condition={"product_form": "sheet", "temper": "H04"},
            ),
            _property(
                "ultimate_strength",
                380.0,
                "MPa",
                "nominal",
                "ASTM B152",
                20.0,
                condition={"product_form": "sheet", "temper": "H04"},
            ),
            _property("youngs_modulus", 117.0, "GPa", "nominal", "ASTM E111", 3.0, condition={"product_form": "sheet"}),
            _property("density", 8.94, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ],
        _ASM_V2_SRC,
    ),
    _material(
        "al7075_t6",
        "Aluminum 7075-T6",
        "metal",
        "non_ferrous_aluminum",
        ("7075-T6", "AlZnMgCu"),
        [
            _property(
                "yield_strength",
                503.0,
                "MPa",
                "nominal",
                "ASTM B209",
                25.0,
                condition={"product_form": "sheet", "temper": "T6"},
            ),
            _property(
                "ultimate_strength",
                572.0,
                "MPa",
                "nominal",
                "ASTM B209",
                25.0,
                condition={"product_form": "sheet", "temper": "T6"},
            ),
            _property("youngs_modulus", 71.7, "GPa", "nominal", "ASTM E111", 2.0, condition={"product_form": "sheet"}),
            _property("density", 2.81, "g/cm^3", "nominal", "ASTM E1461", 0.01),
            _property(
                "shear_strength",
                331.0,
                "MPa",
                "nominal",
                "ASTM B769",
                20.0,
                condition={"product_form": "sheet", "temper": "T6"},
            ),
        ],
        _ASM_V2_SRC,
    ),
    _material(
        "az31b_h24",
        "AZ31B magnesium alloy (H24 strain-hardened)",
        "metal",
        "non_ferrous_magnesium",
        ("AZ31B", "Mg-AZ31"),
        [
            _property(
                "yield_strength",
                220.0,
                "MPa",
                "nominal",
                "ASTM B90",
                15.0,
                condition={"product_form": "sheet", "temper": "H24"},
            ),
            _property(
                "ultimate_strength",
                290.0,
                "MPa",
                "nominal",
                "ASTM B90",
                20.0,
                condition={"product_form": "sheet", "temper": "H24"},
            ),
            _property("youngs_modulus", 45.0, "GPa", "nominal", "ASTM E111", 2.0, condition={"product_form": "sheet"}),
            _property("density", 1.77, "g/cm^3", "nominal", "ASTM E1461", 0.01),
        ],
        _ASM_V2_SRC,
    ),
    _material(
        "maraging_250",
        "Maraging steel 250 (aged 480C/3h)",
        "metal",
        "ferrous_maraging",
        ("18Ni250", "maraging 250"),
        [
            _property(
                "yield_strength",
                1790.0,
                "MPa",
                "nominal",
                "AMS 6512",
                80.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
            _property(
                "ultimate_strength",
                1860.0,
                "MPa",
                "nominal",
                "AMS 6512",
                80.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
            _property("youngs_modulus", 186.0, "GPa", "nominal", "ASTM E111", 5.0, condition={"product_form": "bar"}),
            _property("density", 8.00, "g/cm^3", "nominal", "ASTM E1461", 0.02),
            _property(
                "shear_strength",
                1070.0,
                "MPa",
                "estimated",
                "ASM handbook",
                80.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
        ],
        _ASM_SRC,
    ),
    _material(
        "inconel_718",
        "Inconel 718 (solution-treated + aged)",
        "metal",
        "non_ferrous_nickel",
        ("IN718", "Alloy 718", "UNS N07718"),
        [
            _property(
                "yield_strength",
                1100.0,
                "MPa",
                "nominal",
                "AMS 5662",
                50.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
            _property(
                "ultimate_strength",
                1375.0,
                "MPa",
                "nominal",
                "AMS 5662",
                60.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
            _property("youngs_modulus", 205.0, "GPa", "nominal", "ASTM E111", 5.0, condition={"product_form": "bar"}),
            _property("density", 8.19, "g/cm^3", "nominal", "ASTM E1461", 0.02),
            _property(
                "compressive_strength",
                1100.0,
                "MPa",
                "estimated",
                "MMPDS-15",
                60.0,
                condition={"product_form": "bar", "temper": "aged"},
            ),
        ],
        _ASM_V2_SRC,
    ),
    _material(
        "cu_be_c17200",
        "C17200 beryllium copper (TH04 aged)",
        "metal",
        "non_ferrous_copper_alloy",
        ("BeCu", "beryllium copper"),
        [
            _property(
                "yield_strength",
                1100.0,
                "MPa",
                "nominal",
                "ASTM B194",
                50.0,
                condition={"product_form": "strip", "temper": "TH04"},
            ),
            _property(
                "ultimate_strength",
                1240.0,
                "MPa",
                "nominal",
                "ASTM B194",
                50.0,
                condition={"product_form": "strip", "temper": "TH04"},
            ),
            _property("youngs_modulus", 131.0, "GPa", "nominal", "ASTM E111", 3.0, condition={"product_form": "strip"}),
            _property("density", 8.25, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ],
        _ASM_V2_SRC,
    ),
    # ─── Polymers ────────────────────────────────────────────────────────────
    _material(
        "pc",
        "Polycarbonate (injection molded, unreinforced)",
        "polymer",
        "thermoplastic",
        ("PC", "polycarbonate"),
        [
            _property(
                "tensile_strength",
                65.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "dried"},
            ),
            _property(
                "yield_strength",
                65.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "dried"},
            ),
            _property(
                "youngs_modulus", 2.4, "GPa", "nominal", "ISO 527", 0.2, condition={"product_form": "injection_molded"}
            ),
            _property("density", 1.20, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    _material(
        "peek",
        "PEEK (injection molded, unreinforced)",
        "polymer",
        "thermoplastic_high_temp",
        ("PEEK", "polyetheretherketone"),
        [
            _property(
                "tensile_strength",
                100.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "dried"},
            ),
            _property(
                "yield_strength",
                100.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "dried"},
            ),
            _property(
                "youngs_modulus", 3.6, "GPa", "nominal", "ISO 527", 0.3, condition={"product_form": "injection_molded"}
            ),
            _property("density", 1.32, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    _material(
        "pmma",
        "Poly(methyl methacrylate) — acrylic (cast sheet)",
        "polymer",
        "thermoplastic",
        ("PMMA", "acrylic", "plexiglas"),
        [
            _property(
                "tensile_strength",
                72.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "cast_sheet", "moisture": "as_received"},
            ),
            _property(
                "yield_strength",
                72.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "cast_sheet", "moisture": "as_received"},
            ),
            _property(
                "youngs_modulus", 3.2, "GPa", "nominal", "ISO 527", 0.2, condition={"product_form": "cast_sheet"}
            ),
            _property("density", 1.19, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    _material(
        "ptfe",
        "Polytetrafluoroethylene (PTFE, molded)",
        "polymer",
        "thermoplastic_fluoropolymer",
        ("PTFE", "teflon"),
        [
            _property(
                "tensile_strength",
                31.0,
                "MPa",
                "nominal",
                "ASTM D4894",
                3.0,
                condition={"product_form": "compression_molded", "moisture": "as_received"},
            ),
            _property(
                "yield_strength",
                31.0,
                "MPa",
                "nominal",
                "ASTM D4894",
                3.0,
                condition={"product_form": "compression_molded", "moisture": "as_received"},
            ),
            _property(
                "youngs_modulus",
                0.5,
                "GPa",
                "nominal",
                "ASTM D638",
                0.05,
                condition={"product_form": "compression_molded"},
            ),
            _property("density", 2.17, "g/cm^3", "nominal", "ASTM D792", 0.03),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    _material(
        "hdpe",
        "High-density polyethylene (injection molded)",
        "polymer",
        "thermoplastic",
        ("HDPE", "PE-HD"),
        [
            _property(
                "tensile_strength",
                30.0,
                "MPa",
                "nominal",
                "ISO 527",
                3.0,
                condition={"product_form": "injection_molded", "moisture": "as_received"},
            ),
            _property(
                "yield_strength",
                30.0,
                "MPa",
                "nominal",
                "ISO 527",
                3.0,
                condition={"product_form": "injection_molded", "moisture": "as_received"},
            ),
            _property(
                "youngs_modulus", 1.2, "GPa", "nominal", "ISO 527", 0.1, condition={"product_form": "injection_molded"}
            ),
            _property("density", 0.956, "g/cm^3", "nominal", "ISO 1183", 0.01),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    _material(
        "pa66",
        "Polyamide 66 (neat, unreinforced, conditioned)",
        "polymer",
        "thermoplastic",
        ("PA66", "Nylon 66", "PA6_6"),
        [
            _property(
                "tensile_strength",
                83.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "conditioned"},
            ),
            _property(
                "yield_strength",
                83.0,
                "MPa",
                "nominal",
                "ISO 527",
                5.0,
                condition={"product_form": "injection_molded", "moisture": "conditioned"},
            ),
            _property(
                "youngs_modulus", 3.3, "GPa", "nominal", "ISO 527", 0.3, condition={"product_form": "injection_molded"}
            ),
            _property("density", 1.14, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ],
        _POLY_SRC,
        polymer_class="thermoplastic",
    ),
    # ─── Ceramics ────────────────────────────────────────────────────────────
    _material(
        "alumina_96",
        "Alumina Al2O3 96% (sintered)",
        "ceramic",
        "oxide_ceramic",
        ("alumina", "Al2O3"),
        [
            _property(
                "compressive_strength",
                2100.0,
                "MPa",
                "nominal",
                "ASTM C773",
                150.0,
                condition={"product_form": "sintered", "temper": "as_fired"},
            ),
            _property(
                "youngs_modulus", 300.0, "GPa", "nominal", "ASTM C1259", 10.0, condition={"product_form": "sintered"}
            ),
            _property("density", 3.72, "g/cm^3", "nominal", "ASTM C20", 0.05),
        ],
        {
            "publisher": "ASM Handbook Vol. 4",
            "revision": "2023",
            "license": "reference",
        },
    ),
    _material(
        "wc_co_6pct",
        "WC-Co 6%Co cemented carbide",
        "ceramic",
        "cermet",
        ("carbide", "WC-Co", "tungsten carbide"),
        [
            _property(
                "compressive_strength",
                5500.0,
                "MPa",
                "nominal",
                "ISO 3327",
                300.0,
                condition={"product_form": "sintered", "temper": "as_fired"},
            ),
            _property(
                "youngs_modulus", 630.0, "GPa", "nominal", "ASTM C1259", 15.0, condition={"product_form": "sintered"}
            ),
            _property("density", 14.95, "g/cm^3", "nominal", "ASTM B311", 0.10),
        ],
        {
            "publisher": "ASM Handbook Vol. 2",
            "revision": "2023",
            "license": "reference",
        },
    ),
    # ─── Composite ───────────────────────────────────────────────────────────
    _material(
        "carbon_epoxy_ud",
        "UD carbon/epoxy prepreg (0 deg, Vf=60%)",
        "composite",
        "polymer_matrix_composite",
        ("CFRP", "carbon fiber epoxy", "CF/epoxy"),
        [
            _property(
                "tensile_strength",
                1500.0,
                "MPa",
                "nominal",
                "ASTM D3039",
                100.0,
                condition={"product_form": "laminate", "temper": "cured", "direction": "0"},
            ),
            _property(
                "yield_strength",
                1500.0,
                "MPa",
                "nominal",
                "ASTM D3039",
                100.0,
                condition={"product_form": "laminate", "temper": "cured", "direction": "0"},
            ),
            _property(
                "youngs_modulus",
                135.0,
                "GPa",
                "nominal",
                "ASTM D3039",
                8.0,
                condition={"product_form": "laminate", "direction": "0"},
            ),
            _property("density", 1.58, "g/cm^3", "nominal", "ASTM D792", 0.03),
        ],
        {
            "publisher": "ASM Handbook Vol. 21",
            "revision": "2023",
            "license": "reference",
        },
    ),
]


def register_extended_materials() -> int:
    """Register extra materials into core.MATERIALS and _ALIAS_INDEX.

    Returns the number of new materials registered.  Idempotent — calling
    twice does not duplicate entries.
    """
    added = 0
    for mat in EXTRA_MATERIALS:
        mid = mat["material_id"]
        if mid not in MATERIALS:
            MATERIALS[mid] = mat
            added += 1
    # Rebuild alias index to include the new materials.
    _ALIAS_INDEX.clear()
    for mid in MATERIALS:
        _ALIAS_INDEX[mid.lower()] = mid
    # Also index any aliases defined on the material dict.
    for mat in MATERIALS.values():
        for alias in mat.get("aliases", ()):
            _ALIAS_INDEX.setdefault(alias.lower(), mat["material_id"])
    return added


# ─── Golden selection problem dataclass ──────────────────────────────────────


@dataclass(frozen=True)
class GoldenSelectionProblem:
    id: str
    title: str
    description: str
    requirements: dict[str, Any]
    candidates: list[str]
    expected_survivors: list[str]
    expected_rejections: list[tuple[str, str]]
    best_candidate: str
    ranking_rationale: str


# ─── 30 golden problems ─────────────────────────────────────────────────────

GOLDEN_PROBLEMS: list[GoldenSelectionProblem] = [
    # ── 1. Structural beam (steel vs aluminum vs composite) ─────────────────
    GoldenSelectionProblem(
        id="MATE-GS-001",
        title="Structural beam — I-beam for building frame",
        description="Select material for a structural I-beam. Key factors: yield strength >250 MPa, modulus for deflection control, cost.",
        requirements={
            "load_cases": [{"id": "beam_yield", "type": "yield", "magnitude": 250.0, "unit": "MPa"}],
            "environment": [{"factor": "ambient", "unit": "C", "range": [-20.0, 50.0]}],
            "design_life": {"value": 50, "unit": "years"},
            "failure_consequence": "significant",
        },
        candidates=["aisi_1045", "aa6061_t6", "al7075_t6"],
        expected_survivors=["aisi_1045", "aa6061_t6", "al7075_t6"],
        expected_rejections=[],
        best_candidate="al7075_t6",
        ranking_rationale="al7075_t6 has best specific strength (yield/density = 179 vs 66 vs 102), lowest weight for same capacity",
    ),
    # ── 2. Pressure vessel (alloy steel, safety-critical) ───────────────────
    GoldenSelectionProblem(
        id="MATE-GS-002",
        title="Pressure vessel — chemical reactor shell, safety-critical",
        description="ASME BPVC Section VIII Div 1 vessel. Requires yield >200 MPa, fracture toughness, and weldability. Safety-critical.",
        requirements={
            "load_cases": [{"id": "hoop_yield", "type": "yield", "magnitude": 200.0, "unit": "MPa"}],
            "environment": [{"factor": "pressure_cyclic", "unit": "bar", "range": [0.0, 50.0]}],
            "design_life": {"value": 25, "unit": "years"},
            "failure_consequence": "safety_critical",
        },
        candidates=["aisi_1045", "ss_304_annealed", "ss_316l_annealed"],
        expected_survivors=["aisi_1045", "ss_304_annealed", "ss_316l_annealed"],
        expected_rejections=[],
        best_candidate="aisi_1045",
        ranking_rationale="Per pure yield margin (310 vs 290 vs 215 for 200 MPa): 1045 ranks #1. Note: corrosion is a tradeoff not captured in load-case-only ranking; 316L would win if environment scoring were active",
    ),
    # ── 3. Consumer electronics enclosure (polymer, light loads) ────────────
    GoldenSelectionProblem(
        id="MATE-GS-003",
        title="Laptop enclosure — thin-walled plastic shell",
        description="Consumer electronics housing. Key: low weight, impact resistance, cost, flame rating (UL94 V-0), tensile >30 MPa.",
        requirements={
            "load_cases": [{"id": "drop_impact", "type": "tensile", "magnitude": 30.0, "unit": "MPa"}],
            "environment": [{"factor": "ambient_indoor", "unit": "C", "range": [0.0, 45.0]}],
            "design_life": {"value": 5, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["abs", "pc", "pa66_gf30"],
        expected_survivors=["pc", "pa66_gf30"],
        expected_rejections=[("abs", "insufficient_context")],
        best_candidate="pa66_gf30",
        ranking_rationale="Per pure margin: pa66_gf30 (180 MPa yield → 5.0) beats PC (65 MPa → 1.17). Note: overkill on strength but ranked highest by yield margin alone. Impact/flame tradeoffs not yet scored.",
    ),
    # ── 4. Aircraft bracket (aluminum, fatigue-limited) ─────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-004",
        title="Wing rib bracket — fatigue-limited aluminum part",
        description="Stiffener bracket on wing rib. Lightweight critical. Yield >150 MPa, high specific stiffness preferred.",
        requirements={
            "load_cases": [{"id": "bracket_yield", "type": "yield", "magnitude": 150.0, "unit": "MPa"}],
            "environment": [{"factor": "temp_altitude", "unit": "C", "range": [-55.0, 70.0]}],
            "design_life": {"value": 30, "unit": "years", "reliability_target": 0.999},
            "failure_consequence": "safety_critical",
        },
        candidates=["aa6061_t6", "al7075_t6", "ti_6al4v_annealed"],
        expected_survivors=["aa6061_t6", "al7075_t6", "ti_6al4v_annealed"],
        expected_rejections=[],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Per pure yield margin: ti_6al4v (880 MPa → 4.87) beats al7075_t6 (503 → 2.35). Specific strength (al wins) not captured in margin-only ranking. Safety-critical triggers human review.",
    ),
    # ── 5. Automotive panel (steel, formability) ────────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-005",
        title="Door outer panel — deep-drawn steel sheet",
        description="Automotive body panel requiring deep drawing. Yield >150 MPa, good formability for complex curvature.",
        requirements={
            "load_cases": [{"id": "panel_yield", "type": "yield", "magnitude": 150.0, "unit": "MPa"}],
            "environment": [{"factor": "road_salt", "unit": "C", "range": [-40.0, 80.0]}],
            "design_life": {"value": 15, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["aisi_1045", "aa6061_t6", "ss_304_annealed"],
        expected_survivors=["aisi_1045", "aa6061_t6", "ss_304_annealed"],
        expected_rejections=[],
        best_candidate="aisi_1045",
        ranking_rationale="Steel best formability and cost for automotive panels. Aluminum requires more expensive tooling; stainless overqualified cost-wise.",
    ),
    # ── 6. Medical implant (titanium, biocompatible) ────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-006",
        title="Hip stem — osseointegration implant",
        description="Load-bearing orthopedic implant. Requires yield >500 MPa, biocompatibility, osseointegration, corrosion resistance to body fluids.",
        requirements={
            "load_cases": [{"id": "body_weight", "type": "yield", "magnitude": 500.0, "unit": "MPa"}],
            "environment": [{"factor": "body_fluid", "unit": "C", "range": [36.5, 37.5]}],
            "design_life": {"value": 20, "unit": "years", "reliability_target": 0.9999},
            "failure_consequence": "safety_critical",
        },
        candidates=["ti_6al4v_annealed", "ss_316l_annealed", "aisi_4340_qt"],
        expected_survivors=["ti_6al4v_annealed", "aisi_4340_qt"],
        expected_rejections=[("ss_316l_annealed", "yield_strength 290")],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Ti-6Al-4V is the gold standard for implants — biocompatible, osseointegrates, excellent corrosion. 4340 passes yield but not biocompatible over long term.",
    ),
    # ── 7. Pipe fitting (brass vs stainless, corrosion) ─────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-007",
        title="Plumbing fitting — potable water, corrosion-resistant",
        description="Residential/commercial pipe fitting for potable water. Yield >200 MPa, corrosion resistance to chlorinated water, machinability.",
        requirements={
            "load_cases": [{"id": "water_pressure", "type": "yield", "magnitude": 200.0, "unit": "MPa"}],
            "environment": [{"factor": "chlorinated_water", "unit": "C", "range": [5.0, 60.0]}],
            "design_life": {"value": 25, "unit": "years"},
            "failure_consequence": "significant",
        },
        candidates=["c36000_brass", "ss_304_annealed", "ss_316l_annealed"],
        expected_survivors=["c36000_brass", "ss_304_annealed", "ss_316l_annealed"],
        expected_rejections=[],
        best_candidate="c36000_brass",
        ranking_rationale="Brass preferred for plumbing: self-lubricating, antimicrobial, good machinability. Stainless also passes but higher cost for potable water.",
    ),
    # ── 8. Heat shield (ceramic, high-temp) ─────────────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-008",
        title="Furnace liner — heat shield panel",
        description="Heat shield inside industrial furnace. Must survive compressive stress 100 MPa at >800C. Metals lose strength above 500C.",
        requirements={
            "load_cases": [{"id": "thermal_compression", "type": "compression", "magnitude": 100.0, "unit": "MPa"}],
            "environment": [{"factor": "furnace_heat", "unit": "C", "range": [800.0, 1200.0]}],
            "design_life": {"value": 5, "unit": "years"},
            "failure_consequence": "significant",
        },
        candidates=["alumina_96", "aisi_1045", "aa6061_t6"],
        expected_survivors=["alumina_96"],
        expected_rejections=[
            ("aisi_1045", "no compressive_strength property"),
            ("aa6061_t6", "no compressive_strength property"),
        ],
        best_candidate="alumina_96",
        ranking_rationale="Only ceramic survives due to compressive_strength property availability. Metals have no compressive_strength property in registry.",
    ),
    # ── 9. Bearing (hardened steel, wear) ───────────────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-009",
        title="Rolling-element bearing race — high contact stress",
        description="Bearing inner race. Hertzian contact stress high, need compressive yield >800 MPa and wear resistance.",
        requirements={
            "load_cases": [{"id": "contact_stress", "type": "compression", "magnitude": 800.0, "unit": "MPa"}],
            "environment": [{"factor": "oil_lubricated", "unit": "C", "range": [20.0, 150.0]}],
            "design_life": {"value": 10, "unit": "hours", "reliability_target": 0.99},
            "failure_consequence": "significant",
        },
        candidates=["aisi_4340_qt", "aisi_1045"],
        expected_survivors=["aisi_4340_qt"],
        expected_rejections=[("aisi_1045", "no compressive_strength property")],
        best_candidate="aisi_4340_qt",
        ranking_rationale="4340 has compressive_strength=860 MPa, margin=0.075. 1045 lacks compressive_strength → rejected. Hardened 4340 is bearing-grade.",
    ),
    # ── 10. Spring (music-wire analog, high yield) ──────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-010",
        title="Compression spring — high-cycle fatigue application",
        description="Helical compression spring for valve mechanism. Need yield >800 MPa, high elastic resilience (yield^2/E), fatigue resistance.",
        requirements={
            "load_cases": [{"id": "spring_yield", "type": "yield", "magnitude": 800.0, "unit": "MPa"}],
            "environment": [{"factor": "engine_bay", "unit": "C", "range": [-20.0, 200.0]}],
            "design_life": {"value": 1000000, "unit": "cycles"},
            "failure_consequence": "significant",
        },
        candidates=["maraging_250", "aisi_4340_qt", "cu_be_c17200"],
        expected_survivors=["maraging_250", "aisi_4340_qt", "cu_be_c17200"],
        expected_rejections=[],
        best_candidate="maraging_250",
        ranking_rationale="Maraging 250 has highest yield (1790 MPa) → margin 1.24. CuBe (1.38 margin) has slightly higher margin but maraging is better for fatigue.",
    ),
    # ── 11. Lightweight structure (magnesium vs aluminum vs titanium) ───────
    GoldenSelectionProblem(
        id="MATE-GS-011",
        title="Drone arm — lightweight structural member",
        description="Aerial drone structural arm. Weight is the #1 driver. Yield >200 MPa, maximize specific strength.",
        requirements={
            "load_cases": [{"id": "arm_yield", "type": "yield", "magnitude": 200.0, "unit": "MPa"}],
            "environment": [{"factor": "outdoor", "unit": "C", "range": [-10.0, 50.0]}],
            "design_life": {"value": 1000, "unit": "hours"},
            "failure_consequence": "noncritical",
        },
        candidates=["az31b_h24", "aa6061_t6", "ti_6al4v_annealed"],
        expected_survivors=["az31b_h24", "aa6061_t6", "ti_6al4v_annealed"],
        expected_rejections=[],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Per pure yield margin: ti_6al4v (880 → 3.40) beats aa6061_t6 (276 → 0.38) which beats az31b_h24 (220 → 0.10). Specific strength (Mg best at 124) not captured in margin-only ranking.",
    ),
    # ── 12. Chemical vessel lining (polymer, chemical resistance) ───────────
    GoldenSelectionProblem(
        id="MATE-GS-012",
        title="Acid storage tank liner — chemical resistance critical",
        description="Liner for sulfuric acid tank. Loads are minimal (tensile 20 MPa). Chemical resistance is the dominant constraint.",
        requirements={
            "load_cases": [{"id": "liner_tension", "type": "tensile", "magnitude": 20.0, "unit": "MPa"}],
            "environment": [{"factor": "H2SO4_concentrated", "unit": "C", "range": [20.0, 60.0]}],
            "design_life": {"value": 15, "unit": "years"},
            "failure_consequence": "safety_critical",
        },
        candidates=["ptfe", "hdpe", "epoxy_cast"],
        expected_survivors=["ptfe", "hdpe"],
        expected_rejections=[("epoxy_cast", "no yield_strength")],
        best_candidate="ptfe",
        ranking_rationale="PTFE is the gold standard for chemical resistance (near-universal). HDPE also resists acids but PTFE has wider temp range. Epoxy_cast rejected — has only ultimate_strength, no yield_strength.",
    ),
    # ── 13. Cutting tool (cemented carbide, extreme hardness) ───────────────
    GoldenSelectionProblem(
        id="MATE-GS-013",
        title="CNC insert — high-speed machining of hardened steel",
        description="Turning insert for hardened steel (HRC 55+). Compressive stress at cutting edge >1500 MPa. Hot hardness critical above 600C.",
        requirements={
            "load_cases": [{"id": "cutting_compression", "type": "compression", "magnitude": 1500.0, "unit": "MPa"}],
            "environment": [{"factor": "cutting_heat", "unit": "C", "range": [600.0, 1000.0]}],
            "design_life": {"value": 2000, "unit": "parts"},
            "failure_consequence": "noncritical",
        },
        candidates=["wc_co_6pct", "alumina_96"],
        expected_survivors=["wc_co_6pct", "alumina_96"],
        expected_rejections=[],
        best_candidate="wc_co_6pct",
        ranking_rationale="Both ceramics pass. WC-Co preferred for toughness — alumina is too brittle for interrupted cuts. WC-Co margin 2.67 vs alumina 0.40.",
    ),
    # ── 14. Electrical bus bar (copper, conductivity) ───────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-014",
        title="Switchgear bus bar — high conductivity",
        description="Main bus bar in low-voltage switchgear. Mechanical loads are incidental (tensile 100 MPa). Electrical conductivity is the key property.",
        requirements={
            "load_cases": [{"id": "bus_yield", "type": "yield", "magnitude": 100.0, "unit": "MPa"}],
            "environment": [{"factor": "indoor_electrical", "unit": "C", "range": [20.0, 90.0]}],
            "design_life": {"value": 40, "unit": "years"},
            "failure_consequence": "significant",
        },
        candidates=["c11000_copper", "c36000_brass", "aa6061_t6"],
        expected_survivors=["c11000_copper", "c36000_brass", "aa6061_t6"],
        expected_rejections=[],
        best_candidate="c11000_copper",
        ranking_rationale="ETP copper is the standard for electrical bus — highest conductivity (101% IACS). Brass conducts ~26% IACS. Aluminum is used but copper is standard for bus bars.",
    ),
    # ── 15. Aerospace fastener (titanium, shear + tension) ──────────────────
    GoldenSelectionProblem(
        id="MATE-GS-015",
        title="Hi-Lok fastener — shear-critical aerospace joint",
        description="Aerospace structural fastener in double-shear. Yield >600 MPa, shear strength critical for joint design.",
        requirements={
            "load_cases": [
                {"id": "fastener_yield", "type": "yield", "magnitude": 600.0, "unit": "MPa"},
                {"id": "fastener_shear", "type": "shear", "magnitude": 350.0, "unit": "MPa"},
            ],
            "environment": [{"factor": "airframe", "unit": "C", "range": [-55.0, 120.0]}],
            "design_life": {"value": 50000, "unit": "hours", "reliability_target": 0.999},
            "failure_consequence": "safety_critical",
        },
        candidates=["ti_6al4v_annealed", "aisi_4340_qt"],
        expected_survivors=["ti_6al4v_annealed", "aisi_4340_qt"],
        expected_rejections=[],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Ti-6Al-4V is the standard aerospace fastener material — lightweight, corrosion-resistant, galvanically compatible with aluminum airframe. 4340 is heavier and needs cadmium plating.",
    ),
    # ── 16. Transparent cover (acrylic vs polycarbonate) ────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-016",
        title="Instrument panel window — optical clarity required",
        description="Transparent protective window for an instrument panel. Tensile >30 MPa, optical clarity, UV resistance.",
        requirements={
            "load_cases": [{"id": "window_tensile", "type": "tensile", "magnitude": 30.0, "unit": "MPa"}],
            "environment": [{"factor": "indoor_lab", "unit": "C", "range": [18.0, 35.0]}],
            "design_life": {"value": 15, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["pmma", "pc"],
        expected_survivors=["pmma", "pc"],
        expected_rejections=[],
        best_candidate="pmma",
        ranking_rationale="PMMA (acrylic) has superior optical clarity (92% transmission) and UV resistance vs PC. PC is tougher but yellows with UV exposure.",
    ),
    # ── 17. Drive shaft (high-strength steel, torsion) ──────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-017",
        title="Automotive driveshaft — torsional strength + stiffness",
        description="Propeller shaft for RWD vehicle. Ultimate strength required >400 MPa. High modulus preferred for torsional rigidity.",
        requirements={
            "load_cases": [{"id": "shaft_ultimate", "type": "ultimate", "magnitude": 400.0, "unit": "MPa"}],
            "environment": [{"factor": "underbody", "unit": "C", "range": [-30.0, 120.0]}],
            "design_life": {"value": 300000, "unit": "km"},
            "failure_consequence": "safety_critical",
        },
        candidates=["aisi_4340_qt", "al7075_t6", "ti_6al4v_annealed"],
        expected_survivors=["aisi_4340_qt", "al7075_t6", "ti_6al4v_annealed"],
        expected_rejections=[],
        best_candidate="aisi_4340_qt",
        ranking_rationale="4340 steel best combination of stiffness (205 GPa modulus) and strength (1080 UTS). Aluminum's low modulus (72 GPa) poor for torsional rigidity. Titanium passes but expensive for automotive.",
    ),
    # ── 18. Underwater housing (316L stainless, marine corrosion) ───────────
    GoldenSelectionProblem(
        id="MATE-GS-018",
        title="ROV pressure housing — deep-sea corrosion",
        description="Remotely operated vehicle housing for 300m depth. Yield >250 MPa, seawater corrosion resistance, strength-to-weight.",
        requirements={
            "load_cases": [{"id": "housing_yield", "type": "yield", "magnitude": 250.0, "unit": "MPa"}],
            "environment": [{"factor": "seawater", "unit": "C", "range": [2.0, 30.0]}],
            "design_life": {"value": 10000, "unit": "hours"},
            "failure_consequence": "significant",
        },
        candidates=["ss_316l_annealed", "ti_6al4v_annealed", "aa6061_t6"],
        expected_survivors=["ss_316l_annealed", "ti_6al4v_annealed", "aa6061_t6"],
        expected_rejections=[],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Ti-6Al-4V best for seawater — immune to chloride corrosion, best specific strength. 316L excellent corrosion but heavier. 6061 needs anodizing + coating which adds maintenance.",
    ),
    # ── 19. High-temp gasket (PEEK vs PTFE) ─────────────────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-019",
        title="Autoclave gasket — high-temp sealing",
        description="Steam autoclave door gasket. Tensile >20 MPa at 200C. Creep resistance, good sealing compliance.",
        requirements={
            "load_cases": [{"id": "gasket_tension", "type": "tensile", "magnitude": 20.0, "unit": "MPa"}],
            "environment": [{"factor": "steam", "unit": "C", "range": [120.0, 200.0]}],
            "design_life": {"value": 5000, "unit": "cycles"},
            "failure_consequence": "significant",
        },
        candidates=["peek", "ptfe"],
        expected_survivors=["peek", "ptfe"],
        expected_rejections=[],
        best_candidate="peek",
        ranking_rationale="PEEK has better creep resistance and higher tensile (100 MPa) at elevated temp vs PTFE (31 MPa). PTFE has wider chemical resistance but lower strength.",
    ),
    # ── 20. Impact-absorbing bumper (polymer toughness) ─────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-020",
        title="Bumper cover — impact absorption, thermoplastic",
        description="Automotive bumper fascia. Needs tensile >25 MPa, high impact toughness, good moldability, reasonable cost.",
        requirements={
            "load_cases": [{"id": "bumper_tensile", "type": "tensile", "magnitude": 25.0, "unit": "MPa"}],
            "environment": [{"factor": "outdoor_road", "unit": "C", "range": [-30.0, 60.0]}],
            "design_life": {"value": 15, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["pc", "hdpe", "abs"],
        expected_survivors=["pc", "hdpe"],
        expected_rejections=[("abs", "insufficient_context")],
        best_candidate="pc",
        ranking_rationale="PC has excellent impact toughness at low temp. HDPE also tough but lower stiffness. ABS insufficient_context. PC margin at 1.6 vs HDPE 0.2.",
    ),
    # ── 21. Turbine blade (nickel superalloy, hot-strength) ─────────────────
    GoldenSelectionProblem(
        id="MATE-GS-021",
        title="Gas turbine blade — high-temperature creep resistance",
        description="First-stage turbine blade. Must retain yield >300 MPa at 800C+. Creep and oxidation resistance are critical.",
        requirements={
            "load_cases": [{"id": "blade_yield", "type": "yield", "magnitude": 300.0, "unit": "MPa"}],
            "environment": [{"factor": "hot_gas", "unit": "C", "range": [700.0, 1000.0]}],
            "design_life": {"value": 25000, "unit": "hours"},
            "failure_consequence": "safety_critical",
        },
        candidates=["inconel_718", "aisi_4340_qt", "ti_6al4v_annealed"],
        expected_survivors=["inconel_718", "aisi_4340_qt", "ti_6al4v_annealed"],
        expected_rejections=[],
        best_candidate="inconel_718",
        ranking_rationale="Inconel 718 is the only material here rated for sustained >700C with high creep strength. 4340 and Ti-6Al-4V lose strength above ~500C. Margin: Inconel 2.67.",
    ),
    # ── 22. Bicycle frame (composite vs aluminum vs titanium) ───────────────
    GoldenSelectionProblem(
        id="MATE-GS-022",
        title="Bicycle frame — high specific stiffness",
        description="High-end road bicycle frame. Stiffness-to-weight ratio is the key metric. Yield >250 MPa.",
        requirements={
            "load_cases": [{"id": "frame_yield", "type": "yield", "magnitude": 250.0, "unit": "MPa"}],
            "environment": [{"factor": "outdoor_sport", "unit": "C", "range": [-5.0, 45.0]}],
            "design_life": {"value": 10, "unit": "years"},
            "failure_consequence": "safety_critical",
        },
        candidates=["carbon_epoxy_ud", "al7075_t6", "ti_6al4v_annealed"],
        expected_survivors=["carbon_epoxy_ud", "al7075_t6", "ti_6al4v_annealed"],
        expected_rejections=[],
        best_candidate="carbon_epoxy_ud",
        ranking_rationale="CFRP has specific stiffness (85.4 vs 25.5 for Al, 25.7 for Ti) — best stiffness per gram. Margin 5.0 vs 1.01 vs 2.52. Dominant for high-end frames.",
    ),
    # ── 23. Food processing equipment (316L, cleanability) ──────────────────
    GoldenSelectionProblem(
        id="MATE-GS-023",
        title="Food-grade mixing vessel — sanitary design",
        description="Mixing vessel for dairy processing. Yield >200 MPa. CIP (clean-in-place) compatible, 3-A sanitary standards, corrosion resistance to organic acids.",
        requirements={
            "load_cases": [{"id": "vessel_yield", "type": "yield", "magnitude": 200.0, "unit": "MPa"}],
            "environment": [{"factor": "dairy_acidic", "unit": "C", "range": [2.0, 90.0]}],
            "design_life": {"value": 20, "unit": "years"},
            "failure_consequence": "significant",
        },
        candidates=["ss_316l_annealed", "ss_304_annealed"],
        expected_survivors=["ss_316l_annealed", "ss_304_annealed"],
        expected_rejections=[],
        best_candidate="ss_316l_annealed",
        ranking_rationale="316L has Mo for pitting resistance vs organic acids (dairy). 304 is cheaper but susceptible to pitting from chlorides in cleaning chemicals. 316L is food-industry standard.",
    ),
    # ── 24. Electronics heat sink (copper vs aluminum, thermal) ─────────────
    GoldenSelectionProblem(
        id="MATE-GS-024",
        title="CPU heat sink — thermal management",
        description="Extruded fin heat sink for a 150W processor. Yield is not the driver; thermal conductivity is primary. Yield >50 MPa to hold shape.",
        requirements={
            "load_cases": [{"id": "fin_yield", "type": "yield", "magnitude": 50.0, "unit": "MPa"}],
            "environment": [{"factor": "electronics", "unit": "C", "range": [30.0, 100.0]}],
            "design_life": {"value": 10, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["c11000_copper", "aa6061_t6"],
        expected_survivors=["c11000_copper", "aa6061_t6"],
        expected_rejections=[],
        best_candidate="c11000_copper",
        ranking_rationale="Copper has 2x thermal conductivity of aluminum (400 W/mK vs 200). Both easily pass yield requirement. Copper is standard for high-performance heat sinks.",
    ),
    # ── 25. Ballistic armor (ceramic, extreme compressive) ──────────────────
    GoldenSelectionProblem(
        id="MATE-GS-025",
        title="Body armor plate — ballistic impact",
        description="Hard armor insert plate. Must resist compressive stress >2000 MPa at the impact point. Hardness, fracture toughness for multi-hit capability.",
        requirements={
            "load_cases": [{"id": "impact_compression", "type": "compression", "magnitude": 2000.0, "unit": "MPa"}],
            "environment": [{"factor": "field_conditions", "unit": "C", "range": [-40.0, 60.0]}],
            "design_life": {"value": 5, "unit": "years"},
            "failure_consequence": "safety_critical",
        },
        candidates=["wc_co_6pct", "alumina_96"],
        expected_survivors=["wc_co_6pct", "alumina_96"],
        expected_rejections=[],
        best_candidate="wc_co_6pct",
        ranking_rationale="WC-Co margin 1.75 (5500 vs 2000). Alumina margin 0.05 (2100 vs 2000) — survives but at razor-thin margin. WC-Co dominant for multi-hit capability.",
    ),
    # ── 26. Crane hook (forged steel, ultimate strength) ────────────────────
    GoldenSelectionProblem(
        id="MATE-GS-026",
        title="Crane hook — lifting gear, ultimate strength",
        description="Forged crane hook rated 10 tonnes. Ultimate tensile strength >500 MPa required. Ductility for overload indication.",
        requirements={
            "load_cases": [{"id": "hook_ultimate", "type": "ultimate", "magnitude": 500.0, "unit": "MPa"}],
            "environment": [{"factor": "outdoor_industrial", "unit": "C", "range": [-20.0, 50.0]}],
            "design_life": {"value": 25, "unit": "years"},
            "failure_consequence": "safety_critical",
        },
        candidates=["aisi_4340_qt", "aisi_1045"],
        expected_survivors=["aisi_4340_qt", "aisi_1045"],
        expected_rejections=[],
        best_candidate="aisi_4340_qt",
        ranking_rationale="4340 ultimate 1080 MPa (margin 1.16) vs 1045 565 MPa (margin 0.13). 4340 has far superior ultimate strength and fracture toughness for lifting gear.",
    ),
    # ── 27. Dental implant (titanium, small-scale biocompatible) ────────────
    GoldenSelectionProblem(
        id="MATE-GS-027",
        title="Dental implant screw — small-diameter biocompatible",
        description="Endosseous dental implant. Small diameter (3.5mm), high cyclic bending loads. Yield >400 MPa needed. Biocompatibility required.",
        requirements={
            "load_cases": [{"id": "implant_yield", "type": "yield", "magnitude": 400.0, "unit": "MPa"}],
            "environment": [{"factor": "oral_cavity", "unit": "C", "range": [5.0, 60.0]}],
            "design_life": {"value": 25, "unit": "years", "reliability_target": 0.999},
            "failure_consequence": "safety_critical",
        },
        candidates=["ti_6al4v_annealed", "ss_316l_annealed"],
        expected_survivors=["ti_6al4v_annealed"],
        expected_rejections=[("ss_316l_annealed", "yield_strength 290")],
        best_candidate="ti_6al4v_annealed",
        ranking_rationale="Ti with margin 1.20 (880 vs 400). 316L rejected — yield 290 < 400. Ti is also the standard for dental implants due to osseointegration.",
    ),
    # ── 28. Precision instrument base (low CTE, dimensional stability) ──────
    GoldenSelectionProblem(
        id="MATE-GS-028",
        title="Optical bench — dimensional stability",
        description="Laser interferometer baseplate. Loads are low (tensile 50 MPa). Dimensional stability over temperature swings is the key constraint.",
        requirements={
            "load_cases": [{"id": "base_tensile", "type": "tensile", "magnitude": 50.0, "unit": "MPa"}],
            "environment": [{"factor": "cleanroom", "unit": "C", "range": [20.0, 22.0]}],
            "design_life": {"value": 30, "unit": "years"},
            "failure_consequence": "noncritical",
        },
        candidates=["alumina_96", "aisi_1045", "aa6061_t6"],
        expected_survivors=["aisi_1045", "aa6061_t6"],
        expected_rejections=[("alumina_96", "no yield_strength")],
        best_candidate="aisi_1045",
        ranking_rationale="Alumina has compressive_strength only, no tensile/yield → rejected. Steel has lower CTE (12e-6/K) than aluminum (23e-6/K), so better dimensional stability. Based on yield margin: steel=5.2, al=4.5.",
    ),
    # ── 29. Oil & gas downhole tool (Inconel, H2S + high strength) ──────────
    GoldenSelectionProblem(
        id="MATE-GS-029",
        title="Downhole logging tool housing — sour service",
        description="Pressure housing for downhole logging in sour gas (H2S) wells. Yield >600 MPa, NACE MR0175 compliance for sulfide stress cracking.",
        requirements={
            "load_cases": [{"id": "housing_yield", "type": "yield", "magnitude": 600.0, "unit": "MPa"}],
            "environment": [{"factor": "sour_gas", "unit": "C", "range": [50.0, 200.0]}],
            "design_life": {"value": 10000, "unit": "hours"},
            "failure_consequence": "safety_critical",
        },
        candidates=["inconel_718", "aisi_4340_qt", "ss_316l_annealed"],
        expected_survivors=["inconel_718", "aisi_4340_qt"],
        expected_rejections=[("ss_316l_annealed", "yield_strength 290")],
        best_candidate="inconel_718",
        ranking_rationale="Inconel 718 is NACE MR0175 compliant for sour service. 4340 not rated for H2S (sulfide stress cracking risk). 316L rejected on yield. Inconel margin 0.83 (1100 vs 600).",
    ),
    # ── 30. Consumer product handle (polymer, ergonomic, low load) ──────────
    GoldenSelectionProblem(
        id="MATE-GS-030",
        title="Power tool handle — overmolded grip",
        description="Ergonomic handle for cordless drill. Tensile >25 MPa, good grip feel, overmolding compatibility, chemical resistance to oils.",
        requirements={
            "load_cases": [{"id": "handle_tensile", "type": "tensile", "magnitude": 25.0, "unit": "MPa"}],
            "environment": [{"factor": "workshop", "unit": "C", "range": [5.0, 45.0]}],
            "design_life": {"value": 10000, "unit": "hours"},
            "failure_consequence": "noncritical",
        },
        candidates=["hdpe", "abs", "pa66_gf30"],
        expected_survivors=["hdpe", "pa66_gf30"],
        expected_rejections=[("abs", "insufficient_context")],
        best_candidate="pa66_gf30",
        ranking_rationale="PA66-GF30 highest margin (6.2 vs 0.2 for HDPE). Glass-filled nylon offers better rigidity, oil resistance, and overmolding compatibility than HDPE. HDPE too flexible for this application.",
    ),
]


__all__ = [
    "EXTRA_MATERIALS",
    "GOLDEN_PROBLEMS",
    "GoldenSelectionProblem",
    "register_extended_materials",
]
