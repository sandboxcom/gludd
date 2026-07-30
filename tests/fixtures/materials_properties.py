"""Small property-data fixtures for the materials engineering expert.

Implements spec MATE-001 §4.1 (material identity/properties) and §5.2
(``MaterialCandidate.properties``). These fixtures are intentionally SMALL —
enough to exercise the :class:`PropertyRecord` shape, the condition-metadata
invariant (MATE-SAFE-003), and the data-hierarchy resolution
(MATE-DEC-003) — not a substitute for the external, versioned property data
packages mandated by spec §12.

Each :class:`MaterialFixture` groups a single material_id's properties under a
shared :class:`FixtureSource` (handbook-grade provenance) and a condition
dict carrying at minimum ``product_form`` and ``temper`` so that the resulting
:class:`PropertyRecord` instances are NOT flagged ``insufficient_context``.

Call :func:`to_property_records` to flatten the fixtures into the runtime
:class:`PropertyRecord` shape used by :class:`PropertyStore`.

Values are drawn from widely-cited handbook grades (ASM Handbook Vol 1/2/8,
ASTM material specs, and MatWeb supplier-grade data sheets). They are
NOMINAL handbook values — never lot-specific or design-allowable data
(MATE-SAFE-003). Use ``basis`` and ``uncertainty`` to communicate the
quality of each value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.materials.property_store import PropertyRecord


@dataclass(frozen=True)
class FixtureSource:
    """Handbook-grade provenance for a fixture (spec §11 / MATE-DEC-003)."""

    source_id: str
    publisher: str
    revision: str
    license: str = "handbook"
    authority: str = "handbook"

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        if self.authority not in ("handbook", "supplier", "estimated", "lot"):
            raise ValueError(f"unknown authority {self.authority!r}")


@dataclass(frozen=True)
class PropertyFixture:
    """A single nominal property value with units, basis, method, uncertainty."""

    name: str
    value: float
    unit: str
    basis: str
    method: str
    uncertainty: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.unit:
            raise ValueError("unit must be non-empty")
        if self.uncertainty <= 0:
            raise ValueError("uncertainty must be strictly positive")
        if self.value <= 0:
            raise ValueError("value must be strictly positive")


@dataclass(frozen=True)
class MaterialFixture:
    """One material's condition, properties, and handbook source."""

    material_id: str
    family: str
    designation: str
    condition: dict[str, Any]
    properties: tuple[PropertyFixture, ...]
    source: FixtureSource

    def __post_init__(self) -> None:
        if not self.material_id:
            raise ValueError("material_id must be non-empty")
        if "product_form" not in self.condition:
            raise ValueError("condition must include product_form (MATE-SAFE-003)")
        if not self.properties:
            raise ValueError("properties must be non-empty")


# ─── Steel alloys (ASM Handbook Vol 1; ASTM A108/A276) ────────────────────────

_ASI_STEEL_SOURCE = FixtureSource(
    source_id="handbook:asm_vol1",
    publisher="ASM International",
    revision="ASM Handbook Vol 1: Properties and Selection, 1990",
)

MATERIAL_PROPERTY_FIXTURES: list[MaterialFixture] = [
    MaterialFixture(
        material_id="aisi_1018",
        family="metal",
        designation="AISI 1018 low-carbon steel",
        condition={"product_form": "cold_drawn", "temper": "as_drawn"},
        source=_ASI_STEEL_SOURCE,
        properties=(
            PropertyFixture("yield_strength", 370.0, "MPa", "yield", "ASTM A108", 25.0),
            PropertyFixture("ultimate_tensile_strength", 440.0, "MPa", "ultimate", "ASTM A108", 30.0),
            PropertyFixture("youngs_modulus", 200.0, "GPa", "nominal", "ASTM E111", 5.0),
            PropertyFixture("density", 7.87, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="aisi_1045",
        family="metal",
        designation="AISI 1045 medium-carbon steel",
        condition={"product_form": "cold_drawn", "temper": "as_drawn"},
        source=_ASI_STEEL_SOURCE,
        properties=(
            PropertyFixture("yield_strength", 530.0, "MPa", "yield", "ASTM A108", 30.0),
            PropertyFixture("ultimate_tensile_strength", 625.0, "MPa", "ultimate", "ASTM A108", 35.0),
            PropertyFixture("youngs_modulus", 200.0, "GPa", "nominal", "ASTM E111", 5.0),
            PropertyFixture("density", 7.85, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="aisi_4140",
        family="metal",
        designation="AISI 4140 Cr-Mo low-alloy steel (Q&T)",
        condition={"product_form": "bar", "temper": "quenched_and_tempered"},
        source=_ASI_STEEL_SOURCE,
        properties=(
            PropertyFixture("yield_strength", 655.0, "MPa", "yield", "ASTM A322", 40.0),
            PropertyFixture("ultimate_tensile_strength", 950.0, "MPa", "ultimate", "ASTM A322", 50.0),
            PropertyFixture("youngs_modulus", 200.0, "GPa", "nominal", "ASTM E111", 5.0),
            PropertyFixture("density", 7.85, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="ss_316l",
        family="metal",
        designation="316L austenitic stainless steel (annealed)",
        condition={"product_form": "sheet", "temper": "annealed"},
        source=_ASI_STEEL_SOURCE,
        properties=(
            PropertyFixture("yield_strength", 290.0, "MPa", "yield", "ASTM A240", 20.0),
            PropertyFixture("ultimate_tensile_strength", 580.0, "MPa", "ultimate", "ASTM A240", 30.0),
            PropertyFixture("youngs_modulus", 193.0, "GPa", "nominal", "ASTM E111", 5.0),
            PropertyFixture("density", 7.99, "g/cm^3", "nominal", "ASTM E1461", 0.02),
        ),
    ),
    # ─── Aluminum alloys (ASM Handbook Vol 2; ASTM B209) ──────────────────────
    MaterialFixture(
        material_id="al_6061_t6",
        family="metal",
        designation="AA 6061-T6 (Al-Mg-Si)",
        condition={"product_form": "sheet", "temper": "T6"},
        source=FixtureSource(
            source_id="handbook:asm_vol2",
            publisher="ASM International",
            revision="ASM Handbook Vol 2: Properties and Selection, 1990",
        ),
        properties=(
            PropertyFixture("yield_strength", 276.0, "MPa", "yield", "ASTM B209", 15.0),
            PropertyFixture("ultimate_tensile_strength", 310.0, "MPa", "ultimate", "ASTM B209", 15.0),
            PropertyFixture("youngs_modulus", 68.9, "GPa", "nominal", "ASTM E111", 2.0),
            PropertyFixture("density", 2.70, "g/cm^3", "nominal", "ASTM E1461", 0.01),
        ),
    ),
    MaterialFixture(
        material_id="al_7075_t6",
        family="metal",
        designation="AA 7075-T6 (Al-Zn-Mg-Cu)",
        condition={"product_form": "sheet", "temper": "T6"},
        source=FixtureSource(
            source_id="handbook:asm_vol2",
            publisher="ASM International",
            revision="ASM Handbook Vol 2: Properties and Selection, 1990",
        ),
        properties=(
            PropertyFixture("yield_strength", 503.0, "MPa", "yield", "ASTM B209", 25.0),
            PropertyFixture("ultimate_tensile_strength", 572.0, "MPa", "ultimate", "ASTM B209", 25.0),
            PropertyFixture("youngs_modulus", 71.7, "GPa", "nominal", "ASTM E111", 2.0),
            PropertyFixture("density", 2.81, "g/cm^3", "nominal", "ASTM E1461", 0.01),
        ),
    ),
    # ─── Polymers (ASM Handbook Vol 8; ISO 527 tensile, ISO 1183 density) ─────
    MaterialFixture(
        material_id="abs",
        family="polymer",
        designation="ABS terpolymer (injection molded, unreinforced)",
        condition={"product_form": "injection_molded", "temper": "as_molded", "moisture": "as_received"},
        source=FixtureSource(
            source_id="handbook:asm_vol8",
            publisher="ASM International",
            revision="ASM Handbook Vol 8: Mechanical Testing, 2000",
        ),
        properties=(
            PropertyFixture("tensile_strength", 41.0, "MPa", "ultimate", "ISO 527-2", 4.0),
            PropertyFixture("youngs_modulus", 2.0, "GPa", "nominal", "ISO 527-2", 0.2),
            PropertyFixture("density", 1.05, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="pc",
        family="polymer",
        designation="Polycarbonate (injection molded)",
        condition={"product_form": "injection_molded", "temper": "as_molded", "moisture": "dried"},
        source=FixtureSource(
            source_id="handbook:asm_vol8",
            publisher="ASM International",
            revision="ASM Handbook Vol 8: Mechanical Testing, 2000",
        ),
        properties=(
            PropertyFixture("tensile_strength", 65.0, "MPa", "ultimate", "ISO 527-2", 5.0),
            PropertyFixture("youngs_modulus", 2.4, "GPa", "nominal", "ISO 527-2", 0.2),
            PropertyFixture("density", 1.20, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="peek",
        family="polymer",
        designation="PEEK (injection molded, unreinforced)",
        condition={"product_form": "injection_molded", "temper": "as_molded", "moisture": "dried"},
        source=FixtureSource(
            source_id="handbook:asm_vol8",
            publisher="ASM International",
            revision="ASM Handbook Vol 8: Mechanical Testing, 2000",
        ),
        properties=(
            PropertyFixture("tensile_strength", 100.0, "MPa", "ultimate", "ISO 527-2", 5.0),
            PropertyFixture("youngs_modulus", 3.6, "GPa", "nominal", "ISO 527-2", 0.3),
            PropertyFixture("density", 1.32, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ),
    ),
    MaterialFixture(
        material_id="nylon_66",
        family="polymer",
        designation="Nylon 6/6 (PA66, conditioned)",
        condition={"product_form": "injection_molded", "temper": "as_molded", "moisture": "conditioned"},
        source=FixtureSource(
            source_id="handbook:asm_vol8",
            publisher="ASM International",
            revision="ASM Handbook Vol 8: Mechanical Testing, 2000",
        ),
        properties=(
            PropertyFixture("tensile_strength", 83.0, "MPa", "ultimate", "ISO 527-2", 5.0),
            PropertyFixture("youngs_modulus", 3.3, "GPa", "nominal", "ISO 527-2", 0.3),
            PropertyFixture("density", 1.14, "g/cm^3", "nominal", "ISO 1183", 0.02),
        ),
    ),
    # ─── Ceramics (ASM Handbook Vol 8; ASTM C773/C1326) ───────────────────────
    MaterialFixture(
        material_id="alumina",
        family="ceramic",
        designation="Alumina Al2O3 99.5% (sintered)",
        condition={"product_form": "sintered", "temper": "as_fired"},
        source=FixtureSource(
            source_id="handbook:asm_vol2",
            publisher="ASM International",
            revision="ASM Handbook Vol 2: Properties and Selection, 1990",
        ),
        properties=(
            PropertyFixture("compressive_strength", 2600.0, "MPa", "ultimate", "ASTM C773", 150.0),
            PropertyFixture("youngs_modulus", 380.0, "GPa", "nominal", "ASTM C1259", 10.0),
            PropertyFixture("density", 3.89, "g/cm^3", "nominal", "ASTM C20", 0.05),
        ),
    ),
    MaterialFixture(
        material_id="zirconia",
        family="ceramic",
        designation="Yttria-stabilized zirconia Y-TZP (sintered)",
        condition={"product_form": "sintered", "temper": "as_fired"},
        source=FixtureSource(
            source_id="handbook:asm_vol2",
            publisher="ASM International",
            revision="ASM Handbook Vol 2: Properties and Selection, 1990",
        ),
        properties=(
            PropertyFixture("compressive_strength", 2000.0, "MPa", "ultimate", "ASTM C773", 150.0),
            PropertyFixture("youngs_modulus", 210.0, "GPa", "nominal", "ASTM C1259", 8.0),
            PropertyFixture("density", 6.05, "g/cm^3", "nominal", "ASTM C20", 0.05),
        ),
    ),
    # ─── Composite (ASM Handbook Vol 21; ASTM D3039/D6641) ────────────────────
    MaterialFixture(
        material_id="carbon_fiber_epoxy",
        family="composite",
        designation="UD carbon-fiber/epoxy prepreg (Vf = 60%, 0 deg)",
        condition={
            "product_form": "laminate",
            "temper": "cured",
            "fiber_volume_fraction": 0.60,
            "direction": "0",
        },
        source=FixtureSource(
            source_id="handbook:asm_vol21",
            publisher="ASM International",
            revision="ASM Handbook Vol 21: Composites, 2001",
        ),
        properties=(
            PropertyFixture("tensile_strength", 1500.0, "MPa", "ultimate", "ASTM D3039", 100.0),
            PropertyFixture("youngs_modulus", 135.0, "GPa", "nominal", "ASTM D3039", 8.0),
            PropertyFixture("density", 1.58, "g/cm^3", "nominal", "ASTM D792", 0.03),
        ),
    ),
]


def to_property_records(
    fixtures: list[MaterialFixture] | None = None,
) -> list[PropertyRecord]:
    """Flatten fixture groups into runtime :class:`PropertyRecord` instances.

    Each property becomes one record keyed by a stable
    ``record_id = "{material_id}.{property_name}"`` so tests can look them up
    deterministically. The shared :class:`FixtureSource` becomes the
    ``source_id`` on every record in the group.
    """
    src = fixtures if fixtures is not None else MATERIAL_PROPERTY_FIXTURES
    records: list[PropertyRecord] = []
    for mat in src:
        for prop in mat.properties:
            records.append(
                PropertyRecord(
                    record_id=f"{mat.material_id}.{prop.name}",
                    material_id=mat.material_id,
                    name=prop.name,
                    value=prop.value,
                    unit=prop.unit,
                    basis=prop.basis,
                    method=prop.method,
                    uncertainty=prop.uncertainty,
                    conditions=dict(mat.condition),
                    source_id=mat.source.source_id,
                )
            )
    return records


__all__ = [
    "FixtureSource",
    "MATERIAL_PROPERTY_FIXTURES",
    "MaterialFixture",
    "PropertyFixture",
    "to_property_records",
]
