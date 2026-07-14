from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.auto_update_task_ledger import (
    auto_update,
    build_new_line,
    commit_references_id,
)


class TestBuildNewLine:
    def test_replaces_unchecked_with_checked_and_sha(self) -> None:
        result = build_new_line(
            "- [ ] A.1 — Fix CI | priority: high | status: pending",
            "abc12345",
        )
        assert result.startswith("- [x] A.1")
        assert "abc12345" in result
        assert "status: completed" in result

    def test_preserves_existing_fields(self) -> None:
        result = build_new_line(
            "- [ ] D.9 — Remediation tick | priority: high | effort: medium | status: in_progress",
            "deadbeef",
        )
        assert result.startswith("- [x] D.9")
        assert "priority: high" in result
        assert "effort: medium" in result
        assert "status: completed" in result
        assert "deadbeef" in result

    def test_adds_evidence_when_none_present(self) -> None:
        result = build_new_line(
            "- [ ] W.1 — Simple task",
            "cafebabe",
        )
        assert result.startswith("- [x] W.1")
        assert "cafebabe" in result


class TestCommitReferencesId:
    def test_finds_id_in_message(self) -> None:
        commits = [
            ("abc12345", "chore: H.17 signing verification fix"),
            ("def67890", "Wave 28: stuff"),
        ]
        result = commit_references_id(commits, "H.17")
        assert result is not None
        assert result[0] == "abc12345"

    def test_returns_none_for_no_match(self) -> None:
        commits = [
            ("abc12345", "chore: H.17 fix"),
        ]
        result = commit_references_id(commits, "W.99")
        assert result is None

    def test_matches_suffix_number(self) -> None:
        commits = [
            ("abc12345", "Wave 28: enforce-make subagent bash enforcement fix, D.9 remediation tick test fix"),
        ]
        result = commit_references_id(commits, "D.9")
        assert result is not None
        assert result[0] == "abc12345"


class TestAutoUpdate:
    def test_marks_matching_items_complete(self) -> None:
        content = (
            "- [x] W.1 — Already done | status: completed | evidence: oldhash\n"
            "- [ ] A.1 — Fix CI | status: pending\n"
            "- [ ] D.9 — Remediation | status: in_progress\n"
            "- [ ] C.99 — No match exists | status: pending\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[
                    ("abc12345", "chore: A.1 CI fix"),
                    ("def67890", "Wave 28: D.9 remediation tick test fix"),
                ],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert "- [x] A.1" in updated
            assert "- [x] D.9" in updated
            assert "- [ ] C.99" in updated
            assert "abc12345" in updated
            assert "def67890" in updated
            assert "- [x] W.1" in updated
            assert "oldhash" in updated
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_dry_run_does_not_modify(self) -> None:
        content = "- [ ] A.1 — Fix CI | status: pending\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[("abc12345", "chore: A.1 CI fix")],
            ):
                rc = auto_update(tmp_path, dry_run=True)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert updated == content
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_no_changes_when_nothing_matches(self) -> None:
        content = "- [ ] C.99 — No commit references this\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[("abc12345", "chore: W.1 done")],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert updated == content
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_skips_lines_without_ids(self) -> None:
        content = (
            "- [ ] No ID here at all\n"
            "- [x] W.1 — Done | status: completed\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[("abc12345", "stuff")],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert "- [ ] No ID here at all" in updated
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_empty_tas_md_is_noop(self) -> None:
        content = "# TASKS.md\n\nNo tasks yet.\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[("abc12345", "stuff")],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_handles_empty_commits(self) -> None:
        content = "- [ ] A.1 — Task\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert updated == content
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_multiple_ids_in_one_line(self) -> None:
        content = "- [ ] H.8 H.23 — Two IDs in one line | status: pending\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[
                    ("abc12345", "chore: H.23 credential leak fix"),
                ],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert updated.startswith("- [x]")
            assert "abc12345" in updated
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_idempotent_already_checked_items_unchanged(self) -> None:
        content = "- [x] W.1 — Done | status: completed | evidence: oldhash\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            with patch(
                "scripts.auto_update_task_ledger.parse_git_log",
                return_value=[("newhash1", "W.1 even newer work")],
            ):
                rc = auto_update(tmp_path, dry_run=False)
                assert rc == 0

            updated = tmp_path.read_text(encoding="utf-8")
            assert "oldhash" in updated
            assert "newhash1" not in updated
        finally:
            tmp_path.unlink(missing_ok=True)


class TestMakeTargetExists:
    def test_auto_update_ledger_target_in_makefile(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile = repo_root / "Makefile"
        content = makefile.read_text(encoding="utf-8")
        assert "auto-update-ledger:" in content
        assert "auto_update_task_ledger.py" in content
