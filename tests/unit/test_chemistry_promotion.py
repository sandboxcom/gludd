"""Unit tests for CHEM-019 (provenance) and CHEM-022 (zero-downtime delivery).

Covers ``general_ludd.chemistry.promotion`` and ``general_ludd.chemistry.provenance``
per ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §11 (Zero-Downtime Delivery) and
CHEM-019 (provenance and validation). Acceptance criteria CHEM-AT-020, CHEM-AT-021,
CHEM-AT-024, CHEM-AT-025 drive the assertions:

* A failing research update does not change any active alias; a passing canary
  promotes without dropped requests (CHEM-AT-020).
* Forced canary regression initiates rollback within 60 seconds and preserves
  single-snapshot results (CHEM-AT-021).
* Provenance completeness: every reported value maps to source, method,
  conditions, code, and raw artifact (CHEM-AT-004, CHEM-019).
* Safety-policy updates may tighten immediately but cannot loosen without
  approval and canary evidence (spec §11).

Modules load via importlib file paths so the suite is robust inside worktrees.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMOTION_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "promotion.py")
_PROVENANCE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "provenance.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


promotion = _load(_PROMOTION_PATH, "chem_promotion_under_test")
provenance = _load(_PROVENANCE_PATH, "chem_provenance_under_test")


def _snapshot_payload(label: str = "v1"):
    return {
        "entities": {label + "_water": {"formula": "H2O"}},
        "properties": {label + "_bp_water": {"value": 373.15, "unit": "K"}},
        "reactions": {label + "_combustion": {"reactants": ["CH4", "O2"]}},
        "hazards": {label + "_ethanol": {"tier": "moderate"}},
    }


# ---------------------------------------------------------------------------
# CHEM-022 — ChemistrySnapshot immutability and versioning
# ---------------------------------------------------------------------------


class TestSnapshotImmutableAndVersioned:
    def test_snapshot_rejects_attribute_mutation(self):
        snap = promotion.ChemistrySnapshot(_snapshot_payload(), version=1)
        try:
            snap.entities["x"] = {}
        except Exception:
            return
        snap2 = promotion.ChemistrySnapshot(_snapshot_payload(), version=2)
        snap2.entities["x"] = {}
        assert "x" not in snap.entities, "mutation of one snapshot must not bleed into another"

    def test_snapshot_carries_distinct_version_and_id(self):
        a = promotion.ChemistrySnapshot(_snapshot_payload(), version=1)
        b = promotion.ChemistrySnapshot(_snapshot_payload(), version=2)
        assert a.version != b.version
        assert a.snapshot_id != b.snapshot_id
        assert a.created_at <= b.created_at

    def test_snapshot_payload_is_deep_copied(self):
        payload = _snapshot_payload()
        snap = promotion.ChemistrySnapshot(payload, version=1)
        payload["entities"]["injected"] = {"formula": "X"}
        assert "injected" not in snap.entities, "snapshot must deep-copy its payload"


# ---------------------------------------------------------------------------
# CHEM-022 — PromotionPipeline: shadow, canary, atomic swap, rollback
# ---------------------------------------------------------------------------


class TestShadowAndCanary:
    def test_shadow_read_does_not_affect_production(self):
        pipe = promotion.PromotionPipeline()
        prod = promotion.ChemistrySnapshot(_snapshot_payload("prod"), version=1)
        pipe.register_alias("chemistry", prod)
        shadow = promotion.ChemistrySnapshot(_snapshot_payload("shadow"), version=2)

        pipe.start_shadow("chemistry", shadow)
        prod_view = pipe.read("chemistry", request_id="r1")
        shadow_view = pipe.read_shadow("chemistry", request_id="r2")

        assert "prod_water" in prod_view.entities
        assert "shadow_water" in shadow_view.entities
        assert "shadow_water" not in prod_view.entities

    def test_canary_hash_is_stable_for_same_request(self):
        h1 = promotion.canary_hash({"request_id": "abc-123", "task": "identity"})
        h2 = promotion.canary_hash({"request_id": "abc-123", "task": "identity"})
        h3 = promotion.canary_hash({"request_id": "abc-999", "task": "identity"})
        assert h1 == h2
        assert h1 != h3

    def test_canary_routes_deterministically_by_hash(self):
        pipe = promotion.PromotionPipeline()
        prod = promotion.ChemistrySnapshot(_snapshot_payload("prod"), version=1)
        cand = promotion.ChemistrySnapshot(_snapshot_payload("cand"), version=2)
        pipe.register_alias("chemistry", prod)
        pipe.start_canary("chemistry", cand, fraction=0.5)

        seen_prod = 0
        seen_cand = 0
        for i in range(200):
            request_id = f"r-{i}"
            routed = pipe.route_canary("chemistry", {"request_id": request_id})
            assert routed in {"prod", "canary"}
            if routed == "prod":
                seen_prod += 1
            else:
                seen_cand += 1
        assert seen_prod > 0 and seen_cand > 0, "canary must split traffic"
        assert seen_prod + seen_cand == 200


# ---------------------------------------------------------------------------
# CHEM-022 — atomic alias swap with in-flight preservation
# ---------------------------------------------------------------------------


class TestAtomicAliasSwap:
    def test_atomic_swap_moves_alias_to_new_version(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        pipe.register_alias("chemistry", v1)

        result = pipe.atomic_swap("chemistry", v2)
        assert result["previous_version"] == 1
        assert result["new_version"] == 2
        assert result["dropped_requests"] == 0

        current = pipe.read("chemistry", request_id="post-swap")
        assert "v2_water" in current.entities

    def test_swap_preserves_in_flight_request_on_admitted_version(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        pipe.register_alias("chemistry", v1)

        admitted = pipe.admit("chemistry", request_id="in-flight-1")
        assert admitted == 1
        pipe.atomic_swap("chemistry", v2)

        finishing = pipe.finish("chemistry", request_id="in-flight-1")
        assert finishing["admitted_version"] == 1, "in-flight request must finish on admitted version"
        assert "v1_water" in finishing["snapshot"].entities

    def test_no_dropped_requests_during_full_promotion_cycle(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        pipe.register_alias("chemistry", v1)

        admitted_ids = [pipe.admit("chemistry", request_id=f"r-{i}") for i in range(5)]
        swap_result = pipe.atomic_swap("chemistry", v2)
        finish_results = [pipe.finish("chemistry", request_id=f"r-{i}") for i in range(5)]

        assert swap_result["dropped_requests"] == 0
        assert all(r["admitted_version"] == 1 for r in finish_results)
        assert all(admitted_ids[i] == 1 for i in range(5))


# ---------------------------------------------------------------------------
# CHEM-022 — rollback within 60s, recoverability
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_restores_prior_version(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        pipe.register_alias("chemistry", v1)
        pipe.atomic_swap("chemistry", v2)

        rollback_result = pipe.rollback("chemistry")
        assert rollback_result["rolled_back_to"] == 1
        assert rollback_result["within_seconds"] <= 60
        current = pipe.read("chemistry", request_id="post-rollback")
        assert "v1_water" in current.entities

    def test_rollback_records_breach_timestamp_within_window(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        pipe.register_alias("chemistry", v1)
        pipe.atomic_swap("chemistry", v2)

        breach_at = time.monotonic()
        rollback_result = pipe.rollback("chemistry", breach_at=breach_at)
        elapsed = rollback_result["completed_at"] - breach_at
        assert elapsed <= 60.0

    def test_prior_two_versions_remain_recoverable(self):
        pipe = promotion.PromotionPipeline()
        v1 = promotion.ChemistrySnapshot(_snapshot_payload("v1"), version=1)
        v2 = promotion.ChemistrySnapshot(_snapshot_payload("v2"), version=2)
        v3 = promotion.ChemistrySnapshot(_snapshot_payload("v3"), version=3)
        pipe.register_alias("chemistry", v1)
        pipe.atomic_swap("chemistry", v2)
        pipe.atomic_swap("chemistry", v3)

        history = pipe.recoverable_versions("chemistry")
        assert 1 in history and 2 in history, "prior two known-good versions must be recoverable"


# ---------------------------------------------------------------------------
# CHEM-022 §11 — safety policy: tightening immediate, loosening gated
# ---------------------------------------------------------------------------


class TestSafetyPolicyDirection:
    def test_tightening_applies_immediately(self):
        pipe = promotion.PromotionPipeline()
        old = {"ethanol": {"tier": "moderate"}}
        new = {"ethanol": {"tier": "high"}}
        result = pipe.apply_safety_policy("chemistry", old, new, approval=None)
        assert result["applied"] is True
        assert result["direction"] == "tighten"
        assert result["requires_approval"] is False

    def test_loosening_requires_approval_and_canary_evidence(self):
        pipe = promotion.PromotionPipeline()
        old = {"ethanol": {"tier": "high"}}
        new = {"ethanol": {"tier": "moderate"}}
        blocked = pipe.apply_safety_policy("chemistry", old, new, approval=None)
        assert blocked["applied"] is False
        assert blocked["direction"] == "loosen"
        assert blocked["requires_approval"] is True
        assert "approval_required" in blocked["reason"]

        approved = pipe.apply_safety_policy(
            "chemistry",
            old,
            new,
            approval={"approver": "dr-smith", "canary_evidence": "shadow-passed"},
        )
        assert approved["applied"] is True


# ---------------------------------------------------------------------------
# CHEM-019 — ProvenanceChain build + verify
# ---------------------------------------------------------------------------


class TestProvenanceChain:
    def test_build_chain_extracts_links_from_value(self):
        result = {
            "value": 373.15,
            "unit": "K",
            "provenance": {
                "source": {"locator": "doi:10.x/boiling", "citation": "Smith 2021"},
                "method": "experimental_boiling_point",
                "conditions": {"pressure": "1 atm"},
                "code": {"repo": "gludd", "commit": "abc1234"},
                "raw_artifact": {"uri": "artifact://run-42/raw.json", "digest": "sha256:dead"},
            },
        }
        chain = provenance.build_chain(result)
        assert chain["source"]["locator"] == "doi:10.x/boiling"
        assert chain["method"] == "experimental_boiling_point"
        assert chain["conditions"]["pressure"] == "1 atm"
        assert chain["code"]["commit"] == "abc1234"
        assert chain["raw_artifact"]["uri"].startswith("artifact://")

    def test_verify_chain_accepts_complete_chain(self):
        chain = {
            "source": {"locator": "x"},
            "method": "m",
            "conditions": {"t": 298},
            "code": {"commit": "abc"},
            "raw_artifact": {"uri": "artifact://y", "digest": "sha256:z"},
        }
        report = provenance.verify_chain(chain)
        assert report["complete"] is True
        assert report["missing"] == []

    def test_verify_chain_flags_missing_links(self):
        incomplete = {
            "source": {"locator": "x"},
            "method": "m",
            # conditions missing
            "code": {"commit": "abc"},
            # raw_artifact missing
        }
        report = provenance.verify_chain(incomplete)
        assert report["complete"] is False
        assert "conditions" in report["missing"]
        assert "raw_artifact" in report["missing"]

    def test_verify_chain_flags_orphan_artifact(self):
        chain = {
            "source": {"locator": "x"},
            "method": "m",
            "conditions": {"t": 298},
            "code": {"commit": "abc"},
            "raw_artifact": {"uri": "artifact://missing", "digest": "sha256:gone", "orphan": True},
        }
        report = provenance.verify_chain(chain)
        assert report["complete"] is False
        assert any("orphan" in m for m in report["missing"])

    def test_build_chain_from_nested_result_collects_all_values(self):
        result = {
            "boiling_point": {
                "value": 373.15,
                "provenance": {
                    "source": {"locator": "s1"},
                    "method": "m1",
                    "conditions": {},
                    "code": {"commit": "c1"},
                    "raw_artifact": {"uri": "a1", "digest": "d1"},
                },
            },
            "melting_point": {
                "value": 273.15,
                "provenance": {
                    "source": {"locator": "s2"},
                    "method": "m2",
                    "conditions": {},
                    "code": {"commit": "c2"},
                    "raw_artifact": {"uri": "a2", "digest": "d2"},
                },
            },
        }
        chains = provenance.build_chain(result)
        assert isinstance(chains, list)
        assert len(chains) == 2
        locators = {c["source"]["locator"] for c in chains}
        assert locators == {"s1", "s2"}
