"""Targeted branch coverage tests for cli.py."""

from __future__ import annotations

import contextlib
import io
import sys
from unittest.mock import patch

from general_ludd.cli import main


def _top_level_choices():
    """Return parser._subparsers choices dict (all top-level subcommands)."""
    from general_ludd.cli import build_parser

    parser, _ = build_parser()
    return parser._subparsers._group_actions[0].choices


def _nested_choices(command: str):
    """Return nested subcommand choices for a parent command."""
    from general_ludd.cli import build_parser

    parser, _ = build_parser()
    parent = parser._subparsers._group_actions[0].choices[command]
    for action in parent._actions:
        if hasattr(action, "choices") and action.dest != "help":
            return action.choices
    return {}


class TestManPage:
    def test_man_page_exists(self):
        from general_ludd.cli import MAN_PAGE

        assert "gludd" in MAN_PAGE
        assert "SYNOPSIS" in MAN_PAGE
        assert "COMMANDS" in MAN_PAGE
        assert len(MAN_PAGE) > 500

    def test_man_page_describes_daemon(self):
        from general_ludd.cli import MAN_PAGE

        assert "daemon" in MAN_PAGE
        assert "Start the daemon" in MAN_PAGE
        assert "--host" in MAN_PAGE

    def test_man_page_lists_subcommands(self):
        from general_ludd.cli import MAN_PAGE

        assert "add" in MAN_PAGE.lower()
        assert "status" in MAN_PAGE.lower()
        assert "models" in MAN_PAGE.lower()


class TestBuildParser:
    def test_parser_exists(self):
        from general_ludd.cli import build_parser

        parser, subcommand_map = build_parser()
        assert parser is not None
        assert isinstance(subcommand_map, dict)
        assert parser.prog == "gludd"

    def test_daemon_subparser(self):
        choices = _top_level_choices()
        assert "daemon" in choices

    def test_add_subparser(self):
        choices = _top_level_choices()
        assert "add" in choices

    def test_status_subparser(self):
        choices = _top_level_choices()
        assert "status" in choices

    def test_list_subparser(self):
        choices = _top_level_choices()
        assert "list" in choices

    def test_models_subparser(self):
        choices = _top_level_choices()
        assert "models" in choices

    def test_tui_subparser(self):
        choices = _top_level_choices()
        assert "tui" in choices

    def test_version_subparser(self):
        choices = _top_level_choices()
        assert "version" in choices

    def test_health_subparser(self):
        choices = _top_level_choices()
        assert "health" in choices

    def test_worktree_subparser(self):
        choices = _top_level_choices()
        assert "worktree" in choices

    def test_mcp_subparser(self):
        choices = _top_level_choices()
        assert "mcp" in choices

    def test_skills_subparser(self):
        choices = _top_level_choices()
        assert "skills" in choices

    def test_compute_subparser(self):
        choices = _top_level_choices()
        assert "compute" in choices

    def test_deployments_subparser(self):
        choices = _top_level_choices()
        assert "deployments" in choices

    def test_log_level_subparser(self):
        choices = _top_level_choices()
        assert "log-level" in choices

    def test_smoke_subparser(self):
        choices = _top_level_choices()
        assert "smoke" in choices

    def test_searx_subparser(self):
        choices = _top_level_choices()
        assert "searx" in choices

    def test_filestore_subparser(self):
        choices = _top_level_choices()
        assert "filestore" in choices

    def test_local_serve_subparser(self):
        choices = _top_level_choices()
        assert "local-serve" in choices

    def test_human_todo_subparser(self):
        choices = _top_level_choices()
        assert "human-todo" in choices

    def test_chat_subparser(self):
        choices = _top_level_choices()
        assert "chat" in choices

    def test_project_subparser(self):
        choices = _top_level_choices()
        assert "project" in choices

    def test_test_subparser(self):
        choices = _top_level_choices()
        assert "test" in choices

    def test_nested_models_choices(self):
        choices = _nested_choices("models")
        for sub in ("search", "deploy", "list", "discover", "performance", "ranking"):
            assert sub in choices, f"{sub} missing from models subcommands"

    def test_nested_mcp_choices(self):
        choices = _nested_choices("mcp")
        for sub in ("search", "list", "info"):
            assert sub in choices, f"{sub} missing from mcp subcommands"

    def test_nested_worktree_choices(self):
        choices = _nested_choices("worktree")
        for sub in ("scan", "status"):
            assert sub in choices, f"{sub} missing from worktree subcommands"

    def test_subcommand_map_has_nested_parsers(self):
        from general_ludd.cli import build_parser

        _, subcommand_map = build_parser()
        for name in (
            "login",
            "models",
            "mcp",
            "skills",
            "compute",
            "project",
            "searx",
            "test",
            "chat",
            "pause",
            "resume",
            "config",
            "code",
            "slurm",
            "connectors",
            "make",
            "language",
        ):
            assert name in subcommand_map, f"{name} missing from subcommand_map"


class TestMainFunction:
    def test_main_is_callable(self):
        assert callable(main)

    def test_main_no_args_shows_help_and_exits(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd"]
            buf = io.StringIO()
            with patch("sys.stdout", buf), patch("sys.stderr", io.StringIO()):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 1
                else:
                    raise AssertionError("expected SystemExit")
            stdout_text = buf.getvalue()
            assert "usage:" in stdout_text.lower() or "gludd" in stdout_text.lower()
        finally:
            sys.argv = old_argv

    def test_main_version_flag(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "version"]
            buf = io.StringIO()
            from general_ludd import __version__

            with patch("sys.stdout", buf):
                try:
                    main()
                except SystemExit as e:
                    assert e.code is None or e.code == 0
            stdout_text = buf.getvalue()
            assert "general-ludd-agent" in stdout_text
            assert __version__ in stdout_text
        finally:
            sys.argv = old_argv

    def test_main_unknown_command(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "nonexistent_cmd_xyz"]
            with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 2
                else:
                    raise AssertionError("expected SystemExit")
        finally:
            sys.argv = old_argv

    def test_main_help_command(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "help"]
            buf = io.StringIO()
            with patch("sys.stdout", buf), contextlib.suppress(SystemExit):
                main()
            stdout_text = buf.getvalue()
            assert "gludd" in stdout_text
        finally:
            sys.argv = old_argv

    def test_main_nested_subparser_shows_help(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "models"]
            buf = io.StringIO()
            with patch("sys.stdout", buf), contextlib.suppress(SystemExit):
                main()
            stdout_text = buf.getvalue()
            assert "models" in stdout_text.lower()
        finally:
            sys.argv = old_argv


class TestImportSmoke:
    def test_cli_module_imports(self):
        import general_ludd.cli as cli

        assert hasattr(cli, "main")
        assert hasattr(cli, "build_parser")
        assert hasattr(cli, "MAN_PAGE")

    def test_tui_runner_import(self):
        from general_ludd.tui.runner import run_tui

        assert callable(run_tui)

    def test_config_editor_import(self):
        from general_ludd.tui.config_editor import ConfigEditor

        assert ConfigEditor is not None

    def test_binary_path_resolver_import(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        assert BinaryPathResolver is not None

    def test_file_integrity_scanner_import(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        assert FileIntegrityScanner is not None

    def test_make_table_import(self):
        from general_ludd.tui.tables import _make_table

        assert callable(_make_table)


class TestBinaryBootstrapperImport:
    def test_bootstrapper_import(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        assert BinaryBootstrapper is not None


class TestPerformanceRouterImport:
    def test_performance_router_import(self):
        from general_ludd.models.performance_router import DEFAULT_STRATEGIES

        assert isinstance(DEFAULT_STRATEGIES, (list, tuple, dict))


class TestSubcommandSmoke:
    def test_daemon_command_args(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["daemon", "--host", "127.0.0.1", "--port", "8000"])
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.command == "daemon"

    def test_add_command_args(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["add", "Test todo", "--priority", "high"])
        assert args.title == "Test todo"
        assert args.priority == "high"
        assert args.command == "add"

    def test_list_command_args(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["list", "--queue", "core", "--status", "active"])
        assert args.queue == "core"
        assert args.status == "active"
        assert args.command == "list"

    def test_log_level_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["log-level", "debug"])
        assert args.level == "debug"
        assert args.command == "log-level"

    def test_version_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_health_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["health", "--daemon-url", "http://0:9999"])
        assert args.command == "health"
        assert args.daemon_url == "http://0:9999"

    def test_status_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["status", "--project", "proj-1"])
        assert args.command == "status"
        assert args.project == "proj-1"
        assert args.todo_id is None

    def test_status_command_with_todo_id(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["status", "TODO-42"])
        assert args.command == "status"
        assert args.todo_id == "TODO-42"

    def test_deployments_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["deployments"])
        assert args.command == "deployments"

    def test_smoke_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["smoke", "aws", "ec2-a100", "--live"])
        assert args.command == "smoke"
        assert args.provider == "aws"
        assert args.test == "ec2-a100"
        assert args.live is True
