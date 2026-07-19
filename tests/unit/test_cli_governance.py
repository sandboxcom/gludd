"""Unit tests for ``gludd governance`` CLI subcommand.

Tests cover all 7 subcommands (borders, body, tax, currency, service, treaty,
navigate) plus the ``list`` command, subparser registration, JSON output mode,
and error handling.
"""

from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from typing import Any

import pytest

from general_ludd.governance.cli_governance import (
    _cmd_body,
    _cmd_borders,
    _cmd_currency,
    _cmd_elections,
    _cmd_finance,
    _cmd_legal,
    _cmd_list,
    _cmd_navigate,
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
        for name in ("borders", "body", "tax", "currency", "service", "treaty",
                      "navigate", "list", "elections", "relations", "legal", "finance"):
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


# ── list ───────────────────────────────────────────────────────────────────────


class TestGovernanceList:
    def test_list_shows_all_domains(self):
        args = argparse.Namespace(json=False)
        output = _run_with_stdout(_cmd_list, args)
        for domain in ("borders", "governing_bodies", "treaties", "tax_currency",
                       "civic_services", "elections_voting", "international_relations",
                       "legal_systems", "public_finance"):
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
