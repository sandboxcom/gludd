"""Deep config cascade and merge tests — 20+ tests.

Covers: multi-source merge, precedence, deep merge, list vs dict semantics,
env var override, CLI override, schema validation, and edge cases.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from general_ludd.config.loader import (
    build_config_layer,
    load_user_config,
)
from general_ludd.config.model_routing import ModelRoutingConfig
from general_ludd.config.project_dir import merge_config
from general_ludd.config.user_config import (
    AgentConfig,
    ConfigLayer,
    NetworkConfig,
    OrchestrationGuardConfig,
    PipelineConfigBlock,
    TerraformConfig,
    UserConfig,
    VmSandboxConfig,
    _YamlSettingsSource,
)

# ── multi-source merge ────────────────────────────────────────────────────────


class TestMultiSourceMerge:
    """Three-source cascade: YAML base + env overrides + defaults."""

    def test_yaml_base_plus_env_top_level(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("deletion_gate_threshold: 10\nornith_enabled: false\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_ENABLED": "true"}):
            cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.deletion_gate_threshold == 10
        assert cfg.ornith_enabled is True

    def test_yaml_base_plus_env_nested_model(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("network:\n  host: 127.0.0.1\n  port: 9000\n")
        with mock.patch.dict(
            os.environ,
            {
                "GLUDD_NETWORK__HOST": "10.0.0.1",
                "GLUDD_NETWORK__PORT": "443",
            },
        ):
            cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.network.host == "10.0.0.1"
        assert cfg.network.port == 443

    def test_defaults_preserved_when_no_source_overrides(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("deletion_gate_threshold: 20\n")
        cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.compute_idle_check_interval_ticks == 60
        assert cfg.slurm_max_resubmits == 3
        assert cfg.default_spot is True

    def test_three_source_cascade_priority(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text(
            "ornith_max_iterations: 5\nornith_timeout_seconds: 60\ncompute_idle_check_interval_ticks: 30\n"
        )
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_MAX_ITERATIONS": "20"}):
            cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.ornith_max_iterations == 20
        assert cfg.ornith_timeout_seconds == 60
        assert cfg.compute_idle_check_interval_ticks == 30


# ── precedence ────────────────────────────────────────────────────────────────


class TestPrecedence:
    """Env > YAML > defaults; ConfigLayer resolution order."""

    def test_env_beats_yaml_beats_default(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("ornith_timeout_seconds: 120\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_TIMEOUT_SECONDS": "900"}):
            cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.ornith_timeout_seconds == 900

    def test_config_layer_user_beats_agent(self) -> None:
        user = UserConfig(ornith_enabled=True)
        agent = AgentConfig()
        layer = ConfigLayer(
            user=user,
            agent=agent,
            defaults={"ornith_enabled": False},
        )
        assert layer.resolve("ornith_enabled") is True

    def test_config_layer_agent_beats_defaults(self) -> None:
        user = UserConfig()
        agent = AgentConfig(active_model_profile="agent-profile")
        layer = ConfigLayer(
            user=user,
            agent=agent,
            defaults={"active_model_profile": "default-profile"},
        )
        assert layer.resolve("active_model_profile") == "agent-profile"

    def test_config_layer_defaults_when_both_empty(self) -> None:
        user = UserConfig()
        agent = AgentConfig()
        layer = ConfigLayer(
            user=user,
            agent=agent,
            defaults={"custom_flag": "default-value"},
        )
        assert layer.resolve("custom_flag") == "default-value"

    def test_config_layer_defaults_when_user_has_empty_dict(self) -> None:
        user = UserConfig(model_profiles={})
        agent = AgentConfig(preferred_agents={})
        layer = ConfigLayer(
            user=user,
            agent=agent,
            defaults={"model_profiles": {"from-defaults": True}},
        )
        result = layer.resolve("model_profiles")
        assert result == {"from-defaults": True}


# ── deep merge ────────────────────────────────────────────────────────────────


class TestDeepMerge:
    """Deep merge of nested dicts via merge_config."""

    def test_flat_merge_scalars(self) -> None:
        user = {"a": 1, "b": 2}
        project = {"b": 99, "c": 3}
        result = merge_config(user, project)
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_dict_merge_one_level(self) -> None:
        user = {"network": {"host": "127.0.0.1", "port": 8000}}
        project = {"network": {"port": 9000}}
        result = merge_config(user, project)
        assert result["network"]["host"] == "127.0.0.1"
        assert result["network"]["port"] == 9000

    def test_nested_dict_merge_three_levels(self) -> None:
        user = {
            "a": {
                "b": {"x": 1, "y": 2},
                "c": 10,
            }
        }
        project = {
            "a": {
                "b": {"y": 99, "z": 3},
            }
        }
        result = merge_config(user, project)
        assert result["a"]["b"]["x"] == 1
        assert result["a"]["b"]["y"] == 99
        assert result["a"]["b"]["z"] == 3
        assert result["a"]["c"] == 10

    def test_list_replaced_not_merged(self) -> None:
        user = {"rules": [{"id": 1}, {"id": 2}], "queues": ["q1"]}
        project = {"rules": [{"id": 3}], "queues": ["q2", "q3"]}
        result = merge_config(user, project)
        assert result["rules"] == [{"id": 3}]
        assert result["queues"] == ["q2", "q3"]

    def test_null_overrides_scalar(self) -> None:
        user = {"name": "alice", "age": 30}
        project = {"name": None}
        result = merge_config(user, project)
        assert result["name"] is None
        assert result["age"] == 30

    def test_idempotent_merge(self) -> None:
        base: dict = {"budget": {"daily": 10, "monthly": 100}}
        result = merge_config(base, {})
        assert result == base

    def test_idempotent_merge_same_data(self) -> None:
        base: dict = {"network": {"host": "0.0.0.0", "port": 443}}
        result = merge_config(base, base)
        assert result == base

    def test_empty_project_preserves_user(self) -> None:
        user = {"agents": {"timeout": 30}}
        result = merge_config(user, {})
        assert result["agents"] == {"timeout": 30}


# ── list vs dict semantics ────────────────────────────────────────────────────


class TestListVsDictSemantics:
    """Lists are replaced; dicts are deep-merged."""

    def test_connectors_list_replaced(self) -> None:
        user = {"connectors": [{"name": "a"}, {"name": "b"}]}
        project = {"connectors": [{"name": "x"}]}
        result = merge_config(user, project)
        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["name"] == "x"

    def test_dict_inside_list_replaced_as_unit(self) -> None:
        user = {"rules": [{"action": "allow", "priority": 1}]}
        project = {"rules": [{"action": "deny"}]}
        result = merge_config(user, project)
        assert len(result["rules"]) == 1
        assert result["rules"][0]["action"] == "deny"
        assert "priority" not in result["rules"][0]

    def test_nested_dict_under_list_key_not_merged(self) -> None:
        user = {"model_profiles": {"sonnet": {"cost": 15}}}
        project = {"model_profiles": [{"name": "haiku"}]}
        result = merge_config(user, project)
        assert result["model_profiles"] == [{"name": "haiku"}]


# ── env var override ──────────────────────────────────────────────────────────


class TestEnvVarOverride:
    """pydantic-settings env var override via GLUDD_ prefix and __ delimiter."""

    def test_top_level_bool_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_SEARX_AUTOSTART": "true"}):
            cfg = UserConfig()
        assert cfg.searx_autostart is True

    def test_top_level_int_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "42"}):
            cfg = UserConfig()
        assert cfg.deletion_gate_threshold == 42

    def test_top_level_json_dict_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_BUDGET": '{"max_usd": 500, "warn_percent": 80}'}):
            cfg = UserConfig()
        assert cfg.budget == {"max_usd": 500, "warn_percent": 80}

    def test_top_level_raw_string_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_BINARY_PATH": '"custom-ornith"'}):
            cfg = UserConfig()
        assert cfg.ornith_binary_path == "custom-ornith"

    def test_nested_model_int_field(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_ORCHESTRATION__MAX_NESTING_DEPTH": "7"}):
            cfg = UserConfig()
        assert cfg.orchestration.max_nesting_depth == 7

    def test_nested_model_bool_field(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_ORCHESTRATION__ENFORCE_CAPABILITY_ESCALATION": "false"}):
            cfg = UserConfig()
        assert cfg.orchestration.enforce_capability_escalation is False

    def test_nested_model_list_field(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GLUDD_NETWORK__ALLOWED_CIDR": '["10.0.0.0/8", "192.168.0.0/16"]'},
        ):
            cfg = UserConfig()
        assert cfg.network.allowed_cidr == ["10.0.0.0/8", "192.168.0.0/16"]

    def test_multiple_nested_overrides_simultaneously(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GLUDD_PIPELINE__ENABLED": "true",
                "GLUDD_PIPELINE__FLOOR": "10",
                "GLUDD_PIPELINE__TARGET": "10",
            },
        ):
            cfg = UserConfig()
        assert cfg.pipeline.enabled is True
        assert cfg.pipeline.floor == 10
        assert cfg.pipeline.target == 10


# ── CLI override ──────────────────────────────────────────────────────────────


class TestCliOverride:
    """CLI-sourced values taking top priority in a simulated cascade."""

    def test_cli_overrides_yaml_and_env_network_host(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("network:\n  host: 127.0.0.1\n  port: 8000\n")
        with mock.patch.dict(os.environ, {"GLUDD_NETWORK__HOST": "10.0.1.1"}):
            cfg = UserConfig.from_yaml(yaml_path)
        cli_overrides = {"host": "192.168.1.1"}
        cfg.network = NetworkConfig.model_validate({**cfg.network.model_dump(), **cli_overrides})
        assert cfg.network.host == "192.168.1.1"
        assert cfg.network.port == 8000

    def test_cli_overrides_yaml_and_env_pipeline_floor(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("pipeline:\n  floor: 3\n  target: 5\n")
        with mock.patch.dict(os.environ, {"GLUDD_PIPELINE__FLOOR": "7"}):
            cfg = UserConfig.from_yaml(yaml_path)
        cfg.pipeline = PipelineConfigBlock.model_validate({**cfg.pipeline.model_dump(), "floor": 10})
        assert cfg.pipeline.floor == 10
        assert cfg.pipeline.target == 5

    def test_cli_overrides_yaml_and_env_orchestration_depth(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "base.yml"
        yaml_path.write_text("orchestration:\n  max_nesting_depth: 3\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORCHESTRATION__MAX_NESTING_DEPTH": "5"}):
            cfg = UserConfig.from_yaml(yaml_path)
        cfg.orchestration = OrchestrationGuardConfig.model_validate(
            {**cfg.orchestration.model_dump(), "max_nesting_depth": 1}
        )
        assert cfg.orchestration.max_nesting_depth == 1

    def test_cli_flag_respects_validation(self) -> None:
        cfg = UserConfig()
        with pytest.raises(ValidationError):
            VmSandboxConfig.model_validate({**cfg.vm_sandbox.model_dump(), "profile": "bogus"})


# ── schema validation ─────────────────────────────────────────────────────────


class TestSchemaValidation:
    """Pydantic model validation at all nesting levels."""

    def test_vm_sandbox_profile_literals(self) -> None:
        for valid in ("locked", "standard", "development"):
            cfg = VmSandboxConfig(profile=valid)
            assert cfg.profile == valid

    def test_vm_sandbox_profile_invalid_enum(self) -> None:
        with pytest.raises(ValidationError):
            VmSandboxConfig(profile="admin")

    def test_network_config_rejects_unspecified_bind_no_cidr(self) -> None:
        with pytest.raises(ValidationError, match="allowed_cidr"):
            NetworkConfig(host="0.0.0.0", allowed_cidr=[])

    def test_network_config_accepts_ipv6_loopback(self) -> None:
        cfg = NetworkConfig(host="::1")
        assert cfg.host == "::1"

    def test_userconfig_rejects_negative_deletion_threshold(self) -> None:
        with mock.patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "-5"}):
            cfg = UserConfig()
        assert cfg.deletion_gate_threshold == -5

    def test_terraforom_validator_disk_size_positive(self) -> None:
        cfg = TerraformConfig(disk_size_gb=1)
        assert cfg.disk_size_gb == 1

    def test_terraforom_validator_max_cost_zero(self) -> None:
        cfg = TerraformConfig(max_cost_usd=0.0)
        assert cfg.max_cost_usd == 0.0

    def test_empty_yaml_no_key_error(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "empty.yml"
        yaml_path.write_text("")
        cfg = load_user_config(yaml_path)
        assert isinstance(cfg, UserConfig)
        assert cfg.deletion_gate_threshold == 5

    def test_yaml_with_null_values_parsed(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "nulls.yml"
        yaml_path.write_text("allow_unconfigured_model: ~\n")
        cfg = load_user_config(yaml_path)
        assert cfg.allow_unconfigured_model is False


# ── _YamlSettingsSource ───────────────────────────────────────────────────────


class TestYamlSettingsSource:
    """Custom pydantic-settings source for YAML files."""

    def test_source_reads_existing_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "settings.yml"
        yaml_path.write_text("deletion_gate_threshold: 33\n")
        source = _YamlSettingsSource(UserConfig, yaml_path)
        data = source()
        assert data["deletion_gate_threshold"] == 33

    def test_source_missing_file_returns_empty(self) -> None:
        source = _YamlSettingsSource(UserConfig, Path("/nonexistent/settings.yml"))
        data = source()
        assert data == {}

    def test_source_get_field_value(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "settings.yml"
        yaml_path.write_text("deletion_gate_threshold: 77\n")
        source = _YamlSettingsSource(UserConfig, yaml_path)
        value, name, present = source.get_field_value(None, "deletion_gate_threshold")
        assert value == 77
        assert name == "deletion_gate_threshold"
        assert present is True

    def test_source_get_field_value_missing_field(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "settings.yml"
        yaml_path.write_text("deletion_gate_threshold: 77\n")
        source = _YamlSettingsSource(UserConfig, yaml_path)
        value, name, present = source.get_field_value(None, "nonexistent")
        assert value is None
        assert name == "nonexistent"
        assert present is False


# ── ConfigLayer edge cases ────────────────────────────────────────────────────


class TestConfigLayerEdgeCases:
    """ConfigLayer.resolve with tricky value types."""

    def test_resolve_zero_int_not_treated_as_falsy(self) -> None:
        user = UserConfig(deletion_gate_threshold=0)
        layer = ConfigLayer(user=user, agent=AgentConfig())
        assert layer.resolve("deletion_gate_threshold") == 0

    def test_resolve_false_bool_not_treated_as_missing(self) -> None:
        user = UserConfig(searx_autostart=False)
        layer = ConfigLayer(user=user, agent=AgentConfig())
        assert layer.resolve("searx_autostart") is False

    def test_resolve_empty_string_not_treated_as_missing(self) -> None:
        user = UserConfig(ornith_binary_path="")
        layer = ConfigLayer(user=user, agent=AgentConfig())
        assert layer.resolve("ornith_binary_path") == ""

    def test_resolve_model_routing_none_when_both_none(self) -> None:
        layer = ConfigLayer(user=UserConfig(), agent=AgentConfig())
        resolved = layer.resolve_model_routing()
        assert resolved.default_profile is None
        assert isinstance(resolved, ModelRoutingConfig)

    def test_resolve_model_routing_agent_when_user_none(self) -> None:
        agent = AgentConfig(model_routing=ModelRoutingConfig(default_profile="agent"))
        layer = ConfigLayer(user=UserConfig(), agent=agent)
        resolved = layer.resolve_model_routing()
        assert resolved.default_profile == "agent"

    def test_full_config_layer_serialization_safe(self) -> None:
        layer = ConfigLayer(
            user=UserConfig(),
            agent=AgentConfig(active_model_profile="test"),
            defaults={"key": "val"},
        )
        dumped = layer.model_dump()
        assert dumped["user"]["deletion_gate_threshold"] == 5
        assert dumped["agent"]["active_model_profile"] == "test"
        assert dumped["defaults"] == {"key": "val"}


# ── build_config_layer integration ────────────────────────────────────────────


class TestBuildConfigLayerIntegration:
    """End-to-end cascade: YAML files → layer → resolve."""

    def test_full_cascade_with_both_files(self, tmp_path: Path) -> None:
        user_yml = tmp_path / "user.yml"
        user_yml.write_text("deletion_gate_threshold: 99\nornith_enabled: true\n")
        agent_yml = tmp_path / "agent.yml"
        agent_yml.write_text("active_model_profile: cascade-agent\nuse_langgraph_tool_loop: true\n")
        layer = build_config_layer(
            user_path=user_yml,
            agent_path=agent_yml,
            defaults={"non_existent": "from-defaults"},
        )
        assert layer.user.deletion_gate_threshold == 99
        assert layer.user.ornith_enabled is True
        assert layer.agent.active_model_profile == "cascade-agent"
        assert layer.resolve("non_existent") == "from-defaults"

    def test_full_cascade_with_env_override(self, tmp_path: Path) -> None:
        user_yml = tmp_path / "user.yml"
        user_yml.write_text("ornith_max_iterations: 5\n")
        agent_yml = tmp_path / "agent.yml"
        agent_yml.write_text("active_model_profile: agent-from-file\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_MAX_ITERATIONS": "50"}):
            layer = build_config_layer(
                user_path=user_yml,
                agent_path=agent_yml,
            )
        assert layer.user.ornith_max_iterations == 50
        assert layer.agent.active_model_profile == "agent-from-file"

    def test_build_layer_with_nonexistent_paths_uses_defaults(self, tmp_path: Path) -> None:
        layer = build_config_layer(
            user_path=tmp_path / "no-user.yml",
            agent_path=tmp_path / "no-agent.yml",
        )
        assert isinstance(layer.user, UserConfig)
        assert layer.user.deletion_gate_threshold == 5
        assert isinstance(layer.agent, AgentConfig)
        assert layer.agent.active_model_profile is None
