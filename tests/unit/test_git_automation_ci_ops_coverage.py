"""Typed state and terminal-branch coverage for CI operations."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from general_ludd.git_automation import ci_ops
from general_ludd.git_automation.ci_ops import (
    _load_cooldown_state,
    _parse_gh_run_list,
    _save_cooldown_state,
    _state_file,
    ci_cancel,
)


def test_state_file_precedence_and_project_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer injected state, then environment, then project state."""
    injected = tmp_path / "injected.json"
    monkeypatch.setattr(ci_ops, "_STATE_FILE", injected)
    assert _state_file() == injected

    configured = tmp_path / "configured" / "state.json"
    monkeypatch.setattr(ci_ops, "_STATE_FILE", None)
    monkeypatch.setenv("GLUDD_CI_STATE_FILE", str(configured))
    assert _state_file() == configured
    assert configured.parent.is_dir()

    expected = tmp_path / "project-state.json"
    monkeypatch.delenv("GLUDD_CI_STATE_FILE")
    monkeypatch.setattr(ci_ops, "project_state", lambda: SimpleNamespace(path=lambda *_parts: expected))
    assert _state_file() == expected


def test_load_and_save_cooldown_state_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default missing/corrupt state and atomically persist valid state."""
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(ci_ops, "_STATE_FILE", state_path)
    assert _load_cooldown_state()["check_count"] == 0

    state_path.write_text("not-json", encoding="utf-8")
    assert _load_cooldown_state()["last_verdict"] == ""

    expected = {"check_count": 3, "last_verdict": "green"}
    _save_cooldown_state(expected)
    assert json.loads(state_path.read_text(encoding="utf-8")) == expected


def test_unknown_run_status_and_cancel_failure_are_observable() -> None:
    """Return UNKNOWN and preserve failed cancellation diagnostics."""
    parsed = _parse_gh_run_list(
        [{"conclusion": None, "status": "mystery", "headSha": "abc", "databaseId": 7}]
    )
    assert parsed == {"verdict": "UNKNOWN", "run_id": "7", "headSha": "abc"}

    result = SimpleNamespace(returncode=1, stdout="", stderr="already completed")
    with patch("subprocess.run", return_value=result):
        cancelled = ci_cancel("7")
    assert cancelled == {"success": False, "run_id": "7", "output": "already completed"}
