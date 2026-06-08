"""Tests for model auto-configurator, prioritizer, and discovery wiring."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.models.auto_configurator import AutoConfigurator, ModelPrioritizer, _safe_float, _safe_profile_id


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
            {"id": "new-model", "name": "New", "context_length": 8192,
             "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        assert len(merged) == 1
        assert merged[0]["model_profile_id"] == "openrouter-new-model"

    def test_existing_preserves_user_fields(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile(
            "openrouter-old-model",
            enabled=False,
            user_priority="prioritized",
            role_names=["custom"],
        )]
        scraped = [
            {"id": "old-model", "name": "Old", "context_length": 16384,
             "pricing": {"prompt": "0.001", "completion": "0.002"}}
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
            {"id": "new-model", "name": "New", "context_length": 8192,
             "pricing": {"prompt": "0", "completion": "0"}}
        ]
        merged = cfg.merge_profiles(existing, scraped, "openrouter")
        gone = next(m for m in merged if "gone" in m["model_profile_id"])
        assert gone["enabled"] is False

    def test_merge_preserves_credential_alias(self) -> None:
        cfg = AutoConfigurator()
        existing = [self._make_profile(
            "openrouter-model-x", credential_alias="MY_CUSTOM_KEY"
        )]
        scraped = [
            {"id": "model-x", "name": "Model X", "context_length": 8192,
             "pricing": {"prompt": "0", "completion": "0"}}
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
        cheap = self._make_model(
            model_profile_id="cheap", cost_per_input_token=0.0000001
        )
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
