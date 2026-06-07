"""End-to-end tests for ALL CLI commands — parsing, daemon calls, error handling."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx

from general_ludd.cli import main


def _run_cli(args: list[str]) -> int:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            main()
        return 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 1


def _run_cli_output(args: list[str], capsys) -> tuple[str, str, int]:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            main()
        captured = capsys.readouterr()
        return captured.out, captured.err, 0
    except SystemExit as exc:
        captured = capsys.readouterr()
        return captured.out, captured.err, exc.code if exc.code is not None else 1


class TestDaemonParsingE2E:
    def test_daemon_default_args(self):
        with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            _run_cli(["daemon"])
        args = mock_cmd.call_args[0][0]
        assert args.host == "0.0.0.0"
        assert args.port == 8000
        assert args.log_level == "info"
        assert args.tick_interval == 1.0
        assert args.workers == 1
        assert args.project is None
        assert args.config_dir is None

    def test_daemon_custom_args(self):
        with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            _run_cli([
                "daemon", "--host", "127.0.0.1", "--port", "9000",
                "--log-level", "debug", "--tick-interval", "2.5", "--workers", "8",
                "--project", "proj-abc", "--config-dir", "/etc/gludd",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.log_level == "debug"
        assert args.tick_interval == 2.5
        assert args.workers == 8
        assert args.project == "proj-abc"
        assert args.config_dir == "/etc/gludd"

    def test_daemon_invalid_log_level(self):
        exit_code = _run_cli(["daemon", "--log-level", "verbose"])
        assert exit_code != 0

    def test_daemon_log_level_choices(self):
        for level in ["debug", "info", "warning", "error"]:
            with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
                _run_cli(["daemon", "--log-level", level])
            assert mock_cmd.call_args[0][0].log_level == level


class TestAddE2E:
    def test_add_parsing_all_flags(self):
        with patch("general_ludd.cli._cmd_add") as mock_cmd:
            _run_cli([
                "add", "Fix bug", "--queue", "review", "--priority", "high",
                "--work-type", "bug_fix", "--description", "fix it",
                "--project", "proj-1", "--daemon-url", "http://localhost:9000",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.title == "Fix bug"
        assert args.queue == "review"
        assert args.priority == "high"
        assert args.work_type == "bug_fix"
        assert args.description == "fix it"
        assert args.project == "proj-1"
        assert args.daemon_url == "http://localhost:9000"

    def test_add_success(self, capsys):
        mock_resp_json = MagicMock(return_value={"todo_id": "X", "status": "queued"})
        mock_resp = MagicMock(status_code=201, json=mock_resp_json)
        with patch("httpx.post", return_value=mock_resp):
            _run_cli_output(["add", "Task"], capsys)
        mock_resp_json.assert_called()

    def test_add_with_project_sends_payload(self):
        mock_resp = MagicMock(status_code=201, json=lambda: {"todo_id": "X"})
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _run_cli(["add", "Task", "--project", "proj-99"])
        body = mock_post.call_args[1]["json"]
        assert body["project_id"] == "proj-99"

    def test_add_error_response(self):
        mock_resp = MagicMock(status_code=400, text="Bad request")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["add", "Task"])
        assert exit_code == 1

    def test_add_missing_title(self):
        exit_code = _run_cli(["add"])
        assert exit_code != 0

    def test_add_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            out, err, code = _run_cli_output(["add", "Task"], capsys)
        assert code == 1
        assert ("daemon" in (err + out).lower()) or ("refused" in (err + out).lower())

    def test_add_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["add", "Task"], capsys)
        assert code == 1


class TestStatusE2E:
    def test_status_parsing_no_args(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id is None
        assert args.project is None

    def test_status_parsing_with_todo_id(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "TODO-001"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id == "TODO-001"

    def test_status_parsing_with_project(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "--project", "proj-1"])
        args = mock_cmd.call_args[0][0]
        assert args.project == "proj-1"

    def test_status_system_status_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "version": "0.1.0",
            "uptime_ticks": 10,
            "todos_total": 5,
            "queue_depths": {"core": 3, "qa": 2},
            "tick_metrics": {"todos_dispatched": 8},
            "config_dir": "/etc/gludd",
            "config_files": [],
            "filestore_root": "/tmp",
            "filestore_binaries": [],
            "db_engine": "sqlite",
            "db_url": "sqlite:///gludd.db",
        })
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "v0.1.0" in out
        assert "Todos" in out

    def test_status_todo_detail_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"todo_id": "TODO-001", "title": "Fix"})
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["status", "TODO-001"], capsys)
        assert code == 0
        assert "/api/todos/TODO-001" in mock_get.call_args[0][0]
        assert "TODO-001" in out

    def test_status_todo_not_found(self):
        mock_resp = MagicMock(status_code=404, text="not found")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["status", "MISSING"])
        assert exit_code == 1

    def test_status_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out

    def test_status_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out


class TestListE2E:
    def test_list_parsing_all_filters(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run_cli([
                "list", "--queue", "core", "--status", "active",
                "--project", "proj-1",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.queue == "core"
        assert args.status == "active"
        assert args.project == "proj-1"

    def test_list_parsing_no_filters(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run_cli(["list"])
        args = mock_cmd.call_args[0][0]
        assert args.queue is None
        assert args.status is None
        assert args.project is None

    def test_list_daemon_success_with_filters(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: [{"todo_id": "X"}])
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _out, _err, code = _run_cli_output(
                ["list", "--queue", "dev", "--status", "queued"], capsys,
            )
        assert code == 0
        params = mock_get.call_args[1].get("params", {})
        assert params.get("queue") == "dev"
        assert params.get("status") == "queued"

    def test_list_daemon_success_with_project(self):
        mock_resp = MagicMock(status_code=200, json=lambda: [])
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _run_cli(["list", "--project", "proj-7"])
        params = mock_get.call_args[1].get("params", {})
        assert params.get("project_id") == "proj-7"

    def test_list_empty_results(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: [])
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["list"], capsys)
        assert code == 0

    def test_list_error_response(self):
        mock_resp = MagicMock(status_code=500, text="server error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["list"])
        assert exit_code == 1

    def test_list_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["list"], capsys)
        assert code == 1

    def test_list_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["list"], capsys)
        assert code == 1


class TestLogLevelE2E:
    def test_log_level_parsing_all_choices(self):
        for level in ["debug", "info", "warning", "error"]:
            with patch("general_ludd.cli._cmd_log_level") as mock_cmd:
                _run_cli(["log-level", level])
            assert mock_cmd.call_args[0][0].level == level

    def test_log_level_invalid_choice(self):
        exit_code = _run_cli(["log-level", "verbose"])
        assert exit_code != 0

    def test_log_level_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"status": "ok", "level": "debug"})
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(["log-level", "debug"], capsys)
        assert code == 0
        assert "/admin/log-level" in mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert body["level"] == "debug"
        assert "debug" in out.lower()

    def test_log_level_error_response(self):
        mock_resp = MagicMock(status_code=500, text="internal error")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["log-level", "debug"])
        assert exit_code == 1

    def test_log_level_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["log-level", "debug"], capsys)
        assert code == 1

    def test_log_level_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["log-level", "debug"], capsys)
        assert code == 1


class TestDeploymentsE2E:
    def test_deployments_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: [{"deployment_id": "DEPLOY-001", "provider": "aws"}],
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["deployments"], capsys)
        assert code == 0
        assert "/api/deployments" in mock_get.call_args[0][0]
        assert "DEPLOY-001" in out

    def test_deployments_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: [])
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["deployments"], capsys)
        assert code == 0

    def test_deployments_error_response(self):
        mock_resp = MagicMock(status_code=503, text="unavailable")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["deployments"])
        assert exit_code == 1

    def test_deployments_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["deployments"], capsys)
        assert code == 1

    def test_deployments_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["deployments"], capsys)
        assert code == 1


class TestVersionE2E:
    def test_version_output(self, capsys):
        out, _err, code = _run_cli_output(["version"], capsys)
        assert code == 0
        assert "general-ludd-agent" in out
        assert "0.1.0" in out

    def test_version_no_args(self, capsys):
        out, _err, _code = _run_cli_output(["version"], capsys)
        assert "0.1.0" in out


class TestHealthE2E:
    def test_health_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"status": "healthy"})
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["health"], capsys)
        assert code == 0
        assert "/healthz" in mock_get.call_args[0][0]
        assert "healthy" in out

    def test_health_error_response(self):
        mock_resp = MagicMock(status_code=503, text="unavailable")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["health"])
        assert exit_code == 1

    def test_health_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["health"], capsys)
        assert code == 1

    def test_health_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["health"], capsys)
        assert code == 1


class TestModelsE2E:
    def test_models_search_parsing(self):
        with patch("general_ludd.cli._cmd_models_search") as mock_cmd:
            _run_cli(["models", "search", "bert", "--limit", "5"])
        args = mock_cmd.call_args[0][0]
        assert args.query == "bert"
        assert args.limit == 5

    def test_models_search_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_models_search") as mock_cmd:
            _run_cli(["models", "search"])
        args = mock_cmd.call_args[0][0]
        assert args.query == ""
        assert args.limit == 20

    def test_models_search_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "model_id": "bert-base",
                        "pipeline_tag": "fill-mask",
                        "downloads": 50000,
                    }
                ]
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(["models", "search", "bert"], capsys)
        assert code == 0
        assert "/admin/models/search" in mock_post.call_args[0][0]
        assert "bert-base" in out
        assert "fill-mask" in out

    def test_models_search_no_results(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"results": []})
        with patch("httpx.post", return_value=mock_resp):
            out, _err, code = _run_cli_output(["models", "search", "zzznonexistent"], capsys)
        assert code == 0
        assert "No models found" in out

    def test_models_search_error_response(self):
        mock_resp = MagicMock(status_code=502, text="bad gateway")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["models", "search", "bert"])
        assert exit_code == 1

    def test_models_search_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["models", "search", "bert"], capsys)
        assert code == 1

    def test_models_search_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["models", "search", "bert"], capsys)
        assert code == 1

    def test_models_downloaded_parsing(self):
        with patch("general_ludd.cli._cmd_models_downloaded") as mock_cmd:
            _run_cli(["models", "downloaded"])
        mock_cmd.assert_called_once()

    def test_models_downloaded_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "models": [
                    {
                        "model_id": "llama-7b",
                        "local_path": "/models/llama-7b",
                        "engine": "vllm",
                    }
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["models", "downloaded"], capsys)
        assert code == 0
        assert "/admin/models/downloaded" in mock_get.call_args[0][0]
        assert "llama-7b" in out
        assert "/models/llama-7b" in out

    def test_models_downloaded_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"models": []})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["models", "downloaded"], capsys)
        assert code == 0
        assert "No models downloaded" in out

    def test_models_downloaded_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["models", "downloaded"])
        assert exit_code == 1

    def test_models_downloaded_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["models", "downloaded"], capsys)
        assert code == 1

    def test_models_downloaded_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["models", "downloaded"], capsys)
        assert code == 1


class TestLocalServeE2E:
    def test_local_serve_parsing_all_flags(self):
        with patch("general_ludd.cli._cmd_local_serve") as mock_cmd:
            _run_cli([
                "local-serve", "--engine", "llamacpp", "--model", "llama-7b",
                "--host", "0.0.0.0", "--port", "8080", "--gpu-layers", "32",
                "--context-size", "8192", "--daemon-url", "http://localhost:9000",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.engine == "llamacpp"
        assert args.model == "llama-7b"
        assert args.host == "0.0.0.0"
        assert args.port == 8080
        assert args.gpu_layers == 32
        assert args.context_size == 8192
        assert args.daemon_url == "http://localhost:9000"

    def test_local_serve_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_local_serve") as mock_cmd:
            _run_cli(["local-serve", "--model", "llama-7b"])
        args = mock_cmd.call_args[0][0]
        assert args.engine == "vllm"
        assert args.host == "localhost"
        assert args.port == 8001
        assert args.gpu_layers == -1
        assert args.context_size == 4096

    def test_local_serve_model_required(self):
        exit_code = _run_cli(["local-serve"])
        assert exit_code != 0

    def test_local_serve_invalid_engine(self):
        exit_code = _run_cli(["local-serve", "--engine", "invalid", "--model", "x"])
        assert exit_code != 0

    def test_local_serve_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "server_id": "local-0",
                "endpoint_url": "http://localhost:8001/v1",
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(
                ["local-serve", "--model", "llama-7b"], capsys,
            )
        assert code == 0
        assert "/admin/local-inference/start" in mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert body["model_name"] == "llama-7b"
        assert "local-0" in out

    def test_local_serve_error_response(self):
        mock_resp = MagicMock(status_code=500, text="start failed")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["local-serve", "--model", "x"])
        assert exit_code == 1

    def test_local_serve_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(
                ["local-serve", "--model", "x"], capsys,
            )
        assert code == 1

    def test_local_serve_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(
                ["local-serve", "--model", "x"], capsys,
            )
        assert code == 1


class TestMCPE2E:
    def test_mcp_search_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_mcp_search") as mock_cmd:
            _run_cli(["mcp", "search"])
        args = mock_cmd.call_args[0][0]
        assert args.query == ""

    def test_mcp_search_parsing_with_query(self):
        with patch("general_ludd.cli._cmd_mcp_search") as mock_cmd:
            _run_cli(["mcp", "search", "github"])
        args = mock_cmd.call_args[0][0]
        assert args.query == "github"

    def test_mcp_search_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "server_name": "github",
                        "description": "GitHub API",
                        "source": "official",
                    }
                ]
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(["mcp", "search", "github"], capsys)
        assert code == 0
        assert "/admin/mcp/catalog/search" in mock_post.call_args[0][0]
        assert "github" in out

    def test_mcp_search_no_results(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"results": []})
        with patch("httpx.post", return_value=mock_resp):
            out, _err, code = _run_cli_output(["mcp", "search", "nothing"], capsys)
        assert code == 0
        assert "No MCP servers found" in out

    def test_mcp_search_error_response(self):
        mock_resp = MagicMock(status_code=503, text="error")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["mcp", "search", "x"])
        assert exit_code == 1

    def test_mcp_search_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["mcp", "search", "x"], capsys)
        assert code == 1

    def test_mcp_search_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["mcp", "search", "x"], capsys)
        assert code == 1

    def test_mcp_list_parsing(self):
        with patch("general_ludd.cli._cmd_mcp_list") as mock_cmd:
            _run_cli(["mcp", "list"])
        mock_cmd.assert_called_once()

    def test_mcp_list_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "servers": [
                    {"server_name": "filesystem"},
                    {"server_name": "postgres"},
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["mcp", "list"], capsys)
        assert code == 0
        assert "/admin/mcp/catalog/servers" in mock_get.call_args[0][0]
        assert "filesystem" in out
        assert "postgres" in out

    def test_mcp_list_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"servers": []})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["mcp", "list"], capsys)
        assert code == 0
        assert "No MCP" in out

    def test_mcp_list_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["mcp", "list"])
        assert exit_code == 1

    def test_mcp_list_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["mcp", "list"], capsys)
        assert code == 1

    def test_mcp_list_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["mcp", "list"], capsys)
        assert code == 1

    def test_mcp_info_parsing(self):
        with patch("general_ludd.cli._cmd_mcp_info") as mock_cmd:
            _run_cli(["mcp", "info", "github"])
        args = mock_cmd.call_args[0][0]
        assert args.name == "github"

    def test_mcp_info_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "server": {
                    "server_name": "github",
                    "description": "GitHub API integration",
                }
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["mcp", "info", "github"], capsys)
        assert code == 0
        assert "/admin/mcp/catalog/servers/github" in mock_get.call_args[0][0]
        assert "github" in out

    def test_mcp_info_not_found(self):
        mock_resp = MagicMock(status_code=404, text="not found")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["mcp", "info", "nonexistent"])
        assert exit_code == 1

    def test_mcp_info_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["mcp", "info", "github"], capsys)
        assert code == 1

    def test_mcp_info_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["mcp", "info", "github"], capsys)
        assert code == 1


class TestSkillsE2E:
    def test_skills_search_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_skills_search") as mock_cmd:
            _run_cli(["skills", "search"])
        args = mock_cmd.call_args[0][0]
        assert args.query == ""

    def test_skills_search_parsing_with_query(self):
        with patch("general_ludd.cli._cmd_skills_search") as mock_cmd:
            _run_cli(["skills", "search", "security"])
        args = mock_cmd.call_args[0][0]
        assert args.query == "security"

    def test_skills_search_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "name": "security-first",
                        "description": "Security-first approach",
                        "category": "security",
                        "tags": ["security", "audit"],
                    }
                ]
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(
                ["skills", "search", "security"], capsys,
            )
        assert code == 0
        assert "/admin/skills/catalog/search" in mock_post.call_args[0][0]
        assert "security-first" in out

    def test_skills_search_no_results(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"results": []})
        with patch("httpx.post", return_value=mock_resp):
            out, _err, code = _run_cli_output(
                ["skills", "search", "nothing"], capsys,
            )
        assert code == 0
        assert "No skills found" in out

    def test_skills_search_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["skills", "search", "x"])
        assert exit_code == 1

    def test_skills_search_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(
                ["skills", "search", "x"], capsys,
            )
        assert code == 1

    def test_skills_search_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(
                ["skills", "search", "x"], capsys,
            )
        assert code == 1

    def test_skills_list_parsing(self):
        with patch("general_ludd.cli._cmd_skills_list") as mock_cmd:
            _run_cli(["skills", "list"])
        mock_cmd.assert_called_once()

    def test_skills_list_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "skills": [
                    {"name": "tdd-discipline", "description": "TDD"},
                    {"name": "git-conventional", "description": "Git"},
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["skills", "list"], capsys)
        assert code == 0
        assert "/admin/skills/catalog" in mock_get.call_args[0][0]
        assert "tdd-discipline" in out

    def test_skills_list_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["skills", "list"])
        assert exit_code == 1

    def test_skills_list_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["skills", "list"], capsys)
        assert code == 1

    def test_skills_list_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["skills", "list"], capsys)
        assert code == 1

    def test_skills_install_parsing(self):
        with patch("general_ludd.cli._cmd_skills_install") as mock_cmd:
            _run_cli(["skills", "install", "tdd-discipline"])
        args = mock_cmd.call_args[0][0]
        assert args.name == "tdd-discipline"

    def test_skills_install_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "installed": "/etc/general-ludd/skills/tdd-discipline.md",
                "name": "tdd-discipline",
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(
                ["skills", "install", "tdd-discipline"], capsys,
            )
        assert code == 0
        assert "/admin/skills/catalog/install" in mock_post.call_args[0][0]
        assert "tdd-discipline" in out

    def test_skills_install_not_found(self):
        mock_resp = MagicMock(status_code=404, text="not found")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["skills", "install", "nonexistent"])
        assert exit_code == 1

    def test_skills_install_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(["skills", "install", "x"])
        assert exit_code == 1

    def test_skills_install_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(
                ["skills", "install", "x"], capsys,
            )
        assert code == 1

    def test_skills_install_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(
                ["skills", "install", "x"], capsys,
            )
        assert code == 1


class TestComputeE2E:
    def test_compute_endpoints_parsing(self):
        with patch("general_ludd.cli._cmd_compute_endpoints") as mock_cmd:
            _run_cli(["compute", "endpoints"])
        mock_cmd.assert_called_once()

    def test_compute_endpoints_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "endpoints": [
                    {
                        "endpoint_id": "gpu-0",
                        "url": "http://gpu:8000",
                        "model": "llama-7b",
                        "max_concurrent": 4,
                        "utilization_pct": 0,
                        "current_load": 0,
                        "available_slots": 4,
                        "active": True,
                    }
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["compute", "endpoints"], capsys)
        assert code == 0
        assert "/admin/compute/endpoints" in mock_get.call_args[0][0]
        assert "gpu-0" in out

    def test_compute_endpoints_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"endpoints": []})
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["compute", "endpoints"], capsys)
        assert code == 0

    def test_compute_endpoints_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["compute", "endpoints"])
        assert exit_code == 1

    def test_compute_endpoints_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["compute", "endpoints"], capsys)
        assert code == 1

    def test_compute_endpoints_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["compute", "endpoints"], capsys)
        assert code == 1

    def test_compute_register_parsing_all_flags(self):
        with patch("general_ludd.cli._cmd_compute_register") as mock_cmd:
            _run_cli([
                "compute", "register", "--id", "ep-1", "--url", "http://x:8000",
                "--model", "llama-7b", "--max-concurrent", "8",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.id == "ep-1"
        assert args.url == "http://x:8000"
        assert args.model == "llama-7b"
        assert args.max_concurrent == 8

    def test_compute_register_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "endpoint_id": "ep-1",
                "url": "http://gpu:8000",
                "model": "llama-7b",
            },
        )
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            out, _err, code = _run_cli_output(
                ["compute", "register", "--id", "ep-1", "--url", "http://gpu:8000",
                 "--model", "llama-7b"],
                capsys,
            )
        assert code == 0
        assert "/admin/compute/endpoints" in mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert body["id"] == "ep-1"
        assert "ep-1" in out

    def test_compute_register_error_response(self):
        mock_resp = MagicMock(status_code=422, text="missing fields")
        with patch("httpx.post", return_value=mock_resp):
            exit_code = _run_cli(
                ["compute", "register", "--id", "x", "--url", "x", "--model", "x"],
            )
        assert exit_code == 1

    def test_compute_register_offline(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(
                ["compute", "register", "--id", "x", "--url", "x", "--model", "x"],
                capsys,
            )
        assert code == 1

    def test_compute_register_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(
                ["compute", "register", "--id", "x", "--url", "x", "--model", "x"],
                capsys,
            )
        assert code == 1

    def test_compute_unregister_parsing(self):
        with patch("general_ludd.cli._cmd_compute_unregister") as mock_cmd:
            _run_cli(["compute", "unregister", "ep-to-remove"])
        args = mock_cmd.call_args[0][0]
        assert args.endpoint_id == "ep-to-remove"

    def test_compute_unregister_200_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200, json=lambda: {"removed": "ep-x"},
        )
        with patch("httpx.delete", return_value=mock_resp) as mock_delete:
            _out, _err, code = _run_cli_output(
                ["compute", "unregister", "ep-x"], capsys,
            )
        assert code == 0
        assert "/admin/compute/endpoints/ep-x" in mock_delete.call_args[0][0]

    def test_compute_unregister_204_success(self, capsys):
        mock_resp = MagicMock(status_code=204, json=lambda: None)
        with patch("httpx.delete", return_value=mock_resp):
            out, _err, code = _run_cli_output(
                ["compute", "unregister", "ep-x"], capsys,
            )
        assert code == 0
        assert "ep-x" in out

    def test_compute_unregister_error_response(self):
        mock_resp = MagicMock(status_code=404, text="not found")
        with patch("httpx.delete", return_value=mock_resp):
            exit_code = _run_cli(["compute", "unregister", "nonexistent"])
        assert exit_code == 1

    def test_compute_unregister_offline(self, capsys):
        with patch("httpx.delete", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(
                ["compute", "unregister", "ep-x"], capsys,
            )
        assert code == 1

    def test_compute_unregister_timeout(self, capsys):
        with patch("httpx.delete", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(
                ["compute", "unregister", "ep-x"], capsys,
            )
        assert code == 1


class TestScoresE2E:
    def test_scores_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_scores") as mock_cmd:
            _run_cli(["scores"])
        args = mock_cmd.call_args[0][0]
        assert args.task_type is None

    def test_scores_parsing_with_task_type(self):
        with patch("general_ludd.cli._cmd_scores") as mock_cmd:
            _run_cli(["scores", "--task-type", "bug_fix"])
        args = mock_cmd.call_args[0][0]
        assert args.task_type == "bug_fix"

    def test_scores_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "scores": [
                    {
                        "prompt_profile_id": "aider_edit",
                        "model_profile_id": "claude-3",
                        "task_type": "bug_fix",
                        "avg_composite_score": 0.85,
                        "sample_count": 10,
                    }
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["scores"], capsys)
        assert code == 0
        assert "/admin/benchmark/scores" in mock_get.call_args[0][0]
        assert "aider_edit" in out or "scores" in out.lower()

    def test_scores_with_task_type_filter(self):
        mock_resp = MagicMock(status_code=200, json=lambda: {"scores": []})
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _run_cli(["scores", "--task-type", "bug_fix"])
        params = mock_get.call_args[1].get("params", {})
        assert params.get("task_type") == "bug_fix"

    def test_scores_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["scores"])
        assert exit_code == 1

    def test_scores_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["scores"], capsys)
        assert code == 1

    def test_scores_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["scores"], capsys)
        assert code == 1


class TestLeaderboardE2E:
    def test_leaderboard_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_leaderboard") as mock_cmd:
            _run_cli(["leaderboard"])
        args = mock_cmd.call_args[0][0]
        assert args.task_type is None

    def test_leaderboard_parsing_with_task_type(self):
        with patch("general_ludd.cli._cmd_leaderboard") as mock_cmd:
            _run_cli(["leaderboard", "--task-type", "feature"])
        args = mock_cmd.call_args[0][0]
        assert args.task_type == "feature"

    def test_leaderboard_success(self, capsys):
        mock_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "leaderboard": [
                    {
                        "prompt_profile_id": "aider_edit",
                        "model_profile_id": "claude-3",
                        "task_type": "bug_fix",
                        "composite_score": 0.92,
                        "avg_cost_usd": 0.001,
                        "sample_count": 15,
                    }
                ]
            },
        )
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["leaderboard"], capsys)
        assert code == 0
        assert "/admin/benchmark/leaderboard" in mock_get.call_args[0][0]
        assert "claude-3" in out

    def test_leaderboard_with_task_type_filter(self):
        mock_resp = MagicMock(status_code=200, json=lambda: {"leaderboard": []})
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _run_cli(["leaderboard", "--task-type", "refactor"])
        params = mock_get.call_args[1].get("params", {})
        assert params.get("task_type") == "refactor"

    def test_leaderboard_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"leaderboard": []})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["leaderboard"], capsys)
        assert code == 0
        assert "No benchmark data" in out or "score" in out.lower()

    def test_leaderboard_error_response(self):
        mock_resp = MagicMock(status_code=500, text="error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["leaderboard"])
        assert exit_code == 1

    def test_leaderboard_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, _err, code = _run_cli_output(["leaderboard"], capsys)
        assert code == 1

    def test_leaderboard_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            _out, _err, code = _run_cli_output(["leaderboard"], capsys)
        assert code == 1


class TestCLISubcommandHelp:
    def test_models_no_subcommand_shows_help(self):
        exit_code = _run_cli(["models"])
        assert exit_code == 0

    def test_mcp_no_subcommand_shows_help(self):
        exit_code = _run_cli(["mcp"])
        assert exit_code == 0

    def test_skills_no_subcommand_shows_help(self):
        exit_code = _run_cli(["skills"])
        assert exit_code == 0

    def test_compute_no_subcommand_shows_help(self):
        exit_code = _run_cli(["compute"])
        assert exit_code == 0


class TestCLIDefaultBehavior:
    def test_no_args_shows_help(self):
        exit_code = _run_cli([])
        assert exit_code == 1

    def test_unknown_command_exits_error(self):
        exit_code = _run_cli(["nonexistent"])
        assert exit_code != 0

    def test_help_flag(self):
        exit_code = _run_cli(["--help"])
        assert exit_code == 0


class TestCLICommandExistence:
    def test_all_daemon_commands_exist(self):
        from general_ludd import cli as cli_mod

        commands = [
            "_cmd_daemon", "_cmd_add", "_cmd_status", "_cmd_list",
            "_cmd_log_level", "_cmd_deployments", "_cmd_version",
            "_cmd_health", "_cmd_models_search", "_cmd_models_downloaded",
            "_cmd_local_serve", "_cmd_mcp_search", "_cmd_mcp_list",
            "_cmd_mcp_info", "_cmd_skills_search", "_cmd_skills_list",
            "_cmd_skills_install", "_cmd_compute_endpoints",
            "_cmd_compute_register", "_cmd_compute_unregister",
            "_cmd_scores", "_cmd_leaderboard",
        ]
        for cmd in commands:
            assert hasattr(cli_mod, cmd), f"Missing CLI command function: {cmd}"


class TestCLIProjectFlag:
    def test_add_with_project_id(self):
        with patch("general_ludd.cli._cmd_add") as mock_cmd:
            _run_cli(["add", "test", "--project", "proj-123"])
        assert mock_cmd.call_args[0][0].project == "proj-123"

    def test_status_with_project_filter(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "--project", "proj-456"])
        assert mock_cmd.call_args[0][0].project == "proj-456"

    def test_list_with_project_filter(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run_cli(["list", "--project", "proj-789"])
        assert mock_cmd.call_args[0][0].project == "proj-789"

    def test_daemon_with_project_flag(self):
        with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            _run_cli(["daemon", "--project", "proj-daemon"])
        assert mock_cmd.call_args[0][0].project == "proj-daemon"


class TestCLITimeoutErrors:
    def test_status_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out

    def test_add_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["add", "test"], capsys)
        assert code == 1

    def test_list_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["list"], capsys)
        assert code == 1

    def test_deployments_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["deployments"], capsys)
        assert code == 1

    def test_health_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["health"], capsys)
        assert code == 1

    def test_log_level_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["log-level", "debug"], capsys)
        assert code == 1

    def test_models_search_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["models", "search"], capsys)
        assert code == 1

    def test_models_downloaded_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["models", "downloaded"], capsys)
        assert code == 1

    def test_mcp_search_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["mcp", "search"], capsys)
        assert code == 1

    def test_mcp_list_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["mcp", "list"], capsys)
        assert code == 1

    def test_mcp_info_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["mcp", "info", "github"], capsys)
        assert code == 1

    def test_skills_search_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["skills", "search"], capsys)
        assert code == 1

    def test_skills_list_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["skills", "list"], capsys)
        assert code == 1

    def test_skills_install_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["skills", "install", "x"], capsys)
        assert code == 1

    def test_compute_endpoints_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["compute", "endpoints"], capsys)
        assert code == 1

    def test_compute_register_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(
                ["compute", "register", "--id", "x", "--url", "x", "--model", "x"],
                capsys,
            )
        assert code == 1

    def test_compute_unregister_request_timeout(self, capsys):
        with patch("httpx.delete", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["compute", "unregister", "x"], capsys)
        assert code == 1

    def test_scores_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["scores"], capsys)
        assert code == 1

    def test_leaderboard_request_timeout(self, capsys):
        with patch("httpx.get", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(["leaderboard"], capsys)
        assert code == 1

    def test_local_serve_request_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.TimeoutException("Request timeout")):
            _out, _err, code = _run_cli_output(
                ["local-serve", "--model", "x"], capsys,
            )
        assert code == 1
