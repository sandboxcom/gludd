"""Real Make ranged edits preserve destination bytes and modes on native failure."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("failure", ["missing-input", "invalid-range", "make-expression", "shell-expression", "none"])
def test_ranged_edit_propagates_native_result_and_preserves_mode(
    tmp_path: Path, failure: str, environment_overrides: bool,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "replace_lines.py").write_bytes((ROOT / "scripts/replace_lines.py").read_bytes())
    probe = tmp_path / "probe.mk"
    probe.write_text(f"include {ROOT / 'make/95-collections-and-pipelines.mk'}\n", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_bytes(b"one\ntwo\nthree\n")
    target.chmod(0o750)
    replacement = tmp_path / "replacement.txt"
    if failure != "missing-input":
        replacement.write_bytes(b"TWO\n")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
    }
    environment.update(
        GLUDD_LINE_EDIT_FILE="ambient-wrong.txt", GLUDD_LINE_EDIT_START="wrong",
        GLUDD_LINE_EDIT_END="wrong", GLUDD_LINE_EDIT_NEW_FILE="ambient-wrong.txt",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if environment_overrides else []),
         "-f", str(probe), "replace-lines",
         "PYTHON=" + sys.executable, "FILE=" + target.name,
         "NEW_FILE=" + ("`touch TRIPWIRE`" if failure == "shell-expression" else replacement.name),
         "START=" + ("$(shell touch TRIPWIRE)" if failure == "make-expression" else
                     "0" if failure == "invalid-range" else "2"), "END=2"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=10,
    )
    assert not (tmp_path / "TRIPWIRE").exists()
    if failure == "none":
        assert result.returncode == 0, result.stdout + result.stderr
        assert target.read_bytes() == b"one\nTWO\nthree\n"
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert target.read_bytes() == b"one\ntwo\nthree\n"
    assert target.stat().st_mode & 0o777 == 0o750
    assert not list(tmp_path.glob("target.txt.replace.*"))
