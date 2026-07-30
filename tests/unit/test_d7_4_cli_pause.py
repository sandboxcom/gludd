"""Unit tests for gludd pause / resume CLI subcommands (D.7.4)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli import main


class TestPauseResumeParsing:
    def test_top_level_smoke_command(self):
        with patch(
            "sys.argv",
            ["gludd", "smoke", "aws", "metadata", "--live"],
        ), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.provider == "aws"
        assert args.test == "metadata"
        assert args.live is True

    def test_nested_test_smoke_command_remains_available(self):
        with patch(
            "sys.argv",
            ["gludd", "test", "smoke", "aws", "metadata", "--live"],
        ), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.provider == "aws"
        assert args.test == "metadata"
        assert args.live is True

    def test_pause_list_command(self):
        with patch("sys.argv", ["gludd", "pause", "list"]), patch(
            "general_ludd.cli._cmd_pause_list"
        ) as mock_cmd:
            main()
        mock_cmd.assert_called_once()

    def test_pause_project_command(self):
        with patch("sys.argv", ["gludd", "pause", "project", "my-proj"]), patch(
            "general_ludd.cli._cmd_pause_project"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "my-proj"
            assert args.reason == ""

    def test_pause_project_with_reason(self):
        with patch(
            "sys.argv",
            ["gludd", "pause", "project", "my-proj", "--reason", "budget exceeded"],
        ), patch("general_ludd.cli._cmd_pause_project") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "my-proj"
            assert args.reason == "budget exceeded"

    def test_pause_model_command(self):
        with patch("sys.argv", ["gludd", "pause", "model", "gpt-4"]), patch(
            "general_ludd.cli._cmd_pause_model"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "gpt-4"

    def test_pause_model_with_reason(self):
        with patch(
            "sys.argv",
            ["gludd", "pause", "model", "gpt-4", "--reason", "rate limited"],
        ), patch("general_ludd.cli._cmd_pause_model") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "gpt-4"
            assert args.reason == "rate limited"

    def test_resume_project_command(self):
        with patch("sys.argv", ["gludd", "resume", "project", "my-proj"]), patch(
            "general_ludd.cli._cmd_resume_project"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "my-proj"

    def test_resume_model_command(self):
        with patch("sys.argv", ["gludd", "resume", "model", "gpt-4"]), patch(
            "general_ludd.cli._cmd_resume_model"
        ) as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.target_id == "gpt-4"

    def test_resume_project_daemon_url_override(self):
        with patch(
            "sys.argv",
            ["gludd", "resume", "project", "p1", "--daemon-url", "http://localhost:9000"],
        ), patch("general_ludd.cli._cmd_resume_project") as mock_cmd:
            main()
            args = mock_cmd.call_args[0][0]
            assert args.daemon_url == "http://localhost:9000"

    def test_pause_no_subcommand_shows_help(self):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "pause"]):
            main()
        assert exc_info.value.code == 0

    def test_resume_no_subcommand_shows_help(self):
        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", "resume"]):
            main()
        assert exc_info.value.code == 0


class TestPauseResumeHandlers:
    def test_pause_list_sends_get(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"paused": [], "count": 0}
        with patch("httpx.get", return_value=mock_response) as mock_get:
            import argparse

            from general_ludd.cli import _cmd_pause_list
            args = argparse.Namespace(daemon_url="http://localhost:8000")
            _cmd_pause_list(args)
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/api/pause" in call_args[0][0]

    def test_pause_project_sends_post(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"paused": True, "kind": "project", "target_id": "p1"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from general_ludd.cli import _cmd_pause_project
            args = argparse.Namespace(target_id="p1", reason="budget", daemon_url="http://localhost:8000")
            _cmd_pause_project(args)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/pause/project" in call_args[0][0]
        assert call_args[1]["json"]["target_id"] == "p1"
        assert call_args[1]["json"]["reason"] == "budget"

    def test_pause_model_sends_post(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"paused": True, "kind": "model", "target_id": "m1"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from general_ludd.cli import _cmd_pause_model
            args = argparse.Namespace(target_id="m1", reason="rate-limited", daemon_url="http://localhost:8000")
            _cmd_pause_model(args)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/pause/model" in call_args[0][0]
        assert call_args[1]["json"]["target_id"] == "m1"

    def test_resume_project_sends_post(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"resumed": True, "kind": "project", "target_id": "p1"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from general_ludd.cli import _cmd_resume_project
            args = argparse.Namespace(target_id="p1", daemon_url="http://localhost:8000")
            _cmd_resume_project(args)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/resume/project" in call_args[0][0]
        assert call_args[1]["json"]["target_id"] == "p1"

    def test_resume_model_sends_post(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"resumed": True, "kind": "model", "target_id": "m1"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            import argparse

            from general_ludd.cli import _cmd_resume_model
            args = argparse.Namespace(target_id="m1", daemon_url="http://localhost:8000")
            _cmd_resume_model(args)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/resume/model" in call_args[0][0]
        assert call_args[1]["json"]["target_id"] == "m1"

    def test_pause_list_handles_connection_error(self):
        from unittest.mock import patch as mock_patch

        import httpx

        with mock_patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            import argparse

            from general_ludd.cli import _cmd_pause_list
            args = argparse.Namespace(daemon_url="http://localhost:8000")
            with pytest.raises(SystemExit) as exc_info:
                _cmd_pause_list(args)
            assert exc_info.value.code == 1
