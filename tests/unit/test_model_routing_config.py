from __future__ import annotations

import pytest
import yaml

from general_ludd.config.model_routing import (
    ModelRoutingConfig,
    build_router_from_config,
    load_model_routing,
)
from general_ludd.models.router import ModelRouter


class TestModelRoutingConfigDefaults:
    def test_default_profile_is_none(self):
        cfg = ModelRoutingConfig()
        assert cfg.default_profile is None

    def test_weak_model_profile_is_none(self):
        cfg = ModelRoutingConfig()
        assert cfg.weak_model_profile is None

    def test_role_routing_empty(self):
        cfg = ModelRoutingConfig()
        assert cfg.role_routing == {}

    def test_quality_routing_empty(self):
        cfg = ModelRoutingConfig()
        assert cfg.quality_routing == {}

    def test_latency_routing_empty(self):
        cfg = ModelRoutingConfig()
        assert cfg.latency_routing == {}

    def test_pattern_routing_empty(self):
        cfg = ModelRoutingConfig()
        assert cfg.pattern_routing == {}


class TestModelRoutingConfigAllFields:
    @pytest.fixture()
    def full_config(self):
        return ModelRoutingConfig(
            default_profile="default_prof",
            weak_model_profile="weak_prof",
            role_routing={"coder": "zai_coder", "reviewer": "zai_reviewer"},
            quality_routing={"high": "zai_coder", "medium": "zai_fast"},
            latency_routing={"fast": "zai_fast"},
            pattern_routing={
                "return_review": "reviewer",
                "commit_message": "weak",
                "gap_analysis": "fast",
                "code_generation": "coder",
                "planning": "planner",
            },
        )

    def test_default_profile_set(self, full_config):
        assert full_config.default_profile == "default_prof"

    def test_weak_model_profile_set(self, full_config):
        assert full_config.weak_model_profile == "weak_prof"

    def test_role_routing_set(self, full_config):
        assert full_config.role_routing["coder"] == "zai_coder"
        assert full_config.role_routing["reviewer"] == "zai_reviewer"

    def test_quality_routing_set(self, full_config):
        assert full_config.quality_routing["high"] == "zai_coder"

    def test_latency_routing_set(self, full_config):
        assert full_config.latency_routing["fast"] == "zai_fast"

    def test_pattern_routing_set(self, full_config):
        assert full_config.pattern_routing["code_generation"] == "coder"
        assert full_config.pattern_routing["planning"] == "planner"


class TestModelRoutingConfigSerialization:
    def test_model_dump_round_trip(self):
        original = ModelRoutingConfig(
            default_profile="def",
            role_routing={"coder": "zai"},
        )
        data = original.model_dump()
        restored = ModelRoutingConfig(**data)
        assert restored == original

    def test_model_dump_json_round_trip(self):
        original = ModelRoutingConfig(
            default_profile="def",
            pattern_routing={"return_review": "reviewer"},
        )
        json_str = original.model_dump_json()
        restored = ModelRoutingConfig.model_validate_json(json_str)
        assert restored == original


class TestLoadModelRouting:
    def test_load_from_yaml_file(self, tmp_path):
        data = {
            "default_profile": "default_prof",
            "weak_model_profile": "weak_prof",
            "role_routing": {"coder": "zai_coder", "planner": "zai_planner"},
            "quality_routing": {"high": "zai_coder"},
            "latency_routing": {"fast": "zai_fast"},
            "pattern_routing": {"return_review": "reviewer"},
        }
        yml_file = tmp_path / "model_routing.yml"
        yml_file.write_text(yaml.dump(data))
        cfg = load_model_routing(yml_file)
        assert cfg.default_profile == "default_prof"
        assert cfg.weak_model_profile == "weak_prof"
        assert cfg.role_routing["coder"] == "zai_coder"
        assert cfg.quality_routing["high"] == "zai_coder"
        assert cfg.latency_routing["fast"] == "zai_fast"
        assert cfg.pattern_routing["return_review"] == "reviewer"

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = load_model_routing(tmp_path / "nonexistent.yml")
        assert cfg == ModelRoutingConfig()

    def test_load_empty_file_returns_defaults(self, tmp_path):
        yml_file = tmp_path / "empty.yml"
        yml_file.write_text("")
        cfg = load_model_routing(yml_file)
        assert cfg == ModelRoutingConfig()

    def test_load_partial_file(self, tmp_path):
        data = {"default_profile": "partial_prof", "role_routing": {"fast": "zai_fast"}}
        yml_file = tmp_path / "partial.yml"
        yml_file.write_text(yaml.dump(data))
        cfg = load_model_routing(yml_file)
        assert cfg.default_profile == "partial_prof"
        assert cfg.role_routing == {"fast": "zai_fast"}
        assert cfg.quality_routing == {}
        assert cfg.weak_model_profile is None


class TestBuildRouterFromConfig:
    def test_creates_model_router_instance(self):
        cfg = ModelRoutingConfig()
        router = build_router_from_config(cfg)
        assert isinstance(router, ModelRouter)

    def test_role_routing_resolves(self):
        cfg = ModelRoutingConfig(role_routing={"coder": "zai_coder", "reviewer": "zai_reviewer"})
        router = build_router_from_config(cfg)
        assert router.resolve_role("coder") == "zai_coder"
        assert router.resolve_role("reviewer") == "zai_reviewer"

    def test_default_profile_set(self):
        cfg = ModelRoutingConfig(default_profile="fallback_prof")
        router = build_router_from_config(cfg)
        assert router.default_profile_id == "fallback_prof"
        assert router.resolve_role("unknown") == "fallback_prof"

    def test_weak_model_profile_set(self):
        cfg = ModelRoutingConfig(weak_model_profile="weak_prof")
        router = build_router_from_config(cfg)
        assert router.weak_model_profile_id == "weak_prof"
        assert router.resolve_role("weak") == "weak_prof"

    def test_quality_routing_resolves(self):
        cfg = ModelRoutingConfig(quality_routing={"high": "zai_coder", "medium": "zai_fast"})
        router = build_router_from_config(cfg)
        assert router.resolve_by_quality("high") == "zai_coder"
        assert router.resolve_by_quality("medium") == "zai_fast"

    def test_latency_routing_resolves(self):
        cfg = ModelRoutingConfig(latency_routing={"fast": "zai_fast"})
        router = build_router_from_config(cfg)
        assert router.resolve_by_latency("fast") == "zai_fast"

    def test_pattern_routing_maps_to_roles(self):
        cfg = ModelRoutingConfig(
            role_routing={"reviewer": "zai_reviewer", "weak": "zai_weak", "fast": "zai_fast"},
            pattern_routing={
                "return_review": "reviewer",
                "commit_message": "weak",
                "gap_analysis": "fast",
            },
        )
        router = build_router_from_config(cfg)
        assert router.resolve_role("reviewer") == "zai_reviewer"
        assert router.resolve_role("weak") == "zai_weak"
        assert router.resolve_role("fast") == "zai_fast"

    def test_empty_config_creates_empty_router(self):
        cfg = ModelRoutingConfig()
        router = build_router_from_config(cfg)
        assert router.resolve_role("anything") is None
        assert router.resolve_by_quality("high") is None
        assert router.resolve_by_latency("fast") is None

    def test_full_config_integration(self):
        cfg = ModelRoutingConfig(
            default_profile="default",
            weak_model_profile="weak_prof",
            role_routing={
                "coder": "zai_coder",
                "planner": "zai_coder",
                "reviewer": "zai_coder",
                "fast": "zai_coder",
            },
            quality_routing={"high": "zai_coder", "medium": "zai_coder"},
            latency_routing={"fast": "zai_coder"},
            pattern_routing={
                "return_review": "reviewer",
                "commit_message": "weak",
                "gap_analysis": "fast",
                "code_generation": "coder",
                "planning": "planner",
            },
        )
        router = build_router_from_config(cfg)
        assert router.resolve_role("coder") == "zai_coder"
        assert router.resolve_role("weak") == "weak_prof"
        assert router.resolve_by_quality("high") == "zai_coder"
        assert router.resolve_by_latency("fast") == "zai_coder"
        assert router.default_profile_id == "default"
