"""Unit tests for ``gludd audit-plugins`` CLI subcommand.

Covers:
- The subcommand is registered as a top-level command.
- Argument parsing (``--project``, ``--limit``).
- Handler invokes ``AnsibleRunnerAdapter.run_playbook`` with the
  ``audit_plugins.yml`` playbook and the expected extravars.
- Artifact path is surfaced in the output.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli import build_parser, main


def _ns(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "project": None,
        "limit": None,
        "enforce_disengage": False,
        "daemon_url": "http://localhost:8000",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestAuditPluginsParserWiring:
    """The ``audit-plugins`` subcommand must parse like sibling subcommands."""

    def test_audit_plugins_in_subcommand_map(self):
        _, subcommand_map = build_parser()
        assert "audit-plugins" in subcommand_map

    def test_audit_plugins_invokes_cmd(self):
        with (
            patch("sys.argv", ["gludd", "audit-plugins"]),
            patch("general_ludd.cli_audit_plugins._cmd_audit_plugins") as mock_cmd,
        ):
            main()
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.project is None
        assert args.limit is None

    def test_audit_plugins_parses_flags(self):
        with (
            patch(
                "sys.argv",
                ["gludd", "audit-plugins", "--project", "acme", "--limit", "agent_floor_check"],
            ),
            patch("general_ludd.cli_audit_plugins._cmd_audit_plugins") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.project == "acme"
        assert args.limit == "agent_floor_check"

    def test_audit_plugins_supports_enforce_disengage_flag(self):
        """--enforce-disengage flag opt-in for the destructive role (default false)."""
        with (
            patch(
                "sys.argv",
                ["gludd", "audit-plugins", "--enforce-disengage"],
            ),
            patch("general_ludd.cli_audit_plugins._cmd_audit_plugins") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.enforce_disengage is True

    def test_audit_plugins_enforce_disengage_defaults_false(self):
        """Without the flag, enforce_disengage must be False (opt-in only)."""
        with (
            patch("sys.argv", ["gludd", "audit-plugins"]),
            patch("general_ludd.cli_audit_plugins._cmd_audit_plugins") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.enforce_disengage is False


class TestAuditPluginsHandler:
    """Handler behaviour: adapter invocation + output."""

    def test_calls_runner_with_correct_playbook_name(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            return_value=mock_adapter,
        ):
            _cmd_audit_plugins(_ns())

        mock_adapter.run_playbook.assert_called_once()
        call_kwargs = mock_adapter.run_playbook.call_args
        playbook_name = call_kwargs.args[0] if call_kwargs.args else call_kwargs[1].get("playbook_name")
        assert playbook_name == "audit_plugins.yml"

    def test_passes_project_and_limit_as_extravars(self):
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            return_value=mock_adapter,
        ):
            _cmd_audit_plugins(_ns(project="acme", limit="delegate_discipline_check"))

        extravars = mock_adapter.run_playbook.call_args.kwargs.get("extravars") or {}
        assert extravars.get("project_name") == "acme"
        assert extravars.get("audit_limit") == "delegate_discipline_check"

    def test_enforce_disengage_propagates_to_extravars(self):
        """When --enforce-disengage is set, extravars flips the destructive role on."""
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            return_value=mock_adapter,
        ):
            _cmd_audit_plugins(_ns(enforce_disengage=True))

        extravars = mock_adapter.run_playbook.call_args.kwargs.get("extravars") or {}
        assert extravars.get("audit_plugins_run_enforce_disengage") is True

    def test_enforce_disengage_default_off_in_extravars(self):
        """Without the flag, the destructive role extravars must stay off."""
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            return_value=mock_adapter,
        ):
            _cmd_audit_plugins(_ns())

        extravars = mock_adapter.run_playbook.call_args.kwargs.get("extravars") or {}
        assert extravars.get("audit_plugins_run_enforce_disengage") is False

    def test_outputs_artifact_dir(self, capsys):
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            return_value=mock_adapter,
        ):
            _cmd_audit_plugins(_ns())

        out = capsys.readouterr().out
        assert "/tmp/gludd-plugin-audit" in out

    def test_nonzero_rc_exits_nonzero(self):
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "failed",
            "rc": 2,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with (
            patch(
                "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
                return_value=mock_adapter,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _cmd_audit_plugins(_ns())
        assert exc_info.value.code != 0
