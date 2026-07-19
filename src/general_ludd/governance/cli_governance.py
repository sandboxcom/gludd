"""CLI subcommand: ``gludd governance`` — exposes the governance collection's
knowledge base to users via the command line.

Subcommands::

    gludd governance borders <region>          Border crossing info
    gludd governance body <name>               Governing body lookup
    gludd governance tax <country>             Tax system info
    gludd governance currency <code>           Currency info
    gludd governance service <name> <country>  Civic service lookup
    gludd governance treaty <name>             Treaty lookup
    gludd governance navigate <query>          Natural language routing
    gludd governance list                      List available data
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from general_ludd.governance.loader import (
    get_borders,
    get_civic_services,
    get_conflicts_treaties,
    get_elections_voting,
    get_governing_bodies,
    get_international_relations,
    get_legal_systems,
    get_public_finance,
    get_tax_currency,
)

# ── Output helpers ────────────────────────────────────────────────────────────


def _print_result(data: dict[str, Any], *, json_output: bool = False, stream: TextIO | None = None) -> None:
    """Print a result dict as human-readable text or JSON."""
    out = stream if stream is not None else sys.stdout
    if json_output:
        print(json.dumps(data, indent=2, default=str, sort_keys=True), file=out)
        return
    _print_human(data, out)


def _print_human(data: dict[str, Any], out: TextIO, indent: int = 0) -> None:
    """Recursively print a dict as human-readable key: value lines."""
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            print(f"{pad}{key.replace('_', ' ').title()}:", file=out)
            _print_human(value, out, indent + 1)
        elif isinstance(value, list):
            print(f"{pad}{key.replace('_', ' ').title()}:", file=out)
            for item in value:
                if isinstance(item, dict):
                    print(f"{pad}  -", file=out)
                    _print_human(item, out, indent + 2)
                else:
                    print(f"{pad}  - {item}", file=out)
        else:
            label = key.replace("_", " ").title()
            print(f"{pad}{label}: {value}", file=out)


# ── Subcommand implementations ────────────────────────────────────────────────


def _cmd_borders(args: argparse.Namespace) -> None:
    """``gludd governance borders <region>`` — border crossing info."""
    borders = get_borders()
    result = borders.lookup_border(args.region)
    if result is None:
        print(f"No border data found for '{args.region}'.", file=sys.stderr)
        sys.exit(1)
    _print_result(result, json_output=args.json)


def _cmd_body(args: argparse.Namespace) -> None:
    """``gludd governance body <name>`` — governing body lookup.

    Accepts a body id, name, or alias (e.g. ``un``, ``United Nations``,
    ``UNSC``). Direct match returns the body. Otherwise searches all bodies
    for name/alias fragments.
    """
    bodies_mod = get_governing_bodies()
    needle = args.name.strip()

    # Direct lookup by id, name, or alias
    body = bodies_mod.lookup_body(needle)
    if body is not None:
        _print_result(body, json_output=args.json)
        return

    # Name/alias fragment search across all bodies
    needle_lower = needle.lower()
    matches: list[dict[str, Any]] = []
    for body_entry in bodies_mod.INTERNATIONAL_BODIES:
        if needle_lower in body_entry["name"].lower():
            matches.append(body_entry)
        else:
            for alias in body_entry.get("aliases", ()):
                if needle_lower in alias.lower():
                    matches.append(body_entry)
                    break

    if not matches:
        print(f"No governing body found for '{args.name}'.", file=sys.stderr)
        sys.exit(1)

    _print_result({"query": args.name, "found": True, "count": len(matches), "bodies": matches}, json_output=args.json)


def _cmd_tax(args: argparse.Namespace) -> None:
    """``gludd governance tax <country>`` — tax system info."""
    tax_mod = get_tax_currency()
    code = args.country.upper()
    country_data = tax_mod.TAX_DATA.get(code)
    if country_data is None:
        print(f"No tax data found for country '{code}'.", file=sys.stderr)
        sys.exit(1)

    result: dict[str, Any] = {"country": code, "found": True, "currency_code": country_data.get("currency", ""), **country_data}
    authority = tax_mod.TAX_AUTHORITIES.get(code)
    if authority:
        result["authority"] = authority
    _print_result(result, json_output=args.json)


def _cmd_currency(args: argparse.Namespace) -> None:
    """``gludd governance currency <code>`` — currency info by ISO 4217 code."""
    tax_mod = get_tax_currency()
    record = tax_mod.get_currency_info(args.code)
    if record is None:
        print(f"No currency data found for code '{args.code.upper()}'.", file=sys.stderr)
        sys.exit(1)
    count = tax_mod.get_currency_count(args.code)
    result: dict[str, Any] = {"code": args.code.upper(), "found": True, "count": count, **record}
    _print_result(result, json_output=args.json)


def _cmd_service(args: argparse.Namespace) -> None:
    """``gludd governance service <name> <country>`` — civic service lookup."""
    civic = get_civic_services()
    svc_name = args.service_name
    aliases = {"healthcare": "benefits_claims", "tax": "tax_filing"}
    original_svc = svc_name
    svc_name = aliases.get(svc_name.lower(), svc_name)
    service_info = civic.lookup_service(svc_name, args.country)
    if service_info is None:
        print(
            f"No civic service '{args.service_name}' found for country '{args.country.upper()}'.\n"
            f"Available services: {', '.join(sorted(civic.SERVICES.keys()))}",
            file=sys.stderr,
        )
        sys.exit(1)
    result = service_info.to_dict()
    result["found"] = True
    result["name"] = result.get("issuing_body", "")
    if original_svc.lower() != svc_name:
        result["original_query"] = original_svc
        result["notes"] = f"Mapped '{original_svc}' to nearest service '{svc_name}'. For US healthcare visit healthcare.gov"
    _print_result(result, json_output=args.json)


def _cmd_treaty(args: argparse.Namespace) -> None:
    """``gludd governance treaty <name>`` — treaty lookup.

    Accepts a country code (e.g. ``US``) or a treaty name fragment
    (e.g. ``NATO``, ``Paris Agreement``).
    """
    ct = get_conflicts_treaties()
    needle = args.name.strip()
    code = needle.upper()

    # Direct treaty-id lookup
    for t in ct.TREATY_DATABASE:
        if t["id"].lower() == needle.lower():
            result = dict(t)
            result["found"] = True
            result["country"] = needle.upper()
            _print_result(result, json_output=args.json)
            return

    # Country-code lookup
    if code in ct.TREATIES:
        result = ct.lookup_treaties(code)
        if result:
            result["found"] = True
            _print_result(result, json_output=args.json)
            return

    # Name-based search
    needle_lower = needle.lower()
    matches: list[dict[str, Any]] = []
    for treaty in ct.TREATY_DATABASE:
        haystack = str(treaty.get("name", "")).lower() + " " + str(treaty.get("subject", "")).lower()
        if needle_lower in haystack:
            matches.append(treaty)

    if not matches:
        print(f"No treaty found for '{args.name}'.", file=sys.stderr)
        sys.exit(1)

    _print_result(
        {"query": args.name, "found": True, "count": len(matches), "treaties": matches},
        json_output=args.json,
    )


def _cmd_navigate(args: argparse.Namespace) -> None:
    """``gludd governance navigate <query>`` — natural language routing.

    Attempts to route a free-text query to the appropriate governance
    knowledge module using keyword matching, then returns the best result.
    """
    query = args.query.lower().strip()

    # Route by keyword detection
    _navigate_query(query, json_output=args.json)


def _navigate_query(query: str, *, json_output: bool = False) -> None:
    """Dispatch a natural-language query to the right knowledge module."""
    results: list[dict[str, Any]] = []

    # ── Border / crossing keywords ──
    border_keywords = {"border", "crossing", "visa", "passport", "schengen", "checkpoint", "demilitarized"}
    if any(kw in query for kw in border_keywords):
        borders = get_borders()
        for name, entry in borders.BORDER_DATA.items():
            if any(word in name.lower() for word in query.split()):
                results.append({"domain": "borders", "match": name, "data": entry})
        # If no direct match, return all borders as available
        if not results:
            results.append({
                "domain": "borders",
                "hint": "Try: " + ", ".join(sorted(borders.BORDER_DATA)[:5]),
                "available_count": len(borders.BORDER_DATA),
            })

    # ── Governing body keywords ──
    body_keywords = {
        "government", "body", "council", "parliament", "assembly",
        "organization", "union", "un ", "eu ", "nato", "wto", "who",
    }
    if any(kw in query for kw in body_keywords) or any(
        org in query for org in ("united nations", "european union", "african union")
    ):
        bodies = get_governing_bodies()
        for body in bodies.INTERNATIONAL_BODIES:
            haystack = body["name"].lower() + " " + body["id"] + " " + " ".join(body.get("aliases", ()))
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "bodies", "match": body["name"], "data": body})
        if not results:
            results.append({
                "domain": "bodies",
                "hint": "Try: " + ", ".join(b["name"] for b in bodies.INTERNATIONAL_BODIES[:5]),
                "available_count": len(bodies.INTERNATIONAL_BODIES),
            })

    # ── Tax / currency keywords ──
    tax_keywords = {"tax", "vat", "gst", "irs", "revenue", "duty", "tariff"}
    currency_keywords = {
        "currency", "dollar", "euro", "pound", "yen", "rupee",
        "cad", "usd", "eur", "gbp", "jpy", "inr", "aud",
    }
    if any(kw in query for kw in tax_keywords) or any(kw in query for kw in currency_keywords):
        tax_cur = get_tax_currency()
        for code, record in tax_cur.TAX_CURRENCY.items():
            haystack = " ".join([
                code.lower(),
                str(record.get("name", "")).lower(),
                str(record.get("currency_code", "")).lower(),
                str(record.get("currency_name", "")).lower(),
                str(record.get("tax_authority", "")).lower(),
            ])
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "tax_currency", "match": code, "data": record})

    # ── Treaty / conflict keywords ──
    treaty_keywords = {"treaty", "convention", "agreement", "accord", "pact", "alliance"}
    if any(kw in query for kw in treaty_keywords):
        ct = get_conflicts_treaties()
        for treaty in ct.TREATY_DATABASE:
            haystack = treaty["name"].lower() + " " + treaty["id"] + " " + str(treaty.get("subject", "")).lower()
            if any(word in haystack for word in query.split() if len(word) > 3):
                results.append({"domain": "treaties", "match": treaty["name"], "data": treaty})
        if not results:
            results.append({
                "domain": "treaties",
                "hint": "Try: " + ", ".join(t["name"] for t in ct.TREATY_DATABASE[:5]),
                "available_count": len(ct.TREATY_DATABASE),
            })

    # ── Civic service keywords ──
    civic_keywords = {"service", "healthcare", "passport", "postal", "license", "vote", "voting", "registration"}
    if any(kw in query for kw in civic_keywords):
        civic = get_civic_services()
        for svc_id, svc_def in civic.SERVICES.items():
            for country_code, svc_info in svc_def.get("countries", {}).items():
                haystack = (
                    svc_id.lower() + " "
                    + str(svc_info.get("issuing_body", "")).lower() + " "
                    + str(svc_def.get("category", "")).lower()
                )
                if any(word in haystack for word in query.split() if len(word) > 3):
                    results.append({
                        "domain": "civic_services",
                        "match": f"{svc_def.get('issuing_body', svc_id)} ({country_code})",
                        "data": {"country": country_code, "service_id": svc_id, "issuing_body": svc_info.get("issuing_body", "")},
                    })

    # ── No results ──
    if not results:
        print(
            "No governance data matched your query. Try keywords like:\n"
            "  border, body, tax, currency, treaty, service,\n"
            "  or specific names like 'Schengen', 'UN', 'NATO', 'Paris Agreement'.",
            file=sys.stderr,
        )
        sys.exit(1)

    _print_result({"query": query, "results": results, "count": len(results)}, json_output=json_output)


def _cmd_elections(args: argparse.Namespace) -> None:
    """``gludd governance elections <country>`` — elections and voting info."""
    ev = get_elections_voting()
    if args.method:
        method = ev.get_voting_method(args.method)
        if method is None:
            print(f"No voting method data found for '{args.method}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"method": args.method, "found": True, **method}, json_output=args.json)
        return
    result = ev.lookup_elections(args.country)
    if result is None:
        print(f"No election data found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    # Add human-readable body names for common countries
    body_names = {"gb": "House of Commons", "us": "House of Representatives / Senate", "ca": "House of Commons", "au": "House of Representatives / Senate", "de": "Bundestag", "fr": "National Assembly", "jp": "House of Representatives", "in": "Lok Sabha"}
    result["legislative_body"] = body_names.get(args.country.lower(), "")
    _print_result(result, json_output=args.json)


def _cmd_relations(args: argparse.Namespace) -> None:
    """``gludd governance relations <country>`` — diplomatic relations and alliances."""
    ir_mod = get_international_relations()
    if args.alliance:
        matches = ir_mod.search_alliance(args.alliance)
        if not matches:
            print(f"No alliance found for '{args.alliance}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"query": args.alliance, "found": True, "count": len(matches), "alliances": matches},
                      json_output=args.json)
        return
    if args.sanctions:
        result = ir_mod.lookup_sanctions(args.sanctions)
        if result is None:
            print(f"No sanctions data found for '{args.sanctions}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(result, json_output=args.json)
        return
    result = ir_mod.lookup_diplomatic_relations(args.country)
    if result is None:
        print(f"No diplomatic relations data found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    _print_result(result, json_output=args.json)


def _cmd_legal(args: argparse.Namespace) -> None:
    """``gludd governance legal <country>`` — legal systems, courts, and rights."""
    ls_mod = get_legal_systems()
    if args.charter:
        result = ls_mod.lookup_rights_charter(args.charter)
        if result is None:
            print(f"No rights charter found for '{args.charter}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(result, json_output=args.json)
        return
    if args.courts:
        result = ls_mod.search_court_system(args.courts)
        if result is None:
            print(f"No court hierarchy data found for '{args.courts.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(result, json_output=args.json)
        return
    result = ls_mod.lookup_legal_system(args.country)
    if result is None:
        print(f"No legal system data found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    _print_result(result, json_output=args.json)


def _cmd_finance(args: argparse.Namespace) -> None:
    """``gludd governance finance <country>`` — budgets, debt, and pensions."""
    pf_mod = get_public_finance()
    if args.pensions:
        result = pf_mod.lookup_pension_system(args.pensions)
        if result is None:
            print(f"No pension data found for '{args.pensions.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": args.pensions.upper(), "found": True, **result}, json_output=args.json)
        return
    if args.debt:
        result = pf_mod.lookup_sovereign_debt(args.debt)
        if result is None:
            print(f"No sovereign debt data found for '{args.debt.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(result, json_output=args.json)
        return
    result = pf_mod.lookup_budget(args.country)
    if result is None:
        print(f"No budget data found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    _print_result(result, json_output=args.json)


def _cmd_list(args: argparse.Namespace) -> None:
    """``gludd governance list`` — list available data domains and counts."""
    borders = get_borders()
    bodies = get_governing_bodies()
    ct = get_conflicts_treaties()
    tax_cur = get_tax_currency()
    civic = get_civic_services()
    elections = get_elections_voting()
    relations = get_international_relations()
    legal = get_legal_systems()
    finance = get_public_finance()

    domains = {
        "borders": {
            "count": len(borders.BORDER_DATA),
            "examples": sorted(borders.BORDER_DATA)[:3],
        },
        "governing_bodies": {
            "count": len(bodies.INTERNATIONAL_BODIES),
            "examples": [b["name"] for b in bodies.INTERNATIONAL_BODIES[:3]],
        },
        "treaties": {
            "count": len(ct.TREATY_DATABASE),
            "examples": [t["name"] for t in ct.TREATY_DATABASE[:3]],
        },
        "active_conflicts": {
            "count": len(ct.ACTIVE_CONFLICTS),
            "examples": [c["name"] for c in ct.ACTIVE_CONFLICTS[:3]],
        },
        "tax_currency": {
            "count": len(tax_cur.TAX_CURRENCY),
            "examples": list(sorted(tax_cur.TAX_CURRENCY))[:3],
        },
        "civic_services": {
            "count": len(civic.CIVIC_SERVICES),
            "examples": list(sorted(civic.CIVIC_SERVICES))[:3],
        },
        "elections_voting": {
            "count": len(elections.COUNTRY_ELECTIONS),
            "examples": sorted(elections.COUNTRY_ELECTIONS)[:3],
        },
        "international_relations": {
            "count": len(relations.ALLIANCES),
            "examples": sorted(relations.ALLIANCES)[:3],
        },
        "legal_systems": {
            "count": len(legal.COUNTRY_LEGAL_SYSTEMS),
            "examples": sorted(legal.COUNTRY_LEGAL_SYSTEMS)[:3],
        },
        "public_finance": {
            "count": len(finance.COUNTRY_BUDGETS),
            "examples": sorted(finance.COUNTRY_BUDGETS)[:3],
        },
    }
    for key in list(domains.keys()):
        domains[key]["_key"] = key
    _print_result(domains, json_output=args.json)


# ── Subparser registration ────────────────────────────────────────────────────


def add_governance_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``gludd governance`` subcommand tree."""
    gov_parser = sub.add_parser(
        "governance",
        help="Governance knowledge: borders, bodies, tax, currency, treaties, civic services",
    )
    gov_parser.set_defaults(func=None)
    gov_sub = gov_parser.add_subparsers(dest="governance_command")

    # borders <region>
    borders_p = gov_sub.add_parser("borders", help="Border crossing info for a region")
    borders_p.add_argument("region", help="Border region name (e.g. 'US-Canada land border')")
    borders_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    borders_p.set_defaults(func=_cmd_borders)

    # body <name>
    body_p = gov_sub.add_parser("body", help="Governing body lookup by name or ID")
    body_p.add_argument("name", help="Body name, ID, or alias (e.g. 'UN', 'European Union')")
    body_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    body_p.set_defaults(func=_cmd_body)

    # tax <country>
    tax_p = gov_sub.add_parser("tax", help="Tax system info for a country")
    tax_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'GB')")
    tax_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    tax_p.set_defaults(func=_cmd_tax)

    # currency <code>
    currency_p = gov_sub.add_parser("currency", help="Currency info by ISO 4217 code")
    currency_p.add_argument("code", help="ISO 4217 currency code (e.g. 'USD', 'EUR', 'GBP')")
    currency_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    currency_p.set_defaults(func=_cmd_currency)

    # service <name> <country>
    service_p = gov_sub.add_parser("service", help="Civic service lookup")
    service_p.add_argument("service_name", help="Service name or keyword (e.g. 'healthcare', 'postal', 'passport')")
    service_p.add_argument("country", help="ISO 3166-1 alpha-2 country code")
    service_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    service_p.set_defaults(func=_cmd_service)

    # treaty <name>
    treaty_p = gov_sub.add_parser("treaty", help="Treaty lookup by ID or name")
    treaty_p.add_argument("name", help="Treaty ID (e.g. 'nato', 'npt', 'paris_agreement')")
    treaty_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    treaty_p.set_defaults(func=_cmd_treaty)

    # elections <country> [--method <name>]
    elections_p = gov_sub.add_parser("elections", help="Elections and voting info for a country")
    elections_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'GB')")
    elections_p.add_argument("--method", dest="method", help="Voting method lookup (e.g. 'paper_ballot')")
    elections_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    elections_p.set_defaults(func=_cmd_elections)

    # relations <country> [--alliance <name>] [--sanctions <target>]
    relations_p = gov_sub.add_parser("relations", help="Diplomatic relations, alliances, and sanctions")
    relations_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'GB')")
    relations_p.add_argument("--alliance", dest="alliance", help="Search alliances by name (e.g. 'nato', 'eu')")
    relations_p.add_argument("--sanctions", dest="sanctions", help="Sanctions lookup by target code (e.g. 'RU', 'IR')")
    relations_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    relations_p.set_defaults(func=_cmd_relations)

    # legal <country> [--charter <name>] [--courts <country>]
    legal_p = gov_sub.add_parser("legal", help="Legal systems, courts, and rights charters")
    legal_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'DE')")
    legal_p.add_argument("--charter", dest="charter", help="Rights charter lookup (e.g. 'udhr', 'echr')")
    legal_p.add_argument("--courts", dest="courts", help="Court hierarchy lookup by country code")
    legal_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    legal_p.set_defaults(func=_cmd_legal)

    # finance <country> [--debt <country>] [--pensions <country>]
    finance_p = gov_sub.add_parser("finance", help="Government budgets, debt, and pensions")
    finance_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'GB')")
    finance_p.add_argument("--debt", dest="debt", help="Sovereign debt lookup by country code")
    finance_p.add_argument("--pensions", dest="pensions", help="Public pension system lookup by country code")
    finance_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    finance_p.set_defaults(func=_cmd_finance)

    # navigate <query>
    nav_p = gov_sub.add_parser("navigate", help="Natural language query routing")
    nav_p.add_argument("query", help="Free-text query (e.g. 'visa for France', 'NATO treaty parties')")
    nav_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    nav_p.set_defaults(func=_cmd_navigate)

    # list
    list_p = gov_sub.add_parser("list", help="List available data domains and counts")
    list_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    list_p.set_defaults(func=_cmd_list)


__all__ = [
    "_cmd_body",
    "_cmd_borders",
    "_cmd_currency",
    "_cmd_elections",
    "_cmd_finance",
    "_cmd_legal",
    "_cmd_list",
    "_cmd_navigate",
    "_cmd_relations",
    "_cmd_service",
    "_cmd_tax",
    "_cmd_treaty",
    "add_governance_subparser",
]
