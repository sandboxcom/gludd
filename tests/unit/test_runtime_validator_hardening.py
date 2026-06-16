"""Hardening tests for runtime.validator subprocess argv construction.

Proves that injection-shaped project_root / package args are rejected before
any subprocess call, and that legitimate calls build the exact expected argv
(list form, with ``--`` separating the positional path).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runtime.profile import RuntimeProfile
from general_ludd.runtime.validator import (
    PathArgError,
    RuntimeValidator,
    _validate_path_arg,
)

# ---------------------------------------------------------------------------
# path/package arg validation
# ---------------------------------------------------------------------------

INJECTION_PATH_ARGS = [
    "-rf",  # leading dash -> flag injection
    "--system",  # leading double dash
    "/proj;rm -rf /",  # ;
    "/proj && curl evil",  # &&
    "/proj | sh",  # pipe
    "/proj`id`",  # backtick
    "/proj$(id)",  # command substitution
    "/proj$HOME",  # var expansion
    "/proj\nrm",  # newline
    "/proj\tx",  # tab
    "/proj x",  # whitespace
    "/proj>out",  # redirect
    "/proj'q",  # single quote
    '/proj"q',  # double quote
    "",  # empty
    "   ",  # whitespace only
]


@pytest.mark.parametrize("bad", INJECTION_PATH_ARGS)
def test_validate_path_arg_rejects_injection(bad: str) -> None:
    with pytest.raises(PathArgError):
        _validate_path_arg(bad)


VALID_PATH_ARGS = [
    ".",
    "/home/user/project",
    "./subdir/proj",
    "../sibling",
    "/opt/app-1.2",
    "relative/path_name",
]


@pytest.mark.parametrize("good", VALID_PATH_ARGS)
def test_validate_path_arg_accepts_normal(good: str) -> None:
    assert _validate_path_arg(good) == good


# ---------------------------------------------------------------------------
# native_uv argv construction + rejection
# ---------------------------------------------------------------------------

def test_validate_native_uv_rejects_injection_without_subprocess() -> None:
    profile = RuntimeProfile(
        runtime_profile_id="p", mode="native_uv", project_root="/proj;rm -rf /"
    )
    validator = RuntimeValidator()
    with patch("general_ludd.runtime.validator.subprocess.run") as mrun:
        result = validator.validate_native_uv(profile)
        mrun.assert_not_called()
    assert result.valid is False
    assert any("project_root" in e or "path" in e.lower() for e in result.errors)


def test_validate_native_uv_builds_expected_argv() -> None:
    profile = RuntimeProfile(
        runtime_profile_id="p", mode="native_uv", project_root="/home/me/proj"
    )
    validator = RuntimeValidator()
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("general_ludd.runtime.validator.subprocess.run", return_value=fake) as mrun:
        validator.validate_native_uv(profile)
    argv = mrun.call_args.args[0]
    assert argv == ["uv", "sync", "--dry-run", "--project", "--", "/home/me/proj"]


# ---------------------------------------------------------------------------
# native_pip argv construction + rejection
# ---------------------------------------------------------------------------

def test_validate_native_pip_rejects_injection_without_subprocess() -> None:
    profile = RuntimeProfile(
        runtime_profile_id="p", mode="native_pip", project_root="--target=/etc"
    )
    validator = RuntimeValidator()
    with patch("general_ludd.runtime.validator.subprocess.run") as mrun:
        result = validator.validate_native_pip(profile)
        mrun.assert_not_called()
    assert result.valid is False


def test_validate_native_pip_builds_expected_argv() -> None:
    profile = RuntimeProfile(
        runtime_profile_id="p", mode="native_pip", project_root="/home/me/proj"
    )
    validator = RuntimeValidator()
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("general_ludd.runtime.validator.subprocess.run", return_value=fake) as mrun:
        validator.validate_native_pip(profile)
    argv = mrun.call_args.args[0]
    assert argv == ["pip", "install", "--dry-run", "-e", "--", "/home/me/proj"]
