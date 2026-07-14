"""Structural tests for general_ludd/cli_deploy_check.py.

Verifies: module import, all exported callables exist, function signatures
have correct parameter names, argparse subparser wiring produces the expected
subcommand tree, and module-level attributes are present.
"""

from __future__ import annotations

import argparse
import inspect

import pytest

import general_ludd.cli_deploy_check as m

# ── Module import & attribute existence ──────────────────────────────────────

class TestModuleImport:
    def test_module_is_importable(self):
        assert m is not None

    def test_module_has_docstring(self):
        assert m.__doc__ is not None
        assert "deploy-check" in m.__doc__


class TestExportedCallables:
    """Every function defined at module scope must exist as an attribute."""

    def test_psk_headers_exists(self):
        assert callable(m._psk_headers)

    def test_http_exists(self):
        assert callable(m._http)

    def test_print_json_exists(self):
        assert callable(m._print_json)

    def test_load_deployment_exists(self):
        assert callable(m._load_deployment)

    def test_cmd_run_exists(self):
        assert callable(m._cmd_run)

    def test_cmd_suggest_fix_exists(self):
        assert callable(m._cmd_suggest_fix)

    def test_cmd_approve_exists(self):
        assert callable(m._cmd_approve)

    def test_cmd_reject_exists(self):
        assert callable(m._cmd_reject)

    def test_add_deploy_check_subparser_exists(self):
        assert callable(m.add_deploy_check_subparser)


# ── Function signatures ──────────────────────────────────────────────────────

class TestFunctionSignatures:
    """Verify parameter names for each exported function."""

    def test_psk_headers_no_params(self):
        sig = inspect.signature(m._psk_headers)
        assert list(sig.parameters.keys()) == []

    def test_http_signature(self):
        sig = inspect.signature(m._http)
        params = list(sig.parameters.keys())
        assert params == ["method", "url", "json_body", "timeout", "ok_codes"]

    def test_print_json_signature(self):
        sig = inspect.signature(m._print_json)
        params = list(sig.parameters.keys())
        assert params == ["obj"]

    def test_load_deployment_signature(self):
        sig = inspect.signature(m._load_deployment)
        params = list(sig.parameters.keys())
        assert params == ["path"]

    def test_cmd_run_signature(self):
        sig = inspect.signature(m._cmd_run)
        params = list(sig.parameters.keys())
        assert params == ["args"]

    def test_cmd_suggest_fix_signature(self):
        sig = inspect.signature(m._cmd_suggest_fix)
        params = list(sig.parameters.keys())
        assert params == ["args"]

    def test_cmd_approve_signature(self):
        sig = inspect.signature(m._cmd_approve)
        params = list(sig.parameters.keys())
        assert params == ["args"]

    def test_cmd_reject_signature(self):
        sig = inspect.signature(m._cmd_reject)
        params = list(sig.parameters.keys())
        assert params == ["args"]

    def test_add_deploy_check_subparser_signature(self):
        sig = inspect.signature(m.add_deploy_check_subparser)
        params = list(sig.parameters.keys())
        assert params == ["sub"]


class TestFunctionAnnotations:
    """Verify return type annotations are present on key functions."""

    def test_psk_headers_return_annotation(self):
        sig = inspect.signature(m._psk_headers)
        assert sig.return_annotation is not inspect.Parameter.empty

    def test_http_return_annotation(self):
        sig = inspect.signature(m._http)
        assert sig.return_annotation is not inspect.Parameter.empty

    def test_add_deploy_check_subparser_return_annotation(self):
        sig = inspect.signature(m.add_deploy_check_subparser)
        # from __future__ import annotations stringifies -> None to 'None'
        assert sig.return_annotation in (None, "None")


# ── Argparse subparser wiring ────────────────────────────────────────────────

class TestSubparserStructure:
    """The deploy-check subparser adds a parent command + four sub-commands."""

    @pytest.fixture
    def parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command")
        m.add_deploy_check_subparser(sub)
        return p

    def test_deploy_check_is_registered(self, parser):
        ns = parser.parse_args(["deploy-check", "run", "--config", "d.yaml"])
        assert ns.command == "deploy-check"
        assert ns.deploy_check_command == "run"

    def test_run_command_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "run", "--config", "deploy.yaml",
        ])
        assert ns.config == "deploy.yaml"
        assert ns.gpu_type is None
        assert ns.gpu_count == 1
        assert ns.daemon_url == "http://localhost:8000"
        assert ns.json is False
        assert ns.func is m._cmd_run

    def test_run_with_gpu_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "run", "--config", "d.yml",
            "--gpu-type", "h100", "--gpu-count", "8",
        ])
        assert ns.gpu_type == "h100"
        assert ns.gpu_count == 8

    def test_run_with_daemon_url(self, parser):
        ns = parser.parse_args([
            "deploy-check", "run", "--config", "d.json",
            "--daemon-url", "https://daemon.example.com",
        ])
        assert ns.daemon_url == "https://daemon.example.com"

    def test_run_with_json_flag(self, parser):
        ns = parser.parse_args([
            "deploy-check", "run", "--config", "d.json", "--json",
        ])
        assert ns.json is True

    def test_suggest_fix_command_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "suggest-fix", "--config", "deploy.yaml",
        ])
        assert ns.deploy_check_command == "suggest-fix"
        assert ns.config == "deploy.yaml"
        assert ns.gpu_type is None
        assert ns.gpu_count == 1
        assert ns.daemon_url == "http://localhost:8000"
        assert ns.json is False
        assert ns.func is m._cmd_suggest_fix

    def test_suggest_fix_with_gpu_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "suggest-fix", "--config", "d.yml",
            "--gpu-type", "a100_80", "--gpu-count", "4",
        ])
        assert ns.gpu_type == "a100_80"
        assert ns.gpu_count == 4

    def test_suggest_fix_with_json(self, parser):
        ns = parser.parse_args([
            "deploy-check", "suggest-fix", "--config", "d.json", "--json",
        ])
        assert ns.json is True

    def test_approve_command_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "approve", "fix-abc123",
        ])
        assert ns.deploy_check_command == "approve"
        assert ns.fix_id == "fix-abc123"
        assert ns.retry is False
        assert ns.daemon_url == "http://localhost:8000"
        assert ns.json is False
        assert ns.func is m._cmd_approve

    def test_approve_with_retry(self, parser):
        ns = parser.parse_args([
            "deploy-check", "approve", "fix-abc123", "--retry",
        ])
        assert ns.retry is True

    def test_approve_with_json(self, parser):
        ns = parser.parse_args([
            "deploy-check", "approve", "fix-abc123", "--json",
        ])
        assert ns.json is True

    def test_reject_command_args(self, parser):
        ns = parser.parse_args([
            "deploy-check", "reject", "fix-xyz999",
        ])
        assert ns.deploy_check_command == "reject"
        assert ns.fix_id == "fix-xyz999"
        assert ns.reason == ""
        assert ns.daemon_url == "http://localhost:8000"
        assert ns.json is False
        assert ns.func is m._cmd_reject

    def test_reject_with_reason(self, parser):
        ns = parser.parse_args([
            "deploy-check", "reject", "fix-xyz999",
            "--reason", "not needed",
        ])
        assert ns.reason == "not needed"

    def test_reject_with_json(self, parser):
        ns = parser.parse_args([
            "deploy-check", "reject", "fix-xyz999", "--json",
        ])
        assert ns.json is True


class TestSubparserDefaults:
    """Verify that `func` defaults are correctly wired on every sub-command."""

    @pytest.fixture
    def parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command")
        m.add_deploy_check_subparser(sub)
        return p

    def test_run_func_default(self, parser):
        ns = parser.parse_args([
            "deploy-check", "run", "--config", "d.yaml",
        ])
        assert ns.func is m._cmd_run

    def test_suggest_fix_func_default(self, parser):
        ns = parser.parse_args([
            "deploy-check", "suggest-fix", "--config", "d.yaml",
        ])
        assert ns.func is m._cmd_suggest_fix

    def test_approve_func_default(self, parser):
        ns = parser.parse_args([
            "deploy-check", "approve", "f1",
        ])
        assert ns.func is m._cmd_approve

    def test_reject_func_default(self, parser):
        ns = parser.parse_args([
            "deploy-check", "reject", "f1",
        ])
        assert ns.func is m._cmd_reject

    def test_deploy_check_parent_func_is_none(self, parser):
        ns = parser.parse_args(["deploy-check", "run", "--config", "d.yaml"])
        assert ns.func is m._cmd_run  # run's func, not the parent


class TestSubparserRequiredArgs:
    """Verify that required arguments are enforced."""

    @pytest.fixture
    def parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command")
        m.add_deploy_check_subparser(sub)
        return p

    def test_run_requires_config(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["deploy-check", "run"])
        with pytest.raises(SystemExit):
            parser.parse_args(["deploy-check", "run", "--gpu-type", "h100"])

    def test_suggest_fix_requires_config(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["deploy-check", "suggest-fix"])

    def test_approve_requires_fix_id(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["deploy-check", "approve"])

    def test_reject_requires_fix_id(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["deploy-check", "reject"])


# ── Module-level constants & attributes ──────────────────────────────────────

class TestModuleAttributes:
    def test_imports_json_module(self):
        assert hasattr(m, "_json")
        assert m._json is not None

    def test_module_name_is_correct(self):
        assert m.__name__ == "general_ludd.cli_deploy_check"
