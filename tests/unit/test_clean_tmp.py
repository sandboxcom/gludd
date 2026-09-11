from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import scripts.clean_tmp as clean_tmp

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_tmp.py"


def test_clean_tmp_preserves_generic_pytest_roots_owned_by_other_runs(tmp_path: Path) -> None:
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
    finally:
        if secrets.exists():
            os.chmod(secrets, 0o700)


def test_clean_tmp_never_targets_active_gludd_test_directories() -> None:
    source = SCRIPT.read_text()

    assert '"gludd-iso-*"' not in source
    assert 'Path("/tmp/gludd-gate-basetemp")' not in source
    assert "gludd-azure-accelerator-auth" not in source


def test_documented_azure_credentials_are_never_under_temporary_storage() -> None:
    documentation = "\n".join(
        (
            (ROOT / "docs" / "azure-iam-setup.md").read_text(encoding="utf-8"),
            (ROOT / "config" / "infra" / "IAM_README.md").read_text(
                encoding="utf-8"
            ),
        )
    )

    assert "/tmp/gludd-azure-accelerator-auth" not in documentation
    assert "azure-accelerator-auth-store" in documentation


def test_cleanup_reclaims_explicit_disposable_file_but_preserves_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    durable_root = tmp_path / "durable"
    runtime_root.mkdir()
    durable_root.mkdir()
    disposable = runtime_root / "gludd-test-gate.txt"
    credential = durable_root / "azure-accelerator-auth.json"
    disposable.write_text("reclaim me", encoding="utf-8")
    credential.write_text("preserve me", encoding="utf-8")
    monkeypatch.setattr(clean_tmp, "_candidates", lambda: [disposable])
    monkeypatch.setattr(clean_tmp, "_allowed_roots", lambda: [runtime_root.resolve()])

    assert clean_tmp.main() == 0

    assert not disposable.exists()
    assert credential.read_text(encoding="utf-8") == "preserve me"
