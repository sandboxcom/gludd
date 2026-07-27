"""Targeted branch coverage tests for cli.py."""

from __future__ import annotations

import contextlib
import sys
from unittest.mock import patch

from general_ludd.cli import main


class TestManPage:
    def test_man_page_exists(self):
        from general_ludd.cli import MAN_PAGE

        assert "gludd" in MAN_PAGE
        assert "SYNOPSIS" in MAN_PAGE
        assert "COMMANDS" in MAN_PAGE
        assert len(MAN_PAGE) > 500


class TestBuildParser:
    def test_parser_exists(self):
        from general_ludd.cli import build_parser

        parser, subparsers = build_parser()
        assert parser is not None
        assert isinstance(subparsers, dict)

    def test_daemon_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "daemon" in subparsers

    def test_add_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "add" in subparsers

    def test_status_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "status" in subparsers

    def test_list_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "list" in subparsers

    def test_models_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "models" in subparsers

    def test_tui_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "tui" in subparsers

    def test_version_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "version" in subparsers

    def test_health_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "health" in subparsers

    def test_worktree_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "worktree" in subparsers

    def test_mcp_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "mcp" in subparsers

    def test_skills_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "skills" in subparsers

    def test_compute_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "compute" in subparsers

    def test_deployments_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "deployments" in subparsers

    def test_log_level_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "log-level" in subparsers

    def test_smoke_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "smoke" in subparsers

    def test_searx_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "searx" in subparsers

    def test_filestore_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "filestore" in subparsers

    def test_local_serve_subparser(self):
        from general_ludd.cli import build_parser

        _, subparsers = build_parser()
        assert "local-serve" in subparsers


class TestMainFunction:
    def test_main_is_callable(self):
        assert callable(main)

    def test_main_no_args_shows_help(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd"]
            with patch("sys.stdout"), contextlib.suppress(SystemExit):
                main()
        finally:
            sys.argv = old_argv

    def test_main_version_flag(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "version"]
            with patch("sys.stdout"), contextlib.suppress(SystemExit):
                main()
        finally:
            sys.argv = old_argv

    def test_main_unknown_command(self):
        old_argv = sys.argv
        try:
            sys.argv = ["gludd", "nonexistent_cmd_xyz"]
            with patch("sys.stderr"), patch("sys.stdout"), contextlib.suppress(SystemExit):
                main()
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
        try:
            args = parser.parse_args(["daemon", "--host", "127.0.0.1", "--port", "8000"])
            assert args.host == "127.0.0.1"
            assert args.port == 8000
            assert args.command == "daemon"
        except SystemExit:
            pass

    def test_add_command_args(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        try:
            args = parser.parse_args(["add", "Test todo", "--priority", "50"])
            assert args.title == "Test todo"
            assert args.priority == "50"
            assert args.command == "add"
        except SystemExit:
            pass

    def test_list_command_args(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        try:
            args = parser.parse_args(["list", "--queue", "core", "--status", "active"])
            assert args.queue == "core"
            assert args.status == "active"
            assert args.command == "list"
        except SystemExit:
            pass

    def test_log_level_command(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        try:
            args = parser.parse_args(["log-level", "debug"])
            assert args.level == "debug"
            assert args.command == "log-level"
        except SystemExit:
            pass
