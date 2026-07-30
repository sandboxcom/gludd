from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from scripts import clean_tmp

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_tmp.py"
DISK_GUARD = ROOT / "scripts" / "disk-guard.sh"
MAKEFILE = ROOT / "Makefile"


def test_clean_tmp_scopes_generated_release_audit_artifacts() -> None:
    expected = {
        "gludd-audit-e2e-*",
        "gludd-collect-output.txt",
        "gludd-gate-refresh-test.log",
    }

    assert expected.issubset(set(clean_tmp.TMP_GLOBS))


def test_gate_refresh_log_cannot_be_deleted_by_concurrent_tmp_cleanup() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    gate_refresh = makefile.split("gate-refresh:", 1)[1].split(
        "\n_gate-fresh-check:", 1
    )[0]

    assert 'TEST_LOG="/tmp/gludd-gate-refresh-test.$$$$.log"' in gate_refresh
    assert "test-unit-shards-sequential" in gate_refresh
    assert "[gate-refresh] full log: $$TEST_LOG" in gate_refresh
    assert "> /tmp/gludd-gate-refresh-test.log" not in gate_refresh


def test_gate_refresh_recycles_workers_between_bounded_unit_shards() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    gate_refresh = makefile.split("gate-refresh:", 1)[1].split(
        "\n_gate-fresh-check:", 1
    )[0]
    shard_target = makefile.split("test-unit-shards-sequential:", 1)[1].split(
        "test-ci-shards-parallel:", 1
    )[0]

    assert "test-unit-shards-sequential" in gate_refresh
    assert "run-watched" in gate_refresh
    assert "PYTEST_ARGS=-q" in gate_refresh
    assert 'PYTEST_ARGS="' not in gate_refresh
    assert "pytest tests/unit/" not in gate_refresh
    assert "test-ci-shard" in shard_target
    for shard in ("unit-1a1", "unit-1a2", "unit-1b", "unit-1d", "unit-2", "unit-3"):
        assert shard in shard_target


def test_clean_tmp_removes_home_tmp_pytest_garbage_with_unwritable_children(tmp_path: Path) -> None:
    home = tmp_path / "home"
    pytest_root = home / "tmp" / "pytest-of-shawnwilson"
    secrets = pytest_root / "garbage-deadbeef" / "test_case0" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "pause_mac.key").write_text("secret", encoding="utf-8")
    os.chmod(secrets, 0o000)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp_path / "unrelated-tmp")

    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "failed=0" in result.stdout
        assert pytest_root.exists()
        assert not (pytest_root / "garbage-deadbeef").exists()
    finally:
        if secrets.exists():
            os.chmod(secrets, 0o700)


def test_clean_tmp_preserves_live_foreign_pytest_namespace(tmp_path: Path) -> None:
    """Cleanup may reclaim pytest garbage but never another live test run."""
    home = tmp_path / "home"
    pytest_root = home / "tmp" / "pytest-of-shawnwilson"
    live_worker = pytest_root / "pytest-3" / "popen-gw0"
    live_worker.mkdir(parents=True)
    live_marker = live_worker / "test_config_only_guard0"
    live_marker.write_text("still running", encoding="utf-8")
    garbage = pytest_root / "garbage-deadbeef" / "test_case0"
    garbage.mkdir(parents=True)
    (garbage / "stale.txt").write_text("stale", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home / "tmp")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert live_marker.read_text(encoding="utf-8") == "still running"
    assert not garbage.exists()


def test_disk_guard_delegates_scoped_cleanup_without_shared_root_globs() -> None:
    script = DISK_GUARD.read_text(encoding="utf-8")

    assert "scripts/clean_tmp.py" in script
    assert "rm -rf /tmp/pytest-of-" not in script
    assert "rm -rf /private/tmp/pytest-of-" not in script


def test_clean_tmp_never_chmods_symlink_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    pytest_root = home / "tmp" / "pytest-of-shawnwilson"
    garbage = pytest_root / "garbage-deadbeef"
    garbage.mkdir(parents=True)
    external_executable = tmp_path / "external-python"
    external_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    external_executable.chmod(0o755)
    (garbage / "python3").symlink_to(external_executable)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp_path / "unrelated-tmp")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert stat.S_IMODE(external_executable.stat().st_mode) == 0o755
    assert not garbage.exists()
