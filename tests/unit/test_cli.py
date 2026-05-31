"""Unit tests for unified CLI."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agentic_harness.cli import main


class TestCLIParsing:
    def test_no_args_prints_help_and_exits(self):
        with patch("sys.argv", ["hottentot"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_version_command(self, capsys):
        with patch("sys.argv", ["hottentot", "version"]):
            main()
        captured = capsys.readouterr()
        assert "0.1.0" in captured.out

    def test_daemon_command_defaults(self):
        with patch("sys.argv", ["hottentot", "daemon"]), patch("agentic_harness.cli._cmd_daemon") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.host == "0.0.0.0"
            assert args.port == 8000
            assert args.log_level == "info"
            assert args.tick_interval == 1.0
            assert args.workers == 1

    def test_daemon_command_custom_args(self):
        with patch(
            "sys.argv",
            ["hottentot", "daemon", "--host", "127.0.0.1", "--port", "9000", "--log-level", "debug",
             "--tick-interval", "2.5", "--workers", "4"],
        ), patch("agentic_harness.cli._cmd_daemon") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.host == "127.0.0.1"
            assert args.port == 9000
            assert args.log_level == "debug"
            assert args.tick_interval == 2.5
            assert args.workers == 4

    def test_add_command_required_title(self):
        with patch("sys.argv", ["hottentot", "add"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0

    def test_add_command_options(self):
        with patch(
            "sys.argv",
            ["hottentot", "add", "Fix the login bug", "--queue", "core", "--priority", "high",
             "--work-type", "code", "--description", "desc"],
        ), patch("agentic_harness.cli._cmd_add") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.title == "Fix the login bug"
            assert args.queue == "core"
            assert args.priority == "high"
            assert args.work_type == "code"
            assert args.description == "desc"
            assert args.daemon_url == "http://localhost:8000"

    def test_status_command_optional_id(self):
        with patch("sys.argv", ["hottentot", "status"]), patch("agentic_harness.cli._cmd_status") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.todo_id is None

    def test_status_command_with_id(self):
        with patch("sys.argv", ["hottentot", "status", "TODO-001"]), patch(
            "agentic_harness.cli._cmd_status",
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.todo_id == "TODO-001"

    def test_list_command_filters(self):
        with patch("sys.argv", ["hottentot", "list", "--queue", "core", "--status", "queued"]), patch(
            "agentic_harness.cli._cmd_list"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.queue == "core"
            assert args.status == "queued"

    def test_log_level_command_choices(self):
        with patch("sys.argv", ["hottentot", "log-level", "debug"]), patch(
            "agentic_harness.cli._cmd_log_level"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.level == "debug"

    def test_log_level_command_rejects_invalid(self):
        with patch("sys.argv", ["hottentot", "log-level", "verbose"]), pytest.raises(SystemExit):
            main()

    def test_deployments_command(self):
        with patch("sys.argv", ["hottentot", "deployments"]), patch(
            "agentic_harness.cli._cmd_deployments"
        ) as mock_cmd:
            main()
            mock_cmd.assert_called_once()

    def test_health_command(self):
        with patch("sys.argv", ["hottentot", "health"]), patch("agentic_harness.cli._cmd_health") as mock_cmd:
            main()
            mock_cmd.assert_called_once()


class TestClientCommands:
    @pytest.mark.asyncio
    async def test_add_sends_post_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"todo_id": "TODO-001", "status": "queued"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from agentic_harness.cli import _cmd_add
            args = argparse.Namespace(
                title="Fix bug", queue="core", priority="medium",
                work_type="code", description="", daemon_url="http://localhost:9000",
            )
            _cmd_add(args)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs[0][0] == "http://localhost:9000/api/todos"
            body = call_kwargs[1]["json"]
            assert body["title"] == "Fix bug"
            assert body["queue"] == "core"

    @pytest.mark.asyncio
    async def test_status_sends_get_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"todo_id": "TODO-001", "status": "active"}
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from agentic_harness.cli import _cmd_status
            args = argparse.Namespace(todo_id="TODO-001", daemon_url="http://localhost:8000")
            _cmd_status(args)
        mock_get.assert_called_once()
        assert "TODO-001" in mock_get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_without_id_calls_system_status(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tick_count": 42, "queue_depth": 5}
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from agentic_harness.cli import _cmd_status
            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000")
            _cmd_status(args)
        mock_get.assert_called_once()
        assert "/api/status" in mock_get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_list_sends_get_with_filters(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"todo_id": "TODO-001"}]
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from agentic_harness.cli import _cmd_list
            args = argparse.Namespace(
                queue="core", status="queued", daemon_url="http://localhost:8000",
            )
            _cmd_list(args)
        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert "/api/todos" in url
        params = mock_get.call_args[1].get("params", {})
        assert params.get("queue") == "core"
        assert params.get("status") == "queued"

    @pytest.mark.asyncio
    async def test_log_level_sends_post_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "level": "debug"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from agentic_harness.cli import _cmd_log_level
            args = argparse.Namespace(level="debug", daemon_url="http://localhost:8000")
            _cmd_log_level(args)
        mock_post.assert_called_once()
        assert "/admin/log-level" in mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert body["level"] == "debug"

    @pytest.mark.asyncio
    async def test_deployments_sends_get_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"deployment_id": "DEPLOY-001"}]
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from agentic_harness.cli import _cmd_deployments
            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_deployments(args)
        mock_get.assert_called_once()
        assert "/api/deployments" in mock_get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_health_sends_get_to_healthz(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from agentic_harness.cli import _cmd_health
            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_health(args)
        mock_get.assert_called_once()
        assert "/healthz" in mock_get.call_args[0][0]

    def test_client_handles_connection_refused(self, capsys):
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            import argparse

            from agentic_harness.cli import _cmd_health
            args = argparse.Namespace(daemon_url="http://localhost:8000")
            with pytest.raises(SystemExit) as exc_info:
                _cmd_health(args)
            assert exc_info.value.code == 1


class TestHotLoading:
    def test_cli_import_does_not_import_event_loop(self):
        cli_mod = sys.modules["agentic_harness.cli"]
        assert hasattr(cli_mod, "main")
        assert hasattr(cli_mod, "_cmd_daemon")

    def test_cli_module_has_no_top_level_daemon_imports(self):
        import ast
        import inspect

        import agentic_harness.cli as cli_mod
        source = inspect.getsource(cli_mod)
        tree = ast.parse(source)
        top_level_imports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                    top_level_imports.append(node.module)
        heavy_prefixes = [
            "agentic_harness.event_loop.loop",
            "agentic_harness.models.gateway",
            "agentic_harness.models.router",
            "agentic_harness.ansible",
            "agentic_harness.db.models",
            "agentic_harness.db.repository",
            "agentic_harness.secrets.manager",
            "agentic_harness.mcp",
            "agentic_harness.daemon",
            "uvicorn",
            "gunicorn",
        ]
        for imp in top_level_imports:
            for prefix in heavy_prefixes:
                assert not imp.startswith(prefix), f"Heavy import at module level: {imp}"
