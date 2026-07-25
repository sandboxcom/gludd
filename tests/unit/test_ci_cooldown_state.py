"""CP.9: verify ci-verdict-safe prints the last-known verdict alongside the cooldown message.

Pins that the cooldown state file records the last-known CI verdict and that
the cooldown message surfaces it, so the orchestrator never sees a bare
"CI-COOLDOWN" message without context when CI was already RED.

State file fields:
    last_check_epoch, last_push_epoch, last_head_sha, check_count,
    last_verdict, last_verdict_epoch

The Makefile target ``ci-verdict-safe`` chains three operations:
    1. ``ci_check_cooldown.py check``           - gate (0=proceed, 3=cooldown, 1=last-was-red)
    2. ``make ci-verdict``                       - calls ``gh run list`` (subprocess)
    3. ``ci_check_cooldown.py record-verdict V`` - persists verdict to state

These tests cover the state lifecycle: record on success path, surface on
cooldown path, UNKNOWN when no prior check, and the full expiry loop.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_check_cooldown.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("ci_cooldown_state_under_test", SCRIPT_PATH)
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


def _mock_subproc(sha: str = "deadbeefcafebabe"):
    """Mock subprocess.check_output for git rev-parse / gh run list calls.

    Returns a side_effect callable that handles both the cooldown script's
    ``git rev-parse HEAD`` invocation and (defensively) a ``gh run list``
    invocation shaped like the one ``make ci-verdict`` performs.
    """

    def _fake(cmd, *args, **kwargs):
        cmd_list = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if "rev-parse" in cmd_list:
            return sha
        if "gh" in cmd_list and "run" in cmd_list:
            return "1234567890\tsuccess\t2026-07-24T10:00:00Z\tmaster\ttitle\n"
        return ""

    return _fake


# --------------------------------------------------------------------------- #
# 1. State file records last_verdict when ci-verdict runs (not blocked)        #
# --------------------------------------------------------------------------- #
def test_records_verdict_after_unblocked_check(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cooldown is expired, check() returns 0 and record-verdict persists."""
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeefcafebabe")
    before = time.time()
    rc_check = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc_check == 0

    with patch("subprocess.check_output", side_effect=_mock_subproc()):
        rc_record = ci_check_cooldown.cmd_record_verdict("success")
    assert rc_record == 0

    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "success"
    assert state["last_verdict_epoch"] >= before
    assert state["check_count"] == 1
    assert state["last_head_sha"] == "deadbeefcafebabe"


def test_records_verdict_via_real_subprocess_mock(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_head_sha() goes through subprocess.check_output; mock it for the
    whole check+record flow so no real git invocation happens."""
    monkeypatch.delenv("GLUDD_CI_STATE_FILE", raising=False)
    monkeypatch.setattr(ci_check_cooldown, "STATE_FILE", isolated_state)
    with patch("subprocess.check_output", side_effect=_mock_subproc("feedface")):
        sha = ci_check_cooldown.get_head_sha()
        assert sha == "feedface"
        rc_check = ci_check_cooldown.cmd_check(cooldown=600, force=False)
        assert rc_check == 0
    state_after_check = json.loads(isolated_state.read_text())
    assert state_after_check["last_head_sha"] == "feedface"


# --------------------------------------------------------------------------- #
# 2. When cooldown active, output includes last-known verdict (GREEN/RED/PEND) #
# --------------------------------------------------------------------------- #
def test_cooldown_message_shows_green_last_verdict(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="success",
        last_verdict_epoch=time.time() - 30,
        check_count=4,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert rc == 3
    assert "CI-COOLDOWN" in stderr
    assert "SUCCESS" in stderr
    assert "Last known verdict" in stderr


def test_cooldown_message_shows_red_last_verdict(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    assert rc == 1
    assert "CI-COOLDOWN" in stderr
    assert "FAILURE" in stderr


def test_cooldown_message_shows_pending_last_verdict(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(
        isolated_state,
        last_check_epoch=time.time(),
        last_verdict="pending",
        last_verdict_epoch=time.time() - 60,
        check_count=7,
    )
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert rc == 3
    assert "CI-COOLDOWN" in stderr
    assert "PENDING" in stderr


# --------------------------------------------------------------------------- #
# 3. When no prior check exists, output says UNKNOWN                          #
# --------------------------------------------------------------------------- #
def test_no_prior_verdict_reports_unknown(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "deadbeef")
    _write_state(isolated_state, last_check_epoch=time.time(), check_count=1)
    rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert rc == 3
    assert "CI-COOLDOWN" in stderr
    assert "UNKNOWN" in stderr.upper()


def test_no_state_file_at_all_proceeds_with_check(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fresh boot: state file absent → last_check_epoch defaults to 0 → cooldown
    is expired (not active) → check proceeds (rc=0). No verdict is recorded yet;
    the UNKNOWN surface only applies when cooldown is ACTIVE with no prior verdict
    (covered by test_no_prior_verdict_reports_unknown)."""
    if isolated_state.exists():
        isolated_state.unlink()
    with patch("subprocess.check_output", side_effect=_mock_subproc("feedface")):
        rc = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "feedface" in out


# --------------------------------------------------------------------------- #
# 4. Cooldown expiry allows new check and records new verdict                 #
# --------------------------------------------------------------------------- #
def test_cooldown_expiry_allows_new_check_and_records_new_verdict(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")
    _write_state(
        isolated_state,
        last_check_epoch=time.time() - 660,
        last_verdict="failure",
        last_verdict_epoch=time.time() - 700,
        check_count=2,
    )
    rc_check = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    assert rc_check == 0

    rc_record = ci_check_cooldown.cmd_record_verdict("success")
    assert rc_record == 0

    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "success"
    assert state["check_count"] == 3
    assert state["last_head_sha"] == "cafef00d"


def test_full_lifecycle_record_then_cooldown_surfaces_new_verdict(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full chain: expiry → check OK → record success → next check (cooldown) shows SUCCESS."""
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")
    _write_state(
        isolated_state,
        last_check_epoch=time.time() - 660,
        last_verdict="failure",
        last_verdict_epoch=time.time() - 700,
        check_count=1,
    )
    assert ci_check_cooldown.cmd_check(cooldown=600, force=False) == 0
    capsys.readouterr()
    assert ci_check_cooldown.cmd_record_verdict("success") == 0

    rc_blocked = ci_check_cooldown.cmd_check(cooldown=600, force=False)
    stderr = capsys.readouterr().err
    assert rc_blocked == 3
    assert "SUCCESS" in stderr
    assert "FAILURE" not in stderr
    state = json.loads(isolated_state.read_text())
    assert state["check_count"] == 2
    assert state["last_verdict"] == "success"


def test_record_verdict_normalizes_case(
    isolated_state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ci_check_cooldown.cmd_record_verdict("SUCCESS") == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "success"
    assert ci_check_cooldown.cmd_record_verdict("  Failure  ") == 0
    state = json.loads(isolated_state.read_text())
    assert state["last_verdict"] == "failure"
