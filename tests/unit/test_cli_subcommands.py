"""Structural tests for CLI subcommand registry."""

from __future__ import annotations

import argparse
from typing import cast


def _subparser_choices(
    parser: argparse.ArgumentParser,
    destination: str,
) -> dict[str, argparse.ArgumentParser]:
    """Return the typed choices for one nested subparser destination."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and action.dest == destination:
            return cast(dict[str, argparse.ArgumentParser], action.choices)
    raise AssertionError(f"subparser destination not found: {destination}")


def _named_subparser(
    parser: argparse.ArgumentParser,
    destination: str,
    name: str,
) -> argparse.ArgumentParser:
    """Return a required named parser from a nested subparser collection."""
    result = _subparser_choices(parser, destination).get(name)
    assert result is not None, f"{name} subparser not found"
    return result


def _get_subcommands() -> list[str]:
    """Parse top-level subcommands from the parser built by cli.py."""
    from general_ludd.cli import build_parser

    parser, _ = build_parser()
    return list(_subparser_choices(parser, "command"))


class TestRemovedSubcommands:
    """Verify that superseded physics and account commands stay removed."""

    def test_physics_not_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "physics" not in subcmds, f"physics should NOT be a top-level subcommand, but found in: {subcmds}"

    def test_account_not_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "account" not in subcmds, f"account should NOT be a top-level subcommand, but found in: {subcmds}"


class TestPaymentSubcommand:
    """The documented secure vault remains directly usable by operators."""

    def test_payment_is_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "payment" in subcmds, f"payment command missing from: {subcmds}"


class TestConsolidatedTestSubcommands:
    """Verify test subcommand tree has background/self/smoke."""

    def test_test_is_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "test" in subcmds, f"test should be a top-level subcommand, found: {subcmds}"

    def test_test_background_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = _named_subparser(parser, "command", "test")
        test_sub_choices = _subparser_choices(test_parser, "test_command")
        assert "background" in test_sub_choices, (
            f"'background' not in test sub-commands: {list(test_sub_choices.keys())}"
        )

    def test_test_self_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = _named_subparser(parser, "command", "test")
        test_sub_choices = _subparser_choices(test_parser, "test_command")
        assert "self" in test_sub_choices, f"'self' not in test sub-commands: {list(test_sub_choices.keys())}"

    def test_test_smoke_is_suboption(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        test_parser = _named_subparser(parser, "command", "test")
        test_sub_choices = _subparser_choices(test_parser, "test_command")
        assert "smoke" in test_sub_choices, f"'smoke' not in test sub-commands: {list(test_sub_choices.keys())}"

    def test_testbg_not_standalone(self) -> None:
        """Standalone test-bg should NOT be a top-level command."""
        subcmds = _get_subcommands()
        assert "test-bg" not in subcmds, f"test-bg should NOT be a standalone top-level command, found: {subcmds}"

    def test_selftest_compatibility_alias_is_standalone(self) -> None:
        """Existing automation can keep using the legacy top-level spelling."""
        subcmds = _get_subcommands()
        assert "selftest" in subcmds, f"selftest compatibility alias missing from: {subcmds}"

    def test_selftest_alias_matches_canonical_command(self) -> None:
        from general_ludd.cli import _cmd_selftest, build_parser

        parser, _ = build_parser()
        url = "http://localhost:9123"
        legacy = parser.parse_args(["selftest", "--daemon-url", url])
        canonical = parser.parse_args(["test", "self", "--daemon-url", url])

        assert legacy.daemon_url == canonical.daemon_url == url
        assert legacy.func is canonical.func is _cmd_selftest

    def test_smoke_canonical_command_is_standalone(self) -> None:
        """The documented canonical smoke spelling remains top-level."""
        subcmds = _get_subcommands()
        assert "smoke" in subcmds, f"smoke compatibility command missing from: {subcmds}"


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


class TestCloudIamSubcommand:
    """Verify cloud iam subcommand tree exists and is wired correctly."""

    def test_cloud_is_in_subcommands(self) -> None:
        subcmds = _get_subcommands()
        assert "cloud" in subcmds, f"cloud should be a top-level subcommand, found: {subcmds}"

    def test_cloud_iam_is_subcommand(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        cloud_parser = _named_subparser(parser, "command", "cloud")
        cloud_sub_choices = _subparser_choices(cloud_parser, "cloud_command")
        assert "iam" in cloud_sub_choices, f"'iam' not in cloud sub-commands: {list(cloud_sub_choices.keys())}"

    def test_cloud_iam_generate_is_subcommand(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        cloud_parser = _named_subparser(parser, "command", "cloud")
        iam_parser = _named_subparser(cloud_parser, "cloud_command", "iam")
        iam_sub_choices = _subparser_choices(iam_parser, "iam_command")
        assert "generate" in iam_sub_choices, f"'generate' not in iam sub-commands: {list(iam_sub_choices.keys())}"

    def test_cloud_iam_validate_is_subcommand(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        cloud_parser = _named_subparser(parser, "command", "cloud")
        iam_parser = _named_subparser(cloud_parser, "cloud_command", "iam")
        iam_sub_choices = _subparser_choices(iam_parser, "iam_command")
        assert "validate" in iam_sub_choices, f"'validate' not in iam sub-commands: {list(iam_sub_choices.keys())}"

    def test_cloud_iam_generate_parses_provider_and_persona(self) -> None:
        from general_ludd.cli import _cmd_cloud_iam_generate, build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["cloud", "iam", "generate", "--provider", "aws", "--persona", "monitor"])
        assert args.provider == "aws"
        assert args.persona == "monitor"
        assert args.func is _cmd_cloud_iam_generate

    def test_cloud_iam_generate_defaults_persona(self) -> None:
        from general_ludd.cli import _cmd_cloud_iam_generate, build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["cloud", "iam", "generate", "--provider", "azure"])
        assert args.provider == "azure"
        assert args.persona == "monitor"
        assert args.func is _cmd_cloud_iam_generate

    def test_cloud_iam_validate_parses_provider_and_file(self) -> None:
        from general_ludd.cli import _cmd_cloud_iam_validate, build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["cloud", "iam", "validate", "--provider", "gcp", "--file", "/tmp/role.json"])
        assert args.provider == "gcp"
        assert args.file == "/tmp/role.json"
        assert args.func is _cmd_cloud_iam_validate
