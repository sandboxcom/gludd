"""Unit tests for ``general_ludd.chemistry.inventory`` (CHEM-009 / CHEM-AT-010).

Covers InventoryRecord construction/serialization and ``check_lot_suitability``
verdicts for expiry, restrictions, purity, and their combinations.  Verifies the
spec invariant: unsuitable lots are NEVER silently substituted — the verdict
carries no ``substituted_lot`` / ``replacement`` field.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_INVENTORY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "inventory.py")
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load(_CORE_PATH, "chemistry_core_for_inventory_test")
inventory = _load(_INVENTORY_PATH, "chemistry_inventory_under_test")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides):
    kwargs = {
        "lot": "LOT-001",
        "purity": 0.998,
        "location": "Cabinet-A2",
        "expiry": "2027-06-01",
    }
    kwargs.update(overrides)
    return inventory.InventoryRecord(**kwargs)


# ---------------------------------------------------------------------------
# InventoryRecord construction
# ---------------------------------------------------------------------------


class TestInventoryRecordConstruction:
    def test_required_fields(self):
        rec = _make_record()
        assert rec.lot == "LOT-001"
        assert rec.purity == 0.998
        assert rec.location == "Cabinet-A2"
        assert rec.expiry == "2027-06-01"

    def test_restrictions_empty_by_default(self):
        rec = _make_record()
        assert rec.restrictions == []

    def test_chain_of_custody_empty_by_default(self):
        rec = _make_record()
        assert rec.chain_of_custody == []

    def test_purity_coerced_to_float(self):
        rec = _make_record(purity="0.95")
        assert rec.purity == 0.95
        assert isinstance(rec.purity, float)

    def test_restrictions_supplied(self):
        rec = _make_record(restrictions=["controlled", "single-use"])
        assert rec.restrictions == ["controlled", "single-use"]

    def test_chain_of_custody_supplied(self):
        rec = _make_record(chain_of_custody=[{"actor": "alice", "action": "received"}])
        assert rec.chain_of_custody == [{"actor": "alice", "action": "received"}]

    def test_none_restrictions_becomes_empty_list(self):
        rec = _make_record(restrictions=None)
        assert rec.restrictions == []

    def test_none_chain_of_custody_becomes_empty_list(self):
        rec = _make_record(chain_of_custody=None)
        assert rec.chain_of_custody == []

    def test_slots_enforced_no_extra_attrs(self):
        rec = _make_record()
        with pytest.raises(AttributeError):
            rec.nonexistent = "value"


# ---------------------------------------------------------------------------
# InventoryRecord.as_dict
# ---------------------------------------------------------------------------


class TestInventoryRecordAsDict:
    def test_as_dict_shape(self):
        d = _make_record().as_dict()
        assert d["schema_version"] == core.SCHEMA_VERSION
        assert d["lot"] == "LOT-001"
        assert d["purity"] == 0.998
        assert d["location"] == "Cabinet-A2"
        assert d["expiry"] == "2027-06-01"
        assert d["restrictions"] == []
        assert d["chain_of_custody"] == []

    def test_as_dict_copies_lists(self):
        rec = _make_record(restrictions=["a"], chain_of_custody=[{"actor": "x"}])
        d = rec.as_dict()
        d["restrictions"].append("b")
        d["chain_of_custody"].append({"actor": "y"})
        assert rec.restrictions == ["a"]
        assert rec.chain_of_custody == [{"actor": "x"}]

    def test_as_dict_with_data(self):
        rec = _make_record(
            lot="LOT-B99",
            purity=0.883,
            restrictions=["toxic"],
            chain_of_custody=[{"actor": "bob", "action": "opened", "timestamp": "2026-01-01T00:00:00Z"}],
        )
        d = rec.as_dict()
        assert d["lot"] == "LOT-B99"
        assert d["purity"] == 0.883
        assert d["restrictions"] == ["toxic"]
        assert len(d["chain_of_custody"]) == 1
        assert d["chain_of_custody"][0]["actor"] == "bob"


# ---------------------------------------------------------------------------
# check_lot_suitability — suitable
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilitySuitable:
    def test_all_checks_pass(self):
        rec = _make_record(purity=0.995, expiry="2028-12-31")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2026-01-01")
        assert verdict["suitable"] is True
        assert verdict["reasons"] == []
        assert verdict["lot"] == "LOT-001"

    def test_purity_equals_required(self):
        rec = _make_record(purity=0.950)
        verdict = inventory.check_lot_suitability(rec, required_purity=0.950, as_of="2026-01-01")
        assert verdict["suitable"] is True

    def test_accepts_inventory_record_object(self):
        rec = _make_record(purity=0.99, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is True

    def test_accepts_dict(self):
        d = {"lot": "LOT-D", "purity": 0.992, "expiry": "2030-06-01", "restrictions": []}
        verdict = inventory.check_lot_suitability(d, required_purity=0.99, as_of="2025-01-01")
        assert verdict["suitable"] is True

    def test_empty_expiry_not_checked(self):
        rec = _make_record(purity=0.99, expiry="")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.98, as_of="2025-01-01")
        assert verdict["suitable"] is True


# ---------------------------------------------------------------------------
# check_lot_suitability — expired
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityExpired:
    def test_expiry_before_as_of(self):
        rec = _make_record(purity=0.99, expiry="2020-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-06-01")
        assert verdict["suitable"] is False
        assert len(verdict["reasons"]) == 1
        assert verdict["reasons"][0]["code"] == "lot_expired"

    def test_expired_reason_contains_lot_id(self):
        rec = _make_record(lot="OLD-LOT", expiry="2019-12-31")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert "OLD-LOT" in verdict["reasons"][0]["message"]

    def test_expiry_equal_to_as_of_is_not_expired(self):
        rec = _make_record(purity=0.99, expiry="2025-06-15")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-06-15")
        assert verdict["suitable"] is True


# ---------------------------------------------------------------------------
# check_lot_suitability — restricted
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityRestricted:
    def test_single_restriction(self):
        rec = _make_record(purity=0.99, expiry="2030-01-01", restrictions=["controlled"])
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert verdict["reasons"][0]["code"] == "lot_restricted"
        assert "controlled" in verdict["reasons"][0]["message"]

    def test_multiple_restrictions_joined_in_message(self):
        rec = _make_record(
            purity=0.99,
            expiry="2030-01-01",
            restrictions=["controlled", "single-use", "gowning-required"],
        )
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is False
        msg = verdict["reasons"][0]["message"]
        assert "controlled" in msg
        assert "single-use" in msg
        assert "gowning-required" in msg


# ---------------------------------------------------------------------------
# check_lot_suitability — purity
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityPurity:
    def test_purity_below_required(self):
        rec = _make_record(purity=0.85, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert verdict["reasons"][0]["code"] == "lot_purity_insufficient"

    def test_purity_reason_contains_values(self):
        rec = _make_record(purity=0.8500, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.9900, as_of="2025-01-01")
        msg = verdict["reasons"][0]["message"]
        assert "0.8500" in msg
        assert "0.9900" in msg

    def test_purity_just_below(self):
        rec = _make_record(purity=0.9499, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is False

    def test_zero_purity(self):
        rec = _make_record(purity=0.0, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.01, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert verdict["reasons"][0]["code"] == "lot_purity_insufficient"


# ---------------------------------------------------------------------------
# check_lot_suitability — combinations
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityCombinations:
    def test_expired_and_restricted(self):
        rec = _make_record(purity=0.995, expiry="2020-01-01", restrictions=["controlled"])
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is False
        codes = {r["code"] for r in verdict["reasons"]}
        assert codes == {"lot_expired", "lot_restricted"}

    def test_expired_and_low_purity(self):
        rec = _make_record(purity=0.80, expiry="2020-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        codes = {r["code"] for r in verdict["reasons"]}
        assert codes == {"lot_expired", "lot_purity_insufficient"}

    def test_restricted_and_low_purity(self):
        rec = _make_record(purity=0.80, expiry="2030-01-01", restrictions=["controlled"])
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        codes = {r["code"] for r in verdict["reasons"]}
        assert codes == {"lot_restricted", "lot_purity_insufficient"}

    def test_all_three_violations(self):
        rec = _make_record(purity=0.30, expiry="2018-01-01", restrictions=["toxic", "expired-by-policy"])
        verdict = inventory.check_lot_suitability(rec, required_purity=0.999, as_of="2025-01-01")
        codes = {r["code"] for r in verdict["reasons"]}
        assert codes == {"lot_expired", "lot_restricted", "lot_purity_insufficient"}
        assert len(verdict["reasons"]) == 3


# ---------------------------------------------------------------------------
# check_lot_suitability — spec invariants (CHEM-AT-010)
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityInvariants:
    def test_verdict_has_required_keys(self):
        rec = _make_record()
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        for key in ("schema_version", "lot", "suitable", "reasons"):
            assert key in verdict

    def test_no_silent_substitution_field(self):
        rec = _make_record(purity=0.80, expiry="2020-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        for forbidden in ("substituted_lot", "replacement", "alternative"):
            assert forbidden not in verdict

    def test_lot_echoed_even_when_unsuitable(self):
        rec = _make_record(lot="UNIQUE-LOT-ID", purity=0.50, expiry="2019-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        assert verdict["lot"] == "UNIQUE-LOT-ID"

    def test_dict_input_missing_purity_defaults_to_zero(self):
        d = {"lot": "SIMPLE", "expiry": "2030-01-01"}
        verdict = inventory.check_lot_suitability(d, required_purity=0.90, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert any(r["code"] == "lot_purity_insufficient" for r in verdict["reasons"])

    def test_suitable_verdict_has_no_reasons(self):
        rec = _make_record(purity=0.999, expiry="2030-12-31")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.99, as_of="2025-01-01")
        assert verdict["suitable"] is True
        assert verdict["reasons"] == []

    def test_schema_version_present_in_verdict(self):
        rec = _make_record()
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-01-01")
        assert verdict["schema_version"] == core.SCHEMA_VERSION

    def test_empty_lot_id_in_dict(self):
        d = {"purity": 0.95, "expiry": "2030-01-01", "restrictions": []}
        verdict = inventory.check_lot_suitability(d, required_purity=0.90, as_of="2025-01-01")
        assert verdict["lot"] == ""
        assert verdict["suitable"] is True


# ---------------------------------------------------------------------------
# check_lot_suitability — edge cases
# ---------------------------------------------------------------------------


class TestCheckLotSuitabilityEdgeCases:
    def test_string_date_comparison_lexicographic(self):
        rec = _make_record(purity=0.99, expiry="2025-06-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.95, as_of="2025-05-01")
        assert verdict["suitable"] is True

    def test_zero_required_purity(self):
        rec = _make_record(purity=0.0)
        verdict = inventory.check_lot_suitability(rec, required_purity=0.0, as_of="2025-01-01")
        assert verdict["suitable"] is True

    def test_negative_purity(self):
        rec = _make_record(purity=-0.5, expiry="2030-01-01")
        verdict = inventory.check_lot_suitability(rec, required_purity=0.0, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert verdict["reasons"][0]["code"] == "lot_purity_insufficient"

    def test_restrictions_not_a_list_in_dict(self):
        d = {"lot": "LOT-X", "purity": 0.99, "expiry": "2030-01-01", "restrictions": "controlled"}
        verdict = inventory.check_lot_suitability(d, required_purity=0.95, as_of="2025-01-01")
        assert verdict["suitable"] is False
        assert verdict["reasons"][0]["code"] == "lot_restricted"
