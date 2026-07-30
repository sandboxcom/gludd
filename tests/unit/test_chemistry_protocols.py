"""Unit tests for ``general_ludd.chemistry.protocols`` and ``inventory`` (Phase B).

Covers CHEM-006 (protocol drafting) and CHEM-009 (inventory) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``:

* §8.1 — protocol draft required fields.
* CHEM-AT-009 — changing one byte of an approved protocol invalidates its
  approval token.
* CHEM-AT-010 — inventory tests reject expired/restricted/wrong-purity lots and
  never silently substitute.

Modules are loaded by file path (mirroring ``test_chemistry_reactions.py``) so
the suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import copy
import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")
_PROTO_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "protocols.py")
_INV_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "inventory.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module(_CORE_PATH, "chemistry_core_proto_test")
protocols = _load_module(_PROTO_PATH, "chemistry_protocols_under_test")
inventory = _load_module(_INV_PATH, "chemistry_inventory_under_test")


def _base_protocol() -> dict:
    return {
        "objective": "Synthesize aspirin from salicylic acid and acetic anhydride",
        "evidence_refs": ["source-001", "source-002"],
        "entities": [
            {"entity_id": "ent-salicylic", "lot": "LOT-2024-A"},
            {"entity_id": "ent-acetic-anh", "lot": "LOT-2024-B"},
        ],
        "quantities": [
            {"entity_id": "ent-salicylic", "value": 13.8, "unit": "g", "uncertainty": 0.05},
            {"entity_id": "ent-acetic-anh", "value": 20.4, "unit": "g", "uncertainty": 0.10},
        ],
        "equipment": [
            {"id": "flask-250", "calibration": {"date": "2024-06-01", "status": "valid"}},
        ],
        "operations": [
            {"order": 1, "description": "Charge salicylic acid into flask"},
            {"order": 2, "description": "Add acetic anhydride slowly"},
            {"order": 3, "description": "Heat to 85 C for 20 minutes"},
        ],
        "parameter_ranges": [
            {"name": "temperature_C", "min": 80.0, "max": 90.0},
        ],
        "stop_conditions": [
            {"trigger": "temperature_exceeds_95C", "action": "quench_with_ice"},
        ],
        "quench_workup": [
            {"step": "Pour onto crushed ice"},
            {"step": "Filter precipitate"},
        ],
        "waste_streams": [
            {"stream": "aqueous_acidic", "treatment": "neutralize_before_disposal"},
        ],
        "emergency_actions": [
            {"event": "spill", "response": "absorb_with_vermiculite"},
        ],
        "expected_results": [
            {"name": "yield_percent", "value": 70.0, "unit": "percent"},
        ],
        "approver_roles": ["qualified_chemist", "safety_officer"],
    }


# ---------------------------------------------------------------------------
# CHEM-006 protocol draft — required fields
# ---------------------------------------------------------------------------


class TestProtocolDraftRequiredFields:
    def test_protocol_has_all_required_sections(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        for section in (
            "objective",
            "evidence_refs",
            "entities",
            "quantities",
            "equipment",
            "operations",
            "stop_conditions",
            "waste_streams",
            "emergency_actions",
        ):
            assert section in proto, f"missing required section: {section}"

    def test_protocol_has_objective(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["objective"]

    def test_protocol_has_quantities_with_units(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        for q in proto["quantities"]:
            assert q["unit"], "quantity missing unit"
            assert "uncertainty" in q

    def test_protocol_has_equipment_and_calibration(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["equipment"]
        assert proto["equipment"][0]["calibration"]["status"] == "valid"

    def test_protocol_has_ordered_operations(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        orders = [op["order"] for op in proto["operations"]]
        assert orders == sorted(orders)

    def test_protocol_has_stop_conditions(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["stop_conditions"]
        assert proto["stop_conditions"][0]["action"]

    def test_protocol_has_waste_streams(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["waste_streams"]

    def test_protocol_has_emergency_actions(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["emergency_actions"]


# ---------------------------------------------------------------------------
# CHEM-006 — approval required before execution; immutable version digest
# ---------------------------------------------------------------------------


class TestProtocolApproval:
    def test_protocol_has_immutable_version_digest(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        assert proto["version_digest"]
        assert isinstance(proto["version_digest"], str)
        assert len(proto["version_digest"]) >= 16

    def test_approval_required_before_execution(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        verdict = protocols.validate_protocol(proto, approval_token=None)
        assert verdict["status"] == "awaiting_approval"
        assert not verdict["approved_for_execution"]

    def test_valid_approval_token_grants_execution(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        token = protocols.issue_approval_token(proto, approver="chemist-1", role="qualified_chemist")
        verdict = protocols.validate_protocol(proto, approval_token=token)
        assert verdict["status"] == "succeeded"
        assert verdict["approved_for_execution"] is True

    def test_changes_after_approval_invalidate_approval(self):
        proto = protocols.create_protocol_draft(_base_protocol())
        token = protocols.issue_approval_token(proto, approver="chemist-1", role="qualified_chemist")
        # Mutate the protocol after approval.
        mutated = copy.deepcopy(proto)
        mutated["operations"][0]["description"] = "Charge salicylic acid into a 500 mL flask"
        mutated = protocols.recompute_digest(mutated)
        verdict = protocols.validate_protocol(mutated, approval_token=token)
        assert verdict["status"] == "refused"
        assert not verdict["approved_for_execution"]
        assert any("digest" in str(e.get("message", "")) for e in verdict["errors"])

    def test_one_byte_change_invalidates_approval_token(self):
        # CHEM-AT-009: changing one byte of an approved protocol invalidates
        # its approval token.
        proto = protocols.create_protocol_draft(_base_protocol())
        token = protocols.issue_approval_token(proto, approver="chemist-1", role="qualified_chemist")
        mutated = copy.deepcopy(proto)
        # One-byte change: trailing space on the objective.
        mutated["objective"] = proto["objective"] + " "
        mutated = protocols.recompute_digest(mutated)
        verdict = protocols.validate_protocol(mutated, approval_token=token)
        assert not verdict["approved_for_execution"], "one-byte change must invalidate approval token"
        assert verdict["status"] == "refused"

    def test_expired_lot_excluded_from_protocol(self):
        # A protocol that references an expired lot must not validate.
        base = _base_protocol()
        base["entities"][0]["lot"] = "LOT-EXPIRED-2020"
        proto = protocols.create_protocol_draft(
            base,
            inventory_lots=[
                inventory.InventoryRecord(
                    lot="LOT-EXPIRED-2020",
                    purity=0.99,
                    location="cabinet-A",
                    expiry="2020-01-01",
                    restrictions=[],
                    chain_of_custody=[],
                ).as_dict(),
            ],
        )
        verdict = protocols.validate_protocol(proto, approval_token=None)
        assert not verdict["approved_for_execution"]
        assert any("lot" in lim.lower() or "expired" in lim.lower() for lim in verdict["limitations"])


# ---------------------------------------------------------------------------
# CHEM-009 inventory — lot suitability, never silently substitute
# ---------------------------------------------------------------------------


class TestInventoryRecord:
    def test_inventory_record_carries_required_fields(self):
        rec = inventory.InventoryRecord(
            lot="LOT-A",
            purity=0.98,
            location="shelf-B",
            expiry="2026-12-31",
            restrictions=[],
            chain_of_custody=[{"actor": "alice", "action": "received"}],
        )
        d = rec.as_dict()
        for field in ("lot", "purity", "location", "expiry", "restrictions", "chain_of_custody"):
            assert field in d


class TestCheckLotSuitability:
    def test_valid_lot_suitable(self):
        rec = inventory.InventoryRecord(
            lot="LOT-GOOD",
            purity=0.99,
            location="shelf-A",
            expiry="2027-01-01",
            restrictions=[],
            chain_of_custody=[],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2026-07-30")
        assert verdict["suitable"] is True

    def test_expired_lot_rejected(self):
        rec = inventory.InventoryRecord(
            lot="LOT-EXP",
            purity=0.99,
            location="shelf-A",
            expiry="2020-01-01",
            restrictions=[],
            chain_of_custody=[],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2026-07-30")
        assert verdict["suitable"] is False
        assert any("expired" in r.get("code", "") or "expiry" in r.get("code", "") for r in verdict["reasons"])

    def test_restricted_lot_rejected(self):
        rec = inventory.InventoryRecord(
            lot="LOT-RESTR",
            purity=0.99,
            location="vault",
            expiry="2027-01-01",
            restrictions=["controlled_precursor"],
            chain_of_custody=[],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2026-07-30")
        assert verdict["suitable"] is False
        assert any("restricted" in r.get("code", "") for r in verdict["reasons"])

    def test_wrong_purity_lot_rejected(self):
        rec = inventory.InventoryRecord(
            lot="LOT-LOW",
            purity=0.80,
            location="shelf-A",
            expiry="2027-01-01",
            restrictions=[],
            chain_of_custody=[],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2026-07-30")
        assert verdict["suitable"] is False
        assert any("purity" in r.get("code", "") for r in verdict["reasons"])

    def test_never_silently_substitutes_lot(self):
        # CHEM-AT-010: an unsuitable lot must NOT be silently swapped for a
        # different one. check_lot_suitability must return only a verdict; it
        # must not invent or substitute a different lot identifier.
        rec = inventory.InventoryRecord(
            lot="LOT-BAD",
            purity=0.50,
            location="shelf-A",
            expiry="2019-01-01",
            restrictions=["toxic"],
            chain_of_custody=[],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2026-07-30")
        assert verdict["suitable"] is False
        # The returned lot must be exactly the one queried — no substitution.
        assert verdict["lot"] == "LOT-BAD"
        assert "substituted_lot" not in verdict
        assert "replacement" not in verdict
