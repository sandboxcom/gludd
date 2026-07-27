"""Integration tests for the governance collection: CLI → loader → module_utils.

Exercises the full pipeline: dynamic module loading via importlib, CLI subcommand
registration, and cross-domain lookups across multiple knowledge modules.
"""

from __future__ import annotations

import argparse
from io import StringIO

import pytest

from general_ludd.governance.cli_governance import add_governance_subparser
from general_ludd.governance.loader import (
    clear_cache,
    get_authority_registry,
    get_borders,
    get_civic_services,
    get_classification_markings,
    get_conflicts_treaties,
    get_decision_makers,
    get_elections_voting,
    get_governing_bodies,
    get_info_classification,
    get_international_relations,
    get_jurisdictions,
    get_legal_systems,
    get_licenses_permits,
    get_military_service,
    get_postal_delivery,
    get_public_finance,
    get_tax_currency,
)


@pytest.fixture(autouse=True)
def _reset_loader_cache() -> None:
    clear_cache()


# ── A: Loader integration — 10+ domains load and function ─────────────────


class TestLoaderIntegration:
    """Every domain module loads via the loader and exposes expected attributes."""

    def test_borders_loads_and_lookups(self):
        mod = get_borders()
        assert hasattr(mod, "BORDER_DATA")
        assert hasattr(mod, "lookup_border")
        result = mod.lookup_border("US-Canada land border")
        assert result is not None
        assert "type" in result

    def test_governing_bodies_loads_and_lookups(self):
        mod = get_governing_bodies()
        assert hasattr(mod, "INTERNATIONAL_BODIES")
        assert hasattr(mod, "lookup_body")
        body = mod.lookup_body("un")
        assert body is not None
        assert body["name"] == "United Nations"

    def test_tax_currency_loads_and_lookups(self):
        mod = get_tax_currency()
        assert hasattr(mod, "TAX_DATA")
        assert hasattr(mod, "TAX_CURRENCY")
        usd_info = mod.get_currency_info("USD")
        assert usd_info is not None
        assert "dollar" in str(usd_info).lower() or "USD" in str(usd_info)

    def test_jurisdictions_loads_and_lookups(self):
        mod = get_jurisdictions()
        assert hasattr(mod, "JURISDICTION_CODES")
        assert hasattr(mod, "get_jurisdiction")
        jur = mod.get_jurisdiction("US")
        assert jur is not None
        assert jur.get("alpha_3") == "USA"
        assert jur.get("sovereignty") == "sovereign"

    def test_civic_services_loads_and_lookups(self):
        mod = get_civic_services()
        assert hasattr(mod, "CIVIC_SERVICES")
        assert len(mod.CIVIC_SERVICES) > 0
        assert hasattr(mod, "lookup_service")
        result = mod.lookup_service("passport", "US")
        assert result is not None

    def test_elections_voting_loads_and_lookups(self):
        mod = get_elections_voting()
        assert hasattr(mod, "COUNTRY_ELECTIONS")
        assert hasattr(mod, "lookup_elections")
        result = mod.lookup_elections("US")
        assert result is not None

    def test_international_relations_loads_and_lookups(self):
        mod = get_international_relations()
        assert hasattr(mod, "ALLIANCES")
        assert hasattr(mod, "lookup_diplomatic_relations")
        result = mod.lookup_diplomatic_relations("US")
        assert result is not None
        assert "found" in result

    def test_conflicts_treaties_loads_and_lookups(self):
        mod = get_conflicts_treaties()
        assert hasattr(mod, "TREATIES")
        assert hasattr(mod, "TREATY_DATABASE")
        assert hasattr(mod, "lookup_treaties")
        result = mod.lookup_treaties("nato")
        assert result is not None

    def test_legal_systems_loads_and_lookups(self):
        mod = get_legal_systems()
        assert hasattr(mod, "COUNTRY_LEGAL_SYSTEMS")
        assert hasattr(mod, "lookup_legal_system")
        result = mod.lookup_legal_system("US")
        assert result is not None
        assert "found" in result

    def test_public_finance_loads_and_lookups(self):
        mod = get_public_finance()
        assert hasattr(mod, "COUNTRY_BUDGETS")
        assert hasattr(mod, "lookup_budget")
        result = mod.lookup_budget("US")
        assert result is not None

    def test_classification_markings_loads_and_lookups(self):
        mod = get_classification_markings()
        assert hasattr(mod, "BANNER_FORMATS")
        assert hasattr(mod, "list_systems")
        systems = mod.list_systems()
        assert len(systems) > 0
        assert "US" in systems

    def test_authority_registry_loads_and_lookups(self):
        mod = get_authority_registry()
        assert hasattr(mod, "AUTHORITY_INSTRUMENTS")
        assert hasattr(mod, "get_authority")
        result = mod.get_authority("US-DOS")
        assert result is not None
        assert result["name"] == "U.S. Department of State"

    def test_info_classification_loads_and_lookups(self):
        mod = get_info_classification()
        assert hasattr(mod, "CLASSIFICATION_BY_COUNTRY")
        assert hasattr(mod, "get_classification_system")
        result = mod.get_classification_system("US")
        assert result is not None

    def test_decision_makers_loads_and_lookups(self):
        mod = get_decision_makers()
        assert hasattr(mod, "DECISION_MAKERS")
        assert hasattr(mod, "lookup_decision_makers")
        result = mod.lookup_decision_makers("US")
        assert result is not None
        assert "found" in result

    def test_postal_delivery_loads_and_lookups(self):
        mod = get_postal_delivery()
        assert hasattr(mod, "POSTAL_CODE_PATTERNS")
        assert hasattr(mod, "get_postal_code_pattern")
        result = mod.get_postal_code_pattern("US")
        assert result is not None

    def test_military_service_loads_and_lookups(self):
        mod = get_military_service()
        assert hasattr(mod, "CONSCRIPTION_DATA")
        assert hasattr(mod, "get_conscription_info")
        result = mod.get_conscription_info("US")
        assert result is not None

    def test_licenses_permits_loads_and_lookups(self):
        mod = get_licenses_permits()
        assert hasattr(mod, "LICENSE_TYPES")
        assert hasattr(mod, "list_professions_for_country")
        professions = mod.list_professions_for_country("US")
        assert len(professions) > 0


# ── B: CLI command registration ──────────────────────────────────────────


class TestCLISubcommandRegistration:
    """Every governance subcommand is registered and reachable through the parser."""

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser("gludd")
        sub = parser.add_subparsers(dest="command")
        add_governance_subparser(sub)
        return parser

    def _governance_choices(self) -> set[str]:
        parser = self._build_parser()
        choices: dict[str, argparse.ArgumentParser] = {}
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, subparser in action.choices.items():
                    if name == "governance":
                        for subaction in subparser._actions:
                            if isinstance(subaction, argparse._SubParsersAction):
                                choices = subaction.choices
        return set(choices.keys())

    def test_all_expected_subcommands_registered(self):
        choices = self._governance_choices()
        expected = {
            "borders",
            "body",
            "tax",
            "currency",
            "service",
            "treaty",
            "navigate",
            "list",
            "jurisdictions",
            "classification",
            "authority",
            "info-class",
            "decision-makers",
            "postal",
            "military",
            "licenses",
            "elections",
            "relations",
            "legal",
            "finance",
        }
        missing = expected - choices
        assert not missing, f"Missing subcommands: {missing}"

    def test_each_subcommand_has_default_func(self):
        parser = self._build_parser()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, subparser in action.choices.items():
                    if name == "governance":
                        for subaction in subparser._actions:
                            if isinstance(subaction, argparse._SubParsersAction):
                                for cmd_name, cmd_parser in subaction.choices.items():
                                    assert cmd_parser.get_default("func") is not None, (
                                        f"'{cmd_name}' subcommand has no default func"
                                    )

    def test_parsing_borders_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["governance", "borders", "US-Canada land border"])
        assert args.governance_command == "borders"
        assert args.region == "US-Canada land border"

    def test_parsing_body_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["governance", "body", "UN"])
        assert args.governance_command == "body"
        assert args.name == "UN"

    def test_parsing_tax_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["governance", "tax", "US"])
        assert args.governance_command == "tax"
        assert args.country == "US"


# ── C: Cross-domain lookups ──────────────────────────────────────────────


class TestCrossDomainLookups:
    """Verify relationships across governance knowledge domains."""

    def test_jurisdiction_to_governing_body(self):
        jurisd = get_jurisdictions()
        bodies = get_governing_bodies()

        us_jur = jurisd.get_jurisdiction("US")
        assert us_jur is not None
        assert us_jur.get("region", "") != ""

        region_bodies = bodies.bodies_by_type("international")
        assert len(region_bodies) > 0

        bodies_in_us_jur = [b for b in region_bodies if us_jur.get("alpha_2") in b.get("jurisdictions", [])]
        assert isinstance(bodies_in_us_jur, list)

    def test_authority_references_jurisdiction(self):
        ar = get_authority_registry()
        jurisd = get_jurisdictions()

        dos = ar.get_authority("US-DOS")
        assert dos is not None
        jur_code = dos.get("jurisdiction", "")

        jur = jurisd.get_jurisdiction(jur_code)
        assert jur is not None
        assert jur["alpha_2"] == jur_code

    def test_border_controlling_bodies_exist(self):
        borders = get_borders()

        border = borders.lookup_border("US-Canada land border")
        assert border is not None

        controlling = border.get("controlling_bodies", [])
        assert len(controlling) > 0
        assert "US Customs and Border Protection" in controlling

    def test_passport_authority_to_jurisdiction(self):
        ar = get_authority_registry()
        jurisd = get_jurisdictions()

        pa = ar.get_passport_authority("US")
        assert pa is not None
        assert pa["name"] == "U.S. Department of State"

        authority_code = pa.get("authority", "")
        authority = ar.get_authority(authority_code)
        assert authority is not None

        jur_code = authority.get("jurisdiction", "")
        jur = jurisd.get_jurisdiction(jur_code)
        assert jur is not None
        assert jur["alpha_2"] == jur_code

    def test_navigate_routes_cross_domain(self):
        from general_ludd.governance.cli_governance import _navigate_query

        captured = StringIO()
        import sys

        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _navigate_query("border visa schengen")
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "borders" in output or "Schengen" in output

    def test_loader_cache_deduplication(self):
        a1 = get_authority_registry()
        a2 = get_authority_registry()
        assert a1 is a2

        j1 = get_jurisdictions()
        j2 = get_jurisdictions()
        assert j1 is j2

    def test_all_loader_functions_return_modules(self):
        getters = [
            get_borders,
            get_governing_bodies,
            get_conflicts_treaties,
            get_tax_currency,
            get_civic_services,
            get_elections_voting,
            get_international_relations,
            get_legal_systems,
            get_public_finance,
            get_info_classification,
            get_decision_makers,
            get_postal_delivery,
            get_military_service,
            get_licenses_permits,
            get_jurisdictions,
            get_classification_markings,
            get_authority_registry,
        ]
        for getter in getters:
            mod = getter()
            assert mod is not None, f"{getter.__name__} returned None"
