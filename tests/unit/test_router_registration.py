"""Unit tests for ``general_ludd.routers.register_all``.

Targets the registration block in ``src/general_ludd/routers/__init__.py``
which was flagged at 4.9% coverage (~60 uncovered LOC, lines 13-71). That
block is a single point of failure: a typo'd import or dropped ``register``
call silently removes an entire HTTP surface from the daemon.

Pinning the contract:
  * every router module imported by ``register_all`` contributes >=1 route
  * no two registered routes collide on the same (path, method) tuple
  * the wired router count matches a pinned constant (silent-removal guard)
  * an unknown / missing ``register`` callable surfaces as an error rather
    than being swallowed
"""
from __future__ import annotations

import importlib
import inspect
from collections import Counter

import pytest
from fastapi import FastAPI

from general_ludd.routers import register_all

# Canonical set of router modules wired by ``register_all``. Updating this
# list is a deliberate act: if it drifts from the registration block, the
# ``test_router_count_matches_expected`` test fires.
EXPECTED_ROUTERS: tuple[str, ...] = (
    "account",
    "adversarial",
    "ansible",
    "benchmark",
    "chat",
    "compute",
    "coordination",
    "estimation",
    "eval",
    "filestore",
    "game",
    "generate",
    "human_todos",
    "integrity",
    "mcp",
    "memory",
    "model_performance",
    "models",
    "ornith",
    "projects",
    "quantization",
    "reload",
    "remediation",
    "render",
    "security",
    "self_improve",
    "signing",
    "skills",
    "slurm",
    "spec_quality",
    "stream",
    "terraform_state",
    "todos",
    "variants",
    "web_search",
    "worktree",
)
EXPECTED_ROUTER_COUNT = 36

# Pre-existing (method, path) collisions known to exist across routers wired
# by ``register_all``. The ``test_no_duplicate_route_keys`` test allows these
# specifically; anything else is a hard failure. Each entry MUST link to the
# offending routers in a comment so the fix path is obvious.
#
# No known duplicates. The previous ("GET", "/api/deployments") collision
# between compute.py:402 (dict[str, object] with provider/model fields) and
# todos.py:569 (list[dict[str, str]] with instance_id/status) was resolved by
# deleting the legacy todos.py handler — compute.py is now the sole registrant.
KNOWN_DUPLICATE_ROUTE_KEYS: frozenset[tuple[str, str]] = frozenset()


def _make_app_with_routers() -> FastAPI:
    """Build a fresh FastAPI app and run ``register_all`` against it."""
    app = FastAPI()
    register_all(app, {})
    return app


def _route_keys(app: FastAPI) -> list[tuple[str, str]]:
    """Flatten app.routes into a list of (http_method, path) tuples."""
    keys: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path is None:
            continue
        for method in sorted(methods):
            keys.append((method, path))
    return keys


class TestRegisterAllRouters:
    def test_all_expected_routers_mounted(self):
        """Each router module imported by ``register_all`` must contribute
        at least one route when invoked individually. A router that
        registers zero routes is dead wiring and a regression.
        """
        for name in EXPECTED_ROUTERS:
            app = FastAPI()
            before = len(app.routes)
            module = importlib.import_module(f"general_ludd.routers.{name}")
            register_fn = getattr(module, "register", None)
            assert callable(register_fn), (
                f"router module {name!r} has no callable 'register'"
            )
            register_fn(app, {})
            after = len(app.routes)
            assert after > before, (
                f"router {name!r} registered zero routes "
                f"(before={before}, after={after})"
            )

    def test_register_all_mounts_expected_route_count(self):
        """``register_all`` on a fresh app produces a sane route count.
        This exercises the actual registration block (lines 13-71 of
        routers/__init__.py), not just per-module calls.
        """
        app = _make_app_with_routers()
        # FastAPI seeds an empty app with 4 default routes (OpenAPI/swagger/
        # redoc/health). Each expected router must contribute >=1 route,
        # so the floor is 4 + EXPECTED_ROUTER_COUNT.
        floor = 4 + EXPECTED_ROUTER_COUNT
        assert len(app.routes) >= floor, (
            f"register_all produced {len(app.routes)} routes, "
            f"expected at least {floor} (4 FastAPI defaults + "
            f"{EXPECTED_ROUTER_COUNT} from routers)"
        )

    def test_no_duplicate_route_keys(self):
        """No two routes may collide on the same (method, path) tuple.

        FastAPI silently shadows the first handler with the second when
        two routes share a key — a class of bug introduced by a careless
        new endpoint. New duplicates are hard-failures. Pre-existing
        collisions are pinned in ``KNOWN_DUPLICATE_ROUTE_KEYS``; removing
        an entry from that set (because the underlying collision was
        fixed) is an intentional, flagged act.
        """
        app = _make_app_with_routers()
        keys = _route_keys(app)
        counts = Counter(keys)
        actual_dups = {k: n for k, n in counts.items() if n > 1}

        unknown_dups = {
            k: n for k, n in actual_dups.items()
            if k not in KNOWN_DUPLICATE_ROUTE_KEYS
        }
        assert not unknown_dups, (
            "new duplicate (method, path) routes registered by "
            f"register_all: {unknown_dups}"
        )

        # Inverse check: if a known duplicate was fixed, the maintainer must
        # remove it from KNOWN_DUPLICATE_ROUTE_KEYS. A stale entry here is a
        # prompt to clean up, not a silent pass.
        stale_known = {
            k for k in KNOWN_DUPLICATE_ROUTE_KEYS if k not in actual_dups
        }
        assert not stale_known, (
            "KNOWN_DUPLICATE_ROUTE_KEYS lists collisions that no longer "
            f"reproduce (fix is complete — remove these entries): "
            f"{sorted(stale_known)}"
        )

    def test_router_count_matches_expected(self):
        """Pin the number of routers wired by ``register_all``.

        If a router is added or removed from the registration block without
        updating ``EXPECTED_ROUTERS`` (and ``EXPECTED_ROUTER_COUNT``), this
        test fires. The point is to make silent removal impossible.
        """
        src = inspect.getsource(register_all)
        called: list[str] = []
        for name in EXPECTED_ROUTERS:
            call_stmt = f"register_{name}(app, daemon_state)"
            assert call_stmt in src, (
                f"register_all source does not invoke {call_stmt!r}"
            )
            called.append(name)
        assert len(called) == EXPECTED_ROUTER_COUNT, (
            f"EXPECTED_ROUTER_COUNT={EXPECTED_ROUTER_COUNT} but {len(called)} "
            f"register_*() calls were verified in source"
        )

    def test_router_count_blocks_silent_removal(self):
        """If someone removes a router from the registration block but
        forgets to drop it from ``EXPECTED_ROUTERS``, the source-scan
        above catches it. This test pins the inverse direction: the
        registration block must not reference routers we forgot to
        enumerate.
        """
        import re

        src = inspect.getsource(register_all)
        # Match lines like:  register_account(app, daemon_state)
        invoked = set(re.findall(r"register_([a-z_]+)\(app, daemon_state\)", src))
        enumerated = set(EXPECTED_ROUTERS)
        missing_from_enum = invoked - enumerated
        assert not missing_from_enum, (
            "register_all invokes register functions not enumerated in "
            f"EXPECTED_ROUTERS: {sorted(missing_from_enum)}"
        )
        missing_from_block = enumerated - invoked
        assert not missing_from_block, (
            "EXPECTED_ROUTERS lists routers not invoked by register_all: "
            f"{sorted(missing_from_block)}"
        )

    def test_register_unknown_router_raises(self, monkeypatch):
        """If a router module referenced by ``register_all`` is missing
        its ``register`` callable (e.g. typo, partial refactor), the
        error must surface. Silent skipping would hide a broken router.
        """
        import general_ludd.routers.account as account_mod

        monkeypatch.delattr(account_mod, "register", raising=False)
        app = FastAPI()
        # ``from X import Y`` raises ImportError when Y is absent from X.
        with pytest.raises((ImportError, AttributeError)) as exc_info:
            register_all(app, {})
        assert "register" in str(exc_info.value), (
            f"expected error mentioning 'register', got: {exc_info.value!r}"
        )

    def test_register_all_is_repeatable(self):
        """Calling ``register_all`` twice on the same app adds the same
        set of routes again. While duplicate decoration is technically
        allowed by FastAPI (it appends to the route table), this test
        pins the behaviour so a future change to idempotent registration
        is a deliberate, flagged act.
        """
        app = FastAPI()
        baseline = len(app.routes)
        register_all(app, {})
        first_delta = len(app.routes) - baseline
        assert first_delta > 0, "register_all added zero routes on first call"
        register_all(app, {})
        second_delta = len(app.routes) - baseline - first_delta
        assert second_delta == first_delta, (
            f"second register_all call added {second_delta} routes, "
            f"expected {first_delta} (registration block changed shape "
            "between invocations)"
        )
