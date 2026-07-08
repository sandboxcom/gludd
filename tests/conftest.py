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
import logging
import os
import shutil
import socket
import sys
from pathlib import Path

import pytest

# Python 3.14: yaml C extension (CSafeLoader) fails parsing ansible config.
# Force-load yaml first, then strip C classes so ansible falls back to
# the pure-Python SafeLoader via its existing (ImportError, AttributeError) catch.
import yaml as _yaml_mod

for _name in ("CSafeLoader", "CSafeDumper", "CParser"):
    _yaml_mod.__dict__.pop(_name, None)
del _yaml_mod, _name

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
    if not os.environ.get("GLUDD_PSK", "").strip():
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """Snapshot and restore the root logger around every test.

    Prevents caplog pollution: tests that mutate the root logger's level,
    propagate flag, or handler list (without restoring) leak into sibling
    tests on the same xdist worker, causing caplog assertions to fail
    nondeterministically.
    """
    root = logging.getLogger()
    snap_level = root.level
    snap_propagate = root.propagate
    snap_handlers = list(root.handlers)
    yield
    root.level = snap_level
    root.propagate = snap_propagate
    root.handlers[:] = snap_handlers


# --- Environmental test-skip probes -----------------------------------------
#
# Integration tests that require SLURM or PostgreSQL can decorate themselves
# with ``requires_slurm`` / ``requires_postgres`` (imported from this module)
# so the local gate skips them unless the service is actually reachable. This
# avoids forcing every developer to run both services just to get a green
# local gate; CI exports the env vars (or runs the services) to opt in.
#
# Override shortcuts for operators:
#   SLURM_AVAILABLE=1      treat SLURM as present without sbatch on PATH
#   POSTGRES_AVAILABLE=1   treat Postgres as present without a live :5432


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    """Return True iff a TCP connection to (host, port) succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


SLURM_AVAILABLE: bool = (
    os.environ.get("SLURM_AVAILABLE") == "1" or shutil.which("sbatch") is not None
)
POSTGRES_AVAILABLE: bool = (
    os.environ.get("POSTGRES_AVAILABLE") == "1" or _port_open("127.0.0.1", 5432)
)

requires_slurm = pytest.mark.skipif(
    not SLURM_AVAILABLE,
    reason="requires SLURM — set SLURM_AVAILABLE=1 or install sbatch",
)
requires_postgres = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason="requires PostgreSQL — set POSTGRES_AVAILABLE=1 or run postgres on :5432",
)
