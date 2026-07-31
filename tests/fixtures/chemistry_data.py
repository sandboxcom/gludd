"""Small chemistry dataset fixtures for the chemistry expert tests.

These are deliberately small, hand-curated datasets (NOT full databases) that
support unit tests for ``src/general_ludd/chemistry/core.py``. Each record
mirrors the immutable-registry shape described in
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §5 (Knowledge/Resource Registries):
stable id, source locator, license, validation state.

The module is loaded by file path from
``tests/unit/test_chemistry_fixtures.py`` (same pattern as
``test_chemistry_core.py``) so it does not need to live on ``sys.path``.

Datasets provided:

* ``CHEMICALS`` -- 25 common chemicals with SMILES, Hill formula, molar
  mass (g/mol), CAS registry number, and PubChem CID.
* ``HAZARDS`` -- NFPA-704-style ratings (health/toxicity, flammability,
  reactivity) and a gludd ``risk_tier`` for 12 chemicals.
* ``INCOMPATIBILITIES`` -- 12 chemical-incompatibility pairs, stored
  bidirectionally (A→B and B→A).
* ``REACTIONS`` -- 6 textbook reactions with atom-balanced equations.
"""

from __future__ import annotations

from typing import Any, NamedTuple

SCHEMA_VERSION = "1.0"
FIXTURE_SOURCE = "tests/fixtures/chemistry_data.py"
FIXTURE_LICENSE = "CC0-1.0"


class Chemical(NamedTuple):
    key: str
    smiles: str
    formula: str
    molar_mass: float
    cas: str
    pubchem_cid: int
    names: tuple[str, ...]


CHEMICALS: tuple[Chemical, ...] = (
    Chemical("water", "O", "H2O", 18.015, "7732-18-5", 962, ("water", "oxidane")),
    Chemical("methane", "C", "CH4", 16.043, "74-82-8", 297, ("methane", "marsh gas")),
    Chemical("ethane", "CC", "C2H6", 30.070, "74-84-0", 6324, ("ethane", "dimethyl")),
    Chemical("ethanol", "CCO", "C2H6O", 46.069, "64-17-5", 702, ("ethanol", "ethyl alcohol")),
    Chemical("methanol", "CO", "CH4O", 32.042, "67-56-1", 887, ("methanol", "wood alcohol")),
    Chemical("acetone", "CC(=O)C", "C3H6O", 58.080, "67-64-1", 180, ("acetone", "propan-2-one")),
    Chemical("benzene", "c1ccccc1", "C6H6", 78.114, "71-43-2", 241, ("benzene", "benzol")),
    Chemical("toluene", "Cc1ccccc1", "C7H8", 92.141, "108-88-3", 1140, ("toluene", "methylbenzene")),
    Chemical("ammonia", "N", "NH3", 17.031, "7664-41-7", 222, ("ammonia", "azane")),
    Chemical("hydrogen_chloride", "Cl", "HCl", 36.461, "7647-01-0", 313, ("hydrogen chloride", "hydrochloric acid")),
    Chemical(
        "sodium_hydroxide", "[Na+].[OH-]", "NaOH", 39.998, "1310-73-2", 14798, ("sodium hydroxide", "caustic soda")
    ),
    Chemical("sulfuric_acid", "OS(=O)(=O)O", "H2SO4", 98.079, "7664-93-9", 1118, ("sulfuric acid", "oil of vitriol")),
    Chemical("nitric_acid", "[N+](=O)(O)[O-]", "HNO3", 63.013, "7697-37-2", 944, ("nitric acid", "aqua fortis")),
    Chemical("acetic_acid", "CC(=O)O", "C2H4O2", 60.052, "64-19-7", 176, ("acetic acid", "ethanoic acid")),
    Chemical(
        "glucose",
        "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
        "C6H12O6",
        180.156,
        "50-99-7",
        5793,
        ("glucose", "dextrose"),
    ),
    Chemical(
        "sucrose",
        "C(C1C(C(C(C(O1)OC2(C(C(C(O2)CO)O)O)CO)O)O)O)O",
        "C12H22O11",
        342.303,
        "57-50-1",
        5988,
        ("sucrose", "table sugar"),
    ),
    Chemical(
        "caffeine",
        "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
        "C8H10N4O2",
        194.193,
        "58-08-2",
        2519,
        ("caffeine", "1,3,7-trimethylxanthine"),
    ),
    Chemical(
        "aspirin", "CC(=O)OC1=CC=CC=C1C(=O)O", "C9H8O4", 180.158, "50-78-2", 2244, ("aspirin", "acetylsalicylic acid")
    ),
    Chemical("sodium_chloride", "[Na+].[Cl-]", "NaCl", 58.443, "7647-14-5", 5234, ("sodium chloride", "halite")),
    Chemical("hydrogen_peroxide", "OO", "H2O2", 34.015, "7722-84-1", 784, ("hydrogen peroxide", "dioxidane")),
    Chemical(
        "potassium_permanganate",
        "[K+].[O-][Mn](=O)(=O)=O",
        "KMnO4",
        158.034,
        "7722-64-7",
        516875,
        ("potassium permanganate", "permanganate of potash"),
    ),
    Chemical("sodium_metal", "[Na]", "Na", 22.990, "7440-23-5", 5360545, ("sodium metal", "natrium")),
    Chemical("carbon_dioxide", "O=C=O", "CO2", 44.009, "124-38-9", 280, ("carbon dioxide", "dry ice")),
    Chemical("diethyl_ether", "CCOCC", "C4H10O", 74.123, "60-29-7", 3283, ("diethyl ether", "ether")),
    Chemical("calcium_carbonate", "", "CaCO3", 100.087, "471-34-1", 10112, ("calcium carbonate", "limestone")),
)


class Hazard(NamedTuple):
    key: str
    health: int  # NFPA health/toxicity 0..4
    flammability: int  # NFPA flammability 0..4
    reactivity: int  # NFPA instability/reactivity 0..4
    risk_tier: str  # low | moderate | high | prohibited (spec §9)
    notes: str


HAZARDS: tuple[Hazard, ...] = (
    Hazard("water", 0, 0, 0, "low", "minimal hazard"),
    Hazard("ethanol", 2, 3, 0, "moderate", "flammable CNS depressant"),
    Hazard("methanol", 3, 3, 0, "moderate", "flammable, toxic by ingestion/metabolism"),
    Hazard("acetone", 1, 3, 0, "moderate", "highly flammable, mild irritant"),
    Hazard("benzene", 2, 3, 0, "high", "carcinogen, flammable"),
    Hazard("diethyl_ether", 2, 4, 1, "high", "extremely flammable, peroxide former"),
    Hazard("sulfuric_acid", 3, 0, 2, "high", "strong corrosive acid, oxidizer, exothermic with water"),
    Hazard("nitric_acid", 4, 0, 3, "high", "strong corrosive oxidizer"),
    Hazard("sodium_hydroxide", 3, 0, 1, "moderate", "strongly corrosive base"),
    Hazard("hydrogen_peroxide", 3, 0, 3, "high", "strong oxidizer"),
    Hazard("potassium_permanganate", 2, 0, 2, "high", "strong oxidizer, fire risk with organics"),
    Hazard("sodium_metal", 3, 4, 3, "high", "water-reactive, corrosive, flammable in contact with water"),
)


class Incompatibility(NamedTuple):
    a: str
    b: str
    kind: str
    severity: str  # low | moderate | high | prohibited


_RAW_INCOMPATIBILITIES: tuple[Incompatibility, ...] = (
    Incompatibility("potassium_permanganate", "ethanol", "oxidizer_reducer", "prohibited"),
    Incompatibility("potassium_permanganate", "acetone", "oxidizer_reducer", "prohibited"),
    Incompatibility("hydrogen_peroxide", "ethanol", "oxidizer_reducer", "prohibited"),
    Incompatibility("sulfuric_acid", "sodium_hydroxide", "acid_base_exotherm", "moderate"),
    Incompatibility("nitric_acid", "sodium_hydroxide", "acid_base_exotherm", "moderate"),
    Incompatibility("sulfuric_acid", "sodium_metal", "acid_metal", "prohibited"),
    Incompatibility("sodium_metal", "water", "metal_water", "prohibited"),
    Incompatibility("nitric_acid", "ethanol", "oxidizer_flammable", "prohibited"),
    Incompatibility("sulfuric_acid", "potassium_permanganate", "strong_oxidizer_strong_acid", "high"),
    Incompatibility("hydrogen_peroxide", "sodium_hydroxide", "oxidizer_base", "high"),
    Incompatibility("diethyl_ether", "sulfuric_acid", "peroxide_acid", "high"),
    Incompatibility("nitric_acid", "diethyl_ether", "oxidizer_flammable", "prohibited"),
)


def _expand_bidirectional(pairs: tuple[Incompatibility, ...]) -> tuple[Incompatibility, ...]:
    out: list[Incompatibility] = []
    for p in pairs:
        out.append(p)
        out.append(Incompatibility(a=p.b, b=p.a, kind=p.kind, severity=p.severity))
    return tuple(out)


INCOMPATIBILITIES: tuple[Incompatibility, ...] = _expand_bidirectional(_RAW_INCOMPATIBILITIES)


class Reaction(NamedTuple):
    key: str
    reactants: tuple[tuple[str, int], ...]  # (formula_or_smiles, stoich coeff)
    products: tuple[tuple[str, int], ...]
    description: str


REACTIONS: tuple[Reaction, ...] = (
    Reaction(
        "combustion_hydrogen",
        (("H2", 2), ("O2", 1)),
        (("H2O", 2),),
        "Haber-style hydrogen combustion; canonical textbook balance",
    ),
    Reaction(
        "combustion_methane",
        (("CH4", 1), ("O2", 2)),
        (("CO2", 1), ("H2O", 2)),
        "complete combustion of methane",
    ),
    Reaction(
        "haber_ammonia",
        (("N2", 1), ("H2", 3)),
        (("NH3", 2),),
        "Haber-Bosch nitrogen fixation",
    ),
    Reaction(
        "neutralization_hcl_naoh",
        (("HCl", 1), ("NaOH", 1)),
        (("NaCl", 1), ("H2O", 1)),
        "acid-base neutralization to salt and water",
    ),
    Reaction(
        "decompose_calcium_carbonate",
        (("CaCO3", 1),),
        (("CaO", 1), ("CO2", 1)),
        "thermal decomposition of limestone",
    ),
    Reaction(
        "rusting_iron",
        (("Fe", 4), ("O2", 3)),
        (("Fe2O3", 2),),
        "formation of iron(III) oxide (hematite)",
    ),
)


def all_fixture_records() -> dict[str, Any]:
    """Return the entire fixture corpus as a single dict (introspection helper)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": FIXTURE_SOURCE,
        "license": FIXTURE_LICENSE,
        "chemicals": CHEMICALS,
        "hazards": HAZARDS,
        "incompatibilities": INCOMPATIBILITIES,
        "reactions": REACTIONS,
    }
