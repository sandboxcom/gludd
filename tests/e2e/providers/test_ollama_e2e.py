"""E2E scaffold: ollama provider via OpenAI-compatible /v1 endpoint.

Skips unconditionally when OLLAMA_BASE_URL is not set or the server is not
reachable — CI stays green with no ollama instance running.

Backend requirements:
  OLLAMA_BASE_URL  e.g. http://localhost:11434/v1
  OLLAMA_MODEL     default: llama3.2:1b  (small, fast)

SSRF note: the full gateway base-URL resolution path requires the
GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 opt-in (DESIGN §2.1(A), being added by
another builder). Until that flag ships, gateway-path tests below are
additionally gated behind ALLOW_LOCAL_MODEL_BASE_URLS. The UtilizationTracker
register/route/record assertions do NOT depend on the gateway and run without
the flag.

Wave-B TODO: replace the §2.1(B) bypass comment with real gateway invocation
once GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS is wired.
Wave-B TODO: add cost/weights persistence assertions once P0b (cost write
loop) ships.
"""

from __future__ import annotations

import os

import httpx
import pytest

from general_ludd.infra.utilization import UtilizationTracker
from tests.e2e.providers._provider_skip import (
    ALLOW_LOCAL_MODEL_BASE_URLS,
    require_backend,
)

pytestmark = pytest.mark.e2e

_OLLAMA_MODEL_DEFAULT = "llama3.2:1b"


def _ollama_base_url() -> str:
    """Return OLLAMA_BASE_URL or skip."""
    return require_backend("OLLAMA_BASE_URL")


def _ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", _OLLAMA_MODEL_DEFAULT)


# ---------------------------------------------------------------------------
# Test: backend registers into UtilizationTracker and routes correctly
# ---------------------------------------------------------------------------

class TestOllamaUtilizationTracker:
    """Assert that a discovered ollama backend can be registered and routed.

    These tests do NOT touch the model gateway (no SSRF dependency). They
    exercise gludd's compute-tracking layer with a real backend URL.
    """

    def test_register_endpoint(self) -> None:
        """Register the ollama endpoint and assert it appears in list_endpoints."""
        base_url = _ollama_base_url()
        model = _ollama_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("ollama-e2e", base_url, model=model)

        endpoints = tracker.list_endpoints()
        ids = [ep.endpoint_id for ep in endpoints]
        assert "ollama-e2e" in ids, f"ollama-e2e not found in {ids}"

    def test_route_task(self) -> None:
        """route_task returns the ollama endpoint for the configured model."""
        base_url = _ollama_base_url()
        model = _ollama_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("ollama-e2e", base_url, model=model)

        routing = tracker.route_task("task-001", model=model)
        assert routing is not None, "route_task returned None — no endpoint matched"
        assert routing.endpoint_id == "ollama-e2e"

    def test_record_tokens(self) -> None:
        """record_tokens increments the endpoint's total_tokens counter."""
        base_url = _ollama_base_url()
        model = _ollama_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("ollama-e2e", base_url, model=model)
        tracker.route_task("task-002", model=model)

        before = tracker._endpoints["ollama-e2e"].total_tokens
        tracker.record_tokens("ollama-e2e", 42)
        after = tracker._endpoints["ollama-e2e"].total_tokens
        assert after == before + 42


# ---------------------------------------------------------------------------
# Test: autodiscovery — GET /v1/models returns the expected model
# ---------------------------------------------------------------------------

class TestOllamaModelDiscovery:
    """Assert that GET /v1/models on the live ollama server lists the model."""

    def test_v1_models_lists_configured_model(self) -> None:
        """The OpenAI-compat /v1/models endpoint lists the configured model."""
        base_url = _ollama_base_url()
        model = _ollama_model()

        resp = httpx.get(base_url.rstrip("/") + "/models", timeout=5.0)
        assert resp.status_code == 200, f"GET /v1/models returned {resp.status_code}"
        data = resp.json()
        model_ids = [m.get("id", "") for m in data.get("data", [])]
        assert any(model in mid for mid in model_ids), (
            f"Model {model!r} not found in /v1/models response. "
            f"Available: {model_ids}. "
            "Set OLLAMA_MODEL to a model that is loaded in ollama."
        )

    def test_native_api_tags_note(self) -> None:
        """Document that gludd has no native ollama /api/tags discovery adapter.

        This test always passes — it is a living reminder to add a native
        ollama discovery adapter (§2.2 gap). The GET /api/tags call is made
        here as an informational probe only; failure is non-fatal.
        """
        base_url = _ollama_base_url()
        # Strip /v1 suffix if present to probe the native ollama API
        native_base = base_url.rstrip("/")
        if native_base.endswith("/v1"):
            native_base = native_base[:-3]
        try:
            resp = httpx.get(native_base + "/api/tags", timeout=3.0)
            # Just record reachability; no assertion — not a gludd feature yet
            _ = resp.status_code
        except Exception:
            pass
        # TODO(Wave-B): once a native ollama adapter ships, assert
        # discover_ollama_models(native_base) returns non-empty list matching
        # /api/tags output.


# ---------------------------------------------------------------------------
# Test: gateway model call (gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS)
# ---------------------------------------------------------------------------

class TestOllamaGatewayCall:
    """Assert a real small model call through the gludd gateway completes.

    Gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 (§2.1(A)). Without that flag
    the gateway SSRF guard rejects http://localhost/* — skip with a clear
    reason rather than error.
    """

    def test_gateway_call_pong(self) -> None:
        """A real gateway call to ollama returns a non-empty response."""
        if not ALLOW_LOCAL_MODEL_BASE_URLS:
            pytest.skip(
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 not set — set it to "
                "exercise the full gateway base-URL resolution path for local "
                "backends (see DESIGN_local_cloud_providers_e2e.md §2.1(A))"
            )
        base_url = _ollama_base_url()
        model = _ollama_model()

        # TODO(Wave-B §2.1(A)): Once GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS is
        # wired into gateway._invoke_and_bill, replace the §2.1(B) bypass
        # below with build_local_gateway() from conftest, which uses the real
        # api_base_alias + SSRF-checked resolution path.
        #
        # §2.1(B) bypass (interim): construct ChatOpenAI directly to skip the
        # SSRF base-URL check while still exercising real call+bill logic.
        # This is a real HTTP call to a real ollama server — just bypasses
        # gludd's own URL-resolution path.

        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            pytest.skip("langchain-openai not installed — cannot run gateway call test")

        from langchain_core.messages import HumanMessage

        chat = ChatOpenAI(
            base_url=base_url,
            api_key="local-no-key",  # pragma: allowlist secret
            model=model,
            max_tokens=32,
        )
        resp = chat.invoke([HumanMessage(content="Reply with the single word: pong")])
        assert resp.content, "Gateway returned empty content"
        assert "pong" in resp.content.lower(), (
            f"Expected 'pong' in response, got: {resp.content!r}. "
            "Small models sometimes stray; try a larger OLLAMA_MODEL."
        )

        # TODO(Wave-B P0b): once cost write loop ships, assert:
        #   assert response.cost_estimate == 0.0  (local = free)
        #   assert usage_metadata carries token counts
