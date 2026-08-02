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

from general_ludd.governance.contracts import (
    Policy,
    Rule,
)
from general_ludd.governance.core import ComplianceChecker, PolicyEngine
from general_ludd.governance.loader import (
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

    result: dict[str, Any] = {
        "country": code,
        "found": True,
        "currency_code": country_data.get("currency", ""),
        **country_data,
    }
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
        result["notes"] = (
            f"Mapped '{original_svc}' to nearest service '{svc_name}'. For US healthcare visit healthcare.gov"
        )
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
        domain_start = len(results)
        borders = get_borders()
        for name, entry in borders.BORDER_DATA.items():
            if any(word in name.lower() for word in query.split()):
                results.append({"domain": "borders", "match": name, "data": entry})
        # If no direct match, return all borders as available
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "borders",
                    "hint": "Try: " + ", ".join(sorted(borders.BORDER_DATA)[:5]),
                    "available_count": len(borders.BORDER_DATA),
                }
            )

    # ── Governing body keywords ──
    body_keywords = {
        "government",
        "body",
        "council",
        "parliament",
        "assembly",
        "organization",
        "union",
        "un ",
        "eu ",
        "nato",
        "wto",
        "who",
    }
    if any(kw in query for kw in body_keywords) or any(
        org in query for org in ("united nations", "european union", "african union")
    ):
        domain_start = len(results)
        bodies = get_governing_bodies()
        for body in bodies.INTERNATIONAL_BODIES:
            haystack = body["name"].lower() + " " + body["id"] + " " + " ".join(body.get("aliases", ()))
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "bodies", "match": body["name"], "data": body})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "bodies",
                    "hint": "Try: " + ", ".join(b["name"] for b in bodies.INTERNATIONAL_BODIES[:5]),
                    "available_count": len(bodies.INTERNATIONAL_BODIES),
                }
            )

    # ── Tax / currency keywords ──
    tax_keywords = {"tax", "vat", "gst", "irs", "revenue", "duty", "tariff"}
    currency_keywords = {
        "currency",
        "dollar",
        "euro",
        "pound",
        "yen",
        "rupee",
        "cad",
        "usd",
        "eur",
        "gbp",
        "jpy",
        "inr",
        "aud",
    }
    if any(kw in query for kw in tax_keywords) or any(kw in query for kw in currency_keywords):
        tax_cur = get_tax_currency()
        for code, record in tax_cur.TAX_CURRENCY.items():
            haystack = " ".join(
                [
                    code.lower(),
                    str(record.get("name", "")).lower(),
                    str(record.get("currency_code", "")).lower(),
                    str(record.get("currency_name", "")).lower(),
                    str(record.get("tax_authority", "")).lower(),
                ]
            )
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "tax_currency", "match": code, "data": record})

    # ── Treaty / conflict keywords ──
    treaty_keywords = {"treaty", "convention", "agreement", "accord", "pact", "alliance"}
    if any(kw in query for kw in treaty_keywords):
        domain_start = len(results)
        ct = get_conflicts_treaties()
        for treaty in ct.TREATY_DATABASE:
            haystack = treaty["name"].lower() + " " + treaty["id"] + " " + str(treaty.get("subject", "")).lower()
            if any(word in haystack for word in query.split() if len(word) > 3):
                results.append({"domain": "treaties", "match": treaty["name"], "data": treaty})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "treaties",
                    "hint": "Try: " + ", ".join(t["name"] for t in ct.TREATY_DATABASE[:5]),
                    "available_count": len(ct.TREATY_DATABASE),
                }
            )

    # ── Civic service keywords ──
    civic_keywords = {"service", "healthcare", "passport", "postal", "license", "vote", "voting", "registration"}
    if any(kw in query for kw in civic_keywords):
        civic = get_civic_services()
        for svc_id, svc_def in civic.SERVICES.items():
            for country_code, svc_info in svc_def.get("countries", {}).items():
                haystack = (
                    svc_id.lower()
                    + " "
                    + str(svc_info.get("issuing_body", "")).lower()
                    + " "
                    + str(svc_def.get("category", "")).lower()
                )
                if any(word in haystack for word in query.split() if len(word) > 3):
                    results.append(
                        {
                            "domain": "civic_services",
                            "match": (f"{svc_def.get('issuing_body', svc_id)} ({country_code})"),
                            "data": {
                                "country": country_code,
                                "service_id": svc_id,
                                "issuing_body": svc_info.get("issuing_body", ""),
                            },
                        }
                    )

    # ── Elections / voting keywords ──
    elections_keywords = {
        "election",
        "vote",
        "ballot",
        "electoral",
        "polling",
        "referendum",
        "fptp",
        "runoff",
        "proportional",
        "instant runoff",
        "compulsory voting",
    }
    if any(kw in query for kw in elections_keywords):
        domain_start = len(results)
        ev = get_elections_voting()
        for sys_type, sys_info in ev.ELECTION_SYSTEMS.items():
            haystack = sys_type.lower() + " " + str(sys_info.get("description", "")).lower()
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "elections_voting", "match": sys_type, "data": sys_info})
        for country_code, data in ev.ELECTION_DATA.items():
            haystack = country_code.lower() + " " + str(data.get("name", "")).lower()
            if any(word in haystack for word in query.split() if len(word) > 2):
                results.append({"domain": "elections_voting", "match": country_code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "elections_voting",
                    "hint": "Try: " + ", ".join(sorted(ev.ELECTION_DATA.keys())[:5]),
                    "available_count": len(ev.ELECTION_DATA),
                }
            )

    # ── International relations keywords ──
    ir_keywords = {
        "diplomacy",
        "diplomatic",
        "embassy",
        "sanction",
        "trade agreement",
        "visa waiver",
        "foreign policy",
        "un member",
        "alliance",
    }
    if any(kw in query for kw in ir_keywords) or any(
        org in query for org in ("five eyes", "g7", "g20", "nonaligned", "quad")
    ):
        domain_start = len(results)
        ir_mod = get_international_relations()
        for code, data in ir_mod.DIPLOMATIC_RELATIONS.items():
            haystack = (
                code.lower() + " " + str(data.get("name", "")).lower() + " " + " ".join(data.get("alliances", []))
            )
            if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                results.append({"domain": "international_relations", "match": code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "international_relations",
                    "hint": "Try: " + ", ".join(sorted(ir_mod.DIPLOMATIC_RELATIONS.keys())[:5]),
                    "available_count": len(ir_mod.DIPLOMATIC_RELATIONS),
                }
            )

    # ── Legal systems keywords ──
    legal_keywords = {
        "law",
        "legal",
        "court",
        "judge",
        "appeal",
        "constitution",
        "charter",
        "rights",
        "habeas",
        "trial",
        "common law",
        "civil law",
        "supreme court",
    }
    if any(kw in query for kw in legal_keywords):
        domain_start = len(results)
        ls_mod = get_legal_systems()
        for code, data in ls_mod.COURT_HIERARCHIES.items():
            haystack = code.lower() + " " + str(data.get("name", "")).lower() + " " + str(data.get("system_type", ""))
            if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                results.append({"domain": "legal_systems", "match": code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "legal_systems",
                    "hint": "Try: " + ", ".join(sorted(ls_mod.COURT_HIERARCHIES.keys())[:5]),
                    "available_count": len(ls_mod.COURT_HIERARCHIES),
                }
            )

    # ── Public finance keywords ──
    finance_keywords = {
        "budget",
        "debt",
        "pension",
        "fiscal",
        "expenditure",
        "sovereign debt",
        "government spending",
        "public finance",
    }
    if any(kw in query for kw in finance_keywords):
        domain_start = len(results)
        pf_mod = get_public_finance()
        for code, data in pf_mod.COUNTRY_BUDGETS.items():
            haystack = code.lower() + " " + str(data.get("country", "")).lower()
            if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                results.append({"domain": "public_finance", "match": code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "public_finance",
                    "hint": "Try: " + ", ".join(sorted(pf_mod.COUNTRY_BUDGETS.keys())[:5]),
                    "available_count": len(pf_mod.COUNTRY_BUDGETS),
                }
            )

    # ── Jurisdictions keywords ──
    jurisd_keywords = {
        "jurisdiction",
        "iso code",
        "subdivision",
        "territory",
        "region code",
        "fips",
        "gleif",
    }
    if any(kw in query for kw in jurisd_keywords):
        domain_start = len(results)
        jurisd = get_jurisdictions()
        for code, data in jurisd.JURISDICTION_CODES.items():
            haystack = code.lower() + " " + str(data.get("name", "")).lower()
            if any(word in haystack for word in query.split() if len(word) > 1):
                results.append({"domain": "jurisdictions", "match": code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "jurisdictions",
                    "hint": "Try: " + ", ".join(sorted(jurisd.JURISDICTION_CODES.keys())[:5]),
                    "available_count": len(jurisd.JURISDICTION_CODES),
                }
            )

    # ── Classification markings keywords ──
    class_keywords = {
        "classification",
        "classified",
        "clearance",
        "secret",
        "top secret",
        "banner",
        "portion",
        "marking",
        "declass",
        "caveat",
        "noforn",
        "cosmic",
    }
    if any(kw in query for kw in class_keywords):
        domain_start = len(results)
        cm = get_classification_markings()
        for system in cm.BANNER_FORMATS:
            results.append({"domain": "classification_markings", "match": system, "data": {"system": system}})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "classification_markings",
                    "hint": "Try: " + ", ".join(sorted(cm.BANNER_FORMATS.keys())),
                    "available_count": len(cm.BANNER_FORMATS),
                }
            )

    # ── Authority registry keywords ──
    authority_keywords = {
        "authority",
        "issuer",
        "issuing",
        "consulate",
        "passport office",
        "department of",
        "ministry of",
    }
    if any(kw in query for kw in authority_keywords):
        domain_start = len(results)
        ar = get_authority_registry()
        for code, data in ar.AUTHORITY_INSTRUMENTS.items():
            haystack = code.lower() + " " + str(data.get("name", "")).lower()
            if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                results.append({"domain": "authority_registry", "match": code, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "authority_registry",
                    "hint": "Try: " + ", ".join(sorted(ar.AUTHORITY_INSTRUMENTS.keys())[:5]),
                    "available_count": len(ar.AUTHORITY_INSTRUMENTS),
                }
            )

    # ── Info classification keywords ──
    info_cls_keywords = {
        "foia",
        "freedom of information",
        "info access",
        "official source",
        "gazette",
        "information access",
    }
    if any(kw in query for kw in info_cls_keywords):
        domain_start = len(results)
        ic = get_info_classification()
        for country, data in ic.CLASSIFICATION_BY_COUNTRY.items():
            haystack = country.lower() + " " + str(data.get("system", "")).lower()
            if any(word in haystack.lower() for word in query.split() if len(word) > 1):
                results.append({"domain": "info_classification", "match": country, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "info_classification",
                    "hint": "Try: " + ", ".join(sorted(ic.CLASSIFICATION_BY_COUNTRY.keys())[:5]),
                    "available_count": len(ic.CLASSIFICATION_BY_COUNTRY),
                }
            )

    # ── Decision makers keywords ──
    dm_keywords = {
        "decision maker",
        "official",
        "politician",
        "senator",
        "representative",
        "minister",
        "legislator",
        "proclivity",
        "congress",
        "parliament member",
        "president",
        "prime minister",
        "chief justice",
    }
    if any(kw in query for kw in dm_keywords):
        domain_start = len(results)
        dm = get_decision_makers()
        for country_code, officials in dm.DECISION_MAKERS.items():
            for official in officials:
                haystack = (
                    country_code.lower()
                    + " "
                    + official.get("role", "").lower()
                    + " "
                    + official.get("institution", "").lower()
                )
                if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                    results.append(
                        {"domain": "decision_makers", "match": f"{country_code}: {official['role']}", "data": official}
                    )
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "decision_makers",
                    "hint": "Try: " + ", ".join(sorted(dm.DECISION_MAKERS.keys())[:5]),
                    "available_count": len(dm.DECISION_MAKERS),
                }
            )

    # ── Postal / delivery keywords ──
    postal_keywords = {
        "postal",
        "postage",
        "mail",
        "zip code",
        "courier",
        "tracking",
        "customs declaration",
        "shipping",
        "fedex",
        "usps",
        "dhl",
        "ups",
    }
    if any(kw in query for kw in postal_keywords):
        domain_start = len(results)
        pd = get_postal_delivery()
        for country, data in pd.POSTAL_CODE_PATTERNS.items():
            haystack = country.lower() + " " + str(data.get("name", "")).lower()
            if any(word in haystack.lower() for word in query.split() if len(word) > 1):
                results.append({"domain": "postal_delivery", "match": country, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "postal_delivery",
                    "hint": "Try: " + ", ".join(sorted(pd.POSTAL_CODE_PATTERNS.keys())[:5]),
                    "available_count": len(pd.POSTAL_CODE_PATTERNS),
                }
            )

    # ── Military service keywords ──
    military_keywords = {
        "military",
        "army",
        "navy",
        "conscription",
        "draft",
        "veteran",
        "service branch",
        "armed forces",
        "air force",
        "marine",
    }
    if any(kw in query for kw in military_keywords):
        domain_start = len(results)
        ms = get_military_service()
        for country, data in ms.CONSCRIPTION_DATA.items():
            haystack = country.lower() + " " + str(data.get("name", "")).lower()
            if any(word in haystack.lower() for word in query.split() if len(word) > 2):
                results.append({"domain": "military_service", "match": country, "data": data})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "military_service",
                    "hint": "Try: " + ", ".join(sorted(ms.CONSCRIPTION_DATA.keys())[:5]),
                    "available_count": len(ms.CONSCRIPTION_DATA),
                }
            )

    # ── Licenses / permits keywords ──
    licenses_keywords = {
        "driver",
        "driving license",
        "professional license",
        "export control",
        "export license",
        "permit",
        "certification",
        "medical license",
    }
    if any(kw in query for kw in licenses_keywords):
        domain_start = len(results)
        lp = get_licenses_permits()
        for lic_type in lp.LICENSE_TYPES:
            results.append({"domain": "licenses_permits", "match": lic_type, "data": {"license_type": lic_type}})
        if len(results) == domain_start:
            results.append(
                {
                    "domain": "licenses_permits",
                    "hint": "Try: " + ", ".join(sorted(lp.LICENSE_TYPES)[:5]),
                    "available_count": len(lp.LICENSE_TYPES),
                }
            )

    # ── No results ──
    if not results:
        print(
            "No governance data matched your query. Try keywords like:\n"
            "  border, body, tax, currency, treaty, service,\n"
            "  election, embassy, court, budget, classification, military,\n"
            "  or specific names like 'Schengen', 'UN', 'NATO', 'Paris Agreement'.",
            file=sys.stderr,
        )
        sys.exit(1)

    _print_result({"query": query, "results": results, "count": len(results)}, json_output=json_output)


def _cmd_jurisdictions(args: argparse.Namespace) -> None:
    """``gludd governance jurisdictions <code>`` — jurisdiction lookup."""
    jurisd = get_jurisdictions()
    if args.subdivisions:
        result = jurisd.get_subdivisions(args.code)
        if not result:
            print(f"No subdivisions found for '{args.code.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(
            {"code": args.code.upper(), "found": True, "count": len(result), "subdivisions": result},
            json_output=args.json,
        )
        return
    result = jurisd.get_jurisdiction(args.code)
    if result is None:
        print(f"No jurisdiction found for '{args.code}'.", file=sys.stderr)
        sys.exit(1)
    parents = jurisd.get_parents(args.code)
    if parents:
        result["parents"] = list(parents)
    _print_result({"found": True, **result}, json_output=args.json)


def _cmd_classification(args: argparse.Namespace) -> None:
    """``gludd governance classification <system>`` — classification markings."""
    cm = get_classification_markings()
    if args.caveat:
        result = cm.resolve_caveat(args.caveat)
        if result is None:
            print(f"No caveat found for '{args.caveat}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"caveat": args.caveat, "found": True, **result}, json_output=args.json)
        return
    if args.banner:
        banner = cm.get_banner_line(args.system, args.banner)
        if banner is None:
            print(f"No banner line for level '{args.banner}' in system '{args.system}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(
            {"system": args.system, "level": args.banner, "banner": banner, "found": True}, json_output=args.json
        )
        return
    systems = cm.list_systems()
    caveats = cm.list_caveats(args.system if args.system else None)
    _print_result({"systems": systems, "caveats": caveats, "found": True}, json_output=args.json)


def _cmd_authority(args: argparse.Namespace) -> None:
    """``gludd governance authority <query>`` — issuing authority lookup."""
    ar = get_authority_registry()
    if args.code:
        result = ar.get_authority(args.code)
        if result is None:
            print(f"No authority found for '{args.code}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"code": args.code, "found": True, **result}, json_output=args.json)
        return
    if args.instrument:
        authorities = ar.authorities_by_instrument(args.instrument)
        if not authorities:
            print(f"No authorities issue '{args.instrument}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(
            {"instrument": args.instrument, "found": True, "count": len(authorities), "authorities": authorities},
            json_output=args.json,
        )
        return
    all_authorities = {
        code: {"name": v["name"], "jurisdiction": v["jurisdiction"]} for code, v in ar.AUTHORITY_INSTRUMENTS.items()
    }
    _print_result({"count": len(all_authorities), "authorities": all_authorities, "found": True}, json_output=args.json)


def _cmd_info_classification(args: argparse.Namespace) -> None:
    """``gludd governance info-class <country>`` — info classification and FOIA."""
    ic = get_info_classification()
    display_country = args.country.strip().upper()
    lookup_country = "UK" if display_country == "GB" else display_country
    if args.foia:
        result = ic.get_foia_procedure(lookup_country)
        if result is None:
            print(f"No FOIA procedure found for '{display_country}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": display_country, "found": True, **result}, json_output=args.json)
        return
    if args.source:
        result = ic.find_official_source(args.source, lookup_country)
        if result is None:
            print(f"No source '{args.source}' found for '{display_country}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": display_country, "found": True, **result}, json_output=args.json)
        return
    if args.equiv:
        parts = args.equiv.split(",")
        if len(parts) == 3:
            country_a = parts[1].strip().upper()
            country_b = parts[2].strip().upper()
            lookup_a = "UK" if country_a == "GB" else country_a
            lookup_b = "UK" if country_b == "GB" else country_b
            ok = ic.check_clearance_equiv(parts[0].strip(), lookup_a, lookup_b)
            _print_result(
                {
                    "level": parts[0].strip(),
                    "country_a": country_a,
                    "country_b": country_b,
                    "equivalent": ok,
                    "found": True,
                },
                json_output=args.json,
            )
            return
        print("Use: --equiv <level>,<country_a>,<country_b>", file=sys.stderr)
        sys.exit(1)
    result = ic.get_classification_system(lookup_country)
    if result is None:
        print(f"No classification system found for '{display_country}'.", file=sys.stderr)
        sys.exit(1)
    _print_result({"country": display_country, "found": True, **result}, json_output=args.json)


def _cmd_decision_makers(args: argparse.Namespace) -> None:
    """``gludd governance decision-makers <country>`` — decision-maker lookup."""
    dm = get_decision_makers()
    if args.person:
        info = dm.get_decision_authority(args.person)
        if info is None:
            print(f"No decision-maker found for '{args.person}'.", file=sys.stderr)
            sys.exit(1)
        proclivity = dm.assess_proclivity(args.person, args.topic or "taxation")
        _print_result(
            {"person_id": args.person, "found": True, "authority": info, "proclivity": proclivity},
            json_output=args.json,
        )
        return
    if args.topic:
        results = dm.find_decision_maker(args.topic)
        if not results:
            print(f"No decision-makers found for topic '{args.topic}'.", file=sys.stderr)
            sys.exit(1)
        _print_result(
            {"topic": args.topic, "found": True, "count": len(results), "profiles": results}, json_output=args.json
        )
        return
    result = dm.lookup_decision_makers(args.country)
    if not result.get("found"):
        print(result.get("message", f"No decision-makers found for '{args.country.upper()}'."), file=sys.stderr)
        sys.exit(1)
    _print_result(result, json_output=args.json)


def _cmd_postal(args: argparse.Namespace) -> None:
    """``gludd governance postal <query>`` — postal code and courier lookup."""
    pd = get_postal_delivery()
    if args.courier:
        url = pd.get_courier_tracking_url(args.courier, args.tracking or "TRACKING123")
        if url is None:
            print(f"No courier found for '{args.courier}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"courier": args.courier, "tracking_url": url, "found": True}, json_output=args.json)
        return
    if args.customs:
        result = pd.get_customs_declaration_format(args.country)
        if result is None:
            print(f"No customs format found for '{args.country.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": args.country.upper(), "found": True, **result}, json_output=args.json)
        return
    result = pd.get_postal_code_pattern(args.country)
    if result is None:
        print(f"No postal code pattern found for '{args.country}'.", file=sys.stderr)
        sys.exit(1)
    _print_result({"country": args.country, "found": True, **result}, json_output=args.json)


def _cmd_military(args: argparse.Namespace) -> None:
    """``gludd governance military <country>`` — military service lookup."""
    ms = get_military_service()
    if args.branches:
        branches = ms.get_military_branches(args.country)
        if branches is None:
            print(f"No military branches found for '{args.country.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": args.country.upper(), "found": True, "branches": branches}, json_output=args.json)
        return
    if args.benefits:
        benefits = ms.get_veteran_benefits(args.country, args.benefits if args.benefits != "all" else None)
        if benefits is None:
            print(f"No veteran benefits found for '{args.country.upper()}'.", file=sys.stderr)
            sys.exit(1)
        _print_result({"country": args.country.upper(), "found": True, **benefits}, json_output=args.json)
        return
    if args.conscription:
        conscripts = ms.list_mandatory_service_countries()
        _print_result(
            {"mandatory_conscription_countries": conscripts, "count": len(conscripts), "found": True},
            json_output=args.json,
        )
        return
    result = ms.get_conscription_info(args.country)
    if result is None:
        print(f"No conscription data found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    _print_result({"country": args.country.upper(), "found": True, **result}, json_output=args.json)


def _cmd_licenses(args: argparse.Namespace) -> None:
    """``gludd governance licenses <country>`` — license and permit lookup."""
    lp = get_licenses_permits()
    if args.export_control:
        parts = args.export_control.split(",")
        if len(parts) == 2:
            result = lp.get_export_license_requirements(parts[0].strip(), parts[1].strip())
            if result is None:
                print(f"No export control data for '{args.export_control}'.", file=sys.stderr)
                sys.exit(1)
            _print_result({"found": True, **result}, json_output=args.json)
            return
        print("Use: --export-control <country>,<goods_category>", file=sys.stderr)
        sys.exit(1)
    if args.license_type:
        result = lp.get_license_info(args.license_type, args.country)
        if result is None:
            print(
                f"No license info for type '{args.license_type}' in '{args.country.upper()}'. "
                f"Available: {', '.join(lp.list_professions_for_country(args.country))}",
                file=sys.stderr,
            )
            sys.exit(1)
        _print_result(result, json_output=args.json)
        return
    professions = lp.list_professions_for_country(args.country)
    if not professions:
        print(f"No license registry found for '{args.country.upper()}'.", file=sys.stderr)
        sys.exit(1)
    _print_result(
        {"country": args.country.upper(), "found": True, "professions": professions, "count": len(professions)},
        json_output=args.json,
    )


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
    body_names = {
        "gb": "House of Commons",
        "us": "House of Representatives / Senate",
        "ca": "House of Commons",
        "au": "House of Representatives / Senate",
        "de": "Bundestag",
        "fr": "National Assembly",
        "jp": "House of Representatives",
        "in": "Lok Sabha",
    }
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
        _print_result(
            {"query": args.alliance, "found": True, "count": len(matches), "alliances": matches}, json_output=args.json
        )
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
    jurisd = get_jurisdictions()
    classif = get_classification_markings()
    author = get_authority_registry()
    info_cls = get_info_classification()
    dm = get_decision_makers()
    postal = get_postal_delivery()
    military = get_military_service()
    licenses = get_licenses_permits()

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
            "count": len(tax_cur.TAX_DATA),
            "examples": list(sorted(tax_cur.TAX_DATA))[:3],
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
        "jurisdictions": {
            "count": len(jurisd.JURISDICTION_CODES),
            "examples": list(sorted(jurisd.JURISDICTION_CODES))[:3],
        },
        "classification_markings": {
            "count": len(classif.BANNER_FORMATS),
            "examples": sorted(classif.BANNER_FORMATS.keys())[:3],
        },
        "authority_registry": {
            "count": len(author.AUTHORITY_INSTRUMENTS),
            "examples": sorted(author.AUTHORITY_INSTRUMENTS.keys())[:3],
        },
        "info_classification": {
            "count": len(info_cls.CLASSIFICATION_BY_COUNTRY),
            "examples": sorted(info_cls.CLASSIFICATION_BY_COUNTRY.keys())[:3],
        },
        "decision_makers": {
            "count": len(dm.DECISION_MAKERS),
            "examples": sorted(dm.DECISION_MAKERS.keys())[:3],
        },
        "postal_delivery": {
            "count": len(postal.POSTAL_CODE_PATTERNS),
            "examples": sorted(postal.POSTAL_CODE_PATTERNS.keys())[:3],
        },
        "military_service": {
            "count": len(military.CONSCRIPTION_DATA),
            "examples": sorted(military.CONSCRIPTION_DATA.keys())[:3],
        },
        "licenses_permits": {
            "count": len(licenses.LICENSE_TYPES),
            "examples": sorted(licenses.LICENSE_TYPES)[:3],
        },
    }
    for key in list(domains.keys()):
        domains[key]["_key"] = key
    _print_result(domains, json_output=args.json)


# ── Subparser registration ────────────────────────────────────────────────────


_policy_engine = PolicyEngine()
_compliance_checker = ComplianceChecker(_policy_engine)


def _cmd_policy_add(args: argparse.Namespace) -> None:
    """``gludd governance policy add`` — register a governance policy."""
    name = args.name
    if name in _policy_engine:
        print(f"Policy '{name}' already exists", file=sys.stderr)
        sys.exit(1)
    policy = Policy(
        name=name,
        description=getattr(args, "description", ""),
        domain=getattr(args, "domain", ""),
        level=getattr(args, "level", "enterprise"),
        status=getattr(args, "status", "draft"),
        effective_date=getattr(args, "effective_date", None),
    )
    _policy_engine.register_policy(policy)
    data = {
        "name": policy.name,
        "domain": policy.domain,
        "level": policy.level,
        "status": policy.status,
        "description": policy.description,
    }
    _print_result(data, json_output=args.json)


def _cmd_policy_list(args: argparse.Namespace) -> None:
    """``gludd governance policy list`` — list registered policies."""
    domain = getattr(args, "domain", None)
    level = getattr(args, "level", None)
    policies = _policy_engine.list_policies(domain=domain, level=level)
    result = []
    for p in policies:
        result.append(
            {
                "name": p.name,
                "domain": p.domain,
                "level": p.level,
                "status": p.status,
                "description": p.description,
                "rule_count": len(_policy_engine.get_rules(p.name)),
            }
        )
    _print_result({"policies": result, "count": len(result)}, json_output=args.json)


def _cmd_policy_get(args: argparse.Namespace) -> None:
    """``gludd governance policy get`` — show a policy with its rules."""
    policy = _policy_engine.get_policy(args.name)
    if policy is None:
        print(f"Policy '{args.name}' not found", file=sys.stderr)
        sys.exit(1)
    rules = _policy_engine.get_rules(args.name)
    data = {
        "name": policy.name,
        "domain": policy.domain,
        "level": policy.level,
        "status": policy.status,
        "description": policy.description,
        "effective_date": policy.effective_date,
        "rules": [
            {
                "rule_id": r.rule_id,
                "condition": r.condition,
                "action": r.action,
                "priority": r.priority,
                "enforcement": r.enforcement,
            }
            for r in rules
        ],
        "rule_count": len(rules),
    }
    _print_result(data, json_output=args.json)


def _cmd_policy_remove(args: argparse.Namespace) -> None:
    """``gludd governance policy remove`` — remove a policy."""
    if args.name not in _policy_engine:
        print(f"Policy '{args.name}' not found", file=sys.stderr)
        sys.exit(1)
    _policy_engine._policies.pop(args.name, None)
    _policy_engine._rules.pop(args.name, None)
    _print_result({"removed": args.name}, json_output=args.json)


def _cmd_policy_rule_add(args: argparse.Namespace) -> None:
    """``gludd governance policy rule-add`` — add a rule to a policy."""
    if args.policy not in _policy_engine:
        print(f"Policy '{args.policy}' not found", file=sys.stderr)
        sys.exit(1)
    rule = Rule(
        policy_name=args.policy,
        rule_id=args.rule_id,
        condition=getattr(args, "condition", ""),
        action=getattr(args, "action", "advisory"),
        priority=getattr(args, "priority", 0),
        enforcement=getattr(args, "enforcement", "advisory"),
    )
    _policy_engine.register_rule(rule)
    data = {
        "policy_name": rule.policy_name,
        "rule_id": rule.rule_id,
        "condition": rule.condition,
        "action": rule.action,
        "priority": rule.priority,
        "enforcement": rule.enforcement,
    }
    _print_result(data, json_output=args.json)


def _cmd_policy_check(args: argparse.Namespace) -> None:
    """``gludd governance policy check`` — evaluate policy compliance."""
    report = _compliance_checker.check(args.subject)
    data = {
        "subject": report.subject,
        "status": report.status,
        "is_compliant": report.is_compliant,
        "violations": report.violations,
        "audit_trail": [
            {
                "entry_id": a.entry_id,
                "subject": a.subject,
                "action": a.action,
                "details": a.details,
                "timestamp": a.timestamp,
            }
            for a in report.audit_trail
        ],
    }
    _print_result(data, json_output=args.json)
    if not report.is_compliant:
        sys.exit(1)


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

    # jurisdictions <code> [--subdivisions]
    jurisdictions_p = gov_sub.add_parser("jurisdictions", help="Jurisdiction lookup by ISO code")
    jurisdictions_p.add_argument("code", help="ISO 3166-1 alpha-2, alpha-3, numeric, or subdivision code")
    jurisdictions_p.add_argument("--subdivisions", action="store_true", help="List subdivisions instead")
    jurisdictions_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    jurisdictions_p.set_defaults(func=_cmd_jurisdictions)

    # classification [system] [--banner <level>] [--caveat <code>]
    classification_p = gov_sub.add_parser("classification", help="Classification markings lookup")
    classification_p.add_argument(
        "system", nargs="?", default="", help="Classification system: US, UK, NATO, EU, FR, DE, CA, AU"
    )
    classification_p.add_argument(
        "--banner", dest="banner", help="Banner line lookup by level (e.g. 'secret', 'top_secret')"
    )
    classification_p.add_argument("--caveat", dest="caveat", help="Caveat code lookup (e.g. 'NOFORN', 'SI', 'COSMIC')")
    classification_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    classification_p.set_defaults(func=_cmd_classification)

    # authority [<code>] [--instrument <type>]
    authority_p = gov_sub.add_parser("authority", help="Issuing authority lookup")
    authority_p.add_argument("query", nargs="?", default="", help="Authority code (e.g. 'US-DOS', 'UK-HMPO')")
    authority_p.add_argument("--code", dest="code", help="Direct authority code lookup")
    authority_p.add_argument(
        "--instrument",
        dest="instrument",
        help="Find authorities by instrument type (e.g. 'passport', 'export_license')",
    )
    authority_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    authority_p.set_defaults(func=_cmd_authority)

    # info-class <country> [--foia] [--source <topic>] [--equiv <level>,<country_a>,<country_b>]
    info_class_p = gov_sub.add_parser("info-class", help="Info classification, FOIA, and official sources")
    info_class_p.add_argument("country", help="ISO 3166-1 alpha-2 country code (e.g. 'US', 'GB')")
    info_class_p.add_argument("--foia", action="store_true", help="Show FOIA request procedure")
    info_class_p.add_argument(
        "--source", dest="source", help="Find official info source by keyword (e.g. 'court', 'gazette', 'audit')"
    )
    info_class_p.add_argument(
        "--equiv", dest="equiv", help="Check clearance equivalence: <level>,<country_a>,<country_b>"
    )
    info_class_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    info_class_p.set_defaults(func=_cmd_info_classification)

    # decision-makers <country> [--person <id>] [--topic <topic>]
    dm_p = gov_sub.add_parser("decision-makers", help="Decision-maker profiles and proclivity")
    dm_p.add_argument("country", nargs="?", default="US", help="ISO 3166-1 alpha-2 country code (default: US)")
    dm_p.add_argument("--person", dest="person", help="Person ID lookup (e.g. 'us-sen-01')")
    dm_p.add_argument("--topic", dest="topic", help="Filter/topic assessment (e.g. 'taxation', 'healthcare')")
    dm_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    dm_p.set_defaults(func=_cmd_decision_makers)

    # postal <country> [--courier <name>] [--tracking <number>] [--customs]
    postal_p = gov_sub.add_parser("postal", help="Postal codes, courier tracking, and customs")
    postal_p.add_argument("country", nargs="?", default="US", help="ISO 3166-1 alpha-2 country code (default: US)")
    postal_p.add_argument("--courier", dest="courier", help="Courier tracking URL lookup (e.g. 'usps', 'fedex', 'dhl')")
    postal_p.add_argument("--tracking", dest="tracking", help="Tracking number for courier URL")
    postal_p.add_argument("--customs", action="store_true", help="Show customs declaration format instead")
    postal_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    postal_p.set_defaults(func=_cmd_postal)

    # military <country> [--branches] [--benefits <category>] [--conscription]
    military_p = gov_sub.add_parser("military", help="Military service, branches, and veteran benefits")
    military_p.add_argument("country", nargs="?", default="US", help="ISO 3166-1 alpha-2 country code (default: US)")
    military_p.add_argument("--branches", action="store_true", help="Show military branches")
    military_p.add_argument(
        "--benefits",
        dest="benefits",
        nargs="?",
        const="all",
        help="Show veteran benefits (optionally filter by category)",
    )
    military_p.add_argument("--conscription", action="store_true", help="List countries with active conscription")
    military_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    military_p.set_defaults(func=_cmd_military)

    # licenses <country> [--license-type <type>] [--export-control <country>,<category>]
    licenses_p = gov_sub.add_parser("licenses", help="Professional licenses, permits, and export controls")
    licenses_p.add_argument("country", nargs="?", default="US", help="ISO 3166-1 alpha-2 country code (default: US)")
    licenses_p.add_argument(
        "--license-type",
        dest="license_type",
        help="License type lookup (e.g. 'driving', 'medical_practitioner', 'lawyer')",
    )
    licenses_p.add_argument(
        "--export-control",
        dest="export_control",
        help="Export license lookup: <country>,<category> (e.g. 'US,military_items')",
    )
    licenses_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    licenses_p.set_defaults(func=_cmd_licenses)

    # policy <subcommand>
    policy_p = gov_sub.add_parser("policy", help="Governance policy management and evaluation")
    policy_sub = policy_p.add_subparsers(dest="policy_command")

    # policy add
    pa_p = policy_sub.add_parser("add", help="Register a governance policy")
    pa_p.add_argument("name", help="Policy name")
    pa_p.add_argument("--domain", default="", help="Policy domain (e.g. security, data)")
    pa_p.add_argument("--level", default="enterprise", help="Policy level (e.g. enterprise, project)")
    pa_p.add_argument("--description", default="", help="Policy description")
    pa_p.add_argument("--status", default="draft", help="Policy status (draft, active, deprecated)")
    pa_p.add_argument("--effective-date", default=None, help="Effective date (ISO 8601)")
    pa_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pa_p.set_defaults(func=_cmd_policy_add)

    # policy list
    pl_p = policy_sub.add_parser("list", help="List registered policies")
    pl_p.add_argument("--domain", default=None, help="Filter by domain")
    pl_p.add_argument("--level", default=None, help="Filter by level")
    pl_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pl_p.set_defaults(func=_cmd_policy_list)

    # policy get <name>
    pg_p = policy_sub.add_parser("get", help="Show a policy and its rules")
    pg_p.add_argument("name", help="Policy name")
    pg_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pg_p.set_defaults(func=_cmd_policy_get)

    # policy remove <name>
    pr_p = policy_sub.add_parser("remove", help="Remove a policy")
    pr_p.add_argument("name", help="Policy name")
    pr_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pr_p.set_defaults(func=_cmd_policy_remove)

    # policy rule-add --policy <name> --rule-id <id> ...
    pra_p = policy_sub.add_parser("rule-add", help="Add a rule to a policy")
    pra_p.add_argument("--policy", required=True, help="Policy name")
    pra_p.add_argument("--rule-id", required=True, help="Rule identifier")
    pra_p.add_argument("--condition", default="", help="Rule condition")
    pra_p.add_argument("--action", default="advisory", help="Rule action")
    pra_p.add_argument("--priority", type=int, default=0, help="Rule priority")
    pra_p.add_argument("--enforcement", default="advisory", help="Rule enforcement level")
    pra_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pra_p.set_defaults(func=_cmd_policy_rule_add)

    # policy check <subject>
    pc_p = policy_sub.add_parser("check", help="Evaluate compliance of a subject")
    pc_p.add_argument("subject", help="Subject to evaluate (e.g. repo-1, project-x)")
    pc_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    pc_p.set_defaults(func=_cmd_policy_check)


__all__ = [
    "_cmd_authority",
    "_cmd_body",
    "_cmd_borders",
    "_cmd_classification",
    "_cmd_currency",
    "_cmd_decision_makers",
    "_cmd_elections",
    "_cmd_finance",
    "_cmd_info_classification",
    "_cmd_jurisdictions",
    "_cmd_legal",
    "_cmd_licenses",
    "_cmd_list",
    "_cmd_military",
    "_cmd_navigate",
    "_cmd_policy_add",
    "_cmd_policy_check",
    "_cmd_policy_get",
    "_cmd_policy_list",
    "_cmd_policy_remove",
    "_cmd_policy_rule_add",
    "_cmd_postal",
    "_cmd_relations",
    "_cmd_service",
    "_cmd_tax",
    "_cmd_treaty",
    "add_governance_subparser",
]
