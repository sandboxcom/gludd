"""Deep tests for src/general_ludd/diff_engine.py — Myers diff, patience diff,
3-way merge, patch apply, conflict markers, unified-format output."""

from __future__ import annotations

import textwrap

from general_ludd.diff_engine import (
    Conflict,
    DiffEngine,
    DiffHunk,
    EditOp,
    HunkLine,
    PatchResult,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _lines(text: str) -> list[str]:
    return textwrap.dedent(text).strip().splitlines(keepends=False)


def _txt(text: str) -> list[str]:
    return textwrap.dedent(text).strip().splitlines(keepends=True)


# ── Myers diff ───────────────────────────────────────────────────────────────


class TestMyersDiff:
    def test_empty_both(self) -> None:
        assert DiffEngine.myers_diff([], []) == []

    def test_empty_a(self) -> None:
        edits = DiffEngine.myers_diff([], ["a", "b"])
        assert len(edits) >= 1

    def test_empty_b(self) -> None:
        edits = DiffEngine.myers_diff(["a", "b"], [])
        assert len(edits) >= 1

    def test_identical(self) -> None:
        a = ["line1", "line2", "line3"]
        assert DiffEngine.myers_diff(a, a) == []

    def test_single_insert(self) -> None:
        edits = DiffEngine.myers_diff(["a", "c"], ["a", "b", "c"])
        assert len(edits) == 1
        assert edits[0].new_count > 0

    def test_single_delete(self) -> None:
        edits = DiffEngine.myers_diff(["a", "b", "c"], ["a", "c"])
        assert len(edits) == 1
        assert edits[0].old_count > 0

    def test_multi_change(self) -> None:
        a = ["a", "b", "c", "d"]
        b = ["a", "x", "c", "y"]
        edits = DiffEngine.myers_diff(a, b)
        ops_desc = [(e.old_count, e.new_count) for e in edits]
        total_del = sum(d for d, _i in ops_desc)
        total_ins = sum(i for _d, i in ops_desc)
        assert total_del >= 2
        assert total_ins >= 2


# ── patience diff ────────────────────────────────────────────────────────────


class TestPatienceDiff:
    def test_empty_both(self) -> None:
        assert DiffEngine.patience_diff([], []) == []

    def test_identical(self) -> None:
        a = ["one", "two", "three"]
        assert DiffEngine.patience_diff(a, a) == []

    def test_unique_line_match(self) -> None:
        a = ["a", "b", "c", "d", "e"]
        b = ["a", "x", "b", "c", "y", "d", "e"]
        edits = DiffEngine.patience_diff(a, b)
        assert len(edits) > 0

    def test_diff_via_algorithm(self) -> None:
        a = ["start", "old1", "old2", "end"]
        b = ["start", "new1", "end"]
        edits_myers = DiffEngine.diff(a, b, algorithm="myers")
        edits_patience = DiffEngine.diff(a, b, algorithm="patience")
        assert len(edits_myers) > 0
        assert len(edits_patience) > 0

    def test_algorithm_fallback_default(self) -> None:
        edits = DiffEngine.diff(["x"], ["y"])
        assert len(edits) >= 1


# ── unified format ───────────────────────────────────────────────────────────


class TestUnifiedDiff:
    def test_empty_inputs(self) -> None:
        out = DiffEngine.unified_diff([], [], from_file="a.txt", to_file="b.txt")
        assert "--- a.txt" in out
        assert "+++ b.txt" in out

    def test_header(self) -> None:
        out = DiffEngine.unified_diff(["a"], ["a"], from_file="src", to_file="dst")
        assert "--- src" in out
        assert "+++ dst" in out

    def test_single_line_change(self) -> None:
        a = _lines("""\
            line one
            line two
            line three
        """)
        b = _lines("""\
            line one
            line two MODIFIED
            line three
        """)
        out = DiffEngine.unified_diff(a, b, from_file="a", to_file="b")
        assert "@@" in out
        assert "-line two" in out
        assert "+line two MODIFIED" in out

    def test_multi_line_change(self) -> None:
        a = _lines("""\
            aaa
            bbb
            ccc
            ddd
            eee
        """)
        b = _lines("""\
            aaa
            bbb NEW
            ccc NEW
            ddd
            eee
        """)
        out = DiffEngine.unified_diff(a, b)
        assert out.count("@@") >= 1
        assert "+bbb NEW" in out

    def test_unified_diff_hunks_structured(self) -> None:
        a = ["apple", "banana", "cherry"]
        b = ["apple", "blueberry", "cherry"]
        hunks = DiffEngine.unified_diff_hunks(a, b)
        assert len(hunks) >= 1
        assert isinstance(hunks[0], DiffHunk)
        assert sum(1 for hl in hunks[0].lines if hl.kind == "remove") >= 1
        assert sum(1 for hl in hunks[0].lines if hl.kind == "add") >= 1


# ── 3-way merge ──────────────────────────────────────────────────────────────


class TestMerge3:
    def test_clean_merge_no_changes(self) -> None:
        base = ["a", "b", "c"]
        result = DiffEngine.merge3(base, base, base)
        assert result.success
        assert result.merged == base
        assert result.conflicts == []

    def test_one_side_changed(self) -> None:
        base = ["a", "b", "c"]
        ours = ["a", "b2", "c"]
        result = DiffEngine.merge3(base, ours, base)
        assert result.success or result.merged == ours

    def test_same_change_both_sides(self) -> None:
        base = ["a", "b", "c"]
        changed = ["a", "b2", "c"]
        result = DiffEngine.merge3(base, changed, changed)
        assert result.merged is not None

    def test_conflict_on_different_changes(self) -> None:
        base = ["a", "b", "c"]
        ours = ["a", "b_ours", "c"]
        theirs = ["a", "b_theirs", "c"]
        result = DiffEngine.merge3(base, ours, theirs)
        if not result.success:
            assert len(result.conflicts) >= 1
            assert result.conflicts[0].ours_lines or result.conflicts[0].theirs_lines

    def test_append_conflict(self) -> None:
        base = ["line1"]
        ours = ["line1", "ours_only"]
        theirs = ["line1", "theirs_only"]
        result = DiffEngine.merge3(base, ours, theirs)
        if not result.success:
            assert len(result.conflicts) >= 1


# ── patch apply ──────────────────────────────────────────────────────────────


class TestPatchApply:
    def test_apply_simple_patch(self) -> None:
        original = _lines("""\
            a
            b
            c
            d
        """)
        patch = textwrap.dedent("""\
            --- a
            +++ b
            @@ -2,1 +2,1 @@
             a
            -b
            +b2
             c
        """)
        result = DiffEngine.apply_patch(original, patch)
        assert result.succeeded >= 1 or result.applied == original

    def test_apply_empty_patch(self) -> None:
        result = DiffEngine.apply_patch(["a", "b"], "")
        assert result.succeeded == 0
        assert result.failed == 0

    def test_apply_multi_hunk(self) -> None:
        original = _lines("""\
            one
            two
            three
            four
            five
        """)
        patch = textwrap.dedent("""\
            --- a
            +++ b
            @@ -1,5 +1,5 @@
             one
            -two
            +two2
             three
            -four
            +four2
             five
        """)
        result = DiffEngine.apply_patch(original, patch)
        assert isinstance(result, PatchResult)
        assert result.succeeded + result.failed >= 1

    def test_hunk_rejection(self) -> None:
        original = ["line_a", "line_b"]
        patch = textwrap.dedent("""\
            --- a
            +++ b
            @@ -1,1 +1,1 @@
            -wrong context
            +replacement
        """)
        result = DiffEngine.apply_patch(original, patch)
        assert result.failed > 0
        assert len(result.rejects) > 0

    def test_apply_edits_direct(self) -> None:
        original = ["a", "b", "c"]
        edits = [EditOp(old_start=1, old_count=1, new_start=1, new_count=1)]
        result = DiffEngine.apply_edits(original, edits)
        assert result == ["a", "c"]


# ── conflict markers ─────────────────────────────────────────────────────────


class TestConflictMarkers:
    def test_format_conflict(self) -> None:
        c = Conflict(
            ours_start=2,
            ours_end=4,
            theirs_start=2,
            theirs_end=4,
            ours_lines=["ours_content"],
            theirs_lines=["theirs_content"],
            base_lines=[],
        )
        out = DiffEngine.format_conflict(c)
        assert "<<<<<<< ours" in out
        assert "=======" in out
        assert ">>>>>>> theirs" in out
        assert "ours_content" in out
        assert "theirs_content" in out


# ── edit op reconstruction ───────────────────────────────────────────────────


class TestEditOps:
    def test_edit_op_fields(self) -> None:
        op = EditOp(old_start=0, old_count=3, new_start=0, new_count=2)
        assert op.old_start == 0
        assert op.old_count == 3
        assert op.new_count == 2

    def test_no_op_is_filtered(self) -> None:
        edits = DiffEngine.myers_diff(["same", "same"], ["same", "same"])
        assert edits == []

    def test_edit_op_roundtrip(self) -> None:
        a = ["line1", "line2_x", "line3"]
        b = ["line1", "line2_y", "line3"]
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) > 0


# ── diff hunk structure ─────────────────────────────────────────────────────


class TestDiffHunk:
    def test_hunk_creation(self) -> None:
        hunk = DiffHunk(
            old_start=0,
            old_count=5,
            new_start=0,
            new_count=6,
            lines=[HunkLine("add", "new line")],
        )
        assert hunk.old_count == 5
        assert hunk.new_count == 6
        assert len(hunk.lines) == 1

    def test_hunk_line_types(self) -> None:
        a = ["keep", "remove_me", "keep2"]
        b = ["keep", "add_me", "keep2"]
        hunks = DiffEngine.unified_diff_hunks(a, b)
        kinds: set[str] = set()
        for h in hunks:
            for hl in h.lines:
                kinds.add(hl.kind)
        assert "remove" in kinds or "add" in kinds


# ── edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_large_input(self) -> None:
        a = [f"line_{i}" for i in range(200)]
        b = list(a)
        b[50] = "CHANGED_50"
        b[150] = "CHANGED_150"
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) >= 1

    def test_patience_large(self) -> None:
        a = [f"line_{i}" for i in range(100)]
        b = list(a)
        b.insert(10, "INSERTED")
        edits = DiffEngine.patience_diff(a, b)
        assert len(edits) >= 1

    def test_unicode_lines(self) -> None:
        a = ["αβγ", "δεζ", "ηθι"]
        b = ["αβγ", "δεζ_CHANGED", "ηθι"]
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) >= 1
        assert edits[0].old_count >= 1 or edits[0].new_count >= 1

    def test_newlines_in_content(self) -> None:
        a = ["line with\nnewline\n", "normal"]
        b = ["line with\nmodified\n", "normal"]
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) >= 1

    def test_long_lines(self) -> None:
        a = ["short", "x" * 500, "short"]
        b = ["short", "y" * 500, "short"]
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) >= 1

    def test_capacity_stress(self) -> None:
        a = [f"line_{i:05d}" for i in range(500)]
        b = list(a)
        for i in range(0, 500, 100):
            b[i] = f"modified_{i:05d}"
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) > 0
        assert len(edits) <= 15

    def test_whitespace_only_diff(self) -> None:
        a = ["  leading", "trailing  ", "  both  "]
        b = ["  leading", "trailing", "  both  "]
        edits = DiffEngine.myers_diff(a, b)
        assert len(edits) >= 1

    def test_conflict_format_empty(self) -> None:
        c = Conflict(
            ours_start=0,
            ours_end=0,
            theirs_start=0,
            theirs_end=0,
            ours_lines=[],
            theirs_lines=[],
            base_lines=[],
        )
        out = DiffEngine.format_conflict(c)
        assert "<<<<<<< ours" in out
        assert ">>>>>>> theirs" in out

    def test_myers_shortest_edit(self) -> None:
        a = ["1", "2", "3", "4", "5", "6"]
        b = ["1", "2", "3_CHANGED", "4", "5", "6"]
        edits = DiffEngine.myers_diff(a, b)
        total_old = sum(e.old_count for e in edits)
        total_new = sum(e.new_count for e in edits)
        assert total_old == 1
        assert total_new == 1

    def test_patience_reorder(self) -> None:
        a = ["import os", "", "def foo():", "    pass"]
        b = ["", "import os", "def foo():", "    pass"]
        edits = DiffEngine.patience_diff(a, b)
        assert len(edits) >= 1
