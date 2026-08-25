"""Deep edge-case tests for src/general_ludd/config/loader.py.

Covers: load_agent_config, save_agent_config, build_config_layer, load_user_config —
edge cases not covered by test_config_cascade_deep.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from general_ludd.config.loader import (
    build_config_layer,
    load_agent_config,
    load_user_config,
    save_agent_config,
)
from general_ludd.config.user_config import AgentConfig, ConfigLayer, UserConfig

# ── load_agent_config edge cases ────────────────────────────────────────────────


class TestLoadAgentConfig:
    def test_returns_defaults_when_file_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent" / "agent_config.yml"
        cfg = load_agent_config(path)
        assert isinstance(cfg, AgentConfig)
        assert cfg.active_model_profile is None
        assert cfg.session_notes == ""

    def test_returns_defaults_when_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        cfg = load_agent_config(path)
        assert isinstance(cfg, AgentConfig)

    def test_returns_defaults_when_yaml_is_all_null(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("~")
        cfg = load_agent_config(path)
        assert isinstance(cfg, AgentConfig)

    def test_loads_partial_fields(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("session_notes: 'hello world'\nuse_langgraph_tool_loop: true\n")
        cfg = load_agent_config(path)
        assert cfg.session_notes == "hello world"
        assert cfg.use_langgraph_tool_loop is True
        assert cfg.active_model_profile is None

    def test_ignores_unknown_fields(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("session_notes: 'test'\ninjected_field: 'should_be_ignored'\n")
        cfg = load_agent_config(path)
        assert cfg.session_notes == "test"

    def test_empty_dict_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        cfg = load_agent_config(path)
        assert isinstance(cfg, AgentConfig)

    def test_list_yaml_raises_typeerror(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- item1\n- item2\n")
        try:
            load_agent_config(path)
            raise AssertionError("Expected TypeError for list YAML input")
        except TypeError:
            assert True


# ── save_agent_config edge cases ────────────────────────────────────────────────


class TestSaveAgentConfig:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "deeply" / "nested" / "agent_config.yml"
        cfg = AgentConfig(session_notes="deep")
        save_agent_config(cfg, path)
        assert path.exists()
        roundtripped = load_agent_config(path)
        assert roundtripped.session_notes == "deep"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("session_notes: old_value\n")
        cfg = AgentConfig(session_notes="new_value")
        save_agent_config(cfg, path)
        saved = yaml.safe_load(path.read_text())
        assert saved["session_notes"] == "new_value"

    def test_writes_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(session_notes="test", use_langgraph_tool_loop=True)
        save_agent_config(cfg, path)
        content = yaml.safe_load(path.read_text())
        assert content["session_notes"] == "test"
        assert content["use_langgraph_tool_loop"] is True

    def test_roundtrip_preserves_bools_and_strings(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(
            session_notes="notes",
            use_langgraph_tool_loop=True,
            use_langgraph_review=False,
            bind_tools_on_dispatch=False,
        )
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.session_notes == "notes"
        assert rt.use_langgraph_tool_loop is True
        assert rt.use_langgraph_review is False
        assert rt.bind_tools_on_dispatch is False

    def test_roundtrip_with_preferred_agents(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(preferred_agents={"a": "sonnet", "b": "opus"})
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.preferred_agents == {"a": "sonnet", "b": "opus"}

    def test_roundtrip_with_model_profile(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(active_model_profile="fast")
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.active_model_profile == "fast"

    def test_special_chars_in_session_notes(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        special = "line1\nline2: value\n- bullet\n# comment"
        cfg = AgentConfig(session_notes=special)
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.session_notes == special

    def test_empty_string_fields(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig(session_notes="")
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.session_notes == ""

    def test_preserves_none_active_model_profile_after_save(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        cfg = AgentConfig()
        save_agent_config(cfg, path)
        rt = load_agent_config(path)
        assert rt.active_model_profile is None


# ── build_config_layer edge cases ───────────────────────────────────────────────


class TestBuildConfigLayer:
    def test_returns_config_layer(self) -> None:
        layer = build_config_layer()
        assert isinstance(layer, ConfigLayer)
        assert isinstance(layer.user, UserConfig)
        assert isinstance(layer.agent, AgentConfig)
        assert layer.defaults == {}

    def test_defaults_passed_through(self) -> None:
        defaults = {"key": "val", "num": 42}
        layer = build_config_layer(defaults=defaults)
        assert layer.defaults == defaults

    def test_defaults_none_coerces_to_empty_dict(self) -> None:
        layer = build_config_layer(defaults=None)
        assert layer.defaults == {}

    def test_with_agent_config_from_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("session_notes: 'from_file'\n")
        layer = build_config_layer(agent_path=path)
        assert layer.agent.session_notes == "from_file"

    def test_with_user_config_from_file(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("deletion_gate_threshold: 42\n")
        layer = build_config_layer(user_path=path)
        assert layer.user.deletion_gate_threshold == 42

    def test_defaults_resolve_fallback(self) -> None:
        layer = build_config_layer(defaults={"nonexistent_key": "fallback"})
        result = layer.resolve("nonexistent_key")
        assert result == "fallback"

    def test_user_overrides_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("deletion_gate_threshold: 99\n")
        layer = build_config_layer(
            user_path=path,
            defaults={"deletion_gate_threshold": 50},
        )
        assert layer.user.deletion_gate_threshold == 99

    def test_empty_dict_user_attr_resolves_to_agent(self, tmp_path: Path) -> None:
        ag_path = tmp_path / "agent.yml"
        ag_path.parent.mkdir(parents=True, exist_ok=True)
        ag_path.write_text("preferred_agents:\n  sonnet: 1\n")
        layer = build_config_layer(agent_path=ag_path)
        resolved = layer.resolve("preferred_agents")
        assert resolved == {"sonnet": 1}

    def test_defaults_none_string_does_not_override(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: true\n")
        layer = build_config_layer(user_path=path, defaults={"ornith_enabled": False})
        assert layer.user.ornith_enabled is True


# ── load_user_config edge cases ──────────────────────────────────────────────────


class TestLoadUserConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent" / "user.yml"
        cfg = load_user_config(path)
        assert isinstance(cfg, UserConfig)

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("")
        cfg = load_user_config(path)
        assert isinstance(cfg, UserConfig)

    def test_null_yaml_file(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("null\n")
        cfg = load_user_config(path)
        assert isinstance(cfg, UserConfig)

    def test_partial_yaml_respects_defaults_for_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: true\n")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.deletion_gate_threshold == 5

    def test_yaml_with_environment_variable_override(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("deletion_gate_threshold: 10\n")
        with mock.patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "77"}):
            cfg = load_user_config(path)
        assert cfg.deletion_gate_threshold == 77

    def test_json_env_var_override(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("slurm_preemption_backoff_schedule: [1, 2, 3]\n")
        with mock.patch.dict(os.environ, {"GLUDD_SLURM_PREEMPTION_BACKOFF_SCHEDULE": json.dumps([10, 20, 30])}):
            cfg = load_user_config(path)
        assert cfg.slurm_preemption_backoff_schedule == [10, 20, 30]

    def test_env_var_override_none_yaml_null_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_binary_path: null\nornith_enabled: true\n")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.ornith_binary_path == "ornith"

    def test_bool_env_var_json_true(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: false\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_ENABLED": "true"}):
            cfg = load_user_config(path)
        assert cfg.ornith_enabled is True

    def test_bool_env_var_json_false(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("default_spot: true\n")
        with mock.patch.dict(os.environ, {"GLUDD_DEFAULT_SPOT": "false"}):
            cfg = load_user_config(path)
        assert cfg.default_spot is False

    def test_int_env_var_override(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("slurm_max_resubmits: 1\n")
        with mock.patch.dict(os.environ, {"GLUDD_SLURM_MAX_RESUBMITS": "5"}):
            cfg = load_user_config(path)
        assert cfg.slurm_max_resubmits == 5

    def test_float_env_var_override(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("compute_idle_gpu_sm_pct: 1.0\n")
        with mock.patch.dict(os.environ, {"GLUDD_COMPUTE_IDLE_GPU_SM_PCT": "10.5"}):
            cfg = load_user_config(path)
        assert cfg.compute_idle_gpu_sm_pct == 10.5

    def test_non_json_env_var_falls_back_to_string(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_binary_path: default_ornith\n")
        with mock.patch.dict(os.environ, {"GLUDD_ORNITH_BINARY_PATH": "/usr/local/bin/myornith"}):
            cfg = load_user_config(path)
        assert cfg.ornith_binary_path == "/usr/local/bin/myornith"

    def test_nested_env_var_override_two_levels(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("network:\n  host: 127.0.0.1\n  port: 8000\n")
        with mock.patch.dict(os.environ, {"GLUDD_NETWORK__PORT": "9090"}):
            cfg = load_user_config(path)
        assert cfg.network.port == 9090

    def test_nested_env_var_override_three_levels(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("checkpointing:\n  enabled: false\n  backend: s3\n")
        with mock.patch.dict(os.environ, {"GLUDD_CHECKPOINTING__ENABLED": "true"}):
            cfg = load_user_config(path)
        assert cfg.checkpointing == {"enabled": True, "backend": "s3"}

    def test_nested_env_var_creates_nested_dict_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("agents: {}\n")
        with mock.patch.dict(os.environ, {"GLUDD_AGENTS__TIMEOUT": "120"}):
            cfg = load_user_config(path)
        assert cfg.agents == {"timeout": 120}

    def test_yaml_with_non_gludd_env_vars_not_merged(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: false\n")
        with mock.patch.dict(os.environ, {"OTHER_PREFIX_ORNITH_ENABLED": "true"}):
            cfg = load_user_config(path)
        assert cfg.ornith_enabled is False

    def test_yaml_null_field_retains_default(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("deletion_gate_threshold: null\n")
        cfg = load_user_config(path)
        assert cfg.deletion_gate_threshold == 5

    def test_model_routing_in_yaml(self, tmp_path: Path) -> None:

        path = tmp_path / "user.yml"
        path.write_text("model_routing:\n  default_profile: sonnet\n  weak_model_profile: haiku\n")
        cfg = load_user_config(path)
        assert cfg.model_routing is not None
        assert cfg.model_routing.default_profile == "sonnet"
        assert cfg.model_routing.weak_model_profile == "haiku"

    def test_network_config_from_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("network:\n  host: 0.0.0.0\n  port: 443\n  allowed_cidr:\n    - 10.0.0.0/8\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_user_config(path)
        assert cfg.network.host == "0.0.0.0"
        assert cfg.network.port == 443
        assert cfg.network.allowed_cidr == ["10.0.0.0/8"]

    def test_yaml_with_special_float_values(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("compute_idle_gpu_sm_pct: 5.0\n")
        cfg = load_user_config(path)
        assert cfg.compute_idle_gpu_sm_pct == 5.0

    def test_yaml_with_boolean_variants(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: yes\nsearx_autostart: on\nallow_unconfigured_model: no\n")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.searx_autostart is True
        assert cfg.allow_unconfigured_model is False

    def test_yaml_without_trailing_newline(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: true")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True

    def test_yaml_with_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text(
            "# top comment\nornith_enabled: true  # inline\n# another comment\ndeletion_gate_threshold: 99\n"
        )
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.deletion_gate_threshold == 99

    def test_env_var_that_is_not_a_field_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("ornith_enabled: false\n")
        with mock.patch.dict(os.environ, {"GLUDD_FUTURE_FIELD": "999"}):
            cfg = load_user_config(path)
        assert cfg.ornith_enabled is False

    def test_env_var_overrides_yaml_none_stripping(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("deletion_gate_threshold: null\nornith_enabled: false\n")
        with mock.patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "15"}):
            cfg = load_user_config(path)
        assert cfg.deletion_gate_threshold == 15
        assert cfg.ornith_enabled is False

    def test_multiple_nested_env_vars(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("pipeline:\n  enabled: false\n  floor: 1\n  target: 3\n")
        with mock.patch.dict(
            os.environ,
            {
                "GLUDD_PIPELINE__ENABLED": "true",
                "GLUDD_PIPELINE__FLOOR": "10",
                "GLUDD_PIPELINE__TARGET": "10",
            },
        ):
            cfg = load_user_config(path)
        assert cfg.pipeline.enabled is True
        assert cfg.pipeline.floor == 10
        assert cfg.pipeline.target == 10

    def test_mean_large_config(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        lines = ["ornith_enabled: true", "slurm_max_resubmits: 5", "deletion_gate_threshold: 20"]
        lines += [f"# comment line {i}" for i in range(100)]
        path.write_text("\n".join(lines) + "\n")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.slurm_max_resubmits == 5
        assert cfg.deletion_gate_threshold == 20

    def test_from_yaml_skips_none_values_in_merged_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "user.yml"
        path.write_text("model_routing: null\nrelationship_routing: null\nornith_enabled: true\n")
        cfg = load_user_config(path)
        assert cfg.ornith_enabled is True
        assert cfg.model_routing is None
        assert cfg.relationship_routing is None


# ── ConfigLayer.resolve edge cases ──────────────────────────────────────────────


class TestConfigLayerResolve:
    def test_resolve_returns_user_value_for_non_dict_field(self) -> None:
        layer = ConfigLayer(
            user=UserConfig(ornith_enabled=True),
            defaults={"ornith_enabled": False},
        )
        assert layer.resolve("ornith_enabled") is True

    def test_resolve_falls_back_to_agent_when_user_empty_dict(self) -> None:
        layer = ConfigLayer(
            user=UserConfig(agents={}),
            agent=AgentConfig(preferred_agents={"m": "sonnet"}),
        )
        assert layer.resolve("preferred_agents") == {"m": "sonnet"}

    def test_resolve_falls_back_to_defaults(self) -> None:
        layer = ConfigLayer(defaults={"custom_key": "default_val"})
        assert layer.resolve("custom_key") == "default_val"

    def test_resolve_returns_none_when_none_in_defaults(self) -> None:
        layer = ConfigLayer(defaults={"missing_key": None})
        assert layer.resolve("missing_key") is None

    def test_resolve_agent_non_dict_field(self) -> None:
        layer = ConfigLayer(
            agent=AgentConfig(bind_tools_on_dispatch=False),
            defaults={"bind_tools_on_dispatch": True},
        )
        assert layer.resolve("bind_tools_on_dispatch") is False

    def test_resolve_returns_none_for_missing_key(self) -> None:
        layer = ConfigLayer()
        assert layer.resolve("nonexistent_key") is None

    def test_resolve_empty_dict_agent_field_is_skipped(self) -> None:
        layer = ConfigLayer(
            agent=AgentConfig(preferred_agents={}),
            defaults={"preferred_agents": {"fallback": "haiku"}},
        )
        assert layer.resolve("preferred_agents") == {"fallback": "haiku"}

    def test_resolve_model_routing_user_none_falls_to_agent(self, tmp_path: Path) -> None:
        from general_ludd.config.model_routing import ModelRoutingConfig

        layer = ConfigLayer(
            user=UserConfig(model_routing=None),
            agent=AgentConfig(model_routing=ModelRoutingConfig(default_profile="opus")),
        )
        result = layer.resolve_model_routing()
        assert result.default_profile == "opus"

    def test_resolve_model_routing_user_wins(self) -> None:
        from general_ludd.config.model_routing import ModelRoutingConfig

        layer = ConfigLayer(
            user=UserConfig(model_routing=ModelRoutingConfig(default_profile="sonnet")),
            agent=AgentConfig(model_routing=ModelRoutingConfig(default_profile="haiku")),
        )
        result = layer.resolve_model_routing()
        assert result.default_profile == "sonnet"

    def test_resolve_model_routing_both_none_returns_default(self) -> None:
        from general_ludd.config.model_routing import ModelRoutingConfig

        layer = ConfigLayer(
            user=UserConfig(model_routing=None),
            agent=AgentConfig(model_routing=None),
        )
        result = layer.resolve_model_routing()
        assert isinstance(result, ModelRoutingConfig)
        assert result.default_profile is None


# ── load_agent_config default path edge cases ───────────────────────────────────


class TestLoadAgentConfigDefaultPath:
    def test_default_path_relative_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = tmp_path / ".general-ludd"
        d.mkdir()
        (d / "agent_config.yml").write_text("session_notes: default_path_test\n")
        monkeypatch.chdir(tmp_path)
        cfg = load_agent_config()
        assert cfg.session_notes == "default_path_test"

    def test_default_path_missing_returns_default_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = load_agent_config()
        assert isinstance(cfg, AgentConfig)
        assert cfg.session_notes == ""


# ── save_agent_config default path ──────────────────────────────────────────────


class TestSaveAgentConfigDefaultPath:
    def test_default_path_saves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = AgentConfig(session_notes="saved_via_default")
        save_agent_config(cfg)
        saved_path = tmp_path / ".general-ludd" / "agent_config.yml"
        assert saved_path.exists()
        content = yaml.safe_load(saved_path.read_text())
        assert content["session_notes"] == "saved_via_default"


# ── load_agent_config with permission errors ────────────────────────────────────


class TestLoadAgentConfigPermissions:
    def test_permission_error_while_checking_path_falls_back_to_default(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"

        with mock.patch.object(Path, "exists", side_effect=PermissionError("denied")):
            cfg = load_agent_config(path)

        assert cfg == AgentConfig()

    def test_permission_error_while_opening_file_falls_back_to_default(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".general-ludd" / "agent_config.yml"
        path.parent.mkdir()
        path.write_text("session_notes: must_not_load\n")

        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            cfg = load_agent_config(path)

        assert cfg == AgentConfig()

    def test_unreadable_directory_falls_back_to_default(self, tmp_path: Path) -> None:
        d = tmp_path / ".general-ludd"
        d.mkdir()
        path = d / "agent_config.yml"
        path.write_text("session_notes: secret\n")
        d.chmod(0o000)
        try:
            cfg = load_agent_config(path)
            assert isinstance(cfg, AgentConfig)
        finally:
            d.chmod(0o755)

    def test_nonexistent_parent_dir_str_passed(self, tmp_path: Path) -> None:
        path = tmp_path / "missing_dir" / "agent_config.yml"
        cfg = load_agent_config(path)
        assert isinstance(cfg, AgentConfig)
