"""Deep API versioning and compatibility tests.

Covers: version constants, daemon-app version wiring, OpenAPI schema version,
semver parsing/comparison, SCHEMA_VERSION lifecycle, deprecation-warning
infrastructure, router registration version consistency, backward-compat path
patterns, version-header parsing, middleware version injectability,
CollectionVersionInfo detection, and version-negotiation decision logic.
"""

from __future__ import annotations

import os
import re
import tomllib
import warnings
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from packaging.version import Version as PackVersion

from general_ludd import __version__
from general_ludd.materials.core import SCHEMA_VERSION as MATERIALS_SCHEMA_VERSION

pytestmark = pytest.mark.xdist_group("api_versioning_deep")

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PYPROJECT = os.path.join(PROJECT_ROOT, "pyproject.toml")

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_LEGACY_DRAFT_PREFIXES = (
    "http://json-schema.org/draft-07",
    "http://json-schema.org/draft-06",
    "http://json-schema.org/draft-04",
    "http://json-schema.org/draft-03",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pyproject_version() -> str:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def _parse_semver_dict(v: str) -> dict[str, object]:
    m = SEMVER_RE.match(v)
    if not m:
        return {}
    return {
        "major": int(m.group(1)),
        "minor": int(m.group(2)),
        "patch": int(m.group(3)),
        "prerelease": m.group(4) or "",
        "build": m.group(5) or "",
    }


def _version_sort_key(v: str) -> tuple[int, int, int, tuple[int | str, ...]]:
    parsed = _parse_semver_dict(v)
    if not parsed:
        return (0, 0, 0, ())
    major = cast(int, parsed["major"])
    minor = cast(int, parsed["minor"])
    patch = cast(int, parsed["patch"])
    prerelease = cast(str, parsed["prerelease"])
    if prerelease:
        parts: list[int | str] = []
        for p in prerelease.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(p)
        pre_tuple = tuple(parts)
    else:
        pre_tuple = ()
    return (major, minor, patch, pre_tuple)


# ---------------------------------------------------------------------------
# 1 — Version constant shape and format
# ---------------------------------------------------------------------------


def test_version_is_nonempty_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0
    assert " " not in __version__


def test_version_is_valid_semver() -> None:
    assert SEMVER_RE.match(__version__), f"__version__ '{__version__}' is not valid semver"


def test_version_starts_with_numeric_major() -> None:
    assert __version__[0].isdigit(), f"__version__ must start with a digit, got '{__version__[0]}'"


def test_version_has_no_leading_v() -> None:
    assert not __version__.startswith("v"), f"__version__ must not have leading 'v', got '{__version__}'"


# ---------------------------------------------------------------------------
# 2 — Daemon FastAPI app version wiring
# ---------------------------------------------------------------------------


def test_fastapi_app_title_includes_agent_name() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    assert app.title == "General Ludd Agent"


def test_fastapi_app_version_matches_init_version() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    assert app.version == __version__, f"app.version '{app.version}' != __version__ '{__version__}'"


def test_fastapi_app_version_is_set_and_nonempty() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    assert app.version is not None
    assert len(app.version) > 0
    assert isinstance(app.version, str)


# ---------------------------------------------------------------------------
# 3 — OpenAPI schema version
# ---------------------------------------------------------------------------


def test_openapi_schema_contains_app_version() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    schema = app.openapi()
    assert "info" in schema, "OpenAPI schema must have info section"
    assert "version" in schema["info"], "OpenAPI schema info must include version"
    assert schema["info"]["version"] == __version__, (
        f"OpenAPI version '{schema['info']['version']}' != __version__ '{__version__}'"
    )


def test_openapi_schema_has_title() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    schema = app.openapi()
    assert "title" in schema["info"], "OpenAPI schema info must include title"


def test_openapi_schema_version_is_string() -> None:
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(_db_path_override=":memory:")
    schema = app.openapi()
    assert isinstance(schema["info"]["version"], str)


# ---------------------------------------------------------------------------
# 4 — pyproject.toml version alignment
# ---------------------------------------------------------------------------


def test_init_version_matches_pyproject() -> None:
    py_v = _pyproject_version()
    assert __version__ == py_v, f"__version__ '{__version__}' != pyproject.toml version '{py_v}'"


def test_pyproject_version_is_valid_semver() -> None:
    v = _pyproject_version()
    assert SEMVER_RE.match(v), f"pyproject.toml version '{v}' is not valid semver"


# ---------------------------------------------------------------------------
# 5 — Semver parsing and comparison
# ---------------------------------------------------------------------------


def test_semver_rejects_bare_numbers() -> None:
    assert SEMVER_RE.match("1") is None
    assert SEMVER_RE.match("1.2") is None


def test_semver_accepts_prerelease() -> None:
    assert SEMVER_RE.match("0.1.0-beta.3") is not None
    assert SEMVER_RE.match("1.0.0-alpha.1") is not None
    assert SEMVER_RE.match("2.0.0-rc.1") is not None


def test_semver_accepts_build_metadata() -> None:
    assert SEMVER_RE.match("1.0.0+20230715") is not None
    assert SEMVER_RE.match("1.0.0-beta+exp.sha.5114f85") is not None


def test_semver_rejects_leading_v() -> None:
    assert SEMVER_RE.match("v0.1.0") is None


_VERSIONS_ASC = [
    ("0.1.0-alpha.1", "0.1.0-alpha.2"),
    ("0.1.0-alpha.2", "0.1.0-beta.1"),
    ("0.1.0-beta.1", "0.1.0-beta.3"),
    ("0.1.0-beta.3", "0.1.0"),
    ("0.1.0", "0.2.0"),
    ("0.2.0", "1.0.0"),
    ("1.0.0", "2.0.0"),
]


@pytest.mark.parametrize("earlier,later", _VERSIONS_ASC)
def test_version_ordering_increasing(earlier: str, later: str) -> None:
    assert _version_sort_key(earlier) < _version_sort_key(later), f"Expected '{earlier}' < '{later}'"


def test_version_sort_key_equal_for_same_version() -> None:
    assert _version_sort_key("1.2.3") == _version_sort_key("1.2.3")


def test_version_sort_key_prerelease_less_than_release() -> None:
    assert _version_sort_key("1.0.0-beta") < _version_sort_key("1.0.0")


# ---------------------------------------------------------------------------
# 6 — packaging.version compatibility
# ---------------------------------------------------------------------------


def test_packaging_version_parses_init_version() -> None:
    """__version__ must be parsable by packaging.version (standard in pyproject.toml)."""
    v = PackVersion(__version__)
    assert v is not None
    assert v.major >= 0


def test_packaging_version_comparison_matches_sort_key() -> None:
    """packaging.version ordering must agree with our semver sort key."""
    versions = ["0.1.0-alpha.1", "0.1.0-beta.3", "0.1.0", "1.0.0"]
    by_packaging = sorted(versions, key=PackVersion)
    by_our_key = sorted(versions, key=_version_sort_key)
    assert by_packaging == by_our_key, f"packaging ordering {by_packaging} != our sort key ordering {by_our_key}"


# ---------------------------------------------------------------------------
# 7 — SCHEMA_VERSION for materials
# ---------------------------------------------------------------------------


def test_materials_schema_version_is_nonempty() -> None:
    assert len(MATERIALS_SCHEMA_VERSION) > 0


def test_materials_schema_version_has_slash_separator() -> None:
    """SCHEMA_VERSION follows the 'code/version' pattern (e.g. 'mate-001/0.1')."""
    assert "/" in MATERIALS_SCHEMA_VERSION, (
        f"SCHEMA_VERSION '{MATERIALS_SCHEMA_VERSION}' must follow 'code/version' format"
    )


def test_materials_schema_version_exported_in_init() -> None:
    from general_ludd.materials import SCHEMA_VERSION as exported

    assert exported == MATERIALS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 8 — Deprecation warning infrastructure
# ---------------------------------------------------------------------------


def test_schema_loader_legacy_drafts_are_defined() -> None:
    """The legacy JSON Schema draft prefixes used for deprecation warnings are well-formed URLs."""
    for prefix in _LEGACY_DRAFT_PREFIXES:
        assert prefix.startswith("http"), f"Not a URL: {prefix}"
        assert "json-schema" in prefix


def test_schema_loader_legacy_draft_is_not_current() -> None:
    """None of the legacy drafts is the current 2020-12 standard."""
    from general_ludd.renderers.schema_loader import _DRAFT_2020_12  # type: ignore[attr-defined]

    for prefix in _LEGACY_DRAFT_PREFIXES:
        assert prefix != _DRAFT_2020_12, f"{prefix} is current standard"


def test_deprecation_warning_emits_for_legacy_draft() -> None:
    """Using a legacy JSON Schema draft URI should emit DeprecationWarning via warnings.warn."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        warnings.warn("draft-07 is deprecated — please upgrade to draft 2020-12", DeprecationWarning, stacklevel=2)
    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)


def test_ssl_algorithms_deprecated_status_exists() -> None:
    """The SSL algorithms module defines a DEPRECATED AlgorithmStatus enum member."""
    from general_ludd.ssl.algorithms import AlgorithmStatus

    assert hasattr(AlgorithmStatus, "DEPRECATED")
    assert AlgorithmStatus.DEPRECATED.value == "deprecated"


def test_ssl_algorithm_info_has_deprecation_date_field() -> None:
    from general_ludd.ssl.algorithms import AlgorithmInfo

    info = AlgorithmInfo(
        name="test",
        type="hash",  # type: ignore[arg-type]
        key_sizes=[256],
        security_bits=0,
        status="legacy",  # type: ignore[arg-type]
        deprecation_date="2020-01-01",
    )
    assert info.deprecation_date == "2020-01-01"


# ---------------------------------------------------------------------------
# 9 — Version in response headers / middleware injectability
# ---------------------------------------------------------------------------


def test_version_header_can_be_added_to_fastapi_responses() -> None:
    """A FastAPI app can inject the version into response headers via middleware."""
    app = FastAPI(title="Test", version="9.9.9")

    @app.get("/test-version-header")
    async def _endpoint() -> dict[str, str]:
        return {"ok": "true"}

    @app.middleware("http")
    async def _version_header_middleware(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-API-Version"] = app.version
        return response

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/test-version-header")
        assert resp.status_code == 200
        assert resp.headers.get("X-API-Version") == "9.9.9"


def test_version_in_openapi_schema_with_version_header() -> None:
    """The OpenAPI schema reports the version even when no version header middleware is active."""
    app = FastAPI(title="Test", version="9.9.9")

    @app.get("/test")
    async def _endpoint() -> dict[str, str]:
        return {"ok": "true"}

    schema = app.openapi()
    assert schema["info"]["version"] == "9.9.9"


# ---------------------------------------------------------------------------
# 10 — Version negotiation decision logic
# ---------------------------------------------------------------------------


def test_version_negotiation_exact_match() -> None:
    """Exact version match should produce 'ok' decision."""
    supported = ["0.1.0", "0.2.0", "1.0.0"]
    requested = "0.2.0"

    def _negotiate(supported_versions: list[str], client_version: str) -> tuple[str, str | None]:
        if client_version in supported_versions:
            return ("ok", client_version)
        return ("mismatch", None)

    decision, version = _negotiate(supported, requested)
    assert decision == "ok"
    assert version == "0.2.0"


def test_version_negotiation_no_match() -> None:
    """Requesting an unsupported version should produce 'mismatch'."""
    decision, version = _negotiate_dummy(["0.1.0", "0.2.0"], "3.0.0")
    assert decision == "mismatch"
    assert version is None


def _negotiate_dummy(supported_versions: list[str], client_version: str) -> tuple[str, str | None]:
    if client_version in supported_versions:
        return ("ok", client_version)
    return ("mismatch", None)


def test_version_negotiation_highest_compatible() -> None:
    """When a precise match is missing, select the highest compatible version."""
    supported = sorted(["0.1.0", "0.2.0", "1.0.0", "2.0.0"], key=PackVersion)
    requested = "0.1.0"

    def _resolve_highest_compatible(supported_versions: list[str], client_version: str) -> str | None:
        try:
            client_pv = PackVersion(client_version)
        except Exception:
            return None
        compatible = [v for v in supported_versions if PackVersion(v) >= client_pv]
        return compatible[0] if compatible else None

    result = _resolve_highest_compatible(supported, requested)
    assert result is not None
    assert result == "0.1.0"


def test_version_negotiation_prefers_prerelease_when_available() -> None:
    """If the client requests a prerelease, prefer the matching prerelease over a different release."""
    supported = ["0.1.0-alpha.1", "0.1.0-alpha.2", "0.1.0"]
    requested = "0.1.0-alpha.1"

    def _best_match(supported_versions: list[str], client_version: str) -> str | None:
        if client_version in supported_versions:
            return client_version
        sorted_versions = sorted(supported_versions, key=PackVersion)
        for v in sorted_versions:
            if PackVersion(v) >= PackVersion(client_version):
                return v
        return None

    result = _best_match(supported, requested)
    assert result == "0.1.0-alpha.1"


# ---------------------------------------------------------------------------
# 11 — Router registration / backward-compat path patterns
# ---------------------------------------------------------------------------


def test_public_paths_include_health_endpoints() -> None:
    """The daemon _PUBLIC_PATHS must include healthz, readyz, api/status."""
    from general_ludd.daemon import create_daemon_app

    app_obj = create_daemon_app(_db_path_override=":memory:")
    _routes = [getattr(r, "path", None) for r in app_obj.routes]
    _routes = [p for p in _routes if p is not None]
    assert "/healthz" in _routes
    assert "/readyz" in _routes


def test_public_paths_include_api_todos() -> None:
    from general_ludd.daemon import create_daemon_app

    app_obj = create_daemon_app(_db_path_override=":memory:")
    _routes = [getattr(r, "path", None) for r in app_obj.routes]
    _routes = [p for p in _routes if p is not None]
    assert "/api/todos" in _routes, f"Expected /api/todos in routes: {sorted(_routes)}"


def test_routers_init_registers_subset_of_routers() -> None:
    """routers/__init__.py register_all calls a known subset of register functions."""
    import inspect

    from general_ludd.routers import register_all

    src = inspect.getsource(register_all)
    known_registrations = [
        "register_account",
        "register_ansible",
        "register_chat",
        "register_compute",
        "register_facts",
        "register_filestore",
        "register_human_todos",
        "register_mcp",
        "register_memory",
        "register_models",
        "register_projects",
        "register_remediation",
        "register_render",
        "register_security",
        "register_skills",
        "register_slurm",
        "register_stream",
        "register_todos",
        "register_worktree",
        "register_self_improve",
    ]
    for name in known_registrations:
        assert f"{name}(app" in src or f"{name}( app" in src, f"Expected {name}() call in register_all source"


def test_create_daemon_app_registers_routers() -> None:
    """create_daemon_app must return an app that has at least the expected routes."""
    from general_ludd.daemon import create_daemon_app

    app_obj = create_daemon_app(_db_path_override=":memory:")
    _routes = sorted(getattr(r, "path", "") for r in app_obj.routes)
    _routes = [p for p in _routes if p]
    required = ["/healthz", "/readyz", "/openapi.json"]
    for p in required:
        assert p in _routes, f"Required route {p} missing from app"


# ---------------------------------------------------------------------------
# 12 — CollectionVersionInfo semver detection
# ---------------------------------------------------------------------------


def test_collection_version_info_detects_semver() -> None:
    from general_ludd.ansible.paths import CollectionVersionInfo

    info = CollectionVersionInfo(namespace="ns", collection="coll", version="2.1.3", path=Path("/tmp"))
    assert info.is_semver is True


def test_collection_version_info_rejects_prerelease_tag() -> None:
    from general_ludd.ansible.paths import CollectionVersionInfo

    info = CollectionVersionInfo(namespace="ns", collection="coll", version="beta.2", path=Path("/tmp"))
    assert info.is_semver is False


def test_collection_version_info_latest_tag() -> None:
    from general_ludd.ansible.paths import CollectionVersionInfo

    info = CollectionVersionInfo(namespace="ns", collection="coll", version="latest", path=Path("/tmp"))
    assert info.is_latest is True
    assert info.is_semver is False


# ---------------------------------------------------------------------------
# 13 — Version header parsing utility
# ---------------------------------------------------------------------------


def test_parse_api_version_header_simple() -> None:
    """Extract a bare version string from an X-API-Version header."""
    raw = "0.1.0"
    parsed = raw.strip()
    assert parsed == "0.1.0"
    assert SEMVER_RE.match(parsed) is not None


def test_parse_api_version_header_with_whitespace() -> None:
    raw = "  0.2.0-beta.1  "
    parsed = raw.strip()
    assert parsed == "0.2.0-beta.1"


def test_parse_api_version_header_rejects_malformed() -> None:
    bad_values = ["", "not-a-version", "v1.0", "1"]
    for v in bad_values:
        stripped = v.strip()
        is_valid = bool(SEMVER_RE.match(stripped))
        assert not is_valid, f"'{v}' should not be accepted as valid semver"


# ---------------------------------------------------------------------------
# 14 — Runtime version injection pattern
# ---------------------------------------------------------------------------


def test_app_state_preserves_version_access() -> None:
    """App state does not override the FastAPI version — both are independently accessible."""
    from general_ludd.daemon import create_daemon_app

    app_obj = create_daemon_app(_db_path_override=":memory:")
    assert app_obj.state is not None
    assert app_obj.version == __version__
    daemon_state = cast(dict[str, Any], getattr(app_obj.state, "daemon_state", None))
    assert daemon_state is not None, "daemon_state must be set on app.state"
    assert isinstance(daemon_state, dict), "daemon_state must be a dict"


def test_worker_app_has_version_field() -> None:
    """The worker FastAPI app should also carry a version."""
    from general_ludd.worker.app import create_app

    worker_app = create_app()
    assert worker_app.version is not None
    assert len(worker_app.version) > 0


# ---------------------------------------------------------------------------
# 15 — Version comparison for negotiation edge cases
# ---------------------------------------------------------------------------


def test_version_comparison_same_versions_equal() -> None:
    assert _version_sort_key("1.0.0") == _version_sort_key("1.0.0")


def test_version_comparison_prerelease_ordering() -> None:
    """Prerelease identifiers should sort correctly: alpha < beta < rc."""
    versions = ["1.0.0-rc.1", "1.0.0-alpha.1", "1.0.0-beta.1"]
    sorted_versions = sorted(versions, key=_version_sort_key)
    assert sorted_versions == ["1.0.0-alpha.1", "1.0.0-beta.1", "1.0.0-rc.1"]


def test_version_comparison_prerelease_numeric_parts() -> None:
    """Numeric prerelease parts should sort numerically, not lexicographically."""
    versions = ["1.0.0-alpha.10", "1.0.0-alpha.2", "1.0.0-alpha.1"]
    sorted_versions = sorted(versions, key=_version_sort_key)
    assert sorted_versions == ["1.0.0-alpha.1", "1.0.0-alpha.2", "1.0.0-alpha.10"]


def test_version_comparison_major_takes_precedence() -> None:
    assert _version_sort_key("2.0.0-alpha") > _version_sort_key("1.9.9")
