from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_readiness as rr  # type: ignore[import-not-found]  # noqa: E402


def _completed(argv, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def test_exit_codes_are_stable_and_prioritized() -> None:
    assert rr._exit_code(rr.Readiness()) == rr.EXIT_ERROR
    assert rr._exit_code(rr.Readiness(errors=["CI evidence is not a successful run"])) == rr.EXIT_CI
    assert rr._exit_code(rr.Readiness(errors=["worktree has 1 dirty path(s)"])) == rr.EXIT_DIRTY
    assert rr._exit_code(
        rr.Readiness(errors=["detached or unintegrated sibling worktree/branch exists"])
    ) == rr.EXIT_WORKTREE
    assert rr._exit_code(rr.Readiness(errors=["project version files are inconsistent"])) == rr.EXIT_VERSION
    assert rr._exit_code(rr.Readiness(errors=["TASKS.md ledger validation failed"])) == rr.EXIT_TASKS


def test_main_emits_machine_readable_diagnostics(monkeypatch, capsys) -> None:
    blocked = rr.Readiness(head="abc123", errors=["CI evidence is not a successful run"])
    monkeypatch.setattr(rr, "assess", lambda **_: blocked)

    assert rr.main([]) == rr.EXIT_CI
    payload = json.loads(capsys.readouterr().out)
    assert payload["head"] == "abc123"
    assert payload["ready"] is False
    assert payload["exit_code"] == rr.EXIT_CI


def test_detached_worktree_detection_reuses_porcelain_parser(tmp_path: Path) -> None:
    porcelain = (
        f"worktree {tmp_path}\nHEAD current\nbranch refs/heads/development\n\n"
        f"worktree {tmp_path / 'detached'}\nHEAD detached\ndetached\n"
    )

    def run(argv, cwd=None):
        args = list(argv)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, porcelain)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, str(tmp_path) + "\n")
        raise AssertionError(args)

    detached = rr._detached_worktrees(run, tmp_path)
    assert detached == [{"path": str(tmp_path / "detached"), "head": "detached"}]


def test_assess_passes_when_all_release_evidence_is_present(monkeypatch, tmp_path: Path) -> None:
    import workflow_state_guard  # type: ignore[import-not-found]

    state = SimpleNamespace(
        branch="development",
        head="abc123",
        dirty_count=0,
        unintegrated_worktrees=[],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK: 0.1.0-beta.3"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))
    assert result.ready
    assert result.head == "abc123"


def test_assess_fails_closed_for_dirty_and_unintegrated_state(monkeypatch, tmp_path: Path) -> None:
    import workflow_state_guard

    state = SimpleNamespace(
        branch="development",
        head="abc123",
        dirty_count=2,
        unintegrated_worktrees=[{"path": "/tmp/other", "reasons": ["dirty"]}],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))
    assert not result.ready
    assert any("dirty" in error for error in result.errors)
    assert any("unintegrated" in error for error in result.errors)


def test_incomplete_tasks_uses_beta3_prefix(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        "## Current Session\n"
        "- [ ] T-BETA3-RELEASE — cut beta.3\n"
        "- [x] T-BETA3-E2E — certified\n"
        "- [ ] OTHER-1 — unrelated\n",
        encoding="utf-8",
    )
    assert rr._incomplete_tasks(tmp_path) == ["T-BETA3-RELEASE"]
