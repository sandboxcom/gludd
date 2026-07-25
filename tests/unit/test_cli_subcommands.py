"""Structural tests for CLI subcommand registry."""

from __future__ import annotations

import sys

import pytest


def _get_subcommands() -> list[str]:
    """Parse top-level subcommands from the parser built by cli.py."""
    from general_ludd.cli import build_parser

    parser, _ = build_parser()
    # argparse stores subparsers in a dict keyed by subcommand name
    try:
        choices = parser._subparsers._group_actions[0].choices
    except (AttributeError, IndexError):
        # argparse >= 3.12 stores subparsers differently
        choices = {
            action.dest: action
            for action in parser._actions
            if hasattr(action, "choices") and action.dest == "command"
        }
        if "command" in choices and hasattr(choices["command"], "choices"):
            choices = choices["command"].choices
        else:
            choices = {}
    return list(choices.keys())


class TestRemovedSubcommands:
    """Verify that physics, payment, account are NOT top-level subcommands."""

    def test_physics_not_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "physics" not in subcmds, (
            f"physics should NOT be a top-level subcommand, "
            f"but found in: {subcmds}"
        )

    def test_payment_not_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "payment" not in subcmds, (
            f"payment should NOT be a top-level subcommand, "
            f"but found in: {subcmds}"
        )

    def test_account_not_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "account" not in subcmds, (
            f"account should NOT be a top-level subcommand, "
            f"but found in: {subcmds}"
        )


class TestConsolidatedTestSubcommands:
    """Verify test subcommand tree has background/self/smoke."""

    def test_test_is_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "test" in subcmds, f"test should be a top-level subcommand, found: {subcmds}"

    def test_test_background_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = parser._subparsers._group_actions[0].choices.get("test")
        assert test_parser is not None, "test subparser not found"
        # Subparsers under test
        test_sub_actions = [
            a for a in test_parser._actions if a.dest == "test_command"
        ]
        assert test_sub_actions, "test subparser has no sub-commands"
        test_sub_choices = test_sub_actions[0].choices
        assert "background" in test_sub_choices, (
            f"'background' not in test sub-commands: {list(test_sub_choices.keys())}"
        )

    def test_test_self_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = parser._subparsers._group_actions[0].choices.get("test")
        assert test_parser is not None, "test subparser not found"
        test_sub_actions = [
            a for a in test_parser._actions if a.dest == "test_command"
        ]
        assert test_sub_actions, "test subparser has no sub-commands"
        test_sub_choices = test_sub_actions[0].choices
        assert "self" in test_sub_choices, (
            f"'self' not in test sub-commands: {list(test_sub_choices.keys())}"
        )

    def test_test_smoke_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = parser._subparsers._group_actions[0].choices.get("test")
        assert test_parser is not None, "test subparser not found"
        test_sub_actions = [
            a for a in test_parser._actions if a.dest == "test_command"
        ]
        assert test_sub_actions, "test subparser has no sub-commands"
        test_sub_choices = test_sub_actions[0].choices
        assert "smoke" in test_sub_choices, (
            f"'smoke' not in test sub-commands: {list(test_sub_choices.keys())}"
        )

    def test_testbg_not_standalone(self) -> None:
        """Standalone test-bg should NOT be a top-level command."""
        subcmds = _get_subcommands()
        assert "test-bg" not in subcmds, (
            f"test-bg should NOT be a standalone top-level command, "
            f"found: {subcmds}"
        )

    def test_selftest_not_standalone(self) -> None:
        """Standalone selftest should NOT be a top-level command."""
        subcmds = _get_subcommands()
        assert "selftest" not in subcmds, (
            f"selftest should NOT be a standalone top-level command, "
            f"found: {subcmds}"
        )

    def test_smoke_not_standalone(self) -> None:
        """Standalone smoke should NOT be a top-level command."""
        subcmds = _get_subcommands()
        assert "smoke" not in subcmds, (
            f"smoke should NOT be a standalone top-level command, "
            f"found: {subcmds}"
        )


class TestModulesStillImportable:
    """The original CLI modules are retained for programmatic use."""

    def test_cli_physics_importable(self) -> None:
        from general_ludd.cli_physics import add_physics_subparser

        assert callable(add_physics_subparser)

    def test_cli_payment_importable(self) -> None:
        from general_ludd.cli_payment import register

        assert callable(register)

    def test_cli_account_importable(self) -> None:
        from general_ludd.cli_account import add_account_subparser

        assert callable(add_account_subparser)
