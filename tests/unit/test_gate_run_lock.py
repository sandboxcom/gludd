"""Regression tests for full-gate/gate-refresh mutual exclusion."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate_run_lock.py"
MAKEFILE = ROOT / "Makefile"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_run_lock_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lock_module = _load_module()


def _run(action: str, lock: Path, pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), action, str(lock), str(pid)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_live_owner_blocks_competing_gate(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"

    acquired = _run("acquire", lock, os.getpid())
    assert acquired.returncode == 0, acquired.stderr

    competing = _run("acquire", lock, os.getppid())
    assert competing.returncode != 0
    assert "already running" in (competing.stdout + competing.stderr)


def test_stale_owner_is_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    lock.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    result = _run("acquire", lock, os.getpid())

    assert result.returncode == 0, result.stderr
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["marker"] == lock_module.GATE_LOCK_MARKER
    assert payload["state"] == "active"
    assert payload["pid_started_at"]
    assert payload["project_root"] == str(ROOT)


def test_termination_failed_owner_is_not_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    lock.write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "state": "termination_failed",
                "survivor_pids": [1234],
            }
        ),
        encoding="utf-8",
    )

    result = _run("acquire", lock, os.getpid())

    assert result.returncode != 0
    assert "termination failed" in (result.stdout + result.stderr)
    assert lock.exists()


def test_only_owner_can_release_lock(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    assert _run("acquire", lock, os.getpid()).returncode == 0

    refused = _run("release", lock, os.getppid())
    assert refused.returncode != 0
    assert lock.exists()

    released = _run("release", lock, os.getpid())
    assert released.returncode == 0
    assert not lock.exists()


def test_assert_inactive_blocks_live_owner_and_reclaims_stale_owner(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.lock"
    assert _run("assert-inactive", missing, os.getpid()).returncode == 0

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    refused = _run("assert-inactive", live, os.getppid())
    assert refused.returncode != 0
    assert "active gate" in (refused.stdout + refused.stderr)
    assert "state=" in (refused.stdout + refused.stderr)

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    reclaimed = _run("assert-inactive", stale, os.getpid())
    assert reclaimed.returncode == 0
    assert not stale.exists()


def test_assert_inactive_reclaims_reused_live_pid_identity(tmp_path: Path) -> None:
    """A recycled PID must not preserve a dead gate's mutation lock."""
    lock = tmp_path / "reused-pid.lock"
    lock.write_text(
        json.dumps(
            {
                "marker": lock_module.GATE_LOCK_MARKER,
                "state": "active",
                "pid": os.getpid(),
                "pid_started_at": "not-the-current-process-start-token",
            }
        ),
        encoding="utf-8",
    )

    reclaimed = _run("assert-inactive", lock, os.getppid())

    assert reclaimed.returncode == 0, reclaimed.stderr
    assert "reclaimed stale owner" in reclaimed.stdout
    assert not lock.exists()


def test_assert_inactive_fails_closed_on_unreadable_owner(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    lock.write_text("{not-json", encoding="utf-8")

    refused = _run("assert-inactive", lock, os.getpid())

    assert refused.returncode != 0
    assert "unreadable owner" in (refused.stdout + refused.stderr)


def test_gate_and_refresh_acquire_before_running_phases() -> None:
    text = compose_makefile(MAKEFILE)
    gate_header = next(line for line in text.splitlines() if line.startswith("gate:"))
    refresh_header = next(
        line for line in text.splitlines() if line.startswith("gate-refresh:")
    )

    # Admission is read-only; it must reject invalid policy before acquiring a
    # lease. The lock is still mandatory before any stateful gate recipe runs.
    assert gate_header == (
        "gate: gate-admission-config "
        "$(if $(filter release,$(GATE_TEST_PROFILE)),_gate-release-state-check,) "
        "_gate-run-lock-acquire"
    )
    assert ".NOTPARALLEL: gate gate-refresh" in text
    assert (
        refresh_header.split(":", 1)[1].strip().split()[0]
        == "_gate-run-lock-acquire"
    )
    assert text.count('gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID"') >= 2


@pytest.mark.parametrize(
    ("profile", "failed_check"),
    [
        ("native", None),
        ("release", None),
        ("native", "gate-admission-config"),
        ("release", "gate-admission-config"),
        ("release", "_gate-release-state-check"),
        ("native", "_gate-run-lock-acquire"),
        ("release", "_gate-run-lock-acquire"),
    ],
)
def test_real_gate_prerequisites_admit_before_lock_and_lock_before_phases(
    tmp_path: Path, profile: str, failed_check: str | None,
) -> None:
    """Exercise the actual header's ordered checks without acquiring a real lease."""
    source = compose_makefile(MAKEFILE)
    header = next(line for line in source.splitlines() if line.startswith("gate:"))
    targets = ["gate-admission-config", "_gate-release-state-check", "_gate-run-lock-acquire"]
    fixture = tmp_path / "Makefile"
    fixture.write_text(
        f"GATE_TEST_PROFILE := {profile}\n.NOTPARALLEL: gate gate-refresh\n"
        f"{header}\n\t@printf 'phase\\n' >> events\n"
        + "\n".join(
            f"{target}:\n\t@printf '{target}\\n' >> events\n"
            + ("\t@exit 9\n" if target == failed_check else "")
            for target in targets
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-j2", "-f", str(fixture), "gate"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False,
    )
    expected = [targets[0], *([targets[1]] if profile == "release" else []), targets[2]]
    if failed_check is None:
        assert result.returncode == 0, result.stdout + result.stderr
        expected.append("phase")
    else:
        assert result.returncode != 0
        expected = expected[:expected.index(failed_check) + 1]
    assert (tmp_path / "events").read_text(encoding="utf-8").splitlines() == expected


def test_history_mutation_targets_depend_on_active_gate_guard() -> None:
    text = compose_makefile(MAKEFILE)
    guarded_headers = (
        "_commit-lock-acquire:",
        "_merge-strategy-guard:",
        "agent-merge:",
        "gated-merge:",
        "git-checkout:",
        "git-cherry-pick:",
    )

    for target in guarded_headers:
        header = next(line for line in text.splitlines() if line.startswith(target))
        assert "_gate-mutation-guard" in header, target


def test_pid_and_owner_helpers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert lock_module._pid_alive(0) is False
    assert lock_module._pid_alive(os.getpid()) is True

    def gone(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(lock_module.os, "kill", gone)
    assert lock_module._pid_alive(73) is False

    def inaccessible(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(lock_module.os, "kill", inaccessible)
    assert lock_module._pid_alive(73) is True

    missing = tmp_path / "missing.lock"
    assert lock_module._read_owner(missing) is None
    missing.write_text("{bad-json", encoding="utf-8")
    assert lock_module._read_owner(missing) is None


def test_zombie_gate_owner_is_not_alive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "zombie.lock"
    lock.write_text(json.dumps({"pid": 73}), encoding="utf-8")
    monkeypatch.setattr(lock_module, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(lock_module, "_pid_state", lambda _pid: "Z+")

    assert lock_module._owner_alive(lock, 73) is False


def test_publish_release_and_acquire_boundaries(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    assert lock_module._publish_lock(lock, os.getpid()) is True
    assert lock_module._publish_lock(lock, os.getpid()) is False
    assert lock_module.release(lock, os.getppid()) == 1
    assert lock_module.release(lock, os.getpid()) == 0
    assert lock_module.release(lock, os.getpid()) == 1

    malformed = tmp_path / "malformed.lock"
    malformed.write_text("[]", encoding="utf-8")
    assert lock_module.acquire(malformed, os.getpid()) == 1

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert lock_module.acquire(live, os.getppid()) == 1

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    assert lock_module.acquire(stale, os.getpid()) == 0


def test_assert_inactive_direct_boundaries(tmp_path: Path) -> None:
    missing = tmp_path / "missing.lock"
    assert lock_module.assert_inactive(missing, os.getpid()) == 0

    malformed = tmp_path / "malformed.lock"
    malformed.write_text("null", encoding="utf-8")
    assert lock_module.assert_inactive(malformed, os.getpid()) == 1

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert lock_module.assert_inactive(live, os.getppid()) == 1

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    assert lock_module.assert_inactive(stale, os.getpid()) == 0
    assert not stale.exists()


def test_main_dispatches_actions_and_rejects_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "gate.lock"
    assert lock_module.main(["gate_run_lock.py"]) == 2
    assert lock_module.main(["gate_run_lock.py", "unknown", str(lock), "1"]) == 2
    assert (
        lock_module.main(
            ["gate_run_lock.py", "acquire", str(lock), "not-a-pid"]
        )
        == 2
    )

    monkeypatch.setattr(lock_module, "acquire", lambda _path, _pid: 11)
    monkeypatch.setattr(lock_module, "release", lambda _path, _pid: 12)
    monkeypatch.setattr(lock_module, "assert_inactive", lambda _path, _pid: 13)
    assert lock_module.main(["gate_run_lock.py", "acquire", str(lock), "7"]) == 11
    assert lock_module.main(["gate_run_lock.py", "release", str(lock), "7"]) == 12
    assert (
        lock_module.main(
            ["gate_run_lock.py", "assert-inactive", str(lock), "7"]
        )
        == 13
    )
