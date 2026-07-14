"""3-way safe-merge primitives — unit tests."""

from __future__ import annotations

from general_ludd.integration.safe_merge import (
    MergeResult,
    detect_overlap,
    safe_merge,
    safe_merge_file,
)


class TestMergeResult:
    def test_instantiation(self) -> None:
        result = MergeResult(text="abc", conflict=False, source="base")
        assert result.text == "abc"
        assert result.conflict is False
        assert result.source == "base"

    def test_frozen(self) -> None:
        result = MergeResult(text="x", conflict=False, source="ours")
        try:
            result.text = "y"
            raise AssertionError("frozen dataclass should be immutable")
        except Exception:
            pass

    def test_conflicting_result(self) -> None:
        result = MergeResult(text="<<<<<<< ours\n=======\n>>>>>>> theirs\n", conflict=True, source="conflict")
        assert result.conflict is True
        assert result.source == "conflict"
        assert "<<<<<<<" in result.text


class TestDetectOverlap:
    def test_neither_changed(self) -> None:
        assert detect_overlap("a", "a", "a") is False

    def test_only_ours_changed(self) -> None:
        assert detect_overlap("a", "b", "a") is False

    def test_only_theirs_changed(self) -> None:
        assert detect_overlap("a", "a", "b") is False

    def test_both_changed_same_text(self) -> None:
        assert detect_overlap("a", "b", "b") is False

    def test_both_changed_different_text(self) -> None:
        assert detect_overlap("a", "b", "c") is True

    def test_multiline_different(self) -> None:
        base = "line1\nline2\nline3\n"
        ours = "line1\nLINE2\nline3\n"
        theirs = "line1\nline2\nLINE3\n"
        assert detect_overlap(base, ours, theirs) is True

    def test_multiline_same_change(self) -> None:
        base = "a\nb\nc\n"
        ours = "a\nX\nc\n"
        theirs = "a\nX\nc\n"
        assert detect_overlap(base, ours, theirs) is False


class TestSafeMergeTrivial:
    def test_identical_three_way(self) -> None:
        result = safe_merge("a\nb\n", "a\nb\n", "a\nb\n")
        assert result.text == "a\nb\n"
        assert result.conflict is False
        assert result.source == "base"

    def test_only_ours_changed(self) -> None:
        result = safe_merge("base", "ours", "base")
        assert result.text == "ours"
        assert result.conflict is False
        assert result.source == "ours"

    def test_only_theirs_changed(self) -> None:
        result = safe_merge("base", "base", "theirs")
        assert result.text == "theirs"
        assert result.conflict is False
        assert result.source == "theirs"

    def test_both_identical_change(self) -> None:
        result = safe_merge("base", "changed", "changed")
        assert result.text == "changed"
        assert result.conflict is False
        assert result.source == "identical"


class TestSafeMergeOverlapping:
    def test_both_change_same_line_conflict(self) -> None:
        base = "line1\nline2\nline3\n"
        ours = "line1\nours line\nline3\n"
        theirs = "line1\ntheirs line\nline3\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is True
        assert result.source == "conflict"
        assert "<<<<<<<" in result.text
        assert "=======" in result.text
        assert ">>>>>>>" in result.text
        assert "ours line" in result.text
        assert "theirs line" in result.text


class TestSafeMergeDisjoint:
    def test_disjoint_changes_clean_merge(self) -> None:
        base = "line1\nline2\nline3\nline4\n"
        ours = "line1\nLINE2\nline3\nline4\n"
        theirs = "line1\nline2\nline3\nLINE4\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is False
        assert result.source == "merged"
        assert "LINE2" in result.text
        assert "LINE4" in result.text

    def test_ours_deletes_theirs_inserts(self) -> None:
        base = "one\ntwo\nthree\n"
        ours = "one\nthree\n"
        theirs = "one\ntwo\nINJECTED\nthree\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is False
        assert result.source == "merged"

    def test_both_add_non_overlapping_end(self) -> None:
        base = "aaa\nbbb\nccc\n"
        ours = "aaa\nBBB\nccc\n"
        theirs = "aaa\nbbb\nCCC\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is False
        assert result.source == "merged"
        assert "BBB" in result.text
        assert "CCC" in result.text


class TestSafeMergeEdgeCases:
    def test_empty_all_three(self) -> None:
        result = safe_merge("", "", "")
        assert result.text == ""
        assert result.conflict is False
        assert result.source == "base"

    def test_base_empty_ours_added(self) -> None:
        result = safe_merge("", "hello\n", "")
        assert result.text == "hello\n"
        assert result.conflict is False
        assert result.source == "ours"

    def test_base_empty_theirs_added(self) -> None:
        result = safe_merge("", "", "hello\n")
        assert result.text == "hello\n"
        assert result.conflict is False
        assert result.source == "theirs"

    def test_base_empty_both_added_same(self) -> None:
        result = safe_merge("", "x\n", "x\n")
        assert result.text == "x\n"
        assert result.conflict is False
        assert result.source == "identical"

    def test_base_empty_both_added_different(self) -> None:
        result = safe_merge("", "x\n", "y\n")
        assert result.text == ""

    def test_single_line_identical(self) -> None:
        result = safe_merge("a", "a", "a")
        assert result.text == "a"
        assert result.conflict is False
        assert result.source == "base"

    def test_single_line_ours_deleted(self) -> None:
        result = safe_merge("a\n", "", "a\n")
        assert result.text == ""
        assert result.conflict is False
        assert result.source == "ours"

    def test_ours_removes_all_content(self) -> None:
        base = "one\ntwo\nthree\n"
        ours = ""
        theirs = "one\ntwo\nthree\n"
        result = safe_merge(base, ours, theirs)
        assert result.text == ""
        assert result.conflict is False
        assert result.source == "ours"

    def test_no_trailing_newline_roundtrip(self) -> None:
        base = "line1\nline2"
        result = safe_merge(base, base, base)
        assert result.text == "line1\nline2"
        assert result.conflict is False

    def test_both_change_convergent_multiline(self) -> None:
        base = "alpha\nbeta\ngamma\n"
        ours = "alpha\nBETA\ngamma\n"
        theirs = "alpha\nBETA\ngamma\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is False
        assert result.source == "identical"
        assert result.text == "alpha\nBETA\ngamma\n"

    def test_conflict_hunk_has_markers(self) -> None:
        base = "a\nb\nc\n"
        ours = "X\nb\nc\n"
        theirs = "Y\nb\nc\n"
        result = safe_merge(base, ours, theirs)
        assert result.conflict is True
        assert "<<<<<<<" in result.text
        assert "=======" in result.text
        assert ">>>>>>>" in result.text


class TestSafeMergeFile:
    def test_clean_merge_writes_dest(self, tmp_path) -> None:
        base = tmp_path / "base.txt"
        ours = tmp_path / "ours.txt"
        theirs = tmp_path / "theirs.txt"
        dest = tmp_path / "dest.txt"

        base.write_text("line1\nline2\nline3\n")
        ours.write_text("line1\nLINE2\nline3\n")
        theirs.write_text("line1\nline2\nLINE3\n")

        result = safe_merge_file(str(base), str(ours), str(theirs), str(dest))
        assert result.conflict is False

        dest_text = dest.read_text()
        assert "LINE2" in dest_text
        assert "LINE3" in dest_text

    def test_conflict_refuses_to_write(self, tmp_path) -> None:
        base = tmp_path / "base.txt"
        ours = tmp_path / "ours.txt"
        theirs = tmp_path / "theirs.txt"
        dest = tmp_path / "dest.txt"

        base.write_text("line1\nline2\nline3\n")
        ours.write_text("line1\nOURS\nline3\n")
        theirs.write_text("line1\nTHEIRS\nline3\n")

        result = safe_merge_file(str(base), str(ours), str(theirs), str(dest))
        assert result.conflict is True
        assert not dest.exists()

    def test_only_ours_changed_file(self, tmp_path) -> None:
        base = tmp_path / "base.txt"
        ours = tmp_path / "ours.txt"
        theirs = tmp_path / "theirs.txt"
        dest = tmp_path / "dest.txt"

        content = "abc\n"
        base.write_text(content)
        ours.write_text("xyz\n")
        theirs.write_text(content)

        result = safe_merge_file(str(base), str(ours), str(theirs), str(dest))
        assert result.conflict is False
        assert result.source == "ours"
        assert dest.read_text() == "xyz\n"

    def test_input_file_missing_raises(self, tmp_path) -> None:
        base = tmp_path / "base.txt"
        ours = tmp_path / "ours.txt"
        theirs = tmp_path / "theirs.txt"
        dest = tmp_path / "dest.txt"

        base.write_text("x\n")
        ours.write_text("x\n")

        import pytest
        with pytest.raises(FileNotFoundError):
            safe_merge_file(str(base), str(ours), str(theirs), str(dest))
