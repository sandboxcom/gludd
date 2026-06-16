"""Argument-injection hardening for ansible-galaxy search/install (#70).

These tests prove that collection/role names and versions are validated
before being placed into the ansible-galaxy argv, that option injection
(leading dash), shell metacharacters and whitespace are rejected, and that
the argv stays a list with ``--`` separating positional args. subprocess is
mocked so no real ansible-galaxy / network is ever invoked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from general_ludd.ansible import galaxy


def _ok_run(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> mock.Mock:
    return mock.Mock(
        return_value=SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    )


# --------------------------------------------------------------------------- #
# install_galaxy — happy path                                                 #
# --------------------------------------------------------------------------- #


def test_install_accepts_normal_collection_with_version() -> None:
    run = _ok_run()
    with mock.patch.object(galaxy.subprocess, "run", run):
        result = galaxy.install_galaxy("namespace.collection:1.2.3", galaxy_type="collection")

    assert result["success"] is True
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert isinstance(argv, list), "argv must be a list, never a shell string"
    assert argv[0] == "ansible-galaxy"
    # positional args (the name spec) must come after a -- separator
    assert "--" in argv
    sep = argv.index("--")
    assert "namespace.collection:1.2.3" in argv[sep + 1 :]


def test_install_accepts_plain_name_without_version() -> None:
    run = _ok_run()
    with mock.patch.object(galaxy.subprocess, "run", run):
        result = galaxy.install_galaxy("namespace.role")
    assert result["success"] is True


# --------------------------------------------------------------------------- #
# install_galaxy — injection rejection                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "evil",
    [
        "-r /etc/passwd",  # option injection via -r requirements file
        "--force",  # bare long option
        "-c",  # bare short option / leading dash
        "ns.name; rm -rf /",  # command separator
        "ns.name && cat /etc/shadow",  # logical-and chaining
        "ns.name | tee x",  # pipe
        "ns.name`whoami`",  # backtick subshell
        "ns.name$(id)",  # dollar subshell
        "ns name",  # internal whitespace
        "ns.name\trole",  # tab whitespace
        "ns.name\nrole",  # newline
        "NoNamespaceDot",  # missing namespace.name structure
        ".leadingdot",  # malformed
        "ns..name",  # empty segment
        "",  # empty
        "ns.name:not a version",  # whitespace in version
        "ns.name:1.2.3; rm",  # metachar in version
        "ns.name:-1",  # version starting with dash
    ],
)
def test_install_rejects_injection(evil: str) -> None:
    run = _ok_run()
    with mock.patch.object(galaxy.subprocess, "run", run), pytest.raises(ValueError):
        galaxy.install_galaxy(evil, galaxy_type="collection")
    run.assert_not_called()  # must reject BEFORE ever spawning a process


# --------------------------------------------------------------------------- #
# search_galaxy — injection rejection + happy path                            #
# --------------------------------------------------------------------------- #


def test_search_accepts_normal_query() -> None:
    run = _ok_run(stdout="")
    with mock.patch.object(galaxy.subprocess, "run", run):
        galaxy.search_galaxy("nginx", galaxy_type="role")
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert isinstance(argv, list)
    assert "--" in argv


@pytest.mark.parametrize(
    "evil",
    [
        "-r /etc/passwd",
        "--init",
        "foo; rm -rf /",
        "foo && id",
        "foo`whoami`",
        "foo$(id)",
        "foo\nbar",
    ],
)
def test_search_rejects_injection(evil: str) -> None:
    run = _ok_run()
    with mock.patch.object(galaxy.subprocess, "run", run), pytest.raises(ValueError):
        galaxy.search_galaxy(evil)
    run.assert_not_called()


# --------------------------------------------------------------------------- #
# galaxy_type itself must not be injectable                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_type", ["-x", "role; rm", "collection ", "playbook"])
def test_install_rejects_bad_galaxy_type(bad_type: str) -> None:
    run = _ok_run()
    with mock.patch.object(galaxy.subprocess, "run", run), pytest.raises(ValueError):
        galaxy.install_galaxy("ns.name", galaxy_type=bad_type)
    run.assert_not_called()
