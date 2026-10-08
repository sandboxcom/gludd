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
      choices: [present, absent, list, query]
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
    transport:
      description: Native controller execution or explicit remote rollback.
      type: str
      choices: [native, remote]
      default: native
    remote_url:
      description: Endpoint used only when transport is remote.
      type: str

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

from typing import Any, cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_client import (
    SearchBackend,
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
    return cast(dict[str, Any], mgr.create(name, engines=engines))


def index_exists(name: str) -> bool:
    mgr = _get_manager()
    return cast(bool, mgr.has(name))


def query_index(
    name: str,
    query_text: str,
    max_results: int = 10,
    *,
    search_backend: SearchBackend | None = None,
) -> list[dict[str, Any]]:
    mgr = _get_manager()
    return cast(
        list[dict[str, Any]],
        mgr.query(
            name,
            query_text,
            max_results=max_results,
            search_backend=search_backend,
        ),
    )


def delete_index(name: str) -> dict[str, Any]:
    mgr = _get_manager()
    return cast(dict[str, Any], mgr.delete(name))


def list_indices() -> list[str]:
    mgr = _get_manager()
    return cast(list[str], mgr.list_all())


def main() -> None:
    """Fail closed when Ansible bypasses the controller action plugin."""
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", default="travel-meta"),
            state=dict(
                type="str",
                default="present",
                choices=["present", "absent", "list", "query"],
            ),
            engines=dict(type="str", default=""),
            query=dict(type="str", required=False),
            max_results=dict(type="int", default=10),
            transport=dict(type="str", default="native", choices=["native", "remote"]),
            remote_url=dict(type="str", required=False),
            namespace=dict(type="str", default="gludd-travel"),
            settings_path=dict(type="path", default=""),
            timeout=dict(type="int", default=10),
        ),
        supports_check_mode=True,
        required_if=[
            ("state", "query", ("query",)),
            ("transport", "remote", ("remote_url",)),
        ],
    )
    module.fail_json(msg="searxng_index requires its controller-side action plugin")


if __name__ == "__main__":
    main()
