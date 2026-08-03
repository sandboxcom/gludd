"""Unit tests for sandbox CLI — verifies sandbox is NOT registered as a subcommand."""

from __future__ import annotations

import pytest


class TestSandboxNotRegistered:
    def test_sandbox_not_in_subcommands(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        assert "sandbox" not in choices, (
            f"sandbox should not be a CLI subcommand but is present in: {sorted(choices.keys())}"
        )

    def test_sandbox_raises_keyerror(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        with pytest.raises(KeyError):
            _ = parser._subparsers._group_actions[0].choices["sandbox"]

    def test_help_does_not_mention_sandbox(self) -> None:
        from general_ludd.cli import MAN_PAGE

        assert "sandbox" not in MAN_PAGE.lower(), "MAN_PAGE should not reference sandbox"
