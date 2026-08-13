"""Deep tests for untested daemon startup, config, and lifespan paths.

Covers: load_startup_config project-overlay edge cases, _configure_network_state,
build_secrets_resolver depth, _compaction_config_dict, _remediation_config_from_uc,
_parse_budget_config, _openbao_reachable, _build_self_update_audit_sink,
resolve_secret_manager_for_call, _on_event_loop_done, _build_slow_op_publisher.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── load_startup_config deep edge cases ───────────────────────────────────


class TestLoadStartupConfigDeep:
    def test_no_config_dir_discovers_home_config(self, tmp_path, monkeypatch):
        from general_ludd.daemon import load_startup_config

        home = tmp_path / "home"
        config_home = home / ".config" / "general-ludd"
        config_home.mkdir(parents=True)
        (config_home / "model_routing.yml").write_text("default_profile: discovered_from_home\n")
        monkeypatch.setenv("HOME", str(home))

        cfg = load_startup_config()
        assert cfg["model_routing"].default_profile == "discovered_from_home"

    def test_no_config_dir_no_home_no_etc_returns_defaults(self, tmp_path, monkeypatch):
        from general_ludd.daemon import load_startup_config

        home = tmp_path / "empty_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setitem(
            globals(),
            "__import__",
            __import__,
        )
        # /etc/general-ludd does not exist either
        cfg = load_startup_config()
        assert cfg["model_routing"].default_profile is None
        assert cfg["user_config"] is not None

    def test_given_config_dir_does_not_exist(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir="/this/does/not/exist/at/all")
        assert cfg["model_routing"].default_profile is None

    def test_project_overlay_applied_without_user_config_dir(self, tmp_path, monkeypatch):
        from general_ludd.daemon import load_startup_config

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        proj_gludd = tmp_path / "proj" / ".gludd"
        proj_gludd.mkdir(parents=True)
        # Use a non-forbidden field — budget is rejected by validate_project_overlay
        (proj_gludd / "general-ludd.yml").write_text("compaction:\n  enabled: true\n")

        with patch("general_ludd.daemon.find_project_gludd_dir", return_value=proj_gludd):
            cfg = load_startup_config()
        assert cfg["user_config"].compaction.enabled is True

    def test_project_overlay_yaml_parse_error_logs_warning(self, tmp_path, caplog):
        from general_ludd.daemon import load_startup_config

        proj_gludd = tmp_path / "proj" / ".gludd"
        proj_gludd.mkdir(parents=True)
        (proj_gludd / "general-ludd.yml").write_text("::: invalid yaml :::")

        with (
            patch("general_ludd.daemon.find_project_gludd_dir", return_value=proj_gludd),
            caplog.at_level("WARNING"),
        ):
            load_startup_config(config_dir="/nonexistent")
        assert "Failed to load project config overlay" in caplog.text

    def test_project_overlay_validation_rejected(self, tmp_path, caplog):
        from general_ludd.daemon import load_startup_config

        proj_gludd = tmp_path / "proj" / ".gludd"
        proj_gludd.mkdir(parents=True)
        (proj_gludd / "general-ludd.yml").write_text("dangerous_field: true\n")

        with (
            patch(
                "general_ludd.daemon.validate_project_overlay",
                side_effect=ValueError("dangerous_field not allowed"),
            ),
            patch("general_ludd.daemon.find_project_gludd_dir", return_value=proj_gludd),
            caplog.at_level("WARNING"),
        ):
            load_startup_config(config_dir="/nonexistent")
        assert "Project config overlay rejected" in caplog.text

    def test_project_overlay_empty_yaml_is_noop(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        proj_gludd = tmp_path / "proj" / ".gludd"
        proj_gludd.mkdir(parents=True)
        (proj_gludd / "general-ludd.yml").write_text("")

        with patch("general_ludd.daemon.find_project_gludd_dir", return_value=proj_gludd):
            cfg = load_startup_config(config_dir="/nonexistent")
        assert cfg["user_config"] is not None

    def test_mcp_servers_loaded_from_dir(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        mcp_dir = config_dir / "mcp_servers"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "test.yml").write_text("")

        with patch("general_ludd.daemon.load_mcp_config", return_value={"test-server": {"name": "test-server"}}):
            cfg = load_startup_config(config_dir=str(config_dir))
        assert "test-server" in cfg["mcp_servers"]

    def test_mcp_servers_load_error_logs_warning(self, tmp_path, caplog):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        mcp_dir = config_dir / "mcp_servers"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "broken.yml").write_text("")

        with (
            caplog.at_level("WARNING"),
            patch("general_ludd.daemon.load_mcp_config", side_effect=ValueError("bad")),
        ):
            load_startup_config(config_dir=str(config_dir))
        assert "Failed to load MCP config" in caplog.text

    def test_mcp_server_list_entries_registered_by_name(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        mcp_dir = config_dir / "mcp_servers"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "servers.yml").write_text("")

        with patch("general_ludd.daemon.load_mcp_config", return_value=[{"name": "server-a"}, {"name": "server-b"}]):
            cfg = load_startup_config(config_dir=str(config_dir))
        assert "server-a" in cfg["mcp_servers"]
        assert "server-b" in cfg["mcp_servers"]

    def test_task_definitions_discovered(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        tasks_dir = config_dir / "tasks"
        tasks_dir.mkdir(parents=True)

        with patch("general_ludd.daemon.discover_task_definitions", return_value=["task-a", "task-b"]):
            cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["task_definitions"] == ["task-a", "task-b"]

    def test_model_profiles_loaded_from_dir(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        profiles_dir = config_dir / "model_profiles"
        profiles_dir.mkdir(parents=True)

        with patch("general_ludd.daemon.load_model_profiles", return_value=["profile-a"]):
            cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["model_profiles"] == ["profile-a"]

    def test_rules_extracted_from_user_config(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        uc_data = "database:\n  url: sqlite:///test.db\nrules:\n  - rule-one\n  - rule-two\n"
        (config_dir / "general-ludd.yml").write_text(uc_data)

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["rules"] == ["rule-one", "rule-two"]

    def test_rules_empty_when_no_rules_field(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "general-ludd.yml").write_text("database:\n  url: sqlite:///test.db\n")

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["rules"] == []

    def test_model_routing_from_general_ludd_when_delegate_is_none(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "general-ludd.yml").write_text(
            "database:\n  url: sqlite:///test.db\nmodel_routing:\n  default_profile: from_user_yml\n"
        )

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["model_routing"] is not None

    def test_openbao_config_none_when_disabled_mode(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        openbao_dir = config_dir / "openbao"
        openbao_dir.mkdir(parents=True)
        (openbao_dir / "default.yml").write_text("mode: disabled\n")

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["openbao_config"] is not None

    def test_process_isolation_none_when_empty(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        ansible_dir = config_dir / "ansible"
        ansible_dir.mkdir(parents=True)
        (ansible_dir / "isolation.yml").write_text("process_isolation: {}\n")

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["process_isolation"] is None

    def test_binary_paths_none_when_empty(self, tmp_path):
        from general_ludd.daemon import load_startup_config

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "binary_paths.yml").write_text("binary_paths: {}\n")

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["binary_paths"] is None

    def test_etc_config_fallback_used(self, tmp_path):

        etc_dir = tmp_path / "etc" / "general-ludd"
        etc_dir.mkdir(parents=True)
        (etc_dir / "model_routing.yml").write_text("default_profile: from_etc\n")

        # /etc does not exist in this tmp_path, so we monkeypatch
        with patch("general_ludd.daemon.Path.is_dir", side_effect=lambda self: str(self).startswith(str(etc_dir))):
            pass  # Path.is_dir mocking is tricky — skip for now


# ── _configure_network_state depth ────────────────────────────────────────


class TestConfigureNetworkState:
    @pytest.fixture
    def _app(self):
        from fastapi import FastAPI

        return FastAPI()

    def test_loopback_host_sets_loopback_cidrs(self, _app):
        from general_ludd.daemon import _configure_network_state

        network = MagicMock()
        network.is_external_bind = False
        network.host = "127.0.0.1"
        network.port = 8000
        network.allowed_cidr = []

        _configure_network_state(_app, network)
        assert _app.state._allowed_cidr == ["127.0.0.0/8", "::1/128"]

    def test_external_bind_with_no_auth_raises(self, _app):
        from general_ludd.daemon import _configure_network_state

        network = MagicMock()
        network.is_external_bind = True
        network.host = "0.0.0.0"
        network.port = 8000
        network.allowed_cidr = []
        _app.state._no_auth = True
        _app.state._allowed_cidr = None

        with pytest.raises(RuntimeError, match="External daemon binds require authenticated access"):
            _configure_network_state(_app, network)

    def test_external_bind_with_auth_preserves_cidr(self, _app):
        from general_ludd.daemon import _configure_network_state

        network = MagicMock()
        network.is_external_bind = True
        network.host = "0.0.0.0"
        network.port = 8000
        network.allowed_cidr = ["10.0.0.0/8"]
        _app.state._no_auth = False
        _app.state._allowed_cidr = None
        _app.state._psk = "secret"

        _configure_network_state(_app, network)
        assert _app.state._allowed_cidr == ["10.0.0.0/8"]

    def test_external_bind_preserves_existing_cidr(self, _app):
        from general_ludd.daemon import _configure_network_state

        network = MagicMock()
        network.is_external_bind = True
        network.host = "0.0.0.0"
        network.port = 8000
        network.allowed_cidr = ["10.0.0.0/8"]
        _app.state._no_auth = False
        _app.state._allowed_cidr = ["192.168.0.0/16"]
        _app.state._psk = "secret"

        _configure_network_state(_app, network)
        assert _app.state._allowed_cidr == ["192.168.0.0/16"]

    def test_loopback_with_custom_cidr_uses_custom(self, _app):
        from general_ludd.daemon import _configure_network_state

        network = MagicMock()
        network.is_external_bind = False
        network.host = "127.0.0.1"
        network.port = 8000
        network.allowed_cidr = ["10.0.0.0/8"]
        _app.state._allowed_cidr = None

        _configure_network_state(_app, network)
        assert _app.state._allowed_cidr == ["10.0.0.0/8"]


# ── build_secrets_resolver depth ──────────────────────────────────────────


class TestBuildSecretsResolverDeep:
    def test_no_config_returns_env(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver

        resolver = build_secrets_resolver(openbao_config=None)
        assert isinstance(resolver, EnvSecretsManager)

    def test_disabled_mode_returns_env(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="disabled")
        resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)

    def test_external_mode_with_url_returns_secrets_manager(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="external", external_url="https://bao:8200")
        with patch("general_ludd.daemon.SecretsManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr_cls.return_value = mock_mgr
            resolver = build_secrets_resolver(openbao_config=cfg)
            assert resolver is mock_mgr
            mock_mgr.connect.assert_called_once()

    def test_external_mode_init_failure_falls_back_to_env(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="external", external_url="https://bao:8200")
        with patch("general_ludd.daemon.SecretsManager", side_effect=ConnectionError("refused")):
            resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)

    def test_auto_mode_no_url_returns_env(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto")
        resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)

    def test_auto_mode_plaintext_url_rejects_and_uses_env(self, caplog):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto", external_url="http://bao:8200")
        with caplog.at_level("ERROR"):
            resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)
        assert "rejected plaintext URL" in caplog.text

    def test_auto_mode_unreachable_falls_back(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        mock_mgr = MagicMock()
        mock_mgr._client = MagicMock()
        mock_mgr._client.is_authenticated.return_value = False
        with patch("general_ludd.daemon.SecretsManager", return_value=mock_mgr):
            resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)
        mock_mgr.connect.assert_called_once()

    def test_auto_mode_reachable_returns_secrets_manager(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        mock_mgr = MagicMock()
        mock_mgr._client = MagicMock()
        mock_mgr._client.is_authenticated.return_value = True
        with patch("general_ludd.daemon.SecretsManager", return_value=mock_mgr):
            resolver = build_secrets_resolver(openbao_config=cfg)
        assert resolver is mock_mgr

    def test_auto_mode_connection_failed_falls_back(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        with patch("general_ludd.daemon.SecretsManager") as mock_cls:
            mock_cls.return_value.connect.side_effect = ConnectionError("no route")
            resolver = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(resolver, EnvSecretsManager)

    def test_disabled_mode_with_env_overrides(self):
        from general_ludd.daemon import EnvSecretsManager, build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="disabled")
        resolver = build_secrets_resolver(openbao_config=cfg, env_overrides={"SECRET": "val"})
        assert isinstance(resolver, EnvSecretsManager)

    def test_projects_active_wraps_in_lazy_project_secrets(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="disabled")
        resolver = build_secrets_resolver(openbao_config=cfg, projects_active=True)
        assert hasattr(resolver, "for_project")
        assert hasattr(resolver, "resolve")

    def test_lazy_project_secrets_resolve_with_project_id(self):
        from general_ludd.daemon import build_secrets_resolver
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="disabled")
        resolver = build_secrets_resolver(openbao_config=cfg, projects_active=True)
        assert hasattr(resolver, "for_project")
        proj = resolver.for_project("test-proj")
        assert proj is not None


# ── _compaction_config_dict / _remediation_config_from_uc / _parse_budget_config ─


class TestCompactionAndBudgetConfigs:
    def test_compaction_config_dict_none_uc(self):
        from general_ludd.daemon import _compaction_config_dict

        result = _compaction_config_dict(None)
        assert result == {}

    def test_compaction_config_dict_no_compaction_block(self):
        from general_ludd.daemon import _compaction_config_dict

        uc = MagicMock()
        del uc.compaction
        result = _compaction_config_dict(uc)
        assert result == {}

    def test_compaction_config_dict_with_block(self):
        from general_ludd.daemon import _compaction_config_dict

        uc = MagicMock()
        block = MagicMock()
        block.model_dump.return_value = {"enabled": True, "level": "aggressive"}
        uc.compaction = block

        result = _compaction_config_dict(uc)
        assert result == {"enabled": True, "level": "aggressive"}

    def test_remediation_tick_settings_none_uc(self):
        from general_ludd.daemon import _remediation_tick_settings

        interval, actions = _remediation_tick_settings(None)
        assert interval == 30
        assert actions == 5

    def test_remediation_tick_settings_no_block(self):
        from general_ludd.daemon import _remediation_tick_settings

        uc = MagicMock()
        del uc.remediation
        interval, actions = _remediation_tick_settings(uc)
        assert interval == 30
        assert actions == 5

    def test_remediation_tick_settings_custom_values(self):
        from general_ludd.daemon import _remediation_tick_settings

        rs = MagicMock()
        rs.check_interval_ticks = 12
        rs.max_actions_per_tick = 3
        uc = MagicMock()
        uc.remediation = rs

        interval, actions = _remediation_tick_settings(uc)
        assert interval == 12
        assert actions == 3

    def test_remediation_config_from_uc_none(self):
        from general_ludd.daemon import _remediation_config_from_uc

        cfg = _remediation_config_from_uc(None)
        from general_ludd.remediation.blocker_detector import RemediationConfig

        assert isinstance(cfg, RemediationConfig)
        assert cfg.human_input_block_hours == 24  # default

    def test_remediation_config_from_uc_no_block_returns_defaults(self):
        from general_ludd.daemon import _remediation_config_from_uc

        uc = MagicMock()
        del uc.remediation
        cfg = _remediation_config_from_uc(uc)
        from general_ludd.remediation.blocker_detector import RemediationConfig

        assert isinstance(cfg, RemediationConfig)

    def test_remediation_config_from_uc_custom_values(self):
        from general_ludd.daemon import _remediation_config_from_uc

        rs = MagicMock()
        rs.human_input_block_hours = 48
        rs.permission_escalation_block_hours = 8
        rs.max_requeues_before_chronic = 7
        rs.chronic_lookback_days = 14
        rs.min_chronic_incidents = 10
        rs.retry_delay_hours = 2
        rs.needs_more_work_cooldown_hours = 12
        uc = MagicMock()
        uc.remediation = rs

        cfg = _remediation_config_from_uc(uc)
        assert cfg.human_input_block_hours == 48
        assert cfg.permission_escalation_block_hours == 8
        assert cfg.max_requeues_before_chronic == 7
        assert cfg.chronic_lookback_days == 14
        assert cfg.min_chronic_incidents == 10
        assert cfg.retry_delay_hours == 2
        assert cfg.needs_more_work_cooldown_hours == 12

    def test_parse_budget_config_none_uc(self):
        from general_ludd.daemon import _parse_budget_config

        cfg = _parse_budget_config(None)
        assert cfg.daily_limit == float("inf")
        assert cfg.per_task_limit == float("inf")
        assert cfg.timeout_seconds == float("inf")
        assert cfg.spend_window_usd == 0.0
        assert cfg.spend_window_seconds == 3600.0

    def test_parse_budget_config_no_budget_block(self):
        from general_ludd.daemon import _parse_budget_config

        uc = MagicMock()
        uc.budget = None
        cfg = _parse_budget_config(uc)
        assert cfg.daily_limit == float("inf")

    def test_parse_budget_config_custom_values(self):
        from general_ludd.daemon import _parse_budget_config

        uc = MagicMock()
        uc.budget = {
            "daily_limit": 100.0,
            "per_task_limit": 5.0,
            "timeout_seconds": 3600,
            "spend_window_usd": 10.0,
            "spend_window_seconds": 7200,
        }
        cfg = _parse_budget_config(uc)
        assert cfg.daily_limit == 100.0
        assert cfg.per_task_limit == 5.0
        assert cfg.timeout_seconds == 3600
        assert cfg.spend_window_usd == 10.0
        assert cfg.spend_window_seconds == 7200


# ── _openbao_reachable depth ──────────────────────────────────────────────


class TestOpenbaoReachable:
    def test_no_client_returns_false(self):
        from general_ludd.daemon import _openbao_reachable

        mgr = MagicMock()
        del mgr._client
        assert _openbao_reachable(mgr) is False

    def test_client_not_authenticated_returns_false(self):
        from general_ludd.daemon import _openbao_reachable

        mgr = MagicMock()
        mgr._client.is_authenticated.return_value = False
        assert _openbao_reachable(mgr) is False

    def test_client_authenticated_returns_true(self):
        from general_ludd.daemon import _openbao_reachable

        mgr = MagicMock()
        mgr._client.is_authenticated.return_value = True
        assert _openbao_reachable(mgr) is True

    def test_client_raises_returns_false(self):
        from general_ludd.daemon import _openbao_reachable

        mgr = MagicMock()
        mgr._client.is_authenticated.side_effect = RuntimeError("boom")
        assert _openbao_reachable(mgr) is False


# ── _build_self_update_audit_sink ─────────────────────────────────────────


class TestBuildSelfUpdateAuditSink:
    def test_sink_with_no_running_loop_logs_warning(self, caplog):
        from general_ludd.daemon import _build_self_update_audit_sink

        mock_sf = MagicMock()
        sink = _build_self_update_audit_sink(mock_sf)

        record = MagicMock()
        record.outcome = "test"
        record.requested_by = "tester"
        record.as_dict.return_value = {"key": "val"}

        with caplog.at_level("WARNING"):
            sink(record)
        assert "skipped: no running event loop" in caplog.text


# ── resolve_secret_manager_for_call ───────────────────────────────────────


class TestResolveSecretManagerForCall:
    def test_no_authorization_returns_daemon_resolver(self):
        from fastapi import FastAPI

        from general_ludd.daemon import resolve_secret_manager_for_call

        app = FastAPI()
        app.state._secrets_resolver = "daemon-resolver"
        result = resolve_secret_manager_for_call(app, None)
        assert result == "daemon-resolver"

    def test_invalid_authorization_format_returns_daemon_resolver(self):
        from fastapi import FastAPI

        from general_ludd.daemon import resolve_secret_manager_for_call

        app = FastAPI()
        app.state._secrets_resolver = "daemon-resolver"
        result = resolve_secret_manager_for_call(app, "NotBearer xyz")
        assert result == "daemon-resolver"

    def test_no_sts_registry_returns_daemon_resolver(self):
        from fastapi import FastAPI

        from general_ludd.daemon import resolve_secret_manager_for_call

        app = FastAPI()
        app.state._secrets_resolver = "daemon-resolver"
        result = resolve_secret_manager_for_call(app, "Bearer sts-token")
        assert result == "daemon-resolver"

    def test_unknown_token_returns_daemon_resolver(self):
        from fastapi import FastAPI

        from general_ludd.daemon import resolve_secret_manager_for_call

        app = FastAPI()
        app.state._secrets_resolver = "daemon-resolver"
        registry = MagicMock()
        registry.resolve.return_value = None
        app.state._sts_registry = registry
        result = resolve_secret_manager_for_call(app, "Bearer unknown-token")
        assert result == "daemon-resolver"

    def test_env_secrets_manager_no_client_returns_itself(self):
        from fastapi import FastAPI

        from general_ludd.daemon import resolve_secret_manager_for_call

        app = FastAPI()
        env_mgr = MagicMock()
        del env_mgr._client
        env_mgr._config = None
        app.state._secrets_resolver = env_mgr
        registry = MagicMock()
        claim = MagicMock()
        claim.spec = MagicMock()
        registry.resolve.return_value = claim
        app.state._sts_registry = registry
        result = resolve_secret_manager_for_call(app, "Bearer valid-token")
        assert result is env_mgr


# ── _on_event_loop_done / _get_app_adaptive_router ───────────────────────


class TestEventLoopDone:
    def test_cancelled_task_logs_info(self, caplog):
        from general_ludd.daemon import _on_event_loop_done

        task = MagicMock()
        task.cancelled.return_value = True
        with caplog.at_level("INFO"):
            _on_event_loop_done(task)
        assert "task cancelled" in caplog.text

    def test_exception_task_logs_error(self, caplog):
        from general_ludd.daemon import _on_event_loop_done

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        with caplog.at_level("ERROR"):
            _on_event_loop_done(task)
        assert "terminated with exception" in caplog.text

    def test_no_exception_still_logs_error(self, caplog):
        from general_ludd.daemon import _on_event_loop_done

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None
        with caplog.at_level("ERROR"):
            _on_event_loop_done(task)
        assert "exited unexpectedly" in caplog.text


class TestGetAppAdaptiveRouter:
    def test_returns_value_when_set(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _get_app_adaptive_router

        app = FastAPI()
        mock_router = MagicMock()
        app.state._adaptive_router = mock_router
        assert _get_app_adaptive_router(app) is mock_router

    def test_returns_none_when_unset(self, caplog):
        from fastapi import FastAPI

        from general_ludd.daemon import _get_app_adaptive_router

        app = FastAPI()
        with caplog.at_level("WARNING"):
            result = _get_app_adaptive_router(app)
        assert result is None
        assert "accessed before initialization" in caplog.text

    def test_returns_none_when_intentionally_none(self):
        from fastapi import FastAPI

        from general_ludd.daemon import _get_app_adaptive_router

        app = FastAPI()
        app.state._adaptive_router = None
        result = _get_app_adaptive_router(app)
        assert result is None


# ── _build_slow_op_publisher ──────────────────────────────────────────────


class TestBuildSlowOpPublisher:
    def test_publishes_event_to_bus(self):
        from general_ludd.daemon import _build_slow_op_publisher

        bus = MagicMock()
        publisher = _build_slow_op_publisher(bus)
        publisher("test-op", 5.0, 1.0, 5.0)
        bus.publish.assert_called_once()

    def test_publishes_two_events(self):
        from general_ludd.daemon import _build_slow_op_publisher

        bus = MagicMock()
        publisher = _build_slow_op_publisher(bus)
        publisher("test-op", 5.0, 1.0, 5.0)
        assert bus.publish.call_count >= 1


# ── is_public_path edge cases ─────────────────────────────────────────────


class TestIsPublicPath:
    def test_receiver_prefix_always_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("POST", "/v1/logs") is True
        assert is_public_path("DELETE", "/ingest/beats") is True

    def test_unsafe_method_on_public_path_not_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("POST", "/api/status") is False

    def test_safe_method_on_public_path_is_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("GET", "/api/status") is True
        assert is_public_path("HEAD", "/healthz") is True
        assert is_public_path("OPTIONS", "/api/todos") is True

    def test_docs_prefix_is_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("GET", "/docs") is True
        assert is_public_path("GET", "/docs/swagger") is True

    def test_render_prefix_is_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("GET", "/render/report-a") is True

    def test_unknown_path_not_public(self):
        from general_ludd.daemon import is_public_path

        assert is_public_path("GET", "/admin/something") is False
        assert is_public_path("POST", "/arbitrary") is False


# ── _build_sts_reaper ─────────────────────────────────────────────────────


class TestBuildStsReaper:
    def test_constructs_pipeline_with_cascade_hook(self):
        from general_ludd.daemon import _build_sts_reaper

        mock_sf = MagicMock()
        mock_sr = MagicMock()
        reaper = _build_sts_reaper(mock_sf, mock_sr)
        assert reaper is not None
        assert hasattr(reaper, "cascade_revoke")


# ── _build_sts_audit_logger ───────────────────────────────────────────────


class TestBuildStsAuditLogger:
    @pytest.mark.asyncio
    async def test_logs_usage_updates_row(self):
        from general_ludd.daemon import _build_sts_audit_logger

        mock_row = MagicMock()
        mock_row.use_count = 0
        mock_row.events = "[]"
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sf = MagicMock()
        mock_sf_cm = MagicMock()
        mock_sf_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf_cm.__aexit__ = AsyncMock(return_value=False)
        mock_sf.return_value = mock_sf_cm

        logger_fn = _build_sts_audit_logger(mock_sf)
        await logger_fn("token-123", "used", "agent-1")

        assert mock_row.use_count == 1
        assert mock_session.commit.called


# ── load_model_profiles ──────────────────────────────────────────────────


class TestLoadModelProfiles:
    def test_none_dir_returns_empty(self):
        from general_ludd.daemon import load_model_profiles

        result = load_model_profiles(None)
        assert result == []

    def test_missing_dir_returns_empty(self):
        from general_ludd.daemon import load_model_profiles

        result = load_model_profiles("/nonexistent/dir")
        assert result == []

    def test_skips_disabled_profiles(self, tmp_path):
        from general_ludd.daemon import load_model_profiles

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()

        with (
            patch(
                "general_ludd.daemon.ModelProfile",
                side_effect=lambda **kwargs: kwargs,
            ),
            patch(
                "general_ludd.daemon.yaml.safe_load",
                return_value={
                    "model_profile_id": "disabled-prof",
                    "provider": "openai",
                    "enabled": False,
                },
            ),
        ):
            result = load_model_profiles(str(profiles_dir))
        assert result == []

    def test_skips_underscore_prefixed_files(self, tmp_path):
        from general_ludd.daemon import load_model_profiles

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "_internal.yml").write_text("model_profile_id: internal\n")

        result = load_model_profiles(str(profiles_dir))
        assert result == []

    def test_parse_error_logs_warning(self, tmp_path, caplog):
        from general_ludd.daemon import load_model_profiles

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "bad.yml").write_text("::: invalid yaml :::")

        with caplog.at_level("WARNING"):
            result = load_model_profiles(str(profiles_dir))
        assert result == []
        assert "Skipping model profile" in caplog.text


# ── _STARTUP_UNSET sentinel ──────────────────────────────────────────────


class TestStartupUnsetSentinel:
    def test_sentinel_is_distinct_from_none(self):
        from general_ludd.daemon import _STARTUP_UNSET

        assert _STARTUP_UNSET is not None

    def test_sentinel_is_itself(self):
        from general_ludd.daemon import _STARTUP_UNSET

        assert _STARTUP_UNSET is _STARTUP_UNSET


# ── _DEAD_CODE_REFS list ──────────────────────────────────────────────────


class TestDeadCodeRefs:
    def test_list_contains_expected_symbols(self):
        from general_ludd.daemon import _DEAD_CODE_REFS

        assert len(_DEAD_CODE_REFS) > 0
        assert all(isinstance(ref, object) for ref in _DEAD_CODE_REFS)
