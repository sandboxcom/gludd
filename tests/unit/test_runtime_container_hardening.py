"""Hardening tests for runtime.container subprocess argv construction.

These prove that injection-shaped image refs / runtime names are rejected
before any subprocess call, and that legitimate calls build the exact
expected argv (list form, with ``--`` separating positionals).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runtime.container import (
    ContainerBuilder,
    ImageRefError,
    RuntimeNameError,
    _validate_image_ref,
    _validate_runtime_name,
)

# ---------------------------------------------------------------------------
# image ref validation
# ---------------------------------------------------------------------------

INJECTION_IMAGE_REFS = [
    "-rm",  # leading dash -> looks like a flag
    "--force",  # leading double dash
    "foo;rm -rf /",  # shell metachar ;
    "foo && rm -rf /",  # &&
    "foo | cat",  # pipe
    "foo`whoami`",  # backtick
    "foo$(whoami)",  # command substitution
    "foo$bar",  # var expansion
    "foo\nbar",  # newline injection
    "foo\tbar",  # tab / whitespace injection
    "foo bar",  # space
    "foo>out",  # redirect
    "foo<in",  # redirect
    "foo'quote",  # single quote
    'foo"quote',  # double quote
    "foo\\bar",  # backslash
    "",  # empty
    "   ",  # whitespace only
]


@pytest.mark.parametrize("bad", INJECTION_IMAGE_REFS)
def test_validate_image_ref_rejects_injection(bad: str) -> None:
    with pytest.raises(ImageRefError):
        _validate_image_ref(bad)


VALID_IMAGE_REFS = [
    "myimage",
    "myimage:latest",
    "myimage:1.2.3",
    "registry.example.com/team/myimage:v1",
    "ghcr.io/org/repo:sha-abc123",
    "localhost:5000/img:tag",
    "a/b/c:1.0",
]


@pytest.mark.parametrize("good", VALID_IMAGE_REFS)
def test_validate_image_ref_accepts_normal(good: str) -> None:
    assert _validate_image_ref(good) == good


# ---------------------------------------------------------------------------
# runtime name validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    ["-podman", "pod;man", "pod man", "pod|man", "pod$(x)", "", "pod\nman"],
)
def test_validate_runtime_name_rejects_injection(bad: str) -> None:
    with pytest.raises(RuntimeNameError):
        _validate_runtime_name(bad)


@pytest.mark.parametrize("good", ["podman", "docker", "nerdctl"])
def test_validate_runtime_name_accepts_normal(good: str) -> None:
    assert _validate_runtime_name(good) == good


# ---------------------------------------------------------------------------
# build_image argv construction + rejection
# ---------------------------------------------------------------------------

def test_build_image_rejects_injection_image_ref_without_subprocess() -> None:
    builder = ContainerBuilder()
    with patch("general_ludd.runtime.container.subprocess.run") as mrun:
        result = builder.build_image("/ctx", "foo;rm -rf /")
        mrun.assert_not_called()
    assert result.success is False
    assert "image" in result.logs.lower()


def test_build_image_rejects_injection_runtime_without_subprocess() -> None:
    builder = ContainerBuilder()
    with patch("general_ludd.runtime.container.subprocess.run") as mrun:
        result = builder.build_image("/ctx", "img:latest", runtime="pod;man")
        mrun.assert_not_called()
    assert result.success is False


def test_build_image_builds_expected_argv() -> None:
    builder = ContainerBuilder()
    fake = MagicMock(returncode=0, stdout="sha256:deadbeef\n", stderr="")
    with patch("general_ludd.runtime.container.subprocess.run", return_value=fake) as mrun:
        builder.build_image("/ctx", "myimage:latest", runtime="podman")
    argv = mrun.call_args.args[0]
    assert argv == ["podman", "build", "-t", "myimage:latest", "--", "/ctx"]


# ---------------------------------------------------------------------------
# validate_image argv construction + rejection
# ---------------------------------------------------------------------------

def test_validate_image_rejects_injection_without_subprocess() -> None:
    builder = ContainerBuilder()
    with patch("general_ludd.runtime.container.subprocess.run") as mrun:
        result = builder.validate_image("--force")
        mrun.assert_not_called()
    assert result.valid is False


def test_validate_image_builds_expected_argv() -> None:
    builder = ContainerBuilder()
    fake = MagicMock(returncode=0, stdout='[{"Config": {}, "Size": 0}]', stderr="")
    with patch("general_ludd.runtime.container.subprocess.run", return_value=fake) as mrun:
        builder.validate_image("myimage:latest", runtime="docker")
    argv = mrun.call_args.args[0]
    assert argv == ["docker", "inspect", "--", "myimage:latest"]
