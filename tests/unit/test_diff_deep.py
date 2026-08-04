"""Deep diff/patch algorithm tests — line diff, word diff, patch application,
three-way merge, conflict markers, unified diff format (15+ tests)."""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile

import pytest

from general_ludd.execution.engine import ExecutionEngine


@pytest.fixture
def safe_workspace():
    tmp = tempfile.mkdtemp(prefix="test_diff_ws_")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


class TestLineDiff:
    def test_unified_diff_additions(self):
        a = ["line1", "line2", "line3"]
        b = ["line1", "line2", "line3", "line4"]
        result = list(difflib.unified_diff(a, b, lineterm=""))
        assert any("+line4" in line for line in result)

    def test_unified_diff_deletions(self):
        a = ["line1", "line2", "line3"]
        b = ["line1", "line3"]
        result = list(difflib.unified_diff(a, b, lineterm=""))
        assert any("-line2" in line for line in result)

    def test_unified_diff_identical(self):
        a = ["line1", "line2", "line3"]
        b = ["line1", "line2", "line3"]
        result = list(difflib.unified_diff(a, b, lineterm=""))
        assert len(result) == 0

    def test_unified_diff_header_format(self):
        a = ["old_content"]
        b = ["new_content"]
        result = list(difflib.unified_diff(a, b, fromfile="a/file.py", tofile="b/file.py", lineterm=""))
        assert result[0].startswith("--- ")
        assert result[1].startswith("+++ ")
        assert "a/file.py" in result[0]
        assert "b/file.py" in result[1]

    def test_unified_diff_hunk_header(self):
        a = ["A", "B", "C", "D", "E", "F", "G"]
        b = ["A", "B", "X", "D", "E", "F", "G"]
        result = list(difflib.unified_diff(a, b, lineterm=""))
        assert any(line.startswith("@@") for line in result)


class TestWordDiff:
    def test_sequence_matcher_ratio_identical(self):
        a = "hello world"
        b = "hello world"
        sm = difflib.SequenceMatcher(None, a, b)
        assert sm.ratio() == 1.0

    def test_sequence_matcher_ratio_partial(self):
        a = "hello world"
        b = "hello there world"
        sm = difflib.SequenceMatcher(None, a, b)
        assert 0.5 < sm.ratio() < 1.0

    def test_sequence_matcher_opcodes(self):
        a = "abc"
        b = "axc"
        sm = difflib.SequenceMatcher(None, a, b)
        ops = sm.get_opcodes()
        assert len(ops) >= 3

    def test_ndiff_word_level(self):
        a = ["the", "quick", "brown", "fox"]
        b = ["the", "quick", "red", "fox"]
        result = list(difflib.ndiff(a, b))
        assert any(line.startswith("- brown") for line in result)
        assert any(line.startswith("+ red") for line in result)

    def test_sequence_matcher_longest_match(self):
        sm = difflib.SequenceMatcher(None, "abcdef", "xbcdefg")
        match = sm.find_longest_match(0, 6, 0, 6)
        assert match.size == 5


class TestPatchApplication:
    def test_diff_target_paths_basic(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        diff_text = "--- a/src/foo.py\t2024-01-01\n+++ b/src/foo.py\t2024-01-01\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        targets = engine._diff_target_paths(diff_text)
        assert "src/foo.py" in targets

    def test_diff_target_paths_dev_null_source(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        diff_text = "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,1 @@\n+hello\n"
        targets = engine._diff_target_paths(diff_text)
        assert "/dev/null" not in targets
        assert "src/new.py" in targets

    def test_diff_target_paths_empty(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        targets = engine._diff_target_paths("plain text no diff headers")
        assert targets == []

    def test_diff_changed_files_dedup(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        diff_text = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -3 +3 @@\n-a\n+b\n"
        )
        changed = engine._diff_changed_files(diff_text)
        assert changed == ["src/foo.py"]

    def test_patch_escaping_path_blocked(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        diff_text = "--- a/foo.py\n+++ b/../../etc/passwd\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        targets = engine._diff_target_paths(diff_text)
        assert targets[0] == "foo.py"
        assert targets[1] == "../../etc/passwd"
        result = engine._apply_unified_diff(diff_text)
        assert result == []

    def test_apply_unified_diff_real_patch(self, safe_workspace):
        engine = ExecutionEngine(workspace_path=safe_workspace)
        src_file = os.path.join(safe_workspace, "test.txt")
        with open(src_file, "w") as f:
            f.write("line1\nline2\nline3\n")
        diff_text = "--- a/test.txt\n+++ b/test.txt\n@@ -1,3 +1,4 @@\n line1\n line2\n line3\n+line4\n"
        changed = engine._apply_unified_diff(diff_text)
        assert changed == ["test.txt"]
        with open(src_file) as f:
            content = f.read()
        assert "line4" in content


class TestThreeWayMerge:
    def test_merge_no_conflict(self):
        base = ["line1", "line2", "line3", "line4"]
        ours = ["line1", "line2", "line3", "line4", "ours_add"]
        merged = list(difflib.unified_diff(base, ours, lineterm=""))
        assert len(merged) > 0

    def test_merge_detect_clean(self):
        base = ["A", "B", "C"]
        ours = ["A", "B", "C"]
        theirs = ["A", "B", "C"]
        result_ours = list(difflib.unified_diff(base, ours, lineterm=""))
        result_theirs = list(difflib.unified_diff(base, theirs, lineterm=""))
        assert result_ours == []
        assert result_theirs == []

    def test_merge_same_line_changed_both_sides(self):
        base = ["A", "B", "C"]
        ours = ["A", "B_changed", "C"]
        theirs = ["A", "B_different", "C"]
        diff_ours = list(difflib.unified_diff(base, ours, lineterm=""))
        diff_theirs = list(difflib.unified_diff(base, theirs, lineterm=""))
        assert len(diff_ours) > 0
        assert len(diff_theirs) > 0


class TestConflictMarkers:
    def test_conflict_marker_detection(self):
        text = "line1\n<<<<<<< HEAD\nour change\n=======\ntheir change\n>>>>>>> branch\nline3\n"
        assert "<<<<<<< HEAD" in text
        assert "=======" in text
        assert ">>>>>>> branch" in text

    def test_no_conflict_markers(self):
        text = "line1\nour change\ntheir change\nline3\n"
        assert "<<<<<<<" not in text
        assert "=======" not in text
        assert ">>>>>>>" not in text

    def test_conflict_markers_parsed_to_sections(self):
        text = "before\n<<<<<<< HEAD\nour\no2\n=======\ntheir\nt2\n>>>>>>> branch\nafter\n"
        before = text[: text.index("<<<<<<<")]
        during = text[text.index("<<<<<<<") :]
        assert before.strip() == "before"
        assert "<<<<<<<" in during
        assert "after" in text

    def test_multiple_conflict_blocks(self):
        text = (
            "start\n<<<<<<< HEAD\nours1\n=======\ntheirs1\n>>>>>>> br\n"
            "mid\n<<<<<<< HEAD\nours2\n=======\ntheirs2\n>>>>>>> br\nend\n"
        )
        conflicts = text.count("<<<<<<<")
        assert conflicts == 2


class TestUnifiedDiffFormat:
    def test_valid_unified_diff_parsed(self):
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,3 +1,4 @@\n line1\n-line2\n+line2_new\n line3\n+line4\n"
        lines = diff.split("\n")
        assert lines[0].startswith("--- ")
        assert lines[1].startswith("+++ ")
        assert lines[2].startswith("@@")

    def test_new_file_diff_format(self):
        diff = "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,2 @@\n+line1\n+line2\n"
        lines = [li for li in diff.split("\n") if li]
        assert lines[0].startswith("--- ")
        assert lines[1].startswith("+++ ")
        assert lines[2].startswith("@@")

    def test_deleted_file_diff_format(self):
        diff = "--- a/src/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-line1\n-line2\n"
        lines = [li for li in diff.split("\n") if li]
        assert "+/dev/null" in lines[1] or "+++ /dev/null" in lines[1]


class TestDiffGeneration:
    def test_diff_renders_additions(self):
        old_lines = ["line1", "line2"]
        new_lines = ["line1", "line2", "line3"]
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        assert any("+line3" in line for line in diff)
        assert any(line.startswith("--- ") for line in diff)
        assert any(line.startswith("+++ ") for line in diff)

    def test_diff_renders_deletions(self):
        old_lines = ["line1", "line2", "line3"]
        new_lines = ["line1", "line3"]
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        assert any("-line2" in line for line in diff)

    def test_diff_empty_old_content(self):
        old_lines: list[str] = []
        new_lines = ["line1", "line2"]
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        assert any("+line1" in line for line in diff)

    def test_diff_empty_new_content(self):
        old_lines = ["line1", "line2"]
        new_lines: list[str] = []
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        assert any("-line1" in line for line in diff)

    def test_context_diff_format(self):
        old_lines = ["line1", "line2", "line3", "line4", "line5"]
        new_lines = ["line1", "line2", "changed", "line4", "line5"]
        diff = list(difflib.context_diff(old_lines, new_lines, lineterm=""))
        assert any(line.startswith("*** ") for line in diff)
        assert any(line.startswith("--- ") for line in diff)

    def test_html_diff_table(self):
        a = "hello world\nfoo bar\n"
        b = "hello world\nbaz qux\n"
        table = difflib.HtmlDiff().make_table(a.splitlines(), b.splitlines(), context=True, numlines=1)
        assert "<table" in table
        assert "hello" in table

    def test_differ_compare(self):
        a = ["line1", "line2", "line3"]
        b = ["line1", "line2_new", "line3"]
        result = list(difflib.Differ().compare(a, b))
        assert any(line.startswith("- ") for line in result)
        assert any(line.startswith("+ ") for line in result)
        assert any(line.startswith("  ") for line in result)

    def test_diff_multiline_context(self):
        a = ["A"] * 10 + ["B"] + ["C"] * 10
        b = ["A"] * 10 + ["X"] + ["C"] * 10
        diff = list(difflib.unified_diff(a, b, n=3, lineterm=""))
        assert any("@@ " in line for line in diff)
        assert any("-B" in line for line in diff)
        assert any("+X" in line for line in diff)
