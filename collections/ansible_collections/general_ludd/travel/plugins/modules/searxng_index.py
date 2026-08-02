#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: searxng_index
  short_description: Create and query a travel-specific SearXNG search index
  description:
    - Manages named SearXNG indices for the travel collection.
    - Creates a travel-meta index combining six engines (Google Flights, Kayak,
      Skyscanner, Booking.com, TripAdvisor, Expedia).
    - Supports create, query, list, and delete operations so any travel module
      can reuse a shared index.
  options:
    name:
      description: Index name (defaults to ``travel-meta``).
      type: str
      required: false
      default: "travel-meta"
    state:
      description: Desired state of the index.
      type: str
      default: present
      choices: [present, absent, query]
    engines:
      description: >
        Comma-separated engine list for index creation. When omitted the
        six-engine travel default is used.
      type: str
      default: ""
    query:
      description: Search query text (required when C(state=query)).
      type: str
      required: false
    max_results:
      description: Maximum results when querying.
      type: int
      default: 10
    searxng_url:
      description: Base URL of the SearXNG instance.
      type: str
      default: "http://localhost:8080"
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""

EXAMPLES:
  - name: Create the default travel index
    general_ludd.travel.searxng_index:
      name: travel-meta
      state: present

  - name: Query the travel index for flights
    general_ludd.travel.searxng_index:
      name: travel-meta
      state: query
      query: "flights NYC to Paris September 2026"
    register: results

  - name: Create a custom hotel-only index
    general_ludd.travel.searxng_index:
      name: hotels-only
      engines: "booking,tripadvisor,expedia"
      state: present

  - name: Delete an index
    general_ludd.travel.searxng_index:
      name: hotels-only
      state: absent

RETURN:
  name:
    description: Index name used.
    type: str
    returned: always
  state:
    description: Operation performed.
    type: str
    returned: always
  engines:
    description: Engines configured for the index (create / query).
    type: list
    elements: str
    returned: when state is present or query
  existed:
    description: Whether the index already existed (present only).
    type: bool
    returned: when state is present
  created_at:
    description: ISO-8601 timestamp of index creation.
    type: str
    returned: when state is present or query
  results:
    description: Query results (query only).
    type: list
    elements: dict
    returned: when state is query
  result_count:
    description: Number of query results returned.
    type: int
    returned: when state is query
  indices:
    description: List of all index names (list only).
    type: list
    elements: str
    returned: when state is list
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]
from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_client import (
    TRAVEL_INDEX_ENGINES,
    SearXNGIndexNotFoundError,
    TravelIndexManager,
)

_manager: TravelIndexManager | None = None


def _get_manager() -> TravelIndexManager:
    global _manager
    if _manager is None:
        _manager = TravelIndexManager()
    return _manager


def create_index(name: str, engines: list[str] | None = None) -> dict[str, Any]:
    mgr = _get_manager()
    return mgr.create(name, engines=engines)


def index_exists(name: str) -> bool:
    mgr = _get_manager()
    return mgr.has(name)


def query_index(name: str, query_text: str, max_results: int = 10) -> list[dict[str, Any]]:
    mgr = _get_manager()
    return mgr.query(name, query_text, max_results=max_results)


def delete_index(name: str) -> dict[str, Any]:
    mgr = _get_manager()
    return mgr.delete(name)


def list_indices() -> list[str]:
    mgr = _get_manager()
    return mgr.list_all()


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", default="travel-meta"),
            state=dict(type="str", default="present", choices=["present", "absent", "query"]),
            engines=dict(type="str", default=""),
            query=dict(type="str", required=False),
            max_results=dict(type="int", default=10),
            searxng_url=dict(type="str", default="http://localhost:8080"),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
        required_if=[
            ("state", "query", ("query",)),
        ],
    )

    params = module.params
    name: str = params["name"]
    state: str = params["state"]
    engines_str: str = params["engines"]
    query: str | None = params.get("query")
    max_results: int = params["max_results"]

    try:
        if state == "present":
            engines: list[str] | None = None
            if engines_str:
                engines = [e.strip() for e in engines_str.split(",") if e.strip()]
            result = create_index(name, engines=engines)
            module.exit_json(changed=not result.get("existed", False), **result)

        elif state == "query":
            if not query:
                module.fail_json(msg="query is required when state=query")
            assert query is not None
            results = query_index(name, query, max_results=max_results)
            mgr = _get_manager()
            idx_info = mgr.get(name) if mgr.has(name) else {"engines": TRAVEL_INDEX_ENGINES}
            module.exit_json(
                changed=False,
                name=name,
                state="query",
                engines=idx_info.get("engines", TRAVEL_INDEX_ENGINES),
                query=query,
                results=results,
                result_count=len(results),
            )

        elif state == "absent":
            delete_index(name)
            module.exit_json(changed=True, name=name, state="absent")

    except SearXNGIndexNotFoundError as exc:
        module.fail_json(msg=str(exc))
    except Exception as exc:
        module.fail_json(msg=f"searxng_index failed: {exc}")


if __name__ == "__main__":
    main()
