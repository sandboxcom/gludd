"""Tests for OpenRouter auto-discovery, provider presets, and auto-configuration."""

# ruff: noqa: E501

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

OPENROUTER_FREE_MODELS_RESPONSE = {
    "data": [
        {
            "id": "google/gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "created": 1747689600,
            "description": "Google's latest multimodal model",
            "context_length": 1048576,
            "architecture": {"modality": "text+image->text", "tokenizer": "Gemini"},
            "pricing": {"prompt": "0.00000015", "completion": "0.00000060", "image": "0", "request": "0"},
            "top_provider": {"is_moderated": False, "max_completion_tokens": 65536},
        },
        {
            "id": "meta-llama/llama-4-maverick",
            "name": "Llama 4 Maverick",
            "created": 1746576000,
            "description": "Meta's latest open model",
            "context_length": 131072,
            "architecture": {"modality": "text->text", "tokenizer": "Llama4"},
            "pricing": {"prompt": "0.00000020", "completion": "0.00000060", "image": "0", "request": "0"},
            "top_provider": {"is_moderated": False, "max_completion_tokens": 16384},
        },
        {
            "id": "qwen/qwen3-coder",
            "name": "Qwen 3 Coder",
            "created": 1746144000,
            "description": "Qwen's coding-optimized model",
            "context_length": 131072,
            "architecture": {"modality": "text->text", "tokenizer": "Qwen2"},
            "pricing": {"prompt": "0.00000015", "completion": "0.00000060", "image": "0", "request": "0"},
            "top_provider": {"is_moderated": True, "max_completion_tokens": 32768},
        },
    ]
}


class TestProviderPresets:
    def test_openrouter_preset_has_url(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS.get("openrouter")
        assert preset is not None
        assert preset["api_base_url"] == "https://openrouter.ai/api/v1"
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    def test_openai_preset_has_url(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS.get("openai")
        assert preset is not None
        assert preset["api_base_url"] == "https://api.openai.com/v1"
        assert preset["provider_package"] == "langchain-openai"

    def test_anthropic_preset_has_url(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS.get("anthropic")
        assert preset is not None
        assert preset["api_base_url"] == "https://api.anthropic.com/v1"
        assert preset["provider_package"] == "langchain-anthropic"

    def test_all_presets_have_required_fields(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        required = {"api_base_url", "provider_package", "provider_class", "credential_env_var"}
        for name, preset in PROVIDER_PRESETS.items():
            for field in required:
                assert field in preset, f"Provider '{name}' missing field '{field}'"

    def test_get_provider_preset_unknown_returns_none(self):
        from general_ludd.models.provider_presets import get_provider_preset

        assert get_provider_preset("nonexistent") is None

    def test_get_provider_preset_known_returns_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("openrouter")
        assert preset is not None
        assert "api_base_url" in preset


class TestOpenRouterScraper:
    def test_parse_free_models_response(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        models = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)
        assert len(models) == 3
        assert models[0]["id"] == "google/gemini-2.5-flash"
        assert models[0]["name"] == "Gemini 2.5 Flash"
        assert models[0]["context_length"] == 1048576
        assert models[0]["pricing"]["prompt"] == "0.00000015"
        assert models[0]["pricing"]["completion"] == "0.00000060"
        assert models[2]["id"] == "qwen/qwen3-coder"
        assert models[2]["is_moderated"] is True

    def test_parse_empty_response(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        models = scraper._parse_models_response({"data": []})
        assert models == []

    def test_parse_missing_data_key(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        models = scraper._parse_models_response({})
        assert models == []

    def test_filter_free_models(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        models = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)
        assert len(models) == 3
        assert all(m.get("pricing", {}).get("prompt") is not None for m in models)

    @pytest.mark.asyncio
    async def test_fetch_models_success(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = OPENROUTER_FREE_MODELS_RESPONSE
        mock_client.__aenter__.return_value.get.return_value = mock_resp

        scraper = OpenRouterScraper()
        scraper._api_key = "test-key"
        with patch("general_ludd.models.openrouter_discovery.httpx.AsyncClient", return_value=mock_client):
            models = await scraper.fetch_models()
            assert len(models) == 3

    @pytest.mark.asyncio
    async def test_fetch_models_no_api_key_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraper._api_key = None
        models = await scraper.fetch_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_fetch_models_http_error_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraper._api_key = "test-key"
        with patch("general_ludd.models.openrouter_discovery.httpx.AsyncClient", side_effect=Exception("refused")):
            models = await scraper.fetch_models()
            assert models == []

    @pytest.mark.asyncio
    async def test_fetch_models_401_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=401, text="Unauthorized")
        mock_client.__aenter__.return_value.get.return_value = mock_resp

        scraper = OpenRouterScraper()
        scraper._api_key = "bad-key"
        with patch("general_ludd.models.openrouter_discovery.httpx.AsyncClient", return_value=mock_client):
            models = await scraper.fetch_models()
            assert models == []


class TestAutoConfigurator:
    def test_generate_model_profile_from_scraped(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraped_models = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)

        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        profiles = configurator.generate_profiles("openrouter", scraped_models)
        assert len(profiles) == 3

        gemini = profiles[0]
        assert gemini["model_profile_id"] == "openrouter-google-gemini-2-5-flash"
        assert gemini["provider"] == "openrouter"
        assert gemini["model_name"] == "google/gemini-2.5-flash"
        assert gemini["context_window"] == 1048576
        assert gemini["cost_per_input_token"] == 0.00000015
        assert gemini["cost_per_output_token"] == 0.00000060
        assert gemini["api_metered"] is True
        assert gemini["enabled"] is True

    def test_generate_profiles_empty_models(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        profiles = configurator.generate_profiles("openrouter", [])
        assert profiles == []

    def test_generate_profiles_deduplicates_by_id(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        scraped = [
            {"id": "a/b", "name": "A", "context_length": 1000, "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "a/b", "name": "A Dup", "context_length": 2000, "pricing": {"prompt": "0", "completion": "0"}},
        ]
        profiles = configurator.generate_profiles("openrouter", scraped)
        assert len(profiles) == 1

    def test_generate_profile_uses_provider_preset_urls(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        scraped = [
            {"id": "test/model", "name": "Test", "context_length": 8000, "pricing": {"prompt": "0", "completion": "0"}},
        ]
        profiles = configurator.generate_profiles("openrouter", scraped)
        assert profiles[0]["api_base_alias"] == "openrouter_api_base"
        assert profiles[0]["credential_alias"] == "openrouter_api_key"
        assert profiles[0]["provider_package"] == "langchain-openai"

    def test_auto_configure_role_assignment(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        scraped = [
            {
                "id": "qwen/qwen3-coder",
                "name": "Qwen 3 Coder",
                "context_length": 131072,
                "pricing": {"prompt": "0.00000015", "completion": "0.00000060"},
            },
            {
                "id": "meta-llama/llama-4-maverick",
                "name": "Llama 4 Maverick",
                "context_length": 131072,
                "pricing": {"prompt": "0.00000020", "completion": "0.00000060"},
            },
        ]
        profiles = configurator.generate_profiles("openrouter", scraped)
        coder_profile = [p for p in profiles if "coder" in p["model_name"].lower()]
        assert len(coder_profile) == 1
        assert "coder" in coder_profile[0]["role_names"]
        assert "reviewer" in coder_profile[0]["role_names"]


class TestModelPrioritizer:
    def test_prioritize_by_cost_cheapest_first(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "expensive", "cost_per_input_token": 0.01, "cost_per_output_token": 0.05, "context_window": 128000},
            {"model_profile_id": "cheap", "cost_per_input_token": 0.00000015, "cost_per_output_token": 0.00000060, "context_window": 131072},
            {"model_profile_id": "medium", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000},
        ]
        prioritizer = ModelPrioritizer(strategy="cheapest_first")
        ranked = prioritizer.rank(models)
        assert ranked[0]["model_profile_id"] == "cheap"
        assert ranked[-1]["model_profile_id"] == "expensive"

    def test_prioritize_by_context_largest_first(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "small", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 8000},
            {"model_profile_id": "large", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 200000},
            {"model_profile_id": "medium", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000},
        ]
        prioritizer = ModelPrioritizer(strategy="largest_context_first")
        ranked = prioritizer.rank(models)
        assert ranked[0]["model_profile_id"] == "large"
        assert ranked[-1]["model_profile_id"] == "small"

    def test_prioritize_balanced(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "cheap_small", "cost_per_input_token": 0.0000001, "cost_per_output_token": 0.0000003, "context_window": 8000},
            {"model_profile_id": "expensive_large", "cost_per_input_token": 0.01, "cost_per_output_token": 0.05, "context_window": 200000},
            {"model_profile_id": "mid", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 128000},
        ]
        prioritizer = ModelPrioritizer(strategy="balanced")
        ranked = prioritizer.rank(models)
        assert len(ranked) == 3
        assert ranked[0]["model_profile_id"] == "cheap_small"

    def test_prioritize_default_strategy(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        prioritizer = ModelPrioritizer()
        assert prioritizer._strategy == "balanced"

    def test_prioritize_filter_disabled(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "enabled", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000},
            {"model_profile_id": "disabled", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000, "enabled": False},
        ]
        prioritizer = ModelPrioritizer()
        ranked = prioritizer.rank(models)
        assert len(ranked) == 2
        assert ranked[-1]["model_profile_id"] == "disabled"

    def test_prioritize_user_deprioritized_last(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "normal", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000},
            {"model_profile_id": "deprioritized", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000, "user_priority": "deprioritized"},
        ]
        prioritizer = ModelPrioritizer()
        ranked = prioritizer.rank(models)
        assert ranked[-1]["model_profile_id"] == "deprioritized"

    def test_prioritize_user_prioritized_first(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        models = [
            {"model_profile_id": "normal", "cost_per_input_token": 0.001, "cost_per_output_token": 0.002, "context_window": 64000},
            {"model_profile_id": "prioritized", "cost_per_input_token": 0.01, "cost_per_output_token": 0.05, "context_window": 64000, "user_priority": "prioritized"},
        ]
        prioritizer = ModelPrioritizer()
        ranked = prioritizer.rank(models)
        assert ranked[0]["model_profile_id"] == "prioritized"

    def test_prioritize_empty_list(self):
        from general_ludd.models.auto_configurator import ModelPrioritizer

        prioritizer = ModelPrioritizer()
        assert prioritizer.rank([]) == []


class TestAutoUpdateJob:
    @pytest.mark.asyncio
    async def test_refresh_updates_model_list(self):
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraped = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)
        configurator = AutoConfigurator()
        profiles = configurator.generate_profiles("openrouter", scraped)
        assert len(profiles) == 3

        new_response = {
            "data": [
                {"id": "new/model", "name": "New Model", "context_length": 65536, "pricing": {"prompt": "0", "completion": "0"}},
            ]
        }
        new_scraped = scraper._parse_models_response(new_response)
        new_profiles = configurator.generate_profiles("openrouter", new_scraped)
        assert len(new_profiles) == 1
        assert new_profiles[0]["model_profile_id"] == "openrouter-new-model"

    @pytest.mark.asyncio
    async def test_refresh_upserts_not_duplicates(self):
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraped = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)
        configurator = AutoConfigurator()
        first_profiles = configurator.generate_profiles("openrouter", scraped)
        first_ids = {p["model_profile_id"] for p in first_profiles}
        assert len(first_ids) == 3

        same_scraped = scraper._parse_models_response(OPENROUTER_FREE_MODELS_RESPONSE)
        merged = configurator.merge_profiles(first_profiles, same_scraped, "openrouter")
        assert len(merged) == 3

    def test_credential_alias_detection(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias("openrouter", {"OPENROUTER_API_KEY": "sk-xxx"}) is True
        assert detect_credential_alias("openrouter", {}) is False
        assert detect_credential_alias("unknown", {}) is False

    def test_credential_alias_no_env_returns_false(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias("openrouter", {"SOME_OTHER_VAR": "val"}) is False


class TestListConfiguredProviders:
    def test_lists_configured_providers(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {"OPENROUTER_API_KEY": "sk-xxx", "OPENAI_API_KEY": "sk-yyy"}
        configured = list_configured_providers(env)
        assert "openrouter" in configured
        assert "openai" in configured

    def test_empty_env_returns_empty(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers({})
        assert configured == []

    def test_empty_string_value_not_configured(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {"OPENROUTER_API_KEY": ""}
        configured = list_configured_providers(env)
        assert "openrouter" not in configured

    def test_uses_os_environ_when_none(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers(None)
        assert isinstance(configured, list)
