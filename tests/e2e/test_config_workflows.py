"""E2E tests for config subsystem — 8 modules, end-to-end workflows.

Covers: project, task_loader, loader, user_config, deployment_optimization,
model_routing, binary_paths, project_dir.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ── project ───────────────────────────────────────────────────────────


class TestProjectRootEndToEnd:
    def test_finds_gludd_root_from_cwd(self):
        from general_ludd.config.project import find_project_root

        root = find_project_root()
        assert root is not None
        assert (root / ".gludd").is_dir()

    def test_finds_root_from_child_directory(self):
        from general_ludd.config.project import find_project_root

        root = find_project_root()
        src = root / "src"
        assert src.is_dir()
        found = find_project_root(src)
        assert found == root

    def test_returns_none_at_filesystem_root(self):
        from general_ludd.config.project import find_project_root

        assert find_project_root(Path("/")) is None


# ── task_loader ────────────────────────────────────────────────────────


class TestTaskLoaderEndToEnd:
    def test_loads_valid_task_definitions(self, tmp_path):
        from general_ludd.config.task_loader import load_task_definitions

        yml = tmp_path / "tasks.yml"
        yml.write_text(yaml.dump({"tasks": [
            {"name": "task-a", "description": "first task", "priority": 5},
            {"name": "task-b", "description": "second task", "priority": 3, "tags": ["urgent"]},
        ]}))
        tasks = load_task_definitions(yml)
        assert len(tasks) == 2
        assert tasks[0].name == "task-a"
        assert tasks[1].tags == ["urgent"]

    def test_returns_empty_list_for_nonexistent_file(self, tmp_path):
        from general_ludd.config.task_loader import load_task_definitions

        tasks = load_task_definitions(tmp_path / "nonexistent.yml")
        assert tasks == []

    def test_returns_empty_for_yaml_with_no_tasks_key(self, tmp_path):
        from general_ludd.config.task_loader import load_task_definitions

        yml = tmp_path / "empty.yml"
        yml.write_text("other_key: [1, 2, 3]\n")
        tasks = load_task_definitions(yml)
        assert tasks == []

    def test_returns_empty_for_null_yaml_content(self, tmp_path):
        from general_ludd.config.task_loader import load_task_definitions

        yml = tmp_path / "null.yml"
        yml.write_text("null\n")
        tasks = load_task_definitions(yml)
        assert tasks == []

    def test_discover_task_definitions_across_multiple_dirs(self, tmp_path):
        from general_ludd.config.task_loader import discover_task_definitions

        d1 = tmp_path / "tasks_a"
        d1.mkdir()
        (d1 / "task_main.yml").write_text(yaml.dump({"tasks": [
            {"name": "main-1", "description": "A"},
            {"name": "main-2", "description": "B"},
        ]}))
        d2 = tmp_path / "tasks_b"
        d2.mkdir()
        (d2 / "task_extra.yml").write_text(yaml.dump({"tasks": [
            {"name": "extra-1", "description": "C"},
        ]}))
        tasks = discover_task_definitions(d1, d2)
        assert len(tasks) == 3
        names = {t.name for t in tasks}
        assert names == {"main-1", "main-2", "extra-1"}

    def test_discover_skips_nonexistent_dirs(self, tmp_path):
        from general_ludd.config.task_loader import discover_task_definitions

        d = tmp_path / "real"
        d.mkdir()
        (d / "task.yml").write_text(yaml.dump({"tasks": [{"name": "only", "description": "X"}]}))
        tasks = discover_task_definitions(d, tmp_path / "gone")
        assert len(tasks) == 1

    def test_priority_validation_rejects_negative(self, tmp_path):
        from general_ludd.config.task_loader import load_task_definitions

        yml = tmp_path / "bad.yml"
        yml.write_text(yaml.dump({"tasks": [{"name": "bad", "description": "x", "priority": -1}]}))
        with pytest.raises(ValueError, match="priority must be non-negative"):
            load_task_definitions(yml)


# ── loader ─────────────────────────────────────────────────────────────


class TestConfigLoaderEndToEnd:
    def test_load_user_config_defaults_when_file_absent(self, tmp_path):
        from general_ludd.config.loader import load_user_config
        from general_ludd.config.user_config import UserConfig

        cfg = load_user_config(tmp_path / "nonexistent.yml")
        assert isinstance(cfg, UserConfig)
        assert cfg.network.host == "127.0.0.1"

    def test_load_agent_config_defaults_when_file_absent(self, tmp_path):
        from general_ludd.config.loader import load_agent_config
        from general_ludd.config.user_config import AgentConfig

        cfg = load_agent_config(tmp_path / "nonexistent.yml")
        assert isinstance(cfg, AgentConfig)
        assert cfg.session_notes == ""

    def test_build_config_layer_merges_user_and_agent(self, tmp_path):
        from general_ludd.config.loader import build_config_layer

        user_yml = tmp_path / "user.yml"
        user_yml.write_text(yaml.dump({"pipeline": {"enabled": True, "floor": 2}}))
        agent_yml = tmp_path / "agent.yml"
        agent_yml.write_text(yaml.dump({"model_routing": {"default_profile": "fast"}}))
        layer = build_config_layer(user_path=user_yml, agent_path=agent_yml)
        assert layer.user.pipeline.enabled is True
        assert layer.user.pipeline.floor == 2
        routing = layer.resolve_model_routing()
        assert routing.default_profile == "fast"

    def test_save_agent_config_round_trip(self, tmp_path):
        from general_ludd.config.loader import load_agent_config, save_agent_config
        from general_ludd.config.user_config import AgentConfig

        target = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(session_notes="E2E test notes", active_model_profile="opus")
        save_agent_config(cfg, target)
        reloaded = load_agent_config(target)
        assert reloaded.session_notes == "E2E test notes"
        assert reloaded.active_model_profile == "opus"

    def test_build_config_layer_with_defaults_override(self, tmp_path):
        from general_ludd.config.loader import build_config_layer

        user_yml = tmp_path / "user.yml"
        user_yml.write_text("{}")
        agent_yml = tmp_path / "agent.yml"
        agent_yml.write_text("{}")
        layer = build_config_layer(
            user_path=user_yml, agent_path=agent_yml,
            defaults={"timeout": 99, "retries": 3},
        )
        assert layer.resolve("timeout") == 99
        assert layer.resolve("retries") == 3
        assert layer.resolve("nonexistent_key") is None


# ── user_config ────────────────────────────────────────────────────────


class TestUserConfigEndToEnd:
    def test_network_world_open_rejects_missing_cidr(self):
        from general_ludd.config.user_config import NetworkConfig

        with pytest.raises(ValueError, match="binds to all interfaces"):
            NetworkConfig(host="0.0.0.0", allowed_cidr=[])

    def test_network_loopback_accepts_no_cidr(self):
        from general_ludd.config.user_config import NetworkConfig

        nc = NetworkConfig(host="127.0.0.1")
        assert nc.port == 8000

    def test_user_config_from_yaml_loads_and_env_overrides(self, tmp_path, monkeypatch):
        from general_ludd.config.user_config import UserConfig

        yml = tmp_path / "user.yml"
        yml.write_text(yaml.dump({
            "pipeline": {"enabled": True, "floor": 3},
            "agents": {"timeout": 30},
        }))
        monkeypatch.setenv("GLUDD_PIPELINE", '{"enabled": true, "floor": 5}')
        cfg = UserConfig.from_yaml(yml)
        assert cfg.pipeline.enabled is True
        assert cfg.pipeline.floor == 5

    def test_agent_config_defaults(self):
        from general_ludd.config.user_config import AgentConfig

        cfg = AgentConfig()
        assert cfg.active_model_profile is None
        assert cfg.bind_tools_on_dispatch is True

    def test_config_layer_resolve_agent_fallback(self):
        from general_ludd.config.user_config import AgentConfig, ConfigLayer

        agent = AgentConfig(session_notes="from agent")
        layer = ConfigLayer(agent=agent)
        assert layer.resolve("session_notes") == "from agent"

    def test_config_layer_resolve_non_dict_agent_val(self):
        from general_ludd.config.user_config import AgentConfig, ConfigLayer

        agent = AgentConfig(bind_tools_on_dispatch=False)
        layer = ConfigLayer(agent=agent)
        assert layer.resolve("bind_tools_on_dispatch") is False

    def test_pipeline_block_default_off(self):
        from general_ludd.config.user_config import PipelineConfigBlock

        p = PipelineConfigBlock()
        assert p.enabled is False
        assert p.floor == 1
        assert p.gate_debounce_s == 30.0


# ── deployment_optimization ────────────────────────────────────────────


class TestDeploymentOptimizationEndToEnd:
    def test_from_yaml_parses_full_config(self, tmp_path):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        yml = tmp_path / "deploy.yml"
        yml.write_text(yaml.dump({
            "vllm": {"defaults": {"dtype": "bfloat16"}},
            "llamacpp": {"defaults": {"n_gpu_layers": 40}},
            "hardware_presets": {
                "a100": {"vram_tier": "80gb", "vllm": {"tensor_parallel_size": 8}},
                "t4": {"vram_tier": "16gb", "llamacpp": {"n_gpu_layers": 35}},
            },
            "quantization_recommendations": [
                {"min_params_b": 7, "max_vram_gb": 16, "recommendation": "q4_k_m"},
                {"min_params_b": 13, "min_vram_gb": 24, "recommendation": "q5_k_m"},
            ],
        }))
        cfg = DeploymentOptimizationConfig.from_yaml(yml)
        assert cfg.vllm_defaults == {"dtype": "bfloat16"}
        assert len(cfg.hardware_presets) == 2
        assert len(cfg.quantization_recommendations) == 2

    def test_get_preset_returns_engine_defaults_plus_preset(self, tmp_path):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        yml = tmp_path / "deploy.yml"
        yml.write_text(yaml.dump({
            "vllm": {"defaults": {"dtype": "fp16"}},
            "hardware_presets": {
                "a100": {"vram_tier": "80gb", "vllm": {"tensor_parallel_size": 8}},
            },
        }))
        cfg = DeploymentOptimizationConfig.from_yaml(yml)
        result = cfg.get_preset("vllm", "a100")
        assert result["dtype"] == "fp16"
        assert result["tensor_parallel_size"] == 8
        assert result["vram_tier"] == "80gb"

    def test_get_preset_rejects_unknown_engine(self, tmp_path):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        yml = tmp_path / "deploy.yml"
        yml.write_text("{}")
        cfg = DeploymentOptimizationConfig.from_yaml(yml)
        with pytest.raises(ValueError, match="unknown engine"):
            cfg.get_preset("tensorrt", "a100")

    def test_get_preset_rejects_unknown_gpu(self, tmp_path):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        yml = tmp_path / "deploy.yml"
        yml.write_text("{}")
        cfg = DeploymentOptimizationConfig.from_yaml(yml)
        with pytest.raises(ValueError, match="unknown gpu_type"):
            cfg.get_preset("vllm", "h200")

    def test_validate_against_hardware_rejects_tp_without_nvlink(self):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        cfg = DeploymentOptimizationConfig()
        mock_hw = MagicMock()
        mock_hw.gpu_type = "t4"
        mock_hw.has_nvlink = False
        mock_hw.supports_fp8 = False
        mock_hw.total_vram_gb = 16.0
        mock_hw.gpu_count = 1
        with pytest.raises(ValueError, match="requires NVLink"):
            cfg.validate_against_hardware({"tensor_parallel_size": 4}, mock_hw)

    def test_validate_against_hardware_rejects_oversized_model(self):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        cfg = DeploymentOptimizationConfig()
        mock_hw = MagicMock()
        mock_hw.gpu_type = "t4"
        mock_hw.has_nvlink = False
        mock_hw.supports_fp8 = False
        mock_hw.total_vram_gb = 16.0
        mock_hw.gpu_count = 1
        with pytest.raises(ValueError, match="does not fit"):
            cfg.validate_against_hardware(
                {"params_b": 70.0, "gpu_memory_utilization": 0.9}, mock_hw,
            )

    def test_recommend_quantization_matches_rule(self, tmp_path):
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        yml = tmp_path / "deploy.yml"
        yml.write_text(yaml.dump({
            "quantization_recommendations": [
                {"min_params_b": 7, "max_vram_gb": 16, "recommendation": "q4_k_m"},
                {"min_params_b": 13, "min_vram_gb": 48, "recommendation": "fp16"},
            ],
        }))
        cfg = DeploymentOptimizationConfig.from_yaml(yml)
        assert cfg.recommend_quantization(7, 12) == "q4_k_m"
        assert cfg.recommend_quantization(13, 80) == "fp16"
        assert cfg.recommend_quantization(3, 4) is None


# ── model_routing ──────────────────────────────────────────────────────


class TestModelRoutingEndToEnd:
    def test_load_from_yaml_returns_defaults_when_file_absent(self, tmp_path):
        from general_ludd.config.model_routing import load_model_routing

        cfg = load_model_routing(tmp_path / "nonexistent.yml")
        assert cfg.default_profile is None
        assert cfg.fallback_chain == []

    def test_load_from_yaml_parses_full_config(self, tmp_path):
        from general_ludd.config.model_routing import load_model_routing

        yml = tmp_path / "routing.yml"
        yml.write_text(yaml.dump({
            "default_profile": "sonnet",
            "weak_model_profile": "haiku",
            "role_routing": {"coder": "sonnet"},
            "quality_routing": {"CriticalFixTask": "opus"},
            "fallback_chain": ["haiku", "sonnet", "opus"],
        }))
        cfg = load_model_routing(yml)
        assert cfg.default_profile == "sonnet"
        assert cfg.role_routing == {"coder": "sonnet"}
        assert cfg.quality_routing == {"CriticalFixTask": "opus"}
        assert cfg.fallback_chain == ["haiku", "sonnet", "opus"]

    def test_build_router_from_config_wires_role_and_quality(self):
        from general_ludd.config.model_routing import ModelRoutingConfig, build_router_from_config

        cfg = ModelRoutingConfig(
            default_profile="sonnet",
            role_routing={"coder": "sonnet", "reviewer": "opus"},
            quality_routing={"CriticalFixTask": "opus"},
        )
        router = build_router_from_config(cfg)
        assert router._mapping == {"coder": "sonnet", "reviewer": "opus"}
        assert router.default_profile_id == "sonnet"


# ── binary_paths ───────────────────────────────────────────────────────


class TestBinaryPathsEndToEnd:
    def test_default_binary_paths_have_expected_keys(self):
        from general_ludd.config.binary_paths import BinaryPaths

        bp = BinaryPaths()
        assert bp.terraform == "terraform"
        assert bp.ansible_playbook == "ansible-playbook"
        assert bp.git == "git"

    def test_resolver_uses_configured_absolute_path(self):
        from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths

        cfg = BinaryPaths(terraform="/custom/terraform")
        resolver = BinaryPathResolver(cfg)
        assert resolver.resolve("terraform") == "/custom/terraform"

    def test_resolver_falls_back_to_which_for_unconfigured(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        resolver = BinaryPathResolver()
        resolved = resolver.resolve("python3")
        assert "/" in resolved or resolved == "python3"

    def test_is_available_true_for_existing_binary(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        resolver = BinaryPathResolver()
        assert resolver.is_available("python3")

    def test_get_infra_binary_prefers_opentofu(self, monkeypatch):
        from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths

        monkeypatch.setattr("general_ludd.config.binary_paths.shutil.which", lambda x: "/usr/bin/tofu" if x == "tofu" else None)
        cfg = BinaryPaths(opentofu="tofu", terraform="terraform")
        resolver = BinaryPathResolver(cfg)
        assert resolver.get_infra_binary() == "tofu"

    def test_get_secrets_binary_prefers_openbao(self, monkeypatch):
        from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths

        monkeypatch.setattr("general_ludd.config.binary_paths.shutil.which", lambda x: "/usr/bin/bao" if x == "bao" else None)
        cfg = BinaryPaths(openbao="bao", vault="vault")
        resolver = BinaryPathResolver(cfg)
        assert resolver.get_secrets_binary() == "bao"


# ── project_dir ────────────────────────────────────────────────────────
# (read-only in most envs — uses workspace .gludd/ or GLUDD_PROJECT_DIR)


class TestProjectDirEndToEnd:
    def test_find_returns_workspace_gludd(self):
        from general_ludd.config.project_dir import find_project_gludd_dir

        result = find_project_gludd_dir()
        assert result is not None
        assert result.name == ".gludd"

    def test_find_returns_none_for_path_without_gludd(self):
        from general_ludd.config.project_dir import find_project_gludd_dir

        assert find_project_gludd_dir(Path("/")) is None

    def test_env_override_respects_gludd_project_dir(self, tmp_path, monkeypatch):
        from general_ludd.config.project_dir import find_project_gludd_dir

        gludd_dir = tmp_path / "custom-gludd"
        gludd_dir.mkdir()
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        result = find_project_gludd_dir()
        assert result == gludd_dir

    def test_env_override_returns_none_when_dir_absent(self, monkeypatch):
        from general_ludd.config.project_dir import find_project_gludd_dir

        monkeypatch.setenv("GLUDD_PROJECT_DIR", "/tmp/nonexistent-gludd-dir-e2e")
        assert find_project_gludd_dir() is None

    def test_project_config_path_returns_none_when_dir_is_none(self):
        from general_ludd.config.project_dir import project_config_path

        assert project_config_path(None) is None

    def test_validate_overlay_rejects_dangerous_fields(self):
        from general_ludd.config.project_dir import ProjectOverlayValidationError, validate_project_overlay

        with pytest.raises(ProjectOverlayValidationError, match="database"):
            validate_project_overlay({"database": {"url": "evil"}, "pipeline": {"enabled": True}})

    def test_validate_overlay_rejects_by_allowlist(self):
        from general_ludd.config.project_dir import ProjectOverlayValidationError, validate_project_overlay

        with pytest.raises(ProjectOverlayValidationError, match="model_profiles"):
            validate_project_overlay({"model_profiles": {"bad": "yes"}})

    def test_merge_config_project_overrides_user_scalar(self):
        from general_ludd.config.project_dir import merge_config

        user = {"timeout": 30, "retries": 3}
        project = {"timeout": 60}
        result = merge_config(user, project)
        assert result["timeout"] == 60
        assert result["retries"] == 3

    def test_merge_config_project_list_replaces_user_list(self):
        from general_ludd.config.project_dir import merge_config

        user = {"rules": [{"name": "a"}, {"name": "b"}]}
        project = {"rules": [{"name": "c"}]}
        result = merge_config(user, project)
        assert result["rules"] == [{"name": "c"}]

    def test_merge_config_deep_merges_nested_dicts(self):
        from general_ludd.config.project_dir import merge_config

        user = {"pipeline": {"enabled": False, "floor": 1}}
        project = {"pipeline": {"enabled": True}}
        result = merge_config(user, project)
        assert result["pipeline"]["enabled"] is True
        assert result["pipeline"]["floor"] == 1
