"""Behavioral coverage for daemon startup from a frozen executable."""

from __future__ import annotations

import subprocess
import sys

from general_ludd import cli


def test_frozen_daemon_command_reexecutes_the_bundle(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", "/opt/gludd")

    command = cli._build_daemon_start_cmd(
        host="127.0.0.1",
        port=8765,
        workers=1,
    )

    assert command[:2] == ["/opt/gludd", cli._BUNDLED_GUNICORN_FLAG]
    assert command[2] == "general_ludd.daemon:create_daemon_app()"
    assert command[-1] == "127.0.0.1:8765"


def test_bundled_gunicorn_bootstrap_rewrites_argv(monkeypatch) -> None:
    from gunicorn.app import wsgiapp

    observed: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "/opt/gludd",
            cli._BUNDLED_GUNICORN_FLAG,
            "general_ludd.daemon:create_daemon_app()",
            "--bind",
            "127.0.0.1:8765",
        ],
    )
    monkeypatch.setattr(wsgiapp, "run", lambda: observed.append(list(sys.argv)))

    assert cli._run_bundled_gunicorn_if_requested() is True
    assert observed == [
        [
            "/opt/gludd",
            "general_ludd.daemon:create_daemon_app()",
            "--bind",
            "127.0.0.1:8765",
        ]
    ]


def test_source_runtime_does_not_accept_bundled_bootstrap(monkeypatch) -> None:
    monkeypatch.delattr(cli.sys, "frozen", raising=False)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["/usr/bin/python", cli._BUNDLED_GUNICORN_FLAG],
    )

    assert cli._run_bundled_gunicorn_if_requested() is False


def test_frozen_child_inherits_logs_but_source_child_stays_quiet(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    assert cli._daemon_child_stdio() == (None, None)

    monkeypatch.delattr(cli.sys, "frozen", raising=False)
    assert cli._daemon_child_stdio() == (
        subprocess.DEVNULL,
        subprocess.DEVNULL,
    )
