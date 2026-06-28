"""Tests for `gludd onboard` CLI subcommand scaffold."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from general_ludd.cli import build_parser, main

SUPPORTED = ("aws", "gcp", "azure")


def _run(args: list[str], capsys, expect_exit: int | None = None) -> int:
    """Invoke the CLI and capture exit code."""
    with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["gludd", *args]):
        main()
    code = int(exc_info.value.code) if exc_info.value.code is not None else 0
    return code


def _help_text() -> str:
    parser, _ = build_parser()
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        parser.parse_args(["onboard", "--help"])
    return buf.getvalue()


class TestOnboardHelp:
    def test_onboard_command_exists(self, capsys):
        text = _help_text()
        code = _run(["onboard", "--help"], capsys)
        assert code == 0
        assert "provider" in text.lower()

    def test_onboard_steps_advertised(self, capsys):
        text = _help_text()
        # All four phases mentioned in help.
        for needle in ("IAM", "token", "validat"):
            assert needle.lower() in text.lower()

    def test_onboard_supports_aws_gcp_azure(self, capsys):
        text = _help_text()
        for p in SUPPORTED:
            assert p in text

    def test_onboard_requires_provider(self, capsys):
        # No provider arg -> error exit, supported list mentioned.
        code = _run(["onboard"], capsys)
        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        assert code != 0
        assert "provider" in combined
        for p in SUPPORTED:
            assert p in combined

    def test_onboard_rejects_unknown_provider(self, capsys):
        code = _run(["onboard", "fly"], capsys)
        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        assert code != 0
        for p in SUPPORTED:
            assert p in combined


class TestOnboardDryRun:
    def test_onboard_dry_run_skips_live_calls(self, capsys, tmp_path):
        # --dry-run should not call any cloud API and should not hang on a prompt.
        # Provide --token so we never hit an interactive prompt.
        cfg_dir = tmp_path / "config"
        code = _run(
            [
                "onboard",
                "aws",
                "--dry-run",
                "--token",
                "fake-token",
                "--role-arn",
                "arn:aws:iam::000000000000:role/gludd",
                "--region",
                "us-east-1",
                "--config-dir",
                str(cfg_dir),
            ],
            capsys,
        )
        out = capsys.readouterr().out
        assert code == 0
        # Dry-run markers present.
        assert "phase" in out.lower()


class TestOnboardToken:
    def test_onboard_non_interactive_token_arg(self, capsys, tmp_path):
        # With --token supplied, no prompt should be triggered; the flow runs.
        cfg_dir = tmp_path / "config"
        # If a prompt was triggered, the test would block/fail; we run with dry-run
        # so no live API call is made.
        code = _run(
            [
                "onboard",
                "aws",
                "--dry-run",
                "--token",
                "abc123",
                "--role-arn",
                "arn:aws:iam::000000000000:role/gludd",
                "--region",
                "us-east-1",
                "--config-dir",
                str(cfg_dir),
            ],
            capsys,
        )
        assert code == 0


class TestOnboardObservability:
    def test_onboard_logs_steps_to_stdout(self, capsys, tmp_path):
        cfg_dir = tmp_path / "config"
        code = _run(
            [
                "onboard",
                "aws",
                "--dry-run",
                "--token",
                "abc123",
                "--role-arn",
                "arn:aws:iam::000000000000:role/gludd",
                "--region",
                "us-east-1",
                "--config-dir",
                str(cfg_dir),
            ],
            capsys,
        )
        out = capsys.readouterr().out
        assert code == 0
        # At least 4 phase headers should appear.
        assert out.lower().count("phase ") >= 4


class TestOnboardConfigWrite:
    def test_onboard_writes_config_file(self, capsys, tmp_path):
        cfg_dir = tmp_path / "config"
        code = _run(
            [
                "onboard",
                "aws",
                "--dry-run",
                "--token",
                "abc123",
                "--role-arn",
                "arn:aws:iam::000000000000:role/gludd",
                "--region",
                "us-east-1",
                "--config-dir",
                str(cfg_dir),
            ],
            capsys,
        )
        assert code == 0
        cfg_file = cfg_dir / "onboarded-provider.json"
        assert cfg_file.exists(), f"Expected config at {cfg_file}"
        data = json.loads(cfg_file.read_text())
        assert data["provider"] == "aws"
        assert data["role_arn"].startswith("arn:aws:iam::")
        assert data["region"] == "us-east-1"
        assert "token_validated_at" in data
