"""Tests for governing_bodies knowledge module."""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_MODULE_PATH = os.path.join(
    _PROJECT_ROOT,
    "collections",
    "ansible_collections",
    "general_ludd",
    "governance",
    "plugins",
    "module_utils",
    "governing_bodies.py",
)


def _load_governing_bodies():
    spec = importlib.util.spec_from_file_location(
        "governance_governing_bodies", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None, "governing_bodies.py spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gb = _load_governing_bodies()


class TestBodyTypes:
    def test_seven_types_present(self):
        assert len(gb.BODY_TYPES) == 7

    def test_required_types_present(self):
        for t in (
            "international",
            "supranational",
            "national",
            "state_provincial",
            "municipal",
            "tribal",
            "special_district",
        ):
            assert t in gb.BODY_TYPES, f"missing body type: {t}"

    def test_types_unique(self):
        assert len(set(gb.BODY_TYPES)) == len(gb.BODY_TYPES)


class TestLookupBody:
    def test_lookup_by_id(self):
        body = gb.lookup_body("un")
        assert body is not None
        assert body["name"] == "United Nations"

    def test_lookup_by_name(self):
        body = gb.lookup_body("European Union")
        assert body is not None
        assert body["id"] == "eu"

    def test_lookup_by_alias(self):
        body = gb.lookup_body("UNHRC")
        assert body is not None
        assert body["id"] == "un_hrc"

    def test_lookup_case_insensitive(self):
        assert gb.lookup_body("UN")["id"] == "un"
        assert gb.lookup_body("nato")["id"] == "nato"
        assert gb.lookup_body(" Eu ")["id"] == "eu"

    def test_lookup_unknown_returns_none(self):
        assert gb.lookup_body("does_not_exist") is None

    def test_lookup_empty_returns_none(self):
        assert gb.lookup_body("") is None
        assert gb.lookup_body("   ") is None


class TestRequiredInternationalBodies:
    REQUIRED = (
        "un", "eu", "au", "asean", "nato", "wto", "who",
        "world_bank", "imf", "icc", "icj",
    )

    def test_all_required_bodies_present(self):
        ids = {b["id"] for b in gb.INTERNATIONAL_BODIES}
        missing = [r for r in self.REQUIRED if r not in ids]
        assert not missing, f"missing required international bodies: {missing}"

    def test_un_has_six_principal_organs(self):
        un = gb.lookup_body("un")
        assert len(un["structure"]) >= 6
        for organ in ("General Assembly", "Security Council", "International Court of Justice"):
            assert organ in un["structure"]

    def test_un_children_include_specialized_agencies(self):
        un = gb.lookup_body("un")
        for child_id in ("who", "world_bank", "imf", "unicef", "unhcr"):
            assert child_id in un["children"], f"missing UN child: {child_id}"


class TestBodyShape:
    def test_each_body_has_required_fields(self):
        required = (
            "id", "name", "aliases", "type", "members",
            "structure", "children", "decision_process",
            "decision_mechanism", "jurisdiction_scope", "legal_basis",
        )
        for body in gb.INTERNATIONAL_BODIES:
            for key in required:
                assert key in body, f"{body['id']} missing field {key}"

    def test_each_body_type_is_valid(self):
        for body in gb.INTERNATIONAL_BODIES:
            assert body["type"] in gb.BODY_TYPES, (
                f"{body['id']} has invalid type {body['type']}"
            )

    def test_ids_are_unique(self):
        ids = [b["id"] for b in gb.INTERNATIONAL_BODIES]
        assert len(set(ids)) == len(ids)

    def test_aliases_are_tuples(self):
        for body in gb.INTERNATIONAL_BODIES:
            assert isinstance(body["aliases"], tuple)


class TestGetChildren:
    def test_un_has_children(self):
        children = gb.get_children("un")
        ids = [c["id"] for c in children]
        assert "who" in ids
        assert "un_ga" in ids

    def test_unknown_body_returns_empty(self):
        assert gb.get_children("nope") == []

    def test_leaf_body_has_no_children(self):
        # WTO is a leaf
        assert gb.get_children("wto") == []


class TestGetDescendants:
    def test_descendants_transitive(self):
        descs = gb.get_descendants("un")
        ids = {d["id"] for d in descs}
        assert "who" in ids
        assert "imf" in ids
        # Transitive: un -> un_ga -> un_hrc
        assert "un_hrc" in ids

    def test_descendants_no_duplicates(self):
        descs = gb.get_descendants("un")
        ids = [d["id"] for d in descs]
        assert len(ids) == len(set(ids))

    def test_descendants_unknown_body_empty(self):
        assert gb.get_descendants("nope") == []


class TestGetJurisdiction:
    def test_un_global_scope(self):
        j = gb.get_jurisdiction("un")
        assert j["scope"] == "global"

    def test_eu_regional_scope(self):
        assert gb.get_jurisdiction("eu")["scope"] == "regional"

    def test_includes_legal_basis(self):
        j = gb.get_jurisdiction("icc")
        assert "Rome Statute" in (j["legal_basis"] or "")

    def test_includes_headquarters(self):
        j = gb.get_jurisdiction("nato")
        assert "Brussels" in (j["headquarters"] or "")

    def test_unknown_body_returns_error(self):
        j = gb.get_jurisdiction("nope")
        assert "error" in j


class TestGetDecisionProcess:
    def test_un_security_council_veto_mechanism(self):
        d = gb.get_decision_process("un_sc")
        assert d["mechanism"] == "qualified_majority_with_veto"
        assert "veto" in d["process"].lower()

    def test_nato_consensus(self):
        assert gb.get_decision_process("nato")["mechanism"] == "consensus"

    def test_asean_consensus(self):
        assert gb.get_decision_process("asean")["mechanism"] == "consensus"

    def test_icj_judicial(self):
        assert gb.get_decision_process("icj")["mechanism"] == "judicial"

    def test_imf_weighted_voting(self):
        assert gb.get_decision_process("imf")["mechanism"] == "weighted_voting"

    def test_unknown_body_returns_error(self):
        assert "error" in gb.get_decision_process("nope")


class TestNationalStructures:
    def test_three_branches(self):
        assert gb.national_branches() == (
            "executive", "legislative", "judicial",
        )

    def test_legislative_structure_types(self):
        structures = gb.NATIONAL_STRUCTURES["legislative"]["structure_types"]
        assert "unicameral" in structures
        assert "bicameral" in structures

    def test_ministries_present(self):
        mins = gb.national_ministries()
        assert "finance_treasury" in mins
        assert "defense" in mins
        assert "foreign_affairs" in mins

    def test_examples_cover_government_forms(self):
        ex = gb.NATIONAL_STRUCTURES["examples"]
        for form in (
            "presidential_federal",
            "parliamentary_unitary",
            "parliamentary_federal",
            "semi_presidential",
        ):
            assert form in ex

    def test_agency_categories(self):
        agencies = gb.NATIONAL_STRUCTURES["agencies"]
        assert "central_bank" in agencies
        assert "regulatory_bodies" in agencies


class TestBodyRelationships:
    def test_un_parent_of_who(self):
        assert gb.relationship("un", "who") == "parent_child"

    def test_eu_parent_of_parliament(self):
        assert gb.relationship("eu", "eu_parliament") == "parent_child"

    def test_overlapping_jurisdiction_imf_world_bank(self):
        assert gb.relationship("imf", "world_bank") == "overlapping_jurisdiction"

    def test_regulatory_unsc_icc(self):
        assert gb.relationship("un_sc", "icc") == "regulatory"

    def test_no_relationship_returns_none(self):
        assert gb.relationship("who", "nato") is None

    def test_relationship_detail_includes_note(self):
        d = gb.relationship_detail("icc", "icj")
        assert d is not None
        assert d["kind"] == "overlapping_jurisdiction"
        assert "note" in d

    def test_all_relationships_for_eu(self):
        rels = gb.all_relationships_for("eu")
        partners = {r["parent"] for r in rels} | {r["child"] for r in rels}
        assert "eu" in partners
        assert len(rels) >= 4

    def test_relationship_parents_exist_in_dataset(self):
        all_ids = {b["id"] for b in gb.INTERNATIONAL_BODIES}
        for rel in gb.BODY_RELATIONSHIPS:
            assert rel["parent"] in all_ids, f"unknown parent: {rel['parent']}"
            assert rel["child"] in all_ids, f"unknown child: {rel['child']}"

    def test_relationship_kinds_valid(self):
        valid_kinds = {
            "parent_child",
            "overlapping_jurisdiction",
            "advisory",
            "regulatory",
        }
        for rel in gb.BODY_RELATIONSHIPS:
            assert rel["kind"] in valid_kinds, (
                f"unknown relationship kind: {rel['kind']}"
            )


class TestBodiesByType:
    def test_international_bodies_returned(self):
        intl = gb.bodies_by_type("international")
        assert len(intl) >= 5
        ids = {b["id"] for b in intl}
        assert "un" in ids
        assert "nato" in ids

    def test_supranational_bodies_returned(self):
        supra = gb.bodies_by_type("supranational")
        ids = {b["id"] for b in supra}
        assert "eu" in ids

    def test_invalid_type_returns_empty(self):
        assert gb.bodies_by_type("intergalactic") == []


class TestImportSanity:
    def test_module_exports_required_symbols(self):
        for symbol in (
            "BODY_TYPES",
            "INTERNATIONAL_BODIES",
            "NATIONAL_STRUCTURES",
            "BODY_RELATIONSHIPS",
            "lookup_body",
            "get_children",
            "get_jurisdiction",
            "get_decision_process",
        ):
            assert hasattr(gb, symbol), f"module missing export: {symbol}"
