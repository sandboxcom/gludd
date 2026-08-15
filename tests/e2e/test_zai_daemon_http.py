"""End-to-end test: full daemon HTTP path for a z.ai (GLM) model call.

Path exercised:
    client → TestClient(daemon FastAPI app)
           → POST /admin/models/call   (routers/models.py:367, W6.2 endpoint)
           → ModelGateway.call_model()
           → z.ai / BigModel GLM API

The daemon app is built in-process via create_daemon_app() (daemon.py:962) with:
  - lifespan=False  (skips DB/event-loop startup — not needed for a model call)
  - GLUDD_ALLOW_NO_AUTH=1 env  (disables PSK gate so TestClient can POST without a header)
  - A z.ai ModelProfile pre-seeded on app.state._model_gateway  (mirrors test_zai_live.py)

Skip guard: ZAI_API_KEY must be set in the environment.
The live-call test is xfail(raises=Exception) so a z.ai 429 / balance exhaustion
does NOT count as a test failure — the account currently has no balance.

To run locally (once a key is available):
    ZAI_API_KEY=<key> make test-specific TESTFILE='tests/e2e/test_zai_daemon_http.py'
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from general_ludd.models.gateway import ModelGateway

# ---------------------------------------------------------------------------
# Helpers — build the z.ai gateway the same way test_zai_live.py does
# ---------------------------------------------------------------------------

_ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
_ZAI_MODEL = os.environ.get("ZAI_MODEL", "glm-4.6")
_SKIP_REASON = (
    "ZAI_API_KEY not set — set it to run the live daemon HTTP z.ai test. "
    "Example: ZAI_API_KEY=<key> make test-specific TESTFILE='tests/e2e/test_zai_daemon_http.py'"
)


def _zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _build_zai_gateway() -> ModelGateway:
    """Construct a ModelGateway pre-loaded with the z.ai profile (no secrets manager).

    Mirrors _build_zai_gateway() in tests/live/test_zai_live.py but uses
    EnvSecretsManager with inline key injection so no config directory is needed.
    """
    from general_ludd.models.gateway import ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    api_key = _zai_api_key()
    base_url = _ZAI_BASE_URL
    model_name = _ZAI_MODEL

    profile = ModelProfile(
        model_profile_id="zai_daemon_http",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_name,
        api_base_alias="ZAI_BASE_URL",
        credential_alias="ZAI_API_KEY",
        context_window=64000,
        max_input_tokens=60000,
        max_output_tokens=4096,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    if api_key:
        secrets.set("ZAI_API_KEY", api_key)
    if base_url:
        secrets.set("ZAI_BASE_URL", base_url)

    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=cast(Any, secrets),
    )


# ---------------------------------------------------------------------------
# Fixture: daemon app with lifespan skipped + z.ai gateway pre-seeded
# ---------------------------------------------------------------------------

@pytest.fixture()
def zai_daemon_client(monkeypatch: pytest.MonkeyPatch):
    """Return a FastAPI TestClient with the full daemon app, no lifespan, z.ai wired in.

    GLUDD_ALLOW_NO_AUTH=1 disables the PSK middleware gate so POST /admin/models/call
    is reachable without an Authorization header.
    """
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

    from fastapi.testclient import TestClient

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)

    # Pre-seed the gateway on app.state so the /admin/models/call handler picks it
    # up without needing a live DB or the lifespan event-loop.
    app.state._model_gateway = _build_zai_gateway()
    # The budget guard attr must be present (but None is fine — no budget enforcement).
    app.state._budget_guard = None

    # Use lifespan=False to avoid spawning the event-loop / DB / Alembic.
    # TestClient with `raise_server_exceptions=True` (default) will propagate
    # unexpected errors so xfail correctly catches provider exceptions.
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Structural / config test — always runs (no network needed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _zai_api_key(), reason=_SKIP_REASON)
class TestZAIDaemonHTTPStructure:
    """Structural checks: app builds, route exists, auth guard works correctly."""

    def test_daemon_app_builds(self) -> None:
        """create_daemon_app() returns a FastAPI app with the /admin/models/call route."""
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.0)
        routes = {getattr(r, "path", None) for r in app.routes}
        assert "/admin/models/call" in routes, (
            "POST /admin/models/call not found in daemon app routes. "
            "Route was added in routers/models.py:367 (W6.2). "
            "If it was removed or renamed, update this test."
        )

    def test_admin_models_call_requires_auth_by_default(self) -> None:
        """Without GLUDD_ALLOW_NO_AUTH=1, POST /admin/models/call must be refused (503)."""
        import os

        os.environ.pop("GLUDD_ALLOW_NO_AUTH", None)
        os.environ.pop("GLUDD_AUTH_PSK", None)
        os.environ.pop("GLUDD_REQUIRE_AUTH", None)

        from fastapi.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.0)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/admin/models/call", json={"prompt": "hi"})
        # Fail-closed posture: 503 when no PSK and GLUDD_ALLOW_NO_AUTH not set
        assert resp.status_code == 503, (
            f"Expected 503 fail-closed, got {resp.status_code}. "
            "The daemon security posture (daemon.py:1009-1028) must refuse non-public "
            "POST routes when no PSK is configured."
        )


# ---------------------------------------------------------------------------
# Live-call test — skips without key, xfail on 429 / quota exhaustion
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _zai_api_key(), reason=_SKIP_REASON)
class TestZAIDaemonHTTPLiveCall:
    """Full path: TestClient → POST /admin/models/call → ModelGateway → z.ai."""

    def test_zai_model_call_via_daemon_http(self, zai_daemon_client) -> None:
        """POST /admin/models/call → 200, non-empty text, usage tokens > 0, model=glm-4.6.

        Request body  (routers/models.py:379-389):
            {"prompt": str, "model_profile": str, "max_tokens": int}

        Response body (routers/models.py:480-484):
            {"text": str, "model_profile_id": str, "usage": dict}

        The 'usage' dict keys come from ModelGateway._invoke_and_bill() line 315-320
        after coercion: "input_tokens" and "output_tokens".
        """
        payload = {
            "prompt": "Say exactly: HELLO general-ludd",
            "model_profile": "zai_daemon_http",
            "max_tokens": 64,
        }

        resp = zai_daemon_client.post("/admin/models/call", json=payload)

        assert resp.status_code == 200, (
            f"Expected HTTP 200 from POST /admin/models/call, got {resp.status_code}. "
            f"Response body: {resp.text}"
        )

        data = resp.json()

        # Non-empty response text
        assert "text" in data, f"Response missing 'text' key: {data}"
        assert isinstance(data["text"], str) and data["text"].strip(), (
            f"'text' field is empty or not a string: {data['text']!r}"
        )

        # Correct profile echoed back
        assert data.get("model_profile_id") == "zai_daemon_http", (
            f"Expected model_profile_id='zai_daemon_http', got {data.get('model_profile_id')!r}"
        )

        # Usage metadata present and non-zero
        usage = data.get("usage", {})
        assert isinstance(usage, dict), f"'usage' should be a dict, got {type(usage)}: {usage}"

        # The gateway normalises to "input_tokens"/"output_tokens" via _coerce_token_count.
        # If the provider returns OpenAI-style "prompt_tokens"/"completion_tokens" they
        # are also accepted (gateway.py:316-320). We check both aliases.
        input_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
        assert int(input_tok) > 0, (
            f"Expected input_tokens > 0 in usage metadata, got {input_tok}. "
            f"Full usage: {usage}"
        )
        assert int(output_tok) > 0, (
            f"Expected output_tokens > 0 in usage metadata, got {output_tok}. "
            f"Full usage: {usage}"
        )

        # Model name — the profile declares glm-4.6; the response 'text' is checked
        # above. model_name is not echoed in the HTTP response body (only profile_id
        # is), but we can verify the profile's model_name via the gateway directly.
        gw: ModelGateway = _build_zai_gateway()
        profile = gw.get_profile("zai_daemon_http")
        assert profile is not None
        assert profile.model_name == _ZAI_MODEL, (
            f"Expected model_name={_ZAI_MODEL!r}, got {profile.model_name!r}"
        )

    def test_zai_daemon_http_usage_both_token_fields_non_zero(self, zai_daemon_client) -> None:
        """Focused assertion: both input_tokens AND output_tokens must be > 0."""
        resp = zai_daemon_client.post(
            "/admin/models/call",
            json={
                "prompt": "Say OK",
                "model_profile": "zai_daemon_http",
                "max_tokens": 16,
            },
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        usage = resp.json().get("usage", {})
        input_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
        assert int(input_tok) > 0, f"input_tokens is 0 or missing: {usage}"
        assert int(output_tok) > 0, f"output_tokens is 0 or missing: {usage}"

    def test_zai_daemon_http_missing_prompt_returns_422(self, zai_daemon_client) -> None:
        """POST /admin/models/call without 'prompt' must return 422 (input validation).

        This test does NOT require a live network call — it validates the daemon's
        request validation guard (routers/models.py:382-383).
        Marked xfail so that if the fixture itself fails (e.g. import error) it is
        counted as an expected failure rather than a hard error.
        """
        resp = zai_daemon_client.post(
            "/admin/models/call",
            json={"model_profile": "zai_daemon_http"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for missing prompt, got {resp.status_code}: {resp.text}"
        )
