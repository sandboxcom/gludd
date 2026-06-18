"""E2E scaffold: llama.cpp provider via OpenAI-compatible /v1 endpoint.

Skips unconditionally when LLAMACPP_BASE_URL is not set or the server is not
reachable — CI stays green with no llama.cpp server running.

Backend requirements:
  LLAMACPP_BASE_URL   e.g. http://localhost:8080/v1  (llama-server or
                           python -m llama_cpp.server)
  LLAMACPP_MODEL      model name as the server reports it in /v1/models
  LLAMACPP_MODEL_PATH gguf model path (for spawn variant)

Optional spawn variant:
  LLAMACPP_E2E_SPAWN=1 — requires llama_cpp.server importable + MODEL_PATH set

SSRF note: gateway-path tests are gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1
(DESIGN §2.1(A)), being added by another builder.

Wave-B TODO: replace §2.1(B) bypass with real gateway call once
GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS is wired.
Wave-B TODO: add cost/weights persistence assertions once P0b ships.
Wave-B TODO: implement real-process spawn variant.
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

_LLAMACPP_MODEL_DEFAULT = "local-model"


def _llamacpp_base_url() -> str:
    return require_backend("LLAMACPP_BASE_URL")


def _llamacpp_model() -> str:
    return os.environ.get("LLAMACPP_MODEL", _LLAMACPP_MODEL_DEFAULT)


def _llamacpp_model_path() -> str | None:
    return os.environ.get("LLAMACPP_MODEL_PATH")


# ---------------------------------------------------------------------------
# Test: UtilizationTracker register + route
# ---------------------------------------------------------------------------

class TestLlamaCppUtilizationTracker:
    """Register a llama.cpp endpoint and assert routing works (no SSRF dep)."""

    def test_register_endpoint(self) -> None:
        base_url = _llamacpp_base_url()
        model = _llamacpp_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("llamacpp-e2e", base_url, model=model)

        ids = [ep.endpoint_id for ep in tracker.list_endpoints()]
        assert "llamacpp-e2e" in ids

    def test_route_task(self) -> None:
        base_url = _llamacpp_base_url()
        model = _llamacpp_model()

        tracker = UtilizationTracker()
        tracker.register_endpoint("llamacpp-e2e", base_url, model=model)
        routing = tracker.route_task("task-llamacpp-001", model=model)

        assert routing is not None
        assert routing.endpoint_id == "llamacpp-e2e"


# ---------------------------------------------------------------------------
# Test: LocalInferenceManager argv construction
# ---------------------------------------------------------------------------

class TestLlamaCppLocalInferenceConfig:
    """Assert LocalInferenceManager builds the correct llama_cpp.server argv."""

    def test_create_server_endpoint_url(self) -> None:
        """create_server sets endpoint_url to http://{host}:{port}/v1."""
        _llamacpp_base_url()
        model = _llamacpp_model()
        model_path = _llamacpp_model_path() or "/tmp/model.gguf"

        try:
            from general_ludd.infra.local_inference import (
                LocalInferenceManager,
                LocalServerConfig,
            )
        except ImportError:
            pytest.skip("LocalInferenceManager not importable")

        host, port = "127.0.0.1", 8080
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_name=model,
            model_path=model_path,
            host=host,
            port=port,
        )
        mgr = LocalInferenceManager()
        server = mgr.create_server(cfg)
        assert server.endpoint_url == f"http://{host}:{port}/v1"

    def test_build_command_argv(self) -> None:
        """_build_command produces python3 -m llama_cpp.server with correct flags."""
        _llamacpp_base_url()
        model = _llamacpp_model()
        model_path = _llamacpp_model_path() or "/tmp/model.gguf"

        try:
            from general_ludd.infra.local_inference import (
                LocalInferenceManager,
                LocalServerConfig,
            )
        except ImportError:
            pytest.skip("LocalInferenceManager not importable")

        host, port = "127.0.0.1", 8080
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_name=model,
            model_path=model_path,
            host=host,
            port=port,
        )
        mgr = LocalInferenceManager()
        cmd = mgr._build_command(cfg)

        # Expected: ["python3", "-m", "llama_cpp.server", "--model", PATH,
        #            "--host", host, "--port", str(port), ...]
        assert "python3" in cmd or "python" in cmd, f"python not in argv: {cmd}"
        assert "-m" in cmd, f"'-m' not in argv: {cmd}"
        assert "llama_cpp.server" in cmd, f"'llama_cpp.server' not in argv: {cmd}"
        assert "--host" in cmd
        assert host in cmd
        assert "--port" in cmd
        assert str(port) in cmd

    def test_llamacpp_completion_only_not_supported_note(self) -> None:
        """Document that gludd cannot drive raw /completion-only servers.

        llama.cpp's native /completion endpoint (no /v1) is not supported by
        gludd — there is no completion adapter. This test always passes and
        serves as a living note for anyone who hits this limitation.

        TODO(Wave-B): if a /completion adapter is added, assert it works here.
        """
        # This is a documentation test — always passes.
        pass


# ---------------------------------------------------------------------------
# Test: gateway model call (gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS)
# ---------------------------------------------------------------------------

class TestLlamaCppGatewayCall:
    """Real gateway call to llama.cpp — gated on GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS."""

    def test_gateway_call_pong(self) -> None:
        if not ALLOW_LOCAL_MODEL_BASE_URLS:
            pytest.skip(
                "GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 not set — set it to "
                "exercise the full gateway base-URL resolution path for local "
                "backends (see DESIGN §2.1(A))"
            )
        base_url = _llamacpp_base_url()
        model = _llamacpp_model()

        # TODO(Wave-B §2.1(A)): replace §2.1(B) bypass with build_local_gateway()
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
# Test: real-process spawn (opt-in LLAMACPP_E2E_SPAWN=1)
# ---------------------------------------------------------------------------

class TestLlamaCppSpawn:
    """Real-process llama_cpp.server spawn. Opt-in via LLAMACPP_E2E_SPAWN=1.

    TODO(Wave-B): implement spawn using LocalInferenceManager.start_server,
    poll /v1/models until ready (~120s bounded), run small task, stop.
    """

    def test_spawn_not_implemented_yet(self) -> None:
        """Placeholder — skip unless LLAMACPP_E2E_SPAWN=1."""
        if os.environ.get("LLAMACPP_E2E_SPAWN") != "1":
            pytest.skip(
                "LLAMACPP_E2E_SPAWN=1 not set — real-process spawn test is "
                "opt-in (heavy, requires llama_cpp installed + a gguf model)"
            )
        _llamacpp_base_url()
        if not _llamacpp_model_path():
            pytest.skip("LLAMACPP_MODEL_PATH not set — required for spawn variant")
        # TODO(Wave-B): implement spawn lifecycle
        pytest.skip("LLAMACPP_E2E_SPAWN test not yet implemented (Wave-B)")
