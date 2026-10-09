#!/usr/bin/env python3
"""Command-line adapter for the governance tax and currency knowledge module."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tax_currency import CURRENCIES, TAX_AUTHORITIES, TAX_DATA, list_countries


def lookup_tax_currency(country: str) -> dict[str, Any]:
    """Return the bounded country summary consumed by the public Ansible role."""
    code = country.strip().upper()
    country_data = TAX_DATA.get(code)
    if country_data is None:
        return {"country": code, "found": False}
    currency_code = str(country_data["currency"])
    currency = CURRENCIES.get(currency_code, {})
    authority = TAX_AUTHORITIES.get(code, {})
    return {
        "country": code,
        "country_name": country_data["name"],
        "currency_code": currency_code,
        "currency_name": currency.get("name", "N/A"),
        "filing_deadline": country_data.get("filing_deadline"),
        "found": True,
        "tax_authority": authority.get("name", "N/A"),
        "tax_types": list(country_data.get("tax_types", ())),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the shell-free governance lookup interface used by Ansible."""
    parser = argparse.ArgumentParser(description="Governance tax and currency lookup")
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--country", help="ISO 3166-1 alpha-2 code")
    lookup.add_argument("--list-countries", action="store_true")
    args = parser.parse_args(argv)

    if args.list_countries:
        print(json.dumps({"countries": list_countries(), "found": True}, sort_keys=True))
        return 0

    result = lookup_tax_currency(args.country)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
