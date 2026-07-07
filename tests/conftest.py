"""Shared pytest configuration for the test suite.

Does three things that previously required per-file ``# noqa`` suppressions
or per-test ``os.environ`` patches:

1. Adds the repo's ``scripts/`` and ``src/`` directories to ``sys.path`` so
   test modules can import helper scripts (``classify_agent_error``,
   ``gen_status_table``, ``multitasking_backlog_check``,
   ``check_tf_provider_versions``) without each needing their own
   ``sys.path`` manipulation (which was suppressed with ``# noqa: E402``).

2. Eagerly imports ``general_ludd.routing_roles`` to warm a pre-existing
   repo-wide import cycle (``schemas.benchmark -> routing_roles.weights ->
   schemas.benchmark.TaskType``) so that gateway-importing test modules
   collect in any order without ``ImportError``. Previously each such module
   needed its own ``import general_ludd.routing_roles  # noqa: F401`` line.

3. Sets ``GLUDD_ALLOW_NO_AUTH=1`` for the duration of the test suite so the
   daemon's fail-closed auth middleware (daemon.py:2270-2303) does not return
   HTTP 503 on every non-public path when no PSK is configured. Individual
   tests that exercise the auth layer explicitly override this via their own
   ``monkeypatch.setenv`` (see test_w5_6_worker_auth.py,
   test_worker_redteam.py, test_environment_e2e.py).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_SRC_DIR = _REPO_ROOT / "src"

for _path in (str(_SCRIPTS_DIR), str(_SRC_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

importlib.import_module("general_ludd.routing_roles")


@pytest.fixture(autouse=True)
def _allow_no_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permit non-public daemon/worker endpoints during tests when no PSK is set.

    The production daemon is fail-closed: ``GLUDD_PSK`` unset + no opt-out ⇒
    every non-public path returns 503 (daemon.py:2270-2303, worker/app.py via
    security/auth.py). Tests that don't care about auth should not have to
    each ``monkeypatch.setenv`` to bypass it. Tests that DO exercise the auth
    layer override this by setting ``GLUDD_PSK`` or unsetting the env var
    inside their own ``monkeypatch.setenv`` calls.
    """
    if "GLUDD_PSK" not in os.environ:
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
