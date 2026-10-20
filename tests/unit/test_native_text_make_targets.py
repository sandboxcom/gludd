"""Exercise production Make text targets, preserving data on invalid transport."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "make/40-cross-version-and-worktrees.mk"


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _dispatch(
    tmp_path: Path, target: str, values: dict[str, str], environment_overrides: bool,
) -> subprocess.CompletedProcess[str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "replace_text.py").write_bytes((ROOT / "scripts/replace_text.py").read_bytes())
    makefile = tmp_path / "text-targets.mk"
    makefile.write_text(f"include {SOURCE}\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"}
    }
    environment.update(
        PYTHONPATH=str(ROOT), FILE_PATH="ambient-wrong.txt",
        TEXT_B64=_encoded("ambient wrong"), OLD_B64=_encoded("ambient old"),
        NEW_B64=_encoded("ambient new"), GLUDD_TEXT_FILE_PATH="ambient-wrong.txt",
        GLUDD_TEXT_WRITE_B64=_encoded("ambient wrong"),
        GLUDD_TEXT_OLD_B64=_encoded("ambient old"), GLUDD_TEXT_NEW_B64=_encoded("ambient new"),
    )
    return subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if environment_overrides else []),
         "-f", str(makefile), target, "PYTHON=" + sys.executable,
         *(f"{name}={value}" for name, value in values.items())],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("target,field", [
    ("write-text-b64", "TEXT_B64"),
    ("replace-text-b64", "OLD_B64"),
    ("replace-text-b64", "NEW_B64"),
])
@pytest.mark.parametrize("encoded,diagnostic", [
    ("YmV0YQ==!", "base64"), ("/w==", "UTF-8"), ("YmUAdGE=", "NUL"),
])
@pytest.mark.parametrize("existing", [False, True])
def test_production_targets_validate_before_destination_io(
    tmp_path: Path, environment_overrides: bool, target: str,
    field: str, encoded: str, diagnostic: str, existing: bool,
) -> None:
    destination = tmp_path / ("report.txt" if existing else "missing/report.txt")
    if existing:
        destination.write_bytes(b"alpha\r\n")
        destination.chmod(0o640)
    inode = destination.stat().st_ino if existing else None
    values = {
        "FILE": str(destination.relative_to(tmp_path)), "TEXT_B64": _encoded("beta"),
        "OLD_B64": _encoded("alpha"), "NEW_B64": _encoded("beta"),
    }
    values[field] = encoded
    result = _dispatch(tmp_path, target, values, environment_overrides)
    assert result.returncode != 0 and diagnostic in result.stderr
    if existing:
        assert destination.read_bytes() == b"alpha\r\n"
        assert destination.stat().st_ino == inode
        assert destination.stat().st_mode & 0o777 == 0o640
    else:
        assert not destination.parent.exists()
    assert not (tmp_path / "ambient-wrong.txt").exists()


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("target,field", [
    ("write-text-b64", "TEXT_B64"),
    ("replace-text-b64", "OLD_B64"),
    ("replace-text-b64", "NEW_B64"),
])
@pytest.mark.parametrize("payload", [
    "$(shell touch MAKE_TRIPWIRE)", "`touch SHELL_TRIPWIRE`",
])
def test_malformed_transport_is_literal_data(
    tmp_path: Path, environment_overrides: bool, target: str, field: str, payload: str,
) -> None:
    destination = tmp_path / "report.txt"
    destination.write_bytes(b"alpha")
    values = {
        "FILE": destination.name, "TEXT_B64": _encoded("beta"),
        "OLD_B64": _encoded("alpha"), "NEW_B64": _encoded("beta"),
    }
    values[field] = payload
    result = _dispatch(tmp_path, target, values, environment_overrides)
    assert not (tmp_path / "MAKE_TRIPWIRE").exists()
    assert not (tmp_path / "SHELL_TRIPWIRE").exists()
    assert result.returncode != 0 and "base64" in result.stderr
    assert destination.read_bytes() == b"alpha"


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("target", ["write-text-b64", "replace-text-b64"])
def test_valid_unicode_preserves_native_bytes_and_file_mode(
    tmp_path: Path, environment_overrides: bool, target: str,
) -> None:
    destination = tmp_path / "quoted ' report.txt"
    destination.write_bytes(b"alpha\r\n")
    destination.chmod(0o640)
    inode = destination.stat().st_ino
    value = "caf\u00e9 \U0001f680\r\n"
    values = {
        "FILE": destination.name, "TEXT_B64": _encoded(value),
        "OLD_B64": _encoded("alpha\r\n"), "NEW_B64": _encoded(value),
    }
    result = _dispatch(tmp_path, target, values, environment_overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = value if target == "write-text-b64" else value.replace("\r\n", "\n")
    assert destination.read_bytes() == expected.encode("utf-8")
    assert destination.stat().st_ino == inode
    assert destination.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("target,field", [
    ("write-text-b64", "TEXT_B64"),
    ("replace-text-b64", "OLD_B64"),
    ("replace-text-b64", "NEW_B64"),
])
def test_make_retains_required_nonempty_inputs(tmp_path: Path, target: str, field: str) -> None:
    destination = tmp_path / "report.txt"
    destination.write_bytes(b"alpha")
    values = {
        "FILE": destination.name, "TEXT_B64": _encoded("beta"),
        "OLD_B64": _encoded("alpha"), "NEW_B64": _encoded("beta"),
    }
    values[field] = ""
    result = _dispatch(tmp_path, target, values, False)
    assert result.returncode != 0 and "Usage:" in result.stdout
    assert destination.read_bytes() == b"alpha"


@pytest.mark.parametrize("content", ["missing", "alpha alpha"])
def test_make_retains_exactly_one_match(tmp_path: Path, content: str) -> None:
    destination = tmp_path / "report.txt"
    destination.write_text(content, encoding="utf-8")
    result = _dispatch(tmp_path, "replace-text-b64", {
        "FILE": destination.name, "OLD_B64": _encoded("alpha"), "NEW_B64": _encoded("beta"),
    }, False)
    assert result.returncode != 0 and "ERROR:" in result.stderr
    assert destination.read_text(encoding="utf-8") == content
