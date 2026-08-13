"""Unit tests for unified CLI."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.cli import main


class TestCLIParsing:
    def test_no_args_prints_help_and_exits(self):
        with patch("sys.argv", ["gludd"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_version_command(self, capsys):
        with patch("sys.argv", ["gludd", "version"]):
            main()
        captured = capsys.readouterr()
        assert "0.1.0" in captured.out

    def test_daemon_command_defaults(self):
        with patch("sys.argv", ["gludd", "daemon"]), patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            # Secure-by-default: the daemon binds to loopback (127.0.0.1), not the
            # wildcard 0.0.0.0 (host-bind hardening, commit 2066541). External
            # binds remain available via an explicit --host 0.0.0.0 opt-in.
            assert args.host == "127.0.0.1"
            assert args.port == 8000
            assert args.log_level == "info"
            assert args.tick_interval == 1.0
            assert args.workers == 1

    def test_daemon_command_custom_args(self):
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "daemon",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9000",
                    "--log-level",
                    "debug",
                    "--tick-interval",
                    "2.5",
                    "--workers",
                    "4",
                ],
            ),
            patch("general_ludd.cli._cmd_daemon") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.host == "127.0.0.1"
            assert args.port == 9000
            assert args.log_level == "debug"
            assert args.tick_interval == 2.5
            assert args.workers == 4

    def test_add_command_required_title(self):
        with patch("sys.argv", ["gludd", "add"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0

    def test_add_command_options(self):
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "add",
                    "Fix the login bug",
                    "--queue",
                    "core",
                    "--priority",
                    "high",
                    "--work-type",
                    "code",
                    "--description",
                    "desc",
                ],
            ),
            patch("general_ludd.cli._cmd_add") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.title == "Fix the login bug"
            assert args.queue == "core"
            assert args.priority == "high"
            assert args.work_type == "code"
            assert args.description == "desc"
            assert args.daemon_url == "http://localhost:8000"

    def test_status_command_optional_id(self):
        with patch("sys.argv", ["gludd", "status"]), patch("general_ludd.cli._cmd_status") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.todo_id is None

    def test_status_command_with_id(self):
        with (
            patch("sys.argv", ["gludd", "status", "TODO-001"]),
            patch(
                "general_ludd.cli._cmd_status",
            ) as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.todo_id == "TODO-001"

    def test_list_command_filters(self):
        with (
            patch("sys.argv", ["gludd", "list", "--queue", "core", "--status", "queued"]),
            patch("general_ludd.cli._cmd_list") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.queue == "core"
            assert args.status == "queued"

    def test_log_level_command_choices(self):
        with patch("sys.argv", ["gludd", "log-level", "debug"]), patch("general_ludd.cli._cmd_log_level") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.level == "debug"

    def test_log_level_command_rejects_invalid(self):
        with patch("sys.argv", ["gludd", "log-level", "verbose"]), pytest.raises(SystemExit):
            main()

    def test_deployments_command(self):
        with patch("sys.argv", ["gludd", "deployments"]), patch("general_ludd.cli._cmd_deployments") as mock_cmd:
            main()
            mock_cmd.assert_called_once()

    def test_health_command(self):
        with patch("sys.argv", ["gludd", "health"]), patch("general_ludd.cli._cmd_health") as mock_cmd:
            main()
            mock_cmd.assert_called_once()

    def test_smoke_parser_is_available_to_programmatic_consumers(self):
        from general_ludd.cli import _cmd_smoke, build_parser

        parser, subcommand_map = build_parser()
        parsed = parser.parse_args(["smoke", "azure", "vm-a100"])

        assert subcommand_map["smoke"].prog == "gludd smoke"
        assert parsed.func is _cmd_smoke
        assert (parsed.provider, parsed.test) == ("azure", "vm-a100")

    def test_models_search_command(self):
        with (
            patch("sys.argv", ["gludd", "models", "search", "llama"]),
            patch("general_ludd.cli._cmd_models_search") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.query == "llama"
            assert args.limit == 20

    def test_models_search_default_query(self):
        with patch("sys.argv", ["gludd", "models", "search"]), patch("general_ludd.cli._cmd_models_search") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.query == ""

    def test_models_downloaded_command(self):
        with (
            patch("sys.argv", ["gludd", "models", "downloaded"]),
            patch("general_ludd.cli._cmd_models_downloaded") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_local_serve_command(self):
        with (
            patch("sys.argv", ["gludd", "local-serve", "--model", "llama-7b", "--engine", "vllm"]),
            patch("general_ludd.cli._cmd_local_serve") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.model == "llama-7b"
            assert args.engine == "vllm"

    def test_compute_azure_preflight_parses_accelerator_shape(self):
        with patch(
            "sys.argv",
            [
                "gludd",
                "compute",
                "azure-preflight",
                "--gpu",
                "a100_80",
                "--gpu-count",
                "2",
                "--region",
                "westus3",
            ],
        ), patch("general_ludd.cli._cmd_compute_azure_preflight") as mock_cmd:
            main()

        args = mock_cmd.call_args[0][0]
        assert args.gpu == "a100_80"
        assert args.gpu_count == 2
        assert args.region == "westus3"
        assert args.daemon_url == "http://localhost:8000"


class TestClientCommands:
    @pytest.mark.asyncio
    async def test_add_sends_post_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"todo_id": "TODO-001", "status": "queued"}
        with patch("httpx.post", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_add

            args = argparse.Namespace(
                title="Fix bug",
                queue="core",
                priority="medium",
                work_type="code",
                description="",
                daemon_url="http://localhost:9000",
            )
            _cmd_add(args)
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "http://localhost:9000/api/todos"
            body = call_args[1]["json"]
            assert body["title"] == "Fix bug"
            assert body["queue"] == "core"

    @pytest.mark.asyncio
    async def test_status_sends_get_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"todo_id": "TODO-001", "status": "active"}
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from general_ludd.cli import _cmd_status

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

            from general_ludd.cli import _cmd_status

            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000")
            _cmd_status(args)
        mock_get.assert_called_once()
        assert "/api/status" in mock_get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_renders_new_host_path_keys(self, capsys):
        """_cmd_status must use config_file_count + filestore_available (wave3 host-path change)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "version": "0.1.0",
            "config_file_count": 3,
            "filestore_available": True,
            "queue_depths": {},
        }
        with patch("httpx.get", return_value=mock_response):
            import argparse

            from general_ludd.cli import _cmd_status

            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000", project=None)
            _cmd_status(args)
        captured = capsys.readouterr()
        assert "Config files: 3" in captured.out
        assert "available" in captured.out
        # old keys must NOT appear
        assert "config_dir" not in captured.out
        assert "filestore_root" not in captured.out

    @pytest.mark.asyncio
    async def test_status_renders_filestore_unavailable(self, capsys):
        """_cmd_status shows 'unavailable' when filestore_available is False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "version": "0.1.0",
            "config_file_count": 0,
            "filestore_available": False,
            "queue_depths": {},
        }
        with patch("httpx.get", return_value=mock_response):
            import argparse

            from general_ludd.cli import _cmd_status

            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000", project=None)
            _cmd_status(args)
        captured = capsys.readouterr()
        assert "Config files: 0" in captured.out
        assert "unavailable" in captured.out

    @pytest.mark.asyncio
    async def test_status_renders_unknown_when_keys_missing(self, capsys):
        """_cmd_status shows 'unknown' for config_file_count when key absent."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"version": "0.1.0", "queue_depths": {}}
        with patch("httpx.get", return_value=mock_response):
            import argparse

            from general_ludd.cli import _cmd_status

            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000", project=None)
            _cmd_status(args)
        captured = capsys.readouterr()
        assert "Config files: unknown" in captured.out

    @pytest.mark.asyncio
    async def test_list_sends_get_with_filters(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"todo_id": "TODO-001"}]
        with patch("httpx.get", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_list

            args = argparse.Namespace(
                queue="core",
                status="queued",
                daemon_url="http://localhost:8000",
            )
            _cmd_list(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/api/todos" in call_args[0][0]
        params = call_args[1].get("params", {})
        assert params.get("queue") == "core"
        assert params.get("status") == "queued"

    @pytest.mark.asyncio
    async def test_log_level_sends_post_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "level": "debug"}
        with patch("httpx.post", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_log_level

            args = argparse.Namespace(level="debug", daemon_url="http://localhost:8000")
            _cmd_log_level(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/admin/log-level" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["level"] == "debug"

    @pytest.mark.asyncio
    async def test_deployments_sends_get_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"deployment_id": "DEPLOY-001"}]
        with patch("httpx.get", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_deployments

            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_deployments(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/api/deployments" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_health_sends_get_to_healthz(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}
        with patch("httpx.get", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_health

            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_health(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/healthz" in call_args[0][0]

    def test_client_handles_connection_refused(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")):
            import argparse

            from general_ludd.cli import _cmd_health

            args = argparse.Namespace(daemon_url="http://localhost:8000")
            with pytest.raises(SystemExit) as exc_info:
                _cmd_health(args)
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "daemon" in (captured.err + captured.out).lower()

    def test_compute_unregister_sends_delete_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"removed": "test-ep"}
        with patch("httpx.delete", return_value=mock_response) as mock_delete:
            import argparse

            from general_ludd.cli import _cmd_compute_unregister

            args = argparse.Namespace(endpoint_id="test-ep", daemon_url="http://localhost:8000")
            _cmd_compute_unregister(args)
        mock_delete.assert_called_once()
        assert "/admin/compute/endpoints/test-ep" in mock_delete.call_args[0][0]

    def test_status_connection_error_message(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")):
            import argparse

            from general_ludd.cli import _cmd_status

            args = argparse.Namespace(todo_id=None, daemon_url="http://localhost:8000")
            _cmd_status(args)
        captured = capsys.readouterr()
        assert "General Ludd Agent" in captured.out
        assert "Filestore:" in captured.out
        assert "Database:" in captured.out

    def test_add_connection_error_message(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
            import argparse

            from general_ludd.cli import _cmd_add

            args = argparse.Namespace(
                title="test",
                queue="core",
                priority="medium",
                work_type="code",
                description="",
                daemon_url="http://localhost:8000",
            )
            with pytest.raises(SystemExit) as exc_info:
                _cmd_add(args)
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "daemon" in (captured.err + captured.out).lower()

    def test_models_search_sends_post_to_daemon(self, capsys):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [{"model_id": "meta-llama/Llama-3-8B", "pipeline_tag": "text-generation", "downloads": 100000}]
        }
        with patch("httpx.post", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_models_search

            args = argparse.Namespace(query="llama", limit=10, daemon_url="http://localhost:8000")
            _cmd_models_search(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/admin/models/search" in call_args[0][0]

    def test_models_downloaded_sends_get_to_daemon(self, capsys):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        with patch("httpx.get", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_models_downloaded

            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_models_downloaded(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/admin/models/downloaded" in call_args[0][0]

    def test_local_serve_sends_post_to_daemon(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"server_id": "local-0", "endpoint_url": "http://localhost:8001/v1"}
        with patch("httpx.post", return_value=mock_response) as mock_req:
            import argparse

            from general_ludd.cli import _cmd_local_serve

            args = argparse.Namespace(
                engine="vllm",
                model="llama-7b",
                host="localhost",
                port=8001,
                gpu_layers=-1,
                context_size=4096,
                daemon_url="http://localhost:8000",
            )
            _cmd_local_serve(args)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "/admin/models/local/serve" in call_args[0][0]


class TestHotLoading:
    def test_cli_import_does_not_import_event_loop(self):
        cli_mod = sys.modules["general_ludd.cli"]
        assert hasattr(cli_mod, "main")
        assert hasattr(cli_mod, "_cmd_daemon")

    def test_cli_module_has_no_top_level_daemon_imports(self):
        import ast
        import inspect

        import general_ludd.cli as cli_mod

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
            "general_ludd.event_loop.loop",
            "general_ludd.models.gateway",
            "general_ludd.models.router",
            "general_ludd.ansible",
            "general_ludd.db.models",
            "general_ludd.db.repository",
            "general_ludd.secrets.manager",
            "general_ludd.mcp",
            "general_ludd.daemon",
            "uvicorn",
            "gunicorn",
        ]
        for imp in top_level_imports:
            for prefix in heavy_prefixes:
                assert not imp.startswith(prefix), f"Heavy import at module level: {imp}"

    def test_models_no_subcommand_prints_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "models"]):
            main()
        assert exc_info.value.code == 0

    def test_model_performance_command(self):
        with (
            patch("sys.argv", ["gludd", "models", "performance"]),
            patch("general_ludd.cli._cmd_model_performance") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.service is None
            assert args.task_type is None

    def test_model_performance_command_with_filters(self):
        with (
            patch("sys.argv", ["gludd", "models", "performance", "--service", "openai", "--task-type", "code"]),
            patch("general_ludd.cli._cmd_model_performance") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.service == "openai"
            assert args.task_type == "code"

    def test_model_ranking_command(self):
        with (
            patch("sys.argv", ["gludd", "models", "ranking", "--task-type", "code", "--strategy", "quality"]),
            patch("general_ludd.cli._cmd_model_ranking") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.task_type == "code"
            assert args.strategy == "quality"

    def test_model_router_status_command(self):
        with (
            patch("sys.argv", ["gludd", "models", "router-status"]),
            patch("general_ludd.cli._cmd_model_router_status") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_model_router_set_command(self):
        argv = ["gludd", "models", "router-set", "--task-type", "code", "--strategy", "cheapest"]
        with patch("sys.argv", argv), patch("general_ludd.cli._cmd_model_router_set") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.task_type == "code"
            assert args.strategy == "cheapest"

    def test_mcp_no_subcommand_prints_help(self):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "mcp"]):
            main()
        assert exc_info.value.code == 0

    def test_skills_no_subcommand_prints_help(self):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "skills"]):
            main()
        assert exc_info.value.code == 0

    def test_compute_no_subcommand_prints_help(self):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "compute"]):
            main()
        assert exc_info.value.code == 0

    def test_compute_unregister_parsing(self):
        with (
            patch.object(sys, "argv", ["gludd", "compute", "unregister", "my-endpoint"]),
            patch("general_ludd.cli._cmd_compute_unregister") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.endpoint_id == "my-endpoint"
