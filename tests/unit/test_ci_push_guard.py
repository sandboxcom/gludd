"""Unit contract for the fail-closed CI push coordination helper."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

guard = importlib.import_module("ci_push_guard")


def test_script_exists() -> None:
    assert (SCRIPTS / "ci_push_guard.py").is_file()


def test_active_statuses_cover_all_nonterminal_states() -> None:
    assert set(guard._ACTIVE_STATUSES) == {"in_progress", "queued", "waiting"}


def test_idle_branch_is_safe_to_push(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "_gh_run_list", lambda branch: [])
    assert guard.ci_busy_check("development") == 0
    assert "CI-IDLE" in capsys.readouterr().out


def test_active_branch_blocks_push(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        guard,
        "_gh_run_list",
        lambda branch: [{"databaseId": 42, "status": "in_progress"}],
    )
    assert guard.ci_busy_check("development") == 1
    output = capsys.readouterr().out
    assert "CI BUSY" in output
    assert "42" in output


def test_explicit_force_allows_push(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        guard,
        "_gh_run_list",
        lambda branch: [{"databaseId": 42, "status": "queued"}],
    )
    assert guard.ci_busy_check("development", force=True) == 0
    assert "CI-FORCE-PUSH" in capsys.readouterr().out


def test_main_requires_branch(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["ci_push_guard.py"])
    assert guard.main() == 2
    assert "usage:" in capsys.readouterr().err


def test_main_honors_namespaced_force_flag(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def _check(branch: str, force: bool = False) -> int:
        observed.update(branch=branch, force=force)
        return 0

    monkeypatch.setattr(guard, "ci_busy_check", _check)
    monkeypatch.setattr(sys, "argv", ["ci_push_guard.py", "development"])
    monkeypatch.setenv("GLUDD_FORCE_PUSH", "1")
    assert guard.main() == 0
    assert observed == {"branch": "development", "force": True}
