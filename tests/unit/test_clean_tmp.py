from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from scripts import clean_tmp

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_tmp.py"


def test_clean_tmp_scopes_generated_release_audit_artifacts() -> None:
    expected = {
        "gludd-audit-e2e-*",
        "gludd-collect-output.txt",
        "gludd-gate-refresh-test.log",
    }

    assert expected.issubset(set(clean_tmp.TMP_GLOBS))


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
        assert not pytest_root.exists()
    finally:
        if secrets.exists():
            os.chmod(secrets, 0o700)


def test_clean_tmp_never_chmods_symlink_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    pytest_root = home / "tmp" / "pytest-of-shawnwilson"
    pytest_root.mkdir(parents=True)
    external_executable = tmp_path / "external-python"
    external_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    external_executable.chmod(0o755)
    (pytest_root / "python3").symlink_to(external_executable)

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
