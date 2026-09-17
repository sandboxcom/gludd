"""Pin the behavior of scripts/ci_check_cooldown.py.

The cooldown prevents the CI-poll anti-pattern: an agent dispatches a
"wait for CI green" subagent that loops ``make ci-verdict`` every 60-90s
for 30-40 minutes, holding a subagent slot while producing zero value.
CI runs on its own schedule; polling it does not speed it up.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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


def test_record_push_increments_restart_and_arms_verdict_history(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.json"
    restart_file = tmp_path / "restart-count"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")
    restart_file.write_text("1")

    assert ci_check_cooldown.cmd_record_push() == 0

    assert restart_file.read_text() == "2"
    history = json.loads(history_file.read_text())
    assert history["last_push_sha"] == "cafef00d"
    assert history["last_checked_sha"] == ""
    assert isinstance(history["ts"], int)


def test_record_push_preserves_history_metadata_and_audits_forced_overage(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.json"
    restart_file = tmp_path / "restart-count"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "new-head")
    history_file.write_text(json.dumps({"operator_note": "retain", "last_checked_sha": "old"}))
    restart_file.write_text("3")

    assert ci_check_cooldown.cmd_record_push() == 0

    assert restart_file.read_text() == "4"
    history = json.loads(history_file.read_text())
    assert history["operator_note"] == "retain"
    assert history["last_push_sha"] == "new-head"
    assert history["last_checked_sha"] == ""


def test_record_push_fails_closed_on_invalid_restart_state(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    restart_file = tmp_path / "restart-count"
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")
    restart_file.write_text("not-an-integer")

    assert ci_check_cooldown.cmd_record_push() == 1
    assert "invalid restart count" in capsys.readouterr().err.lower()


def test_record_push_missing_counter_starts_at_one(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.json"
    restart_file = tmp_path / "missing-restart-count"
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)
    monkeypatch.setattr(ci_check_cooldown, "RESTART_COUNT_FILE", restart_file)
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "first-head")

    assert ci_check_cooldown.cmd_record_push() == 0
    assert restart_file.read_text() == "1"


def test_record_push_rejects_missing_head(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "")
    assert ci_check_cooldown.cmd_record_push() == 1
    assert "resolve HEAD" in capsys.readouterr().err


def test_record_push_surfaces_state_write_failure(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", lambda: "cafef00d")

    def fail_write(_history: dict[str, object]) -> None:
        raise OSError("read-only state")

    monkeypatch.setattr(ci_check_cooldown, "save_history_atomic", fail_write)
    assert ci_check_cooldown.cmd_record_push() == 1
    assert "state write failed" in capsys.readouterr().err.lower()


def test_non_mapping_json_state_and_history_fail_to_empty_defaults(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_state.write_text("[]")
    history_file = tmp_path / "history.json"
    history_file.write_text("[]")
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)

    assert ci_check_cooldown.load_state()["check_count"] == 0
    assert ci_check_cooldown.load_history() == {}


def test_make_restart_guard_is_check_only_with_isolated_state(tmp_path: Path) -> None:
    restart_file = tmp_path / "restart-count"
    push_state_file = tmp_path / "push-state.json"
    restart_file.write_text("2")
    env = os.environ.copy()
    env.update(
        {
            "GLUDD_CI_RESTART_COUNT_FILE": str(restart_file),
            "GLUDD_PUSH_STATE_FILE": str(push_state_file),
        }
    )

    result = subprocess.run(
        ["make", "_ci-restart-cap"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert restart_file.read_text() == "2"
    assert not push_state_file.exists()


def test_make_restart_guard_blocks_at_limit_without_increment(tmp_path: Path) -> None:
    restart_file = tmp_path / "restart-count"
    push_state_file = tmp_path / "push-state.json"
    restart_file.write_text("3")
    env = os.environ.copy()
    env.update(
        {
            "GLUDD_CI_RESTART_COUNT_FILE": str(restart_file),
            "GLUDD_PUSH_STATE_FILE": str(push_state_file),
        }
    )

    result = subprocess.run(
        ["make", "_ci-restart-cap"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert restart_file.read_text() == "3"
    assert json.loads(push_state_file.read_text())["block_reason"] == "_ci-restart-cap:limit"


def test_record_restart_block_writes_namespaced_atomic_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    push_state_file = tmp_path / "push-state.json"
    monkeypatch.setattr(ci_check_cooldown, "PUSH_STATE_FILE", push_state_file)

    assert ci_check_cooldown.cmd_record_restart_block(3) == 0

    event = json.loads(push_state_file.read_text())
    assert event["last_push_blocked"] is True
    assert event["block_reason"] == "_ci-restart-cap:limit"
    assert event["restart_count"] == 3
    assert event["max_allowed"] == 3


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


@pytest.mark.parametrize(
    ("argv", "handler", "expected_args"),
    [
        (["check", "42"], "cmd_check", (42, False)),
        (["deploy"], "cmd_deploy", ()),
        (["status", "43"], "cmd_status", (43,)),
        (["record-verdict"], "cmd_record_verdict", ("unknown", None)),
        (["record-verdict", "failure", "abc123"], "cmd_record_verdict", ("failure", "abc123")),
        (["record-push"], "cmd_record_push", ()),
        (["record-restart-block", "3"], "cmd_record_restart_block", (3,)),
    ],
)
def test_main_dispatches_each_command(
    argv: list[str], handler: str, expected_args: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def invoke(*args: object) -> int:
        calls.append(args)
        return 17

    monkeypatch.delenv("FORCE", raising=False)
    monkeypatch.setattr(ci_check_cooldown.sys, "argv", ["ci_check_cooldown.py", *argv])
    monkeypatch.setattr(ci_check_cooldown, handler, invoke)
    assert ci_check_cooldown.main() == 17
    assert calls == [expected_args]


def test_main_rejects_missing_and_unknown_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ci_check_cooldown.sys, "argv", ["ci_check_cooldown.py"])
    assert ci_check_cooldown.main() == 2
    assert "usage:" in capsys.readouterr().err

    monkeypatch.setattr(ci_check_cooldown.sys, "argv", ["ci_check_cooldown.py", "surprise"])
    assert ci_check_cooldown.main() == 2
    assert "unknown command" in capsys.readouterr().err
