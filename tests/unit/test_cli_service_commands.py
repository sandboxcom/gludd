"""Unit tests for cli_service_commands."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from general_ludd.cli_service_commands import (
    _SERVICE_DISPATCH,
    _cmd_catalog,
    _cmd_discover,
    _cmd_show,
    add_service_subparser,
    main,
)


class TestAddServiceSubparser:
    def test_adds_discover_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_service_subparser(subparsers)

        ns = parser.parse_args(["service", "discover"])
        assert ns.service_command == "discover"

    def test_adds_catalog_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_service_subparser(subparsers)

        ns = parser.parse_args(["service", "catalog"])
        assert ns.service_command == "catalog"

    def test_adds_show_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_service_subparser(subparsers)

        ns = parser.parse_args(["service", "show", "myservice"])
        assert ns.service_command == "show"
        assert ns.name == "myservice"

    def test_discover_defaults(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_service_subparser(subparsers)

        ns = parser.parse_args(["service", "discover"])
        assert ns.searx_url == "http://localhost:8888"

    def test_discover_terms_option(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_service_subparser(subparsers)

        ns = parser.parse_args(["service", "discover", "--term", "ai", "--term", "ml"])
        assert ns.terms == ["ai", "ml"]


class TestServiceDispatch:
    def test_dispatches_discover(self):
        assert _SERVICE_DISPATCH["discover"] is _cmd_discover

    def test_dispatches_catalog(self):
        assert _SERVICE_DISPATCH["catalog"] is _cmd_catalog

    def test_dispatches_show(self):
        assert _SERVICE_DISPATCH["show"] is _cmd_show

    def test_unknown_handler_returns_none(self):
        assert _SERVICE_DISPATCH.get("nonexistent") is None


class TestMain:
    def test_no_args_returns_1(self):
        with patch("sys.argv", ["service"]):
            result = main()
            assert result == 1

    def test_unknown_command_returns_1(self):
        with patch("sys.argv", ["service", "nonexistent"]):
            result = main()
            assert result == 1

    def test_valid_command_dispatches(self):
        with patch(
            "general_ludd.cli_service_commands._cmd_show", return_value=0
        ) as mock_handler:
            result = mock_handler([])
            assert result == 0
            mock_handler.assert_called_once()


class TestCmdCatalog:
    def test_returns_zero(self):
        args = MagicMock()
        args.path = "/nonexistent/path.yml"
        with patch("general_ludd.cli_service_commands.ServiceCatalog") as mock_cat:
            instance = mock_cat.return_value
            instance.services = {}
            result = _cmd_catalog(args)
            assert result == 0


class TestCmdShow:
    def test_not_found_returns_1(self):
        args = MagicMock()
        args.name = "nonexistent"
        args.path = "/nonexistent/path.yml"

        catalog = MagicMock()
        catalog.services = {}
        with patch("general_ludd.cli_service_commands.ServiceCatalog", return_value=catalog):
            result = _cmd_show(args)
            assert result == 1

    def test_found_displays_details(self, capsys):
        from general_ludd.infra.service_catalog import DiscoveredService

        svc = DiscoveredService(
            name="myservice",
            url="http://example.com",
            api_docs_url="http://example.com/docs",
            pricing_url="http://example.com/pricing",
            status="active",
            description="A test service",
            source_engine="searx",
        )

        catalog = MagicMock()
        catalog.services = {"myservice": svc}

        args = MagicMock()
        args.name = "myservice"
        args.path = "/n/p.yml"

        with patch("general_ludd.cli_service_commands.ServiceCatalog", return_value=catalog):
            result = _cmd_show(args)
            assert result == 0
