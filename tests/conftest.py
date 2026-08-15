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

4. Gives every Starlette ``TestClient`` the loopback peer address it actually
   is (``127.0.0.1``) instead of the un-parseable pseudo-host ``"testclient"``,
   so the daemon's CIDR allowlist (a real security control) can stay armed
   during tests instead of 403-ing the in-process transport.

5. Disables OpenCode's background gate-refresh autospawn inside tests. Tests
   invoke plugin constructors in fresh Node processes; those constructors must
   not start an unrelated full release gate. The singleflight contract test
   explicitly opts back in with a fake spawner.
"""

from __future__ import annotations

import functools
import importlib
import logging
import os
import shutil
import socket
import sys
import unittest.mock as _mock_mod
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import yaml as _yaml_mod

# Captured ONCE at module load so the fixture always restores to the
# canonical reference, never to a value that a prior test leaked onto
# the module attribute.
_CANONICAL_HTTPX_GET = httpx.get

# Python 3.14: yaml C extension (CSafeLoader) fails parsing ansible config.
# Force-load yaml first, then strip C classes so ansible falls back to
# the pure-Python SafeLoader via its existing (ImportError, AttributeError) catch.
for _name in ("CSafeLoader", "CSafeDumper", "CParser"):
    _yaml_mod.__dict__.pop(_name, None)
del _yaml_mod, _name

# Python 3.14: unittest.mock._is_async_obj triggers RecursionError when
# creating NonCallableMock(spec_set=_CODE_ATTRS) because
# inspect.iscoroutinefunction -> _has_code_flag -> _unwrap_partialmethod
# enters infinite recursion on mock objects.
# Workaround: wrap _is_async_obj to catch RecursionError.
_orig_is_async_obj = _mock_mod._is_async_obj


def _safe_is_async_obj(obj: object) -> bool:
    try:
        return _orig_is_async_obj(obj)
    except BaseException:
        return False


_mock_mod._is_async_obj = _safe_is_async_obj

# Python 3.14: pkg_resources was removed from the stdlib.
# The ``fs`` (pyfilesystem) package calls ``__import__("pkg_resources").declare_namespace``
# at module load time, which fails with ModuleNotFoundError.
# Provide a stub ``pkg_resources`` module that exposes a no-op
# ``declare_namespace`` so ``fs`` can import without error.
_FAKE_PKG_RESOURCES = type(sys)("pkg_resources")
_FAKE_PKG_RESOURCES.declare_namespace = lambda _name: None
sys.modules.setdefault("pkg_resources", _FAKE_PKG_RESOURCES)
del _FAKE_PKG_RESOURCES

# Path setup for test imports — must happen before importing project modules
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_SRC_DIR = _REPO_ROOT / "src"
for _p in (str(_SCRIPTS_DIR), str(_SRC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

importlib.import_module("general_ludd.routing_roles")

# The per-fixture-invocation snapshot pattern was the root cause of
# cross-test pollution: a corrupt teardown of test N left a mock/sentinel
# on ``httpx.get``, and test N+1's fixture snapshot captured and
# re-applied the corrupt value, making the pollution self-reinforcing.
# _CANONICAL_HTTPX_GET is captured above at import time.
_ab_compare = importlib.import_module("general_ludd.abtest.compare")
_CANONICAL_RUN_CANDIDATE = _ab_compare.run_candidate_in_subprocess


def _deny_unowned_unit_gunicorn(
    event: str,
    args: tuple[object, ...],
) -> None:
    """Deny a real Gunicorn exec from a unit test at the CPython audit boundary.

    Tests that exercise daemon launch semantics must mock ``Popen`` and own the
    returned process.  A real Gunicorn launch is detached by production code,
    so pytest cannot reap it when the worker exits.  An audit hook cannot be
    bypassed by a test restoring or replacing ``subprocess.Popen``.
    """
    if event != "subprocess.Popen" or not args:
        return
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not current_test.startswith("tests/unit/"):
        return
    try:
        executable = os.fsdecode(os.fspath(args[0]))
    except TypeError:
        return
    if Path(executable).name == "gunicorn":
        raise RuntimeError(
            "unit test attempted an unowned Gunicorn launch; mock "
            "subprocess.Popen and explicitly own the fake process"
        )


sys.addaudithook(_deny_unowned_unit_gunicorn)


def pytest_configure(config: pytest.Config) -> None:
    """Per-worker RLIMIT_AS backstop — DISABLED in CI.

    RLIMIT_AS breaks Node.js WASM (amaro TypeScript parser) on Linux
    because V8 requires a large contiguous virtual address space for
    WebAssembly instantiation.  With the 6 GiB ceiling, Node child processes
    fail with ``RangeError: WebAssembly.Instance(): Out of memory``,
    causing ALL test shards to fail.

    The adaptive test runner (adaptive_test.py) limits xdist workers by
    available RAM — that is the real OOM protection.  RLIMIT_AS is kept
    as an opt-in for local debugging:

        GLUDD_TEST_WORKER_MEM_MB=8192 pytest ...

    but is unconditionally disabled in CI (the corresponding env var in
    .github/workflows/build.yml has been removed).
    """
    # RLIMIT_AS is intentionally NOT set here.
    # The adaptive_test.py worker cap is sufficient OOM protection.
    # Uncomment the block below for local memory-pressure debugging:
    #
    # import resource
    # mem_mb = int(os.environ.get("GLUDD_TEST_WORKER_MEM_MB", "0"))
    # if mem_mb > 0:
    #     limit = mem_mb * 1024 * 1024
    #     resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _parse_ratchet_entries() -> dict[str, str]:
    """Parse config/ratchet.yml into {node_id: reason} map.

    Supports two formats:
    * Single-line: ``node_id: reason`` — e.g. ``tests/unit/test_foo.py::test_bar: known flaky``
    * YAML key-li: ``key:\n  detail\n  ...`` — first line is the node_id,
      subsequent indented lines become the reason.

    Blank lines and ``#``-comment lines are skipped.
    """
    ratchet_path = _REPO_ROOT / "config" / "ratchet.yml"
    if not ratchet_path.exists():
        return {}
    entries: dict[str, str] = {}
    lines = ratchet_path.read_text().splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        # YAML key with multiline value: "key:" followed by indented lines
        if not stripped.endswith(": ") and stripped.endswith(":") and len(stripped) > 1:
            node_id = stripped[:-1].strip()
            reason_parts: list[str] = []
            while i < len(lines):
                sub = lines[i].rstrip()
                if not sub or sub.startswith("#"):
                    i += 1
                    continue
                if not sub.startswith(" ") and not sub.startswith("\t"):
                    break
                reason_parts.append(sub.strip())
                i += 1
            reason = " ".join(reason_parts) if reason_parts else "ratchet entry"
            entries[node_id] = reason
            continue
        # Single-line: "node_id: reason" or "node_id::*"
        if "::" in stripped or ": " in stripped:
            parts = stripped.split(": ", 1)
            node_id = parts[0].strip()
            reason = parts[1].strip() if len(parts) > 1 else "ratchet entry"
            entries[node_id] = reason
    return entries


ENFORCEMENT_SHARED_STATE_GROUP = "enforcement-shared-state"
_LEGACY_ENFORCEMENT_XDIST_GROUPS = frozenset(
    {
        ENFORCEMENT_SHARED_STATE_GROUP,
        "hook-hardcoded-tmp",
        "gludd-watchdog-ci-cache",
        "enforcement_plugin_state_files",
        "enforcement_state_files",
        "deadline_e2e_state",
    }
)


def _xdist_group_name(marker: object) -> str | None:
    """Return an xdist group name from either a Mark or MarkDecorator."""
    mark = getattr(marker, "mark", marker)
    kwargs = getattr(mark, "kwargs", {})
    name = kwargs.get("name")
    if name is not None:
        return str(name)
    args = getattr(mark, "args", ())
    return str(args[0]) if args else None


def _item_xdist_groups(item: pytest.Item) -> set[str]:
    iter_markers = getattr(item, "iter_markers", None)
    if callable(iter_markers):
        markers = iter_markers("xdist_group")
    else:
        markers = (
            marker
            for marker in getattr(item, "own_markers", ())
            if getattr(getattr(marker, "mark", marker), "name", None) == "xdist_group"
        )
    return {name for marker in markers if (name := _xdist_group_name(marker)) is not None}


@functools.cache
def _source_touches_hardcoded_gludd_tmp(path: Path) -> bool:
    """Detect a source-level absolute Gludd tmp path once per test module."""
    try:
        return "/tmp/gludd-" in path.read_text()
    except (OSError, UnicodeError):
        return False


def _item_touches_hardcoded_gludd_tmp(item: pytest.Item) -> bool:
    if "hook_plugin_env" in getattr(item, "fixturenames", ()):
        return True
    raw_path = getattr(item, "path", None)
    if raw_path is None:
        raw_path = getattr(item, "fspath", None)
    if raw_path is None:
        return False
    return _source_touches_hardcoded_gludd_tmp(Path(str(raw_path)))


def _remove_legacy_enforcement_groups(item: pytest.Item) -> None:
    """Remove inherited and direct legacy aliases before adding one group."""
    listchain = getattr(item, "listchain", None)
    nodes = listchain() if callable(listchain) else [item]
    for node in nodes:
        markers = getattr(node, "own_markers", None)
        if markers is None:
            continue
        markers[:] = [
            marker
            for marker in markers
            if not (
                getattr(getattr(marker, "mark", marker), "name", None) == "xdist_group"
                and _xdist_group_name(marker) in _LEGACY_ENFORCEMENT_XDIST_GROUPS
            )
        ]


def _pin_enforcement_shared_state(item: pytest.Item) -> None:
    """Normalize shared-state tests while retaining unrelated serialization."""
    groups = _item_xdist_groups(item)
    enforcement_groups = groups & _LEGACY_ENFORCEMENT_XDIST_GROUPS
    touches_shared_state = _item_touches_hardcoded_gludd_tmp(item)
    if not enforcement_groups and not touches_shared_state:
        return

    unrelated_groups = groups - _LEGACY_ENFORCEMENT_XDIST_GROUPS
    _remove_legacy_enforcement_groups(item)
    if unrelated_groups:
        return
    item.add_marker(pytest.mark.xdist_group(name=ENFORCEMENT_SHARED_STATE_GROUP))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Serialize shared enforcement state and apply strict ratchet markers.

    Any collected test source containing an absolute ``/tmp/gludd-*`` path, or
    using ``hook_plugin_env``, is pinned to one canonical xdist group. Legacy
    enforcement group aliases are normalized so their teardown cannot race a
    sibling group. Existing non-enforcement resource groups (for example port
    and hot-reload groups) remain unchanged. Sources that only use tmp_path or
    env-var redirects stay fully parallel.

    **Ratchet strict-xfail:** config/ratchet.yml tracks known test failures.
    Each entry is a ``node_id: reason`` pair.  At collection time, matching
    tests are marked ``pytest.mark.xfail(strict=True, reason=...)`` — meaning:
    * if the test FAILS → xfail (expected), suite stays green
    * if the test PASSES → XPASS (unexpected), suite turns RED

    This is the ratchet: a test that starts passing forces the operator to
    remove its ratchet.yml entry AND lift the xfail marker.  A red suite
    with a ratchet entry is "OK" (known failure); a green suite with a
    ratchet entry is a bug (the entry should have been removed already).
    """
    # 1. xdist group pinning
    for item in items:
        _pin_enforcement_shared_state(item)

    # 2. Ratchet strict-xfail markers
    entries = _parse_ratchet_entries()
    if not entries:
        return
    for item in items:
        if item.nodeid in entries:
            item.add_marker(pytest.mark.xfail(strict=True, reason=entries[item.nodeid]))


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
def _disable_gate_refresh_autospawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep plugin-construction tests from launching background release gates."""
    monkeypatch.setenv("GLUDD_GATE_REFRESH_AUTOSPAWN", "0")


@pytest.fixture(autouse=True, scope="session")
def _testclient_presents_loopback_host():
    """Make Starlette's ``TestClient`` present the loopback IP it actually is.

    ``starlette.testclient.TestClient`` defaults to ``client=("testclient",
    50000)`` — a *pseudo-host* that is not a parseable IP address. The daemon's
    CIDR allowlist (``cidr_middleware``, daemon.py) is a real security control:
    ``_lifespan`` auto-enforces ``["127.0.0.0/8", "::1/128"]`` whenever the
    configured bind host is loopback (the default posture, daemon.py:964-971),
    and the middleware denies any client host that does not parse into an
    allowed network — ``"testclient"`` raises ``ValueError`` in
    ``ipaddress.ip_address()`` and is therefore (correctly) refused with
    ``403 {"error": "forbidden", ...}``.

    The in-process ASGI transport IS a loopback caller, so the honest fix is to
    say so — not to widen the production allowlist or punch a hole in the guard.
    Every ``TestClient`` therefore reports ``client=("127.0.0.1", 50000)``,
    exactly what a real ``curl http://127.0.0.1:8000`` would present, and the
    CIDR guard stays fully armed against everything else.

    Tests that deliberately exercise the *deny* path (or need another peer
    address) keep full control: an explicit ``TestClient(app, client=(...))``
    wins, because this only supplies a default.
    """
    import starlette.testclient as _testclient_mod

    original_init = _testclient_mod.TestClient.__init__

    @functools.wraps(original_init)
    def _loopback_init(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    _testclient_mod.TestClient.__init__ = _loopback_init  # type: ignore[method-assign]
    try:
        yield
    finally:
        _testclient_mod.TestClient.__init__ = original_init  # type: ignore[method-assign]


_LEAKY_ENV_VARS: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "GLUDD_PSK",
        "GLUDD_REQUIRE_AUTH",
        "GLUDD_ALLOW_NO_AUTH",
        "GLUDD_WEB_FETCH_ALLOWED_DOMAINS",
        "DELETION_REASON",
        "GLUDD_DELETION_GATE_THRESHOLD",
        "GL_CONFIG_DIR",
        "DATABASE_URL",
        "GLUDD_MT_BACKLOG",
        "GLUDD_XDIST_TRACE_LOG",
        "ZAI_API_KEY",
        "ZAI_BASE_URL",
    }
)


@pytest.fixture(autouse=True)
def _restore_leaky_env_vars(monkeypatch):
    """Snapshot and restore leaky env vars around every test.

    Backstop for tests that still mutate ``os.environ`` directly.  Guarantees
    that none of the known process-global config knobs leak from one test into
    a sibling on the same xdist worker, even if a test forgets to clean up.
    Uses ``monkeypatch.setenv`` / ``monkeypatch.delenv`` during fixture setup
    so all mutations are auto-restored at teardown, avoiding bare
    ``os.environ`` writes entirely.
    """
    snap = {k: os.environ.get(k) for k in _LEAKY_ENV_VARS}
    for k, original in snap.items():
        if original is not None:
            monkeypatch.setenv(k, original)
        else:
            monkeypatch.delenv(k, raising=False)
    yield

@pytest.fixture(autouse=True)
def _restore_cwd_after_test() -> None:
    """Restore the worker CWD after every test.

    Some tests intentionally chdir into temporary project roots. If a test fails
    before restoring CWD, later tests that use repo-relative paths see missing
    Makefile, src, or collection files. This fixture confines that process-global
    mutation to the test that made it.
    """
    original_cwd = os.getcwd()
    try:
        yield
    finally:
        try:
            os.chdir(original_cwd)
        except OSError:
            os.chdir(_REPO_ROOT)

@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """Snapshot and restore the ENTIRE logging state around every test.

    Prevents caplog pollution: tests that mutate any logger's level,
    propagate flag, handler list, or ``disabled`` flag (without restoring)
    leak into sibling tests on the same xdist worker. The root-only snapshot
    was insufficient because named child loggers
    (``general_ludd.secrets.migration``, ``general_ludd.models.model_registry``,
    etc.) carry their own state that doesn't reset when root is restored.

    Also guards against ``logging.config.fileConfig()`` (default
    ``disable_existing_loggers=True``, e.g. ``alembic/env.py`` and
    ``tests/unit/test_alembic_config.py``): it sets ``.disabled = True`` on
    every already-imported logger for the rest of the process. Snapshotting
    only (level, propagate, handlers) does not undo that -- ``.disabled``
    must be captured and restored too.

    Implements the durable fix from
    ``docs/audit/CI_GREEN_PLAN_2026-07-01.md`` Appendix A6: snapshot the
    root logger, every existing named logger in
    ``logging.Logger.manager.loggerDict`` (skipping ``PlaceHolder`` entries),
    and the global ``logging.disable`` level at test entry; restore them
    wholesale at exit. Loggers created *during* the test are reset to
    defaults (``NOTSET`` level, ``propagate=True``, no handlers,
    ``disabled=False``) so they cannot pollute later tests either.
    """
    root = logging.getLogger()
    snap_root = (root.level, root.propagate, list(root.handlers), root.disabled)

    manager = logging.Logger.manager
    logger_dict = manager.loggerDict
    snap_named: dict[str, tuple[int, bool, list[logging.Handler], bool]] = {}
    for name, logger in logger_dict.items():
        if isinstance(logger, logging.PlaceHolder):
            continue
        snap_named[name] = (
            logger.level,
            logger.propagate,
            list(logger.handlers),
            logger.disabled,
        )

    snap_disable = manager.disable if hasattr(manager, "disable") else 0

    yield

    root.level, root.propagate, root.handlers[:], root.disabled = snap_root

    for name, (level, propagate, handlers, disabled) in snap_named.items():
        logger = logger_dict.get(name)
        if isinstance(logger, logging.Logger):
            logger.level = level
            logger.propagate = propagate
            logger.handlers[:] = handlers
            logger.disabled = disabled

    if hasattr(manager, "disable"):
        manager.disable = snap_disable

    for new_name in set(logger_dict.keys()) - set(snap_named.keys()):
        new_logger = logger_dict.get(new_name)
        if isinstance(new_logger, logging.Logger):
            new_logger.level = logging.NOTSET
            new_logger.propagate = True
            new_logger.handlers.clear()
            new_logger.disabled = False


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
    monkeypatch.setattr(me, "_REGISTRY", CollectorRegistry(auto_describe=False), raising=False)


_A3_DENYLIST_PREFIXES: frozenset[str] = frozenset(
    {
        "live_pkg_",
        "livepkg",
        "rbpkg",
        "smg_",
        "capability_policy",
        "fs_write_policy",
    }
)


def _snapshot_sys_modules_and_path() -> tuple[dict[str, object], list[str]]:
    """Snapshot sys.modules (shallow dict copy) and sys.path (shallow list copy)."""
    import sys

    return dict(sys.modules), list(sys.path)


def _restore_sys_modules_and_path(snap_modules: dict[str, object], snap_path: list[str]) -> None:
    """Restore sys.path verbatim; evict denylisted test-injected sys.modules keys;
    restore any replaced modules from the snapshot."""
    import sys

    sys.path[:] = snap_path

    current = set(sys.modules.keys())
    snap_keys = set(snap_modules.keys())
    for key in current - snap_keys:
        is_denylisted = any(key.startswith(p) for p in _A3_DENYLIST_PREFIXES)
        if is_denylisted:
            sys.modules.pop(key, None)

    for key in snap_keys & current:
        if sys.modules[key] is not snap_modules[key]:
            sys.modules[key] = snap_modules[key]


@pytest.fixture(autouse=True)
def _sandbox_sys_modules_and_path():
    """Snapshot and restore sys.modules + sys.path around every test.

    Prevents fake-module injection leaks: tests that inject stub modules
    (live_pkg_*, rbpkg, smg_*, capability_policy, fs_write_policy) into
    sys.modules without cleanup leak into sibling tests, causing
    order-dependent import failures.

    Implements CI_GREEN_PLAN_2026-07-01.md Appendix A3.
    """
    snap_modules, snap_path = _snapshot_sys_modules_and_path()
    yield
    _restore_sys_modules_and_path(snap_modules, snap_path)


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


SLURM_AVAILABLE: bool = os.environ.get("SLURM_AVAILABLE") == "1" or shutil.which("sbatch") is not None
POSTGRES_AVAILABLE: bool = os.environ.get("POSTGRES_AVAILABLE") == "1" or _port_open("127.0.0.1", 5432)

requires_slurm = pytest.mark.skipif(
    not SLURM_AVAILABLE,
    reason="requires SLURM — set SLURM_AVAILABLE=1 or install sbatch",
)
requires_postgres = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason="requires PostgreSQL — set POSTGRES_AVAILABLE=1 or run postgres on :5432",
)


# ----------------------------------------------------------------------
# CI: ensure .gludd/ project directory exists at the repo root
# ----------------------------------------------------------------------
#
# The .gludd/ directory is gitignored and absent from fresh CI checkouts.
# Many source modules (memory, replay, retrieval, history) default their
# cache/store paths to subdirectories of .gludd/.  Tests that instantiate
# these modules — or call create_daemon_app() which internally references
# .gludd/ defaults — fail with FileNotFoundError when the parent directory
# does not exist.  This fixture creates it once for the session lifetime.
#
# Why session scope: creating .gludd/ per function would race xdist workers
# on the same filesystem path (teardown/removal by one worker during a
# sibling's test).  Session scope means one creation, no removal — the CI
# workspace is ephemeral anyway.


@pytest.fixture(autouse=True, scope="session")
def _ensure_gludd_dir_exists():
    """Ensure .gludd/ directory exists once per test session.

    The .gludd/ directory is gitignored and absent from fresh CI checkouts.
    Many source modules default their cache/store paths to subdirectories of
    .gludd/. This fixture creates it once for the session lifetime; session
    scope avoids xdist workers racing on mkdir/rmdir.
    """
    gludd_dir = _REPO_ROOT / ".gludd"
    gludd_dir.mkdir(exist_ok=True)
    yield


# ----------------------------------------------------------------------
# aiosqlite event-loop teardown guard
# ----------------------------------------------------------------------
#
# aiosqlite.Connection creates a daemon thread (_connection_worker_thread)
# that processes SQLite operations and sends results/exceptions back to
# the calling event loop via ``loop.call_soon_threadsafe()``.  When the
# loop is closed BEFORE the thread processes its stop sentinel
# (_STOP_RUNNING_SENTINEL), ``call_soon_threadsafe`` raises
# ``RuntimeError('Event loop is closed')`` — which is caught by the broad
# ``except BaseException``, and the handler then tries to send the
# *exception itself* back via ``call_soon_threadsafe`` … which raises
# *again*, uncaught this time, and the thread dies with an unhandled
# exception.  Python 3.14 + pytest-asyncio surface this as
# ``PytestUnhandledThreadExceptionWarning``, and when it happens inside an
# xdist worker it takes down the entire worker (``[gw0] node down: Not
# properly terminated``).
#
# The fix: monkeypatch the background worker at session start so the
# ``call_soon_threadsafe`` calls inside the thread are wrapped in a
# try/except RuntimeError.  A closed loop is a legitimate teardown state;
# the futures are already orphaned, so silently dropping the result is safe.
#
# Session scope is required because the patch must be in place before
# *any* test creates an async engine (which creates aiosqlite connections).


@pytest.fixture(autouse=True)
def _reset_httpx_and_abtest_runner():
    """Reset ``httpx.get`` and ``run_candidate_in_subprocess`` around every test.

    Prevents async mock contamination across xdist workers: tests patch
    ``httpx.get`` or ``general_ludd.abtest.compare.run_candidate_in_subprocess``
    via ``patch()`` context-managers / decorators, but setup-method-level
    patching, ``start()``/``stop()`` mismatches, or teardown exceptions can
    leak mock objects into sibling tests on the same worker.  This fixture
    always restores the canonical (module-load-time) reference so a single
    corrupt teardown cannot poison all subsequent tests.

    ``httpx.get`` is the target because ~100+ test sites across the suite
    mock it (cli, e2e, connector SSRF, skills, TUI, etc.).  The
    ``run_candidate_in_subprocess`` reference inside ``abtest.compare`` is
    the import that tests actually patch (NOT the ``runner`` module source),
    so it is the reference that must be reset.
    """
    import httpx as _httpx

    _httpx.get = _CANONICAL_HTTPX_GET
    _ab_compare.run_candidate_in_subprocess = _CANONICAL_RUN_CANDIDATE
    yield
    _httpx.get = _CANONICAL_HTTPX_GET
    _ab_compare.run_candidate_in_subprocess = _CANONICAL_RUN_CANDIDATE


@pytest.fixture(autouse=True, scope="session")
def _patch_aiosqlite_worker_for_closed_loop_teardown():
    """Patch aiosqlite's background worker to survive event-loop shutdown.

    aiosqlite.Connection creates a daemon thread that calls
    ``loop.call_soon_threadsafe()``. When the event loop is closed before the
    thread processes its stop sentinel, ``call_soon_threadsafe`` raises
    ``RuntimeError``, the thread dies with an unhandled exception, and xdist
    workers crash. This session-scoped fixture wraps the worker with a
    try/except RuntimeError so a closed loop is handled gracefully.
    """
    try:
        import aiosqlite.core as _ac
    except ImportError:
        yield
        return

    _orig_worker = _ac._connection_worker_thread

    def _safe_worker(tx):
        while True:
            future, function = tx.get()
            try:
                result = function()
                if future and not future.done():
                    with suppress(RuntimeError):
                        future.get_loop().call_soon_threadsafe(_ac.set_result, future, result)
                if result is _ac._STOP_RUNNING_SENTINEL:
                    break
            except BaseException as e:
                if future and not future.done():
                    with suppress(RuntimeError):
                        future.get_loop().call_soon_threadsafe(_ac.set_exception, future, e)

    _ac._connection_worker_thread = _safe_worker
    yield
    _ac._connection_worker_thread = _orig_worker
