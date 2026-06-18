"""E2E scaffold: vllm provider via OpenAI-compatible /v1 endpoint.

Skips unconditionally when VLLM_BASE_URL is not set or the server is not
reachable — CI stays green with no vllm instance running.

Backend requirements:
  VLLM_BASE_URL    e.g. http://localhost:8000/v1
  VLLM_MODEL       e.g. Qwen/Qwen2.5-0.5B-Instruct

Optional spawn variant (starts a real vllm process):
  VLLM_E2E_SPAWN=1  — also requires vllm on PATH
  VLLM_MODEL must be a model available locally to vllm

SSRF note: gateway-path tests are gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1
(DESIGN §2.1(A)), being added by another builder.

Wave-B TODO: replace §2.1(B) bypass with real gateway call once
GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS is wired.
Wave-B TODO: add cost/weights persistence assertions once P0b ships.
Wave-B TODO: implement real-process spawn variant (VLLM_E2E_SPAWN=1).
"""

from __future__ import annotations

import os

import pytest

from general_ludd.infra.utilization import UtilizationTracker
from tests.e2e.providers._provider_skip import (
    ALLOW_LOCAL_MODEL_BASE_URLS,
    require_backend,
)

pytestmark = pytest.mark.e2e

_VLLM_MODEL_DEFAULT = "Qwen/Qwen2.5-0.5B-Instruct"


def _vllm_base_url() -> str:
    return require_backend("VLLM_BASE_URL")


def _vllm_model() -> str:
    return os.environ.get("VLLM_MODEL", _VLLM_MODEL_DEFAULT)


# ---------------------------------------------------------------------------
# Test: UtilizationTracker register + route
# ---------------------------------------------------------------------------

class TestVllmUtilizationTracker:
    """Register a vllm endpoint and assert routing works (no SSRF dep)."""

    def test_register_endpoint(self) -> None:
        base_url = _vllm_base_url()
        model = _vllm_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("vllm-e2e", base_url, model=model)

        ids = [ep.endpoint_id for ep in tracker.list_endpoints()]
        assert "vllm-e2e" in ids

    def test_route_task(self) -> None:
        base_url = _vllm_base_url()
        model = _vllm_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("vllm-e2e", base_url, model=model)
        routing = tracker.route_task("task-vllm-001", model=model)

        assert routing is not None
        assert routing.endpoint_id == "vllm-e2e"


# ---------------------------------------------------------------------------
# Test: LocalInferenceManager config + argv construction
# ---------------------------------------------------------------------------

class TestVllmLocalInferenceConfig:
    """Assert LocalInferenceManager builds the correct vllm argv.

    This test does NOT start a real process. It validates gludd's own
    spawn-argv construction logic (the thing the existing unit tests mock).
    """

    def test_create_server_endpoint_url(self) -> None:
        """create_server sets endpoint_url to http://{host}:{port}/v1."""
        _vllm_base_url()  # skip if not configured
        model = _vllm_model()

        try:
            from general_ludd.infra.local_inference import (
                LocalInferenceManager,
                LocalServerConfig,
            )
        except ImportError:
            pytest.skip("LocalInferenceManager not importable")

        host, port = "127.0.0.1", 8000
        cfg = LocalServerConfig(
            engine="vllm",
            model_name=model,
            host=host,
            port=port,
        )
        mgr = LocalInferenceManager()
        server = mgr.create_server(cfg)
        assert server.endpoint_url == f"http://{host}:{port}/v1", (
            f"Unexpected endpoint_url: {server.endpoint_url!r}"
        )

    def test_build_command_argv(self) -> None:
        """_build_command produces the expected vllm serve argv."""
        _vllm_base_url()  # skip if not configured
        model = _vllm_model()

        try:
            from general_ludd.infra.local_inference import (
                LocalInferenceManager,
                LocalServerConfig,
            )
        except ImportError:
            pytest.skip("LocalInferenceManager not importable")

        host, port = "127.0.0.1", 8000
        cfg = LocalServerConfig(
            engine="vllm",
            model_name=model,
            host=host,
            port=port,
        )
        mgr = LocalInferenceManager()
        cmd = mgr._build_command(cfg)
        assert cmd[0] == "vllm", f"Expected argv[0]='vllm', got {cmd[0]!r}"
        assert "serve" in cmd, f"'serve' not in argv: {cmd}"
        assert model in cmd, f"model {model!r} not in argv: {cmd}"
        assert "--host" in cmd
        assert host in cmd
        assert "--port" in cmd
        assert str(port) in cmd


# ---------------------------------------------------------------------------
# Test: gateway model call (gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS)
# ---------------------------------------------------------------------------

class TestVllmGatewayCall:
    """Real gateway call to vllm — gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS."""

    def test_gateway_call_pong(self) -> None:
        if not ALLOW_LOCAL_MODEL_BASE_URLS:
            pytest.skip(
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 not set — set it to "
                "exercise the full gateway base-URL resolution path for local "
                "backends (see DESIGN §2.1(A))"
            )
        base_url = _vllm_base_url()
        model = _vllm_model()

        # TODO(Wave-B §2.1(A)): replace §2.1(B) bypass with build_local_gateway()
        # once GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS is wired into gateway.
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            pytest.skip("langchain-openai not installed")

        from langchain_core.messages import HumanMessage

        chat = ChatOpenAI(
            base_url=base_url,
            api_key="local-no-key",  # pragma: allowlist secret
            model=model,
            max_tokens=32,
        )
        resp = chat.invoke([HumanMessage(content="Reply with the single word: pong")])
        assert resp.content, "Gateway returned empty content"
        assert "pong" in resp.content.lower()

        # TODO(Wave-B P0b): assert cost_estimate == 0.0 and token counts present


# ---------------------------------------------------------------------------
# Test: real-process spawn (opt-in VLLM_E2E_SPAWN=1)
# ---------------------------------------------------------------------------

class TestVllmSpawn:
    """Real-process vllm spawn test. Opt-in via VLLM_E2E_SPAWN=1.

    TODO(Wave-B): implement spawn, poll /v1/models until ready (~120s
    bounded), run small task, stop. Add shutil.which("vllm") skip gate.
    """

    def test_spawn_not_implemented_yet(self) -> None:
        """Placeholder — skip unless VLLM_E2E_SPAWN=1."""
        if os.environ.get("VLLM_E2E_SPAWN") != "1":
            pytest.skip(
                "VLLM_E2E_SPAWN=1 not set — real-process spawn test is opt-in "
                "(heavy, requires vllm on PATH and a downloadable model)"
            )
        _vllm_base_url()  # also skip if no base URL configured
        # TODO(Wave-B): implement:
        #   1. shutil.which("vllm") or skip
        #   2. LocalInferenceManager.start_server(server_id)
        #   3. poll /v1/models until ready (bounded ~120s, skip on timeout)
        #   4. run gateway call test
        #   5. LocalInferenceManager.stop_server(server_id)
        #   6. assert process reaped
        pytest.skip("VLLM_E2E_SPAWN test not yet implemented (Wave-B)")
