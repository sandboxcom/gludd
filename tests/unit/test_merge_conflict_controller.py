"""Unit tests for the merge-conflict triage controller."""

from __future__ import annotations

from general_ludd.controllers.merge_conflict import (
    ConflictKind,
    MergeConflictController,
    ResolutionStrategy,
)


def _conflict(ours: str, theirs: str, *, before: str = "x = 0\n", after: str = "\ny = 9") -> str:
    """Build file content with a single conflict block around real lines."""
    return (
        f"{before}"
        f"<<<<<<< ours\n"
        f"{ours}"
        f"=======\n"
        f"{theirs}"
        f">>>>>>> theirs"
        f"{after}"
    )


class TestParseHunks:
    def test_no_markers_yields_no_hunks(self):
        ctrl = MergeConflictController()
        assert ctrl.parse_hunks("a = 1\nb = 2\n") == []

    def test_parses_a_single_two_way_hunk(self):
        ctrl = MergeConflictController()
        content = _conflict("a = 1\n", "a = 2\n")
        hunks = ctrl.parse_hunks(content)
        assert len(hunks) == 1
        assert hunks[0].ours == ("a = 1",)
        assert hunks[0].theirs == ("a = 2",)

    def test_records_one_based_marker_line(self):
        ctrl = MergeConflictController()
        content = _conflict("a = 1\n", "a = 2\n", before="x = 0\nz = 0\n")
        hunks = ctrl.parse_hunks(content)
        # before has 2 lines, so the <<<<<<< marker is on line 3.
        assert hunks[0].start_line == 3

    def test_parses_multiple_hunks_in_order(self):
        ctrl = MergeConflictController()
        content = (
            "<<<<<<< ours\na = 1\n=======\na = 2\n>>>>>>> theirs\n"
            "middle\n"
            "<<<<<<< ours\nb = 1\n=======\nb = 2\n>>>>>>> theirs\n"
        )
        hunks = ctrl.parse_hunks(content)
        assert len(hunks) == 2
        assert hunks[0].ours == ("a = 1",)
        assert hunks[1].theirs == ("b = 2",)

    def test_ignores_unterminated_block(self):
        ctrl = MergeConflictController()
        content = "<<<<<<< ours\na = 1\n=======\na = 2\n"  # no closing marker
        assert ctrl.parse_hunks(content) == []

    def test_ignores_block_without_separator(self):
        ctrl = MergeConflictController()
        content = "<<<<<<< ours\na = 1\n>>>>>>> theirs\n"
        assert ctrl.parse_hunks(content) == []

    def test_diff3_base_section_is_dropped_from_ours(self):
        ctrl = MergeConflictController()
        content = (
            "<<<<<<< ours\n"
            "a = 1\n"
            "||||||| base\n"
            "a = 0\n"
            "=======\n"
            "a = 2\n"
            ">>>>>>> theirs\n"
        )
        hunks = ctrl.parse_hunks(content)
        assert len(hunks) == 1
        assert hunks[0].ours == ("a = 1",)
        assert hunks[0].theirs == ("a = 2",)


class TestClassify:
    def test_identical_sides(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("a = 1\n", "a = 1\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.IDENTICAL

    def test_add_on_one_side_when_theirs_empty(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("a = 1\n", ""))[0]
        assert ctrl.classify(hunk) is ConflictKind.ADD_ON_ONE_SIDE

    def test_add_on_one_side_when_ours_empty(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("", "a = 2\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.ADD_ON_ONE_SIDE

    def test_whitespace_only_difference(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("a = 1\n", "    a = 1\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.WHITESPACE_ONLY

    def test_import_block(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("import os\n", "import sys\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.IMPORT_BLOCK

    def test_semantic_divergence(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("return compute_a()\n", "return compute_b()\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.SEMANTIC

    def test_mixed_import_and_code_is_semantic_not_import_block(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("import os\nx = os.getpid()\n", "import sys\n"))[0]
        assert ctrl.classify(hunk) is ConflictKind.SEMANTIC


class TestResolveHunk:
    def test_identical_takes_either_full_confidence(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("a = 1\n", "a = 1\n"))[0]
        res = ctrl.resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_EITHER
        assert res.confidence == 1.0
        assert res.resolved_lines == ("a = 1",)

    def test_add_on_one_side_takes_ours_when_theirs_empty(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("a = 1\n", ""))[0]
        res = ctrl.resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_OURS
        assert res.resolved_lines == ("a = 1",)

    def test_add_on_one_side_takes_theirs_when_ours_empty(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("", "a = 2\n"))[0]
        res = ctrl.resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_THEIRS
        assert res.resolved_lines == ("a = 2",)

    def test_import_block_unions_and_dedupes_sorted(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("import sys\nimport os\n", "import os\nimport json\n"))[0]
        res = ctrl.resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_UNION
        assert res.resolved_lines == ("import json", "import os", "import sys")

    def test_semantic_escalates_with_zero_confidence(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("return a()\n", "return b()\n"))[0]
        res = ctrl.resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.ESCALATE
        assert res.confidence == 0.0
        assert res.resolved_lines is None

    def test_resolution_is_deterministic(self):
        ctrl = MergeConflictController()
        hunk = ctrl.parse_hunks(_conflict("import b\nimport a\n", "import c\n"))[0]
        first = ctrl.resolve_hunk(hunk)
        second = ctrl.resolve_hunk(hunk)
        assert first == second


class TestPlanFile:
    def test_empty_when_no_conflicts(self):
        ctrl = MergeConflictController()
        plan = ctrl.plan_file("clean.py", "a = 1\nb = 2\n")
        assert plan.resolutions == []
        assert plan.auto_resolvable is False  # nothing to resolve => not auto-resolvable
        assert plan.escalation_count == 0

    def test_all_mechanical_is_auto_resolvable(self):
        ctrl = MergeConflictController()
        content = (
            "<<<<<<< ours\nimport os\n=======\nimport sys\n>>>>>>> theirs\n"
            "code\n"
            "<<<<<<< ours\na = 1\n=======\na = 1\n>>>>>>> theirs\n"
        )
        plan = ctrl.plan_file("f.py", content)
        assert len(plan.resolutions) == 2
        assert plan.auto_resolvable is True
        assert plan.escalation_count == 0

    def test_one_semantic_hunk_forces_whole_file_to_escalate(self):
        ctrl = MergeConflictController()
        content = (
            "<<<<<<< ours\nimport os\n=======\nimport sys\n>>>>>>> theirs\n"
            "code\n"
            "<<<<<<< ours\nreturn a()\n=======\nreturn b()\n>>>>>>> theirs\n"
        )
        plan = ctrl.plan_file("f.py", content)
        assert plan.auto_resolvable is False
        assert plan.escalation_count == 1

    def test_path_is_echoed(self):
        ctrl = MergeConflictController()
        plan = ctrl.plan_file("pkg/mod.py", _conflict("a = 1\n", "a = 2\n"))
        assert plan.path == "pkg/mod.py"
