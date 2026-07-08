"""Pin the behavior of scripts/ci_check_cooldown.py.

The cooldown prevents the CI-poll anti-pattern: an agent dispatches a
"wait for CI green" subagent that loops ``make ci-verdict`` every 60-90s
for 30-40 minutes, holding a subagent slot while producing zero value.
CI runs on its own schedule; polling it does not speed it up.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_check_cooldown.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_check_cooldown_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"failed to load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_check_cooldown = _load_module()


def _write_state(path: Path, **fields: object) -> None:
    base: dict[str, object] = {
        "last_check_epoch": 0.0,
        "last_push_epoch": 0.0,
        "last_head_sha": "",
        "check_count": 0,
    }
    base.update(fields)
    path.write_text(json.dumps(base))


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ci_check_cooldown at a per-test state file under tmp_path."""
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("GLUDD_CI_STATE_FILE", str(state_file))
    monkeypatch.setattr(ci_check_cooldown, "STATE_FILE", state_file)
    return state_file


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file(), f"scripts/ci_check_cooldown.py must exist at {SCRIPT_PATH}"


def test_check_refused_within_cooldown(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time())
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 3


def test_check_allowed_after_cooldown(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time() - 660)
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 0


def test_force_bypasses_cooldown(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time())
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=True)
    assert rc == 0


def test_deploy_records_push_timestamp(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")
    before = time.time()
    rc = ci_check_cooldown.cmd_deploy()
    assert rc == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_push_epoch"] >= before
    assert state["last_head_sha"] == "cafef00d"


def test_status_shows_remaining_cooldown(isolated_state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_state(isolated_state, last_check_epoch=time.time())
    rc = ci_check_cooldown.cmd_status(cooldown=600)
    assert rc == 0
    captured = capsys.readouterr()
    assert "COOLDOWN-ACTIVE" in captured.out


def test_state_file_round_trips(isolated_state: Path) -> None:
    payload = {
        "last_check_epoch": 1234.5,
        "last_push_epoch": 5678.9,
        "last_head_sha": "abc123",
        "check_count": 7,
    }
    ci_check_cooldown.save_state(payload)
    loaded = ci_check_cooldown.load_state()
    assert loaded == payload


def test_check_count_increments(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=0.0, check_count=0)
    ci_check_cooldown.cmd_check(cooldown=600, force=True)
    after_first = json.loads(isolated_state.read_text())
    assert after_first["check_count"] == 1
    ci_check_cooldown.cmd_check(cooldown=600, force=True)
    after_second = json.loads(isolated_state.read_text())
    assert after_second["check_count"] == 2
