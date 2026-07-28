from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_push_guard.py"


@pytest.fixture
def guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_push_guard_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists() -> None:
    assert SCRIPT.is_file()


def test_current_guard_api_exists(guard: ModuleType) -> None:
    assert callable(guard._gh_run_list)
    assert callable(guard.ci_busy_check)
    assert callable(guard.main)


def test_active_statuses_cover_queued_and_running(guard: ModuleType) -> None:
    assert {"queued", "waiting", "in_progress"} <= set(guard._ACTIVE_STATUSES)


def test_idle_ci_allows_push(
    guard: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(guard, "_gh_run_list", lambda _branch: [])
    assert guard.ci_busy_check("development") == 0
    assert "CI-IDLE" in capsys.readouterr().out


def test_busy_ci_blocks_push(
    guard: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        guard,
        "_gh_run_list",
        lambda _branch: [{"databaseId": 42, "status": "in_progress"}],
    )
    assert guard.ci_busy_check("development") == 1
    assert "CI BUSY" in capsys.readouterr().out


def test_explicit_force_allows_busy_push(
    guard: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        guard,
        "_gh_run_list",
        lambda _branch: [{"databaseId": 42, "status": "queued"}],
    )
    assert guard.ci_busy_check("development", force=True) == 0
    assert "CI-BUSY-FORCED" in capsys.readouterr().out
