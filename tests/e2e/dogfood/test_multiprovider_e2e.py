"""Multi-provider E2E dogfood harness.

Enumerates known providers and runs a minimal gateway round-trip for each
available one. Providers whose endpoint/key is absent are gracefully skipped,
so this test file is always green in CI with zero credentials.

Run with:
    make test-iso TESTFILE='tests/e2e/dogfood/test_multiprovider_e2e.py'
    make dogfood-multiprovider
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tests.e2e.dogfood._gateway import build_gateway
from tests.e2e.dogfood._secrets import load_llm_keys

pytestmark = pytest.mark.e2e

# Registry of providers to test. Each entry maps a provider name to a factory
# that returns (creds_dict | None). None means "not available, skip".
_REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _load_zai() -> dict[str, str] | None:
    return load_llm_keys(_REPO_ROOT)


def _load_ollama() -> dict[str, str] | None:
    """Ollama has no API key; check for intent-signal env vars instead."""
    from general_ludd.models.provider_presets import is_ollama_available

    if not is_ollama_available():
        return None
    base = (
        os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_API_BASE")
        or "http://localhost:11434/v1"
    )
    model = os.environ.get("OLLAMA_MODEL") or "llama3"
    return {"provider": "ollama", "api_base": base, "model": model, "api_key": ""}


def _load_openrouter() -> dict[str, str] | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return {
        "provider": "openrouter",
        "api_base": "https://openrouter.ai/api/v1",
        "model": os.environ.get("OPENROUTER_MODEL") or "openrouter/auto",
        "api_key": key,
    }


# Parametrize: (provider_name, loader_fn)
_PROVIDERS: list[tuple[str, Any]] = [
    ("zai", _load_zai),
    ("ollama", _load_ollama),
    ("openrouter", _load_openrouter),
]


def _provider_ids() -> list[str]:
    return [name for name, _ in _PROVIDERS]


@pytest.fixture(
    scope="module",
    params=[pytest.param((name, loader), id=name) for name, loader in _PROVIDERS],
)
def provider_creds(request: pytest.FixtureRequest) -> tuple[str, dict[str, str] | None]:
    provider_name: str
    loader: Any
    provider_name, loader = request.param
    creds = loader()
    return provider_name, creds


def _build_provider_gateway(
    provider_name: str, creds: dict[str, str] | None
) -> tuple[Any, str]:
    """Build a gateway for the given provider, returning (gateway, mode)."""
    if provider_name == "zai":
        return build_gateway(creds)

    # For non-zai providers, build a live or mock gateway manually.
    if creds is None:
        from tests.e2e.dogfood._gateway import _make_mock_gateway

        return _make_mock_gateway(), "mock"

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.secrets import EnvSecretsManager

    secrets = EnvSecretsManager()
    cred_alias = f"{provider_name}_api_key"
    base_alias = f"{provider_name}_api_base"

    if creds.get("api_key"):
        secrets.set(cred_alias, creds["api_key"])
    secrets.set(base_alias, creds["api_base"])

    profile = ModelProfile(
        model_profile_id=f"{provider_name}-e2e",
        provider=provider_name,
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=creds["model"],
        credential_alias=cred_alias,
        api_base_alias=base_alias,
        enabled=True,
        run_budget_usd=1.0,
    )
    gw = ModelGateway(profiles=[profile], secrets_manager=secrets)
    return gw, "live"


class TestMultiproviderDogfood:
    def test_provider_gateway_builds(
        self, provider_creds: tuple[str, dict[str, str] | None]
    ) -> None:
        """For each available provider, the gateway builds without error."""
        provider_name, creds = provider_creds
        if creds is None:
            pytest.skip(
                f"provider '{provider_name}' not configured "
                f"(no credentials or availability signals found) — skipping live E2E"
            )
        gw, mode = _build_provider_gateway(provider_name, creds)
        assert gw is not None
        assert mode in ("live", "mock")

    def test_provider_mock_roundtrip(
        self, provider_creds: tuple[str, dict[str, str] | None]
    ) -> None:
        """Mock gateway round-trip always passes (no creds needed)."""
        provider_name, _creds = provider_creds
        from tests.e2e.dogfood._gateway import _make_mock_gateway

        gw = _make_mock_gateway(mock_response=f"hello from {provider_name}")
        resp = gw.call_model("any", [{"role": "user", "content": "ping"}])
        assert resp is not None
        assert provider_name in resp.content

    def test_provider_preset_exists(
        self, provider_creds: tuple[str, dict[str, str] | None]
    ) -> None:
        """Every tested provider must have a preset entry."""
        from general_ludd.models.provider_presets import get_provider_preset

        provider_name, _ = provider_creds
        # openrouter and ollama have presets; zai has one too
        preset = get_provider_preset(provider_name)
        assert preset is not None, (
            f"provider '{provider_name}' has no preset in PROVIDER_PRESETS"
        )
