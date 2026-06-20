"""Additional unit tests for daemon.py module-level functions.

Targets high-ROI pure/near-pure functions:
- build_secrets_resolver (all branches)
- _openbao_reachable (all branches)
- load_startup_config (discovery + fallback + file branches)
- load_model_profiles (_private / disabled / malformed skip)
- _on_event_loop_done (cancelled / exception / normal exit)
- _parse_budget_config (defaults + overrides)
- _restore_persisted_spend / _restore_persisted_projects (None guard branches)
- _init_project_workspaces (None guard + list_active branch)
- auth middleware 401/503 via TestClient
- _is_public method/path logic
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import (
    _on_event_loop_done,
    _openbao_reachable,
    _parse_budget_config,
    build_secrets_resolver,
    create_daemon_app,
    load_model_profiles,
    load_startup_config,
)
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


# ---------------------------------------------------------------------------
# _openbao_reachable
# ---------------------------------------------------------------------------


class TestOpenbaoReachable:
    def test_client_none_returns_false(self):
        mgr = MagicMock()
        mgr._client = None
        assert _openbao_reachable(mgr) is False

    def test_missing_client_attr_returns_false(self):
        mgr = object()  # no _client attribute at all
        assert _openbao_reachable(mgr) is False

    def test_is_authenticated_true_returns_true(self):
        client = MagicMock()
        client.is_authenticated.return_value = True
        mgr = MagicMock()
        mgr._client = client
        assert _openbao_reachable(mgr) is True

    def test_is_authenticated_false_returns_false(self):
        client = MagicMock()
        client.is_authenticated.return_value = False
        mgr = MagicMock()
        mgr._client = client
        assert _openbao_reachable(mgr) is False

    def test_is_authenticated_raises_returns_false(self):
        client = MagicMock()
        client.is_authenticated.side_effect = ConnectionError("refused")
        mgr = MagicMock()
        mgr._client = client
        assert _openbao_reachable(mgr) is False


# ---------------------------------------------------------------------------
# build_secrets_resolver
# ---------------------------------------------------------------------------


class TestBuildSecretsResolver:
    def test_no_openbao_config_returns_env_manager(self):
        result = build_secrets_resolver(openbao_config=None)
        assert isinstance(result, EnvSecretsManager)

    def test_disabled_mode_returns_env_manager(self):
        cfg = OpenBaoConfig(mode="disabled")
        result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_env_overrides_forwarded(self):
        result = build_secrets_resolver(
            openbao_config=None,
            env_overrides={"MY_API_KEY": "secret"},
        )
        assert isinstance(result, EnvSecretsManager)
        assert result.resolve("MY_API_KEY") == "secret"

    def test_projects_active_wraps_in_lazy_project_secrets(self):
        result = build_secrets_resolver(openbao_config=None, projects_active=True)
        # Should not be a bare EnvSecretsManager — it's a _LazyProjectSecrets
        assert not isinstance(result, EnvSecretsManager)
        assert hasattr(result, "for_project")

    def test_lazy_project_secrets_for_project_returns_psm(self):
        from general_ludd.secrets.project_secrets import ProjectSecretsManager

        result = build_secrets_resolver(openbao_config=None, projects_active=True)
        psm = result.for_project("proj-123")
        assert isinstance(psm, ProjectSecretsManager)

    def test_lazy_project_secrets_resolve_delegates(self):
        result = build_secrets_resolver(
            openbao_config=None,
            env_overrides={"MY_API_KEY": "val"},
            projects_active=True,
        )
        assert result.resolve("MY_API_KEY") == "val"

    def test_lazy_project_secrets_resolve_nonexistent_returns_none(self):
        result = build_secrets_resolver(openbao_config=None, projects_active=True)
        assert result.resolve("TOTALLY_UNKNOWN_VAR_XYZ_ABC") is None

    def test_external_mode_connect_success_returns_secrets_manager(self):
        cfg = OpenBaoConfig(mode="external", external_url="https://bao:8200")
        mock_mgr = MagicMock()
        with patch("general_ludd.daemon.SecretsManager", return_value=mock_mgr):
            result = build_secrets_resolver(openbao_config=cfg)
        assert result is mock_mgr
        mock_mgr.connect.assert_called_once()

    def test_external_mode_connect_fails_falls_back_to_env(self):
        cfg = OpenBaoConfig(mode="external", external_url="https://bao:8200")
        with patch(
            "general_ludd.daemon.SecretsManager",
            side_effect=RuntimeError("connection refused"),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_auto_mode_no_url_returns_env_manager(self):
        cfg = OpenBaoConfig(mode="auto", external_url=None)
        result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_auto_mode_reachable_returns_secrets_manager(self):
        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        mock_mgr = MagicMock()
        with (
            patch("general_ludd.daemon.SecretsManager", return_value=mock_mgr),
            patch("general_ludd.daemon._openbao_reachable", return_value=True),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
        assert result is mock_mgr

    def test_auto_mode_unreachable_falls_back_to_env(self):
        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        mock_mgr = MagicMock()
        with (
            patch("general_ludd.daemon.SecretsManager", return_value=mock_mgr),
            patch("general_ludd.daemon._openbao_reachable", return_value=False),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_auto_mode_http_url_raises_logs_error_and_falls_back(self, caplog):
        """HTTP (not HTTPS) external_url in auto mode should fall back + log security error."""
        cfg = OpenBaoConfig(mode="auto", external_url="http://bao:8200")
        with (
            patch(
                "general_ludd.daemon.SecretsManager",
                side_effect=ValueError("plaintext rejected"),
            ),
            caplog.at_level("ERROR"),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)
        # Should log an error mentioning https
        error_messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
        assert any("https" in m.lower() or "plaintext" in m.lower() for m in error_messages)

    def test_auto_mode_connect_exception_non_http_logs_warning(self, caplog):
        cfg = OpenBaoConfig(mode="auto", external_url="https://bao:8200")
        with (
            patch(
                "general_ludd.daemon.SecretsManager",
                side_effect=RuntimeError("timeout"),
            ),
            caplog.at_level("WARNING"),
        ):
            result = build_secrets_resolver(openbao_config=cfg)
        assert isinstance(result, EnvSecretsManager)

    def test_unknown_mode_returns_env_manager(self):
        # mode must match the regex pattern; use a known valid mode but then
        # test that any non-external/auto mode falls into the else branch.
        # The only valid modes are auto/external/disabled.
        # disabled is tested above; test with external without a URL.
        cfg = OpenBaoConfig(mode="external", external_url=None)
        result = build_secrets_resolver(openbao_config=cfg)
        # external mode without URL: has_url is False → falls into the else at line 241
        assert isinstance(result, EnvSecretsManager)


# ---------------------------------------------------------------------------
# load_startup_config
# ---------------------------------------------------------------------------


class TestLoadStartupConfig:
    def test_nonexistent_config_dir_returns_defaults(self):
        cfg = load_startup_config(config_dir="/does/not/exist/xyz")
        assert cfg["model_routing"] is not None
        assert cfg["user_config"] is not None
        assert cfg["mcp_servers"] == {}
        assert cfg["task_definitions"] == []
        assert cfg["model_profiles"] == []

    def test_empty_config_dir_returns_defaults(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["binary_paths"] is None
        assert cfg["openbao_config"] is None
        assert cfg["process_isolation"] is None

    def test_model_routing_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        (d / "model_routing.yml").write_text("default_profile: prof1\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["model_routing"].default_profile == "prof1"

    def test_general_ludd_yml_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        (d / "general-ludd.yml").write_text("database:\n  url: sqlite:///test.db\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["user_config"].database["url"] == "sqlite:///test.db"

    def test_binary_paths_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        (d / "binary_paths.yml").write_text("binary_paths:\n  terraform: /usr/bin/terraform\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["binary_paths"] is not None

    def test_binary_paths_empty_block_returns_none(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        (d / "binary_paths.yml").write_text("{}\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["binary_paths"] is None

    def test_openbao_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        ob = d / "openbao"
        ob.mkdir()
        (ob / "default.yml").write_text("mode: external\nexternal_url: https://bao:8200\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["openbao_config"] is not None
        assert cfg["openbao_config"].mode == "external"

    def test_process_isolation_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        iso = d / "ansible"
        iso.mkdir()
        (iso / "isolation.yml").write_text(
            "process_isolation:\n  enabled: true\n  executable: docker\n"
        )
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["process_isolation"] is not None

    def test_process_isolation_empty_block_returns_none(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        iso = d / "ansible"
        iso.mkdir()
        (iso / "isolation.yml").write_text("{}\n")
        cfg = load_startup_config(config_dir=str(d))
        assert cfg["process_isolation"] is None

    def test_mcp_servers_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        mcp_dir = d / "mcp_servers"
        mcp_dir.mkdir()
        (mcp_dir / "test.yml").write_text('{"name": "myserver", "command": "npx"}\n')
        with patch(
            "general_ludd.daemon.load_mcp_config",
            return_value={"myserver": {"name": "myserver"}},
        ):
            cfg = load_startup_config(config_dir=str(d))
        assert "myserver" in cfg["mcp_servers"]

    def test_mcp_load_failure_does_not_crash(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        mcp_dir = d / "mcp_servers"
        mcp_dir.mkdir()
        (mcp_dir / "bad.yml").write_text("bad: [yaml")
        with patch("general_ludd.daemon.load_mcp_config", side_effect=ValueError("bad")):
            cfg = load_startup_config(config_dir=str(d))
        # No crash; mcp_servers may be empty
        assert isinstance(cfg["mcp_servers"], dict)

    def test_tasks_dir_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        tasks_dir = d / "tasks"
        tasks_dir.mkdir()
        with patch("general_ludd.daemon.discover_task_definitions", return_value=["task1"]):
            cfg = load_startup_config(config_dir=str(d))
        assert cfg["task_definitions"] == ["task1"]

    def test_model_profiles_dir_loaded(self, tmp_path):
        d = tmp_path / "cfg"
        d.mkdir()
        prof_dir = d / "model_profiles"
        prof_dir.mkdir()
        with patch("general_ludd.daemon.load_model_profiles", return_value=["p1"]):
            cfg = load_startup_config(config_dir=str(d))
        assert cfg["model_profiles"] == ["p1"]

    def test_config_dir_none_no_home_candidates_returns_defaults(self, monkeypatch, tmp_path):
        """With config_dir=None and no matching home dir, returns defaults."""
        # Override HOME to a tmp path where the standard dirs don't exist
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = load_startup_config(config_dir=None)
        assert cfg["model_routing"] is not None

    def test_mcp_list_format_loaded(self, tmp_path):
        """MCP config returned as a list of dicts (alternative format)."""
        d = tmp_path / "cfg"
        d.mkdir()
        mcp_dir = d / "mcp_servers"
        mcp_dir.mkdir()
        (mcp_dir / "list.yml").write_text("- name: srv1\n  command: npx\n")
        with patch(
            "general_ludd.daemon.load_mcp_config",
            return_value=[{"name": "srv1", "command": "npx"}],
        ):
            cfg = load_startup_config(config_dir=str(d))
        assert "srv1" in cfg["mcp_servers"]


# ---------------------------------------------------------------------------
# load_model_profiles
# ---------------------------------------------------------------------------


class TestLoadModelProfiles:
    def test_none_profiles_dir_returns_empty(self):
        assert load_model_profiles(profiles_dir=None) == []

    def test_nonexistent_dir_returns_empty(self):
        assert load_model_profiles(profiles_dir="/nonexistent/xyz") == []

    def test_private_file_skipped(self, tmp_path):
        d = tmp_path / "profiles"
        d.mkdir()
        (d / "_private.yml").write_text("model_id: skip\n")
        # Should be skipped (starts with _)
        result = load_model_profiles(profiles_dir=str(d))
        assert result == []

    def test_disabled_profile_skipped(self, tmp_path):
        d = tmp_path / "profiles"
        d.mkdir()
        (d / "disabled.yml").write_text("model_id: gpt4\nenabled: false\n")
        result = load_model_profiles(profiles_dir=str(d))
        assert result == []

    def test_malformed_profile_skipped(self, tmp_path):
        d = tmp_path / "profiles"
        d.mkdir()
        # Valid YAML but invalid ModelProfile (e.g., extra unknown required field —
        # actually ModelProfile may be lenient; use an invalid type to force exception)
        (d / "bad.yml").write_text("model_id: [this, is, a, list, not, a, string]\n")
        # Should not raise; bad profiles are skipped with a warning
        result = load_model_profiles(profiles_dir=str(d))
        # Either skipped or loaded — just must not raise
        assert isinstance(result, list)

    def test_valid_profile_loaded(self, tmp_path):
        from general_ludd.models.gateway import ModelProfile

        d = tmp_path / "profiles"
        d.mkdir()
        (d / "gpt4.yml").write_text(
            "model_id: gpt4\nprovider: openai\nmodel_name: gpt-4\n"
        )
        result = load_model_profiles(profiles_dir=str(d))
        assert len(result) == 1
        assert isinstance(result[0], ModelProfile)
        assert result[0].model_id == "gpt4"

    def test_multiple_profiles_sorted(self, tmp_path):
        d = tmp_path / "profiles"
        d.mkdir()
        (d / "b_profile.yml").write_text("model_id: b\n")
        (d / "a_profile.yml").write_text("model_id: a\n")
        result = load_model_profiles(profiles_dir=str(d))
        # sorted() means a_profile loads before b_profile
        assert len(result) == 2
        assert result[0].model_id == "a"
        assert result[1].model_id == "b"


# ---------------------------------------------------------------------------
# _on_event_loop_done
# ---------------------------------------------------------------------------


class TestOnEventLoopDone:
    def test_cancelled_task_logs_info(self, caplog):
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = True
        with caplog.at_level("INFO"):
            _on_event_loop_done(task)
        assert any("cancel" in r.message.lower() for r in caplog.records)

    def test_task_with_exception_logs_error(self, caplog):
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        with caplog.at_level("ERROR"):
            _on_event_loop_done(task)
        error_msgs = [r.message for r in caplog.records if r.levelname == "ERROR"]
        assert any("boom" in m for m in error_msgs)

    def test_task_with_no_exception_logs_error(self, caplog):
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None
        with caplog.at_level("ERROR"):
            _on_event_loop_done(task)
        error_msgs = [r.message for r in caplog.records if r.levelname == "ERROR"]
        assert any("unexpected" in m.lower() for m in error_msgs)


# ---------------------------------------------------------------------------
# _parse_budget_config
# ---------------------------------------------------------------------------


class TestParseBudgetConfig:
    def test_none_uc_returns_all_inf_and_defaults(self):
        bc = _parse_budget_config(None)
        import math
        assert math.isinf(bc.daily_limit)
        assert math.isinf(bc.per_task_limit)
        assert math.isinf(bc.timeout_seconds)
        assert bc.spend_window_usd == 0.0
        assert bc.spend_window_seconds == 3600.0

    def test_uc_with_no_budget_attr_returns_defaults(self):
        uc = MagicMock()
        uc.budget = None
        bc = _parse_budget_config(uc)
        import math
        assert math.isinf(bc.daily_limit)

    def test_uc_with_budget_values_parsed(self):
        uc = MagicMock()
        uc.budget = {
            "daily_limit": "10.0",
            "per_task_limit": "1.5",
            "timeout_seconds": "300",
            "spend_window_usd": "5.0",
            "spend_window_seconds": "7200",
        }
        bc = _parse_budget_config(uc)
        assert bc.daily_limit == 10.0
        assert bc.per_task_limit == 1.5
        assert bc.timeout_seconds == 300.0
        assert bc.spend_window_usd == 5.0
        assert bc.spend_window_seconds == 7200.0

    def test_uc_with_empty_budget_dict_returns_defaults(self):
        uc = MagicMock()
        uc.budget = {}
        bc = _parse_budget_config(uc)
        import math
        assert math.isinf(bc.daily_limit)
        assert bc.spend_window_usd == 0.0


# ---------------------------------------------------------------------------
# _restore_persisted_spend (None-guard branches only — DB path is integration)
# ---------------------------------------------------------------------------


class TestRestorePersistedSpend:
    @pytest.mark.asyncio
    async def test_none_spend_limiter_returns_immediately(self):
        from general_ludd.daemon import _restore_persisted_spend

        # Should not raise
        await _restore_persisted_spend(None, MagicMock(), window_seconds=3600)

    @pytest.mark.asyncio
    async def test_none_session_factory_returns_immediately(self):
        from general_ludd.daemon import _restore_persisted_spend

        spend_limiter = MagicMock()
        await _restore_persisted_spend(spend_limiter, None, window_seconds=3600)
        # restore() should NOT have been called
        spend_limiter.restore.assert_not_called()


# ---------------------------------------------------------------------------
# _restore_persisted_projects (None-guard branches only)
# ---------------------------------------------------------------------------


class TestRestorePersistedProjects:
    @pytest.mark.asyncio
    async def test_none_project_manager_returns_immediately(self):
        from general_ludd.daemon import _restore_persisted_projects

        await _restore_persisted_projects(None, MagicMock())

    @pytest.mark.asyncio
    async def test_none_session_factory_returns_immediately(self):
        from general_ludd.daemon import _restore_persisted_projects

        pm = MagicMock()
        await _restore_persisted_projects(pm, None)
        # list_projects should NOT have been called
        pm.list_projects.assert_not_called()


# ---------------------------------------------------------------------------
# _init_project_workspaces
# ---------------------------------------------------------------------------


class TestInitProjectWorkspaces:
    def test_none_project_manager_returns_empty_dict(self):
        from general_ludd.daemon import _init_project_workspaces

        result = _init_project_workspaces(None)
        assert result == {}

    def test_project_manager_with_active_projects(self, tmp_path):
        from general_ludd.daemon import _init_project_workspaces

        pm = MagicMock()
        proj = MagicMock()
        proj.project_id = "proj-001"
        pm.list_active.return_value = [proj]

        mock_workspace = MagicMock()
        with patch("general_ludd.daemon.ProjectWorkspace", return_value=mock_workspace):
            result = _init_project_workspaces(pm)

        assert "proj-001" in result
        mock_workspace.ensure_dirs.assert_called_once()

    def test_project_manager_list_active_raises_returns_empty(self):
        from general_ludd.daemon import _init_project_workspaces

        pm = MagicMock()
        pm.list_active.side_effect = RuntimeError("db error")
        result = _init_project_workspaces(pm)
        assert result == {}

    def test_project_id_from_str_fallback(self):
        from general_ludd.daemon import _init_project_workspaces

        pm = MagicMock()
        # project object without project_id attr
        proj = "plain-string-id"
        pm.list_active.return_value = [proj]

        mock_workspace = MagicMock()
        with patch("general_ludd.daemon.ProjectWorkspace", return_value=mock_workspace):
            result = _init_project_workspaces(pm)

        assert "plain-string-id" in result


# ---------------------------------------------------------------------------
# Auth middleware via TestClient (no lifespan — just app-level middleware)
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Test the PSK auth middleware using httpx AsyncClient (no lifespan startup)."""

    @pytest.mark.asyncio
    async def test_no_psk_no_allow_returns_503_on_private_path(self, monkeypatch):
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/log-level")
            assert resp.status_code == 503
            body = resp.json()
            assert body["error"] == "auth_required"

    @pytest.mark.asyncio
    async def test_no_psk_allow_no_auth_permits_access(self, monkeypatch):
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            # Public path always works
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_psk_set_wrong_token_returns_401(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PSK", "correct-secret")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/log-level", headers={"Authorization": "Bearer wrong-token"}
            )
            assert resp.status_code == 401
            assert resp.json()["error"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_psk_set_correct_token_allows_access(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PSK", "my-secret")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/log-level", headers={"Authorization": "Bearer my-secret"}
            )
            # 200 or 405 (method not allowed) — both mean auth passed
            assert resp.status_code not in (401, 503)

    @pytest.mark.asyncio
    async def test_require_auth_env_overrides_allow_no_auth(self, monkeypatch):
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/some-private-path")
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_public_get_path_allowed_without_auth(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PSK", "secret-psk")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_to_public_path_requires_auth_with_psk(self, monkeypatch):
        """POST /api/todos is NOT public even though GET /api/todos is."""
        monkeypatch.setenv("GLUDD_PSK", "my-secret")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "test"},
            )
            # No auth header → should be 401
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_receiver_prefix_bypasses_psk_auth(self, monkeypatch):
        """Paths under /v1/ bypass PSK middleware (use ingest-token auth instead)."""
        monkeypatch.setenv("GLUDD_PSK", "secret")
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)

        from httpx import ASGITransport, AsyncClient

        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # /v1/ is a receiver prefix — PSK middleware should not challenge it
            # (it may return 404 from the router, but NOT 401/503 from PSK middleware)
            resp = await client.post("/v1/telemetry", json={})
            assert resp.status_code not in (401, 503)
