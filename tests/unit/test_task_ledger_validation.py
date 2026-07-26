from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.validate_task_ledger import ID_PATTERN, extract_tasks, main


class TestIdPattern:
    def test_matches_task_ids(self) -> None:
        assert ID_PATTERN.findall("W.1 — task description") == ["W.1"]
        assert ID_PATTERN.findall("G.5.2 — nested task") == ["G.5.2"]
        assert ID_PATTERN.findall("A.10 — single letter") == ["A.10"]
        assert ID_PATTERN.findall("H.16 — triple-digit suffix") == ["H.16"]

    def test_hyphenated_ids_supported(self) -> None:
        assert ID_PATTERN.findall("FIX-3 — hotfix id") == ["FIX-3"]

    def test_beta3_hyphenated_ids_supported(self) -> None:
        assert ID_PATTERN.findall("T-BETA3-E2E — certification") == ["T-BETA3-E2E"]

    def test_does_not_match_plain_text(self) -> None:
        assert not ID_PATTERN.findall("description with 1.2 value")


class TestExtractTasks:
    def test_parses_checked_items(self) -> None:
        content = "- [x] W.1 — Finished task | status: completed\n"
        checked, unchecked = extract_tasks_content(content)
        assert len(checked) == 1
        assert len(unchecked) == 0
        assert checked[0]["ids"] == ["W.1"]

    def test_parses_unchecked_items(self) -> None:
        content = "- [ ] A.1 — Pending task | status: pending\n"
        checked, unchecked = extract_tasks_content(content)
        assert len(checked) == 0
        assert len(unchecked) == 1
        assert unchecked[0]["ids"] == ["A.1"]

    def test_parses_beta3_unchecked_item(self) -> None:
        content = "- [ ] T-BETA3-COVERAGE — Branch coverage | status: pending\n"
        _checked, unchecked = extract_tasks_content(content)
        assert unchecked[0]["ids"] == ["T-BETA3-COVERAGE"]

    def test_mixed_items(self) -> None:
        content = (
            "- [x] W.1 — Done\n"
            "- [ ] A.1 — Todo\n"
            "- [x] W.2 — Also done\n"
            "- [ ] A.2 — Also todo\n"
        )
        checked, unchecked = extract_tasks_content(content)
        assert len(checked) == 2
        assert len(unchecked) == 2

    def test_extracts_status_field(self) -> None:
        content = "- [ ] A.3 — Push, wait for CI | status: in_progress\n"
        _, unchecked = extract_tasks_content(content)
        assert unchecked[0]["status"] == "in_progress"

    def test_extracts_epoch_timestamp(self) -> None:
        content = "- [ ] A.3 — Stale item | status: in_progress | epoch 1710000000\n"
        _, unchecked = extract_tasks_content(content)
        assert unchecked[0]["epoch"] == 1710000000

    def test_ts_timestamp_variant(self) -> None:
        content = "- [ ] A.3 — With ts field | status: in_progress | ts 1710000000\n"
        _, unchecked = extract_tasks_content(content)
        assert unchecked[0]["epoch"] == 1710000000

    def test_skips_non_task_lines(self) -> None:
        content = (
            "# Header\n"
            "Some prose\n"
            "- [x] W.1 — Task\n"
            "---\n"
        )
        checked, unchecked = extract_tasks_content(content)
        assert len(checked) == 1
        assert len(unchecked) == 0

    def test_multiple_ids_per_line_captures_all(self) -> None:
        content = "- [ ] W.1 W.2 — Two IDs\n"
        _checked, unchecked = extract_tasks_content(content)
        assert len(unchecked) == 1
        assert unchecked[0]["ids"] == ["W.1", "W.2"]


class TestValidateAgainstRealTas:
    def test_real_tas_md_is_parseable(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        tas_path = repo_root / "TASKS.md"
        assert tas_path.exists(), f"TASKS.md not found at {tas_path}"
        checked, unchecked = extract_tasks(tas_path)
        assert isinstance(checked, list)
        assert isinstance(unchecked, list)
        total = len(checked) + len(unchecked)
        assert total > 0, "Expected non-empty TASKS.md"


class TestTemporaryTasMd:
    def test_detects_unchecked_items_in_temp_file(self) -> None:
        content = (
            "# TASKS.md\n\n"
            "## Phase A\n\n"
            "- [x] W.1 — Done item | status: completed\n"
            "- [ ] A.1 — First unchecked | status: pending\n"
            "- [ ] A.2 — Second unchecked | status: pending\n"
            "- [x] W.2 — Also done | status: completed\n"
            "## Phase B\n\n"
            "- [ ] B.1 — Third unchecked | status: in_progress | epoch 1710000000\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            checked, unchecked = extract_tasks(tmp_path)
            assert len(checked) == 2
            assert len(unchecked) == 3
            unchecked_ids = {tid for t in unchecked for tid in t["ids"]}
            assert unchecked_ids == {"A.1", "A.2", "B.1"}
            checked_ids = {tid for t in checked for tid in t["ids"]}
            assert checked_ids == {"W.1", "W.2"}
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_empty_tas_md(self) -> None:
        content = "# TASKS.md\n\nNo tasks yet.\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            checked, unchecked = extract_tasks(tmp_path)
            assert len(checked) == 0
            assert len(unchecked) == 0
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_duplicate_id_detection(self) -> None:
        content = (
            "# TASKS.md\n\n"
            "- [x] W.1 — Completed task | status: completed\n"
            "- [ ] W.1 — Same ID, unchecked | status: pending\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            checked, unchecked = extract_tasks(tmp_path)
            assert len(checked) == 1
            assert len(unchecked) == 1
            assert checked[0]["ids"] == ["W.1"]
            assert unchecked[0]["ids"] == ["W.1"]
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_missing_id_detection(self) -> None:
        content = (
            "# TASKS.md\n\n"
            "- [ ] No ID here | status: pending\n"
            "- [x] W.1 — Has an ID | status: completed\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            checked, unchecked = extract_tasks(tmp_path)
            assert len(checked) == 1
            assert len(unchecked) == 1
            assert unchecked[0]["ids"] == []
            assert checked[0]["ids"] == ["W.1"]
        finally:
            tmp_path.unlink(missing_ok=True)


class TestMainExitCodes:
    def test_main_with_real_tas_md(self) -> None:
        rc = main()
        assert rc in (0, 1), f"main() returned unexpected {rc}"

    def test_main_handles_missing_file(self) -> None:
        with patch.object(Path, "exists", return_value=False):
            rc = main()
            assert rc == 1


class TestMakeTargetExists:
    def test_validate_task_ledger_target_exists(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile = repo_root / "Makefile"
        content = makefile.read_text(encoding="utf-8")
        assert "validate-task-ledger:" in content
        assert "validate_task_ledger.py" in content


def extract_tasks_content(content: str) -> tuple[list[dict], list[dict]]:
    """Helper: call extract_tasks on a string by writing it to a temp file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        return extract_tasks(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
