"""Argv-injection hardening for PipBundleBuilder.

PipBundleBuilder.build() interpolates the caller-supplied ``output_dir`` into
the ``uv build --out-dir <output_dir>`` argv (and runs ``os.makedirs`` on it).
``output_dir`` is config/operator-derived (it flows in untrusted via
``release_orchestrator.build_and_validate_release`` -> ``make release-validate``).

The subprocess calls already use the safe contract — argv **list-form**, never a
string, and never ``shell=True`` — so there is no classic shell-injection sink.
But that alone does NOT protect against:

  * leading-dash values (``--config=...``) being smuggled as *flags* into uv
    (argv injection / flag smuggling),
  * shell metacharacters / NUL / newlines slipping through if the call site is
    ever refactored to a shell form,
  * ``..`` path-traversal components escaping the intended artifacts root.

These tests pin BOTH halves of the contract:

  1. injection-y ``output_dir`` values are REJECTED before any subprocess/makedirs;
  2. a normal build still produces the expected list-form argv with shell=False,
     and the ``git rev-parse HEAD`` argv is the fixed internal-constant list.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runtime.pip_bundle import PipBundleBuilder


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# 1. Injection-y output_dir values are rejected (fail closed, no side effects).
# ---------------------------------------------------------------------------

INJECTION_DIRS = [
    "--out-dir=/etc",            # leading-dash: flag smuggling into uv
    "-rf",                       # leading-dash short flag
    "--config-file=/tmp/evil",   # leading-dash long flag with value
    "dist; rm -rf /",            # shell metachar: command separator
    "dist && curl evil.sh",      # shell metachar: chaining
    "dist | tee /etc/passwd",    # shell metachar: pipe
    "dist`whoami`",              # shell metachar: backtick
    "dist$(id)",                 # shell metachar: command substitution
    "dist\nrm -rf /",            # embedded newline
    "dist\x00hidden",            # embedded NUL
    "../../etc",                 # path traversal
    "build/../../../root",       # traversal mid-path
]


@pytest.mark.parametrize("bad_dir", INJECTION_DIRS)
@patch("general_ludd.runtime.pip_bundle.os.makedirs")
@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_injection_output_dir_rejected(
    mock_run: MagicMock, mock_makedirs: MagicMock, bad_dir: str
) -> None:
    builder = PipBundleBuilder()
    with pytest.raises(ValueError):
        builder.build(bad_dir, "1.0.0")
    # Fail closed: nothing was spawned and no directory was created.
    mock_run.assert_not_called()
    mock_makedirs.assert_not_called()


@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_empty_output_dir_rejected(mock_run: MagicMock) -> None:
    builder = PipBundleBuilder()
    with pytest.raises(ValueError):
        builder.build("   ", "1.0.0")
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Safe-contract regression: normal build builds the expected list-form argv,
#    shell=False, and git rev-parse argv is the fixed internal-constant list.
# ---------------------------------------------------------------------------


@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_normal_build_argv_is_safe_list_form(mock_run: MagicMock, tmp_path: Path) -> None:
    (tmp_path / "pkg-1.0.0-py3-none-any.whl").write_bytes(b"w")

    calls: list[dict] = []

    def side_effect(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if "uv" in cmd:
            return _make_completed(returncode=0)
        if "git" in cmd:
            return _make_completed(returncode=0, stdout="abc123\n")
        return _make_completed()

    mock_run.side_effect = side_effect

    out = str(tmp_path)
    result = PipBundleBuilder().build(out, "1.0.0")
    assert result.success is True

    # uv build argv: list-form, exact, with the caller dir as the --out-dir VALUE.
    uv_call = next(c for c in calls if "uv" in c["cmd"])
    assert uv_call["cmd"] == ["uv", "build", "--out-dir", out]
    assert isinstance(uv_call["cmd"], list)
    # Never shell=True (default is False, and must never be passed True).
    assert uv_call["kwargs"].get("shell", False) is False

    # git rev-parse argv: fixed internal constants only — no interpolation.
    git_call = next(c for c in calls if "git" in c["cmd"])
    assert git_call["cmd"] == ["git", "rev-parse", "HEAD"]
    assert git_call["kwargs"].get("shell", False) is False


@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_no_subprocess_call_uses_shell_true(mock_run: MagicMock, tmp_path: Path) -> None:
    (tmp_path / "pkg-1.0.0.tar.gz").write_bytes(b"s")

    def side_effect(cmd, **kwargs):
        # Hard-assert the contract on EVERY spawn the builder makes.
        assert isinstance(cmd, list), f"argv must be list-form, got {type(cmd)}"
        assert kwargs.get("shell", False) is False, "shell=True is forbidden"
        if "uv" in cmd:
            return _make_completed(returncode=0)
        if "git" in cmd:
            return _make_completed(returncode=0, stdout="deadbeef\n")
        return _make_completed()

    mock_run.side_effect = side_effect

    result = PipBundleBuilder().build(str(tmp_path), "2.0.0")
    assert result.success is True


@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_absolute_normal_dir_is_accepted(mock_run: MagicMock, tmp_path: Path) -> None:
    """Absolute, well-formed dirs (the normal release flow) must NOT be rejected."""
    nested = tmp_path / "artifacts" / "dist"

    def side_effect(cmd, **kwargs):
        if "uv" in cmd:
            return _make_completed(returncode=0)
        if "git" in cmd:
            return _make_completed(returncode=0, stdout="cafe\n")
        return _make_completed()

    mock_run.side_effect = side_effect

    result = PipBundleBuilder().build(str(nested), "3.0.0")
    assert result.success is True
    assert Path(nested).is_dir()


def test_timeout_on_uv_build_still_propagates(tmp_path: Path) -> None:
    """Sanity: validation happens before spawn, not by swallowing real errors."""
    with patch("general_ludd.runtime.pip_bundle.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["uv", "build"], timeout=120)
        with pytest.raises(subprocess.TimeoutExpired):
            PipBundleBuilder().build(str(tmp_path), "1.0.0")
