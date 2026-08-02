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
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from general_ludd.cli import build_parser, main
from general_ludd.security.state import SecureStateError


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
            new=Mock(return_value=mock_adapter),
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
            new=Mock(return_value=mock_adapter),
        ):
            _cmd_audit_plugins(_ns(project="acme", limit="delegate_discipline_check"))

        extravars = mock_adapter.run_playbook.call_args.kwargs.get("extravars") or {}
        assert extravars.get("project_name") == "acme"
        assert extravars.get("project_root") == str(Path.cwd().resolve(strict=True))
        assert extravars.get("audit_limit") == "delegate_discipline_check"

    def test_logical_project_name_uses_current_directory(self, tmp_path, monkeypatch):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        monkeypatch.chdir(tmp_path)

        project_name, project_root = _resolve_project_argument("my-proj")

        assert project_name == "my-proj"
        assert project_root == tmp_path.resolve(strict=True)

    def test_explicit_relative_project_path_resolves_existing_directory(
        self,
        tmp_path,
        monkeypatch,
    ):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        project_name, project_root = _resolve_project_argument("./project")

        assert project_name == "./project"
        assert project_root == project_dir.resolve(strict=True)

    def test_missing_explicit_project_path_fails_closed(self, tmp_path):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        with pytest.raises(SecureStateError, match="project path is unavailable"):
            _resolve_project_argument(str(tmp_path / "missing"))

    def test_explicit_project_file_fails_closed(self, tmp_path):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        project_file = tmp_path / "project.txt"
        project_file.write_text("not a directory", encoding="utf-8")

        with pytest.raises(SecureStateError, match="project path is not a directory"):
            _resolve_project_argument(str(project_file))

    def test_project_path_with_parent_traversal_fails_closed(self, tmp_path, monkeypatch):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        (tmp_path / "project").mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SecureStateError, match=r"must not contain '\.\.'"):
            _resolve_project_argument("./child/../project")

    def test_project_path_with_symlink_component_fails_closed(self, tmp_path):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        link = tmp_path / "project-link"
        link.symlink_to(project_dir, target_is_directory=True)

        with pytest.raises(SecureStateError, match="symlink component"):
            _resolve_project_argument(str(link))

    def test_invalid_bare_project_name_fails_closed(self):
        from general_ludd.cli_audit_plugins import _resolve_project_argument

        with pytest.raises(SecureStateError, match="logical project name"):
            _resolve_project_argument("invalid project name")

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
            new=Mock(return_value=mock_adapter),
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
            new=Mock(return_value=mock_adapter),
        ):
            _cmd_audit_plugins(_ns())

        extravars = mock_adapter.run_playbook.call_args.kwargs.get("extravars") or {}
        assert extravars.get("audit_plugins_run_enforce_disengage") is False

    def test_outputs_secure_artifact_dir(self, capsys, tmp_path, monkeypatch):
        from general_ludd.cli_audit_plugins import _cmd_audit_plugins

        state_root = tmp_path / "state"
        monkeypatch.setenv("GLUDD_STATE_DIR", str(state_root))

        mock_adapter = MagicMock()
        mock_adapter.run_playbook.return_value = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }
        mock_adapter.list_playbooks.return_value = ["audit_plugins.yml"]

        with patch(
            "general_ludd.cli_audit_plugins.AnsibleRunnerAdapter",
            new=Mock(return_value=mock_adapter),
        ):
            _cmd_audit_plugins(_ns())

        out = capsys.readouterr().out
        assert f"artifact_dir={state_root}" in out
        assert out.rstrip().endswith("plugin-audit")

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
                new=Mock(return_value=mock_adapter),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _cmd_audit_plugins(_ns())
        assert exc_info.value.code != 0
