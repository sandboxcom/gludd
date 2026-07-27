"""Stub tax_currency module_util for molecule testing."""

import json
import sys


def main():
    country = "US"
    for i, a in enumerate(sys.argv[1:]):
        if a == "--country" and i + 1 < len(sys.argv[1:]):
            country = sys.argv[i + 2]
            break

    result = {
        "country": country,
        "currency_code": "USD",
        "currency_name": "United States Dollar",
        "tax_authority": "Internal Revenue Service (IRS)",
        "tax_types": ["income_progressive", "corporate", "sales", "property"],
        "filing_deadline": "April 15",
        "found": True,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
