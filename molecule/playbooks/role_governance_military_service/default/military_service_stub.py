"""Stub military_service module_util for molecule testing."""

import json
import sys

_DATA = {
    "US": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": True,
        "registration_age": "18-25 (Selective Service, males only)",
        "notes": "All-volunteer force since 1973.",
    },
    "IL": {
        "active": True,
        "type": "mandatory_conscription",
        "registration_required": True,
        "registration_age": "18 (both sexes)",
        "duration_months": 32,
        "notes": "Mandatory for Jewish, Druze, and Circassian citizens.",
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
