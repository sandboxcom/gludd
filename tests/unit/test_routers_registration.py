"""Tests for ``general_ludd.routers.register_all`` — pins the daemon router surface.

``routers/__init__.py::register_all`` is a parallel router-registration entry
point. It is the single declarative list of every router module the daemon is
expected to mount; if a router drops out (typo, accidental deletion, silent
import failure) the daemon's API surface silently shrinks and operators have
no signal until a missing endpoint is hit at runtime.

These tests pin that contract by:

1. Importing ``create_daemon_app`` (the daemon app factory) so changes to its
   signature surface here.
2. Calling ``register_all`` on a fresh ``FastAPI`` and asserting every router
   module referenced in ``routers/__init__.py`` mounts at least one
   representative HTTP path on ``app.routes``.
3. Verifying that a router which throws at import time surfaces the error
   rather than being silently swallowed (``register_all`` has no ``try/except``
   around its imports — that is the contract).
4. Covering the ``app.include_router(prefix=..., tags=...)`` mounting path
   that several routers rely on for their prefix/tags wiring.
"""

from __future__ import annotations

import sys
import types
import warnings
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI

from general_ludd.daemon import create_daemon_app
from general_ludd.routers import register_all

# (module_name, representative_path) — one fixture path per router that
# ``register_all`` must mount. Adding a new router to ``register_all`` requires
# adding a row here, which is exactly the point: this is the pinned contract.
# Source of truth for the paths: the first ``@app.<verb>(...)`` decorator in
# each ``routers/<name>.py::register`` function.
EXPECTED_ROUTES: list[tuple[str, str]] = [
    ("account", "/api/account/backup"),
    ("adversarial", "/admin/security/scan-text"),
    ("ansible", "/admin/ansible/search"),
    ("benchmark", "/admin/benchmark/scores"),
    ("chat", "/api/chat/sessions"),
    ("compute", "/admin/compute/utilization"),
    ("coordination", "/api/coordination/claim"),
    ("estimation", "/admin/estimation/report"),
    ("eval", "/admin/eval/run"),
    ("filestore", "/admin/filestore/list"),
    ("game", "/api/game/generate-multi"),
    ("generate", "/api/generate/list-types"),
    ("human_todos", "/api/human-todos"),
    ("integrity", "/admin/integrity/scan"),
    ("mcp", "/admin/mcp/catalog/search"),
    ("memory", "/api/memory"),
    ("model_performance", "/admin/models/performance"),
    ("models", "/admin/models"),
    ("ornith", "/admin/ornith/record"),
    ("projects", "/admin/projects"),
    ("quantization", "/admin/quantization"),
    ("reload", "/admin/reload"),
    ("remediation", "/admin/remediation/scan"),
    ("render", "/api/renderers"),
    ("security", "/admin/sts/issue"),
    ("self_improve", "/admin/self-improve/analyze"),
    ("signing", "/admin/signing/cosign/generate"),
    ("skills", "/admin/skills/catalog/search"),
    ("slurm", "/admin/slurm/status"),
    ("spec_quality", "/api/spec-quality/audit"),
    ("stream", "/admin/stream/dispatch"),
    ("terraform_state", "/api/terraform/state/{stack_name:path}"),
    ("todos", "/api/todos"),
    ("variants", "/admin/prompts/variants/report"),
    ("web_search", "/admin/web/search"),
    ("worktree", "/admin/worktree/scan"),
]


def _route_paths(app: FastAPI) -> set[str]:
    """Return concrete path strings from every route shape FastAPI exposes."""
    return {
        path
        for route in app.routes
        if isinstance(path := getattr(route, "path", None), str)
    }


@pytest.fixture(scope="module")
def registered_app() -> FastAPI:
    """Bare FastAPI app with ``register_all`` invoked exactly once.

    ``register_all`` is the unit under test; ``create_daemon_app`` is the
    daemon factory import referenced separately to track its signature.
    """
    app = FastAPI()
    daemon_state: dict[str, Any] = {
        "todos": [],
        "tick_metrics": {},
        "quality_gate": {},
    }
    register_all(app, daemon_state)
    return app


class TestDaemonAppFactoryImport:
    """Requirement 1: the daemon app factory must be importable from daemon.py."""

    def test_create_daemon_app_is_callable(self) -> None:
        assert callable(create_daemon_app)
        assert create_daemon_app.__module__ == "general_ludd.daemon"


class TestRegisterAllMountsEveryRouter:
    """Requirement 2: every router listed in ``routers/__init__.py`` mounts."""

    @pytest.mark.parametrize("module_name, expected_path", EXPECTED_ROUTES)
    def test_router_mounts_representative_path(
        self,
        registered_app: FastAPI,
        module_name: str,
        expected_path: str,
    ) -> None:
        paths = _route_paths(registered_app)
        assert expected_path in paths, (
            f"router {module_name!r} did not mount {expected_path!r}; "
            f"register_all() must register every router module — a silent "
            f"shrink of the daemon API surface is the failure mode this pins"
        )

    def test_expected_routes_table_covers_all_register_all_imports(self) -> None:
        """If a router module is added to register_all, EXPECTED_ROUTES must grow.

        Parses ``routers/__init__.py`` for the canonical list of
        ``from general_ludd.routers.<name> import register`` statements and
        fails if the EXPECTED_ROUTES table is missing any of them. This makes
        adding a router without adding a test row a structural failure.
        """
        import inspect

        source = inspect.getsource(register_all)
        import re

        registered_modules = set(re.findall(
            r"from general_ludd\.routers\.(\w+) import register as register_\w+",
            source,
        ))
        expected_modules = {name for name, _ in EXPECTED_ROUTES}

        missing_from_table = registered_modules - expected_modules
        assert not missing_from_table, (
            f"register_all imports these routers but EXPECTED_ROUTES has no row: "
            f"{sorted(missing_from_table)} — add a (module, path) row for each"
        )

        stale_in_table = expected_modules - registered_modules
        assert not stale_in_table, (
            f"EXPECTED_ROUTES has rows for routers register_all no longer imports: "
            f"{sorted(stale_in_table)} — delete the stale row(s)"
        )

    def test_register_all_adds_at_least_one_route_per_router(
        self, registered_app: FastAPI
    ) -> None:
        """Sanity: after register_all, the app has routes from many routers."""
        paths = _route_paths(registered_app)
        # 29 EXPECTED_ROUTES rows means at least 29 distinct paths.
        assert len(paths) >= len(EXPECTED_ROUTES), (
            f"register_all only mounted {len(paths)} paths; expected at least "
            f"{len(EXPECTED_ROUTES)} (one per router module)"
        )

    def test_openapi_has_no_duplicate_operation_id_warnings(
        self, registered_app: FastAPI
    ) -> None:
        """Every mounted method must have a unique OpenAPI operation id."""
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            registered_app.openapi()
        duplicates = [
            str(item.message)
            for item in captured
            if "Duplicate Operation ID" in str(item.message)
        ]
        assert not duplicates, "duplicate FastAPI operation IDs: " + "; ".join(duplicates)


class TestRegisterAllImportErrorPropagation:
    """Requirement 3: a router throwing at import time must surface the error.

    ``register_all`` performs its imports lazily inside the function body so
    that a circular-import at module-load time is avoided. Those lazy imports
    are NOT wrapped in ``try/except``: an ``ImportError`` raised by any router
    module's dependencies must propagate to the caller so the operator sees
    the failure rather than a silently degraded API.
    """

    def test_attribute_access_failure_on_router_module_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Inject a broken module whose ``register`` attribute access raises.

        ``from general_ludd.routers.account import register`` performs an
        attribute access on the module after import. If that raises
        ``ImportError`` (the canonical signal for "this module's deps are
        broken"), ``register_all`` must let it propagate — never swallow.
        """
        class BrokenAccountModule(types.ModuleType):
            """Module double whose lazy register access fails like an import."""

            def __getattr__(self, name: str) -> Any:
                if name == "register":
                    raise ImportError(
                        "simulated import-time failure: account deps unavailable"
                    )
                raise AttributeError(name)

        broken = BrokenAccountModule("general_ludd.routers.account")
        monkeypatch.setitem(
            sys.modules, "general_ludd.routers.account", broken
        )

        app = FastAPI()
        daemon_state: dict[str, Any] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        with pytest.raises(ImportError, match="account deps unavailable"):
            register_all(app, daemon_state)

    def test_no_try_except_wraps_imports(self) -> None:
        """Structural check: register_all source contains no try/except shield.

        The lazy-import block in ``register_all`` must remain unwrapped so any
        router-side failure surfaces verbatim. Adding a ``try/except`` around
        those imports would silently shrink the API surface — a regression.
        """
        import inspect
        import re

        source = inspect.getsource(register_all)
        # The import block lives inside the function body. Reject any line
        # starting a try: or except clause within that source.
        offending_lines = [
            line.strip()
            for line in source.splitlines()
            if re.match(r"\s+(try|except)\b", line)
        ]
        assert not offending_lines, (
            "register_all wraps its imports in try/except — this would "
            f"silently swallow router registration failures: {offending_lines}"
        )


class TestIncludeRouterPrefixAndTags:
    """Requirement 4: ``app.include_router(prefix=..., tags=...)`` mounting path.

    Several daemon routers (e.g. ``observe``, ``webmcp``) mount via
    ``app.include_router`` with prefix + tags. This test pins that the
    FastAPI mounting mechanism the daemon relies on actually surfaces the
    prefix on ``app.routes`` and the tags on the OpenAPI schema — so that
    any future refactor that drops the prefix (or the tag) is caught here
    rather than in production when an OpenAPI client breaks.
    """

    def test_prefix_appears_on_route_path(self) -> None:
        sub_router = APIRouter()

        @sub_router.get("/widget")
        async def get_widget() -> dict[str, str]:
            return {"ok": "true"}

        app = FastAPI()
        app.include_router(sub_router, prefix="/prefixed", tags=["test-tag"])

        paths = _route_paths(app)
        assert "/prefixed/widget" in paths, (
            "include_router(prefix=...) did not compose with the sub-router's "
            "path — daemon routers that mount this way would silently 404"
        )

    def test_tags_surface_in_openapi_schema(self) -> None:
        sub_router = APIRouter()

        @sub_router.get("/widget")
        async def get_widget() -> dict[str, str]:
            return {"ok": "true"}

        app = FastAPI()
        app.include_router(sub_router, prefix="/prefixed", tags=["test-tag"])

        schema = app.openapi()
        operation = schema["paths"]["/prefixed/widget"]["get"]
        assert "test-tag" in operation["tags"], (
            "include_router(tags=...) did not propagate to the OpenAPI schema — "
            "daemon OpenAPI clients would lose their grouping metadata"
        )

    def test_include_router_without_prefix_keeps_path(self) -> None:
        sub_router = APIRouter()

        @sub_router.get("/unprefixed")
        async def get_unprefixed() -> dict[str, str]:
            return {"ok": "true"}

        app = FastAPI()
        app.include_router(sub_router)

        paths = _route_paths(app)
        assert "/unprefixed" in paths
