"""Golden material selection tests (spec MATE-AT-002).

Runs 30 reviewed golden problems through screen_candidates() and
rank_candidates(), verifying expected survivors, hard-constraint rejections,
ranking order, and traceable trade-offs.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.material_selection import (
    rank_candidates,
    screen_candidates,
)

from tests.fixtures.golden_material_selection import (
    GOLDEN_PROBLEMS,
    GoldenSelectionProblem,
    register_extended_materials,
)

_EXTRA_COUNT = register_extended_materials()


# ─── Setup verification ────────────────────────────────────────────────────


def test_extended_materials_registered():
    """At least 20 extra materials registered for golden tests."""
    assert _EXTRA_COUNT >= 20, f"Expected >=20 extra materials, got {_EXTRA_COUNT}"


# ─── Parametrized golden suite ──────────────────────────────────────────────


@pytest.mark.parametrize("problem", GOLDEN_PROBLEMS, ids=lambda p: p.id)
class TestGoldenMaterialSelection:
    # ── Screening ──────────────────────────────────────────────────────

    def test_screening_rejects_hard_constraints(self, problem: GoldenSelectionProblem):
        result = screen_candidates(problem.requirements, problem.candidates)

        # Every candidate in the input should appear in the result.
        result_ids = {r["material_id"] for r in result["candidates"]}
        for cid in problem.candidates:
            assert cid in result_ids, f"{cid} missing from screen result"

        # Expected rejections must have state="rejected" and reason matches.
        for reject_id, expected_substring in problem.expected_rejections:
            match = next(
                (r for r in result["candidates"] if r["material_id"] == reject_id),
                None,
            )
            assert match is not None, f"Expected rejection {reject_id} not in results"
            assert match["state"] == "rejected", f"{reject_id} expected rejected but was {match['state']}"
            assert expected_substring.lower() in match["reason"].lower(), (
                f"Rejection reason for {reject_id} does not contain '{expected_substring}': got '{match['reason']}'"
            )

        # Expected survivors must have state="survived".
        for surv_id in problem.expected_survivors:
            match = next(
                (r for r in result["candidates"] if r["material_id"] == surv_id),
                None,
            )
            assert match is not None, f"Expected survivor {surv_id} not in results"
            assert match["state"] == "survived", (
                f"{surv_id} expected survived but was {match['state']}: {match['reason']}"
            )

    # ── Ranking ────────────────────────────────────────────────────────

    def test_ranking_returns_nominal_conservative_sensitivity(self, problem: GoldenSelectionProblem):
        result = rank_candidates(problem.requirements, problem.candidates)

        for case in ("nominal", "conservative", "sensitivity"):
            assert case in result, f"Missing case '{case}' in result"
            assert isinstance(result[case], list), f"'{case}' is not a list"

        assert result["verdict"] == "candidate"

    def test_ranking_best_candidate_is_top_nominal(self, problem: GoldenSelectionProblem):
        result = rank_candidates(problem.requirements, problem.candidates)
        nominal_entries = result["nominal"]

        if not nominal_entries:
            pytest.skip("No nominal entries to rank")

        def avg_margin(entry: dict) -> float:
            margins = entry.get("margins", [])
            if not margins:
                return -999.0
            valid = [m["margin"] for m in margins if m.get("margin") is not None]
            return sum(valid) / len(valid) if valid else -999.0

        sorted_entries = sorted(nominal_entries, key=avg_margin, reverse=True)
        top_id = sorted_entries[0]["material_id"] if sorted_entries else ""

        assert top_id == problem.best_candidate, (
            f"Expected best candidate {problem.best_candidate} but got "
            f"{top_id}. Ranking order: "
            f"{[e['material_id'] for e in sorted_entries]}. "
            f"Rationale: {problem.ranking_rationale}"
        )

    def test_survivors_have_tradeoff_profiles(self, problem: GoldenSelectionProblem):
        result = rank_candidates(problem.requirements, problem.candidates)

        for case in ("nominal", "conservative", "sensitivity"):
            for entry in result[case]:
                if entry["material_id"] in problem.expected_survivors:
                    assert "tradeoffs" in entry, f"{entry['material_id']} missing tradeoffs in {case}"
                    assert isinstance(entry["tradeoffs"], dict), (
                        f"{entry['material_id']} tradeoffs not a dict in {case}"
                    )
                    assert "performance_indices" in entry, (
                        f"{entry['material_id']} missing performance_indices in {case}"
                    )

    def test_margins_include_data_tier(self, problem: GoldenSelectionProblem):
        result = rank_candidates(problem.requirements, problem.candidates)

        for case in ("nominal", "conservative", "sensitivity"):
            for entry in result[case]:
                if entry["material_id"] in problem.expected_survivors:
                    for margin in entry.get("margins", []):
                        assert "data_tier" in margin, f"{entry['material_id']} margin missing data_tier in {case}"

    def test_ranking_exposes_source_and_unknowns(self, problem: GoldenSelectionProblem):
        result = rank_candidates(problem.requirements, problem.candidates)

        for case in ("nominal", "conservative", "sensitive", "sensitivity"):
            if case not in result:
                continue
            for entry in result[case]:
                assert "source" in entry, f"{entry['material_id']} missing source in {case}"
                assert "unknowns" in entry, f"{entry['material_id']} missing unknowns in {case}"


# ─── Boundary cases ────────────────────────────────────────────────────────


class TestGoldenEdgeCases:
    def test_empty_candidates_returns_empty(self):
        result = rank_candidates(
            {
                "load_cases": [{"id": "a", "type": "yield", "magnitude": 250, "unit": "MPa"}],
            },
            candidates=[],
        )
        for case in ("nominal", "conservative", "sensitivity"):
            assert result[case] == []

    def test_no_load_cases_returns_insufficient_data(self):
        result = rank_candidates({}, candidates=["aisi_1045"])
        assert result["verdict"] == "insufficient_data"

    def test_unknown_material_screened_out(self):
        result = screen_candidates(
            {"load_cases": [{"id": "a", "type": "yield", "magnitude": 250, "unit": "MPa"}]},
            candidates=["unobtanium"],
        )
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["state"] == "rejected"
        assert "unknown_material" in result["candidates"][0]["reason"]

    def test_all_rejected_yields_infeasible_verdict(self):
        result = screen_candidates(
            {"load_cases": [{"id": "a", "type": "yield", "magnitude": 9999, "unit": "MPa"}]},
            candidates=["aisi_1045", "aa6061_t6"],
        )
        assert result["verdict"] == "infeasible"
        assert all(r["state"] == "rejected" for r in result["candidates"])

    def test_overrides_supplier_data_affect_margin(self):
        """Supplier overrides should change margins (data-tier effect)."""
        reqs = {
            "load_cases": [{"id": "a", "type": "yield", "magnitude": 300, "unit": "MPa"}],
        }
        # Without overrides: 1045 has yield 310 MPa → margin ~0.033
        base = rank_candidates(reqs, candidates=["aisi_1045"])
        # With overrides: stronger yield → higher margin
        override = rank_candidates(
            reqs,
            candidates=["aisi_1045"],
            overrides={
                "aisi_1045": {
                    "yield_strength": {
                        "value": 500.0,
                        "unit": "MPa",
                        "uncertainty": 10.0,
                        "tier": "supplier",
                    }
                }
            },
        )
        base_margin = base["nominal"][0]["margins"][0]["margin"]
        override_margin = override["nominal"][0]["margins"][0]["margin"]
        assert override_margin > base_margin, f"Override ({override_margin}) should exceed base ({base_margin})"
        assert override["nominal"][0]["margins"][0]["data_tier"] == "supplier"

    def test_conservative_case_reduces_margin_vs_nominal(self):
        result = rank_candidates(
            {"load_cases": [{"id": "a", "type": "yield", "magnitude": 200, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        nominal_margin = result["nominal"][0]["margins"][0]["margin"]
        conservative_margin = result["conservative"][0]["margins"][0]["margin"]
        assert conservative_margin <= nominal_margin, (
            f"Conservative ({conservative_margin}) should be <= nominal ({nominal_margin})"
        )

    def test_sensitivity_case_reduces_margin_vs_nominal(self):
        result = rank_candidates(
            {"load_cases": [{"id": "a", "type": "yield", "magnitude": 200, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        nominal_margin = result["nominal"][0]["margins"][0]["margin"]
        sensitivity_margin = result["sensitivity"][0]["margins"][0]["margin"]
        assert sensitivity_margin <= nominal_margin, (
            f"Sensitivity ({sensitivity_margin}) should be <= nominal ({nominal_margin})"
        )

    def test_unit_conversion_between_mpa_and_gpa(self):
        """Verify modulus values in GPa are normalized to MPa in tradeoffs."""
        result = rank_candidates(
            {"load_cases": [{"id": "a", "type": "yield", "magnitude": 200, "unit": "MPa"}]},
            candidates=["aisi_1045"],
        )
        tradeoffs = result["nominal"][0]["tradeoffs"]
        mod = tradeoffs.get("youngs_modulus", {})
        assert mod.get("unit") == "MPa", f"Expected MPa in tradeoffs, got {mod.get('unit')}"
        assert mod["value"] > 100000, f"Expected 200 GPa → 200000 MPa, got {mod['value']}"
