"""Structural tests for global pytest cwd isolation."""
from __future__ import annotations

from pathlib import Path


def test_conftest_restores_cwd_after_each_test() -> None:
    conftest = Path(__file__).resolve().parents[1] / "conftest.py"
    source = conftest.read_text()

    assert "def _restore_cwd_after_test" in source
    assert "original_cwd = os.getcwd()" in source
    assert "os.chdir(original_cwd)" in source
    assert "os.chdir(_REPO_ROOT)" in source
