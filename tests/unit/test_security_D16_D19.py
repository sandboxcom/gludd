"""TDD tests for security findings D-16 and D-19.

D-16: routers/dispatch.py api_dispatch — no per-request tool_calls cap.
      Fix: MAX_CALLS_PER_REQUEST = 20; return HTTP 422 if len(calls) > 20.

D-19: models/router.py ModelRouter.resolve_role — silently returns
      default_profile_id for ANY unmapped role.
      Fix: strict=False param; when strict=True an unrecognised role raises
      ValueError instead of falling through to the default.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.models.router import ModelRouter
from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST, register

# ---------------------------------------------------------------------------
# D-16 — per-request tool_calls cap
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    """Minimal FastAPI app with the dispatch router registered."""
    app = FastAPI()
    register(app, {})
    return app


class TestD16DispatchCap:
    """MAX_CALLS_PER_REQUEST = 20; >20 → HTTP 422; exactly 20 → OK."""

    def test_constant_is_twenty(self):
        assert MAX_CALLS_PER_REQUEST == 20

    def test_exactly_twenty_calls_ok(self):
        """Exactly 20 calls must succeed (return 200, not 422)."""
        client = TestClient(_make_app())
        body = {
            "tool_calls": [
                {"kind": "role", "name": f"role_{i}", "args": {}}
                for i in range(20)
            ]
        }
        resp = client.post("/api/dispatch", json=body)
        # 200 (dispatched) or any non-422 non-5xx is acceptable here;
        # the cap must not fire at exactly the limit.
        assert resp.status_code != 422, (
            f"Expected non-422 for exactly 20 calls; got {resp.status_code}: {resp.text}"
        )

    def test_twenty_one_calls_returns_422(self):
        """21 calls must be rejected with HTTP 422."""
        client = TestClient(_make_app())
        body = {
            "tool_calls": [
                {"kind": "role", "name": f"role_{i}", "args": {}}
                for i in range(21)
            ]
        }
        resp = client.post("/api/dispatch", json=body)
        assert resp.status_code == 422, (
            f"Expected 422 for 21 calls; got {resp.status_code}: {resp.text}"
        )

    def test_over_cap_detail_mentions_limit(self):
        """422 response body should reference the cap."""
        client = TestClient(_make_app())
        body = {
            "tool_calls": [
                {"kind": "role", "name": f"role_{i}"}
                for i in range(25)
            ]
        }
        resp = client.post("/api/dispatch", json=body)
        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        # Must mention the limit somewhere in the detail string.
        assert "20" in str(detail), f"422 detail should mention 20, got: {detail}"

    def test_zero_calls_still_422_empty(self):
        """Empty tool_calls list → 422 (existing behaviour — no calls parsed)."""
        client = TestClient(_make_app())
        resp = client.post("/api/dispatch", json={"tool_calls": []})
        assert resp.status_code == 422

    def test_single_call_not_capped(self):
        """Single inline call (not a list) is well under the cap."""
        client = TestClient(_make_app())
        body = {"kind": "role", "name": "planner", "args": {}}
        resp = client.post("/api/dispatch", json=body)
        assert resp.status_code != 422


# ---------------------------------------------------------------------------
# D-19 — ModelRouter.resolve_role strict mode
# ---------------------------------------------------------------------------

class TestD19StrictResolveRole:
    """strict=False (default) → falls through to default; strict=True →
    unrecognised role raises ValueError; mapped role always resolves."""

    def _router(self) -> ModelRouter:
        return ModelRouter(
            role_mapping={"planner": "gpt4o-profile", "reviewer": "gpt4o-mini-profile"},
            default_profile_id="default-profile",
        )

    # --- non-strict (default) ---

    def test_non_strict_known_role_resolves(self):
        r = self._router()
        assert r.resolve_role("planner") == "gpt4o-profile"

    def test_non_strict_unknown_role_falls_to_default(self):
        """Without strict=True an unrecognised role silently returns the default."""
        r = self._router()
        result = r.resolve_role("totally_unknown_role")
        assert result == "default-profile"

    def test_non_strict_unknown_role_no_default_returns_none(self):
        """Without a default and without strict, returns None for unmapped role."""
        r = ModelRouter(role_mapping={"planner": "gpt4o-profile"})
        assert r.resolve_role("unknown") is None

    # --- strict mode ---

    def test_strict_known_role_resolves(self):
        """A role that IS in the mapping resolves normally even with strict=True."""
        r = self._router()
        assert r.resolve_role("planner", strict=True) == "gpt4o-profile"

    def test_strict_weak_role_resolves(self):
        """The 'weak' sentinel still resolves in strict mode when weak_model is set."""
        r = ModelRouter(
            role_mapping={"planner": "gpt4o-profile"},
            default_profile_id="default-profile",
            weak_model_profile_id="gpt4o-mini-profile",
        )
        assert r.resolve_role("weak", strict=True) == "gpt4o-mini-profile"

    def test_strict_unknown_role_raises_value_error(self):
        """strict=True + unmapped role → ValueError (not silent default return)."""
        r = self._router()
        with pytest.raises(ValueError, match="totally_unknown_role"):
            r.resolve_role("totally_unknown_role", strict=True)

    def test_strict_unknown_role_error_names_the_role(self):
        """ValueError message includes the unrecognised role name."""
        r = self._router()
        with pytest.raises(ValueError) as exc_info:
            r.resolve_role("mystery_role", strict=True)
        assert "mystery_role" in str(exc_info.value)

    def test_strict_no_default_unknown_role_raises(self):
        """strict=True with no default_profile_id still raises for unknown role."""
        r = ModelRouter(role_mapping={"planner": "gpt4o-profile"})
        with pytest.raises(ValueError):
            r.resolve_role("unknown_role", strict=True)

    def test_non_strict_default_is_false(self):
        """Calling resolve_role without strict kwarg behaves as strict=False."""
        r = self._router()
        # Should NOT raise — strict defaults to False.
        result = r.resolve_role("ghost_role")
        assert result == "default-profile"
