"""Unit tests for general_ludd.chemistry.entities."""

from __future__ import annotations

import pytest

from general_ludd.chemistry.entities import (
    EntityRegistry,
    RelatedRecord,
    _detect_representation,
    _dict_to_entity,
    _isotope_status_from_core,
    _stereo_status_from_core,
    resolve_entity,
)
from general_ludd.chemistry.schemas import (
    ChemicalEntity,
    ChemicalStructure,
    EntityKind,
    IsotopeStatus,
    StereoStatus,
    StructureRepresentation,
    ValidationStatus,
)

# ── _detect_representation ────────────────────────────────────────────────


def test_detect_representation_inchi():
    assert _detect_representation("InChI=1S/H2O/h1H2") == StructureRepresentation.inchi
    assert _detect_representation("inchi=1S/CH4/c1/h1H4") == StructureRepresentation.inchi


def test_detect_representation_molfile():
    assert _detect_representation("\n  M  END\n") == StructureRepresentation.molfile
    assert _detect_representation("  V2000\n  1\n") == StructureRepresentation.molfile


def test_detect_representation_smiles_fallback():
    assert _detect_representation("CCO") == StructureRepresentation.smiles
    assert _detect_representation("c1ccccc1") == StructureRepresentation.smiles
    assert _detect_representation("") == StructureRepresentation.smiles


# ── _stereo_status_from_core ────────────────────────────────────────────────


def test_stereo_status_from_core_specified():
    assert _stereo_status_from_core("C[C@H](O)") == StereoStatus.specified
    assert _stereo_status_from_core("C/C=C/C") == StereoStatus.specified
    assert _stereo_status_from_core("C\\C=C\\C") == StereoStatus.specified


def test_stereo_status_from_core_partial():
    assert _stereo_status_from_core("C(=O)O") == StereoStatus.partial


def test_stereo_status_from_core_unknown():
    assert _stereo_status_from_core("CCO") == StereoStatus.unknown
    assert _stereo_status_from_core("anything_else") == StereoStatus.unknown


# ── _isotope_status_from_core ───────────────────────────────────────────────


def test_isotope_status_from_core_specified():
    assert _isotope_status_from_core("[13C]") == IsotopeStatus.specified


def test_isotope_status_from_core_unknown():
    assert _isotope_status_from_core("CCO") == IsotopeStatus.unknown


def test_isotope_status_from_core_natural():
    assert _isotope_status_from_core("O") == IsotopeStatus.natural
    assert _isotope_status_from_core("CC") == IsotopeStatus.natural


# ── _dict_to_entity ────────────────────────────────────────────────────────


def test_dict_to_entity_full_record():
    record = {
        "entity_id": "chem-001",
        "kind": "compound",
        "names": [
            {"value": "water", "type": "preferred"},
            {"value": "oxidane", "type": "systematic"},
        ],
        "structure": {
            "representation": "smiles",
            "value": "O",
            "submitted": "water",
            "canonicalizer": "chemistry-core@0.1.0",
            "stereochemistry": "unknown",
            "isotopes": "natural",
            "charge": 0,
        },
        "components": [],
        "identifiers": [
            {"scheme": "formula", "value": "H2O"},
        ],
        "validation": [
            {"check": "name_lookup", "status": "pass"},
        ],
    }
    entity = _dict_to_entity(record)
    assert isinstance(entity, ChemicalEntity)
    assert entity.entity_id == "chem-001"
    assert entity.kind == EntityKind.compound
    assert len(entity.names) == 2
    assert entity.names[0].value == "water"
    assert entity.names[1].value == "oxidane"
    assert entity.structure.value == "O"
    assert entity.structure.submitted == "water"
    assert entity.structure.stereochemistry == StereoStatus.unknown
    assert entity.structure.isotopes == IsotopeStatus.natural
    assert entity.structure.charge == 0
    assert len(entity.identifiers) == 1
    assert entity.identifiers[0].scheme == "formula"
    assert entity.identifiers[0].value == "H2O"
    assert len(entity.validation) == 1
    assert entity.validation[0].check == "name_lookup"
    assert entity.validation[0].status == ValidationStatus.pass_


def test_dict_to_entity_missing_structure_uses_empty():
    record: dict = {"kind": "compound"}
    entity = _dict_to_entity(record)
    assert isinstance(entity.entity_id, str)
    assert len(entity.entity_id) == 36
    assert entity.kind == EntityKind.compound
    assert entity.structure.value == ""
    assert entity.structure.submitted == ""
    assert entity.structure.representation == StructureRepresentation.unknown
    assert entity.structure.charge == 0
    assert entity.names == []
    assert entity.components == []
    assert entity.identifiers == []
    assert entity.validation == []


def test_dict_to_entity_kind_fallback():
    record = {"kind": "nonexistent", "structure": {"value": "O", "submitted": "O"}}
    entity = _dict_to_entity(record)
    assert entity.kind == EntityKind.compound


def test_dict_to_entity_components():
    record = {
        "kind": "mixture",
        "structure": {"value": "O.[Na+].[Cl-]", "submitted": "O.[Na+].[Cl-]"},
        "components": [
            {"entity_id": "a", "fraction": 0.5, "basis": "mole", "uncertainty": 0.01},
            {"entity_id": "b", "fraction": 0.3, "basis": "mass", "uncertainty": 0.0},
        ],
    }
    entity = _dict_to_entity(record)
    assert len(entity.components) == 2
    assert entity.components[0].entity_id == "a"
    assert entity.components[0].fraction == 0.5
    assert entity.components[0].uncertainty == 0.01
    assert entity.components[1].entity_id == "b"
    assert entity.components[1].fraction == 0.3


def test_dict_to_entity_component_invalid_basis_defaults_to_mole():
    record = {
        "kind": "mixture",
        "structure": {"value": "O.N", "submitted": "O.N"},
        "components": [
            {"entity_id": "a", "fraction": 0.5, "basis": "bad_basis"},
        ],
    }
    entity = _dict_to_entity(record)
    assert entity.components[0].basis.value == "mole"


def test_dict_to_entity_names_filter_empty_value():
    record = {
        "kind": "compound",
        "structure": {"value": "O", "submitted": "O"},
        "names": [
            {"value": "water", "type": "preferred"},
            {"value": "", "type": "systematic"},
            {"value": "oxidane", "type": "systematic"},
        ],
    }
    entity = _dict_to_entity(record)
    assert len(entity.names) == 2
    assert entity.names[0].value == "water"
    assert entity.names[1].value == "oxidane"


def test_dict_to_entity_identifiers_filter_empty_value():
    record = {
        "kind": "compound",
        "structure": {"value": "O", "submitted": "O"},
        "identifiers": [
            {"scheme": "formula", "value": "H2O"},
            {"scheme": "formula", "value": ""},
        ],
    }
    entity = _dict_to_entity(record)
    assert len(entity.identifiers) == 1


def test_dict_to_entity_validation_bad_status_defaults_to_warning():
    record = {
        "kind": "compound",
        "structure": {"value": "O", "submitted": "O"},
        "validation": [
            {"check": "some_check", "status": "not_a_real_status"},
        ],
    }
    entity = _dict_to_entity(record)
    assert entity.validation[0].status == ValidationStatus.warning


def test_dict_to_entity_mixture_kind():
    record = {
        "kind": "mixture",
        "structure": {"value": "O.N", "submitted": "O.N"},
    }
    entity = _dict_to_entity(record)
    assert entity.kind == EntityKind.mixture


def test_dict_to_entity_polymer_kind():
    record = {
        "kind": "polymer",
        "structure": {"value": "CCO", "submitted": "CCO"},
    }
    entity = _dict_to_entity(record)
    assert entity.kind == EntityKind.polymer


def test_dict_to_entity_submitted_falls_back_to_value():
    record = {
        "kind": "compound",
        "structure": {"value": "CCO"},
    }
    entity = _dict_to_entity(record)
    assert entity.structure.submitted == "CCO"
    assert entity.structure.value == "CCO"


def test_dict_to_entity_component_with_exception_tolerated():
    record = {
        "kind": "mixture",
        "structure": {"value": "O", "submitted": "O"},
        "components": [
            None,
            {"entity_id": "b", "fraction": 0.5},
        ],
    }
    entity = _dict_to_entity(record)
    assert len(entity.components) == 1
    assert entity.components[0].entity_id == "b"


# ── resolve_entity ─────────────────────────────────────────────────────────


def test_resolve_entity_common_name_water():
    entity = resolve_entity("water")
    assert isinstance(entity, ChemicalEntity)
    assert entity.structure.submitted == "water"
    assert entity.structure.value == "O"
    name_values = {n.value for n in entity.names}
    assert "water" in name_values
    assert "oxidane" in name_values
    validation_checks = {v.check for v in entity.validation}
    assert "name_lookup" in validation_checks


def test_resolve_entity_smiles_string():
    entity = resolve_entity("CCO")
    assert entity.structure.submitted == "CCO"
    assert entity.structure.value == "CCO"
    assert entity.structure.representation == StructureRepresentation.smiles


def test_resolve_entity_dict_input():
    entity = resolve_entity({"query": "ethanol"})
    assert entity.structure.submitted == "ethanol"
    assert entity.structure.value == "CCO"


def test_resolve_entity_empty_string():
    entity = resolve_entity("")
    assert entity.structure.representation == StructureRepresentation.unknown
    assert entity.structure.value == ""
    assert entity.structure.submitted == ""
    assert entity.validation[0].status == ValidationStatus.fail
    assert entity.validation[0].check == "identity_resolution"


def test_resolve_entity_none_like_query_in_dict():
    entity = resolve_entity({"query": ""})
    assert entity.structure.value == ""
    assert entity.validation[0].status == ValidationStatus.fail


def test_resolve_entity_preserves_submitted_exact_text():
    entity = resolve_entity("   ethanol   ")
    assert entity.structure.submitted == "ethanol"


def test_resolve_entity_stereo_isotope_in_smiles():
    entity = resolve_entity("C[C@H](O)CO")
    assert entity.structure.stereochemistry == StereoStatus.specified
    assert entity.structure.isotopes == IsotopeStatus.natural


def test_resolve_entity_salt_mixture():
    entity = resolve_entity("sodium chloride")
    assert entity.kind == EntityKind.mixture
    assert len(entity.components) >= 2


def test_resolve_entity_unknown_name_still_produces_entity():
    entity = resolve_entity("nonexistent_molecule_xyz")
    assert isinstance(entity, ChemicalEntity)
    assert entity.structure.submitted == "nonexistent_molecule_xyz"
    assert len(entity.names) >= 1


# ── EntityRegistry ─────────────────────────────────────────────────────────


def make_entity(entity_id: str = "test-001") -> ChemicalEntity:
    return ChemicalEntity(
        entity_id=entity_id,
        kind=EntityKind.compound,
        structure=ChemicalStructure(
            representation=StructureRepresentation.smiles,
            value="CCO",
            submitted="CCO",
            canonicalizer="chemistry-core@0.1.0",
            stereochemistry=StereoStatus.unknown,
            isotopes=IsotopeStatus.natural,
            charge=0,
        ),
    )


def test_registry_register_and_get():
    reg = EntityRegistry()
    e = make_entity("id-a")
    reg.register(e)
    result = reg.get("id-a")
    assert result is not None
    assert result.entity_id == "id-a"


def test_registry_get_missing_returns_none():
    reg = EntityRegistry()
    assert reg.get("nonexistent") is None


def test_registry_upsert_overwrites():
    reg = EntityRegistry()
    e1 = make_entity("dup")
    e2 = make_entity("dup")
    e2.structure.value = "CCCC"
    reg.register(e1)
    reg.register(e2)
    result = reg.get("dup")
    assert result is not None
    assert result.structure.value == "CCCC"


def test_registry_link_related_both_directions():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    reg.register(make_entity("b"))
    reg.link_related("a", "b", "tautomer")
    a_rels = reg.related("a")
    b_rels = reg.related("b")
    assert len(a_rels) == 1
    assert a_rels[0].entity_id == "b"
    assert a_rels[0].relation == "tautomer"
    assert len(b_rels) == 1
    assert b_rels[0].entity_id == "a"
    assert b_rels[0].relation == "inverse:tautomer"


def test_registry_link_related_multiple():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    reg.register(make_entity("b"))
    reg.register(make_entity("c"))
    reg.link_related("a", "b", "tautomer")
    reg.link_related("a", "c", "stereoisomer")
    a_rels = reg.related("a")
    assert len(a_rels) == 2
    rel_labels = {r.relation for r in a_rels}
    assert rel_labels == {"tautomer", "stereoisomer"}


def test_registry_link_related_unknown_source():
    reg = EntityRegistry()
    reg.register(make_entity("b"))
    with pytest.raises(KeyError, match="unknown source entity"):
        reg.link_related("a", "b", "tautomer")


def test_registry_link_related_unknown_target():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    with pytest.raises(KeyError, match="unknown target entity"):
        reg.link_related("a", "b", "tautomer")


def test_registry_related_preserves_label_verbatim():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    reg.register(make_entity("b"))
    reg.link_related("a", "b", "salt_of")
    a_rels = reg.related("a")
    assert a_rels[0].relation == "salt_of"


def test_registry_related_no_relations():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    assert reg.related("a") == []


def test_registry_related_unregistered_returns_empty():
    reg = EntityRegistry()
    assert reg.related("ghost") == []


def test_registry_all_entities():
    reg = EntityRegistry()
    reg.register(make_entity("a"))
    reg.register(make_entity("b"))
    reg.register(make_entity("c"))
    all_e = reg.all_entities()
    assert len(all_e) == 3
    ids = {e.entity_id for e in all_e}
    assert ids == {"a", "b", "c"}


def test_registry_all_entities_empty():
    reg = EntityRegistry()
    assert reg.all_entities() == []


def test_registry_register_returns_entity():
    reg = EntityRegistry()
    e = make_entity("x")
    returned = reg.register(e)
    assert returned is e


# ── RelatedRecord ──────────────────────────────────────────────────────────


def test_related_record_construction():
    rr = RelatedRecord(entity_id="abc-123", relation="tautomer")
    assert rr.entity_id == "abc-123"
    assert rr.relation == "tautomer"


def test_related_record_forbids_extra_fields():
    with pytest.raises(ValueError):
        RelatedRecord(entity_id="x", relation="y", extra="bad")
