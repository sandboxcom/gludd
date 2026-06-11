"""Tests for uncovered CLI command execution paths."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _ns(**kwargs: object) -> argparse.Namespace:
    defaults = {"daemon_url": "http://localhost:8000"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestModelsDiscoverExecution:
    def test_discover_success_with_models(self, capsys):
        from general_ludd.cli import _cmd_models_discover

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "provider": "openrouter",
            "discovered_count": 2,
            "generated_profiles": 2,
            "models": [
                {
                    "display_name": "Llama 3 8B",
                    "model_name": "meta-llama/llama-3-8b",
                    "is_free": True,
                    "cost_per_input_token": 0.0,
                    "cost_per_output_token": 0.0,
                    "context_window": 8192,
                    "quality_class": "good",
                    "role_names": ["coder", "reviewer"],
                },
                {
                    "display_name": "GPT-4",
                    "model_name": "openai/gpt-4",
                    "is_free": False,
                    "cost_per_input_token": 0.00003,
                    "cost_per_output_token": 0.00006,
                    "context_window": 128000,
                    "quality_class": "excellent",
                    "role_names": ["coder"],
                },
            ],
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_models_discover(_ns(provider="openrouter"))
        out = capsys.readouterr().out
        assert "Llama 3 8B" in out
        assert "[FREE]" in out
        assert "GPT-4" in out
        assert "2" in out

    def test_discover_failure_shows_configured(self, capsys):
        from general_ludd.cli import _cmd_models_discover

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": False,
            "error": "no providers",
            "configured": ["openrouter", "anthropic"],
        }
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_discover(_ns(provider="openrouter"))
        out = capsys.readouterr().out
        assert "openrouter" in out

    def test_discover_http_error(self, capsys):
        from general_ludd.cli import _cmd_models_discover

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_discover(_ns(provider="openrouter"))

    def test_discover_connection_error(self):
        from general_ludd.cli import _cmd_models_discover

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_models_discover(_ns(provider="openrouter"))


class TestModelsDiscoveredExecution:
    def test_discovered_with_profiles(self, capsys):
        from general_ludd.cli import _cmd_models_discovered

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "profiles": [
                {"display_name": "Llama 3", "model_profile_id": "llama3", "enabled": True},
                {"display_name": "GPT-4", "model_profile_id": "gpt4", "enabled": False},
            ],
        }
        with patch("httpx.get", return_value=mock_resp):
            _cmd_models_discovered(_ns())
        out = capsys.readouterr().out
        assert "Llama 3" in out
        assert "[enabled]" in out
        assert "[disabled]" in out

    def test_discovered_empty(self, capsys):
        from general_ludd.cli import _cmd_models_discovered

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"profiles": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_models_discovered(_ns())
        out = capsys.readouterr().out
        assert "No auto-discovered" in out

    def test_discovered_http_error(self):
        from general_ludd.cli import _cmd_models_discovered

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_discovered(_ns())


class TestProjectCommandsExecution:
    def test_project_add_success(self, capsys):
        from general_ludd.cli import _cmd_project_add

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "project_id": "p1",
            "name": "myproject",
            "weight": 50,
            "dispatch_mode": "active",
            "repo_url": "https://github.com/test/repo",
            "workspace_path": "/tmp/ws",
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_project_add(_ns(
                name="myproject", weight=50, description="",
                repo_url="https://github.com/test/repo",
                workspace_path="/tmp/ws", dispatch_mode="active",
            ))
        out = capsys.readouterr().out
        assert "p1" in out
        assert "myproject" in out
        assert "50%" in out

    def test_project_add_error(self):
        from general_ludd.cli import _cmd_project_add

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_project_add(_ns(
                name="bad", weight=0, description="",
                repo_url="", workspace_path="", dispatch_mode="active",
            ))

    def test_project_add_connection_error(self):
        from general_ludd.cli import _cmd_project_add

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_project_add(_ns(
                name="x", weight=0, description="",
                repo_url="", workspace_path="", dispatch_mode="active",
            ))

    def test_project_list_with_projects(self, capsys):
        from general_ludd.cli import _cmd_project_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "projects": [
                {
                    "project_id": "p1",
                    "name": "proj1",
                    "weight": 60,
                    "dispatch_mode": "active",
                    "active": True,
                    "repo_url": "https://github.com/test",
                    "workspace_path": "/tmp/ws1",
                },
            ],
        }
        with patch("httpx.get", return_value=mock_resp):
            _cmd_project_list(_ns())
        out = capsys.readouterr().out
        assert "proj1" in out
        assert "[active]" in out
        assert "github.com" in out

    def test_project_list_empty(self, capsys):
        from general_ludd.cli import _cmd_project_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"projects": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_project_list(_ns())
        out = capsys.readouterr().out
        assert "No projects" in out

    def test_project_remove_success(self, capsys):
        from general_ludd.cli import _cmd_project_remove

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp):
            _cmd_project_remove(_ns(project_id="p1"))
        out = capsys.readouterr().out
        assert "p1" in out

    def test_project_remove_error(self):
        from general_ludd.cli import _cmd_project_remove

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "not found"
        with patch("httpx.delete", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_project_remove(_ns(project_id="bad"))


class TestWorktreeCommandsExecution:
    def test_worktree_scan_success(self, capsys):
        from general_ludd.cli import _cmd_worktree_scan

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tracked_count": 3,
            "todos": [
                {"title": "Fix bug", "queue": "core"},
                {"title": "Add feature", "queue": "infra"},
            ],
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_worktree_scan(_ns(path=None))
        out = capsys.readouterr().out
        assert "3" in out
        assert "Fix bug" in out

    def test_worktree_scan_with_path(self, capsys):
        from general_ludd.cli import _cmd_worktree_scan

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracked_count": 0, "todos": []}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_worktree_scan(_ns(path="/home/user"))
        call_params = mock_post.call_args[1].get("params", {})
        assert call_params.get("watch_paths") == "/home/user"

    def test_worktree_scan_error(self):
        from general_ludd.cli import _cmd_worktree_scan

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_worktree_scan(_ns(path=None))

    def test_worktree_status_success(self, capsys):
        from general_ludd.cli import _cmd_worktree_status

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tracked_worktrees": [
                {"path": "/ws/proj1", "todo_id": "TODO-1", "has_agents_md": True},
                {"path": "/ws/proj2", "todo_id": None, "has_agents_md": False},
            ],
        }
        with patch("httpx.get", return_value=mock_resp):
            _cmd_worktree_status(_ns())
        out = capsys.readouterr().out
        assert "/ws/proj1" in out
        assert "TODO-1" in out
        assert "AGENTS.md" in out
        assert "/ws/proj2" in out

    def test_worktree_status_error(self):
        from general_ludd.cli import _cmd_worktree_status

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_worktree_status(_ns())


class TestSelftestExecution:
    def test_selftest_success(self, capsys):
        from general_ludd.cli import _cmd_selftest

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "podman_available": True,
            "scenarios_run": 3,
            "scenarios_passed": 3,
            "errors": [],
            "success": True,
            "results": [
                {"scenario": "basic_dispatch", "passed": True},
                {"scenario": "worker_health", "passed": True},
                {"scenario": "config_reload", "passed": True},
            ],
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_selftest(_ns())
        out = capsys.readouterr().out
        assert "podman (available)" in out
        assert "PASS" in out
        assert "3" in out

    def test_selftest_no_podman(self, capsys):
        from general_ludd.cli import _cmd_selftest

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "podman_available": False,
            "scenarios_run": 2,
            "scenarios_passed": 2,
            "errors": [],
            "success": True,
            "results": [{"scenario": "basic", "passed": True}],
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_selftest(_ns())
        out = capsys.readouterr().out
        assert "NOT available" in out

    def test_selftest_with_errors(self, capsys):
        from general_ludd.cli import _cmd_selftest

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "podman_available": True,
            "scenarios_run": 2,
            "scenarios_passed": 1,
            "errors": ["timeout on worker ping"],
            "success": False,
            "results": [
                {"scenario": "basic", "passed": True},
                {"scenario": "fail_case", "passed": False},
            ],
        }
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_selftest(_ns())
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "timeout" in out

    def test_selftest_http_error(self):
        from general_ludd.cli import _cmd_selftest

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_selftest(_ns())

    def test_selftest_connection_error(self):
        from general_ludd.cli import _cmd_selftest

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_selftest(_ns())


class TestStatusQualityGate:
    def test_status_quality_gate_with_checks(self, capsys):
        from general_ludd.cli import _cmd_status

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "version": "0.1.0",
            "config_dir": "/etc/gludd",
            "config_files": [],
            "filestore_root": "/var/lib/gludd",
            "filestore_binaries": [],
            "binary_versions": {},
            "db_engine": "sqlite",
            "db_url": "sqlite:///test.db",
            "uptime_ticks": 42,
            "todos_total": 10,
            "queue_depths": {"core": 3},
            "tick_metrics": {"todos_dispatched": 5, "leases_reclaimed": 1},
            "quality_gate": {
                "overall": "pass",
                "passed_count": 2,
                "total_count": 3,
                "checks": [
                    {"name": "lint", "passed": True},
                    {"name": "typecheck", "passed": True},
                    {"name": "coverage", "passed": False},
                ],
            },
        }
        with patch("httpx.get", return_value=mock_resp):
            _cmd_status(_ns(todo_id=None, project=None))
        out = capsys.readouterr().out
        assert "Quality Gate: pass (2/3" in out
        assert "lint" in out
        assert "coverage" in out

    def test_status_with_project_filter(self, capsys):
        from general_ludd.cli import _cmd_status

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "version": "0.1.0",
            "config_dir": "",
            "config_files": [],
            "filestore_root": "",
            "filestore_binaries": [],
            "binary_versions": {},
            "db_engine": "sqlite",
            "db_url": "",
            "uptime_ticks": 0,
            "todos_total": 0,
            "queue_depths": {},
            "tick_metrics": {},
            "quality_gate": {},
        }
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _cmd_status(_ns(todo_id=None, project="proj1"))
        url = mock_get.call_args[0][0]
        assert "project_id=proj1" in url

    def test_status_todo_with_project(self, capsys):
        from general_ludd.cli import _cmd_status

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"todo_id": "T1", "status": "active"}
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _cmd_status(_ns(todo_id="T1", project="proj1"))
        url = mock_get.call_args[0][0]
        assert "T1" in url
        assert "project_id=proj1" in url

    def test_status_todo_connection_error_exits(self):
        from general_ludd.cli import _cmd_status

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_status(_ns(todo_id="T1", project=None))


class TestCmdDaemonExecution:
    def test_daemon_calls_create_app_and_popen(self):
        from general_ludd.cli import _cmd_daemon

        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("logging.basicConfig"):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_daemon(_ns(
                    host="0.0.0.0", port=8000, log_level="info",
                    tick_interval=1.0, workers=1, config_dir=None,
                ))
            assert exc_info.value.code == 0
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert "create_daemon_app" in " ".join(cmd)

    def test_daemon_nonzero_exit(self):
        from general_ludd.cli import _cmd_daemon

        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 1
        with patch("general_ludd.daemon.create_daemon_app"), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch("logging.basicConfig"):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_daemon(_ns(
                    host="0.0.0.0", port=8000, log_level="info",
                    tick_interval=1.0, workers=1, config_dir=None,
                ))
            assert exc_info.value.code == 1


class TestFmtSizeEdgeCases:
    def test_fmt_size_bytes(self, capsys):
        from general_ludd.cli import _fmt_size

        assert _fmt_size(0) == "0 B"
        assert _fmt_size(100) == "100 B"

    def test_fmt_size_kb(self):
        from general_ludd.cli import _fmt_size

        assert _fmt_size(2048) == "2.0 KB"

    def test_fmt_size_mb(self):
        from general_ludd.cli import _fmt_size

        assert _fmt_size(5 * 1024 * 1024) == "5.0 MB"

    def test_fmt_size_large_mb(self):
        from general_ludd.cli import _fmt_size

        result = _fmt_size(500 * 1024 * 1024)
        assert "MB" in result


class TestHandleConnectionError:
    def test_timeout_exception(self, capsys):
        from general_ludd.cli import _handle_connection_error

        exc = httpx.ConnectTimeout("timed out")
        with pytest.raises(SystemExit) as exc_info:
            _handle_connection_error(exc, "http://localhost:8000")
        assert exc_info.value.code == 1
        out = capsys.readouterr()
        assert "daemon" in (out.out + out.err).lower()

    def test_generic_exception(self, capsys):
        from general_ludd.cli import _handle_connection_error

        exc = RuntimeError("something broke")
        with pytest.raises(SystemExit) as exc_info:
            _handle_connection_error(exc, "http://localhost:8000")
        assert exc_info.value.code == 1


class TestModelsDiscoverExecutionExtended:
    def test_discover_failure_no_configured(self, capsys):
        from general_ludd.cli import _cmd_models_discover

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": False,
            "error": "no providers found",
        }
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_discover(_ns(provider="test"))
        out = capsys.readouterr().out
        assert "no providers found" in out


class TestQuantizationEdgeCases:
    def test_quantization_list_empty(self, capsys):
        from general_ludd.cli import _cmd_quantization_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_quantization_list(_ns())
        out = capsys.readouterr().out
        assert "No quantization data" in out

    def test_quantization_drift_detected(self, capsys):
        from general_ludd.cli import _cmd_quantization_drift_check

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "drift_detected": True,
            "drifted_models": [
                {"model_id": "m1", "old_precision": "fp16", "new_precision": "int8"},
            ],
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_quantization_drift_check(_ns())
        out = capsys.readouterr().out
        assert "Drift detected" in out
        assert "m1" in out
        assert "fp16" in out
        assert "int8" in out

    def test_quantization_detect_error(self):
        from general_ludd.cli import _cmd_quantization_detect

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_quantization_detect(_ns(model_id="bad"))

    def test_quantization_list_error(self):
        from general_ludd.cli import _cmd_quantization_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_quantization_list(_ns())

    def test_quantization_drift_error(self):
        from general_ludd.cli import _cmd_quantization_drift_check

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_quantization_drift_check(_ns())


class TestCodeSearchEdgeCases:
    def test_code_search_empty_results(self, capsys):
        from general_ludd.cli import _cmd_code_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_code_search(_ns(query="nonexistent", language="python"))
        out = capsys.readouterr().out
        assert "No results" in out

    def test_code_search_error(self):
        from general_ludd.cli import _cmd_code_search

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_code_search(_ns(query="x", language="python"))

    def test_code_graph_error(self):
        from general_ludd.cli import _cmd_code_graph

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_code_graph(_ns(source="src", language="python"))


class TestTemplatesPlaybooksEdgeCases:
    def test_templates_list_empty(self, capsys):
        from general_ludd.cli import _cmd_templates_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"templates": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_templates_list(_ns())
        out = capsys.readouterr().out
        assert "No templates" in out

    def test_playbooks_list_empty(self, capsys):
        from general_ludd.cli import _cmd_playbooks_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"playbooks": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_playbooks_list(_ns())
        out = capsys.readouterr().out
        assert "No playbooks" in out

    def test_templates_refresh_error(self):
        from general_ludd.cli import _cmd_templates_refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_templates_refresh(_ns())

    def test_playbooks_refresh_error(self):
        from general_ludd.cli import _cmd_playbooks_refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_playbooks_refresh(_ns())


class TestHooksEdgeCases:
    def test_hooks_list_error(self):
        from general_ludd.cli import _cmd_hooks_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_hooks_list(_ns())

    def test_hooks_register_error(self):
        from general_ludd.cli import _cmd_hooks_register

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_hooks_register(_ns(event="test", handler="mod.fn"))

    def test_hooks_delete_error(self):
        from general_ludd.cli import _cmd_hooks_delete

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.delete", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_hooks_delete(_ns(hook_id="bad"))


class TestWorkersEdgeCases:
    def test_workers_list_empty(self, capsys):
        from general_ludd.cli import _cmd_workers_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"workers": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_workers_list(_ns())
        out = capsys.readouterr().out
        assert "No workers" in out

    def test_workers_list_error(self):
        from general_ludd.cli import _cmd_workers_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_workers_list(_ns())

    def test_workers_ping_error(self):
        from general_ludd.cli import _cmd_workers_ping

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_workers_ping(_ns())


class TestAgentsEdgeCases:
    def test_agents_list_error(self):
        from general_ludd.cli import _cmd_agents_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_agents_list(_ns())


class TestMetricsEdgeCases:
    def test_metrics_cost_error(self):
        from general_ludd.cli import _cmd_metrics_cost

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_metrics_cost(_ns())

    def test_metrics_report_error(self):
        from general_ludd.cli import _cmd_metrics_report

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_metrics_report(_ns())


class TestReloadEdgeCases:
    def test_reload_error(self):
        from general_ludd.cli import _cmd_reload

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_reload(_ns(scope="all"))

    def test_reload_connection_error(self):
        from general_ludd.cli import _cmd_reload

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_reload(_ns(scope="all"))


class TestModelsAddRemoveExecution:
    def test_models_add_with_api_key_env(self, capsys):
        from general_ludd.cli import _cmd_models_add

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_models_add(_ns(
                model_id="test-m", provider="openai", model="gpt-4",
                api_key_env="OPENAI_API_KEY",
            ))
        body = mock_post.call_args[1]["json"]
        assert body["api_key_env"] == "OPENAI_API_KEY"
        out = capsys.readouterr().out
        assert "test-m" in out

    def test_models_add_error(self):
        from general_ludd.cli import _cmd_models_add

        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = "bad"
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_add(_ns(
                model_id="bad", provider="x", model="x",
                api_key_env=None,
            ))

    def test_models_remove_error(self):
        from general_ludd.cli import _cmd_models_remove

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.delete", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_remove(_ns(model_id="nonexistent"))

    def test_models_list_empty(self, capsys):
        from general_ludd.cli import _cmd_models_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_models_list(_ns())
        out = capsys.readouterr().out
        assert "No models" in out

    def test_models_list_error(self):
        from general_ludd.cli import _cmd_models_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_models_list(_ns())


class TestPostTUICommands:
    def _mock_resp(self, status: int = 200, json_data: dict | None = None) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.json.return_value = json_data or {}
        r.text = "error text"
        return r

    def test_hooks_list_with_hooks(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_list

        resp = self._mock_resp(200, {"hooks": [{"hook_id": "h1", "event": "todo.done", "handler": "cb"}]})
        with patch("httpx.get", return_value=resp):
            _cmd_hooks_list(_ns())
        assert "h1" in capsys.readouterr().out

    def test_hooks_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_list

        resp = self._mock_resp(200, {"hooks": []})
        with patch("httpx.get", return_value=resp):
            _cmd_hooks_list(_ns())
        assert "No hooks" in capsys.readouterr().out

    def test_hooks_list_error(self) -> None:
        from general_ludd.cli import _cmd_hooks_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_hooks_list(_ns())

    def test_hooks_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_hooks_list(_ns())

    def test_hooks_register_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_register

        resp = self._mock_resp(200, {"hook_id": "h2"})
        with patch("httpx.post", return_value=resp):
            _cmd_hooks_register(_ns(event="todo.done", handler="cb"))
        assert "h2" in capsys.readouterr().out

    def test_hooks_register_error(self) -> None:
        from general_ludd.cli import _cmd_hooks_register

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_hooks_register(_ns(event="e", handler="h"))

    def test_hooks_register_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_register

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_hooks_register(_ns(event="e", handler="h"))

    def test_hooks_delete_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_delete

        resp = self._mock_resp(200)
        with patch("httpx.delete", return_value=resp):
            _cmd_hooks_delete(_ns(hook_id="h1"))
        assert "h1" in capsys.readouterr().out

    def test_hooks_delete_error(self) -> None:
        from general_ludd.cli import _cmd_hooks_delete

        resp = self._mock_resp(500)
        with patch("httpx.delete", return_value=resp), pytest.raises(SystemExit):
            _cmd_hooks_delete(_ns(hook_id="h1"))

    def test_hooks_delete_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_hooks_delete

        with patch("httpx.delete", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_hooks_delete(_ns(hook_id="h1"))

    def test_workers_list_with_workers(self, capsys) -> None:
        from general_ludd.cli import _cmd_workers_list

        resp = self._mock_resp(200, {"workers": [{"worker_id": "w1", "status": "active", "url": "http://w"}]})
        with patch("httpx.get", return_value=resp):
            _cmd_workers_list(_ns())
        assert "w1" in capsys.readouterr().out

    def test_workers_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_workers_list

        resp = self._mock_resp(200, {"workers": []})
        with patch("httpx.get", return_value=resp):
            _cmd_workers_list(_ns())
        assert "No workers" in capsys.readouterr().out

    def test_workers_list_error(self) -> None:
        from general_ludd.cli import _cmd_workers_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_workers_list(_ns())

    def test_workers_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_workers_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_workers_list(_ns())

    def test_workers_ping_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_workers_ping

        resp = self._mock_resp(200, {"workers": []})
        with patch("httpx.post", return_value=resp):
            _cmd_workers_ping(_ns())
        assert "workers" in capsys.readouterr().out

    def test_workers_ping_error(self) -> None:
        from general_ludd.cli import _cmd_workers_ping

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_workers_ping(_ns())

    def test_workers_ping_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_workers_ping

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_workers_ping(_ns())

    def test_agents_list_with_agents(self, capsys) -> None:
        from general_ludd.cli import _cmd_agents_list

        resp = self._mock_resp(200, {"agents": [{"agent_id": "a1", "status": "active", "model": "gpt-4"}]})
        with patch("httpx.get", return_value=resp):
            _cmd_agents_list(_ns())
        assert "a1" in capsys.readouterr().out

    def test_agents_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_agents_list

        resp = self._mock_resp(200, {"agents": []})
        with patch("httpx.get", return_value=resp):
            _cmd_agents_list(_ns())
        assert "No agents" in capsys.readouterr().out

    def test_agents_list_error(self) -> None:
        from general_ludd.cli import _cmd_agents_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_agents_list(_ns())

    def test_agents_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_agents_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_agents_list(_ns())

    def test_metrics_cost_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_metrics_cost

        resp = self._mock_resp(200, {"total_cost": 1.50})
        with patch("httpx.get", return_value=resp):
            _cmd_metrics_cost(_ns())
        assert "total_cost" in capsys.readouterr().out

    def test_metrics_cost_error(self) -> None:
        from general_ludd.cli import _cmd_metrics_cost

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_metrics_cost(_ns())

    def test_metrics_cost_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_metrics_cost

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_metrics_cost(_ns())

    def test_metrics_report_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_metrics_report

        resp = self._mock_resp(200, {"report": "ok"})
        with patch("httpx.get", return_value=resp):
            _cmd_metrics_report(_ns())
        assert "report" in capsys.readouterr().out

    def test_metrics_report_error(self) -> None:
        from general_ludd.cli import _cmd_metrics_report

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_metrics_report(_ns())

    def test_metrics_report_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_metrics_report

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_metrics_report(_ns())

    def test_reload_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_reload

        resp = self._mock_resp(200, {"scope": "all"})
        with patch("httpx.post", return_value=resp):
            _cmd_reload(_ns(scope="all"))
        assert "all" in capsys.readouterr().out

    def test_reload_error(self) -> None:
        from general_ludd.cli import _cmd_reload

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_reload(_ns(scope="all"))

    def test_reload_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_reload

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_reload(_ns(scope="all"))

    def test_templates_list_with_templates(self, capsys) -> None:
        from general_ludd.cli import _cmd_templates_list

        resp = self._mock_resp(200, {"templates": ["code", "test"]})
        with patch("httpx.get", return_value=resp):
            _cmd_templates_list(_ns())
        out = capsys.readouterr().out
        assert "code" in out and "test" in out

    def test_templates_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_templates_list

        resp = self._mock_resp(200, {"templates": []})
        with patch("httpx.get", return_value=resp):
            _cmd_templates_list(_ns())
        assert "No templates" in capsys.readouterr().out

    def test_templates_list_error(self) -> None:
        from general_ludd.cli import _cmd_templates_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_templates_list(_ns())

    def test_templates_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_templates_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_templates_list(_ns())

    def test_templates_refresh_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_templates_refresh

        resp = self._mock_resp(200, {"templates": ["t1", "t2", "t3", "t4", "t5"]})
        with patch("httpx.post", return_value=resp):
            _cmd_templates_refresh(_ns())
        assert "5" in capsys.readouterr().out

    def test_templates_refresh_error(self) -> None:
        from general_ludd.cli import _cmd_templates_refresh

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_templates_refresh(_ns())

    def test_templates_refresh_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_templates_refresh

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_templates_refresh(_ns())

    def test_playbooks_list_with_playbooks(self, capsys) -> None:
        from general_ludd.cli import _cmd_playbooks_list

        resp = self._mock_resp(200, {"playbooks": ["noop", "validate"]})
        with patch("httpx.get", return_value=resp):
            _cmd_playbooks_list(_ns())
        assert "noop" in capsys.readouterr().out

    def test_playbooks_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_playbooks_list

        resp = self._mock_resp(200, {"playbooks": []})
        with patch("httpx.get", return_value=resp):
            _cmd_playbooks_list(_ns())
        assert "No playbooks" in capsys.readouterr().out

    def test_playbooks_list_error(self) -> None:
        from general_ludd.cli import _cmd_playbooks_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_playbooks_list(_ns())

    def test_playbooks_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_playbooks_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_playbooks_list(_ns())

    def test_playbooks_refresh_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_playbooks_refresh

        resp = self._mock_resp(200, {"playbooks": ["p1", "p2", "p3"]})
        with patch("httpx.post", return_value=resp):
            _cmd_playbooks_refresh(_ns())
        assert "3" in capsys.readouterr().out

    def test_playbooks_refresh_error(self) -> None:
        from general_ludd.cli import _cmd_playbooks_refresh

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_playbooks_refresh(_ns())

    def test_playbooks_refresh_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_playbooks_refresh

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_playbooks_refresh(_ns())

    def test_code_graph_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_code_graph

        resp = self._mock_resp(200, {"nodes": []})
        with patch("httpx.get", return_value=resp):
            _cmd_code_graph(_ns(source="main.py", language="python"))
        assert "nodes" in capsys.readouterr().out

    def test_code_graph_error(self) -> None:
        from general_ludd.cli import _cmd_code_graph

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_code_graph(_ns(source="main.py", language="python"))

    def test_code_graph_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_code_graph

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_code_graph(_ns(source="main.py", language="python"))

    def test_code_search_with_results(self, capsys) -> None:
        from general_ludd.cli import _cmd_code_search

        resp = self._mock_resp(200, {"results": [{"file": "cli.py", "line": 1, "text": "def main"}]})
        with patch("httpx.get", return_value=resp):
            _cmd_code_search(_ns(query="main", language="python"))
        assert "cli.py" in capsys.readouterr().out

    def test_code_search_no_results(self, capsys) -> None:
        from general_ludd.cli import _cmd_code_search

        resp = self._mock_resp(200, {"results": []})
        with patch("httpx.get", return_value=resp):
            _cmd_code_search(_ns(query="xyz", language="python"))
        assert "No results" in capsys.readouterr().out

    def test_code_search_error(self) -> None:
        from general_ludd.cli import _cmd_code_search

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_code_search(_ns(query="q", language="python"))

    def test_code_search_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_code_search

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_code_search(_ns(query="q", language="python"))

    def test_quantization_list_with_models(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_list

        resp = self._mock_resp(200, {"models": [{"model_id": "gpt-4", "precision": "fp16", "confidence": 0.9}]})
        with patch("httpx.get", return_value=resp):
            _cmd_quantization_list(_ns())
        out = capsys.readouterr().out
        assert "gpt-4" in out and "fp16" in out

    def test_quantization_list_empty(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_list

        resp = self._mock_resp(200, {"models": []})
        with patch("httpx.get", return_value=resp):
            _cmd_quantization_list(_ns())
        assert "No quantization" in capsys.readouterr().out

    def test_quantization_list_error(self) -> None:
        from general_ludd.cli import _cmd_quantization_list

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_quantization_list(_ns())

    def test_quantization_list_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_list

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_quantization_list(_ns())

    def test_quantization_detect_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_detect

        resp = self._mock_resp(200, {"model_id": "gpt-4", "precision": "fp16", "confidence": 0.95})
        with patch("httpx.post", return_value=resp):
            _cmd_quantization_detect(_ns(model_id="gpt-4"))
        out = capsys.readouterr().out
        assert "gpt-4" in out and "fp16" in out

    def test_quantization_detect_error(self) -> None:
        from general_ludd.cli import _cmd_quantization_detect

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_quantization_detect(_ns(model_id="gpt-4"))

    def test_quantization_detect_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_detect

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_quantization_detect(_ns(model_id="gpt-4"))

    def test_quantization_drift_detected(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_drift_check

        resp = self._mock_resp(200, {
            "drift_detected": True,
            "drifted_models": [{"model_id": "gpt-4", "old_precision": "fp16", "new_precision": "int8"}],
        })
        with patch("httpx.post", return_value=resp):
            _cmd_quantization_drift_check(_ns())
        out = capsys.readouterr().out
        assert "Drift detected" in out and "gpt-4" in out

    def test_quantization_drift_no_drift(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_drift_check

        resp = self._mock_resp(200, {"drift_detected": False})
        with patch("httpx.post", return_value=resp):
            _cmd_quantization_drift_check(_ns())
        assert "No drift" in capsys.readouterr().out

    def test_quantization_drift_error(self) -> None:
        from general_ludd.cli import _cmd_quantization_drift_check

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_quantization_drift_check(_ns())

    def test_quantization_drift_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_quantization_drift_check

        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_quantization_drift_check(_ns())

    def test_integrity_scan_success_with_changes(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_scan

        resp = self._mock_resp(200, {"scanned": 10, "changes": [{"file": "/a.yml", "type": "modified"}]})
        with patch("httpx.post", return_value=resp):
            _cmd_integrity_scan(_ns(paths=None))
        out = capsys.readouterr().out
        assert "10" in out and "a.yml" in out

    def test_integrity_scan_success_no_changes(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_scan

        resp = self._mock_resp(200, {"scanned": 5, "changes": []})
        with patch("httpx.post", return_value=resp):
            _cmd_integrity_scan(_ns(paths=None))
        assert "No changes" in capsys.readouterr().out

    def test_integrity_scan_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_scan

        with patch("httpx.post", side_effect=Exception("fail")), \
             patch("general_ludd.cli._gather_offline_status", return_value={}), \
             patch("general_ludd.cli._scan_local_integrity", return_value={"scanned": 3, "changes": []}):
            _cmd_integrity_scan(_ns(paths=None))
        assert "Local scan" in capsys.readouterr().out

    def test_integrity_report_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_report

        resp = self._mock_resp(200, {"changes": [], "log_entries": 0})
        with patch("httpx.get", return_value=resp):
            _cmd_integrity_report(_ns())
        assert "changes" in capsys.readouterr().out

    def test_integrity_report_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_report

        with patch("httpx.get", side_effect=Exception("fail")), \
             patch("general_ludd.cli._gather_offline_status", return_value={}), \
             patch("general_ludd.cli._scan_local_integrity", return_value={"scanned": 0, "changes": []}):
            _cmd_integrity_report(_ns())
        assert "scanned" in capsys.readouterr().out

    def test_integrity_approve_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_approve

        resp = self._mock_resp(200, {"path": "/a.yml", "signature": "abc123def456"})
        with patch("httpx.post", return_value=resp):
            _cmd_integrity_approve(_ns(change_id="/a.yml", reason="ok", signer="admin"))
        out = capsys.readouterr().out
        assert "Approved" in out and "abc123" in out

    def test_integrity_approve_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_approve

        mock_result = {"path": "/a.yml", "signature": "sig"}
        with patch("httpx.post", side_effect=Exception("fail")), \
             patch("general_ludd.integrity.scanner.sign_change_openbao", return_value=mock_result):
            _cmd_integrity_approve(_ns(change_id="/a.yml", reason="ok", signer="admin"))
        assert "sig" in capsys.readouterr().out

    def test_integrity_reject_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_reject

        resp = self._mock_resp(200, {"path": "/a.yml", "status": "rejected"})
        with patch("httpx.post", return_value=resp):
            _cmd_integrity_reject(_ns(change_id="/a.yml", reason="bad"))
        assert "Rejected" in capsys.readouterr().out

    def test_integrity_reject_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_reject

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp):
            _cmd_integrity_reject(_ns(change_id="/a.yml", reason="bad"))
        assert "Error" in capsys.readouterr().err

    def test_integrity_log_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_log

        entry = {"timestamp": "2026-01-01", "action": "approved",
                 "path": "/a.yml", "reason": "ok", "signer": "admin"}
        resp = self._mock_resp(200, {"entries": [entry]})
        with patch("httpx.get", return_value=resp):
            _cmd_integrity_log(_ns())
        out = capsys.readouterr().out
        assert "approved" in out and "/a.yml" in out

    def test_integrity_log_error(self) -> None:
        from general_ludd.cli import _cmd_integrity_log

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_integrity_log(_ns())

    def test_integrity_log_connection_error(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_log

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(SystemExit):
            _cmd_integrity_log(_ns())

    def test_ansible_search_with_results(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_search

        resp = self._mock_resp(200, {"results": [{"name": "nginx", "description": "web server"}]})
        with patch("httpx.get", return_value=resp):
            _cmd_ansible_search(_ns(query="nginx", type="role"))
        assert "nginx" in capsys.readouterr().out

    def test_ansible_search_no_results(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_search

        resp = self._mock_resp(200, {"results": []})
        with patch("httpx.get", return_value=resp):
            _cmd_ansible_search(_ns(query="nonexistent", type="role"))
        assert "No results" in capsys.readouterr().out

    def test_ansible_search_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_search

        with patch("httpx.get", side_effect=Exception("fail")), \
             patch("general_ludd.ansible.galaxy.search_galaxy", return_value=[{"name": "nginx", "description": "web"}]):
            _cmd_ansible_search(_ns(query="nginx", type="role"))
        assert "nginx" in capsys.readouterr().out

    def test_ansible_search_error_falls_back_no_results(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_search

        with patch("httpx.get", side_effect=Exception("fail")), \
             patch("general_ludd.ansible.galaxy.search_galaxy", return_value=[]):
            _cmd_ansible_search(_ns(query="xyz", type="role"))
        assert "offline" in capsys.readouterr().out

    def test_ansible_install_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_install

        resp = self._mock_resp(200, {"success": True, "output": "installed"})
        with patch("httpx.post", return_value=resp):
            _cmd_ansible_install(_ns(name="nginx", type="role"))
        out = capsys.readouterr().out
        assert "OK" in out and "nginx" in out

    def test_ansible_install_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_install

        with patch("httpx.post", side_effect=Exception("fail")), \
             patch("general_ludd.ansible.galaxy.install_galaxy", return_value={"success": True, "output": "ok"}):
            _cmd_ansible_install(_ns(name="nginx", type="role"))
        assert "OK" in capsys.readouterr().out

    def test_ansible_builtins_success(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_builtins

        resp = self._mock_resp(200, {"modules": ["copy", "file"]})
        with patch("httpx.get", return_value=resp):
            _cmd_ansible_builtins(_ns())
        out = capsys.readouterr().out
        assert "copy" in out and "file" in out

    def test_ansible_builtins_error_falls_back(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_builtins

        with patch("httpx.get", side_effect=Exception("fail")), \
             patch("general_ludd.ansible.galaxy.get_builtin_modules", return_value=["copy"]):
            _cmd_ansible_builtins(_ns())
        assert "copy" in capsys.readouterr().out


class TestErrorPathCoverage:

    @staticmethod
    def _mock_resp(status: int, data: dict | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = data or {}
        resp.text = "server error"
        return resp

    def test_integrity_scan_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_scan

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_integrity_scan(_ns(paths=None))
        assert "Error" in capsys.readouterr().err

    def test_integrity_scan_offline_with_changes(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_scan

        changes = [{"file": "/b.yml", "type": "modified"}]
        with patch("httpx.post", side_effect=Exception("fail")), \
             patch("general_ludd.cli._gather_offline_status", return_value={}), \
             patch("general_ludd.cli._scan_local_integrity",
                   return_value={"scanned": 2, "changes": changes}):
            _cmd_integrity_scan(_ns(paths=None))
        out = capsys.readouterr().out
        assert "Local scan" in out and "b.yml" in out

    def test_integrity_report_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_report

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_integrity_report(_ns())
        assert "Error" in capsys.readouterr().err

    def test_integrity_approve_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_integrity_approve

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_integrity_approve(_ns(change_id="/a.yml", reason="ok", signer="admin"))

    def test_ansible_search_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_search

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_ansible_search(_ns(query="nginx", type="role"))
        assert "Error" in capsys.readouterr().err

    def test_ansible_install_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_install

        resp = self._mock_resp(500)
        with patch("httpx.post", return_value=resp), pytest.raises(SystemExit):
            _cmd_ansible_install(_ns(name="nginx", type="role"))
        assert "Error" in capsys.readouterr().err

    def test_ansible_builtins_http_error_exits(self, capsys) -> None:
        from general_ludd.cli import _cmd_ansible_builtins

        resp = self._mock_resp(500)
        with patch("httpx.get", return_value=resp), pytest.raises(SystemExit):
            _cmd_ansible_builtins(_ns())
        assert "Error" in capsys.readouterr().err

    def test_main_entry_point(self, capsys) -> None:
        with patch("general_ludd.cli._cmd_tui") as mock_tui, \
             patch("sys.argv", ["gludd", "tui"]):
            from general_ludd.cli import main
            main()
        mock_tui.assert_called_once()
