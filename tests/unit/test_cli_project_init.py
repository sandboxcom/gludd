"""Unit tests for cli_project_init."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli_project_init import (
    _cmd_project_init,
    _extract_summary,
    _resolve_playbook_path,
    add_project_init_subparser,
    os_cwd,
)


class TestOsCwd:
    def test_returns_string(self):
        cwd = os_cwd()
        assert isinstance(cwd, str)
        assert len(cwd) > 0


class TestExtractSummary:
    def test_empty_events_returns_empty(self):
        result = {"events": []}
        assert _extract_summary(result) == ""

    def test_no_events_key_returns_empty(self):
        result = {}
        assert _extract_summary(result) == ""

    def test_extracts_summary_from_events(self):
        result = {
            "events": [
                {"event_data": {"task": "report scaffold summary", "res": {"msg": "scaffold created"}}}
            ]
        }
        assert _extract_summary(result) == "scaffold created"

    def test_summary_task_without_res(self):
        result = {
            "events": [
                {"event_data": {"task": "report scaffold summary"}}
            ]
        }
        assert _extract_summary(result) == ""

    def test_non_dict_events_skipped(self):
        result = {"events": ["not a dict", {"event_data": {"task": "summary", "res": {"msg": "done"}}}]}
        assert _extract_summary(result) == "done"

    def test_multiple_summaries_joined(self):
        result = {
            "events": [
                {"event_data": {"task": "report scaffold summary", "res": {"msg": "first"}}},
                {"event_data": {"task": "report scaffold summary", "res": {"msg": "second"}}},
            ]
        }
        assert _extract_summary(result) == "first\nsecond"

    def test_res_not_a_dict(self):
        result = {
            "events": [
                {"event_data": {"task": "summary", "res": "not a dict"}}
            ]
        }
        assert _extract_summary(result) == ""


class TestResolvePlaybookPath:
    def test_returns_path(self, tmp_path):
        playbooks = tmp_path / "playbooks"
        playbooks.mkdir()
        (playbooks / "test_playbook.yml").write_text("")

        with (
            patch("general_ludd.cli_project_init.__file__", str(tmp_path / "x" / "y" / "z" / "mod.py")),
            patch("general_ludd.cli_project_init.Path.cwd", return_value=tmp_path),
        ):
            path = _resolve_playbook_path("test_playbook.yml")
            assert path.name == "test_playbook.yml"

    def test_falls_back_to_first_candidate_when_missing(self, tmp_path):
        with patch("general_ludd.cli_project_init.Path.cwd", return_value=tmp_path):
            path = _resolve_playbook_path("nonexistent.yml")
            assert path.name == "nonexistent.yml"


class TestCmdProjectInit:
    def test_namespace_required_exits_2(self):
        args = MagicMock()
        args.namespace = None
        args.collection = None
        args.force = False
        args.project_dir = "/tmp"

        with patch("general_ludd.cli_project_init.os_cwd", return_value="/tmp"):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_project_init(args)
            assert exc_info.value.code == 2

    def test_successful_invocation(self, capsys):
        args = MagicMock()
        args.namespace = "acme"
        args.collection = "myproj"
        args.force = False
        args.project_dir = "/tmp"

        mock_result = {"rc": 0, "status": "successful"}

        with patch("general_ludd.cli_project_init._invoke_role", return_value=mock_result):
            _cmd_project_init(args)

    def test_failed_status_exits_nonzero(self):
        args = MagicMock()
        args.namespace = "acme"
        args.collection = "myproj"
        args.force = False
        args.project_dir = "/tmp"

        mock_result = {"rc": 2, "status": "failed"}

        with patch("general_ludd.cli_project_init._invoke_role", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_project_init(args)
            assert exc_info.value.code == 2

    def test_default_collection_when_none(self):
        args = MagicMock()
        args.namespace = "acme"
        args.collection = None
        args.force = False
        args.project_dir = "/tmp"

        mock_result = {"rc": 0, "status": "successful"}

        with patch("general_ludd.cli_project_init._invoke_role", return_value=mock_result):
            _cmd_project_init(args)
            sys.modules.get("general_ludd.cli_project_init._invoke_role", None)


class TestAddProjectInitSubparser:
    def test_creates_parser(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="project_command")
        add_project_init_subparser(subparsers)

        ns = parser.parse_args(["init", "mydir", "--namespace", "acme"])
        assert ns.namespace == "acme"
        assert ns.project_dir == "mydir"
        assert ns.func is _cmd_project_init

    def test_default_project_dir(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="project_command")
        add_project_init_subparser(subparsers)

        ns = parser.parse_args(["init", "--namespace", "acme"])
        assert ns.project_dir is None

    def test_force_flag(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="project_command")
        add_project_init_subparser(subparsers)

        ns = parser.parse_args(["init", "--namespace", "acme", "--force"])
        assert ns.force is True

    def test_default_collection(self):
        import argparse

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="project_command")
        add_project_init_subparser(subparsers)

        ns = parser.parse_args(["init", "--namespace", "acme"])
        assert ns.collection == "project"
