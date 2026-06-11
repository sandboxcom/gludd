"""Tests for new MCP, skills, and compute CLI subcommands."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from general_ludd.cli import main


def _parse(args: list[str]) -> object:
    with patch.object(sys, "argv", ["gludd", *args]):
        main()
    return True


class TestMCPCliParsing:
    def test_mcp_search_defaults(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"results": [{"server_name": "github", "description": "GitHub", "source": "official"}]},
            )
            _parse(["mcp", "search"])
            out = capsys.readouterr().out
            assert "github" in out

    def test_mcp_search_with_query(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"results": []},
            )
            _parse(["mcp", "search", "git"])
            mock_post.assert_called_once()

    def test_mcp_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"servers": [{"server_name": "filesystem", "description": "Files"}]},
            )
            _parse(["mcp", "list"])
            out = capsys.readouterr().out
            assert "filesystem" in out

    def test_mcp_info(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"server": {"server_name": "github", "description": "GitHub API"}},
            )
            _parse(["mcp", "info", "github"])
            out = capsys.readouterr().out
            assert "github" in out

    def test_mcp_info_not_found(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404, text="not found")
            with patch("sys.exit") as mock_exit:
                _parse(["mcp", "info", "nonexistent"])
                mock_exit.assert_called_with(1)


class TestSkillsCliParsing:
    def test_skills_search_defaults(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "results": [
                        {
                            "name": "tdd-discipline",
                            "description": "TDD",
                            "category": "methodology",
                            "tags": ["testing"],
                        }
                    ]
                },
            )
            _parse(["skills", "search"])
            out = capsys.readouterr().out
            assert "tdd-discipline" in out

    def test_skills_search_with_query(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"results": []})
            _parse(["skills", "search", "security"])
            mock_post.assert_called_once()

    def test_skills_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"skills": [{"name": "tdd-discipline", "description": "TDD"}]},
            )
            _parse(["skills", "list"])
            out = capsys.readouterr().out
            assert "tdd-discipline" in out

    def test_skills_install(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"installed": "/etc/general-ludd/skills/tdd-discipline.md", "name": "tdd-discipline"},
            )
            _parse(["skills", "install", "tdd-discipline"])
            out = capsys.readouterr().out
            assert "tdd-discipline" in out

    def test_skills_install_not_found(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=404, text="not found")
            with patch("sys.exit") as mock_exit:
                _parse(["skills", "install", "nonexistent"])
                mock_exit.assert_called_with(1)


class TestComputeCliParsing:
    def test_compute_endpoints(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "endpoints": [
                        {
                            "endpoint_id": "local-gpu",
                            "url": "http://localhost:8080",
                            "model": "my-model",
                            "utilization_pct": 0,
                            "current_load": 0,
                            "max_concurrent": 4,
                            "available_slots": 4,
                            "active": True,
                        }
                    ]
                },
            )
            _parse(["compute", "endpoints"])
            out = capsys.readouterr().out
            assert "local-gpu" in out

    def test_compute_register(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"endpoint_id": "test-ep", "url": "http://gpu:8000", "model": "llama"},
            )
            _parse(["compute", "register", "--id", "test-ep", "--url", "http://gpu:8000", "--model", "llama"])
            out = capsys.readouterr().out
            assert "test-ep" in out

    def test_compute_register_error(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=422, text="missing fields")
            with patch("sys.exit") as mock_exit:
                _parse(["compute", "register", "--id", "", "--url", ""])
                mock_exit.assert_called_with(1)


class TestHooksCliParsing:
    def test_hooks_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"hooks": [{"hook_id": "h1", "event": "todo.created", "handler": "my_handler"}]},
            )
            _parse(["hooks", "list"])
            out = capsys.readouterr().out
            assert "h1" in out

    def test_hooks_list_empty(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"hooks": []})
            _parse(["hooks", "list"])
            out = capsys.readouterr().out
            assert "No hooks" in out

    def test_hooks_register(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"hook_id": "new-hook"},
            )
            _parse(["hooks", "register", "--event", "todo.created", "--handler", "mod.fn"])
            out = capsys.readouterr().out
            assert "new-hook" in out

    def test_hooks_delete(self, capsys):
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=200)
            _parse(["hooks", "delete", "h1"])
            out = capsys.readouterr().out
            assert "h1" in out


class TestWorkersCliParsing:
    def test_workers_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"workers": [{"worker_id": "w1", "status": "healthy", "url": "http://w:8000"}]},
            )
            _parse(["workers", "list"])
            out = capsys.readouterr().out
            assert "w1" in out

    def test_workers_ping(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"pinged": 1},
            )
            _parse(["workers", "ping"])
            out = capsys.readouterr().out
            assert "pinged" in out


class TestAgentsCliParsing:
    def test_agents_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"agents": [{"agent_id": "coder", "status": "idle", "model": "gpt-4"}]},
            )
            _parse(["agents", "list"])
            out = capsys.readouterr().out
            assert "coder" in out

    def test_agents_list_empty(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"agents": []})
            _parse(["agents", "list"])
            out = capsys.readouterr().out
            assert "No agents" in out


class TestMetricsCliParsing:
    def test_metrics_cost(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"total_cost_usd": 1.23},
            )
            _parse(["metrics", "cost"])
            out = capsys.readouterr().out
            assert "1.23" in out

    def test_metrics_report(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"report": "ok"},
            )
            _parse(["metrics", "report"])
            out = capsys.readouterr().out
            assert "ok" in out


class TestReloadCliParsing:
    def test_reload(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"scope": "all"},
            )
            _parse(["reload"])
            out = capsys.readouterr().out
            assert "Reloaded" in out


class TestTemplatesCliParsing:
    def test_templates_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"templates": ["prompt_a.txt", "prompt_b.txt"]},
            )
            _parse(["templates", "list"])
            out = capsys.readouterr().out
            assert "prompt_a" in out

    def test_templates_refresh(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"templates": ["t1", "t2", "t3", "t4", "t5"]},
            )
            _parse(["templates", "refresh"])
            out = capsys.readouterr().out
            assert "5" in out


class TestPlaybooksCliParsing:
    def test_playbooks_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"playbooks": ["noop.yml", "run.yml"]},
            )
            _parse(["playbooks", "list"])
            out = capsys.readouterr().out
            assert "noop.yml" in out

    def test_playbooks_refresh(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"playbooks": ["p1", "p2", "p3"]},
            )
            _parse(["playbooks", "refresh"])
            out = capsys.readouterr().out
            assert "3" in out


class TestCodeIntelCliParsing:
    def test_code_graph(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"nodes": [], "edges": []},
            )
            _parse(["code", "graph"])
            out = capsys.readouterr().out
            assert "nodes" in out

    def test_code_search(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"results": [{"file": "a.py", "line": 10, "text": "def foo()"}]},
            )
            _parse(["code", "search", "foo"])
            out = capsys.readouterr().out
            assert "a.py" in out


class TestModelsCrudCliParsing:
    def test_models_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"models": [{"model_id": "gpt4", "provider": "openai", "model": "gpt-4"}]},
            )
            _parse(["models", "list"])
            out = capsys.readouterr().out
            assert "gpt4" in out

    def test_models_add(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            _parse(["models", "add", "--model-id", "test-model", "--provider", "openai"])
            out = capsys.readouterr().out
            assert "test-model" in out

    def test_models_remove(self, capsys):
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=200)
            _parse(["models", "remove", "test-model"])
            out = capsys.readouterr().out
            assert "test-model" in out


class TestQuantizationCli:
    def test_quantization_list(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"models": [{"model_id": "test-model", "precision": "bf16", "confidence": 0.95}]},
            )
            _parse(["quantization", "list"])
            out = capsys.readouterr().out
            assert "test-model" in out

    def test_quantization_detect(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"model_id": "test-model", "precision": "fp16", "confidence": 0.9},
            )
            _parse(["quantization", "detect", "--model-id", "test-model"])
            out = capsys.readouterr().out
            assert "test-model" in out

    def test_quantization_drift_check(self, capsys):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"drift_detected": False, "checked_models": 3},
            )
            _parse(["quantization", "drift-check"])
            out = capsys.readouterr().out
            assert "No drift" in out

    def test_quantization_detect_missing_model(self):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=422, text="validation error")
            with patch("sys.exit") as mock_exit:
                _parse(["quantization", "detect"])
                mock_exit.assert_called()
