from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_tmp.py"


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
