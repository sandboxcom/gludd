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
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_check_cooldown.py"


def _load_module() -> ModuleType:
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


@pytest.mark.parametrize("sha", ("a" * 40, "AB" * 20))
def test_record_push_uses_explicit_full_sha_without_reading_new_head(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, sha: str,
) -> None:
    def unexpected_head() -> str:
        raise AssertionError("a pinned publication must not re-read a later HEAD")

    monkeypatch.setattr(ci_check_cooldown, "get_head_sha", unexpected_head)
    assert ci_check_cooldown.cmd_record_push(sha) == 0
    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    assert history["last_push_sha"] == sha.lower()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "1"


@pytest.mark.parametrize("sha", ("", "abc", "a" * 39, "a" * 41, "g" * 40, " a" + "b" * 38))
def test_record_push_rejects_malformed_explicit_sha_before_state_write(
    isolated_state: Path, sha: str, capsys: pytest.CaptureFixture[str],
) -> None:
    assert ci_check_cooldown.cmd_record_push(sha) == 2
    assert "explicit SHA must be full40hex" in capsys.readouterr().err
    assert not ci_check_cooldown.HISTORY_FILE.exists()
    assert not ci_check_cooldown.RESTART_COUNT_FILE.exists()


def test_record_push_cli_forwards_saved_sha_and_rejects_extra_arguments(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sha = "b" * 40
    monkeypatch.setattr(ci_check_cooldown.sys, "argv", ["ci_check_cooldown.py", "record-push", sha])
    assert ci_check_cooldown.main() == 0
    assert json.loads(ci_check_cooldown.HISTORY_FILE.read_text())["last_push_sha"] == sha
    history = ci_check_cooldown.HISTORY_FILE.read_bytes()
    monkeypatch.setattr(ci_check_cooldown.sys, "argv", ["ci_check_cooldown.py", "record-push", sha, "extra"])
    assert ci_check_cooldown.main() == 2
    assert "at most one" in capsys.readouterr().err
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == history
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "1"


def test_non_mapping_json_state_and_history_fail_to_empty_defaults(
    isolated_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_state.write_text("[]")
    history_file = tmp_path / "history.json"
    history_file.write_text("[]")
    monkeypatch.setattr(ci_check_cooldown, "HISTORY_FILE", history_file)

    assert ci_check_cooldown.load_state()["check_count"] == 0
    assert ci_check_cooldown.load_history() == {}


def _restart_guard_command(
    path: Path, make_flags: tuple[str, ...], restart_file: Path, push_state_file: Path,
) -> list[str]:
    """Own only fixture state values while preserving the actual Make entrypoint."""
    bindings = path / "restart-guard-fixture-bindings.mk"
    bindings.write_text(
        f"override GLUDD_CI_RESTART_COUNT_FILE := {restart_file}\n"
        f"override GLUDD_PUSH_STATE_FILE := {push_state_file}\n"
        "override FORCE :=\n"
        "export GLUDD_CI_RESTART_COUNT_FILE GLUDD_PUSH_STATE_FILE FORCE\n"
    )
    return [
        "make", *make_flags, "-f", "Makefile", "-f", str(bindings), "_ci-restart-cap",
        f"GLUDD_CI_RESTART_COUNT_FILE={restart_file}",
        f"GLUDD_PUSH_STATE_FILE={push_state_file}", "FORCE=",
    ]


@pytest.mark.parametrize("make_flags", [(), ("-e",)], ids=["ordinary", "early-environment"])
def test_make_restart_guard_is_check_only_with_isolated_state(
    tmp_path: Path, make_flags: tuple[str, ...],
) -> None:
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
        _restart_guard_command(tmp_path, make_flags, restart_file, push_state_file),
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


@pytest.mark.parametrize("make_flags", [(), ("-e",)], ids=["ordinary", "early-environment"])
def test_make_restart_guard_blocks_at_limit_without_increment(
    tmp_path: Path, make_flags: tuple[str, ...],
) -> None:
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
        _restart_guard_command(tmp_path, make_flags, restart_file, push_state_file),
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


def _diagnostic_arguments() -> dict[str, object]:
    sha = "b" * 40
    branch = "feature/owned-feedback"
    return {
        "repo": "sandboxcom/gludd", "branch": branch, "sha": sha,
        "workflow": "Build and Release", "event": "workflow_dispatch",
        "run": {
            "databaseId": 42, "headSha": sha, "headBranch": branch,
            "workflowName": "Build and Release", "event": "workflow_dispatch",
            "status": "completed", "conclusion": "success",
        },
    }


@pytest.mark.parametrize("conclusion", [
    "success", "failure", "action_required", "cancelled", "neutral",
    "skipped", "stale", "startup_failure", "timed_out",
])
def test_diagnostic_recorder_requires_current_push_and_keeps_explicit_authority(
    isolated_state: Path, conclusion: str,
) -> None:
    arguments = _diagnostic_arguments()
    run = arguments["run"]
    assert isinstance(run, dict)
    run["conclusion"] = conclusion
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({
        "last_push_sha": arguments["sha"], "last_checked_sha": "", "keep": "owned",
    }))
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")
    _write_state(isolated_state, check_count=7, last_verdict="pending")

    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is True

    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    state = json.loads(isolated_state.read_text())
    expected = "diagnostic_success" if conclusion == "success" else "failure"
    assert history["last_verdict"] == state["last_verdict"] == expected
    assert history["last_verdict_kind"] == "diagnostic-workflow-dispatch"
    assert state["last_verdict_kind"] == "diagnostic-workflow-dispatch"
    assert history["last_checked_sha"] == arguments["sha"]
    assert history["diagnostic_run"]["conclusion"] == conclusion
    assert history["keep"] == "owned"
    assert state["check_count"] == 7
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "0"


@pytest.mark.parametrize("field, value", [
    ("repo", "other/project"), ("branch", "development"), ("branch", "feature/"),
    ("branch", "feature/unsafe..branch"), ("branch", "feature/a//b"),
    ("branch", "feature/a/"), ("branch", "feature/a."),
    ("branch", "feature/.hidden"), ("branch", "feature/owned.lock"),
    ("sha", "b" * 39), ("workflow", "Molecule Tests"), ("event", "push"),
    ("after_run_id", 42), ("after_run_id", -1),
])
def test_diagnostic_recorder_rejects_ineligible_selector_without_any_state_mutation(
    isolated_state: Path, field: str, value: object,
) -> None:
    arguments = _diagnostic_arguments()
    arguments[field] = value
    before = '{"last_push_sha": "' + "b" * 40 + '", "keep": "owned"}'
    ci_check_cooldown.HISTORY_FILE.write_text(before)
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False

    assert ci_check_cooldown.HISTORY_FILE.read_text() == before
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"
    assert not ci_check_cooldown.HISTORY_FILE.with_name("history.json.lock").exists()


@pytest.mark.parametrize("field, value", [
    ("databaseId", 0), ("databaseId", -1), ("databaseId", True), ("databaseId", "42"),
    ("headSha", "c" * 40), ("headBranch", "feature/other"),
    ("workflowName", "Molecule Tests"), ("event", "push"),
    ("status", "queued"), ("status", "in_progress"),
    ("conclusion", ""), ("conclusion", "unknown"), ("conclusion", []),
])
def test_diagnostic_recorder_rejects_inexact_or_incomplete_run_without_history_write(
    isolated_state: Path, field: str, value: object,
) -> None:
    arguments = _diagnostic_arguments()
    run = arguments["run"]
    assert isinstance(run, dict)
    run[field] = value
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({
        "last_push_sha": "b" * 40, "last_checked_sha": "", "keep": "owned",
    }))
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False

    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"


@pytest.mark.parametrize("history", [
    {}, {"last_push_sha": "c" * 40}, {"last_checked_sha": "b" * 40},
])
def test_diagnostic_recorder_never_creates_ownership_from_caller_claims(
    isolated_state: Path, history: dict[str, object],
) -> None:
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps(history))
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_verdict(**_diagnostic_arguments()) is False

    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"


@pytest.mark.parametrize("new_push", [False, True])
@pytest.mark.parametrize("observed_id", [41, 42])
def test_reobserving_same_diagnostic_run_cannot_clear_new_restart_debt(
    isolated_state: Path, new_push: bool, observed_id: int,
) -> None:
    arguments = _diagnostic_arguments()
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({"last_push_sha": "b" * 40}))
    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is True
    if new_push:
        assert ci_check_cooldown.cmd_record_push("b" * 40) == 0
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    state_before = isolated_state.read_bytes()
    run = arguments["run"]
    assert isinstance(run, dict)
    run["databaseId"] = observed_id

    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False

    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert isolated_state.read_bytes() == state_before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"


@pytest.mark.parametrize("failed_owner", ["state", "history", "restart"])
def test_diagnostic_atomic_write_failures_retain_counter_and_applied_state_scope(
    isolated_state: Path, failed_owner: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _diagnostic_arguments()
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({
        "last_push_sha": "b" * 40, "last_checked_sha": "", "keep": "owned",
    }))
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")
    _write_state(isolated_state, last_verdict="pending")
    state_before = isolated_state.read_bytes()
    history_before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    target = {
        "state": isolated_state,
        "history": ci_check_cooldown.HISTORY_FILE,
        "restart": ci_check_cooldown.RESTART_COUNT_FILE,
    }[failed_owner]
    original_replace = ci_check_cooldown.os.replace

    def replace(source: Path, destination: Path) -> None:
        if destination == target:
            raise OSError(f"owned {failed_owner} replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(ci_check_cooldown.os, "replace", replace)
    with pytest.raises(OSError, match=f"owned {failed_owner} replacement failure"):
        ci_check_cooldown.record_diagnostic_verdict(**arguments)

    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"
    assert not list(isolated_state.parent.glob("*.tmp"))
    if failed_owner == "state":
        assert isolated_state.read_bytes() == state_before
    if failed_owner in {"state", "history"}:
        assert ci_check_cooldown.HISTORY_FILE.read_bytes() == history_before
    else:
        assert json.loads(ci_check_cooldown.HISTORY_FILE.read_text())["last_checked_sha"] == "b" * 40


@pytest.mark.parametrize("contents", [None, "[]", "{invalid"])
def test_diagnostic_recorder_does_not_forge_missing_or_corrupt_push_history(
    isolated_state: Path, contents: str | None,
) -> None:
    if contents is not None:
        ci_check_cooldown.HISTORY_FILE.write_text(contents)
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_verdict(**_diagnostic_arguments()) is False

    assert ci_check_cooldown.HISTORY_FILE.exists() is (contents is not None)
    if contents is not None:
        assert ci_check_cooldown.HISTORY_FILE.read_text() == contents
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"


def test_existing_verdict_writer_clears_diagnostic_label_and_preserves_run_history(
    isolated_state: Path,
) -> None:
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({"last_push_sha": "b" * 40}))
    assert ci_check_cooldown.record_diagnostic_verdict(**_diagnostic_arguments()) is True
    run = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())["diagnostic_run"]

    assert ci_check_cooldown.cmd_record_verdict("pending") == 0
    assert "last_verdict_kind" not in json.loads(isolated_state.read_text())
    assert ci_check_cooldown.cmd_record_verdict("success", "b" * 40) == 0
    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    assert "last_verdict_kind" not in history
    assert history["diagnostic_run"] == run


@pytest.mark.parametrize("preseed", ["symlink", "regular"])
def test_atomic_writer_never_clobbers_a_preexisting_predictable_temporary_path(
    tmp_path: Path, preseed: str,
) -> None:
    target = tmp_path / "owned-state.json"
    target.write_text("previous state")
    foreign = tmp_path / "foreign-sentinel"
    foreign.write_text("foreign bytes")
    predictable = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    if preseed == "symlink":
        predictable.symlink_to(foreign)
    else:
        predictable.write_text("preexisting temporary bytes")

    ci_check_cooldown._replace_atomic(target, "owned replacement")

    assert foreign.read_text() == "foreign bytes"
    assert predictable.exists()
    assert predictable.is_symlink() is (preseed == "symlink")
    assert predictable.read_text() == (
        "foreign bytes" if preseed == "symlink" else "preexisting temporary bytes"
    )
    assert not target.is_symlink()
    assert target.read_text() == "owned replacement"


@pytest.mark.parametrize("replacement", ["symlink", "regular"])
def test_atomic_failure_cleanup_preserves_a_foreign_replacement_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    target = tmp_path / "owned-state.json"
    target.write_text("previous state")
    foreign = tmp_path / "foreign-sentinel"
    foreign.write_text("foreign bytes")
    interrupted: list[Path] = []

    def fail_after_foreign_replacement(source: Path, destination: Path) -> None:
        assert destination == target
        source.unlink()
        if replacement == "symlink":
            source.symlink_to(foreign)
        else:
            source.write_text("foreign temporary bytes")
        interrupted.append(source)
        raise OSError("owned interrupted replacement")

    monkeypatch.setattr(ci_check_cooldown.os, "replace", fail_after_foreign_replacement)
    with pytest.raises(OSError, match="owned interrupted replacement"):
        ci_check_cooldown._replace_atomic(target, "owned replacement")

    assert target.read_text() == "previous state"
    assert foreign.read_text() == "foreign bytes"
    assert len(interrupted) == 1
    assert interrupted[0].exists()
    assert interrupted[0].is_symlink() is (replacement == "symlink")
    assert interrupted[0].read_text() == (
        "foreign bytes" if replacement == "symlink" else "foreign temporary bytes"
    )


def _owned_diagnostic_pending() -> dict[str, object]:
    arguments = _diagnostic_arguments()
    assert ci_check_cooldown.cmd_record_push(
        str(arguments["sha"]), str(arguments["branch"]), "feature-ci",
    ) == 0
    arguments["expected_generation"] = ci_check_cooldown.diagnostic_push_generation()
    run = arguments["run"]
    assert isinstance(run, dict)
    run.update(status="queued", conclusion="")
    return arguments


@pytest.mark.parametrize("status", ["queued", "in_progress", "pending", "requested", "waiting"])
def test_owned_pending_acknowledgement_keeps_counter_and_canonical_state(
    isolated_state: Path, status: str,
) -> None:
    arguments = _owned_diagnostic_pending()
    run = arguments["run"]
    assert isinstance(run, dict)
    run["status"] = status
    _write_state(isolated_state, last_verdict="failure", check_count=9)
    before = isolated_state.read_bytes()
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is True

    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    assert history["last_checked_sha"] == history["last_push_sha"] == arguments["sha"]
    assert history["last_verdict"] == "pending"
    assert history["diagnostic_run"]["generation"] == history["push_generation"] == 1
    assert history["diagnostic_run"]["status"] == status
    assert isolated_state.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "3"
    pending = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == pending


@pytest.mark.parametrize("conclusion", ["failure", "success"])
def test_current_pending_run_can_upgrade_to_terminal_exactly_once(
    isolated_state: Path, conclusion: str,
) -> None:
    arguments = _owned_diagnostic_pending()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is True
    run = arguments["run"]
    assert isinstance(run, dict)
    run.update(status="completed", conclusion=conclusion)
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("3")

    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is True
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "0"
    history = ci_check_cooldown.HISTORY_FILE.read_bytes()
    state = isolated_state.read_bytes()
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("2")
    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == history
    assert isolated_state.read_bytes() == state
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"


@pytest.mark.parametrize("change", [
    "legacy", "canonical", "branch", "purpose", "repo", "sha", "generation",
    "boolean-generation", "incomplete", "record-incomplete", "floor", "missing-token",
])
def test_pending_requires_complete_current_saved_feature_publication(
    isolated_state: Path, change: str,
) -> None:
    arguments = _owned_diagnostic_pending()
    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    if change == "legacy":
        history = {"last_push_sha": arguments["sha"], "last_checked_sha": ""}
    elif change == "canonical":
        assert ci_check_cooldown.cmd_record_push(str(arguments["sha"])) == 0
        history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
        arguments["expected_generation"] = history["push_generation"]
    elif change == "record-incomplete":
        history["push_record_complete"] = False
    elif change == "missing-token":
        arguments["expected_generation"] = None
    else:
        saved = history["diagnostic_push"]
        field, value = {
            "branch": ("branch", "feature/foreign"),
            "purpose": ("purpose", "canonical"), "repo": ("repo", "other/project"),
            "sha": ("sha", "c" * 40), "generation": ("generation", 2),
            "boolean-generation": ("generation", True),
            "incomplete": ("complete", False), "floor": ("run_floor", 42),
        }[change]
        saved[field] = value
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps(history))
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    count = ci_check_cooldown.RESTART_COUNT_FILE.read_bytes()

    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is False

    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_bytes() == count
    assert not isolated_state.exists()


@pytest.mark.parametrize("new_branch", ["feature/owned-feedback", "diagnostic/other"])
@pytest.mark.parametrize("terminal", [False, True])
def test_generation_captured_before_lookup_cannot_acknowledge_same_sha_republication(
    isolated_state: Path, new_branch: str, terminal: bool,
) -> None:
    arguments = _owned_diagnostic_pending()
    assert ci_check_cooldown.cmd_record_push(str(arguments["sha"]), new_branch, "feature-ci") == 0
    run = arguments["run"]
    assert isinstance(run, dict)
    if terminal:
        run.update(status="completed", conclusion="success")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()

    recorder = (ci_check_cooldown.record_diagnostic_verdict if terminal
                else ci_check_cooldown.record_diagnostic_pending)
    assert recorder(**arguments) is False

    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"
    assert not isolated_state.exists()


@pytest.mark.parametrize("failed_stage", ["arm", "counter", "complete"])
def test_partial_publication_record_never_enables_pending_acknowledgement(
    isolated_state: Path, failed_stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _diagnostic_arguments()
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({"last_push_sha": "a" * 40}))
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("2")
    original = ci_check_cooldown._replace_atomic
    history_writes = 0

    def fail_stage(path: Path, contents: str) -> None:
        nonlocal history_writes
        if path == ci_check_cooldown.HISTORY_FILE:
            history_writes += 1
        if (
            (failed_stage == "arm" and path == ci_check_cooldown.HISTORY_FILE)
            or (failed_stage == "counter" and path == ci_check_cooldown.RESTART_COUNT_FILE)
            or (failed_stage == "complete" and path == ci_check_cooldown.HISTORY_FILE
                and history_writes == 2)
        ):
            raise OSError("owned partial publication record")
        original(path, contents)

    with monkeypatch.context() as patch:
        patch.setattr(ci_check_cooldown, "_replace_atomic", fail_stage)
        assert ci_check_cooldown.cmd_record_push(
            str(arguments["sha"]), str(arguments["branch"]), "feature-ci",
        ) == 1
    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    assert history.get("push_record_complete") is not True
    assert "diagnostic_push" not in history
    assert history.get("last_checked_sha", "") == ""
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == (
        "3" if failed_stage == "complete" else "2"
    )
    arguments["expected_generation"] = ci_check_cooldown.diagnostic_push_generation()
    run = arguments["run"]
    assert isinstance(run, dict)
    run.update(status="queued", conclusion="")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is False
    run.update(status="completed", conclusion="success")
    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert not isolated_state.exists()


@pytest.mark.parametrize("branch,purpose", [
    ("development", "feature-ci"), ("feature/", "feature-ci"),
    ("feature/a..b", "feature-ci"), ("feature/.hidden", "feature-ci"),
    ("feature/a.lock", "feature-ci"), ("feature/a", "canonical"),
    (None, "feature-ci"), ("feature/a", None),
])
def test_record_push_rejects_invalid_diagnostic_metadata_before_any_write(
    isolated_state: Path, branch: str | None, purpose: str | None,
) -> None:
    assert ci_check_cooldown.cmd_record_push("b" * 40, branch, purpose) == 2
    assert not ci_check_cooldown.HISTORY_FILE.exists()
    assert not ci_check_cooldown.RESTART_COUNT_FILE.exists()


@pytest.mark.parametrize("generation", [-1, True, "1", None])
def test_record_push_refuses_corrupt_generation_without_counter_or_history_change(
    isolated_state: Path, generation: object,
) -> None:
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({"push_generation": generation}))
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("2")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.cmd_record_push("b" * 40) == 1
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"


def test_record_push_cli_persists_saved_feature_metadata(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci_check_cooldown.sys, "argv", [
        "ci_check_cooldown.py", "record-push", "b" * 40, "feature/owned-feedback", "feature-ci",
    ])
    assert ci_check_cooldown.main() == 0
    saved = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())["diagnostic_push"]
    assert saved["sha"] == "b" * 40
    assert saved["branch"] == "feature/owned-feedback"
    assert saved["purpose"] == "feature-ci"
    assert saved["generation"] == 1


@pytest.mark.parametrize("field,value", [
    ("status", "completed"), ("status", "unknown"), ("conclusion", "success"),
    ("headBranch", "feature/other"), ("databaseId", True),
])
def test_pending_rejects_inexact_or_terminal_runs(
    isolated_state: Path, field: str, value: object,
) -> None:
    arguments = _owned_diagnostic_pending()
    run = arguments["run"]
    assert isinstance(run, dict)
    run[field] = value
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "1"


def test_pending_history_write_failure_preserves_counter_and_canonical_state(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _owned_diagnostic_pending()
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()

    def fail_history(_history: dict[str, object]) -> None:
        raise OSError("owned pending history failure")

    monkeypatch.setattr(ci_check_cooldown, "save_history_atomic", fail_history)
    with pytest.raises(OSError, match="owned pending history failure"):
        ci_check_cooldown.record_diagnostic_pending(**arguments)
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "1"
    assert not isolated_state.exists()


def test_saved_feature_terminal_feedback_also_requires_pre_lookup_generation(
    isolated_state: Path,
) -> None:
    arguments = _owned_diagnostic_pending()
    run = arguments["run"]
    assert isinstance(run, dict)
    run.update(status="completed", conclusion="success")
    arguments.pop("expected_generation")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.record_diagnostic_verdict(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "1"
    assert not isolated_state.exists()


def test_fresh_same_sha_publication_retains_known_run_floor_before_new_feedback(
    isolated_state: Path,
) -> None:
    arguments = _owned_diagnostic_pending()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is True
    assert ci_check_cooldown.cmd_record_push(
        str(arguments["sha"]), str(arguments["branch"]), "feature-ci",
    ) == 0
    arguments["expected_generation"] = ci_check_cooldown.diagnostic_push_generation()
    history = json.loads(ci_check_cooldown.HISTORY_FILE.read_text())
    assert history["diagnostic_push"]["run_floor"] == 42
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is False
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"
    run = arguments["run"]
    assert isinstance(run, dict)
    run["databaseId"] = 43
    assert ci_check_cooldown.record_diagnostic_pending(**arguments) is True
    assert not isolated_state.exists()
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"


def test_publication_record_refuses_a_malformed_prior_run_floor(
    isolated_state: Path,
) -> None:
    ci_check_cooldown.HISTORY_FILE.write_text(json.dumps({
        "last_push_sha": "a" * 40, "diagnostic_run": {"id": "invalid"},
    }))
    ci_check_cooldown.RESTART_COUNT_FILE.write_text("2")
    before = ci_check_cooldown.HISTORY_FILE.read_bytes()
    assert ci_check_cooldown.cmd_record_push("b" * 40) == 1
    assert ci_check_cooldown.HISTORY_FILE.read_bytes() == before
    assert ci_check_cooldown.RESTART_COUNT_FILE.read_text() == "2"
