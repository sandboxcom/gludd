"""Unit tests for cli_deploy_check."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli_deploy_check import (
    _cmd_approve,
    _cmd_reject,
    _cmd_run,
    _cmd_suggest_fix,
    _load_deployment,
    _print_json,
    _psk_headers,
    add_deploy_check_subparser,
)


class TestPskHeaders:
    def test_no_psk_env(self):
        with patch.dict("os.environ", {}, clear=True):
            headers = _psk_headers()
            assert "Authorization" not in headers

    def test_psk_env_adds_bearer(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": "secret123"}, clear=True):
            headers = _psk_headers()
            assert headers["Authorization"] == "Bearer secret123"


class TestPrintJson:
    def test_outputs_json(self, capsys):
        _print_json({"findings": []})
        captured = capsys.readouterr()
        assert "findings" in captured.out


class TestLoadDeployment:
    def test_json_file(self, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')
        result = _load_deployment(str(config))
        assert result == {"model": "test"}

    def test_missing_file_exits_1(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_deployment("/nonexistent/path.json")
        assert exc_info.value.code == 1

    def test_invalid_json_exits_1(self, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text("not valid json")
        with pytest.raises(SystemExit) as exc_info:
            _load_deployment(str(config))
        assert exc_info.value.code == 1

    def test_yaml_file(self, tmp_path):
        config = tmp_path / "deploy.yaml"
        config.write_text("model: test\ngpu: 4\n")
        result = _load_deployment(str(config))
        assert result == {"model": "test", "gpu": 4}

    def test_yaml_fallback_to_json_when_no_pyyaml(self, tmp_path):
        config = tmp_path / "deploy.yaml"
        config.write_text('{"model": "test"}')
        with patch.dict("sys.modules", {"yaml": None}):
            pass


class TestCmdRun:
    def test_no_findings(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = False

        with patch("general_ludd.cli_deploy_check._http", return_value={"findings": []}):
            _cmd_run(args)
        captured = capsys.readouterr()
        assert "No misconfigurations detected" in captured.out

    def test_json_output(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = True

        data = {"findings": [{"rule_id": "R1", "severity": "error", "engine": "static", "message": "bad"}]}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_run(args)
        captured = capsys.readouterr()
        assert "R1" in captured.out

    def test_findings_output(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = False

        data = {
            "findings": [
                {
                    "rule_id": "R1", "severity": "critical", "engine": "static",
                    "message": "bad config", "remediation": "fix it",
                }
            ],
            "remediations": [],
            "has_critical": True,
        }
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_run(args)
            assert exc_info.value.code == 2

    def test_findings_with_patches(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = False

        data = {
            "findings": [
                {"rule_id": "R1", "severity": "warning", "engine": "static", "message": "bad", "remediation": "fix"}
            ],
            "remediations": [{"rule_id": "R1", "config_patch": {"key": "val"}}],
        }
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_run(args)
        captured = capsys.readouterr()
        assert "R1" in captured.out
        assert "config_patch" in captured.out


class TestCmdSuggestFix:
    def test_json_output(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = True

        data = {"fix_id": "fix-1", "source": "static", "patch": {"key": "val"}}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_suggest_fix(args)
        captured = capsys.readouterr()
        assert "fix-1" in captured.out

    def test_text_output_with_patch(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = False

        data = {"fix_id": "fix-1", "source": "static", "patch": {"key": "val"}}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_suggest_fix(args)
        captured = capsys.readouterr()
        assert "fix-1" in captured.out
        assert "key" in captured.out

    def test_no_patch(self, capsys, tmp_path):
        config = tmp_path / "deploy.json"
        config.write_text('{"model": "test"}')

        args = MagicMock()
        args.config = str(config)
        args.gpu_type = None
        args.gpu_count = 1
        args.daemon_url = "http://localhost:8000"
        args.json = False

        data = {"fix_id": "fix-1", "source": "static", "patch": {}}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_suggest_fix(args)
        captured = capsys.readouterr()
        assert "empty" in captured.out


class TestCmdApprove:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.fix_id = "fix-1"
        args.retry = False
        args.json = True

        data = {"status": "approved", "merged_config": {}}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_approve(args)
        captured = capsys.readouterr()
        assert "approved" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.fix_id = "fix-1"
        args.retry = True
        args.json = False

        data = {"status": "approved", "merged_config": {"key": "val"}, "retried": True}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_approve(args)
        captured = capsys.readouterr()
        assert "approved" in captured.out
        assert "True" in captured.out


class TestCmdReject:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.fix_id = "fix-1"
        args.reason = "not needed"
        args.json = True

        data = {"status": "rejected", "reason": "not needed"}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_reject(args)
        captured = capsys.readouterr()
        assert "rejected" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.fix_id = "fix-1"
        args.reason = "not needed"
        args.json = False

        data = {"status": "rejected", "reason": "not needed"}
        with patch("general_ludd.cli_deploy_check._http", return_value=data):
            _cmd_reject(args)
        captured = capsys.readouterr()
        assert "rejected" in captured.out


class TestAddDeployCheckSubparser:
    def test_adds_run_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_deploy_check_subparser(subparsers)

        ns = parser.parse_args(["deploy-check", "run", "--config", "deploy.yaml"])
        assert ns.deploy_check_command == "run"
        assert ns.config == "deploy.yaml"

    def test_adds_suggest_fix_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_deploy_check_subparser(subparsers)

        ns = parser.parse_args(["deploy-check", "suggest-fix", "--config", "deploy.yaml"])
        assert ns.deploy_check_command == "suggest-fix"
        assert ns.func is _cmd_suggest_fix

    def test_adds_approve_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_deploy_check_subparser(subparsers)

        ns = parser.parse_args(["deploy-check", "approve", "fix-123"])
        assert ns.deploy_check_command == "approve"
        assert ns.fix_id == "fix-123"

    def test_adds_reject_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_deploy_check_subparser(subparsers)

        ns = parser.parse_args(["deploy-check", "reject", "fix-123", "--reason", "bad"])
        assert ns.deploy_check_command == "reject"
        assert ns.fix_id == "fix-123"
        assert ns.reason == "bad"

    def test_gpu_args(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_deploy_check_subparser(subparsers)

        ns = parser.parse_args(["deploy-check", "run", "--config", "d.yaml", "--gpu-type", "h100", "--gpu-count", "8"])
        assert ns.gpu_type == "h100"
        assert ns.gpu_count == 8
