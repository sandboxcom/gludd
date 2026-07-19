"""TDD tests for coverage_gap_heatmap() and prioritize_scenarios() in verify_coverage.

These tests pin the behavior of the two new functions BEFORE the implementation
exists (RED), then drive the minimal implementation (GREEN).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = (
    COLLECTION_ROOT
    / "roles"
    / "verify_coverage"
    / "files"
    / "verify_coverage.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_coverage_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_coverage_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


vc = _load_module()


# ── coverage_gap_heatmap ──────────────────────────────────────────────────


class TestCoverageGapHeatmap:
    def test_returns_rows_with_module_and_cells(self):
        modules = [
            {
                "name": "worker.py",
                "coverage_pct": 60.0,
                "symbols": [
                    {"name": "run", "state": "covered", "missing_lines": []},
                    {"name": "stop", "state": "missing", "missing_lines": [10, 11]},
                ],
            },
        ]
        hm = vc.coverage_gap_heatmap(modules)
        assert isinstance(hm, list)
        assert len(hm) == 1
        row = hm[0]
        assert row["module"] == "worker.py"
        assert row["coverage_pct"] == 60.0
        assert "cells" in row
        assert len(row["cells"]) == 2
        assert row["cells"][0]["symbol"] == "run"
        assert row["cells"][0]["glyph"] == "covered"
        assert row["cells"][1]["symbol"] == "stop"
        assert row["cells"][1]["glyph"] == "missing"

    def test_glyph_assignment_for_each_state(self):
        modules = [
            {
                "name": "m.py",
                "coverage_pct": 0.0,
                "symbols": [
                    {"name": "a", "state": "covered", "missing_lines": []},
                    {"name": "b", "state": "partial", "missing_lines": [3]},
                    {"name": "c", "state": "missing", "missing_lines": [1, 2]},
                ],
            },
        ]
        hm = vc.coverage_gap_heatmap(modules)
        cells = {c["symbol"]: c["glyph"] for c in hm[0]["cells"]}
        assert cells == {"a": "covered", "b": "partial", "c": "missing"}

    def test_render_returns_ascii_grid(self):
        modules = [
            {
                "name": "worker.py",
                "coverage_pct": 50.0,
                "symbols": [
                    {"name": "run", "state": "covered", "missing_lines": []},
                    {"name": "stop", "state": "missing", "missing_lines": [1]},
                ],
            },
        ]
        hm = vc.coverage_gap_heatmap(modules)
        rendered = vc.render_heatmap(hm)
        assert isinstance(rendered, str)
        assert "worker.py" in rendered
        assert "run" in rendered
        assert "stop" in rendered

    def test_empty_modules_returns_empty_list(self):
        assert vc.coverage_gap_heatmap([]) == []

    def test_symbol_without_state_defaults_to_missing(self):
        modules = [
            {
                "name": "m.py",
                "coverage_pct": 0.0,
                "symbols": [{"name": "x"}],
            },
        ]
        hm = vc.coverage_gap_heatmap(modules)
        assert hm[0]["cells"][0]["glyph"] == "missing"

    def test_missing_lines_count_in_cell(self):
        modules = [
            {
                "name": "m.py",
                "coverage_pct": 0.0,
                "symbols": [
                    {"name": "a", "state": "partial", "missing_lines": [1, 2, 3]},
                ],
            },
        ]
        hm = vc.coverage_gap_heatmap(modules)
        assert hm[0]["cells"][0]["missing_count"] == 3


# ── prioritize_scenarios ──────────────────────────────────────────────────


class TestPrioritizeScenarios:
    def test_ranks_by_priority_score_descending(self):
        gaps = [
            {
                "target": "low_risk_fn",
                "kind": "partial",
                "missing_lines": [1],
                "line_range": [1, 2],
            },
            {
                "target": "high_risk_fn",
                "kind": "missing",
                "missing_lines": [10, 11, 12, 13],
                "line_range": [10, 20],
            },
        ]
        ranked = vc.prioritize_scenarios(gaps)
        assert isinstance(ranked, list)
        assert len(ranked) == 2
        assert ranked[0]["target"] == "high_risk_fn"
        assert ranked[1]["target"] == "low_risk_fn"
        assert ranked[0]["priority_score"] >= ranked[1]["priority_score"]

    def test_missing_ranks_above_partial(self):
        gaps = [
            {"target": "p", "kind": "partial", "missing_lines": [1], "line_range": [1, 1]},
            {"target": "m", "kind": "missing", "missing_lines": [1], "line_range": [1, 1]},
        ]
        ranked = vc.prioritize_scenarios(gaps)
        assert ranked[0]["target"] == "m"
        assert ranked[1]["target"] == "p"

    def test_each_entry_has_priority_score_and_rationale(self):
        gaps = [
            {"target": "x", "kind": "missing", "missing_lines": [1, 2], "line_range": [1, 2]},
        ]
        ranked = vc.prioritize_scenarios(gaps)
        assert "priority_score" in ranked[0]
        assert "rationale" in ranked[0]
        assert isinstance(ranked[0]["priority_score"], (int, float))
        assert ranked[0]["priority_score"] > 0

    def test_larger_gap_gets_higher_score(self):
        gaps = [
            {"target": "big", "kind": "missing", "missing_lines": list(range(1, 21)), "line_range": [1, 20]},
            {"target": "small", "kind": "missing", "missing_lines": [1], "line_range": [1, 1]},
        ]
        ranked = vc.prioritize_scenarios(gaps)
        big = next(r for r in ranked if r["target"] == "big")
        small = next(r for r in ranked if r["target"] == "small")
        assert big["priority_score"] > small["priority_score"]

    def test_empty_gaps_returns_empty_list(self):
        assert vc.prioritize_scenarios([]) == []

    def test_unknown_kind_treated_as_partial(self):
        gaps = [
            {"target": "u", "kind": "weird", "missing_lines": [1, 2], "line_range": [1, 2]},
        ]
        ranked = vc.prioritize_scenarios(gaps)
        assert len(ranked) == 1
        assert ranked[0]["priority_score"] > 0

    def test_tiebreak_by_target_name_alphabetical(self):
        gaps = [
            {"target": "zzz", "kind": "missing", "missing_lines": [1], "line_range": [1, 1]},
            {"target": "aaa", "kind": "missing", "missing_lines": [1], "line_range": [1, 1]},
        ]
        ranked = vc.prioritize_scenarios(gaps)
        assert ranked[0]["target"] == "aaa"
        assert ranked[1]["target"] == "zzz"
