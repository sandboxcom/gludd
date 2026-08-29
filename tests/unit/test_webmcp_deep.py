"""Deep invariant tests for the webmcp self-description.

These tests check structural consistency, completeness, and cross-referencing
across the webmcp data tables — going beyond the shape/documentation tests
in the existing test_webmcp_* files to assert that the data is internally
consistent and honest.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest

from general_ludd.daemon import _PUBLIC_PATHS_FROZEN, is_public_path
from general_ludd.routers.webmcp import (
    _ENDPOINTS,
    _ERROR_RESPONSES,
    _FACTS_FACETS,
    _PUBLIC_PATHS,
    _SELF_DESCRIPTION,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find(method: str, path: str) -> dict[str, Any]:
    for ep in _ENDPOINTS:
        if ep["path"] == path and ep["method"].upper() == method.upper():
            return ep
    raise AssertionError(f"{method} {path} not found in endpoint inventory")


# ---------------------------------------------------------------------------
# Endpoint inventory structure tests
# ---------------------------------------------------------------------------


class TestEndpointInventoryStructure:
    """Every endpoint entry must carry required fields with correct types."""

    REQUIRED_FIELDS: ClassVar[set[str]] = {"method", "path", "purpose", "auth_required"}
    VALID_METHODS: ClassVar[set[str]] = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

    def test_no_duplicate_method_path(self):
        seen = set()
        for ep in _ENDPOINTS:
            key = (ep["method"], ep["path"])
            assert key not in seen, f"Duplicate endpoint: {key}"
            seen.add(key)

    @pytest.mark.parametrize("ep", _ENDPOINTS, ids=lambda e: f"{e['method']} {e['path']}")
    def test_required_fields_present(self, ep):
        missing = self.REQUIRED_FIELDS - set(ep.keys())
        assert not missing, f"{ep['method']} {ep['path']} missing: {sorted(missing)}"

    @pytest.mark.parametrize("ep", _ENDPOINTS, ids=lambda e: f"{e['method']} {e['path']}")
    def test_method_is_valid_http(self, ep):
        assert ep["method"] in self.VALID_METHODS, f"{ep['method']} is not a valid HTTP method"

    @pytest.mark.parametrize("ep", _ENDPOINTS, ids=lambda e: f"{e['method']} {e['path']}")
    def test_path_starts_with_slash(self, ep):
        assert ep["path"].startswith("/"), f"Path {ep['path']!r} does not start with /"

    @pytest.mark.parametrize("ep", _ENDPOINTS, ids=lambda e: f"{e['method']} {e['path']}")
    def test_auth_required_is_bool(self, ep):
        assert isinstance(ep["auth_required"], bool), f"auth_required must be bool, got {type(ep['auth_required'])}"


# ---------------------------------------------------------------------------
# Public-path / auth consistency
# ---------------------------------------------------------------------------


class TestPublicPathConsistency:
    """Every path in _PUBLIC_PATHS must match a documented endpoint with
    auth_required=False, and vice versa."""

    def test_public_paths_are_documented_endpoints(self):
        endpoint_paths = {(e["method"], e["path"]) for e in _ENDPOINTS}
        for p in _PUBLIC_PATHS:
            assert ("GET", p) in endpoint_paths or ("POST", p) in endpoint_paths, (
                f"Public path {p!r} has no documented endpoint"
            )

    def test_auth_false_endpoints_are_in_public_paths(self) -> None:
        for ep in _ENDPOINTS:
            if ep["auth_required"] is False:
                method = cast(str, ep["method"])
                path = cast(str, ep["path"])
                assert is_public_path(method, path), (
                    f"{ep['method']} {ep['path']} has auth_required=False but "
                    "the daemon's method-aware policy protects it"
                )

    def test_auth_true_endpoints_are_not_in_public_paths(self) -> None:
        for ep in _ENDPOINTS:
            if ep["auth_required"] is True:
                method = cast(str, ep["method"])
                path = cast(str, ep["path"])
                assert not is_public_path(method, path), (
                    f"{ep['method']} {ep['path']} has auth_required=True but "
                    "the daemon's method-aware policy exposes it"
                )

    def test_public_paths_match_daemon_exact_allowlist(self) -> None:
        assert set(_PUBLIC_PATHS) == set(_PUBLIC_PATHS_FROZEN), (
            "webmcp public_paths drifted from the daemon's exact allowlist"
        )

    def test_webmcp_itself_is_public(self):
        assert "/api/webmcp" in _PUBLIC_PATHS, "/api/webmcp must be in public_paths — bootstrap deadlock prevention"


# ---------------------------------------------------------------------------
# POST endpoint shape completeness
# ---------------------------------------------------------------------------


class TestPostEndpointShapes:
    """Every POST endpoint must document its request body, and should document
    its response shape."""

    @pytest.mark.parametrize(
        "ep",
        [e for e in _ENDPOINTS if e["method"] == "POST"],
        ids=lambda e: f"POST {e['path']}",
    )
    def test_post_has_request_body(self, ep):
        assert "request_body" in ep, (
            f"POST {ep['path']} has no request_body documented — a consumer cannot know what fields to send"
        )
        rb = ep["request_body"]
        assert isinstance(rb, dict) and rb, f"POST {ep['path']} request_body is empty; must document expected fields"

    @pytest.mark.parametrize(
        "ep",
        [e for e in _ENDPOINTS if e["method"] == "POST"],
        ids=lambda e: f"POST {e['path']}",
    )
    def test_post_has_response_shape(self, ep):
        assert "response_shape" in ep, (
            f"POST {ep['path']} has no response_shape documented — a consumer cannot know what shape to expect back"
        )


# ---------------------------------------------------------------------------
# GET endpoint response_shape completeness (where documented)
# ---------------------------------------------------------------------------


class TestGetEndpointResponseShapes:
    """When a GET endpoint carries a response_shape dict, it must not be empty."""

    @pytest.mark.parametrize(
        "ep",
        [e for e in _ENDPOINTS if "response_shape" in e],
        ids=lambda e: f"{e['method']} {e['path']}",
    )
    def test_response_shape_dict_is_nonempty(self, ep):
        shape = ep["response_shape"]
        if isinstance(shape, dict):
            assert shape, (
                f"{ep['method']} {ep['path']} response_shape is an empty dict; "
                "document the keys or use a string description instead"
            )


# ---------------------------------------------------------------------------
# facts_facets cross-reference with /api/facts endpoint
# ---------------------------------------------------------------------------


class TestFactsFacetsCrossReference:
    """The facts_facets list must match the /api/facts response_shape keys."""

    def test_facts_facets_matches_facts_response_shape(self):
        facts_ep = _find("GET", "/api/facts")
        assert "response_shape" in facts_ep
        shape_keys = set(facts_ep["response_shape"].keys())
        facet_set = set(_FACTS_FACETS)

        missing_from_shape = facet_set - shape_keys
        assert not missing_from_shape, (
            f"facts_facets contains keys NOT in /api/facts response_shape: {sorted(missing_from_shape)}"
        )

        missing_from_facets = shape_keys - facet_set
        assert not missing_from_facets, (
            f"/api/facts response_shape has keys NOT in facts_facets: {sorted(missing_from_facets)}"
        )

    def test_facts_facets_is_sorted(self):
        assert sorted(_FACTS_FACETS) == _FACTS_FACETS, (
            "facts_facets must be alphabetically sorted for diff-friendliness"
        )


# ---------------------------------------------------------------------------
# Error response structural tests
# ---------------------------------------------------------------------------


class TestErrorResponses:
    """The error_responses section must be complete and honest."""

    def test_error_keys_are_numeric_strings(self):
        for key in _ERROR_RESPONSES:
            assert key.isdigit(), f"error_responses key {key!r} is not a numeric status code"

    def test_common_error_codes_present(self):
        for code in ("401", "422", "503"):
            assert code in _ERROR_RESPONSES, f"error_responses missing {code} — the daemon emits this status"

    def test_401_needs_auth_true_endpoints_exist(self):
        """If 401 is documented, at least one endpoint must require auth."""
        protected = [e for e in _ENDPOINTS if e["auth_required"] is True]
        assert protected, "error_responses documents 401 but no endpoint has auth_required=True"

    def test_error_entries_have_meaning_and_body(self):
        for code, entry in _ERROR_RESPONSES.items():
            assert "meaning" in entry, f"{code} error entry missing 'meaning'"
            assert "body" in entry, f"{code} error entry missing 'body'"


# ---------------------------------------------------------------------------
# Auth section completeness
# ---------------------------------------------------------------------------


class TestAuthSection:
    """The auth section must carry all fields a fresh consumer needs."""

    REQUIRED_AUTH_KEYS: ClassVar[set[str]] = {
        "type",
        "header",
        "scheme",
        "format",
        "env_var",
        "require_auth_env_var",
        "description",
        "public_paths",
        "note",
    }

    def test_all_required_keys_present(self):
        auth = _SELF_DESCRIPTION["auth"]
        missing = self.REQUIRED_AUTH_KEYS - set(auth.keys())
        assert not missing, f"auth section missing: {sorted(missing)}"

    def test_type_is_psk(self):
        assert _SELF_DESCRIPTION["auth"]["type"] == "PSK"

    def test_header_is_authorization(self):
        assert _SELF_DESCRIPTION["auth"]["header"] == "Authorization"

    def test_scheme_is_bearer(self):
        assert _SELF_DESCRIPTION["auth"]["scheme"] == "Bearer"

    def test_format_contains_bearer_token(self):
        fmt = _SELF_DESCRIPTION["auth"]["format"]
        assert "Bearer" in fmt and "token" in fmt.lower()

    def test_public_paths_matches_source_list(self):
        assert _SELF_DESCRIPTION["auth"]["public_paths"] is _PUBLIC_PATHS, (
            "auth.public_paths must reference _PUBLIC_PATHS directly (same object)"
        )


# ---------------------------------------------------------------------------
# Self-description top-level structure
# ---------------------------------------------------------------------------


class TestSelfDescriptionStructure:
    """The _SELF_DESCRIPTION dict must carry the required top-level keys."""

    REQUIRED_TOP_KEYS: ClassVar[set[str]] = {
        "name",
        "description",
        "auth",
        "facts_facets",
        "endpoints",
        "error_responses",
    }

    def test_required_top_level_keys_present(self):
        missing = self.REQUIRED_TOP_KEYS - set(_SELF_DESCRIPTION.keys())
        assert not missing, f"_SELF_DESCRIPTION missing: {sorted(missing)}"

    def test_name_matches_package(self):
        assert "general-ludd" in _SELF_DESCRIPTION["name"]

    def test_description_is_reasonably_long(self):
        assert len(_SELF_DESCRIPTION["description"]) > 50

    def test_facts_facets_references_source_list(self):
        assert _SELF_DESCRIPTION["facts_facets"] is _FACTS_FACETS, (
            "facts_facets must reference _FACTS_FACETS directly (same object)"
        )

    def test_endpoints_references_source_list(self):
        assert _SELF_DESCRIPTION["endpoints"] is _ENDPOINTS, (
            "endpoints must reference _ENDPOINTS directly (same object)"
        )


# ---------------------------------------------------------------------------
# Idempotent registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """The register() function must be idempotent and safe to call multiple times."""

    def test_register_is_idempotent(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.webmcp import register

        app = FastAPI()
        register(app, {})
        register(app, {})  # second call must not raise or duplicate routes

        client = TestClient(app)
        resp = client.get("/api/webmcp")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "general-ludd-agent"

    def test_register_with_different_apps(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.webmcp import register

        app1 = FastAPI()
        register(app1, {})
        app2 = FastAPI()
        register(app2, {})

        c1 = TestClient(app1)
        c2 = TestClient(app2)
        assert c1.get("/api/webmcp").status_code == 200
        assert c2.get("/api/webmcp").status_code == 200

    def test_daemon_state_not_mutated(self):
        from fastapi import FastAPI

        from general_ludd.routers.webmcp import register

        state: dict[str, object] = {"existing_key": "value"}
        app = FastAPI()
        register(app, state)
        assert "existing_key" in state
        assert state["existing_key"] == "value", "register() must not mutate the daemon_state dict"

    def test_endpoint_response_is_deterministic(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.webmcp import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        r1 = client.get("/api/webmcp").json()
        r2 = client.get("/api/webmcp").json()
        assert r1 == r2, "GET /api/webmcp must return the same response every call"


# ---------------------------------------------------------------------------
# Endpoint path uniqueness (path-level, to catch colliding routes)
# ---------------------------------------------------------------------------


class TestEndpointPathUniqueness:
    """Paths within the same HTTP method must not collide."""

    def test_no_duplicate_paths_within_same_method(self):
        seen: dict[str, set[str]] = {}
        for ep in _ENDPOINTS:
            method = ep["method"]
            path = ep["path"]
            seen.setdefault(method, set()).add(path)

    def test_no_single_endpoint_multiple_methods_collision(self):
        """Two endpoints with the same path but different methods must have
        consistent auth_required values (e.g. GET /api/todos is public,
        POST /api/todos is not — both must be documented correctly)."""
        by_path: dict[str, list[dict]] = {}
        for ep in _ENDPOINTS:
            by_path.setdefault(ep["path"], []).append(ep)
        for path, eps in by_path.items():
            if len(eps) > 1:
                methods = {e["method"] for e in eps}
                assert len(methods) == len(eps), (
                    f"Path {path} has multiple entries with the same method: methods={methods}"
                )


# ---------------------------------------------------------------------------
# Endpoint count sanity
# ---------------------------------------------------------------------------


class TestEndpointCount:
    """The inventory must be non-trivial in size."""

    def test_minimum_endpoint_count(self):
        assert len(_ENDPOINTS) >= 15, f"Only {len(_ENDPOINTS)} endpoints documented; expected at least 15"

    def test_at_least_one_protected_endpoint(self):
        protected = [e for e in _ENDPOINTS if e["auth_required"] is True]
        assert len(protected) >= 3, f"Only {len(protected)} protected endpoints; expected at least 3"

    def test_at_least_one_public_endpoint(self):
        public = [e for e in _ENDPOINTS if e["auth_required"] is False]
        assert len(public) >= 3, f"Only {len(public)} public endpoints; expected at least 3"
