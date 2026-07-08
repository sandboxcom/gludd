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


_LEAKY_ENV_VARS: frozenset[str] = frozenset({
    "GLUDD_PSK",
    "GLUDD_REQUIRE_AUTH",
    "GLUDD_ALLOW_NO_AUTH",
    "GLUDD_WEB_FETCH_ALLOWED_DOMAINS",
    "DELETION_REASON",
    "GLUDD_DELETION_GATE_THRESHOLD",
    "GL_CONFIG_DIR",
    "DATABASE_URL",
    "GLUDD_MT_BACKLOG",
    "ZAI_API_KEY",
    "ZAI_BASE_URL",
})


@pytest.fixture(autouse=True)
def _restore_leaky_env_vars():
    """Snapshot and restore leaky env vars around every test.

    Backstop for tests that still mutate ``os.environ`` directly (or via
    ``monkeypatch.setenv``). Guarantees that none of the known process-global
    config knobs leak from one test into a sibling on the same xdist worker,
    even if a test forgets to clean up. Pairs with
    ``scripts/check_test_env_writes.py`` which forbids new bare
    ``os.environ[...] =`` writes in ``tests/``.
    """
    snap = {k: os.environ.get(k) for k in _LEAKY_ENV_VARS}
    yield
    for k, original in snap.items():
        if original is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = original


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """Snapshot and restore the ENTIRE logging state around every test.

    Prevents caplog pollution: tests that mutate any logger's level,
    propagate flag, or handler list (without restoring) leak into sibling
    tests on the same xdist worker. The root-only snapshot was insufficient
    because named child loggers (``general_ludd.secrets.migration``,
    ``general_ludd.models.model_registry``, etc.) carry their own state that
    doesn't reset when root is restored.

    Implements the durable fix from
    ``docs/audit/CI_GREEN_PLAN_2026-07-01.md`` Appendix A6: snapshot the
    root logger, every existing named logger in
    ``logging.Logger.manager.loggerDict`` (skipping ``PlaceHolder`` entries),
    and the global ``logging.disable`` level at test entry; restore them
    wholesale at exit. Loggers created *during* the test are reset to
    defaults (``NOTSET`` level, ``propagate=True``, no handlers) so they
    cannot pollute later tests either.
    """
    root = logging.getLogger()
    snap_root = (root.level, root.propagate, list(root.handlers))

    manager = logging.Logger.manager
    logger_dict = manager.loggerDict
    snap_named: dict[str, tuple[int, bool, list[logging.Handler]]] = {}
    for name, logger in logger_dict.items():
        if isinstance(logger, logging.PlaceHolder):
            continue
        snap_named[name] = (logger.level, logger.propagate, list(logger.handlers))

    snap_disable = manager.disable if hasattr(manager, "disable") else 0

    yield

    root.level, root.propagate, root.handlers[:] = snap_root

    for name, (level, propagate, handlers) in snap_named.items():
        logger = logger_dict.get(name)
        if isinstance(logger, logging.Logger):
            logger.level = level
            logger.propagate = propagate
            logger.handlers[:] = handlers

    if hasattr(manager, "disable"):
        manager.disable = snap_disable

    for new_name in set(logger_dict.keys()) - set(snap_named.keys()):
        new_logger = logger_dict.get(new_name)
        if isinstance(new_logger, logging.Logger):
            new_logger.level = logging.NOTSET
            new_logger.propagate = True
            new_logger.handlers.clear()


@pytest.fixture(autouse=True)
def _reset_process_registry():
    """Reset the process.registry._DEFAULT_REGISTRY singleton around every test.

    Prevents order-dependent failures when tests register managed PIDs into
    the singleton and a later test asserts on registry contents. Implements
    the CI_GREEN_PLAN_2026-07-01.md A2 fix that was claimed-but-never-landed.
    """
    import general_ludd.process.registry as pr
    original = pr._DEFAULT_REGISTRY
    pr._DEFAULT_REGISTRY = None
    yield
    pr._DEFAULT_REGISTRY = original


@pytest.fixture(autouse=True)
def _reset_language_parsers():
    """Clear the _LANGUAGE_PARSERS cache around every test.

    Prevents the MagicMock injected by test_returns_cached_parser
    (tests/unit/test_extractor_coverage.py:15-19) from leaking into sibling
    tests that call extract_blocks without mocking _get_parser
    (test_code_intelligence.py, test_code_intel_adversarial.py,
    test_daemon_endpoint_coverage.py). The setup_method in TestGetParser only
    clears the cache for tests inside that class; this autouse fixture covers
    every test in the suite.
    """
    import general_ludd.code_intelligence.extractor as ex
    ex._LANGUAGE_PARSERS.clear()
    yield
    ex._LANGUAGE_PARSERS.clear()


@pytest.fixture(autouse=True)
def _reset_worker_runner():
    """Reset the worker.app._runner singleton around every test.

    Prevents the 2026-06-21 xdist flake (documented in SESSION_HANDOFF) where
    a test that creates an AnsibleRunnerAdapter via get_runner() leaks the
    instance into sibling tests. Hoists the per-file _reset_runner pattern
    from test_worker_d09_d10_d35.py + test_worker_tool_dispatch.py.
    """
    import general_ludd.worker.app as wapp
    original = wapp._runner
    wapp._runner = None
    yield
    wapp._runner = original


@pytest.fixture(autouse=True)
def _reset_observability_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset observability + integrity module-level singletons around every test.

    Prevents accumulation of token history, duration samples, metric
    registrations, and cached integrity keys across tests on the same xdist
    worker. Without this, ``test_default_token_tracker_is_shared`` populates
    ``_shared_tracker`` and a sibling asserting on a fresh tracker sees stale
    state; likewise ``test_core_changes_commit`` caches ``_INTEGRITY_KEY`` and
    leaks the cache key into tests expecting a cold start. Covers P6-P9 from
    the cross-test pollution audit.

    P9 note: ``metrics_exporter`` carries a paired singleton — the module-level
    ``_REGISTRY`` that ``MetricsExporter.__init__`` registers the uptime Gauge
    into. Resetting ``_metrics_exporter`` alone forces each test to rebuild the
    exporter, which then collides with the previous Gauge registration
    (``Duplicated timeseries``). The registry must be reset alongside the
    exporter or the leak simply moves one layer deeper.
    """
    import general_ludd.integrity.scanner as scanner
    monkeypatch.setattr(scanner, "_INTEGRITY_KEY", None, raising=False)

    import general_ludd.observability.token_cost as tc
    monkeypatch.setattr(tc, "_shared_tracker", None, raising=False)

    import general_ludd.observability.timing as timing
    monkeypatch.setattr(timing, "_default_tracker", None, raising=False)

    from prometheus_client import CollectorRegistry

    import general_ludd.observability.metrics_exporter as me
    monkeypatch.setattr(me, "_metrics_exporter", None, raising=False)
    monkeypatch.setattr(me, "_current_trace_id", {}, raising=False)
    monkeypatch.setattr(
        me, "_REGISTRY", CollectorRegistry(auto_describe=False), raising=False
    )


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
