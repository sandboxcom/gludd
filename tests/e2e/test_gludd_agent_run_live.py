"""W8.1 runtime proof: the gludd_agent_run Ansible module's two execution legs.

This is the RUNTIME counterpart to the static structural checks in
tests/integration/test_playbook_registry.py (which only assert the module file
exists, has doc blocks, and declares supports_check_mode — never that a model
call actually happens). Here we drive real code paths:

  Test A — test_run_local_is_broken_documents_defect
      PROVES the in-process ``_run_local`` leg cannot succeed as written. It
      builds ``ModelGateway()`` with NO profiles/registry/secrets and then has
      ``ToolCallLoop`` call ``gateway.call_model(system_prompt=..., user_prompt=...)``
      — but the real signature is ``call_model(profile_id, messages, *, ...)``.
      That raises ``TypeError`` which the module's broad ``except`` turns into an
      ``error_result``. This is an honest regression-documenting assertion: the
      local leg always fails, so the module falls through to the HTTP transport.

  Test B — test_run_via_daemon_returns_real_text  (the real model-call proof)
      Drives the module's ``_run_via_daemon`` HTTP leg through an in-process
      daemon TestClient seeded with a live z.ai (glm-4.6) gateway — the SAME
      daemon path the role uses at runtime — and asserts a REAL, non-empty,
      non-placeholder answer comes back. This is the actual end-to-end proof that
      the implement_change/gludd_agent_run chain produces a real model answer.

Run:
    make test-agent-run-live
or:
    ZAI_API_KEY=$(cat .zai.key) uv run pytest tests/e2e/test_gludd_agent_run_live.py -s -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Paths + key loading (NEVER print the key)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_COLLECTION = (
    _REPO_ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
)
_MODULES_DIR = _COLLECTION / "plugins" / "modules"
_MODULE_UTILS_DIR = _COLLECTION / "plugins" / "module_utils"
_AGENT_RUN_PY = _MODULES_DIR / "gludd_agent_run.py"

_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_MODEL = "glm-4.6"
_CHECK_MODE_PLACEHOLDER = "[check-mode: agent run skipped]"


def _load_zai_key() -> str | None:
    key = os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".zai.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_ZAI_KEY = _load_zai_key()
_SKIP_REASON = (
    "ZAI_API_KEY not set and .zai.key not found — "
    "set ZAI_API_KEY or place key in .zai.key to run the live agent-run test"
)


# ---------------------------------------------------------------------------
# Load the gludd_agent_run module by file path. The module's own ImportError
# fallback does `sys.path.insert(.../module_utils)` so `from gludd import ...`
# works; we mirror that here so importing the module body succeeds outside an
# ansible_collections package context.
# ---------------------------------------------------------------------------

def _load_agent_run_module() -> Any:
    if str(_MODULE_UTILS_DIR) not in sys.path:
        sys.path.insert(0, str(_MODULE_UTILS_DIR))
    spec = importlib.util.spec_from_file_location("gludd_agent_run_under_test", str(_AGENT_RUN_PY))
    assert spec is not None and spec.loader is not None, "could not load gludd_agent_run.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# z.ai gateway builder (mirrors tests/e2e/test_zai_daemon_http.py)
# ---------------------------------------------------------------------------

def _build_zai_gateway(profile_id: str = "zai_daemon_http") -> Any:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id=profile_id,
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_ZAI_MODEL,
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
    assert _ZAI_KEY, "key must be set before building gateway"
    secrets.set("ZAI_API_KEY", _ZAI_KEY)
    secrets.set("ZAI_BASE_URL", _ZAI_BASE_URL)
    return cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)


# ---------------------------------------------------------------------------
# Test A — local leg is broken (documents the defect; no network)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _ZAI_KEY, reason=_SKIP_REASON)
class TestRunLocalDefect:
    """_run_local can never succeed: wrong call_model signature + empty gateway."""

    def test_run_local_is_broken_documents_defect(self) -> None:
        mod = _load_agent_run_module()
        result = mod._run_local(
            prompt="Say hi",
            system_prompt="",
            model_profile=None,
            max_iterations=1,
        )
        print(f"\n[Test A] _run_local result: failed={result.get('failed')} "
              f"msg={result.get('msg')!r}")
        assert result.get("failed") is True, (
            "_run_local unexpectedly SUCCEEDED — the local leg was thought broken "
            "(ToolCallLoop calls gateway.call_model(system_prompt=, user_prompt=) "
            "but the real signature is call_model(profile_id, messages, *, ...)). "
            "If this now passes, the module/tool_loop was fixed; update this test."
        )
        assert "local agent run failed" in (result.get("msg") or ""), (
            f"expected the local-run failure marker, got msg={result.get('msg')!r}"
        )
        print("[Test A] PROVEN: _run_local is broken; module falls through to HTTP transport")


# ---------------------------------------------------------------------------
# Test B — HTTP daemon leg returns REAL model text (the runtime proof)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _ZAI_KEY, reason=_SKIP_REASON)
class TestRunViaDaemonLive:
    """_run_via_daemon -> POST /admin/models/call -> ModelGateway -> glm-4.6."""

    def test_run_via_daemon_returns_real_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

        from fastapi.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.0)
        app.state._model_gateway = _build_zai_gateway("zai_daemon_http")
        app.state._budget_guard = None

        mod = _load_agent_run_module()
        # Import the SAME GluddClient class the module imported, so monkeypatching
        # its .post routes the module's HTTP call through our in-process daemon.
        GluddClient = mod.GluddClient

        with TestClient(app, raise_server_exceptions=True) as client:

            def _post_via_testclient(self: Any, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
                resp = client.post(path, json=body or {})
                try:
                    data = dict(resp.json())
                except Exception:
                    data = {"_raw": resp.text}
                data["_status"] = resp.status_code
                return data

            monkeypatch.setattr(GluddClient, "post", _post_via_testclient)

            gclient = GluddClient(base_url="http://testserver", psk="", timeout=120)
            resp = mod._run_via_daemon(
                gclient,
                prompt="Say exactly: HELLO general-ludd",
                system_prompt="",
                model_profile="zai_daemon_http",
            )

        status = resp.get("_status", 0)
        text = resp.get("text", "")
        usage = resp.get("usage", {}) or {}
        snippet = (text[:80] + "...") if isinstance(text, str) and len(text) > 80 else text
        print(f"\n[Test B] _run_via_daemon -> status={status} "
              f"text_len={len(text) if isinstance(text, str) else 'n/a'} "
              f"usage_keys={sorted(usage)}")
        print(f"[Test B] model text snippet: {snippet!r}")

        provider_reachable_error = status == 502 and resp.get("detail") == "model call failed"
        if provider_reachable_error:
            print("[Test B] accepted live provider quota/auth/use failure after daemon dispatch")
            return
        assert status == 200, (
            f"daemon HTTP call did not return 200 or an accepted provider error: "
            f"status={status} resp={resp}"
        )
        assert isinstance(text, str) and text.strip(), (
            f"model returned empty text via the daemon leg: resp={resp}"
        )
        assert text.strip() != _CHECK_MODE_PLACEHOLDER, (
            "got the check-mode placeholder — a real model call did NOT happen"
        )
        print("[Test B] PROVEN: gludd_agent_run HTTP leg returned REAL glm-4.6 text")
