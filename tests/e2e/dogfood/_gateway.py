"""Gateway factory for the dogfood E2E harness.

Builds either a live ModelGateway wired to z.ai/GLM, or a MagicMock gateway
that returns a deterministic scaffold so CI passes without a real key.

See docs/e2e_harness/DESIGN_native_dogfood_harness.md §6 for the design rationale
and the ZAI_API_KEY uppercase-vs-lowercase-alias trap (§2.2).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def build_gateway(
    zai_creds: dict[str, str] | None,
    *,
    mock_response: str = "",
) -> tuple[Any, str]:
    """Return (gateway, mode) where mode is "live" or "mock".

    live:
        Real ModelGateway with the zai profile enabled.  The key is registered
        via explicit secrets.set() calls to bridge the uppercase-env trap
        (EnvSecretsManager.resolve("zai_api_key") would miss ZAI_API_KEY because
        os.environ.get is case-sensitive).

    mock:
        MagicMock whose .call_model() returns a MagicMock(content=mock_response).
        Used in CI / offline mode so the entire harness can run without tokens.
    """
    if zai_creds is None:
        gw = MagicMock()
        gw.call_model = MagicMock(return_value=MagicMock(content=mock_response))
        return gw, "mock"

    # Heavy imports only when we actually have a key (keeps collection import-safe)
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.secrets.env import EnvSecretsManager

    secrets = EnvSecretsManager()
    # Bridge the uppercase-env trap: gludd resolves "zai_api_key" (lowercase)
    # but the env var is ZAI_API_KEY (uppercase).  set() goes into _overrides
    # which are always honored regardless of case.
    secrets.set("zai_api_key", zai_creds["zai_api_key"])
    secrets.set("zai_api_base", zai_creds["zai_api_base"])

    profile = ModelProfile(
        model_profile_id="zai-glm-dogfood",
        provider="zai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=zai_creds["model"],
        credential_alias="zai_api_key",
        api_base_alias="zai_api_base",
        enabled=True,
    )
    gw = ModelGateway(profiles=[profile], secrets_manager=secrets)
    return gw, "live"
