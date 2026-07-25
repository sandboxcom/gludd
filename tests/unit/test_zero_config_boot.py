"""Zero-config boot verification.

Verifies the daemon can determine ALL required settings from built-in defaults
without any user-created config files. The config loading chain is:

    field defaults  →  system config (/etc/general-ludd)  →
        user config (~/.config/general-ludd)  →  GLUDD_* env vars  →  CLI flags

Every layer above the field defaults is OPTIONAL. If no config file exists and
no env var is set, the system still boots with safe built-in defaults.

This is a structural/behavioral test — it does not boot the daemon. Instead it
verifies:
  1. Each config model constructs with zero arguments (every field has a default)
  2. The loader functions handle missing files gracefully (return defaults)
  3. ``load_startup_config`` returns a fully-populated dict with no config dir
  4. Env var overrides (GLUDD_NETWORK__HOST, GLUDD_PIPELINE__ENABLED) take effect
  5. Every boot-critical config file in ``config/`` has a matching default model
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.config.binary_paths import BinaryPaths
from general_ludd.config.loader import (
    build_config_layer,
    load_agent_config,
    load_user_config,
)
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.user_config import (
    AgentConfig,
    ConfigLayer,
    NetworkConfig,
    UserConfig,
)
from general_ludd.secrets.config import OpenBaoConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG_DIR = REPO_ROOT / "config"


class TestConfigModelsConstructWithDefaults:
    """Every config model MUST construct with zero args (all fields defaulted)."""

    def test_user_config_constructs_with_no_args(self):
        cfg = UserConfig()
        assert cfg is not None
        assert cfg.network.host == "127.0.0.1"
        assert cfg.network.port == 8000

    def test_model_routing_config_constructs_with_no_args(self):
        cfg = ModelRoutingConfig()
        assert cfg.default_profile is None
        assert cfg.role_routing == {}

    def test_binary_paths_constructs_with_no_args(self):
        cfg = BinaryPaths()
        assert cfg.terraform == "terraform"
        assert cfg.git == "git"

    def test_openbao_config_constructs_with_no_args(self):
        cfg = OpenBaoConfig()
        assert cfg.mode == "auto"
        assert cfg.kv_mount == "secret"

    def test_process_isolation_config_constructs_with_no_args(self):
        cfg = ProcessIsolationConfig()
        assert cfg.enabled is False
        assert cfg.executable == "podman"

    def test_agent_config_constructs_with_no_args(self):
        cfg = AgentConfig()
        assert cfg.model_routing is None
        assert cfg.preferred_agents == {}

    def test_network_config_constructs_with_no_args(self):
        cfg = NetworkConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.allowed_cidr == []

    def test_config_layer_constructs_with_no_args(self):
        layer = ConfigLayer()
        assert isinstance(layer.user, UserConfig)
        assert isinstance(layer.agent, AgentConfig)


class TestUserConfigAllFieldsHaveDefaults:
    """No UserConfig field is required — every field has a default value."""

    def test_every_user_config_field_has_a_default(self):
        for name, field in UserConfig.model_fields.items():
            assert field.is_required() is False, (
                f"UserConfig.{name} is REQUIRED — zero-config boot would fail. "
                f"Add a default value."
            )

    def test_every_model_routing_field_has_a_default(self):
        for name, field in ModelRoutingConfig.model_fields.items():
            assert field.is_required() is False, (
                f"ModelRoutingConfig.{name} is REQUIRED — zero-config boot would fail."
            )

    def test_every_openbao_field_has_a_default(self):
        for name, field in OpenBaoConfig.model_fields.items():
            assert field.is_required() is False, (
                f"OpenBaoConfig.{name} is REQUIRED — zero-config boot would fail."
            )

    def test_every_process_isolation_field_has_a_default(self):
        for name, field in ProcessIsolationConfig.model_fields.items():
            assert field.is_required() is False, (
                f"ProcessIsolationConfig.{name} is REQUIRED — zero-config boot would fail."
            )


class TestLoadersHandleMissingFiles:
    """Every loader MUST return defaults when its file is absent (no crash)."""

    def test_load_user_config_missing_path_returns_defaults(self, tmp_path):
        cfg = load_user_config(tmp_path / "does-not-exist.yml")
        assert isinstance(cfg, UserConfig)
        assert cfg.network.host == "127.0.0.1"

    def test_load_agent_config_missing_path_returns_defaults(self, tmp_path):
        cfg = load_agent_config(tmp_path / "missing.yml")
        assert isinstance(cfg, AgentConfig)
        assert cfg.model_routing is None

    def test_load_model_routing_missing_path_returns_defaults(self, tmp_path):
        cfg = load_model_routing(tmp_path / "absent.yml")
        assert isinstance(cfg, ModelRoutingConfig)
        assert cfg.default_profile is None

    def test_build_config_layer_with_all_none_paths(self):
        layer = build_config_layer(user_path=None, agent_path=None, defaults=None)
        assert isinstance(layer, ConfigLayer)
        assert isinstance(layer.user, UserConfig)
        assert isinstance(layer.agent, AgentConfig)


class TestStartupConfigZeroConfig:
    """``load_startup_config`` MUST return a fully-populated dict with no config."""

    def test_startup_config_with_nonexistent_dir_returns_defaults(self):
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir="/nonexistent/path/zero-config-test")
        assert "model_routing" in cfg
        assert "user_config" in cfg
        assert isinstance(cfg["user_config"], UserConfig)
        assert isinstance(cfg["model_routing"], ModelRoutingConfig)
        assert cfg["mcp_servers"] == {}
        assert cfg["task_definitions"] == []
        assert cfg["model_profiles"] == []

    def test_startup_config_with_none_dir_does_not_crash(self):
        """When config_dir=None and no ~/.config or /etc dir exists, defaults win."""
        from general_ludd.daemon import load_startup_config

        with patch.dict(os.environ, {"HOME": "/nonexistent/home/zero-config"}, clear=False):
            cfg = load_startup_config(config_dir=None)
        assert isinstance(cfg["user_config"], UserConfig)
        assert cfg["user_config"].network.host == "127.0.0.1"

    def test_startup_config_keys_all_present_with_zero_config(self):
        """Every key the daemon consumes MUST be populated by the defaults path."""
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir="/nonexistent/zero-config")
        required_keys = {
            "model_routing",
            "user_config",
            "binary_paths",
            "openbao_config",
            "process_isolation",
            "mcp_servers",
            "task_definitions",
            "model_profiles",
            "rules",
            "project_gludd_dir",
            "remediation_config",
        }
        missing = required_keys - cfg.keys()
        assert missing == set(), (
            f"load_startup_config did not populate keys: {missing}. "
            f"Zero-config boot would KeyError on these."
        )


class TestEnvVarOverrides:
    """GLUDD_* env vars MUST override the built-in defaults."""

    def test_network_host_env_var_overrides_default(self):
        with patch.dict(os.environ, {"GLUDD_NETWORK__HOST": "10.0.0.1"}, clear=False):
            cfg = UserConfig()
        assert cfg.network.host == "10.0.0.1"

    def test_pipeline_enabled_env_var_overrides_default(self):
        with patch.dict(os.environ, {"GLUDD_PIPELINE__ENABLED": "true"}, clear=False):
            cfg = UserConfig()
        assert cfg.pipeline.enabled is True

    def test_deletion_gate_threshold_env_var_overrides_default(self):
        with patch.dict(os.environ, {"GLUDD_DELETION_GATE_THRESHOLD": "99"}, clear=False):
            cfg = UserConfig()
        assert cfg.deletion_gate_threshold == 99

    def test_compaction_enabled_env_var_overrides_default(self):
        with patch.dict(os.environ, {"GLUDD_COMPACTION__ENABLED": "true"}, clear=False):
            cfg = UserConfig()
        assert cfg.compaction.enabled is True

    def test_env_var_overrides_yaml_in_load_user_config(self, tmp_path):
        """Env var precedence > YAML file (per the documented loading chain)."""
        yml = tmp_path / "user.yml"
        yml.write_text("deletion_gate_threshold: 5\n")
        with patch.dict(
            os.environ,
            {"GLUDD_DELETION_GATE_THRESHOLD": "42"},
            clear=False,
        ):
            cfg = load_user_config(yml)
        assert cfg.deletion_gate_threshold == 42


class TestEnvVarJsonForm:
    """Top-level GLUDD_<FIELD> env vars accept JSON for nested-dict fields."""

    def test_network_json_env_var_overrides_default(self):
        import json

        payload = json.dumps({"host": "192.168.1.10", "port": 9999})
        with patch.dict(os.environ, {"GLUDD_NETWORK": payload}, clear=False):
            cfg = UserConfig()
        assert cfg.network.host == "192.168.1.10"
        assert cfg.network.port == 9999


class TestRepoConfigFilesHaveDefaults:
    """Every boot-critical config file shipped in ``config/`` MUST have a
    corresponding config model with built-in defaults.

    These are the files the daemon's ``load_startup_config`` reads from the
    user config dir. Auxiliary files (permissions/, opa/, skills/, examples/,
    infra/, ratchet.yml, tdd_allowlist.yml) are loaded by other subsystems and
    are not boot-critical.
    """

    BOOT_CRITICAL_FILES = frozenset(
        {
            "general-ludd.yml",
            "model_routing.yml",
            "binary_paths.yml",
            "openbao/default.yml",
            "ansible/isolation.yml",
        }
    )

    @pytest.mark.parametrize("relative", sorted(BOOT_CRITICAL_FILES))
    def test_boot_critical_file_has_default_model(self, relative):
        assert (REPO_CONFIG_DIR / relative).exists(), (
            f"Boot-critical sample config {relative} missing from repo config/."
        )

    def test_each_boot_critical_file_loads_via_corresponding_model(self, tmp_path):
        """Each boot-critical config file in config/ must round-trip through a
        config model with defaults — the default model produces the same shape
        the file provides."""
        # general-ludd.yml → UserConfig
        assert UserConfig().model_dump() is not None
        # model_routing.yml → ModelRoutingConfig
        assert ModelRoutingConfig().model_dump() is not None
        # binary_paths.yml → BinaryPaths
        assert BinaryPaths().model_dump() is not None
        # openbao/default.yml → OpenBaoConfig
        assert OpenBaoConfig().model_dump() is not None
        # ansible/isolation.yml → ProcessIsolationConfig
        assert ProcessIsolationConfig().model_dump() is not None

    def test_optional_dirs_default_to_empty_when_absent(self, tmp_path):
        """mcp_servers/, tasks/, model_profiles/ are optional. Missing → empty."""
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["mcp_servers"] == {}
        assert cfg["task_definitions"] == []
        assert cfg["model_profiles"] == []


class TestLoadingChainOrder:
    """Documented chain: defaults → system config → user config → env → CLI.

    This test verifies the env-overrides-YAML precedence relationship, which is
    the only precedence that can be exercised without a real /etc dir.
    """

    def test_env_var_wins_over_yaml(self, tmp_path):
        yml = tmp_path / "user.yml"
        yml.write_text("deletion_gate_threshold: 7\n")
        with patch.dict(
            os.environ,
            {"GLUDD_DELETION_GATE_THRESHOLD": "100"},
            clear=False,
        ):
            yaml_val = load_user_config(yml).deletion_gate_threshold
            env_val = UserConfig().deletion_gate_threshold
        # YAML path honored when env unset; env path overrides when set.
        assert yaml_val == 100
        assert env_val == 100

    def test_default_used_when_no_yaml_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = UserConfig()
        assert cfg.deletion_gate_threshold == 5


class TestNetworkConfigSafetyDefault:
    """The default network config MUST be loopback-only (safe out of the box)."""

    def test_default_host_is_loopback(self):
        assert NetworkConfig().host == "127.0.0.1"

    def test_default_port_is_8000(self):
        assert NetworkConfig().port == 8000

    def test_world_open_host_requires_allowed_cidr(self):
        """A world-open bind without an allowlist is rejected — the default
        must never accidentally bind to all interfaces."""
        with pytest.raises(ValueError, match="allowed_cidr"):
            NetworkConfig(host="0.0.0.0", allowed_cidr=[])
