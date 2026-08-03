"""Deep configuration management tests.

Covers: env var override precedence, YAML file loading, validation schema,
default fallbacks, ConfigLayer resolution, secret masking, binary path resolution,
model routing config loading, and agent config persistence.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.config.loader import (
    build_config_layer,
    load_agent_config,
    load_user_config,
    save_agent_config,
)
from general_ludd.config.model_routing import (
    ModelRoutingConfig,
    load_model_routing,
)
from general_ludd.config.user_config import (
    AgentConfig,
    CompactionConfigBlock,
    ConfigLayer,
    HumanInTheLoopConfig,
    IssuesConfig,
    NetworkConfig,
    NotificationsConfig,
    ObservabilityConfig,
    OrchestrationGuardConfig,
    PipelineConfigBlock,
    RelationshipRoutingConfig,
    RemediationSettings,
    TerraformConfig,
    UserConfig,
    VmSandboxConfig,
)

# ── Env var override precedence ──────────────────────────────────────────────


class TestEnvVarOverridePrecedence:
    """GLUDD_<FIELD> env vars override YAML file values."""

    def test_env_var_overrides_yaml_top_level_int(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("deletion_gate_threshold: 42\n")
        with mock.patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "99"}):
            cfg = UserConfig.from_yaml(yaml_path)
            assert cfg.deletion_gate_threshold == 99

    def test_env_var_overrides_yaml_bool(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("allow_unconfigured_model: false\n")
        with mock.patch.dict(os.environ, {"GLUDD_ALLOW_UNCONFIGURED_MODEL": "true"}):
            cfg = UserConfig.from_yaml(yaml_path)
            assert cfg.allow_unconfigured_model is True

    def test_env_var_overrides_yaml_string(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("ornith_binary_path: /usr/bin/fake\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_BINARY_PATH": '"real_path"'}):
            cfg = UserConfig.from_yaml(yaml_path)
            assert cfg.ornith_binary_path == "real_path"

    def test_env_var_overrides_yaml_dict(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("budget:\n  daily: 10\n  monthly: 100\n")
        with mock.patch.dict(os.environ, {"GLUDD_BUDGET": '{"daily": 50}'}):
            cfg = UserConfig.from_yaml(yaml_path)
            assert cfg.budget == {"daily": 50}

    def test_env_var_not_set_falls_back_to_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("deletion_gate_threshold: 77\n")
        cfg = UserConfig.from_yaml(yaml_path)
        assert cfg.deletion_gate_threshold == 77

    def test_env_var_not_set_falls_back_to_default(self) -> None:
        cfg = UserConfig()
        assert cfg.deletion_gate_threshold == 5

    def test_invalid_json_env_var_falls_back_to_string(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("ornith_binary_path: original\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_BINARY_PATH": "not_json"}):
            cfg = UserConfig.from_yaml(yaml_path)
            assert cfg.ornith_binary_path == "not_json"

    def test_env_nested_delimiter(self) -> None:
        """GLUDD_NETWORK__HOST + GLUDD_NETWORK__ALLOWED_CIDR override network fields."""
        with mock.patch.dict(
            os.environ,
            {
                "GLUDD_NETWORK__HOST": "0.0.0.0",
                "GLUDD_NETWORK__ALLOWED_CIDR": '["10.0.0.0/8"]',
            },
        ):
            cfg = UserConfig()
            assert cfg.network.host == "0.0.0.0"
            assert cfg.network.allowed_cidr == ["10.0.0.0/8"]


# ── YAML/JSON file loading ───────────────────────────────────────────────────


class TestYamlFileLoading:
    """load_user_config, load_agent_config, and from_yaml loading behaviour."""

    def test_load_user_config_from_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("deletion_gate_threshold: 11\nsearx_autostart: true\n")
        cfg = load_user_config(yaml_path)
        assert cfg.deletion_gate_threshold == 11
        assert cfg.searx_autostart is True

    def test_load_user_config_nonexistent_file_returns_defaults(self) -> None:
        cfg = load_user_config(Path("/nonexistent/path/user.yml"))
        assert cfg.deletion_gate_threshold == 5

    def test_load_user_config_empty_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "user.yml"
        yaml_path.write_text("")
        cfg = load_user_config(yaml_path)
        assert cfg.deletion_gate_threshold == 5

    def test_load_agent_config_from_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "agent.yml"
        yaml_path.write_text("active_model_profile: test-profile\nsession_notes: hello\n")
        cfg = load_agent_config(yaml_path)
        assert cfg.active_model_profile == "test-profile"
        assert cfg.session_notes == "hello"

    def test_load_agent_config_nonexistent_returns_defaults(self) -> None:
        cfg = load_agent_config(Path("/nonexistent/agent.yml"))
        assert cfg.active_model_profile is None
        assert cfg.session_notes == ""

    def test_load_agent_config_empty_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "agent.yml"
        yaml_path.write_text("")
        cfg = load_agent_config(yaml_path)
        assert isinstance(cfg, AgentConfig)
        assert cfg.active_model_profile is None

    def test_save_agent_config_roundtrip(self, tmp_path: Path) -> None:
        cfg = AgentConfig(
            active_model_profile="roundtrip",
            session_notes="persist me",
            bind_tools_on_dispatch=False,
        )
        yaml_path = tmp_path / "agent.yml"
        save_agent_config(cfg, yaml_path)
        loaded = load_agent_config(yaml_path)
        assert loaded.active_model_profile == "roundtrip"
        assert loaded.session_notes == "persist me"
        assert loaded.bind_tools_on_dispatch is False

    def test_save_agent_config_creates_parent_dir(self, tmp_path: Path) -> None:
        cfg = AgentConfig(active_model_profile="nested")
        yaml_path = tmp_path / "deeply" / "nested" / "agent.yml"
        save_agent_config(cfg, yaml_path)
        assert yaml_path.exists()
        loaded = load_agent_config(yaml_path)
        assert loaded.active_model_profile == "nested"


# ── Validation schema ────────────────────────────────────────────────────────


class TestValidationSchema:
    """Model validators and field type enforcement."""

    def test_network_config_unspecified_bind_without_cidr_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            NetworkConfig(host="0.0.0.0", allowed_cidr=[])
        assert "allowed_cidr" in str(exc_info.value)

    def test_network_config_unspecified_bind_with_cidr_passes(self) -> None:
        cfg = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
        assert cfg.is_unspecified_bind is True
        assert cfg.allowed_cidr == ["10.0.0.0/8"]

    def test_network_config_loopback_is_not_external(self) -> None:
        cfg = NetworkConfig(host="127.0.0.1")
        assert cfg.is_external_bind is False

    def test_network_config_public_is_external(self) -> None:
        cfg = NetworkConfig(host="192.168.1.1")
        assert cfg.is_external_bind is True

    def test_network_config_localhost_is_not_external(self) -> None:
        cfg = NetworkConfig(host="localhost")
        assert cfg.is_external_bind is False

    def test_compaction_config_defaults(self) -> None:
        cfg = CompactionConfigBlock()
        assert cfg.enabled is False
        assert cfg.level == 1

    def test_pipeline_config_defaults(self) -> None:
        cfg = PipelineConfigBlock()
        assert cfg.enabled is False
        assert cfg.floor == 1
        assert cfg.target == 3
        assert cfg.gate_debounce_s == 30.0

    def test_remediation_settings_defaults(self) -> None:
        cfg = RemediationSettings()
        assert cfg.check_interval_ticks == 30
        assert cfg.max_actions_per_tick == 5
        assert cfg.human_input_block_hours == 24
        assert cfg.min_chronic_incidents == 5

    def test_orchestration_guard_defaults(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_nesting_depth == 3
        assert cfg.max_redispatch_count == 5
        assert cfg.enforce_capability_escalation is True


# ── Default fallbacks ─────────────────────────────────────────────────────────


class TestDefaultFallbacks:
    """Every UserConfig field has a safe default."""

    def test_bare_user_config_creates_with_defaults(self) -> None:
        cfg = UserConfig()
        assert isinstance(cfg.network, NetworkConfig)
        assert cfg.network.host == "127.0.0.1"
        assert cfg.network.port == 8000
        assert isinstance(cfg.observability, ObservabilityConfig)
        assert cfg.observability.service_name == "general-ludd"
        assert cfg.searx_autostart is False
        assert cfg.allow_unconfigured_model is False
        assert cfg.ornith_enabled is False

    def test_binary_path_resolver_defaults(self) -> None:
        resolver = BinaryPathResolver()
        assert resolver._config.terraform == "terraform"
        assert resolver._config.git == "git"
        assert resolver._config.uv == "uv"

    def test_binary_path_resolver_custom_config(self) -> None:
        cfg = BinaryPaths(terraform="/custom/terraform")
        resolver = BinaryPathResolver(cfg)
        assert resolver._config.terraform == "/custom/terraform"

    def test_binary_path_resolver_unknown_attribute(self) -> None:
        resolver = BinaryPathResolver()
        result = resolver.resolve("nonexistent_binary")
        assert result is not None

    def test_model_routing_config_defaults(self) -> None:
        cfg = ModelRoutingConfig()
        assert cfg.default_profile is None
        assert cfg.role_routing == {}
        assert cfg.fallback_chain == []

    def test_vm_sandbox_config_defaults(self) -> None:
        cfg = VmSandboxConfig()
        assert cfg.enabled is False
        assert cfg.profile == "locked"
        assert cfg.image_type == "firecracker"
        assert cfg.vcpu_count == 1
        assert cfg.mem_mib == 512

    def test_terraforom_config_defaults(self) -> None:
        cfg = TerraformConfig()
        assert cfg.region == "us-east-1"
        assert cfg.gpu_count == 1
        assert cfg.max_cost_usd == 10.0
        assert cfg.enable_structured_outputs is True

    def test_human_in_the_loop_config_defaults(self) -> None:
        cfg = HumanInTheLoopConfig()
        assert cfg.enabled is False
        assert cfg.confidence_threshold == 0.7


# ── ConfigLayer resolution ────────────────────────────────────────────────────


class TestConfigLayerResolution:
    """ConfigLayer.resolve priority: user > agent > defaults."""

    def test_resolve_prefers_user(self) -> None:
        user = UserConfig(allow_unconfigured_model=True)
        agent = AgentConfig()
        layer = ConfigLayer(user=user, agent=agent, defaults={"allow_unconfigured_model": False})
        assert layer.resolve("allow_unconfigured_model") is True

    def test_resolve_falls_back_to_agent_for_dict(self) -> None:
        user = UserConfig(model_profiles={})
        agent = AgentConfig(preferred_agents={"agent_a": {"model": "sonnet"}})
        layer = ConfigLayer(user=user, agent=agent)
        result = layer.resolve("preferred_agents")
        assert result == {"agent_a": {"model": "sonnet"}}

    def test_resolve_falls_back_to_defaults(self) -> None:
        user = UserConfig(model_profiles={})
        agent = AgentConfig()
        layer = ConfigLayer(user=user, agent=agent, defaults={"non_existent": "fallback"})
        assert layer.resolve("non_existent") == "fallback"

    def test_resolve_returns_none_for_missing_key(self) -> None:
        user = UserConfig()
        agent = AgentConfig()
        layer = ConfigLayer(user=user, agent=agent)
        assert layer.resolve("nonexistent_field") is None

    def test_resolve_non_dict_user_val_returns_directly(self) -> None:
        user = UserConfig(ornith_enabled=True)
        agent = AgentConfig()
        layer = ConfigLayer(user=user, agent=agent)
        assert layer.resolve("ornith_enabled") is True

    def test_resolve_model_routing_user_priority(self) -> None:
        user_cfg = UserConfig(model_routing=ModelRoutingConfig(default_profile="user-default"))
        agent_cfg = AgentConfig(model_routing=ModelRoutingConfig(default_profile="agent-default"))
        layer = ConfigLayer(user=user_cfg, agent=agent_cfg)
        resolved = layer.resolve_model_routing()
        assert resolved.default_profile == "user-default"

    def test_resolve_model_routing_falls_back_to_agent(self) -> None:
        agent_cfg = AgentConfig(model_routing=ModelRoutingConfig(default_profile="agent-default"))
        layer = ConfigLayer(user=UserConfig(), agent=agent_cfg)
        resolved = layer.resolve_model_routing()
        assert resolved.default_profile == "agent-default"

    def test_resolve_model_routing_returns_default_when_both_none(self) -> None:
        layer = ConfigLayer(user=UserConfig(), agent=AgentConfig())
        resolved = layer.resolve_model_routing()
        assert isinstance(resolved, ModelRoutingConfig)
        assert resolved.default_profile is None


# ── Load model routing config ─────────────────────────────────────────────────


class TestModelRoutingConfigLoading:
    """load_model_routing from YAML files."""

    def test_load_model_routing_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "routing.yml"
        yaml_path.write_text(
            "default_profile: sonnet\nrole_routing:\n  code_review: opus\nfallback_chain:\n  - haiku\n"
        )
        cfg = load_model_routing(yaml_path)
        assert cfg.default_profile == "sonnet"
        assert cfg.role_routing == {"code_review": "opus"}
        assert cfg.fallback_chain == ["haiku"]

    def test_load_model_routing_missing_file(self, tmp_path: Path) -> None:
        cfg = load_model_routing(tmp_path / "nonexistent.yml")
        assert cfg.default_profile is None
        assert cfg.role_routing == {}

    def test_load_model_routing_empty_file(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "routing.yml"
        yaml_path.write_text("")
        cfg = load_model_routing(yaml_path)
        assert isinstance(cfg, ModelRoutingConfig)


# ── Secret-like data in config context ────────────────────────────────────────


class TestSecretHandlingInConfig:
    """Secret and sensitive-value handling through config paths."""

    def test_user_config_serialization_does_not_leak_sensitive_defaults(self) -> None:
        """Default config serialization is safe for logging."""
        cfg = UserConfig()
        dumped = cfg.model_dump()
        assert "secret" not in str(dumped).lower()

    def test_agent_config_round_trip_preserves_structure(self, tmp_path: Path) -> None:
        cfg = AgentConfig(
            active_model_profile="safe-profile",
            task_preferences={"model": "sonnet"},
            use_langgraph_tool_loop=True,
        )
        yaml_path = tmp_path / "agent.yml"
        save_agent_config(cfg, yaml_path)
        with open(yaml_path) as f:
            raw = f.read()
        assert "active_model_profile" in raw
        assert "safe-profile" in raw

    def test_notifications_config_default_is_safe(self) -> None:
        cfg = NotificationsConfig()
        assert cfg.enabled is False
        assert cfg.backends == {"stdout": {}}

    def test_issues_config_default_is_safe(self) -> None:
        cfg = IssuesConfig()
        assert cfg.polling_enabled is False
        assert cfg.github_owner == ""
        assert cfg.github_repo == ""

    def test_config_string_repr_does_not_leak_internals(self) -> None:
        cfg = NetworkConfig(host="10.0.0.1", port=9999, allowed_cidr=["10.0.0.0/8"])
        dumped = cfg.model_dump()
        assert dumped["host"] == "10.0.0.1"
        assert dumped["port"] == 9999

    def test_relationship_routing_null_by_default(self) -> None:
        cfg = UserConfig()
        assert cfg.relationship_routing is None

    def test_relationship_routing_when_set(self) -> None:
        rr = RelationshipRoutingConfig(
            enable_cross_project_borrowing=True,
            edge_decay=0.3,
            min_borrow_weight=0.01,
        )
        cfg = UserConfig(relationship_routing=rr)
        assert cfg.relationship_routing is not None
        assert cfg.relationship_routing.enable_cross_project_borrowing is True
        assert cfg.relationship_routing.edge_decay == 0.3


# ── Config export / build_config_layer integration ────────────────────────────


class TestBuildConfigLayer:
    """Integration of load_user_config + load_agent_config into ConfigLayer."""

    def test_build_config_layer_with_files(self, tmp_path: Path) -> None:
        user_yml = tmp_path / "user.yml"
        user_yml.write_text("deletion_gate_threshold: 55\n")
        agent_yml = tmp_path / "agent.yml"
        agent_yml.write_text("active_model_profile: from-build\n")
        layer = build_config_layer(user_path=user_yml, agent_path=agent_yml)
        assert layer.user.deletion_gate_threshold == 55
        assert layer.agent.active_model_profile == "from-build"

    def test_build_config_layer_with_defaults(self, tmp_path: Path) -> None:
        layer = build_config_layer(
            user_path=tmp_path / "nonexistent.yml",
            agent_path=tmp_path / "nonexistent.yml",
            defaults={"custom_key": "custom_value"},
        )
        assert layer.resolve("custom_key") == "custom_value"
        assert layer.defaults == {"custom_key": "custom_value"}

    def test_build_config_layer_all_defaults(self) -> None:
        layer = build_config_layer()
        assert isinstance(layer, ConfigLayer)
        assert isinstance(layer.user, UserConfig)
        assert isinstance(layer.agent, AgentConfig)


# ── Generic UserConfig special cases ──────────────────────────────────────────


class TestUserConfigEdgeCases:
    """Edge cases in UserConfig construction and behaviour."""

    def test_userconfig_extra_fields_ignored(self) -> None:
        cfg = UserConfig.model_validate({"deletion_gate_threshold": 7, "bogus_field": True})
        assert cfg.deletion_gate_threshold == 7

    def test_userconfig_slurm_fields(self) -> None:
        cfg = UserConfig()
        assert cfg.slurm_max_resubmits == 3
        assert cfg.slurm_preemption_backoff_schedule == [30, 60, 120]

    def test_ornith_fields(self) -> None:
        cfg = UserConfig()
        assert cfg.ornith_max_iterations == 10
        assert cfg.ornith_timeout_seconds == 300

    def test_checkpointing_default(self) -> None:
        cfg = UserConfig()
        assert cfg.checkpointing == {"enabled": False}

    def test_compute_idle_defaults(self) -> None:
        cfg = UserConfig()
        assert cfg.compute_idle_check_interval_ticks == 60
        assert cfg.compute_idle_teardown_threshold_ticks == 3
        assert cfg.compute_idle_gpu_sm_pct == 5.0

    def test_default_spot(self) -> None:
        cfg = UserConfig()
        assert cfg.default_spot is True
