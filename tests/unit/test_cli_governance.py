"""Unit tests for ``gludd governance`` CLI subcommand.

Tests cover all 15 subcommands (borders, body, tax, currency, service, treaty,
navigate, elections, relations, legal, finance, jurisdictions, classification,
authority, info-class, decision-makers, postal, military, licenses) plus the
``list`` command, subparser registration, JSON output mode, and error handling.
"""

from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from typing import Any

import pytest

from general_ludd.governance.cli_governance import (
    _cmd_authority,
    _cmd_body,
    _cmd_borders,
    _cmd_classification,
    _cmd_currency,
    _cmd_decision_makers,
    _cmd_elections,
    _cmd_finance,
    _cmd_info_classification,
    _cmd_jurisdictions,
    _cmd_legal,
    _cmd_licenses,
    _cmd_list,
    _cmd_military,
    _cmd_navigate,
    _cmd_postal,
    _cmd_relations,
    _cmd_service,
    _cmd_tax,
    _cmd_treaty,
    add_governance_subparser,
)
from general_ludd.governance.loader import clear_cache


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    """Clear the module cache before each test so modules are re-loaded fresh."""
    clear_cache()
    yield
    clear_cache()


def _run_with_stdout(cmd: Any, args: argparse.Namespace) -> str:
    """Run a CLI command, capture stdout, and return the output."""
    captured = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        cmd(args)
    finally:
        sys.stdout = old_stdout
    return captured.getvalue()


def _run_with_stderr_exit(cmd: Any, args: argparse.Namespace) -> int:
    """Run a CLI command expected to exit non-zero; capture stderr and return exit code."""
    captured_err = StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured_err
    try:
        cmd(args)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    finally:
        sys.stderr = old_stderr
    return 0


# ── Subparser registration ─────────────────────────────────────────────────────


class TestGovernanceSubparser:
    def test_governance_command_registered(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_governance_subparser(sub)
        ns = top.parse_args(["governance"])
        assert ns.command == "governance"

    def test_all_subcommands_registered(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_governance_subparser(sub)
        for name in (
            "borders",
            "body",
            "tax",
            "currency",
            "service",
            "treaty",
            "navigate",
            "list",
            "elections",
            "relations",
            "legal",
            "finance",
            "jurisdictions",
            "classification",
            "authority",
            "info-class",
            "decision-makers",
            "postal",
            "military",
            "licenses",
        ):
            if name == "service":
                ns = top.parse_args(["governance", name, "healthcare", "US"])
            elif name not in ("list",):
                ns = top.parse_args(["governance", name, "dummy"])
            else:
                ns = top.parse_args(["governance", name])
            assert ns.governance_command == name

    def test_service_requires_two_args(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_governance_subparser(sub)
        ns = top.parse_args(["governance", "service", "healthcare", "US"])
        assert ns.service_name == "healthcare"
        assert ns.country == "US"


# ── borders ────────────────────────────────────────────────────────────────────


class TestGovernanceBorders:
    def test_borders_us_canada(self):
        args = argparse.Namespace(region="US-Canada land border", json=False)
        output = _run_with_stdout(_cmd_borders, args)
        assert "land" in output.lower()
        assert "universal" in output.lower()

    def test_borders_case_insensitive(self):
        args = argparse.Namespace(region="us-canada LAND border", json=False)
        output = _run_with_stdout(_cmd_borders, args)
        assert "passport" in output.lower() or "type" in output.lower()

    def test_borders_not_found_exits(self):
        args = argparse.Namespace(region="Nonexistent Border", json=False)
        rc = _run_with_stderr_exit(_cmd_borders, args)
        assert rc == 1

    def test_borders_json_output(self):
        args = argparse.Namespace(region="Schengen internal border", json=True)
        output = _run_with_stdout(_cmd_borders, args)
        data = json.loads(output)
        assert data["type"] == "customs"


# ── body ───────────────────────────────────────────────────────────────────────


class TestGovernanceBody:
    def test_body_un_by_id(self):
        args = argparse.Namespace(name="un", json=False)
        output = _run_with_stdout(_cmd_body, args)
        assert "United Nations" in output

    def test_body_by_alias(self):
        args = argparse.Namespace(name="UNSC", json=False)
        output = _run_with_stdout(_cmd_body, args)
        assert "Security Council" in output

    def test_body_not_found_exits(self):
        args = argparse.Namespace(name="Nonexistent Body XYZ", json=False)
        rc = _run_with_stderr_exit(_cmd_body, args)
        assert rc == 1

    def test_body_json_output(self):
        args = argparse.Namespace(name="eu", json=True)
        output = _run_with_stdout(_cmd_body, args)
        data = json.loads(output)
        assert data["name"] == "European Union"


# ── tax ────────────────────────────────────────────────────────────────────────


class TestGovernanceTax:
    def test_tax_us(self):
        args = argparse.Namespace(country="US", json=False)
        output = _run_with_stdout(_cmd_tax, args)
        assert "USD" in output
        assert "IRS" in output

    def test_tax_lowercase_country(self):
        args = argparse.Namespace(country="gb", json=False)
        output = _run_with_stdout(_cmd_tax, args)
        assert "GBP" in output

    def test_tax_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", json=False)
        rc = _run_with_stderr_exit(_cmd_tax, args)
        assert rc == 1

    def test_tax_json_output(self):
        args = argparse.Namespace(country="DE", json=True)
        output = _run_with_stdout(_cmd_tax, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["currency_code"] == "EUR"


# ── currency ───────────────────────────────────────────────────────────────────


class TestGovernanceCurrency:
    def test_currency_usd(self):
        args = argparse.Namespace(code="USD", json=False)
        output = _run_with_stdout(_cmd_currency, args)
        assert "USD" in output
        assert "United States" in output

    def test_currency_eur_multi_country(self):
        args = argparse.Namespace(code="EUR", json=True)
        output = _run_with_stdout(_cmd_currency, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["count"] >= 2

    def test_currency_lowercase_code(self):
        args = argparse.Namespace(code="gbp", json=False)
        output = _run_with_stdout(_cmd_currency, args)
        assert "GBP" in output

    def test_currency_not_found_exits(self):
        args = argparse.Namespace(code="XYZ", json=False)
        rc = _run_with_stderr_exit(_cmd_currency, args)
        assert rc == 1


# ── service ────────────────────────────────────────────────────────────────────


class TestGovernanceService:
    def test_service_healthcare_us(self):
        args = argparse.Namespace(service_name="healthcare", country="US", json=False)
        output = _run_with_stdout(_cmd_service, args)
        assert "healthcare.gov" in output.lower() or "health" in output.lower()

    def test_service_passport_gb(self):
        args = argparse.Namespace(service_name="passport", country="GB", json=False)
        output = _run_with_stdout(_cmd_service, args)
        assert "passport" in output.lower()

    def test_service_not_found_exits(self):
        args = argparse.Namespace(service_name="nonexistent_service", country="US", json=False)
        rc = _run_with_stderr_exit(_cmd_service, args)
        assert rc == 1

    def test_service_country_not_found_exits(self):
        args = argparse.Namespace(service_name="healthcare", country="ZZ", json=False)
        rc = _run_with_stderr_exit(_cmd_service, args)
        assert rc == 1

    def test_service_json_output(self):
        args = argparse.Namespace(service_name="tax", country="US", json=True)
        output = _run_with_stdout(_cmd_service, args)
        data = json.loads(output)
        assert data["found"] is True
        assert "IRS" in str(data.get("name", ""))


# ── treaty ─────────────────────────────────────────────────────────────────────


class TestGovernanceTreaty:
    def test_treaty_nato(self):
        args = argparse.Namespace(name="nato", json=False)
        output = _run_with_stdout(_cmd_treaty, args)
        assert "North Atlantic" in output

    def test_treaty_paris_agreement(self):
        args = argparse.Namespace(name="paris_agreement", json=False)
        output = _run_with_stdout(_cmd_treaty, args)
        assert "Paris" in output

    def test_treaty_not_found_exits(self):
        args = argparse.Namespace(name="nonexistent_treaty", json=False)
        rc = _run_with_stderr_exit(_cmd_treaty, args)
        assert rc == 1

    def test_treaty_json_output(self):
        args = argparse.Namespace(name="npt", json=True)
        output = _run_with_stdout(_cmd_treaty, args)
        data = json.loads(output)
        assert "Nuclear" in data["name"]


# ── navigate ───────────────────────────────────────────────────────────────────


class TestGovernanceNavigate:
    def test_navigate_border_query(self):
        args = argparse.Namespace(query="Schengen border crossing", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "borders" in output.lower()

    def test_navigate_body_query(self):
        args = argparse.Namespace(query="United Nations organization", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "bodies" in output.lower()

    def test_navigate_treaty_query(self):
        args = argparse.Namespace(query="Paris Agreement climate treaty", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "treaties" in output.lower()

    def test_navigate_civic_query(self):
        args = argparse.Namespace(query="healthcare service", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "civic_services" in output.lower() or "Health" in output

    def test_navigate_no_match_exits(self):
        args = argparse.Namespace(query="zzz_nonexistent_topic_12345", json=False)
        rc = _run_with_stderr_exit(_cmd_navigate, args)
        assert rc == 1

    def test_navigate_json_output(self):
        args = argparse.Namespace(query="NATO treaty alliance", json=True)
        output = _run_with_stdout(_cmd_navigate, args)
        data = json.loads(output)
        assert data["count"] >= 1
        assert "results" in data

    def test_navigate_elections_query(self):
        args = argparse.Namespace(query="election fptp runoff ballot", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "elections_voting" in output.lower()

    def test_navigate_diplomatic_query(self):
        args = argparse.Namespace(query="diplomatic relations embassy alliances", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "international_relations" in output.lower()

    def test_navigate_legal_query(self):
        args = argparse.Namespace(query="court judge appeal legal constitution", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "legal_systems" in output.lower()

    def test_navigate_finance_query(self):
        args = argparse.Namespace(query="budget debt fiscal expenditure", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "public_finance" in output.lower()

    def test_navigate_jurisdiction_query(self):
        args = argparse.Namespace(query="jurisdiction iso code subdivision territory", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "jurisdictions" in output.lower()

    def test_navigate_classification_query(self):
        args = argparse.Namespace(query="classification secret clearance caveat noforn", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "classification_markings" in output.lower()

    def test_navigate_authority_query(self):
        args = argparse.Namespace(query="authority issuer department of state", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "authority_registry" in output.lower()

    def test_navigate_info_class_query(self):
        args = argparse.Namespace(query="foia freedom of information gazette", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "info_classification" in output.lower()

    def test_navigate_decision_makers_query(self):
        args = argparse.Namespace(query="politician senator congress minister", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "decision_makers" in output.lower()

    def test_navigate_postal_query(self):
        args = argparse.Namespace(query="postal courier tracking dhl fedex shipping", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "postal_delivery" in output.lower()

    def test_navigate_military_query(self):
        args = argparse.Namespace(query="military army navy conscription veteran", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "military_service" in output.lower()

    def test_navigate_licenses_query(self):
        args = argparse.Namespace(query="driver professional license permit certification", json=False)
        output = _run_with_stdout(_cmd_navigate, args)
        assert "licenses_permits" in output.lower()


# ── list ───────────────────────────────────────────────────────────────────────


class TestGovernanceList:
    def test_list_shows_all_domains(self):
        args = argparse.Namespace(json=False)
        output = _run_with_stdout(_cmd_list, args)
        for domain in (
            "borders",
            "governing_bodies",
            "treaties",
            "tax_currency",
            "civic_services",
            "elections_voting",
            "international_relations",
            "legal_systems",
            "public_finance",
        ):
            assert domain in output.lower()

    def test_list_json_output(self):
        args = argparse.Namespace(json=True)
        output = _run_with_stdout(_cmd_list, args)
        data = json.loads(output)
        assert "borders" in data
        assert data["borders"]["count"] >= 5
        assert data["civic_services"]["count"] >= 3
        assert "elections_voting" in data
        assert "international_relations" in data
        assert "legal_systems" in data
        assert "public_finance" in data


# ── elections ──────────────────────────────────────────────────────────────────


class TestGovernanceElections:
    def test_elections_us(self):
        args = argparse.Namespace(country="US", method=None, json=False)
        output = _run_with_stdout(_cmd_elections, args)
        assert "presidential" in output.lower()

    def test_elections_lowercase(self):
        args = argparse.Namespace(country="gb", method=None, json=False)
        output = _run_with_stdout(_cmd_elections, args)
        assert "house of commons" in output.lower()

    def test_elections_method_lookup(self):
        args = argparse.Namespace(country="US", method="paper_ballot", json=False)
        output = _run_with_stdout(_cmd_elections, args)
        assert "paper" in output.lower()

    def test_elections_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", method=None, json=False)
        rc = _run_with_stderr_exit(_cmd_elections, args)
        assert rc == 1

    def test_elections_json_output(self):
        args = argparse.Namespace(country="US", method=None, json=True)
        output = _run_with_stdout(_cmd_elections, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["country"] == "US"


# ── relations ──────────────────────────────────────────────────────────────────


class TestGovernanceRelations:
    def test_relations_us(self):
        args = argparse.Namespace(country="US", alliance=None, sanctions=None, json=False)
        output = _run_with_stdout(_cmd_relations, args)
        assert "US" in output

    def test_relations_alliance_search(self):
        args = argparse.Namespace(country="US", alliance="nato", sanctions=None, json=False)
        output = _run_with_stdout(_cmd_relations, args)
        assert "North Atlantic" in output

    def test_relations_sanctions_lookup(self):
        args = argparse.Namespace(country="US", alliance=None, sanctions="RU", json=False)
        output = _run_with_stdout(_cmd_relations, args)
        assert "Russia" in output

    def test_relations_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", alliance=None, sanctions=None, json=False)
        rc = _run_with_stderr_exit(_cmd_relations, args)
        assert rc == 1

    def test_relations_json_output(self):
        args = argparse.Namespace(country="US", alliance=None, sanctions=None, json=True)
        output = _run_with_stdout(_cmd_relations, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["country"] == "US"


# ── legal ──────────────────────────────────────────────────────────────────────


class TestGovernanceLegal:
    def test_legal_us(self):
        args = argparse.Namespace(country="US", charter=None, courts=None, json=False)
        output = _run_with_stdout(_cmd_legal, args)
        assert "common_law" in output.lower() or "common law" in output.lower()

    def test_legal_charter_lookup(self):
        args = argparse.Namespace(country="US", charter="udhr", courts=None, json=False)
        output = _run_with_stdout(_cmd_legal, args)
        assert "Universal Declaration" in output

    def test_legal_courts_lookup(self):
        args = argparse.Namespace(country="US", charter=None, courts="US", json=False)
        output = _run_with_stdout(_cmd_legal, args)
        assert "Supreme Court" in output

    def test_legal_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", charter=None, courts=None, json=False)
        rc = _run_with_stderr_exit(_cmd_legal, args)
        assert rc == 1

    def test_legal_json_output(self):
        args = argparse.Namespace(country="DE", charter=None, courts=None, json=True)
        output = _run_with_stdout(_cmd_legal, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["country"] == "DE"


# ── finance ────────────────────────────────────────────────────────────────────


class TestGovernanceFinance:
    def test_finance_us(self):
        args = argparse.Namespace(country="US", debt=None, pensions=None, json=False)
        output = _run_with_stdout(_cmd_finance, args)
        assert "USD" in output

    def test_finance_debt_lookup(self):
        args = argparse.Namespace(country="US", debt="JP", pensions=None, json=False)
        output = _run_with_stdout(_cmd_finance, args)
        assert "Japan" in output

    def test_finance_pensions_lookup(self):
        args = argparse.Namespace(country="US", debt=None, pensions="US", json=False)
        output = _run_with_stdout(_cmd_finance, args)
        assert "Social Security" in output

    def test_finance_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", debt=None, pensions=None, json=False)
        rc = _run_with_stderr_exit(_cmd_finance, args)
        assert rc == 1

    def test_finance_json_output(self):
        args = argparse.Namespace(country="GB", debt=None, pensions=None, json=True)
        output = _run_with_stdout(_cmd_finance, args)
        data = json.loads(output)
        assert data["found"] is True
        assert data["country"] == "GB"


# ── jurisdictions ────────────────────────────────────────────────────────────────


class TestGovernanceJurisdictions:
    def test_jurisdictions_us(self):
        args = argparse.Namespace(code="US", subdivisions=False, json=False)
        output = _run_with_stdout(_cmd_jurisdictions, args)
        assert "US" in output

    def test_jurisdictions_subdivisions(self):
        args = argparse.Namespace(code="US", subdivisions=True, json=False)
        output = _run_with_stdout(_cmd_jurisdictions, args)
        assert "US" in output

    def test_jurisdictions_not_found_exits(self):
        args = argparse.Namespace(code="ZZ", subdivisions=False, json=False)
        rc = _run_with_stderr_exit(_cmd_jurisdictions, args)
        assert rc == 1

    def test_jurisdictions_json_output(self):
        args = argparse.Namespace(code="GB", subdivisions=False, json=True)
        output = _run_with_stdout(_cmd_jurisdictions, args)
        data = json.loads(output)
        assert data["found"] is True


# ── classification ───────────────────────────────────────────────────────────────


class TestGovernanceClassification:
    def test_classification_list_systems(self):
        args = argparse.Namespace(system="", banner=None, caveat=None, json=False)
        output = _run_with_stdout(_cmd_classification, args)
        assert "systems" in output.lower()

    def test_classification_us_system(self):
        args = argparse.Namespace(system="US", banner=None, caveat=None, json=False)
        output = _run_with_stdout(_cmd_classification, args)
        assert "systems" in output.lower()

    def test_classification_banner_lookup(self):
        args = argparse.Namespace(system="US", banner="secret", caveat=None, json=False)
        output = _run_with_stdout(_cmd_classification, args)
        assert "secret" in output.lower()

    def test_classification_caveat_lookup(self):
        args = argparse.Namespace(system="", banner=None, caveat="NOFORN", json=False)
        output = _run_with_stdout(_cmd_classification, args)
        assert "NOFORN" in output

    def test_classification_banner_not_found_exits(self):
        args = argparse.Namespace(system="ZZ", banner="nonexistent_level", caveat=None, json=False)
        rc = _run_with_stderr_exit(_cmd_classification, args)
        assert rc == 1

    def test_classification_json_output(self):
        args = argparse.Namespace(system="UK", banner=None, caveat=None, json=True)
        output = _run_with_stdout(_cmd_classification, args)
        data = json.loads(output)
        assert data["found"] is True


# ── authority ────────────────────────────────────────────────────────────────────


class TestGovernanceAuthority:
    def test_authority_list(self):
        args = argparse.Namespace(query="", code=None, instrument=None, json=False)
        output = _run_with_stdout(_cmd_authority, args)
        assert "Us-Dos" in output

    def test_authority_code_lookup(self):
        args = argparse.Namespace(query="", code="US-DOS", instrument=None, json=False)
        output = _run_with_stdout(_cmd_authority, args)
        assert "Department of State" in output

    def test_authority_instrument_lookup(self):
        args = argparse.Namespace(query="", code=None, instrument="passport", json=False)
        output = _run_with_stdout(_cmd_authority, args)
        assert "authorities" in output.lower()

    def test_authority_not_found_exits(self):
        args = argparse.Namespace(query="", code="ZZ-NONEXISTENT", instrument=None, json=False)
        rc = _run_with_stderr_exit(_cmd_authority, args)
        assert rc == 1

    def test_authority_json_output(self):
        args = argparse.Namespace(query="", code="UK-HMPO", instrument=None, json=True)
        output = _run_with_stdout(_cmd_authority, args)
        data = json.loads(output)
        assert data["found"] is True


# ── info-class ───────────────────────────────────────────────────────────────────


class TestGovernanceInfoClassification:
    def test_info_class_us(self):
        args = argparse.Namespace(country="US", foia=False, source=None, equiv=None, json=False)
        output = _run_with_stdout(_cmd_info_classification, args)
        assert "US" in output

    def test_info_class_foia(self):
        args = argparse.Namespace(country="US", foia=True, source=None, equiv=None, json=False)
        output = _run_with_stdout(_cmd_info_classification, args)
        assert "FOIA" in output or "Freedom" in output

    def test_info_class_source(self):
        args = argparse.Namespace(country="US", foia=False, source="court", equiv=None, json=False)
        output = _run_with_stdout(_cmd_info_classification, args)
        assert "court" in output.lower()

    def test_info_class_equiv(self):
        args = argparse.Namespace(country="US", foia=False, source=None, equiv="SECRET,US,GB", json=False)
        output = _run_with_stdout(_cmd_info_classification, args)
        assert "equivalent" in output.lower()

    def test_info_class_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", foia=False, source=None, equiv=None, json=False)
        rc = _run_with_stderr_exit(_cmd_info_classification, args)
        assert rc == 1

    def test_info_class_json_output(self):
        args = argparse.Namespace(country="GB", foia=False, source=None, equiv=None, json=True)
        output = _run_with_stdout(_cmd_info_classification, args)
        data = json.loads(output)
        assert data["found"] is True


# ── decision-makers ──────────────────────────────────────────────────────────────


class TestGovernanceDecisionMakers:
    def test_decision_makers_us(self):
        args = argparse.Namespace(country="US", person=None, topic=None, json=False)
        output = _run_with_stdout(_cmd_decision_makers, args)
        assert "US" in output

    def test_decision_makers_person_lookup(self):
        args = argparse.Namespace(country="US", person="us-sen-01", topic=None, json=False)
        output = _run_with_stdout(_cmd_decision_makers, args)
        assert "proclivity" in output.lower()

    def test_decision_makers_topic(self):
        args = argparse.Namespace(country="US", person=None, topic="healthcare", json=False)
        output = _run_with_stdout(_cmd_decision_makers, args)
        assert "profiles" in output.lower()

    def test_decision_makers_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", person=None, topic=None, json=False)
        rc = _run_with_stderr_exit(_cmd_decision_makers, args)
        assert rc == 1

    def test_decision_makers_json_output(self):
        args = argparse.Namespace(country="GB", person=None, topic=None, json=True)
        output = _run_with_stdout(_cmd_decision_makers, args)
        data = json.loads(output)
        assert data["found"] is True


# ── postal ───────────────────────────────────────────────────────────────────────


class TestGovernancePostal:
    def test_postal_us(self):
        args = argparse.Namespace(country="US", courier=None, tracking=None, customs=False, json=False)
        output = _run_with_stdout(_cmd_postal, args)
        assert "US" in output

    def test_postal_courier(self):
        args = argparse.Namespace(country="US", courier="usps", tracking="123456789", customs=False, json=False)
        output = _run_with_stdout(_cmd_postal, args)
        assert "tracking" in output.lower() and "usps" in output.lower()

    def test_postal_customs(self):
        args = argparse.Namespace(country="US", courier=None, tracking=None, customs=True, json=False)
        output = _run_with_stdout(_cmd_postal, args)
        assert "US" in output

    def test_postal_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", courier=None, tracking=None, customs=False, json=False)
        rc = _run_with_stderr_exit(_cmd_postal, args)
        assert rc == 1

    def test_postal_json_output(self):
        args = argparse.Namespace(country="GB", courier=None, tracking=None, customs=False, json=True)
        output = _run_with_stdout(_cmd_postal, args)
        data = json.loads(output)
        assert data["found"] is True


# ── military ─────────────────────────────────────────────────────────────────────


class TestGovernanceMilitary:
    def test_military_us(self):
        args = argparse.Namespace(country="US", branches=False, benefits=None, conscription=False, json=False)
        output = _run_with_stdout(_cmd_military, args)
        assert "US" in output

    def test_military_branches(self):
        args = argparse.Namespace(country="US", branches=True, benefits=None, conscription=False, json=False)
        output = _run_with_stdout(_cmd_military, args)
        assert "Army" in output or "Navy" in output or "Air Force" in output

    def test_military_conscription(self):
        args = argparse.Namespace(country="US", branches=False, benefits=None, conscription=True, json=False)
        output = _run_with_stdout(_cmd_military, args)
        assert "mandatory conscription" in output.lower()

    def test_military_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", branches=False, benefits=None, conscription=False, json=False)
        rc = _run_with_stderr_exit(_cmd_military, args)
        assert rc == 1

    def test_military_json_output(self):
        args = argparse.Namespace(country="GB", branches=False, benefits=None, conscription=False, json=True)
        output = _run_with_stdout(_cmd_military, args)
        data = json.loads(output)
        assert data["found"] is True


# ── licenses ─────────────────────────────────────────────────────────────────────


class TestGovernanceLicenses:
    def test_licenses_us(self):
        args = argparse.Namespace(country="US", license_type=None, export_control=None, json=False)
        output = _run_with_stdout(_cmd_licenses, args)
        assert "US" in output

    def test_licenses_driving(self):
        args = argparse.Namespace(country="US", license_type="driving", export_control=None, json=False)
        output = _run_with_stdout(_cmd_licenses, args)
        assert "driving" in output.lower()

    def test_licenses_export_control(self):
        args = argparse.Namespace(country="US", license_type=None, export_control="US,military_items", json=False)
        output = _run_with_stdout(_cmd_licenses, args)
        assert "US" in output

    def test_licenses_not_found_exits(self):
        args = argparse.Namespace(country="ZZ", license_type=None, export_control=None, json=False)
        rc = _run_with_stderr_exit(_cmd_licenses, args)
        assert rc == 1

    def test_licenses_json_output(self):
        args = argparse.Namespace(country="GB", license_type=None, export_control=None, json=True)
        output = _run_with_stdout(_cmd_licenses, args)
        data = json.loads(output)
        assert data["found"] is True


# ── Loader ──────────────────────────────────────────────────────────────────────


class TestGovernanceLoader:
    def test_loader_imports_borders(self):
        from general_ludd.governance.loader import get_borders

        borders = get_borders()
        assert hasattr(borders, "lookup_border")
        assert hasattr(borders, "BORDER_DATA")

    def test_loader_imports_civic_services(self):
        from general_ludd.governance.loader import get_civic_services

        civic = get_civic_services()
        assert hasattr(civic, "lookup_service")
        assert hasattr(civic, "CIVIC_SERVICES")

    def test_loader_cache_returns_same_module(self):
        from general_ludd.governance.loader import get_tax_currency

        first = get_tax_currency()
        second = get_tax_currency()
        assert first is second

    def test_loader_imports_elections_voting(self):
        from general_ludd.governance.loader import get_elections_voting

        ev = get_elections_voting()
        assert hasattr(ev, "lookup_elections")
        assert hasattr(ev, "COUNTRY_ELECTIONS")

    def test_loader_imports_international_relations(self):
        from general_ludd.governance.loader import get_international_relations

        ir_mod = get_international_relations()
        assert hasattr(ir_mod, "lookup_diplomatic_relations")
        assert hasattr(ir_mod, "ALLIANCES")

    def test_loader_imports_legal_systems(self):
        from general_ludd.governance.loader import get_legal_systems

        ls_mod = get_legal_systems()
        assert hasattr(ls_mod, "lookup_legal_system")
        assert hasattr(ls_mod, "COUNTRY_LEGAL_SYSTEMS")

    def test_loader_imports_public_finance(self):
        from general_ludd.governance.loader import get_public_finance

        pf_mod = get_public_finance()
        assert hasattr(pf_mod, "lookup_budget")
        assert hasattr(pf_mod, "COUNTRY_BUDGETS")

    def test_loader_imports_jurisdictions(self):
        from general_ludd.governance.loader import get_jurisdictions

        jurisd = get_jurisdictions()
        assert hasattr(jurisd, "get_jurisdiction")
        assert hasattr(jurisd, "JURISDICTION_CODES")

    def test_loader_imports_classification_markings(self):
        from general_ludd.governance.loader import get_classification_markings

        cm = get_classification_markings()
        assert hasattr(cm, "list_systems")
        assert hasattr(cm, "BANNER_FORMATS")

    def test_loader_imports_authority_registry(self):
        from general_ludd.governance.loader import get_authority_registry

        ar = get_authority_registry()
        assert hasattr(ar, "get_authority")
        assert hasattr(ar, "AUTHORITY_INSTRUMENTS")

    def test_loader_imports_info_classification(self):
        from general_ludd.governance.loader import get_info_classification

        ic = get_info_classification()
        assert hasattr(ic, "get_classification_system")

    def test_loader_imports_decision_makers(self):
        from general_ludd.governance.loader import get_decision_makers

        dm = get_decision_makers()
        assert hasattr(dm, "lookup_decision_makers")

    def test_loader_imports_postal_delivery(self):
        from general_ludd.governance.loader import get_postal_delivery

        pd = get_postal_delivery()
        assert hasattr(pd, "get_postal_code_pattern")

    def test_loader_imports_military_service(self):
        from general_ludd.governance.loader import get_military_service

        ms = get_military_service()
        assert hasattr(ms, "get_conscription_info")

    def test_loader_imports_licenses_permits(self):
        from general_ludd.governance.loader import get_licenses_permits

        lp = get_licenses_permits()
        assert hasattr(lp, "get_license_info")

    def test_loader_imports_conflicts_treaties(self):
        from general_ludd.governance.loader import get_conflicts_treaties

        ct = get_conflicts_treaties()
        assert hasattr(ct, "lookup_treaties")
        assert hasattr(ct, "TREATY_DATABASE")


# ── Package-level exports ────────────────────────────────────────────────────────


class TestGovernancePackageExports:
    def test_jurisdictions_importable_from_package(self):
        from general_ludd.governance import get_jurisdictions

        jurisd = get_jurisdictions()
        assert hasattr(jurisd, "get_jurisdiction")

    def test_classification_markings_importable_from_package(self):
        from general_ludd.governance import get_classification_markings

        cm = get_classification_markings()
        assert hasattr(cm, "list_systems")

    def test_authority_registry_importable_from_package(self):
        from general_ludd.governance import get_authority_registry

        ar = get_authority_registry()
        assert hasattr(ar, "get_authority")
