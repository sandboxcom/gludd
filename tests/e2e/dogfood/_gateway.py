"""Gateway factory for e2e harness -- live zai or offline mock."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse


def _make_mock_gateway(mock_response: str = "# mock response\n") -> Any:
    """Return a MagicMock gateway that returns a fixed response."""
    gw = MagicMock(spec=ModelGateway)
    resp = ModelResponse(content=mock_response, model_name="mock")
    gw.call_model.return_value = resp
    gw.call_model_with_retry.return_value = resp
    gw.list_profiles.return_value = []
    return gw


def build_gateway(
    zai_creds: dict[str, str] | None,
    *,
    mock_response: str = "# mock response\n",
) -> tuple[Any, str]:
    """Build a gateway for the e2e harness.

    Returns (gateway, mode) where mode is "live" or "mock".
    """
    if zai_creds is None:
        return _make_mock_gateway(mock_response), "mock"

    from general_ludd.secrets import EnvSecretsManager

    secrets = EnvSecretsManager()
    secrets.set("zai_api_key", zai_creds["zai_api_key"])
    secrets.set("zai_api_base", zai_creds["zai_api_base"])

    profile = ModelProfile(
        model_profile_id="zai-glm-e2e",
        provider="zai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=zai_creds["model"],
        credential_alias="zai_api_key",
        api_base_alias="zai_api_base",
        enabled=True,
        run_budget_usd=1.0,
    )
    gw = ModelGateway(profiles=[profile], secrets_manager=secrets)
    return gw, "live"
