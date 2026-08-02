"""Unit tests for sandbox CLI commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.cli import main


class TestSandboxSubcommandRegistered:
    def test_sandbox_is_in_subcommands(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        assert "sandbox" in choices, f"sandbox command missing from: {sorted(choices.keys())}"

    def test_sandbox_has_subcommands(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        sandbox_parser = parser._subparsers._group_actions[0].choices["sandbox"]
        sub_actions = [a for a in sandbox_parser._actions if a.dest == "sandbox_command"]
        assert sub_actions, "sandbox parser has no sub-commands"
        choices = sub_actions[0].choices
        assert "list-backends" in choices
        assert "execute" in choices
        assert "config" in choices
        assert "levels" in choices


class TestSandboxListBackends:
    def test_list_backends_dispatches(self) -> None:
        with (
            patch("sys.argv", ["gludd", "sandbox", "list-backends"]),
            patch("general_ludd.cli._cmd_sandbox_list_backends") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_list_backends_runs_without_daemon(self, capsys) -> None:
        with patch("sys.argv", ["gludd", "sandbox", "list-backends"]):
            main()
        captured = capsys.readouterr()
        assert "process" in captured.out
        assert "Sandbox Backends" in captured.out
        assert "container" in captured.out or "docker" in captured.out or "podman" in captured.out
        assert "firecracker" in captured.out
        assert "gvisor" in captured.out


class TestSandboxExecute:
    def test_execute_dispatches(self) -> None:
        with (
            patch("sys.argv", ["gludd", "sandbox", "execute", "--command", "echo hello"]),
            patch("general_ludd.cli._cmd_sandbox_execute") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.command == "echo hello"
            assert args.isolation == "process"
            assert args.memory == 512
            assert args.timeout == 300
            assert args.backend == "auto"
            assert args.allow_network is False

    def test_execute_defaults(self) -> None:
        with (
            patch("sys.argv", ["gludd", "sandbox", "execute", "--command", "echo test"]),
            patch("general_ludd.cli._cmd_sandbox_execute") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.isolation == "process"
            assert args.backend == "auto"
            assert args.allow_network is False

    def test_execute_custom_isolation_and_memory(self) -> None:
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "sandbox",
                    "execute",
                    "--command",
                    "ls",
                    "--isolation",
                    "none",
                    "--memory",
                    "256",
                    "--timeout",
                    "60",
                    "--backend",
                    "process",
                ],
            ),
            patch("general_ludd.cli._cmd_sandbox_execute") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.isolation == "none"
            assert args.memory == 256
            assert args.timeout == 60
            assert args.backend == "process"

    def test_execute_allow_network(self) -> None:
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "sandbox",
                    "execute",
                    "--command",
                    "curl example.com",
                    "--allow-network",
                ],
            ),
            patch("general_ludd.cli._cmd_sandbox_execute") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.allow_network is True

    def test_execute_with_workdir(self) -> None:
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "sandbox",
                    "execute",
                    "--command",
                    "pwd",
                    "--workdir",
                    "/tmp",
                ],
            ),
            patch("general_ludd.cli._cmd_sandbox_execute") as mock_cmd,
        ):
            main()
            args = mock_cmd.call_args[0][0]
            assert args.workdir == "/tmp"

    def test_execute_command_required(self) -> None:
        with patch("sys.argv", ["gludd", "sandbox", "execute"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0

    def test_execute_runs_command_in_process_backend(self, capsys) -> None:
        with patch(
            "sys.argv",
            [
                "gludd",
                "sandbox",
                "execute",
                "--command",
                "echo sandbox_test_output",
                "--isolation",
                "none",
            ],
        ):
            main()
        captured = capsys.readouterr()
        assert "sandbox_test_output" in captured.out

    def test_execute_shows_backend_info(self, capsys) -> None:
        with patch(
            "sys.argv",
            [
                "gludd",
                "sandbox",
                "execute",
                "--command",
                "true",
                "--isolation",
                "process",
            ],
        ):
            main()
        captured = capsys.readouterr()
        assert "Backend:" in captured.out
        assert "Isolation:" in captured.out


class TestSandboxConfig:
    def test_config_dispatches(self) -> None:
        with (
            patch("sys.argv", ["gludd", "sandbox", "config"]),
            patch("general_ludd.cli._cmd_sandbox_config") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_config_shows_minimal_and_strict(self, capsys) -> None:
        with patch("sys.argv", ["gludd", "sandbox", "config"]):
            main()
        captured = capsys.readouterr()
        assert "Minimal Sandbox Config" in captured.out
        assert "Strict Sandbox Config" in captured.out
        assert "backend" in captured.out
        assert "isolation" in captured.out
        assert "fail_open" in captured.out
        assert "process" in captured.out
        assert "firecracker" in captured.out


class TestSandboxLevels:
    def test_levels_dispatches(self) -> None:
        with (
            patch("sys.argv", ["gludd", "sandbox", "levels"]),
            patch("general_ludd.cli._cmd_sandbox_levels") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_levels_lists_all_five(self, capsys) -> None:
        with patch("sys.argv", ["gludd", "sandbox", "levels"]):
            main()
        captured = capsys.readouterr()
        assert "Isolation Levels" in captured.out
        for level_name in ("none", "process", "container", "vm_userspace", "vm_hardware"):
            assert level_name in captured.out, f"Missing level: {level_name}"

    def test_levels_shows_rank_order(self, capsys) -> None:
        with patch("sys.argv", ["gludd", "sandbox", "levels"]):
            main()
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        level_lines = [ln for ln in lines if "rank=" in ln]
        ranks = []
        for line in level_lines:
            import re

            m = re.search(r"rank=(\d+)", line)
            if m:
                ranks.append(int(m.group(1)))
        assert ranks == sorted(ranks), f"Levels not in rank order: {ranks}"


class TestSandboxHelp:
    def test_sandbox_no_subcommand_shows_help_and_exits(self) -> None:
        with patch("sys.argv", ["gludd", "sandbox"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code is not None

    def test_help_includes_sandbox(self) -> None:
        from general_ludd.cli import MAN_PAGE

        assert "sandbox" not in MAN_PAGE.lower(), "MAN_PAGE needs sandbox docs entry"
