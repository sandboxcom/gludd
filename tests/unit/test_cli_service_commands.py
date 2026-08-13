"""Unit tests for cli_service_commands."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
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


class TestCmdDiscover:
    def test_reports_discovered_services_and_errors(self, capsys):
        args = argparse.Namespace(
            searx_url="https://search.example",
            catalog_path="/tmp/catalog.yml",
            terms=["gpu"],
        )
        report = SimpleNamespace(
            new_services=["accelerator-api"],
            retired_services=[],
            changed_services=["model-api"],
            total_discovered=2,
            errors=["one endpoint unavailable"],
        )
        pipeline = MagicMock()
        pipeline.run_discovery_pipeline.return_value = report

        with (
            patch("general_ludd.cli_service_commands.SearXConnector") as connector,
            patch("general_ludd.cli_service_commands.ServiceCatalog") as catalog,
            patch(
                "general_ludd.cli_service_commands.ServiceDiscoveryPipeline",
                return_value=pipeline,
            ) as pipeline_factory,
        ):
            assert _cmd_discover(args) == 0

        connector.assert_called_once_with({"base_url": "https://search.example"})
        catalog.assert_called_once_with("/tmp/catalog.yml")
        pipeline_factory.assert_called_once_with(
            searx_url="https://search.example",
            catalog_path="/tmp/catalog.yml",
            search_terms=["gpu"],
        )
        output = capsys.readouterr().out
        assert "NEW: accelerator-api" in output
        assert "ERROR: one endpoint unavailable" in output

    def test_connector_construction_failure_returns_one(self, caplog):
        args = argparse.Namespace(
            searx_url="bad-url",
            catalog_path="/tmp/catalog.yml",
            terms=None,
        )
        with patch(
            "general_ludd.cli_service_commands.SearXConnector",
            side_effect=ValueError("invalid endpoint"),
        ):
            assert _cmd_discover(args) == 1
        assert "invalid endpoint" in caplog.text


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
