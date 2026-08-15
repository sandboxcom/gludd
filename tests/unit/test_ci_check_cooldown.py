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
    """Point ci_check_cooldown at per-test state files under tmp_path.

    Isolates ALL THREE state files (check-state, verdict-history, and the
    AA023 restart counter) — without this, terminal-verdict tests reset the
    real /tmp/gludd-ci-restart-count and pollute the real verdict-history
    file, corrupting the live push-guard state (2026-08-15 incident)."""
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"
    restart_file = tmp_path / "restart-count"
    monkeypatch.setenv("GLUDD_CI_STATE_FILE", str(state_file))
    monkeypatch.setattr(ci_check_cooldown, "STATE_FILE", state_file)
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    return state_file


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file(), f"scripts/ci_check_cooldown.py must exist at {SCRIPT_PATH}"


def test_check_refused_within_cooldown(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time())
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 3


def test_check_allowed_after_cooldown(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time() - 660)
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 0


def test_force_bypasses_cooldown(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time())
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=True)
    assert rc == 0


def test_deploy_records_push_timestamp(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_check_count_increments(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=0.0, check_count=0)
    ci_check_cooldown.cmd_check(cooldown=600, force=True)
    after_first = json.loads(isolated_state.read_text())
    assert after_first["check_count"] == 1
    ci_check_cooldown.cmd_check(cooldown=600, force=True)
    after_second = json.loads(isolated_state.read_text())
    assert after_second["check_count"] == 2


def test_blocked_check_reports_last_verdict(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="success",
        last_verdict_epoch=time.time() - 30,
        check_count=3,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert "CI-COOLDOWN" in stderr
    assert "SUCCESS" in stderr
    assert rc == 3


def test_blocked_check_with_red_verdict_returns_1(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="failure",
        last_verdict_epoch=time.time() - 45,
        check_count=2,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert "CI-COOLDOWN" in stderr
    assert "FAILURE" in stderr
    assert rc == 1


def test_blocked_check_no_prior_verdict_returns_3(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time(), check_count=1)
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert "CI-COOLDOWN" in stderr
    assert "no prior check" in stderr or "unknown" in stderr.lower()
    assert rc == 3


def test_record_verdict_stores_and_can_be_read(isolated_state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = ci_check_cooldown.cmd_record_verdict("success")
    assert rc == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "success"
    assert state["last_verdict_epoch"] > 0
    rc = ci_check_cooldown.cmd_record_verdict("FAILURE")
    assert rc == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "failure"
    rc = ci_check_cooldown.cmd_record_verdict("pending")
    assert rc == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "pending"


def test_record_verdict_with_sha_updates_history_file_preserving_push_sha(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    history_file.write_text(json.dumps({"last_push_sha": "pushsha111", "last_checked_sha": "", "ts": 123}))
    rc = ci_check_cooldown.cmd_record_verdict("success", "checksha222")
    assert rc == 0
    history = json.loads(history_file.read_text())
    assert history["last_checked_sha"] == "checksha222"
    assert history["last_push_sha"] == "pushsha111"


def test_record_verdict_with_sha_creates_history_file_when_missing(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history-missing.json"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    rc = ci_check_cooldown.cmd_record_verdict("failure", "deadbeef")
    assert rc == 0
    history = json.loads(history_file.read_text())
    assert history["last_checked_sha"] == "deadbeef"
    assert history["last_verdict"] == "failure"


def test_record_verdict_with_sha_handles_corrupt_history_file(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history-corrupt.json"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    history_file.write_text("{not valid json")
    rc = ci_check_cooldown.cmd_record_verdict("pending", "abcd1234")
    assert rc == 0
    history = json.loads(history_file.read_text())
    assert history["last_checked_sha"] == "abcd1234"
    assert history["last_verdict"] == "pending"


def test_record_verdict_without_sha_does_not_create_history_file(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history-untouched.json"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    rc = ci_check_cooldown.cmd_record_verdict("success")
    assert rc == 0
    assert not history_file.exists()


def test_terminal_verdict_resets_restart_cap(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restart_file = tmp_path / "restart-count"
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    restart_file.write_text("3")
    assert ci_check_cooldown.cmd_record_verdict("failure", "deadbeef") == 0
    assert restart_file.read_text() == "0"


def test_pending_verdict_keeps_restart_cap(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restart_file = tmp_path / "restart-count-pending"
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    restart_file.write_text("3")
    assert ci_check_cooldown.cmd_record_verdict("pending", "deadbeef") == 0
    assert restart_file.read_text() == "3"


def test_terminal_verdict_reset_swallows_oserror(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    restart_file = tmp_path / "restart-count-oserror"
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    restart_file.mkdir()
    assert ci_check_cooldown.cmd_record_verdict("success", "deadbeef") == 0


def test_status_shows_last_verdict(isolated_state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_state(
        isolated_state,
        last_check_epoch=time.time() - 660,
        last_verdict="success",
        last_verdict_epoch=time.time() - 120,
        check_count=5,
    )
    rc = ci_check_cooldown.cmd_status(cooldown=600)
    assert rc == 0
    out = capsys.readouterr().out
    assert "SUCCESS" in out
    assert "last verdict" in out.lower()


def test_blocked_check_with_cancelled_returns_1(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="cancelled",
        last_verdict_epoch=time.time() - 60,
        check_count=1,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 1


def test_blocked_check_with_pending_returns_3(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="pending",
        last_verdict_epoch=time.time() - 90,
        check_count=4,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert "CI-COOLDOWN" in stderr
    assert "PENDING" in stderr
    assert rc == 3
