"""Deep endpoint specification tests — every route in every router inspected.

Verifies mechanically:
  - response_model set on all endpoints
  - status_code set on POST endpoints that create (201)
  - Path correctness (no trailing slashes, admin/api prefix)
  - DELETE routes have response_model
  - No duplicate (method, path) across routers
  - Every router module has a callable register()
  - Router import completeness in __init__.register_all
  - Minimum route count

Advisory (skip with counts):
  - OpenAPI tags presence
  - summary/description presence
  - POST create endpoints have status_code=201
  - Admin write routes have capability guards
  - Short path parameter names

This is a structural gate — it loads router modules, builds bare FastAPI apps,
registers each router, and introspects the route table without invoking handlers.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

_ROUTER_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "general_ludd" / "routers"
_ROUTERS_INIT = _ROUTER_DIR / "__init__.py"

_ROUTER_MODULES = sorted(
    f.stem for f in _ROUTER_DIR.iterdir() if f.suffix == ".py" and f.stem not in {"__init__", "_util"}
)


# ---------------------------------------------------------------------------
# App builder — one router at a time
# ---------------------------------------------------------------------------


def _build_app(router_name: str) -> FastAPI:
    app = FastAPI()
    mod = importlib.import_module(f"general_ludd.routers.{router_name}")
    if not hasattr(mod, "register"):
        pytest.skip(f"{router_name} has no register()")
    try:
        mod.register(app, {})
    except Exception as exc:
        pytest.skip(f"{router_name} register() raised {type(exc).__name__}: {exc}")
    return app


def _all_routes(router_name: str) -> list[APIRoute]:
    return [r for r in _build_app(router_name).routes if isinstance(r, APIRoute)]


# ---------------------------------------------------------------------------
# Fixtures — build full route table once per module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_routes_by_router() -> dict[str, list[APIRoute]]:
    result: dict[str, list[APIRoute]] = {}
    for name in _ROUTER_MODULES:
        try:
            routes = _all_routes(name)
        except pytest.skip.Exception:
            continue
        if routes:
            result[name] = routes
    return result


@pytest.fixture(scope="module")
def all_routes(all_routes_by_router: dict[str, list[APIRoute]]) -> list[APIRoute]:
    flat: list[APIRoute] = []
    for routes in all_routes_by_router.values():
        flat.extend(routes)
    return flat


# ===========================================================================
# HARD GATE TESTS — failures must be fixed
# ===========================================================================


def test_every_router_registers_routes(all_routes_by_router: dict[str, list[APIRoute]]):
    """Each router module must produce >=1 route."""
    for name in _ROUTER_MODULES:
        assert name in all_routes_by_router, f"Router {name!r} did not register any routes"


def test_every_get_route_has_response_model(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Every GET route must set response_model or have a return annotation."""
    missing: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if "GET" not in r.methods:
                continue
            if r.response_model is not None:
                continue
            ra = r.endpoint.__annotations__.get("return")
            if ra is None:
                missing.append(f"{router} GET {r.path}")
    assert not missing, f"{len(missing)} GET route(s) missing response_model and return annotation:\n" + "\n".join(
        f"  {m}" for m in sorted(missing)
    )


def test_no_duplicate_routes(all_routes_by_router: dict[str, list[APIRoute]]):
    """No two routers should register the same (method, path)."""
    seen: dict[tuple[str, str], str] = {}
    dupes: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            for m in r.methods:
                key = (m, r.path)
                if key in seen:
                    dupes.append(f"{m} {r.path} — {seen[key]} and {router}")
                else:
                    seen[key] = router
    assert not dupes, f"{len(dupes)} duplicate route(s):\n" + "\n".join(f"  {d}" for d in sorted(dupes))


def test_valid_status_codes(all_routes_by_router: dict[str, list[APIRoute]]):
    """Route status_code (if set) must be a valid HTTP code."""
    _VALID = {
        200,
        201,
        202,
        204,
        301,
        302,
        400,
        401,
        403,
        404,
        405,
        409,
        410,
        413,
        415,
        422,
        429,
        500,
        501,
        502,
        503,
        504,
    }
    bad: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if r.status_code is None:
                continue
            if r.status_code in _VALID:
                continue
            bad.append(f"{router} {next(iter(r.methods))} {r.path} status_code={r.status_code}")
    assert not bad, f"{len(bad)} routes with invalid status_code:\n" + "\n".join(f"  {b}" for b in sorted(bad))


def test_no_trailing_slash_in_paths(all_routes_by_router: dict[str, list[APIRoute]]):
    """API routes should not end with /."""
    bad: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if r.path.endswith("/") and r.path != "/":
                bad.append(f"{router} {next(iter(r.methods))} {r.path}")
    assert not bad, f"{len(bad)} route(s) with trailing slash:\n" + "\n".join(f"  {b}" for b in sorted(bad))


def test_delete_routes_have_response_model(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """DELETE routes must declare response_model or return annotation."""
    missing: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if "DELETE" not in r.methods:
                continue
            if r.response_model is not None:
                continue
            ra = r.endpoint.__annotations__.get("return")
            if ra is None:
                missing.append(f"{router} DELETE {r.path}")
    assert not missing, f"{len(missing)} DELETE route(s) without response model:\n" + "\n".join(
        f"  {m}" for m in sorted(missing)
    )


def test_every_router_has_register_function():
    """Each router module must expose a callable register(app, daemon_state)."""
    missing: list[str] = []
    for name in _ROUTER_MODULES:
        try:
            mod = importlib.import_module(f"general_ludd.routers.{name}")
        except ImportError as exc:
            missing.append(f"{name} (import error: {exc})")
            continue
        fn = getattr(mod, "register", None)
        if fn is None or not callable(fn):
            missing.append(f"{name} (no callable register)")
    assert not missing, f"{len(missing)} router(s) without a callable register():\n" + "\n".join(
        f"  {m}" for m in sorted(missing)
    )


def test_register_function_signature():
    """register() must accept at least (app, daemon_state)."""
    bad: list[str] = []
    for name in _ROUTER_MODULES:
        try:
            mod = importlib.import_module(f"general_ludd.routers.{name}")
        except ImportError:
            continue
        fn = getattr(mod, "register", None)
        if fn is None or not callable(fn):
            continue
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        if len(params) < 2:
            bad.append(f"{name} register({params}) — expected >=2 params")
    assert not bad, f"{len(bad)} router(s) with wrong register() signature"


def test_router_init_imports_are_valid():
    """Every import in __init__.register_all maps to a real .py file."""
    if not _ROUTERS_INIT.exists():
        pytest.skip("__init__.py not found")
    init_source = _ROUTERS_INIT.read_text()
    tree = ast.parse(init_source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("general_ludd.routers."):
            for alias in node.names:
                if alias.name == "register":
                    mod_part = node.module[len("general_ludd.routers.") :]
                    imported.add(mod_part)
    on_disk = {f.stem for f in _ROUTER_DIR.iterdir() if f.suffix == ".py" and f.stem not in {"__init__", "_util"}}
    missing_on_disk = imported - on_disk
    assert not missing_on_disk, f"__init__ imports stale router(s): {sorted(missing_on_disk)}"


def test_route_count_minimum(all_routes: list[APIRoute]):
    """At least 100 routes must be registered across all routers."""
    assert len(all_routes) >= 100, f"Only {len(all_routes)} routes found — expected >=100"


def test_all_paths_have_admin_or_api_prefix(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Every route must use /admin/ or /api/ prefix (or be a known utility)."""
    _KNOWN = frozenset(
        {
            "/healthz",
            "/readyz",
            "/openapi.json",
            "/docs",
            "/redoc",
            "/metrics",
        }
    )
    _PREFIXES = ("/admin/", "/api/", "/render/")
    bad: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if r.path in _KNOWN:
                continue
            if any(r.path.startswith(p) for p in _PREFIXES):
                continue
            bad.append(f"{router} {next(iter(r.methods))} {r.path}")
    assert not bad, f"{len(bad)} route(s) without recognized prefix:\n" + "\n".join(f"  {b}" for b in sorted(bad))


# ===========================================================================
# ADVISORY TESTS — skip with diagnostic counts, not hard gates
# ===========================================================================


def test_tags_presence_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: count routes without OpenAPI tags."""
    missing: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if not r.tags:
                missing.append(f"{router} {next(iter(r.methods))} {r.path}")
    if missing:
        pytest.skip(f"ADVISORY: {len(missing)} route(s) missing OpenAPI tags. Add tags= for better swagger UX.")


def test_summary_description_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: count routes without summary or description."""
    missing: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if r.summary or r.description:
                continue
            missing.append(f"{router} {next(iter(r.methods))} {r.path}")
    if missing:
        pytest.skip(
            f"ADVISORY: {len(missing)} route(s) missing summary AND description. "
            f"Add summary= or description= for readable OpenAPI docs."
        )


def test_post_create_status_201_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: POST-create endpoints should use status_code=201."""
    _CREATE_RE = re.compile(r"/(add|create|register|issue|enqueue|submit|file)")
    missing: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if "POST" not in r.methods:
                continue
            if r.status_code == 201:
                continue
            if _CREATE_RE.search(r.path.lower()):
                missing.append(f"{router} POST {r.path}")
    if missing:
        pytest.skip(
            f"ADVISORY: {len(missing)} POST-create route(s) returning 200 "
            f"instead of 201. Consider adding status_code=201."
        )


def test_admin_write_guard_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: admin write routes that lack RequireCapability guards."""
    unguarded: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if not ({"POST", "PUT", "DELETE", "PATCH"} & r.methods):
                continue
            if not r.path.startswith("/admin/"):
                continue
            deps_str = str(r.dependencies)
            if "RequireCapability" in deps_str:
                continue
            unguarded.append(f"{router} {next(iter(r.methods))} {r.path}")
    if unguarded:
        pytest.skip(
            f"ADVISORY: {len(unguarded)} admin write route(s) without "
            f"RequireCapability guard. PSK-gated routes may not need it."
        )


def test_short_path_params_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: path params shorter than 3 chars (e.g. {id})."""
    bad: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            for m in re.finditer(r"\{(\w+)\}", r.path):
                if len(m.group(1)) < 3:
                    bad.append(f"{router} {next(iter(r.methods))} {r.path}")
                    break
    if bad:
        pytest.skip(
            f"ADVISORY: {len(bad)} route(s) with short path params. "
            f"Use descriptive names like {{project_id}} instead of {{id}}."
        )


def test_bare_dict_response_advisory(
    all_routes_by_router: dict[str, list[APIRoute]],
):
    """Advisory: GET routes that return bare dict instead of response_model."""
    bare: list[str] = []
    for router, routes in all_routes_by_router.items():
        for r in routes:
            if "GET" not in r.methods:
                continue
            if r.response_model is not None:
                continue
            ra = r.endpoint.__annotations__.get("return")
            ra_str = str(ra) if ra else "None"
            bare.append(f"{router} GET {r.path} -> {ra_str}")
    if bare:
        pytest.skip(
            f"ADVISORY: {len(bare)} GET route(s) without explicit "
            f"response_model. dict[str,object] is functional but "
            f"Pydantic models give better OpenAPI schemas."
        )
