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
