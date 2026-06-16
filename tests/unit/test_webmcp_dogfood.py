"""Dogfood the webmcp self-description from a context-free consumer's POV.

These tests take the perspective of a fresh API consumer who has read NOTHING
about gludd except the bytes returned by ``GET /api/webmcp``.  For each thing
the self-description SHOULD make discoverable but historically did not, there is
a test that asserts the gap is closed.  Each was written failing-first, then the
webmcp text in ``general_ludd.routers.webmcp`` was extended until it passed.

Unlike ``tests/integration/test_webmcp_self_describes.py`` (which boots the real
daemon app over ASGITransport), these are pure unit assertions against the
in-process ``_SELF_DESCRIPTION`` dict — no event loop, no DB, no network.  Every
assertion is grounded in behaviour that genuinely exists on the daemon
(``daemon.py`` middleware error shapes, ``routers/todos.py`` ``/api/status``
shape, ``routers/spend.py`` ``/api/spend`` shape), so the documentation stays
HONEST: nothing is asserted that the daemon does not actually do.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.routers.webmcp import _SELF_DESCRIPTION


def _endpoint(method: str, path: str) -> dict[str, Any]:
    """Return the inventory entry for ``method path`` or fail the test."""
    for ep in _SELF_DESCRIPTION["endpoints"]:
        if ep["path"] == path and ep["method"].upper() == method.upper():
            return ep
    raise AssertionError(
        f"{method} {path} is absent from the webmcp endpoint inventory; a "
        "context-free consumer cannot discover it."
    )


class TestAuthDiscoverability:
    """A fresh consumer must learn the FULL auth posture, including fail-closed."""

    def test_require_auth_env_var_is_documented(self):
        """The GLUDD_REQUIRE_AUTH fail-closed switch must be discoverable.

        daemon.py honours GLUDD_REQUIRE_AUTH: when truthy and no PSK is
        configured, every non-public path is refused with 503 instead of being
        served open.  A consumer who sees 503s has no way to understand why
        unless the self-description names this env var.
        """
        auth = _SELF_DESCRIPTION["auth"]
        blob = repr(auth)
        assert "GLUDD_REQUIRE_AUTH" in blob, (
            "auth section never mentions GLUDD_REQUIRE_AUTH; a consumer cannot "
            "understand a fail-closed 503 response."
        )

    def test_require_auth_has_dedicated_field(self):
        """GLUDD_REQUIRE_AUTH must be a structured field, not buried in prose."""
        auth = _SELF_DESCRIPTION["auth"]
        assert "require_auth_env_var" in auth, (
            "auth.require_auth_env_var is missing; the fail-closed switch should "
            "be a machine-readable field, not only free text."
        )
        assert auth["require_auth_env_var"] == "GLUDD_REQUIRE_AUTH"

    def test_no_psk_dev_mode_documented(self):
        """The consumer must learn that an unset PSK = open dev mode."""
        blob = repr(_SELF_DESCRIPTION["auth"]).lower()
        assert "psk" in blob and ("disabled" in blob or "dev" in blob), (
            "auth section does not explain the no-PSK (auth-disabled) posture."
        )


class TestErrorFormats:
    """A consumer must be able to parse failure responses, not just successes."""

    def test_error_responses_section_present(self):
        """The self-description must document the daemon's error envelope shapes."""
        assert "error_responses" in _SELF_DESCRIPTION, (
            "GET /api/webmcp has no 'error_responses' key; a consumer cannot "
            "know how 401/422/503 bodies are shaped."
        )

    def test_401_unauthorized_shape_documented(self):
        """401 returns {'error': 'unauthorized'} (see daemon.py middleware)."""
        errs = _SELF_DESCRIPTION["error_responses"]
        assert "401" in errs, "error_responses missing the 401 entry."
        blob = repr(errs["401"])
        assert "error" in blob and "unauthorized" in blob, (
            f"401 error shape does not match the daemon's "
            f"{{'error': 'unauthorized'}} body: {errs['401']!r}"
        )

    def test_503_auth_required_shape_documented(self):
        """503 (GLUDD_REQUIRE_AUTH, no PSK) returns {'error':'auth_required', ...}."""
        errs = _SELF_DESCRIPTION["error_responses"]
        assert "503" in errs, "error_responses missing the 503 entry."
        blob = repr(errs["503"])
        assert "auth_required" in blob, (
            f"503 error shape does not mention 'auth_required' (the daemon's "
            f"fail-closed body): {errs['503']!r}"
        )

    def test_422_validation_error_documented(self):
        """FastAPI returns 422 with a 'detail' array on bad request bodies."""
        errs = _SELF_DESCRIPTION["error_responses"]
        assert "422" in errs, "error_responses missing the 422 entry."
        blob = repr(errs["422"]).lower()
        assert "detail" in blob, (
            f"422 error shape does not mention the FastAPI 'detail' field: "
            f"{errs['422']!r}"
        )


class TestStatusEndpointShape:
    """/api/status is the first public call a fresh consumer makes — shape it."""

    def test_status_has_response_shape(self):
        ep = _endpoint("GET", "/api/status")
        assert "response_shape" in ep, (
            "GET /api/status has no response_shape; the first public probe a "
            "consumer hits is undocumented."
        )

    def test_status_shape_lists_core_keys(self):
        """Documented keys must match what routers/todos.py:api_status returns."""
        shape = _endpoint("GET", "/api/status")["response_shape"]
        assert isinstance(shape, dict), "/api/status response_shape must be a dict."
        for key in ("version", "uptime_ticks", "todos_total", "queue_depths", "quality_gate"):
            assert key in shape, (
                f"/api/status response_shape is missing {key!r}; the live "
                "api_status handler returns it."
            )


class TestSpendEndpointShape:
    """/api/spend purpose lists fields in prose — give it a machine shape too."""

    def test_spend_has_response_shape(self):
        ep = _endpoint("GET", "/api/spend")
        assert "response_shape" in ep, (
            "GET /api/spend has no machine-readable response_shape (only prose)."
        )

    def test_spend_shape_lists_limiter_fields(self):
        """Keys must match routers/spend.py:api_spend_status output."""
        shape = _endpoint("GET", "/api/spend")["response_shape"]
        assert isinstance(shape, dict), "/api/spend response_shape must be a dict."
        for key in (
            "limiter_active",
            "window_spend_usd",
            "limit_usd",
            "remaining_usd",
            "window_seconds",
        ):
            assert key in shape, (
                f"/api/spend response_shape is missing {key!r}; the live "
                "api_spend_status handler returns it."
            )


class TestScheduleEndpointDiscoverable:
    """POST /api/schedule must expose its item shape and cycle error (409)."""

    def test_schedule_request_body_documented(self):
        ep = _endpoint("POST", "/api/schedule")
        assert "request_body" in ep and "items" in ep["request_body"], (
            "POST /api/schedule does not document its 'items' request body."
        )

    def test_schedule_cycle_error_documented(self):
        """The 409-on-cycle behaviour must be discoverable to a consumer."""
        ep = _endpoint("POST", "/api/schedule")
        blob = repr(ep).lower()
        assert "409" in blob or "cycle" in blob, (
            "POST /api/schedule never mentions the 409 dependency-cycle error."
        )


class TestSelfDescriptionStaysHonest:
    """Guardrails so future edits can't drift the doc away from reality."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/todos"),
            ("GET", "/api/status"),
            ("GET", "/api/spend"),
            ("POST", "/api/schedule"),
        ],
    )
    def test_documented_endpoints_carry_method_path_purpose(self, method, path):
        ep = _endpoint(method, path)
        for field in ("method", "path", "purpose"):
            assert ep.get(field), f"{method} {path} missing {field!r}."

    def test_facts_facets_nonempty(self):
        facets = _SELF_DESCRIPTION["facts_facets"]
        assert isinstance(facets, list) and facets, "facts_facets must be a non-empty list."
