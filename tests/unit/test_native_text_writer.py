"""Native text transport rejects corrupt input before destination mutation."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import replace_text as owner
else:
    from scripts import replace_text as owner

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "scripts/replace_text.py"
INVALID = [
    pytest.param("YmV0YQ==!", "base64", id="invalid-base64"),
    pytest.param("/w==", "UTF-8", id="invalid-utf8"),
    pytest.param("YmUAdGE=", "NUL", id="nul"),
]


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _run_b64(tmp_path: Path, target: Path, operation: str, *,
             text: str = "beta", old: str = "alpha", encoded: str | None = None,
             field: str = "new") -> subprocess.CompletedProcess[str]:
    """Exercise the approved existing-target dispatch through real Make/Python."""
    fixture = tmp_path / "text-owner.mk"
    fixture.write_text(
        ".PHONY: write-text-b64 replace-text-b64\n"
        "write-text-b64:\n"
        "\t@$(PYTHON) -c 'import os; from scripts.replace_text import write_text_b64; "
        'write_text_b64(os.environ["FILE_PATH"], os.environ["TEXT_B64"])' + "'\n"
        "replace-text-b64:\n"
        "\t@$(PYTHON) -c 'import os, sys; from scripts.replace_text import replace_text_b64; "
        'sys.exit(replace_text_b64(os.environ["FILE_PATH"], os.environ["OLD_B64"], os.environ["NEW_B64"]))'
        + "'\n",
        encoding="utf-8",
    )
    environment = dict(
        os.environ, PYTHONPATH=str(ROOT), FILE_PATH=str(target),
        TEXT_B64=encoded if encoded is not None else _encoded(text),
        OLD_B64=encoded if encoded is not None and field == "old" else _encoded(old),
        NEW_B64=encoded if encoded is not None and field == "new" else _encoded(text),
    )
    return subprocess.run(
        ["make", "--no-print-directory", "-f", str(fixture),
         operation + "-text-b64", "PYTHON=" + sys.executable],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False, timeout=10,
    )


@pytest.mark.parametrize(("encoded", "diagnostic"), INVALID)
@pytest.mark.parametrize("state", ["existing", "absent", "missing-parent"])
def test_write_rejects_invalid_input_before_filesystem_changes(
    tmp_path: Path, encoded: str, diagnostic: str, state: str,
) -> None:
    target = tmp_path / ("absent-parent/target.txt" if state == "missing-parent" else "target.txt")
    if state == "existing":
        target.write_bytes(b"retained\r\n")
        target.chmod(0o640)
    before = target.read_bytes() if target.exists() else None
    inode = target.stat().st_ino if target.exists() else None
    result = _run_b64(tmp_path, target, "write", encoded=encoded)
    assert result.returncode != 0 and diagnostic in result.stderr
    if before is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before and target.stat().st_ino == inode
        assert target.stat().st_mode & 0o777 == 0o640
    if state == "missing-parent":
        assert not target.parent.exists()


@pytest.mark.parametrize(("encoded", "diagnostic"), INVALID)
@pytest.mark.parametrize("field", ["old", "new"])
@pytest.mark.parametrize("state", ["existing", "missing-parent"])
def test_replace_rejects_invalid_input_before_destination_io(
    tmp_path: Path, encoded: str, diagnostic: str, field: str, state: str,
) -> None:
    target = tmp_path / ("absent-parent/target.txt" if state == "missing-parent" else "target.txt")
    if state == "existing":
        target.write_bytes(b"alpha\r\n")
        target.chmod(0o640)
    before = target.read_bytes() if target.exists() else None
    inode = target.stat().st_ino if target.exists() else None
    result = _run_b64(tmp_path, target, "replace", encoded=encoded, field=field)
    assert result.returncode != 0 and diagnostic in result.stderr
    if before is None:
        assert not target.parent.exists()
    else:
        assert target.read_bytes() == before and target.stat().st_ino == inode
        assert target.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("value", ["", "caf\u00e9 \U0001f680\r\n", "line\nnext\n"])
@pytest.mark.parametrize("existing", [False, True])
def test_write_preserves_valid_unicode_empty_bytes_and_mode(
    tmp_path: Path, value: str, existing: bool,
) -> None:
    target = tmp_path / "target.txt"
    if existing:
        target.write_text("old", encoding="utf-8")
        target.chmod(0o600)
    original_inode = target.stat().st_ino if existing else None
    result = _run_b64(tmp_path, target, "write", text=value)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == value.encode("utf-8")
    if existing:
        assert target.stat().st_ino == original_inode and target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("content", "old", "new", "expected"), [
    ("alpha\r\n", "alpha", "caf\u00e9 \U0001f680", "caf\u00e9 \U0001f680\n"),
    ("alpha", "alpha", "", ""),
    ("", "", "new", "new"),
    ("alpha\r\n", "alpha\r\n", "beta\r\n", "beta\n"),
])
def test_replace_preserves_exact_match_unicode_empty_and_newline_contract(
    tmp_path: Path, content: str, old: str, new: str, expected: str,
) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(content.encode("utf-8"))
    target.chmod(0o640)
    inode = target.stat().st_ino
    result = _run_b64(tmp_path, target, "replace", old=old, text=new)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == expected.encode("utf-8")
    assert target.stat().st_ino == inode and target.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("content", ["missing", "alpha alpha", "alpha"])
def test_replace_retains_absent_duplicate_and_empty_old_refusals(tmp_path: Path, content: str) -> None:
    target = tmp_path / "target.txt"
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()
    old = "" if content == "alpha" else "alpha"
    assert owner.replace_text_b64(target, _encoded(old), _encoded("beta")) == 1
    result = _run_b64(tmp_path, target, "replace", old=old)
    assert result.returncode == 2 and "Error 1" in result.stderr
    assert target.read_bytes() == before


@pytest.mark.parametrize("operation", ["write", "replace"])
def test_valid_input_retains_missing_parent_refusal(tmp_path: Path, operation: str) -> None:
    target = tmp_path / "missing-parent/target.txt"
    result = _run_b64(tmp_path, target, operation)
    assert result.returncode != 0
    assert not target.parent.exists()


@pytest.mark.parametrize("operation", ["write", "replace"])
def test_valid_text_retains_existing_symlink_and_referent_mode(
    tmp_path: Path, operation: str,
) -> None:
    referent = tmp_path / "referent.txt"
    referent.write_bytes(b"alpha")
    referent.chmod(0o640)
    target = tmp_path / "link.txt"
    target.symlink_to(referent)
    inode = referent.stat().st_ino
    result = _run_b64(tmp_path, target, operation)
    assert result.returncode == 0, result.stderr
    assert target.is_symlink() and referent.read_bytes() == b"beta"
    assert referent.stat().st_ino == inode and referent.stat().st_mode & 0o777 == 0o640


def test_legacy_cli_retains_usage_refusal() -> None:
    result = subprocess.run(
        [sys.executable, str(OWNER)], cwd=ROOT, capture_output=True,
        text=True, check=False, timeout=10,
    )
    assert result.returncode == 2 and "Usage:" in result.stderr


@pytest.mark.parametrize("field", ["target", "old", "new"])
def test_legacy_cli_refuses_nul_before_mutation(tmp_path: Path, field: str) -> None:
    paths = {name: tmp_path / (name + ".txt") for name in ("target", "old", "new")}
    for name, value in (("target", b"alpha"), ("old", b"alpha"), ("new", b"beta")):
        paths[name].write_bytes(value + (b"\x00" if name == field else b""))
    before = {name: path.read_bytes() for name, path in paths.items()}
    result = subprocess.run(
        [sys.executable, str(OWNER), *(str(paths[name]) for name in ("target", "old", "new"))],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode != 0 and "NUL" in result.stderr
    assert {name: path.read_bytes() for name, path in paths.items()} == before


@pytest.mark.parametrize("value", [b"alpha\x00", b"\xff"])
def test_base64_replace_refuses_invalid_existing_destination(tmp_path: Path, value: bytes) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(value)
    result = _run_b64(tmp_path, target, "replace")
    assert result.returncode != 0 and target.read_bytes() == value
