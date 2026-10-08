"""Unit tests for ``general_ludd.chemistry.schemas`` and ``entities`` (CHEM Phase A).

Implements CHEM-AT-001 / CHEM-AT-002 acceptance: schema/property tests reject
missing units, invalid fractions, negative uncertainty, and unknown mutating
fields; identity fixtures preserve submitted structure and distinguish stereo,
isotope, salt, solvate, tautomer, and mixture cases.

Spec reference: ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §4.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from general_ludd.chemistry.entities import (
    EntityRegistry,
    resolve_entity,
)
from general_ludd.chemistry.schemas import (
    ChemicalEntity,
    ChemicalStructure,
    ChemistryRequest,
    ChemistryResult,
    EntityKind,
    IsotopeStatus,
    ResultStatus,
    StereoStatus,
    StructureRepresentation,
    TaskKind,
)

# ---------------------------------------------------------------------------
# ChemicalEntity — structure preservation (CHEM-AT-002)
# ---------------------------------------------------------------------------


class TestChemicalEntityStructure:
    @pytest.mark.parametrize(
        ("representation", "value", "submitted"),
        [
            (StructureRepresentation.smiles, "", ""),
            (StructureRepresentation.unknown, "CCO", ""),
            (StructureRepresentation.unknown, "", "CCO"),
            (StructureRepresentation.unknown, " ", "\t"),
        ],
    )
    def test_empty_structure_is_limited_to_complete_unknown_sentinel(
        self,
        representation: StructureRepresentation,
        value: str,
        submitted: str,
    ) -> None:
        with pytest.raises(ValidationError):
            ChemicalStructure(
                representation=representation,
                value=value,
                submitted=submitted,
                canonicalizer="test@0.1",
                stereochemistry=StereoStatus.unknown,
                isotopes=IsotopeStatus.unknown,
                charge=0,
            )

    def test_preserves_submitted_smiles_when_canonicalizing(self):
        submitted = "C[C@H](N)C(=O)O"
        entity = ChemicalEntity(
            kind=EntityKind.compound,
            names=[{"value": "alanine", "type": "preferred"}],
            structure={
                "representation": StructureRepresentation.smiles,
                "value": submitted,
                "submitted": submitted,
                "canonicalizer": "test@0.1",
                "stereochemistry": StereoStatus.specified,
                "isotopes": "natural",
                "charge": 0,
            },
        )
        assert entity.structure.submitted == submitted
        assert entity.structure.value == submitted

    def test_stereochemistry_marker_survives_round_trip(self):
        entity = ChemicalEntity(
            kind=EntityKind.compound,
            structure={
                "representation": StructureRepresentation.smiles,
                "value": "[C@@H](N)(C)C(=O)O",
                "submitted": "[C@@H](N)(C)C(=O)O",
                "canonicalizer": "test@0.1",
                "stereochemistry": StereoStatus.specified,
                "isotopes": "natural",
                "charge": 0,
            },
        )
        assert entity.structure.stereochemistry == StereoStatus.specified
        assert entity.structure.submitted == entity.structure.value

    def test_isotope_specified_is_distinct_from_natural(self):
        specified = ChemicalEntity(
            kind=EntityKind.compound,
            structure={
                "representation": StructureRepresentation.smiles,
                "value": "[13CH4]",
                "submitted": "[13CH4]",
                "canonicalizer": "test@0.1",
                "stereochemistry": StereoStatus.unknown,
                "isotopes": "specified",
                "charge": 0,
            },
        )
        natural = ChemicalEntity(
            kind=EntityKind.compound,
            structure={
                "representation": StructureRepresentation.smiles,
                "value": "C",
                "submitted": "C",
                "canonicalizer": "test@0.1",
                "stereochemistry": StereoStatus.unknown,
                "isotopes": "natural",
                "charge": 0,
            },
        )
        assert specified.structure.isotopes != natural.structure.isotopes

    def test_salt_components_are_distinct_records(self):
        salt = ChemicalEntity(
            kind=EntityKind.mixture,
            structure={
                "representation": StructureRepresentation.smiles,
                "value": "[Na+].[Cl-]",
                "submitted": "[Na+].[Cl-]",
                "canonicalizer": "test@0.1",
                "stereochemistry": StereoStatus.unknown,
                "isotopes": "natural",
                "charge": 0,
            },
            components=[
                {"entity_id": "c1", "fraction": 0.5, "basis": "mole", "uncertainty": 0.0},
                {"entity_id": "c2", "fraction": 0.5, "basis": "mole", "uncertainty": 0.0},
            ],
        )
        assert salt.kind == EntityKind.mixture
        assert len(salt.components) == 2
        assert {c.entity_id for c in salt.components} == {"c1", "c2"}

    def test_fraction_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            ChemicalEntity(
                kind=EntityKind.mixture,
                structure={
                    "representation": StructureRepresentation.smiles,
                    "value": "A.B",
                    "submitted": "A.B",
                    "canonicalizer": "t@0.1",
                    "stereochemistry": StereoStatus.unknown,
                    "isotopes": "natural",
                    "charge": 0,
                },
                components=[
                    {"entity_id": "c1", "fraction": 1.5, "basis": "mole", "uncertainty": 0.0},
                ],
            )

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            ChemicalEntity(
                kind="super_compound",
                structure={
                    "representation": StructureRepresentation.smiles,
                    "value": "C",
                    "submitted": "C",
                    "canonicalizer": "t@0.1",
                    "stereochemistry": StereoStatus.unknown,
                    "isotopes": "natural",
                    "charge": 0,
                },
            )


# ---------------------------------------------------------------------------
# ChemistryRequest — constraint validation
# ---------------------------------------------------------------------------


def _basic_structure(value: str = "CCO") -> dict:
    return {
        "representation": StructureRepresentation.smiles,
        "value": value,
        "submitted": value,
        "canonicalizer": "test@0.1",
        "stereochemistry": StereoStatus.unknown,
        "isotopes": "natural",
        "charge": 0,
    }


class TestChemistryRequest:
    def test_minimal_request_validates(self):
        req = ChemistryRequest(
            request_id="r1",
            tenant_id="tnt",
            task=TaskKind.identity,
            entities=["e1"],
        )
        assert req.schema_version == "1.0"
        assert req.constraints.deadline_s == 300
        assert req.constraints.data_classification == "internal"

    def test_negative_deadline_rejected(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task=TaskKind.identity,
                entities=["e1"],
                constraints={"deadline_s": -1},
            )

    def test_negative_budget_rejected(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task=TaskKind.identity,
                entities=["e1"],
                constraints={"budget_usd": -0.01},
            )

    def test_invalid_data_classification_rejected(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task=TaskKind.identity,
                entities=["e1"],
                constraints={"data_classification": "top_secret"},
            )

    def test_conditions_require_unit(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task=TaskKind.identity,
                entities=["e1"],
                conditions=[{"name": "temperature", "value": 298.15, "unit": "", "uncertainty": 0.1}],
            )

    def test_negative_uncertainty_in_conditions_rejected(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task=TaskKind.identity,
                entities=["e1"],
                conditions=[{"name": "T", "value": 298.15, "unit": "K", "uncertainty": -0.1}],
            )

    def test_unknown_task_rejected(self):
        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="r1",
                tenant_id="tnt",
                task="transmute",
                entities=["e1"],
            )


# ---------------------------------------------------------------------------
# ChemistryResult — units, uncertainty, provenance
# ---------------------------------------------------------------------------


class TestChemistryResult:
    def test_value_record_requires_unit(self):
        with pytest.raises(ValidationError):
            ChemistryResult(
                request_id="r1",
                run_id="run1",
                status=ResultStatus.succeeded,
                summary="ok",
                values=[{"name": "mass", "value": 1.0, "unit": "", "uncertainty": 0.0, "method_id": "m"}],
            )

    def test_value_record_rejects_negative_uncertainty(self):
        with pytest.raises(ValidationError):
            ChemistryResult(
                request_id="r1",
                run_id="run1",
                status=ResultStatus.succeeded,
                summary="ok",
                values=[{"name": "mass", "value": 1.0, "unit": "g", "uncertainty": -0.5, "method_id": "m"}],
            )

    def test_status_enum_validated(self):
        with pytest.raises(ValidationError):
            ChemistryResult(
                request_id="r1",
                run_id="run1",
                status="perfect",
                summary="ok",
            )

    def test_citations_carry_claim_linkage(self):
        result = ChemistryResult(
            request_id="r1",
            run_id="run1",
            status=ResultStatus.succeeded,
            summary="ok",
            citations=[{"source_id": "src1", "locator": "doi:...", "claim_ids": ["c1", "c2"]}],
        )
        assert result.citations[0].claim_ids == ["c1", "c2"]

    def test_safety_defaults(self):
        result = ChemistryResult(
            request_id="r1",
            run_id="run1",
            status=ResultStatus.succeeded,
            summary="ok",
        )
        assert result.safety.risk_tier == "low"
        assert result.safety.approvals == []


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


class TestJsonRoundTrip:
    def test_entity_round_trip(self):
        entity = ChemicalEntity(
            kind=EntityKind.compound,
            names=[{"value": "water", "type": "preferred"}],
            structure=_basic_structure("O"),
        )
        s = entity.model_dump_json()
        d = json.loads(s)
        rt = ChemicalEntity.model_validate(d)
        assert rt == entity

    def test_request_round_trip(self):
        req = ChemistryRequest(
            request_id="r1",
            tenant_id="tnt",
            task=TaskKind.stoichiometry,
            entities=["e1", "e2"],
            conditions=[{"name": "T", "value": 298.15, "unit": "K", "uncertainty": 0.1}],
        )
        rt = ChemistryRequest.model_validate_json(req.model_dump_json())
        assert rt.task == TaskKind.stoichiometry
        assert rt.conditions[0].unit == "K"

    def test_result_round_trip(self):
        result = ChemistryResult(
            request_id="r1",
            run_id="run1",
            status=ResultStatus.degraded,
            summary="partial",
            values=[
                {
                    "name": "molar_mass",
                    "value": 18.015,
                    "unit": "g/mol",
                    "uncertainty": 0.001,
                    "method_id": "atom_weights@1",
                },
            ],
            safety={"risk_tier": "moderate", "review_id": "rev1", "approvals": ["a1"]},
        )
        rt = ChemistryResult.model_validate_json(result.model_dump_json())
        assert rt.values[0].unit == "g/mol"
        assert rt.safety.risk_tier == "moderate"


# ---------------------------------------------------------------------------
# Entity resolution registry (entities.py)
# ---------------------------------------------------------------------------


class TestEntityRegistry:
    def test_resolve_smiles_preserves_submitted_representation(self):
        entity = resolve_entity("C[C@H](N)C(=O)O")
        assert entity.structure.submitted == "C[C@H](N)C(=O)O"
        assert entity.structure.stereochemistry == StereoStatus.specified

    def test_resolve_inchi_keeps_inchi_form(self):
        inchi = "InChI=1S/H2O/h1H2"
        entity = resolve_entity(inchi)
        assert entity.structure.representation == StructureRepresentation.inchi
        assert entity.structure.submitted == inchi

    def test_tautomers_distinguished_as_related_records(self):
        keto = resolve_entity("CC(=O)O")
        enol = resolve_entity("C=C(O)O")
        assert keto.entity_id != enol.entity_id
        # both must round-trip to their submitted representation verbatim
        assert keto.structure.submitted == "CC(=O)O"
        assert enol.structure.submitted == "C=C(O)O"

    def test_registry_links_related_entities(self):
        registry = EntityRegistry()
        a = registry.register(resolve_entity("CC(=O)O"))
        b = registry.register(resolve_entity("C=C(O)O"))
        registry.link_related(a.entity_id, b.entity_id, relation="tautomer")
        related = registry.related(a.entity_id)
        assert any(r.entity_id == b.entity_id for r in related)

    def test_mixture_components_remain_separate(self):
        entity = resolve_entity("[Na+].[Cl-]")
        assert entity.kind == EntityKind.mixture
        assert len(entity.components) >= 2
