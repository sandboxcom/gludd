"""Compatibility checks for the existing audit while AB060 gains file-set input."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import audit_agent_behavior as audit
import pytest


@pytest.mark.parametrize(("code", "stdout", "expected"), ((0, " a\n", "a"), (1, "a", "")))
def test_git_helper_preserves_exit_and_trim_behavior(
    code: int, stdout: str, expected: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "status"]
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(command, code, stdout, "")
    monkeypatch.setattr(subprocess, "run", run)
    assert audit._git("status") == expected


def test_git_helpers_and_time_parsing_survive_native_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("fixture unavailable")
    monkeypatch.setattr(subprocess, "run", unavailable)
    assert audit._git("status") == ""
    assert audit._git_lines("status") == []
    monkeypatch.setattr(audit, "_git", lambda *args: "a\n\nb")
    assert audit._git_lines("status") == ["a", "b"]
    assert audit._parse_iso("bad") is None
    assert audit._parse_iso("2026-10-19T00:00:00Z") == datetime(2026, 10, 19, tzinfo=UTC)
    assert audit._now().tzinfo is UTC


def test_overlap_audit_uses_real_time_and_shared_file_intersection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "_git_lines", lambda *args: [
        "first|2026-10-19T00:00:00Z", "a.py", "only.py",
        "second|2026-10-19T00:04:59Z", "a.py",
        "third|2026-10-19T00:10:00Z", "a.py",
        "bad|not-a-date", "a.py",
    ])
    result = audit.check_ab041_overlapping_edits()
    assert result["status"] == "FAIL"
    assert len(result["violations"]) == 1
    assert result["violations"][0]["overlap_files"] == ["a.py"]
    monkeypatch.setattr(audit, "_git_lines", lambda *args: [])
    assert audit.check_ab041_overlapping_edits()["status"] == "PASS"


def test_task_evidence_and_ledger_keep_missing_and_pending_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    assert audit.check_ab047_task_evidence()["status"] == "SKIP"
    assert audit.check_ab048_task_ledger_drift()["status"] == "SKIP"
    (tmp_path / "TASKS.md").write_text(
        "- [x] missing proof\n- [x] fixed abcdef1\n* [x] CI GREEN\n- [ ] open\n- [.] active\n",
    )
    result = audit.check_ab047_task_evidence()
    assert result["status"] == "FAIL"
    assert [item["line"] for item in result["violations"]] == [1]
    monkeypatch.setattr(audit, "_git", lambda *args: "2026-10-19T00:00:00Z")
    ledger = audit.check_ab048_task_ledger_drift()
    assert ledger["pending_count"] == 2
    assert ledger["last_commit"] == "2026-10-19T00:00:00"
    assert ledger["tasks_mtime"]
    monkeypatch.setattr(audit, "_git", lambda *args: "")
    assert audit.check_ab048_task_ledger_drift()["last_commit"] == "unknown"


def test_worktree_isolation_retains_shared_branch_source_and_merge_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "_git_lines", lambda *args: [
        "one|feature change", "src/owned.py", "tests/unit/test_owned.py",
        "two|Merge branch", "src/merged.py",
    ])
    result = audit.check_ab052_worktree_isolation()
    assert result["status"] == "INFO"
    assert len(result["violations"]) == 2
    assert {item["file"] for item in result["violations"]} == {"src/owned.py"}


def test_worktree_health_keeps_age_detached_and_missing_ref_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    old = tmp_path / "old"
    old.mkdir()
    detached = tmp_path / "detached"
    detached.mkdir()
    missing = tmp_path / "missing"
    now = datetime.now(UTC) + timedelta(hours=25)
    monkeypatch.setattr(audit, "_now", lambda: now)
    def lines(*args: str) -> list[str]:
        if args[0] == "worktree":
            return ["incomplete", f"{tmp_path} hash [main]", f"{old} hash [agent-old]",
                    f"{detached} hash detached", f"{missing} hash"]
        return ["unmerged"] if args[1] == "development..[agent-old]" else []
    monkeypatch.setattr(audit, "_git_lines", lines)
    result = audit.check_ab054_abandoned_worktrees()
    assert result["status"] == "FAIL"
    assert result["violations"][0]["path"] == str(old)
    assert result["violations"][0]["unmerged_commits"] == 1
    monkeypatch.setattr(audit, "_now", lambda: datetime.now(UTC))
    assert audit.check_ab054_abandoned_worktrees()["status"] == "PASS"


def test_dead_code_output_remains_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["uv", "run", "vulture", "src/"]
        assert kwargs["timeout"] == 60
        return subprocess.CompletedProcess(command, 1, " dead symbol\n\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    result = audit.check_ab056_dead_code()
    assert result["status"] == "INFO"
    assert result["dead_symbol_count"] == 1
    assert result["sample"] == ["dead symbol"]


def test_orphan_scripts_keep_private_init_and_native_target_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    assert audit.check_ab057_orphan_scripts()["status"] == "SKIP"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("__init__.py", "_private.py", "owned.py", "orphan.py"):
        (scripts / name).write_text("")
    (tmp_path / "Makefile").write_text("target: owned.py")
    result = audit.check_ab057_orphan_scripts()
    assert result["status"] == "FAIL"
    assert result["violations"] == ["orphan.py"]
    assert result["total_scripts"] == 4


@pytest.mark.parametrize(
    "result",
    ({"status": "FAIL", "violations": [{"path": "owned"}]},
     {"status": "FAIL", "violations": ["owned"]},
     {"status": "INFO", "note": "fixture", "total_worktrees": 2,
      "dead_symbol_count": 1, "pending_count": 3}),
)
def test_main_retains_diagnostic_rendering_and_failure_exit(
    result: dict[str, object], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(audit, "CHECKS", {"AB041": lambda: result})
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB041"])
    assert audit.main() == int(result["status"] == "FAIL")
    output = capsys.readouterr().out
    assert str(result["status"]) in output
    if result["status"] == "INFO":
        assert "NOTE: fixture" in output
        assert "worktrees: 2" in output
        assert "dead_symbols: 1" in output
        assert "pending_tasks: 3" in output


def test_main_list_unknown_and_exception_stay_observable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["audit", "--list"])
    assert audit.main() == 0
    assert "AB060:" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "UNKNOWN"])
    assert audit.main() == 2
    assert "Unknown check IDs" in capsys.readouterr().err
    def broken() -> dict[str, object]:
        raise RuntimeError("fixture error")
    monkeypatch.setattr(audit, "CHECKS", {"AB041": broken})
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB041", "--json"])
    audit.main()
    assert "fixture error" in capsys.readouterr().out


def test_main_context_text_and_failures_are_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text("measured")
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB060"])
    assert audit.main() == 0
    output = capsys.readouterr().out
    assert "8 bytes" in output
    assert "1 physical lines" in output
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_FILES", '["missing.md"]')
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_JSON", "0")
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB060", "--context-from-env"])
    assert audit.main() == 1
    assert "Cannot measure missing.md" in capsys.readouterr().out
