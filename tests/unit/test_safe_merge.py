"""Tests for the overlap-aware 3-way merge primitive.

The danger this guards against: gludd integrates a self-improve/agent worktree
back into a base repo. If it does that with a WHOLE-FILE copy, then when BOTH
the base and the worktree changed the same file, the copy silently REVERTS the
base's change — real data loss. ``safe_merge`` must NEVER silently pick one side
when both diverged; it either produces a clean 3-way line merge containing both
edits or returns a conflict-flagged result with markers.
"""

from __future__ import annotations

import pytest

from general_ludd.integration.safe_merge import (
    MergeResult,
    detect_overlap,
    safe_merge,
    safe_merge_file,
)

BASE = "line1\nline2\nline3\nline4\nline5\n"


class TestDetectOverlap:
    def test_no_change_is_not_overlap(self) -> None:
        assert detect_overlap(BASE, BASE, BASE) is False

    def test_only_ours_changed_is_not_overlap(self) -> None:
        ours = BASE.replace("line1", "OURS")
        assert detect_overlap(BASE, ours, BASE) is False

    def test_only_theirs_changed_is_not_overlap(self) -> None:
        theirs = BASE.replace("line5", "THEIRS")
        assert detect_overlap(BASE, BASE, theirs) is False

    def test_both_changed_identically_is_not_overlap(self) -> None:
        # Both sides made the SAME edit — convergent, nothing to clobber.
        edit = BASE.replace("line3", "SAME")
        assert detect_overlap(BASE, edit, edit) is False

    def test_both_changed_differently_is_overlap(self) -> None:
        ours = BASE.replace("line1", "OURS")
        theirs = BASE.replace("line5", "THEIRS")
        # THE dangerous case: a blind copy of either side reverts the other.
        assert detect_overlap(BASE, ours, theirs) is True


class TestSafeMerge:
    def test_only_ours_changed_takes_ours(self) -> None:
        ours = BASE.replace("line1", "OURS")
        result = safe_merge(BASE, ours, BASE)
        assert isinstance(result, MergeResult)
        assert result.conflict is False
        assert result.text == ours
        assert result.source == "ours"

    def test_only_theirs_changed_takes_theirs(self) -> None:
        theirs = BASE.replace("line5", "THEIRS")
        result = safe_merge(BASE, BASE, theirs)
        assert result.conflict is False
        assert result.text == theirs
        assert result.source == "theirs"

    def test_both_changed_same_takes_that(self) -> None:
        edit = BASE.replace("line3", "SAME")
        result = safe_merge(BASE, edit, edit)
        assert result.conflict is False
        assert result.text == edit
        assert result.source == "identical"

    def test_no_change_at_all_takes_base(self) -> None:
        result = safe_merge(BASE, BASE, BASE)
        assert result.conflict is False
        assert result.text == BASE

    def test_both_different_non_overlapping_lines_clean_merge(self) -> None:
        # THE anti-clobber case: ours edits the TOP, theirs edits the BOTTOM.
        # A blind whole-file copy of either side would LOSE the other edit.
        # A correct 3-way merge keeps BOTH.
        ours = BASE.replace("line1", "OURS_TOP")
        theirs = BASE.replace("line5", "THEIRS_BOTTOM")

        result = safe_merge(BASE, ours, theirs)

        assert result.conflict is False, result.text
        assert result.source == "merged"
        # Both independent edits survive.
        assert "OURS_TOP" in result.text
        assert "THEIRS_BOTTOM" in result.text
        # The untouched middle is preserved exactly once.
        assert "line2" in result.text
        assert "line3" in result.text
        assert "line4" in result.text
        # No conflict markers in a clean merge.
        assert "<<<<<<<" not in result.text
        assert ">>>>>>>" not in result.text

    def test_both_changed_same_line_conflicts_no_silent_pick(self) -> None:
        # Both sides rewrite the SAME line to DIFFERENT values. There is no safe
        # automatic resolution — must flag a conflict, never silently choose.
        ours = BASE.replace("line3", "OURS_WINS")
        theirs = BASE.replace("line3", "THEIRS_WINS")

        result = safe_merge(BASE, ours, theirs)

        assert result.conflict is True
        # Neither side may be silently adopted as the whole answer.
        assert result.text != ours
        assert result.text != theirs
        # Both competing edits are surfaced inside conflict markers.
        assert "OURS_WINS" in result.text
        assert "THEIRS_WINS" in result.text
        assert "<<<<<<<" in result.text
        assert "=======" in result.text
        assert ">>>>>>>" in result.text
        assert result.source == "conflict"

    def test_pure_and_deterministic(self) -> None:
        ours = BASE.replace("line1", "OURS_TOP")
        theirs = BASE.replace("line5", "THEIRS_BOTTOM")
        first = safe_merge(BASE, ours, theirs)
        second = safe_merge(BASE, ours, theirs)
        assert first == second
        # Inputs are not mutated (they are immutable strings, but assert anyway).
        assert ours == BASE.replace("line1", "OURS_TOP")

    def test_empty_base_both_add_same(self) -> None:
        added = "new line\n"
        result = safe_merge("", added, added)
        assert result.conflict is False
        assert result.text == added


class TestSafeMergeFile:
    def _write(self, path: object, text: str) -> str:
        p = str(path)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_clean_merge_writes_dest(self, tmp_path: object) -> None:
        base = self._write(tmp_path / "base.txt", BASE)  # type: ignore[operator]
        ours = self._write(
            tmp_path / "ours.txt",  # type: ignore[operator]
            BASE.replace("line1", "OURS_TOP"),
        )
        theirs = self._write(
            tmp_path / "theirs.txt",  # type: ignore[operator]
            BASE.replace("line5", "THEIRS_BOTTOM"),
        )
        dest = str(tmp_path / "dest.txt")  # type: ignore[operator]

        result = safe_merge_file(base, ours, theirs, dest)

        assert result.conflict is False
        with open(dest, encoding="utf-8") as fh:
            written = fh.read()
        assert "OURS_TOP" in written
        assert "THEIRS_BOTTOM" in written

    def test_conflict_refuses_to_write(self, tmp_path: object) -> None:
        base = self._write(tmp_path / "base.txt", BASE)  # type: ignore[operator]
        ours = self._write(
            tmp_path / "ours.txt",  # type: ignore[operator]
            BASE.replace("line3", "OURS_WINS"),
        )
        theirs = self._write(
            tmp_path / "theirs.txt",  # type: ignore[operator]
            BASE.replace("line3", "THEIRS_WINS"),
        )
        dest = str(tmp_path / "dest.txt")  # type: ignore[operator]

        result = safe_merge_file(base, ours, theirs, dest)

        assert result.conflict is True
        # The destination must NOT exist — a conflict must never be written out
        # as if it were a resolved file (that would re-introduce the clobber).
        import os

        assert not os.path.exists(dest)

    def test_only_one_side_changed_writes_that(self, tmp_path: object) -> None:
        base = self._write(tmp_path / "base.txt", BASE)  # type: ignore[operator]
        ours = self._write(
            tmp_path / "ours.txt",  # type: ignore[operator]
            BASE.replace("line1", "OURS"),
        )
        theirs = self._write(tmp_path / "theirs.txt", BASE)  # type: ignore[operator]
        dest = str(tmp_path / "dest.txt")  # type: ignore[operator]

        result = safe_merge_file(base, ours, theirs, dest)

        assert result.conflict is False
        with open(dest, encoding="utf-8") as fh:
            assert "OURS" in fh.read()

    def test_missing_base_raises(self, tmp_path: object) -> None:
        ours = self._write(tmp_path / "ours.txt", BASE)  # type: ignore[operator]
        theirs = self._write(tmp_path / "theirs.txt", BASE)  # type: ignore[operator]
        dest = str(tmp_path / "dest.txt")  # type: ignore[operator]
        with pytest.raises((FileNotFoundError, OSError)):
            safe_merge_file(
                str(tmp_path / "nope.txt"),  # type: ignore[operator]
                ours,
                theirs,
                dest,
            )
