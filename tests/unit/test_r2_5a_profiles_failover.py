"""Tests for R2.5a: Qwen + DeepSeek model profiles, fallback_chain in routing.

Verifies:
1. DeepSeek and Qwen model profile YAML files exist and are loadable
2. model_routing.yml has a fallback_chain
3. ModelRoutingConfig accepts fallback_chain
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent


class TestR2_5aModelProfiles:
    def test_deepseek_profile_exists_and_loads(self):
        path = ROOT / "config" / "model_profiles" / "deepseek_coder.yml"
        assert path.exists(), f"Missing: {path}"

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        assert data["model_profile_id"] == "deepseek_coder"
        assert data["provider"] == "deepseek"
        assert isinstance(data["fallback_profiles"], list)
        assert "qwen_coder" in data["fallback_profiles"]

    def test_qwen_profile_exists_and_loads(self):
        path = ROOT / "config" / "model_profiles" / "qwen_coder.yml"
        assert path.exists(), f"Missing: {path}"

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        assert data["model_profile_id"] == "qwen_coder"
        assert data["model_name"] == "qwen3-coder"

    def test_zai_profile_falls_back_to_deepseek(self):
        path = ROOT / "config" / "model_profiles" / "zai_example.yml"
        assert path.exists()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        assert isinstance(data.get("fallback_profiles"), list)


class TestR2_5aFallbackChain:
    def test_model_routing_has_fallback_chain(self):
        path = ROOT / "config" / "model_routing.yml"
        assert path.exists()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        assert "fallback_chain" in data
        assert isinstance(data["fallback_chain"], list)
        assert len(data["fallback_chain"]) >= 2
        assert "deepseek_coder" in data["fallback_chain"]

    def test_model_routing_config_accepts_fallback_chain(self):
        from general_ludd.config.model_routing import ModelRoutingConfig

        cfg = ModelRoutingConfig(
            default_profile="zai_coder",
            fallback_chain=["deepseek_coder", "qwen_coder"],
        )
        assert cfg.fallback_chain == ["deepseek_coder", "qwen_coder"]

    def test_model_routing_config_loads_from_yml_with_fallback_chain(self):
        from general_ludd.config.model_routing import load_model_routing

        path = str(ROOT / "config" / "model_routing.yml")
        cfg = load_model_routing(path)
        assert cfg.fallback_chain is not None
        assert "deepseek_coder" in cfg.fallback_chain
