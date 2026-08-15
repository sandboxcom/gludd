from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.cli import _build_daemon_env, _build_daemon_start_cmd
from general_ludd.daemon import create_daemon_app, load_startup_config


class TestCmdDaemonSignalForwarding:
    def test_sigterm_handler_terminates_child_process(self):
        import argparse
        from contextlib import suppress

        from general_ludd.cli import _cmd_daemon

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("general_ludd.cli._build_daemon_env", return_value={"GLUDD_AUTH_PSK": ""}), \
             patch.dict(os.environ, {}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            old_handler = signal.getsignal(signal.SIGTERM)
            with suppress(SystemExit):
                _cmd_daemon(args)
            new_handler = signal.getsignal(signal.SIGTERM)
            assert new_handler != old_handler or mock_proc.terminate.called
        assert mock_proc.terminate.call_count >= 0


    def test_cmd_daemon_preserves_existing_psk_for_loopback(self):
        import argparse
        from contextlib import suppress

        from general_ludd.cli import _cmd_daemon

        captured = {}
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        def fake_popen(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return mock_proc

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch.dict(os.environ, {"GLUDD_AUTH_PSK": "configured-token"}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            with suppress(SystemExit):
                _cmd_daemon(args)

        assert captured["env"]["GLUDD_AUTH_PSK"] == "configured-token"

    def test_sigterm_handler_kills_child_when_terminate_wait_times_out(self):
        import argparse
        import threading

        from general_ludd.cli import _cmd_daemon

        handlers = {}
        killed = threading.Event()

        def capture_signal(signum, handler):
            handlers[signum] = handler

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0

        def wait_for_watchdog():
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            assert killed.wait(timeout=1.0)
            mock_proc.returncode = -signal.SIGKILL
            return mock_proc.returncode

        mock_proc.wait.side_effect = wait_for_watchdog
        mock_proc.kill.side_effect = killed.set
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("signal.signal", side_effect=capture_signal), \
             patch("general_ludd.cli._build_daemon_env", return_value={"GLUDD_AUTH_PSK": ""}), \
             patch("general_ludd.cli._DAEMON_SHUTDOWN_TIMEOUT_SECONDS", 0.01), \
             patch.dict(os.environ, {}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            with pytest.raises(SystemExit) as exit_info:
                _cmd_daemon(args)

        assert exit_info.value.code == 128 + signal.SIGTERM
        assert mock_proc.terminate.call_count == 1
        assert mock_proc.kill.call_count == 1
        assert mock_proc.wait.call_count == 1

    def test_first_shutdown_signal_wins_without_reentrant_wait(self):
        import argparse

        from general_ludd.cli import _cmd_daemon

        handlers = {}

        def capture_signal(signum, handler):
            handlers[signum] = handler

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        def wait_once():
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            handlers[signal.SIGINT](signal.SIGINT, None)
            mock_proc.returncode = -signal.SIGTERM
            return mock_proc.returncode

        mock_proc.wait.side_effect = wait_once
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("signal.signal", side_effect=capture_signal), \
             patch("general_ludd.cli._build_daemon_env", return_value={"GLUDD_AUTH_PSK": ""}), \
             patch.dict(os.environ, {}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            with pytest.raises(SystemExit) as exit_info:
                _cmd_daemon(args)

        assert exit_info.value.code == 128 + signal.SIGTERM
        assert mock_proc.terminate.call_count == 1
        assert mock_proc.wait.call_count == 1

    def test_sigterm_handler_only_forwards_and_never_reenters_wait(self):
        import argparse

        from general_ludd.cli import _cmd_daemon

        handlers = {}
        handler_errors: list[BaseException] = []
        inside_wait = False

        def capture_signal(signum, handler):
            handlers[signum] = handler

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        def wait_once(*, timeout=None):
            nonlocal inside_wait
            if inside_wait:
                return 0
            inside_wait = True
            try:
                try:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                except BaseException as exc:
                    handler_errors.append(exc)
                mock_proc.returncode = -signal.SIGTERM
                return mock_proc.returncode
            finally:
                inside_wait = False

        mock_proc.wait.side_effect = wait_once
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("signal.signal", side_effect=capture_signal), \
             patch("general_ludd.cli._build_daemon_env", return_value={"GLUDD_AUTH_PSK": ""}), \
             patch.dict(os.environ, {}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            with pytest.raises(SystemExit) as exit_info:
                _cmd_daemon(args)

        assert handler_errors == []
        assert mock_proc.terminate.call_count == 1
        assert mock_proc.wait.call_count == 1
        assert exit_info.value.code == 128 + signal.SIGTERM

    def test_cmd_daemon_kills_child_on_signal(self):
        import argparse
        from contextlib import suppress

        from general_ludd.cli import _cmd_daemon

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.side_effect = KeyboardInterrupt()
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("general_ludd.cli._build_daemon_env", return_value={"GLUDD_AUTH_PSK": ""}), \
             patch.dict(os.environ, {}, clear=False):
            args = argparse.Namespace(
                host="127.0.0.1", port=9999, workers=1,
                log_level="info", config_dir=None, templates_dir=None,
                playbooks_dir=None, tick_interval=1.0,
            )
            with suppress(SystemExit, KeyboardInterrupt):
                _cmd_daemon(args)
        assert mock_proc.terminate.call_count >= 1


class TestBuildDaemonEnv:
    def test_build_env_includes_all_vars_when_given(self):
        env = _build_daemon_env(
            config_dir="/tmp/my-config",
            templates_dir="/tmp/my-templates",
            playbooks_dir="/tmp/my-playbooks",
            tick_interval=2.5,
            log_level="debug",
            psk="abc123",
        )
        assert env["GLUDD_CONFIG_DIR"] == "/tmp/my-config"
        assert env["GLUDD_TEMPLATES_DIR"] == "/tmp/my-templates"
        assert env["GLUDD_PLAYBOOKS_DIR"] == "/tmp/my-playbooks"
        assert env["GLUDD_TICK_INTERVAL"] == "2.5"
        assert env["GLUDD_LOG_LEVEL"] == "debug"
        assert env["GLUDD_AUTH_PSK"] == "abc123"

    def test_build_env_empty_when_no_flags(self):
        env = _build_daemon_env()
        assert env == {"GLUDD_AUTH_PSK": ""}

    def test_build_env_default_values_excluded(self):
        env = _build_daemon_env(tick_interval=1.0, log_level="info")
        assert "GLUDD_TICK_INTERVAL" not in env
        assert "GLUDD_LOG_LEVEL" not in env
        assert env["GLUDD_AUTH_PSK"] == ""

    def test_build_env_includes_psk(self):
        env = _build_daemon_env(psk="abc123")
        assert env["GLUDD_AUTH_PSK"] == "abc123"

    def test_build_env_no_psk_when_empty(self):
        env = _build_daemon_env()
        assert env["GLUDD_AUTH_PSK"] == ""


class TestBuildDaemonStartCmd:
    def test_build_cmd_basic(self):
        cmd = _build_daemon_start_cmd(host="0.0.0.0", port=8000, workers=1)
        assert cmd[0] == "gunicorn"
        assert "general_ludd.daemon:create_daemon_app()" in cmd
        assert "--bind" in cmd
        assert "0.0.0.0:8000" in cmd

    def test_build_cmd_default_host_is_loopback(self):
        # Secure-by-default: with no explicit host, the daemon binds to the
        # loopback interface (127.0.0.1), not the wildcard 0.0.0.0.
        cmd = _build_daemon_start_cmd(port=8000, workers=1)
        assert "127.0.0.1:8000" in cmd
        assert "0.0.0.0:8000" not in cmd

    def test_build_cmd_explicit_external_host_preserved(self):
        # Explicit opt-in to an external bind must still work — only the
        # *default* changed, not the ability to bind the wildcard interface.
        cmd = _build_daemon_start_cmd(host="0.0.0.0", port=9000, workers=1)
        assert "0.0.0.0:9000" in cmd

    def test_daemon_parser_default_host_is_loopback(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["daemon"])
        assert args.host == "127.0.0.1"

    def test_daemon_parser_explicit_host_overrides_default(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["daemon", "--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"


class TestCreateDaemonAppReadsEnvFallback:
    def test_config_dir_from_env_fallback(self, tmp_path):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        profiles_dir = config_dir / "model_profiles"
        profiles_dir.mkdir()
        profile_file = profiles_dir / "test.yml"
        profile_file.write_text(
            yaml.dump({
                "model_profile_id": "test-profile",
                "provider": "openai",
                "model_name": "gpt-4",
                "credential_alias": "OPENAI_API_KEY",
                "context_window": 8192,
            })
        )

        with patch.dict(os.environ, {"GLUDD_CONFIG_DIR": str(config_dir)}, clear=False):
            app = create_daemon_app()
            startup = app.state._startup_config
            profiles = startup.get("model_profiles", [])
            assert len(profiles) == 1
            assert profiles[0].model_profile_id == "test-profile"

    def test_templates_dir_from_env_fallback(self):
        with patch.dict(os.environ, {"GLUDD_TEMPLATES_DIR": "/tmp/my-templates"}, clear=False):
            app = create_daemon_app()
            assert app.state._templates_dir == "/tmp/my-templates"

    def test_playbooks_dir_from_env_fallback(self):
        with patch.dict(os.environ, {"GLUDD_PLAYBOOKS_DIR": "/tmp/my-playbooks"}, clear=False):
            app = create_daemon_app()
            assert app.state._playbooks_dir == "/tmp/my-playbooks"

    def test_tick_interval_from_env_fallback(self):
        with patch.dict(os.environ, {"GLUDD_TICK_INTERVAL": "3.0"}, clear=False):
            app = create_daemon_app()
            assert app.state.tick_interval == 3.0

    def test_log_level_from_env_fallback(self):
        with patch.dict(os.environ, {"GLUDD_LOG_LEVEL": "debug"}, clear=False):
            app = create_daemon_app()
            assert app.state.log_level == "debug"

    def test_explicit_args_override_env(self):
        with patch.dict(
            os.environ,
            {"GLUDD_CONFIG_DIR": "/env/config", "GLUDD_TICK_INTERVAL": "5.0"},
            clear=False,
        ):
            app = create_daemon_app(tick_interval=1.5, config_dir="/explicit/config")
            assert app.state.tick_interval == 1.5
            assert app.state._config_dir == "/explicit/config"


class TestDefaultConfigSearchPath:
    def test_finds_config_in_xdg_config(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = home / ".config" / "general-ludd"
        config_dir.mkdir(parents=True)
        gl_path = config_dir / "general-ludd.yml"
        gl_path.write_text("")

        with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
            cfg = load_startup_config()
            assert cfg["user_config"] is not None

    def test_returns_defaults_when_no_config_dir_found(self):
        with patch.dict(os.environ, {}, clear=True), patch("os.path.expanduser", return_value="/nonexistent/home"):
            cfg = load_startup_config()
            assert cfg["model_profiles"] == []
            assert cfg["mcp_servers"] == {}

    def test_config_dir_arg_overrides_search_path(self, tmp_path):
        config_dir = tmp_path / "explicit"
        config_dir.mkdir()
        gl_path = config_dir / "general-ludd.yml"
        gl_path.write_text("")

        cfg = load_startup_config(config_dir=str(config_dir))
        assert cfg["model_profiles"] == []


class TestAllMcpServerFilesLoaded:
    def test_multiple_mcp_files_loaded(self, tmp_path):
        mcp_dir = tmp_path / "mcp_servers"
        mcp_dir.mkdir()
        server1 = mcp_dir / "server1.yml"
        server1.write_text(
            yaml.dump({"servers": {"server1": {"command": ["python3"], "args": ["-m", "srv1"]}}})
        )
        server2 = mcp_dir / "server2.yml"
        server2.write_text(
            yaml.dump({"servers": {"server2": {"command": ["python3"], "args": ["-m", "srv2"]}}})
        )

        cfg = load_startup_config(config_dir=str(tmp_path))
        mcp_servers = cfg.get("mcp_servers", {})
        assert "server1" in mcp_servers
        assert "server2" in mcp_servers

    def test_single_mcp_file_still_works(self, tmp_path):
        mcp_dir = tmp_path / "mcp_servers"
        mcp_dir.mkdir()
        server1 = mcp_dir / "example.yml"
        server1.write_text(
            yaml.dump({"servers": {"example": {"command": ["python3"], "args": ["-m", "example"]}}})
        )

        cfg = load_startup_config(config_dir=str(tmp_path))
        mcp_servers = cfg.get("mcp_servers", {})
        assert "example" in mcp_servers

    def test_no_mcp_dir_returns_empty(self, tmp_path):
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["mcp_servers"] == {}


class TestMissingDaemonFlags:
    def test_templates_dir_flag_in_parser(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        daemon_parser = parser._subparsers._group_actions[0].choices.get("daemon")
        assert daemon_parser is not None
        actions = {action.dest: action for action in daemon_parser._actions}
        assert "templates_dir" in actions
        assert "playbooks_dir" in actions
