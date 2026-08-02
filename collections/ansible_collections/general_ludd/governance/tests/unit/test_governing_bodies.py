"""Tests for governing_bodies module — international bodies, hierarchy, relationships."""

from __future__ import annotations

from plugins.module_utils.governing_bodies import (
    BODY_TYPES,
    INTERNATIONAL_BODIES,
    NATIONAL_STRUCTURES,
    all_relationships_for,
    bodies_by_type,
    get_children,
    get_decision_process,
    get_descendants,
    get_jurisdiction,
    lookup_body,
    national_branches,
    national_ministries,
    relationship,
    relationship_detail,
)


class TestBodyTypes:
    def test_all_seven_types(self):
        expected = (
            "international",
            "supranational",
            "national",
            "state_provincial",
            "municipal",
            "tribal",
            "special_district",
        )
        assert expected == BODY_TYPES


class TestInternationalBodies:
    def test_has_core_bodies(self):
        ids = {b["id"] for b in INTERNATIONAL_BODIES}
        expected = {"un", "eu", "nato", "au", "asean", "wto", "who", "icc", "icj", "world_bank", "imf"}
        assert expected <= ids

    def test_all_bodies_have_required_keys(self):
        required = {
            "id",
            "name",
            "type",
            "members",
            "structure",
            "children",
            "decision_process",
            "decision_mechanism",
            "jurisdiction_scope",
            "legal_basis",
        }
        for body in INTERNATIONAL_BODIES:
            assert set(body.keys()) >= required, f"{body.get('id')} missing fields"
            assert body["type"] in BODY_TYPES, f"{body['id']} unknown type {body['type']}"

    def test_all_ids_unique(self):
        ids = [b["id"] for b in INTERNATIONAL_BODIES]
        assert len(ids) == len(set(ids))


class TestNationalStructures:
    def test_branches_tuple(self):
        assert national_branches() == ("executive", "legislative", "judicial")

    def test_ministries_tuple(self):
        ministries = national_ministries()
        assert "finance_treasury" in ministries
        assert "defense" in ministries
        assert "foreign_affairs" in ministries
        assert len(ministries) == 15

    def test_structure_templates(self):
        assert "branches" in NATIONAL_STRUCTURES
        assert "executive" in NATIONAL_STRUCTURES
        assert "legislative" in NATIONAL_STRUCTURES
        assert "judicial" in NATIONAL_STRUCTURES
        assert "examples" in NATIONAL_STRUCTURES


class TestLookupBody:
    def test_by_id(self):
        un = lookup_body("un")
        assert un is not None
        assert un["name"] == "United Nations"

    def test_by_name(self):
        eu = lookup_body("European Union")
        assert eu is not None
        assert eu["id"] == "eu"

    def test_by_alias(self):
        who = lookup_body("WHO")
        assert who is not None
        assert who["id"] == "who"

    def test_case_insensitive(self):
        assert lookup_body("WHO") is not None
        assert lookup_body("who") is not None

    def test_unknown_is_none(self):
        assert lookup_body("nonexistent") is None
        assert lookup_body("") is None
        assert lookup_body("   ") is None


class TestGetChildren:
    def test_un_has_many_children(self):
        children = get_children("un")
        ids = {c["id"] for c in children}
        assert "un_ga" in ids
        assert "who" in ids
        assert "world_bank" in ids
        assert len(children) >= 10

    def test_leaf_bodies_have_no_children(self):
        assert get_children("who") == []
        assert get_children("imf") == []

    def test_unknown_body_empty(self):
        assert get_children("zzz") == []


class TestGetDescendants:
    def test_un_descendants(self):
        desc = get_descendants("un")
        ids = {d["id"] for d in desc}
        assert "who" in ids
        assert "world_bank" in ids
        assert "un_ga" in ids
        assert "un_hrc" in ids

    def test_eu_descendants(self):
        desc = get_descendants("eu")
        ids = {d["id"] for d in desc}
        assert "eu_parliament" in ids
        assert "eu_commission" in ids

    def test_unknown_body_empty(self):
        assert get_descendants("zzz") == []

    def test_leaf_body_empty_descendants(self):
        assert get_descendants("who") == []


class TestGetJurisdiction:
    def test_un_jurisdiction(self):
        j = get_jurisdiction("un")
        assert j["scope"] == "global"
        assert "UN Charter" in j["legal_basis"]
        assert j["headquarters"] == "New York, NY, USA"

    def test_eu_jurisdiction(self):
        j = get_jurisdiction("eu")
        assert j["scope"] == "regional"

    def test_unknown_body(self):
        assert "error" in get_jurisdiction("zzz")


class TestGetDecisionProcess:
    def test_known_bodies(self):
        dp = get_decision_process("un_sc")
        assert dp["mechanism"] == "qualified_majority_with_veto"

        dp2 = get_decision_process("asean")
        assert dp2["mechanism"] == "consensus"

        dp3 = get_decision_process("icj")
        assert dp3["mechanism"] == "judicial"

    def test_unknown_body(self):
        assert "error" in get_decision_process("zzz")


class TestBodiesByType:
    def test_international_bodies(self):
        intl = bodies_by_type("international")
        assert len(intl) > 0
        assert all(b["type"] == "international" for b in intl)

    def test_supranational_bodies(self):
        supra = bodies_by_type("supranational")
        ids = {b["id"] for b in supra}
        assert "eu" in ids

    def test_unknown_type_returns_empty(self):
        assert bodies_by_type("nonexistent") == []


class TestRelationship:
    def test_direct_parent_child(self):
        assert relationship("un", "who") == "parent_child"
        assert relationship("eu", "eu_parliament") == "parent_child"

    def test_no_direct_relationship(self):
        assert relationship("un", "eu") is None
        assert relationship("who", "nato") is None

    def test_overlapping_jurisdiction(self):
        assert relationship("imf", "world_bank") == "overlapping_jurisdiction"


class TestRelationshipDetail:
    def test_has_kind_and_note(self):
        detail = relationship_detail("un", "who")
        assert detail is not None
        assert "kind" in detail
        assert "note" in detail
        assert detail["kind"] == "parent_child"

    def test_nonexistent_is_none(self):
        assert relationship_detail("un", "eu") is None


class TestAllRelationshipsFor:
    def test_un_has_many(self):
        rels = all_relationships_for("un")
        assert len(rels) >= 5
        kinds = {r["kind"] for r in rels}
        assert "parent_child" in kinds

    def test_no_relationships_for_unknown(self):
        assert all_relationships_for("zzz") == []
