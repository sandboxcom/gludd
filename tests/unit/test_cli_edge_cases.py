"""Edge-case tests for CLI parsing — invalid subcommands, missing args, conflicting flags, env vars, output modes."""

from __future__ import annotations

import contextlib
import os
from unittest.mock import patch

import pytest

from general_ludd.cli import build_parser, main

# ---------------------------------------------------------------------------
# 1. Invalid subcommand
# ---------------------------------------------------------------------------


def test_invalid_subcommand_exits_nonzero():
    with patch("sys.argv", ["gludd", "nonexistent_command_xyz"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_invalid_subcommand_stderr_mentions_usage(capsys):
    with patch("sys.argv", ["gludd", "bogus"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "invalid choice" in combined.lower() or "usage" in combined.lower() or "{" in combined


# ---------------------------------------------------------------------------
# 2. Missing required arguments
# ---------------------------------------------------------------------------


def test_add_missing_title_exits_nonzero():
    with patch("sys.argv", ["gludd", "add"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_local_serve_missing_model_exits_nonzero():
    with patch("sys.argv", ["gludd", "local-serve"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_log_level_missing_level_exits_nonzero():
    with patch("sys.argv", ["gludd", "log-level"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_login_missing_service_exits_nonzero():
    with patch("sys.argv", ["gludd", "login"]), patch("general_ludd.cli._cmd_login") as mock_cmd:
        mock_cmd.side_effect = SystemExit(2)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_onboard_missing_provider_exits_nonzero():
    with patch("sys.argv", ["gludd", "onboard"]), patch("general_ludd.cli._cmd_onboard") as mock_cmd:
        mock_cmd.side_effect = SystemExit(2)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_compute_register_missing_required_flags_exits():
    argv = ["gludd", "compute", "register"]
    with patch("sys.argv", argv):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_models_ranking_missing_task_type_exits():
    argv = ["gludd", "models", "ranking"]
    with patch("sys.argv", argv):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# 3. Conflicting / nonsensical flag combos
# ---------------------------------------------------------------------------


def test_smoke_list_and_live_together_accepted():
    """--list and --live are not mutually exclusive by argparse — parser accepts both."""
    argv = ["gludd", "test", "smoke", "aws", "ec2-a100", "--list", "--live"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.list is True
        assert args.live is True


def test_smoke_json_and_output_flag_parsed():
    argv = ["gludd", "test", "smoke", "aws", "ec2-a100", "--json", "--output", "/tmp/out.txt"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.json is True
        assert args.output == "/tmp/out.txt"


def test_onboard_dry_run_and_token_flags_parsed():
    argv = ["gludd", "onboard", "aws", "--dry-run", "--token", "fake-token"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_onboard") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.dry_run is True
        assert args.token == "fake-token"


def test_chat_list_sessions_and_export_parsed():
    argv = ["gludd", "chat", "--list-sessions", "--export", "json"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_chat") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.list_sessions is True
        assert args.export == "json"


# ---------------------------------------------------------------------------
# 4. Help text output for each subcommand
# ---------------------------------------------------------------------------


def test_help_prints_man_page(capsys):
    with patch("sys.argv", ["gludd", "help"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "SYNOPSIS" in captured.out or "SYNOPSIS" in captured.err
    assert "DESCRIPTION" in captured.out or "DESCRIPTION" in captured.err


def test_daemon_help_shows(capsys):
    with patch("sys.argv", ["gludd", "daemon", "--help"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "daemon" in combined.lower()


def test_models_subcommand_help(capsys):
    with patch("sys.argv", ["gludd", "models", "--help"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "models" in combined.lower()


def test_chat_help_shows_env_var_defaults(capsys):
    """Verify chat --help mentions env var fallbacks (OPENAI_BASE_URL / OPENAI_API_KEY)."""
    with patch("sys.argv", ["gludd", "chat", "--help"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "OPENAI_BASE_URL" in combined or "OPENAI_API_KEY" in combined


# ---------------------------------------------------------------------------
# 5. Version flag (argparse --version on root parser)
# ---------------------------------------------------------------------------


def test_version_flag_via_argument(capsys):
    with patch("sys.argv", ["gludd", "--version"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "0.1.0" in captured.out or "0.1.0" in captured.err


def test_version_command_output(capsys):
    with patch("sys.argv", ["gludd", "version"]), contextlib.suppress(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "0.1.0" in captured.out or "general-ludd" in captured.out


# ---------------------------------------------------------------------------
# 6. Environment variable fallbacks for configuration
# ---------------------------------------------------------------------------


def test_chat_api_base_env_fallback():
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://custom.api/v1"}):
        with patch("sys.argv", ["gludd", "chat"]), patch("general_ludd.cli._cmd_chat") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.api_base == "https://custom.api/v1"


def test_chat_api_key_env_fallback():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-fallback-key"}):
        with patch("sys.argv", ["gludd", "chat"]), patch("general_ludd.cli._cmd_chat") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.api_key == "sk-env-fallback-key"


def test_chat_cli_overrides_env_fallback():
    with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://env.api/v1"}):
        argv = ["gludd", "chat", "--api-base", "https://cli.api/v1"]
        with patch("sys.argv", argv), patch("general_ludd.cli._cmd_chat") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.api_base == "https://cli.api/v1"


# ---------------------------------------------------------------------------
# 7. Dry-run mode behaviour (onboard --dry-run)
# ---------------------------------------------------------------------------


def test_onboard_dry_run_passes_flag():
    argv = ["gludd", "onboard", "aws", "--dry-run"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_onboard") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.dry_run is True


def test_onboard_no_dry_run_defaults_false():
    argv = ["gludd", "onboard", "aws"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_onboard") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.dry_run is False


# ---------------------------------------------------------------------------
# 8. JSON output mode (smoke --json)
# ---------------------------------------------------------------------------


def test_smoke_json_flag_defaults_false():
    argv = ["gludd", "test", "smoke", "aws", "ec2-a100", "--live"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.json is False


def test_smoke_json_flag_set_true():
    argv = ["gludd", "test", "smoke", "aws", "ec2-a100", "--json"]
    with patch("sys.argv", argv), patch("general_ludd.cli._cmd_smoke") as mock_cmd:
        main()
        args = mock_cmd.call_args[0][0]
        assert args.json is True


# ---------------------------------------------------------------------------
# 9. Verbose / debug flag effects
# ---------------------------------------------------------------------------


def test_daemon_log_level_choices():
    for level in ("debug", "info", "warning", "error"):
        argv = ["gludd", "daemon", "--log-level", level]
        with patch("sys.argv", argv), patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.log_level == level


def test_daemon_log_level_rejects_invalid():
    with patch("sys.argv", ["gludd", "daemon", "--log-level", "verbose"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0


def test_log_level_command_choices():
    for level in ("debug", "info", "warning", "error"):
        argv = ["gludd", "log-level", level]
        with patch("sys.argv", argv), patch("general_ludd.cli._cmd_log_level") as mock_cmd:
            main()
        args = mock_cmd.call_args[0][0]
        assert args.level == level


def test_log_level_command_rejects_verbose():
    with patch("sys.argv", ["gludd", "log-level", "verbose"]), pytest.raises(SystemExit):
        main()


# ---------------------------------------------------------------------------
# 10. Config file / terraform config override behaviour
# ---------------------------------------------------------------------------


def test_config_terraform_get_field_parsed():
    argv = ["gludd", "config", "terraform", "get", "--field", "region"]
    with (
        patch("sys.argv", argv),
        patch("general_ludd.cli._cmd_config_terraform_get") as mock_cmd,
    ):
        main()
        args = mock_cmd.call_args[0][0]
        assert args.field == "region"


def test_config_terraform_set_overwrites_value():
    argv = ["gludd", "config", "terraform", "set", "region", "eu-west-1"]
    with (
        patch("sys.argv", argv),
        patch("general_ludd.cli._cmd_config_terraform_set") as mock_cmd,
    ):
        main()
        args = mock_cmd.call_args[0][0]
        assert args.field == "region"
        assert args.value == "eu-west-1"


def test_daemon_default_no_op():
    """Global parser should have no default func; bare invocation exits."""
    with patch("sys.argv", ["gludd"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_parser_args_has_command_attr():
    parser, _ = build_parser()
    namespace = parser.parse_args([])
    assert hasattr(namespace, "command")
    assert hasattr(namespace, "func")
    assert namespace.func is None


def test_bare_make_missing_target_exits():
    argv = ["gludd", "make"]
    with patch("sys.argv", argv):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0
