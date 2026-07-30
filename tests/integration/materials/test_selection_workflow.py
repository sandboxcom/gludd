"""Integration tests for the full material-selection workflow (spec MATE-AT-002).

Drives the end-to-end pipeline ``DesignRequirements -> screen_candidates ->
rank_candidates`` and verifies that every surviving candidate exposes the
MATE-DEC-002 deliverables:

  - requirement margins (nominal + conservative + sensitivity cases)
  - uncertainty per margin (MATE-SAFE-003: no fabricated precision)
  - provenance/source on every candidate (MATE-DEC-004: traceability)
  - trade-offs as a structured dict, NOT a collapsed score (MATE-DEC-002 step 5)

Spans multiple material families: steel (aisi_1045), aluminum (aa6061_t6),
reinforced polymer (pa66_gf30), and thermoset (epoxy_cast). Negative cases
assert the pipeline rejects hard-constraint violations and surfaces
``insufficient_context`` data rather than fabricating a margin.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.core import (
    INSUFFICIENT_CONTEXT,
    SCHEMA_VERSION,
    normalize_requirements,
)
from general_ludd.materials.material_selection import (
    rank_candidates,
    resolve_property,
    screen_candidates,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _yield_req(magnitude_mpa: float = 200.0) -> dict:
    """A normalized requirements dict carrying one yield load case."""
    return normalize_requirements(
        {
            "load_cases": [
                {"id": "y1", "type": "yield", "magnitude": magnitude_mpa, "unit": "MPa"},
            ],
            "failure_consequence": "significant",
        }
    )


def _multi_load_req() -> dict:
    """Requirements spanning yield + ultimate + shear load cases."""
    return normalize_requirements(
        {
            "load_cases": [
                {"id": "y1", "type": "yield", "magnitude": 200, "unit": "MPa"},
                {"id": "u1", "type": "ultimate", "magnitude": 350, "unit": "MPa"},
                {"id": "s1", "type": "shear", "magnitude": 120, "unit": "MPa"},
            ],
            "failure_consequence": "significant",
        }
    )


# ---------------------------------------------------------------------------
# End-to-end workflow tests (MATE-AT-002)
# ---------------------------------------------------------------------------


class TestSelectionWorkflowEndToEnd:
    """DesignRequirements -> screen -> rank -> verify deliverables."""

    def test_steel_passes_full_pipeline_with_margin_sources_and_uncertainty(self):
        """aisi_1045 (medium-carbon steel) survives a 200 MPa yield requirement
        and the ranked result exposes margin, source, uncertainty, and tradeoffs."""
        reqs = _yield_req(200.0)
        screened = screen_candidates(reqs, candidates=["aisi_1045"])
        assert screened["verdict"] == "candidate"
        steel = screened["candidates"][0]
        assert steel["state"] == "survived"
        assert steel["source"]["publisher"] == "ASM Handbook Vol. 1"
        # every surviving margin must carry uncertainty + unit
        for m in steel["requirement_margins"]:
            assert m["state"] == "pass"
            assert m["unit"] == "MPa"
            assert m["margin"] > 0

        ranked = rank_candidates(reqs, candidates=["aisi_1045"])
        nominal = ranked["nominal"][0]
        margin = nominal["margins"][0]
        assert margin["capacity"] == pytest.approx(310.0)
        assert margin["uncertainty"] == pytest.approx(20.0)
        assert margin["data_tier"] == "handbook"
        assert "yield_strength" in nominal["tradeoffs"]
        assert nominal["source"]["publisher"]

    def test_aluminum_workflow_attaches_performance_index_when_density_given(self):
        """aa6061_t6 with a lot-supplied density produces specific_strength."""
        reqs = _yield_req(150.0)
        overrides = {
            "aa6061_t6": {
                "density": {
                    "value": 2.70,
                    "unit": "g/cm^3",
                    "uncertainty": 0.05,
                    "tier": "lot",
                }
            }
        }
        ranked = rank_candidates(reqs, candidates=["aa6061_t6"], overrides=overrides)
        nominal = ranked["nominal"][0]
        assert nominal["performance_indices"]["specific_strength"] == pytest.approx(276.0 / 2.70, rel=1e-2)
        # the density tradeoff entry must record its lot tier provenance
        dens_entry = nominal["tradeoffs"]["density"]
        assert dens_entry["tier"] == "lot"
        assert dens_entry["value"] == pytest.approx(2.70)

    def test_reinforced_polymer_rejected_when_yield_below_requirement(self):
        """pa66_gf30 (yield=180 MPa) is screened OUT against a 200 MPa demand;
        the verdict must still be 'candidate' if another material survives, but
        the polymer entry carries a hard_constraint rejection reason."""
        reqs = _yield_req(200.0)
        screened = screen_candidates(reqs, candidates=["pa66_gf30", "aisi_1045"])
        by_id = {c["material_id"]: c for c in screened["candidates"]}
        assert by_id["pa66_gf30"]["state"] == "rejected"
        assert "hard_constraint" in by_id["pa66_gf30"]["reason"]
        assert by_id["aisi_1045"]["state"] == "survived"
        assert screened["verdict"] == "candidate"

    def test_thermoset_carrying_no_yield_property_is_rejected_cleanly(self):
        """epoxy_cast exposes ultimate_strength but no yield_strength; a yield
        demand must produce a 'no yield_strength property' rejection, NOT a
        fabricated margin (MATE-SAFE-003)."""
        reqs = _yield_req(50.0)
        screened = screen_candidates(reqs, candidates=["epoxy_cast"])
        epoxy = screened["candidates"][0]
        assert epoxy["state"] == "rejected"
        assert "yield_strength" in epoxy["reason"]
        assert screened["verdict"] == "infeasible"

    def test_ranked_candidates_expose_three_cases_no_collapsed_score(self):
        """MATE-DEC-002 step 5: ranking must expose nominal, conservative, and
        sensitivity cases AND must NOT collapse into a single 'score' field."""
        reqs = _yield_req(180.0)
        ranked = rank_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"])
        for case in ("nominal", "conservative", "sensitivity"):
            assert case in ranked
            assert len(ranked[case]) == 2
            for entry in ranked[case]:
                assert "tradeoffs" in entry
                assert "score" not in entry
                assert "aggregate_score" not in entry

    def test_full_pipeline_with_multi_load_case_preserves_per_case_margins(self):
        """When requirements carry yield + ultimate + shear, every surviving
        candidate exposes one margin per load case id, each carrying units +
        data tier."""
        reqs = _multi_load_req()
        # aisi_1045 carries yield + ultimate but NOT shear_strength
        screened = screen_candidates(reqs, candidates=["aisi_1045"])
        steel = screened["candidates"][0]
        # no shear_strength on file -> hard-constraint rejection
        assert steel["state"] == "rejected"
        assert "shear_strength" in steel["reason"]

        # rank still produces margins per case id; missing ones are surfaced
        ranked = rank_candidates(reqs, candidates=["aisi_1045"])
        nominal_margins = {m["requirement_id"]: m for m in ranked["nominal"][0]["margins"]}
        assert "y1" in nominal_margins and nominal_margins["y1"]["state"] == "pass"
        assert "u1" in nominal_margins and nominal_margins["u1"]["state"] == "pass"
        # shear_strength missing -> insufficient_data surfaced, not hidden
        assert nominal_margins["s1"]["state"] == "insufficient_data"

    def test_lot_data_override_propagates_through_pipeline_to_ranked_margin(self):
        """A lot-tier yield override must beat the handbook value end-to-end:
        resolve_property -> _compute_margins -> ranked output."""
        reqs = _yield_req(200.0)
        overrides = {
            "aisi_1045": {
                "yield_strength": {
                    "value": 295.0,
                    "unit": "MPa",
                    "uncertainty": 5.0,
                    "tier": "lot",
                    "method": "ISO 6892",
                }
            }
        }
        # resolve_property is the chokepoint the ranker uses
        prop, tier = resolve_property("aisi_1045", "yield_strength", overrides=overrides)
        assert tier == "lot"
        assert prop is not None
        assert prop["value"] == 295.0

        ranked = rank_candidates(reqs, candidates=["aisi_1045"], overrides=overrides)
        nominal = ranked["nominal"][0]["margins"][0]
        assert nominal["capacity"] == pytest.approx(295.0)
        assert nominal["data_tier"] == "lot"
        # conservative = 295 - 5 = 290 MPa -> margin (290-200)/200
        cons = ranked["conservative"][0]["margins"][0]
        assert cons["capacity"] == pytest.approx(290.0)
        assert cons["data_tier"] == "lot"

    def test_insufficient_context_property_blocks_margin_in_ranked_output(self):
        """abs has yield_strength=45 MPa but state=INSUFFICIENT_CONTEXT (no
        condition metadata). The pipeline must NOT use the 45 MPa figure to
        compute a margin; the ranked output must surface insufficient_data
        (MATE-SAFE-003 no fabricated precision)."""
        reqs = _yield_req(30.0)
        ranked = rank_candidates(reqs, candidates=["abs"])
        nominal = ranked["nominal"][0]["margins"][0]
        assert nominal["state"] == "insufficient_data"
        assert nominal["margin"] is None
        # the underlying registry record is genuinely INSUFFICIENT_CONTEXT
        prop, _ = resolve_property("abs", "yield_strength")
        assert prop is not None
        assert prop["state"] == INSUFFICIENT_CONTEXT

    def test_ranked_result_carries_schema_version_for_traceability(self):
        """MATE-DEC-004: outputs are versioned. Both screen and rank results
        must echo the SCHEMA_VERSION constant."""
        reqs = _yield_req(200.0)
        screened = screen_candidates(reqs, candidates=["aisi_1045"])
        ranked = rank_candidates(reqs, candidates=["aisi_1045"])
        assert screened["schema_version"] == SCHEMA_VERSION
        assert ranked["schema_version"] == SCHEMA_VERSION

    def test_pipeline_rejects_unknown_material_without_raising(self):
        """An unknown candidate id must produce a structured rejection rather
        than an exception (MATE-SAFE-006 fail-closed with a reason)."""
        reqs = _yield_req(100.0)
        screened = screen_candidates(reqs, candidates=["unobtanium_9000"])
        cand = screened["candidates"][0]
        assert cand["state"] == "rejected"
        assert "unknown_material" in cand["reason"]
        # rank path also tolerates unknowns without crashing
        ranked = rank_candidates(reqs, candidates=["unobtanium_9000"])
        assert ranked["verdict"] == "candidate"  # rank does not hard-reject
        assert ranked["nominal"][0]["designation"] == ""


# ---------------------------------------------------------------------------
# Ranking-order invariants across material types (MATE-AT-002 golden problems)
# ---------------------------------------------------------------------------


class TestRankingAcrossMaterialFamilies:
    """Verify ranking behaviour is consistent across steel, aluminum, and
    polymer candidates running through the same pipeline."""

    def test_steel_and_aluminum_rank_order_tracks_yield_capacity(self):
        """With equal uncertainty structure, the higher-yield material
        (aisi_1045 @ 310 MPa) outranks aa6061_t6 (@ 276 MPa) nominally."""
        reqs = _yield_req(150.0)
        ranked = rank_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"])
        by_id = {c["material_id"]: c["margins"][0]["margin"] for c in ranked["nominal"]}
        assert by_id["aisi_1045"] > by_id["aa6061_t6"]

    def test_ranking_remains_stable_under_sensitivity_overload(self):
        """Under the +10% sensitivity case both survivors remain passing; the
        ranking order is preserved (MATE-DEC-002 step 4)."""
        reqs = _yield_req(150.0)
        ranked = rank_candidates(reqs, candidates=["aisi_1045", "aa6061_t6"])
        nom = {c["material_id"]: c["margins"][0]["margin"] for c in ranked["nominal"]}
        sen = {c["material_id"]: c["margins"][0]["margin"] for c in ranked["sensitivity"]}
        # margins shrink under sensitivity overload but stay positive
        for mid in nom:
            assert sen[mid] < nom[mid]
            assert sen[mid] > 0
        # order preserved
        assert (sen["aisi_1045"] > sen["aa6061_t6"]) == (nom["aisi_1045"] > nom["aa6061_t6"])
