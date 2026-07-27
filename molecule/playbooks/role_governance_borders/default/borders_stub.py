"""Stub borders module_util for molecule testing."""

import json
import sys

_DATA = {
    "US-Canada land border": {
        "type": "land",
        "controlling_bodies": ["US CBP", "CBSA"],
        "recognition": "universal",
        "crossing_requirements": {
            "documents": ["passport"],
            "visa_required": False,
            "visa_type": None,
            "notes": "Visa-free <180 days",
        },
    },
}


def main():
    query = "all"
    for a in sys.argv[1:]:
        if a.startswith("--query"):
            continue
        if not a.startswith("-"):
            query = a
    if query == "all":
        print(json.dumps(_DATA))
    else:
        entry = _DATA.get(query)
        print(json.dumps(entry if entry is not None else {}))


if __name__ == "__main__":
    main()
