"""Regression coverage for detached daemon ownership in unit tests."""

from __future__ import annotations

import sys

import pytest


def test_unowned_gunicorn_spawn_is_denied_before_process_creation() -> None:
    with pytest.raises(RuntimeError, match="unowned Gunicorn"):
        sys.audit(
            "subprocess.Popen",
            "gunicorn",
            ["gunicorn", "general_ludd.daemon:create_daemon_app()"],
            None,
            None,
        )
