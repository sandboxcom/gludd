"""Compatibility contract for the decomposed CLI entrypoint."""

from __future__ import annotations

import inspect

import general_ludd.cli as cli


def test_cli_entrypoint_stays_below_project_line_limit() -> None:
    """The public entrypoint must remain a facade, not become monolithic again."""
    assert len(inspect.getsource(cli).splitlines()) < 2500


def test_cli_facade_reexports_extracted_helpers() -> None:
    """Legacy imports keep resolving to the extracted implementations."""
    from general_ludd.cli_commands import daemon_control, platform, tui_views

    assert cli._build_daemon_start_cmd is daemon_control.build_daemon_start_cmd
    assert cli._gather_offline_status is platform.gather_offline_status
    assert cli._build_health_table is tui_views.build_health_table


def test_parser_cache_observes_legacy_handler_monkeypatch(monkeypatch) -> None:
    """Parser extraction must retain the established cli-module patch seam."""

    def replacement(_args: object) -> None:
        return None

    monkeypatch.setattr(cli, "_cmd_health", replacement)
    parser, _subcommands = cli.build_parser()

    assert parser.parse_args(["health"]).func is replacement
