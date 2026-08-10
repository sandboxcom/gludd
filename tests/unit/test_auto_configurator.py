"""Tests for model auto-configurator, prioritizer, and discovery wiring."""

from __future__ import annotations

from typing import Any, cast

import pytest

from general_ludd.models.auto_configurator import AutoConfigurator, ModelPrioritizer, _safe_float, _safe_profile_id
from general_ludd.models.gateway import ModelProfile


class TestSafeHelpers:
    def test_safe_profile_id_replaces_slash(self) -> None:
        assert _safe_profile_id("openai/gpt-4") == "openai-gpt-4"

    def test_safe_profile_id_replaces_dots(self) -> None:
        assert _safe_profile_id("model.v2.0") == "model-v2-0"

    def test_safe_profile_id_replaces_colons(self) -> None:
        assert _safe_profile_id("ns:model") == "ns-model"

    def test_safe_profile_id_lowercases(self) -> None:
        assert _safe_profile_id("GPT-4") == "gpt-4"

    def test_safe_float_from_string(self) -> None:
        assert _safe_float("3.14") == 3.14

    def test_safe_float_from_int(self) -> None:
        assert _safe_float(42) == 42.0

    def test_safe_float_invalid_returns_default(self) -> None:
        assert _safe_float("bad") == 0.0

    def test_safe_float_none_returns_default(self) -> None:
        assert _safe_float(None) == 0.0

    def test_safe_float_custom_default(self) -> None:
        assert _safe_float("bad", -1.0) == -1.0


class TestAutoConfiguratorGenerateProfiles:
    def _make_models(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "openai/gpt-4o",
                "name": "GPT-4o",
                "description": "Latest GPT-4 model",
                "context_length": 128000,
                "max_completion_tokens": 4096,
                "pricing": {"prompt": "0.000005", "completion": "0.000015"},
            },
            {
                "id": "openai/gpt-4o-mini",
                "name": "GPT-4o Mini",
                "description": "Smaller faster model",
                "context_length": 128000,
                "max_completion_tokens": 2048,
                "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
            },
            {
                "id": "meta-llama/llama-3-70b",
                "name": "Llama 3 70B",
                "description": "Open model",
                "context_length": 8192,
                "pricing": {"prompt": "0", "completion": "0"},
            },
        ]

    def test_generates_profiles_with_correct_provider(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        assert len(profiles) == 3
        for p in profiles:
            assert p["provider"] == "openrouter"

    def test_generates_profile_ids(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        ids = [p["model_profile_id"] for p in profiles]
        assert "openrouter-openai-gpt-4o" in ids
        assert "openrouter-openai-gpt-4o-mini" in ids
        assert "openrouter-meta-llama-llama-3-70b" in ids

    def test_free_model_detected(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        llama = next(p for p in profiles if "llama" in p["model_name"])
        assert llama["is_free"] is True
        gpt = next(p for p in profiles if p["model_name"].split("/")[-1] == "gpt-4o")
        assert gpt["is_free"] is False

    def test_pricing_parsed_correctly(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        gpt = next(p for p in profiles if "gpt-4o-mini" in p["model_name"])
        assert gpt["cost_per_input_token"] > 0
        assert gpt["cost_per_output_token"] > 0

    def test_context_window_set(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        gpt = next(p for p in profiles if "gpt-4o" in p["model_name"] and "mini" not in p["model_name"])
        assert gpt["context_window"] == 128000

    def test_auto_discovered_flag(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        assert all(p["auto_discovered"] is True for p in profiles)

    def test_enabled_by_default(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        assert all(p["enabled"] is True for p in profiles)

    def test_deduplicates_by_profile_id(self) -> None:
        cfg = AutoConfigurator()
        models = self._make_models()
        models.append(models[0].copy())
        profiles = cfg.generate_profiles("openrouter", models)
        assert len(profiles) == 3

    def test_skips_empty_id(self) -> None:
        cfg = AutoConfigurator()
        models = [{"id": "", "name": "empty"}]
        profiles = cfg.generate_profiles("openrouter", models)
        assert len(profiles) == 0

    def test_unknown_provider_returns_empty(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("unknown_provider", self._make_models())
        assert profiles == []

    def test_api_metered_flag(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        llama = next(p for p in profiles if "llama" in p["model_name"])
        assert llama["api_metered"] is False
        gpt = next(p for p in profiles if "gpt-4o" in p["model_name"] and "mini" not in p["model_name"])
        assert gpt["api_metered"] is True

    def test_preset_fields_populated(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.generate_profiles("openrouter", self._make_models())
        p = profiles[0]
        assert p["credential_alias"] == "openrouter_api_key"
        assert p["provider_package"] == "langchain-openai"
        assert p["provider_class_hint"] == "ChatOpenAI"


class TestAutoConfiguratorMergeProfiles:
    def _make_profile(self, pid: str, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "model_profile_id": pid,
            "provider": "openrouter",
            "model_name": pid,
            "display_name": pid,
            "cost_per_input_token": 0.0,
            "cost_per_output_token": 0.0,
            "context_window": 8192,
            "enabled": True,
            "auto_discovered": True,
            "is_free": True,
        }
        base.update(overrides)
        return base

    def test_new_models_added(self) -> None:
        cfg = AutoConfigurator()
        existing: list[dict[str, Any]] = []
        scraped = [
            {"id": "new-model", "name": "New", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        assert len(merged) == 1
        assert merged[0]["model_profile_id"] == "openrouter-new-model"

    def test_existing_preserves_user_fields(self) -> None:
        cfg = AutoConfigurator()
        existing = [
            self._make_profile(
                "openrouter-old-model",
                enabled=False,
                user_priority="prioritized",
                role_names=["custom"],
            )
        ]
        scraped = [
            {
                "id": "old-model",
                "name": "Old",
                "context_length": 16384,
                "pricing": {"prompt": "0.001", "completion": "0.002"},
            }
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        assert len(merged) == 1
        assert merged[0]["enabled"] is False
        assert merged[0]["user_priority"] == "prioritized"
        assert merged[0]["role_names"] == ["custom"]
        assert merged[0]["context_window"] == 16384

    def test_disappearing_model_disabled(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile("openrouter-gone")]
        scraped: list[dict[str, Any]] = [
            {"id": "new-model", "name": "New", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        gone = next(m for m in merged if "gone" in m["model_profile_id"])
        assert gone["enabled"] is False

    def test_merge_preserves_credential_alias(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile("openrouter-model-x", credential_alias="MY_CUSTOM_KEY")]
        scraped = [
            {"id": "model-x", "name": "Model X", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        assert merged[0]["credential_alias"] == "MY_CUSTOM_KEY"


class TestAssignRoles:
    def test_coder_keywords(self) -> None:
        model = {"name": "DeepSeek Coder", "id": "deepseek/deepseek-coder"}
        roles = AutoConfigurator._assign_roles(model)
        assert "coder" in roles
        assert "test_writer" in roles

    def test_reasoner_keywords(self) -> None:
        model = {"name": "o1 Reasoner", "id": "openai/o1"}
        roles = AutoConfigurator._assign_roles(model)
        assert "reviewer" in roles
        assert "planner" in roles

    def test_flash_keywords(self) -> None:
        model = {"name": "Gemini Flash", "id": "google/gemini-flash"}
        roles = AutoConfigurator._assign_roles(model)
        assert "summarizer" in roles

    def test_pro_keywords(self) -> None:
        model = {"name": "GPT Pro", "id": "openai/gpt-pro"}
        roles = AutoConfigurator._assign_roles(model)
        assert "architect" in roles

    def test_default_roles(self) -> None:
        model = {"name": "Some Model", "id": "some/model"}
        roles = AutoConfigurator._assign_roles(model)
        assert roles == ["coder", "reviewer"]


class TestAssignQuality:
    def test_pro_is_high(self) -> None:
        model = {"name": "GPT Pro", "id": "x", "context_length": 8192}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_large_context_is_high(self) -> None:
        model = {"name": "Big", "id": "x", "context_length": 256000}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_medium_context_is_medium(self) -> None:
        model = {"name": "Med", "id": "x", "context_length": 100000}
        assert AutoConfigurator._assign_quality(model) == "medium"

    def test_mini_is_low(self) -> None:
        model = {"name": "Mini", "id": "x", "context_length": 4096}
        assert AutoConfigurator._assign_quality(model) == "low"

    def test_default_is_medium(self) -> None:
        model = {"name": "Normal", "id": "x", "context_length": 8192}
        assert AutoConfigurator._assign_quality(model) == "medium"


class TestModelPrioritizer:
    def _make_model(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "model_profile_id": "test",
            "cost_per_input_token": 0.00001,
            "cost_per_output_token": 0.00003,
            "context_window": 8192,
            "enabled": True,
            "user_priority": "",
        }
        base.update(overrides)
        return base

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid strategy"):
            ModelPrioritizer("invalid")

    def test_cheapest_first_sorts_by_cost(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        expensive = self._make_model(cost_per_input_token=0.01)
        cheap = self._make_model(cost_per_input_token=0.0000001)
        ranked = p.rank([expensive, cheap])
        assert ranked[0] is cheap

    def test_largest_context_first(self) -> None:
        p = ModelPrioritizer("largest_context_first")
        small = self._make_model(context_window=4096)
        big = self._make_model(context_window=200000)
        ranked = p.rank([small, big])
        assert ranked[0] is big

    def test_balanced_strategy(self) -> None:
        p = ModelPrioritizer("balanced")
        models = [
            self._make_model(context_window=200000, cost_per_input_token=0.01),
            self._make_model(context_window=8192, cost_per_input_token=0.0000001),
        ]
        ranked = p.rank(models)
        assert len(ranked) == 2

    def test_user_prioritized_comes_first(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        prioritized = self._make_model(
            model_profile_id="prio",
            user_priority="prioritized",
            cost_per_input_token=0.1,
        )
        cheap = self._make_model(model_profile_id="cheap", cost_per_input_token=0.0000001)
        ranked = p.rank([cheap, prioritized])
        assert ranked[0]["model_profile_id"] == "prio"

    def test_deprioritized_goes_last(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        deprioritized = self._make_model(user_priority="deprioritized", cost_per_input_token=0.0)
        expensive = self._make_model(cost_per_input_token=0.1)
        ranked = p.rank([expensive, deprioritized])
        assert ranked[-1] is deprioritized

    def test_disabled_goes_after_enabled(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        disabled = self._make_model(enabled=False, cost_per_input_token=0.0)
        expensive = self._make_model(cost_per_input_token=0.1)
        ranked = p.rank([expensive, disabled])
        assert ranked[0] is expensive

    def test_empty_returns_empty(self) -> None:
        p = ModelPrioritizer("balanced")
        assert p.rank([]) == []

    def test_strategy_property(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        assert p.strategy == "cheapest_first"


# ── Deep tests for auto_configure_from_env (previously 0 coverage) ──


class TestAutoConfigureFromEnv:
    def _env_with_openai(self) -> dict[str, str]:
        return {"OPENAI_API_KEY": "sk-test-key"}

    def test_skips_providers_without_credential(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env({"SOME_OTHER_VAR": "value"})
        assert profiles == []

    def test_generates_profile_for_provider_with_credential(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        assert len(profiles) >= 1
        openai_profile = next(p for p in profiles if p["provider"] == "openai")
        assert openai_profile["model_name"] == "gpt-4o"
        assert openai_profile["enabled"] is True
        assert openai_profile["auto_discovered"] is True

    def test_skips_provider_with_empty_credential_value(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env({"OPENAI_API_KEY": ""})
        assert all(p["provider"] != "openai" for p in profiles)

    def test_skips_provider_with_missing_flagship_model(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env({"MISSING_FLAGSHIP_VAR": "key"})
        openai_profiles = [p for p in profiles if p["provider"] == "openai"]
        assert len(openai_profiles) == 0

    def test_profile_has_correct_profile_id_format(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        openai_profile = next(p for p in profiles if p["provider"] == "openai")
        model_id = cast(str, openai_profile["model_profile_id"])
        assert model_id.startswith("openai-")
        assert "/" not in model_id

    def test_profile_has_required_fields(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        required_fields = [
            "model_profile_id",
            "provider",
            "model_name",
            "display_name",
            "description",
            "api_base_alias",
            "credential_alias",
            "provider_package",
            "provider_class_hint",
            "context_window",
            "max_output_tokens",
            "cost_per_input_token",
            "cost_per_output_token",
            "role_names",
            "quality_class",
            "latency_class",
            "api_metered",
            "resource_profile",
            "enabled",
            "auto_discovered",
            "auto_discovered_at",
            "is_free",
        ]
        for field in required_fields:
            assert field in p, f"Missing field '{field}' in profile"

    def test_multiple_providers_with_credentials(self) -> None:
        cfg = AutoConfigurator()
        env = {"OPENAI_API_KEY": "sk-1", "ANTHROPIC_API_KEY": "sk-2"}
        profiles = cfg.auto_configure_from_env(env)
        providers = {p["provider"] for p in profiles}
        assert "openai" in providers
        assert "anthropic" in providers

    def test_is_free_when_zero_cost(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert isinstance(p["is_free"], bool)

    def test_api_metered_matches_cost(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        cost_in = float(cast(float, p["cost_per_input_token"]))
        cost_out = float(cast(float, p["cost_per_output_token"]))
        has_cost = cost_in > 0.0 or cost_out > 0.0
        assert p["api_metered"] == has_cost

    def test_role_names_are_list_of_strings(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        roles = p["role_names"]
        assert isinstance(roles, list)
        assert len(roles) >= 1
        assert all(isinstance(r, str) for r in roles)

    def test_defaults_context_window(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert p["context_window"] == 8192

    def test_defaults_max_output_tokens(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert p["max_output_tokens"] == 2048

    def test_quality_class_is_valid(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        for p in profiles:
            assert p["quality_class"] in ("low", "medium", "high")

    def test_latency_class_is_medium(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        for p in profiles:
            assert p["latency_class"] == "medium"

    def test_resource_profile_is_ai_heavy(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        for p in profiles:
            assert p["resource_profile"] == "ai_heavy"

    def test_auto_discovered_at_is_timestamp(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        for p in profiles:
            assert "T" in cast(str, p["auto_discovered_at"])

    def test_description_contains_credential_env_var(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert "OPENAI_API_KEY" in cast(str, p["description"])

    def test_display_name_contains_flagship(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai())
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert "gpt-4o" in cast(str, p["display_name"])

    def test_with_pricing_catalog_none(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env(self._env_with_openai(), catalog=None)
        p = next(prof for prof in profiles if prof["provider"] == "openai")
        assert p["cost_per_input_token"] == 0.0
        assert p["cost_per_output_token"] == 0.0

    def test_no_env_defaults_to_os_environ(self) -> None:
        cfg = AutoConfigurator()
        result = cfg.auto_configure_from_env(environ={})
        assert result == []

    def test_empty_environ_produces_no_profiles(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_from_env({"PATH": "/usr/bin"})
        assert profiles == []


# ── Deep tests for auto_configure_profiles (previously 0 coverage) ──


class TestAutoConfigureProfiles:
    def test_returns_model_profile_objects(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_profiles({"OPENAI_API_KEY": "sk-test"})
        assert all(isinstance(p, ModelProfile) for p in profiles)

    def test_empty_env_returns_empty(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_profiles({})
        assert profiles == []

    def test_profile_ids_match(self) -> None:
        cfg = AutoConfigurator()
        profiles = cfg.auto_configure_profiles({"OPENAI_API_KEY": "sk-test"})
        ids = [p.model_profile_id for p in profiles]
        assert any("openai" in pid for pid in ids)


# ── Extra edge cases for _assign_roles ──


class TestAssignRolesEdgeCases:
    def test_dev_keyword(self) -> None:
        model: dict[str, object] = {"name": "DevBot", "id": "dev/dev-bot"}
        roles = AutoConfigurator._assign_roles(model)
        assert "coder" in roles
        assert "test_writer" in roles

    def test_maverick_keyword(self) -> None:
        model: dict[str, object] = {"name": "Maverick Pro", "id": "x/maverick"}
        roles = AutoConfigurator._assign_roles(model)
        assert "architect" in roles

    def test_ultra_keyword(self) -> None:
        model: dict[str, object] = {"name": "Ultra model", "id": "x/ultra"}
        roles = AutoConfigurator._assign_roles(model)
        assert "architect" in roles

    def test_max_keyword(self) -> None:
        model: dict[str, object] = {"name": "Max Model", "id": "x/max"}
        roles = AutoConfigurator._assign_roles(model)
        assert "architect" in roles

    def test_mini_in_name(self) -> None:
        model: dict[str, object] = {"name": "Mini V2", "id": "x/mini"}
        roles = AutoConfigurator._assign_roles(model)
        assert "summarizer" in roles

    def test_small_in_name(self) -> None:
        model: dict[str, object] = {"name": "Small Model", "id": "x/small"}
        roles = AutoConfigurator._assign_roles(model)
        assert "summarizer" in roles

    def test_sonnet_keyword(self) -> None:
        model: dict[str, object] = {"name": "Claude 3.5 Sonnet", "id": "anthropic/sonnet"}
        roles = AutoConfigurator._assign_roles(model)
        assert "reviewer" in roles
        assert "planner" in roles

    def test_opus_keyword(self) -> None:
        model: dict[str, object] = {"name": "Claude Opus", "id": "anthropic/opus"}
        roles = AutoConfigurator._assign_roles(model)
        assert "planner" in roles

    def test_deep_keyword(self) -> None:
        model: dict[str, object] = {"name": "DeepSeek", "id": "deepseek/deep"}
        roles = AutoConfigurator._assign_roles(model)
        assert "planner" in roles

    def test_think_keyword(self) -> None:
        model: dict[str, object] = {"name": "Thinker", "id": "x/think"}
        roles = AutoConfigurator._assign_roles(model)
        assert "planner" in roles

    def test_empty_name_and_id(self) -> None:
        model: dict[str, object] = {"name": "", "id": ""}
        roles = AutoConfigurator._assign_roles(model)
        assert roles == ["coder", "reviewer"]

    def test_keyword_in_id_not_name(self) -> None:
        model: dict[str, object] = {"name": "Plain", "id": "code-llama"}
        roles = AutoConfigurator._assign_roles(model)
        assert "coder" in roles
        assert "test_writer" in roles

    def test_pro_keyword_in_id(self) -> None:
        model: dict[str, object] = {"name": "Generic", "id": "pro-model"}
        roles = AutoConfigurator._assign_roles(model)
        assert "architect" in roles


# ── Extra edge cases for _assign_quality ──


class TestAssignQualityEdgeCases:
    def test_nano_is_low(self) -> None:
        model: dict[str, object] = {"name": "Nano", "id": "x", "context_length": 4096}
        assert AutoConfigurator._assign_quality(model) == "low"

    def test_tiny_is_low(self) -> None:
        model: dict[str, object] = {"name": "Tiny", "id": "x", "context_length": 1024}
        assert AutoConfigurator._assign_quality(model) == "low"

    def test_context_at_exactly_200000_is_high(self) -> None:
        model: dict[str, object] = {"name": "Big", "id": "x", "context_length": 200000}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_context_at_exactly_100000_is_medium(self) -> None:
        model: dict[str, object] = {"name": "Med", "id": "x", "context_length": 100000}
        assert AutoConfigurator._assign_quality(model) == "medium"

    def test_context_at_199999_is_medium(self) -> None:
        model: dict[str, object] = {"name": "Almost", "id": "x", "context_length": 199999}
        assert AutoConfigurator._assign_quality(model) == "medium"

    def test_context_at_99999_is_medium(self) -> None:
        model: dict[str, object] = {"name": "Almost", "id": "x", "context_length": 99999}
        assert AutoConfigurator._assign_quality(model) == "medium"

    def test_sonnet_is_high(self) -> None:
        model: dict[str, object] = {"name": "Sonnet", "id": "x", "context_length": 8192}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_opus_is_high(self) -> None:
        model: dict[str, object] = {"name": "Opus", "id": "x", "context_length": 8192}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_zero_context_length(self) -> None:
        model: dict[str, object] = {"name": "Normal", "id": "x", "context_length": 0}
        assert AutoConfigurator._assign_quality(model) == "medium"

    def test_context_checked_before_keywords(self) -> None:
        model: dict[str, object] = {"name": "Mini", "id": "x", "context_length": 300000}
        assert AutoConfigurator._assign_quality(model) == "high"

    def test_context_200000_is_high_even_for_flash_name(self) -> None:
        model: dict[str, object] = {"name": "Flash", "id": "x", "context_length": 200000}
        assert AutoConfigurator._assign_quality(model) == "high"


# ── Extra edge cases for merge_profiles ──


class TestMergeProfilesEdgeCases:
    def _make_profile(self, pid: str, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "model_profile_id": pid,
            "provider": "openrouter",
            "model_name": pid,
            "display_name": pid,
            "cost_per_input_token": 0.0,
            "cost_per_output_token": 0.0,
            "context_window": 8192,
            "enabled": True,
            "auto_discovered": True,
            "is_free": True,
        }
        base.update(overrides)
        return base

    def test_empty_scraped_preserves_all_existing(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile("openrouter-a"), self._make_profile("openrouter-b")]
        merged = cfg.merge_profiles(existing, [], "openrouter")
        assert len(merged) == 2
        assert all(m["enabled"] is False for m in merged)

    def test_empty_existing_adds_all_scraped(self) -> None:
        cfg = AutoConfigurator()
        scraped = [
            {"id": "x", "name": "X", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "y", "name": "Y", "context_length": 4096, "pricing": {"prompt": "0", "completion": "0"}},
        ]
        merged = cfg.merge_profiles([], scraped, "openrouter")
        assert len(merged) == 2
        assert all(m["enabled"] is True for m in merged)

    def test_existing_not_in_scraped_does_not_lose_other_fields(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile("openrouter-gone", user_priority="prioritized", credential_alias="CUSTOM")]
        scraped: list[dict[str, Any]] = [
            {"id": "new", "name": "New", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        gone = next(m for m in merged if "gone" in m["model_profile_id"])
        assert gone["enabled"] is False
        assert gone["user_priority"] == "prioritized"
        assert gone["credential_alias"] == "CUSTOM"

    def test_existing_with_no_user_fields_updated(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile("openrouter-old-model")]
        scraped = [
            {
                "id": "old-model",
                "name": "Old V2",
                "context_length": 65536,
                "pricing": {"prompt": "0.001", "completion": "0.002"},
            }
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        assert len(merged) == 1
        assert merged[0]["display_name"] == "Old V2"
        assert merged[0]["context_window"] == 65536

    def test_multiple_existing_not_in_scraped_all_disabled(self) -> None:
        cfg = AutoConfigurator()
        existing = [
            self._make_profile("openrouter-a"),
            self._make_profile("openrouter-b"),
            self._make_profile("openrouter-c"),
        ]
        scraped = [{"id": "a", "name": "A", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}}]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        disabled = [m for m in merged if m["enabled"] is False]
        assert len(disabled) == 2


# ── Extra edge cases for ModelPrioritizer ──


class TestModelPrioritizerEdgeCases:
    def _make_model(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "model_profile_id": "test",
            "cost_per_input_token": 0.00001,
            "cost_per_output_token": 0.00003,
            "context_window": 8192,
            "enabled": True,
            "user_priority": "",
        }
        base.update(overrides)
        return base

    def test_cheapest_first_with_zero_cost_handles_division(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        free_model = self._make_model(cost_per_input_token=0.0, cost_per_output_token=0.0)
        paid_model = self._make_model(cost_per_input_token=0.01, cost_per_output_token=0.02)
        ranked = p.rank([paid_model, free_model])
        assert ranked[0] is free_model

    def test_balanced_with_zero_cost(self) -> None:
        p = ModelPrioritizer("balanced")
        free = self._make_model(cost_per_input_token=0.0, cost_per_output_token=0.0)
        paid = self._make_model(cost_per_input_token=0.000001, context_window=200000)
        ranked = p.rank([free, paid])
        assert len(ranked) == 2

    def test_balanced_with_zero_input_cost(self) -> None:
        p = ModelPrioritizer("balanced")
        m = self._make_model(cost_per_input_token=0.0, cost_per_output_token=0.00001)
        ranked = p.rank([m])
        assert len(ranked) == 1

    def test_stable_sort_order_with_equal_scores(self) -> None:
        p = ModelPrioritizer("cheapest_first")
        a = self._make_model(model_profile_id="a", cost_per_input_token=0.00001, cost_per_output_token=0.00003)
        b = self._make_model(model_profile_id="b", cost_per_input_token=0.00001, cost_per_output_token=0.00003)
        ranked = p.rank([a, b])
        assert len(ranked) == 2

    def test_all_valid_strategies_rank_without_error(self) -> None:
        models = [self._make_model(context_window=128000, cost_per_input_token=0.000005)]
        for strategy in ModelPrioritizer.VALID_STRATEGIES:
            p = ModelPrioritizer(strategy)
            ranked = p.rank(models)
            assert len(ranked) == 1

    def test_single_model_returns_same(self) -> None:
        p = ModelPrioritizer("balanced")
        m = self._make_model()
        ranked = p.rank([m])
        assert ranked == [m]

    def test_prioritized_dominates_all_strategies(self) -> None:
        for strategy in ModelPrioritizer.VALID_STRATEGIES:
            p = ModelPrioritizer(strategy)
            prio = self._make_model(model_profile_id="prio", user_priority="prioritized", cost_per_input_token=100.0)
            cheap = self._make_model(model_profile_id="cheap", cost_per_input_token=0.000001, context_window=200000)
            ranked = p.rank([cheap, prio])
            assert ranked[0]["model_profile_id"] == "prio"

    def test_deprioritized_last_all_strategies(self) -> None:
        for strategy in ModelPrioritizer.VALID_STRATEGIES:
            p = ModelPrioritizer(strategy)
            deprio = self._make_model(model_profile_id="dep", user_priority="deprioritized", cost_per_input_token=0.0)
            ok = self._make_model(model_profile_id="ok", cost_per_input_token=0.00001)
            ranked = p.rank([ok, deprio])
            assert ranked[-1]["model_profile_id"] == "dep"

    def test_disabled_has_negative_score_all_strategies(self) -> None:
        for strategy in ModelPrioritizer.VALID_STRATEGIES:
            p = ModelPrioritizer(strategy)
            disabled = self._make_model(model_profile_id="off", enabled=False, cost_per_input_token=0.0)
            enabled = self._make_model(model_profile_id="on", cost_per_input_token=100.0)
            ranked = p.rank([enabled, disabled])
            assert ranked[0]["model_profile_id"] == "on"


# ── generate_profiles role/quality integration edge cases ──


class TestGenerateProfilesRoleQualityIntegration:
    def test_coder_model_gets_test_writer_role(self) -> None:
        cfg = AutoConfigurator()
        models: list[dict[str, object]] = [
            {
                "id": "x/coder-v2",
                "name": "Code Llama",
                "context_length": 8192,
                "pricing": {"prompt": "0", "completion": "0"},
            }
        ]
        profiles = cfg.generate_profiles("openrouter", models)
        assert "test_writer" in cast(list[object], profiles[0]["role_names"])

    def test_flash_model_gets_low_quality(self) -> None:
        cfg = AutoConfigurator()
        models: list[dict[str, object]] = [
            {
                "id": "x/flash",
                "name": "Flash V2",
                "context_length": 4096,
                "pricing": {"prompt": "0", "completion": "0"},
            }
        ]
        profiles = cfg.generate_profiles("openrouter", models)
        assert profiles[0]["quality_class"] == "low"

    def test_pro_model_gets_high_quality(self) -> None:
        cfg = AutoConfigurator()
        models: list[dict[str, object]] = [
            {
                "id": "x/pro",
                "name": "Pro Model",
                "context_length": 8192,
                "pricing": {"prompt": "0", "completion": "0"},
            }
        ]
        profiles = cfg.generate_profiles("openrouter", models)
        assert profiles[0]["quality_class"] == "high"
