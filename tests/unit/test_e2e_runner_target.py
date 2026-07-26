"""Regression coverage for the bounded E2E runner Make target."""

from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    remaining = content[start:]
    return remaining.split("\n\n", 1)[0]


def test_e2e_runner_uses_unique_basetemp() -> None:
    body = _target_body("test-e2e")
    assert 'BT="/tmp/gludd-e2e-' in body
    assert '--basetemp=$$BT' in body


def test_e2e_runner_marks_nested_execution() -> None:
    body = _target_body("test-e2e")
    assert "GLUDD_E2E_ACTIVE=1" in body


def test_e2e_runner_has_per_test_timeout_and_cleanup() -> None:
    body = _target_body("test-e2e")
    assert "--timeout=" in body
    assert 'rm -rf "$$BT"' in body
    assert "exit $$RC" in body


def test_e2e_marker_is_registered() -> None:
    config = (MAKEFILE.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"e2e:' in config


def test_e2e_runner_has_suite_watchdog_bounds() -> None:
    body = _target_body("test-e2e")
    assert "run-watched" in body
    assert "E2E_FILE_MAX_SECS" in body
    assert "E2E_STALL_SECS" in body


def test_e2e_runner_log_is_namespaced_by_process() -> None:
    body = _target_body("test-e2e")
    assert 'LOG="/tmp/gludd-e2e-$$$$.log"' in body


def test_e2e_runner_exclusively_owns_full_suite() -> None:
    body = _target_body("test-e2e")
    assert 'LOCK="/tmp/gludd-e2e-run.lock"' in body
    assert 'mkdir "$$LOCK"' in body
    assert "E2E_RUN_BUSY" in body
    assert "exit 75" in body


def test_e2e_runner_releases_its_own_lock_without_killing_owner() -> None:
    body = _target_body("test-e2e")
    assert "trap" in body
    assert 'rm -rf "$$LOCK"' in body
    assert "pkill" not in body


def test_worktree_e2e_cleanup_is_scoped_to_the_requesting_worktree() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    assert "kill-worktree-e2e:" in content
    start = content.index("kill-worktree-e2e:")
    body = content[start:].split("\n\n", 1)[0]
    assert "$(CURDIR)" in body
    assert "pytest tests/e2e/" in body
    assert "pgrep -P" in body
    assert "Refusing to kill unrelated" in body

    assert "tree_contains_local_e2e" in body


def test_e2e_runner_executes_files_in_bounded_serial_processes() -> None:
    body = _target_body("test-e2e")
    assert "e2e_supervisor.py pending" in body
    assert "for test_file in" in body
    assert "E2E_WORKERS" in body


def test_nested_full_unit_suite_is_rejected_during_e2e() -> None:
    body = _target_body("test-unit")
    assert "GLUDD_E2E_ACTIVE" in body
    assert "nested full test-unit" in body


def test_e2e_runner_uses_durable_restart_supervisor() -> None:
    body = _target_body("test-e2e")
    assert "e2e_supervisor.py pending" in body
    assert "e2e_supervisor.py record" in body
    assert "heartbeat-loop" in body
    assert "E2E_HEARTBEAT_SECS" in body
