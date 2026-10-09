#!/usr/bin/python
"""Ansible module stub paired with the controller-side batch action."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: searxng_batch
short_description: Run a bounded SearXNG query batch on the controller
description:
  - Reuses one official SearXNG Python WSGI application for the batch.
  - Native mode opens no listener and starts no child process.
  - Remote HTTP is available only when transport=remote is explicit.
options:
  requests:
    description: One to 32 uniquely identified bounded searches.
    type: list
    elements: dict
    required: true
    suboptions:
      id:
        description: Stable response key.
        type: str
        required: true
      query:
        description: Search text, limited to 2048 characters.
        type: str
        required: true
      categories:
        description: Up to 20 category names.
        type: list
        elements: str
      engines:
        description: Up to 20 engine names.
        type: list
        elements: str
      language:
        description: Bounded language identifier.
        type: str
        default: en
      safe_search:
        description: SearXNG safe-search level.
        type: int
        choices: [0, 1, 2]
        default: 0
      page:
        description: Result page from 1 through 100.
        type: int
        default: 1
      time_range:
        description: Optional SearXNG time range.
        type: str
        choices: [day, week, month, year]
      max_results:
        description: Per-query result ceiling from 1 through 100.
        type: int
        default: 10
  transport:
    description: Native primary transport or explicit remote rollback.
    type: str
    choices: [native, remote]
    default: native
  remote_url:
    description: Explicit remote rollback endpoint.
    type: str
  namespace:
    description: Native project resource namespace.
    type: str
    default: gludd-searx-consumers
  settings_path:
    description: Optional controller-local SearXNG settings path.
    type: path
  timeout:
    description: Explicit remote transport timeout.
    type: float
    default: 30
author:
  - Agentic Harness Agent
"""

EXAMPLES = r"""
- name: Run entity research searches without a listener
  general_ludd.travel.searxng_batch:
    requests:
      - id: company_news
        query: Example Corp acquisition news
        categories: [news]
      - id: company_risk
        query: Example Corp security incident
        categories: [news, general]
"""

RETURN = r"""
responses:
  description: URI-compatible response mappings keyed by request id.
  type: dict
  returned: always
query_count:
  description: Number of accepted request ids.
  type: int
  returned: always
result_count:
  description: Aggregate returned result count, never above 500.
  type: int
  returned: always
"""


def main() -> None:
    """Fail closed when Ansible bypasses the paired action plugin."""
    module = AnsibleModule(
        argument_spec={
            "requests": {
                "type": "list",
                "elements": "dict",
                "required": True,
                "options": {
                    "id": {"type": "str", "required": True},
                    "query": {"type": "str", "required": True},
                    "categories": {"type": "list", "elements": "str"},
                    "engines": {"type": "list", "elements": "str"},
                    "language": {"type": "str", "default": "en"},
                    "safe_search": {
                        "type": "int",
                        "default": 0,
                        "choices": [0, 1, 2],
                    },
                    "page": {"type": "int", "default": 1},
                    "time_range": {
                        "type": "str",
                        "choices": ["day", "week", "month", "year"],
                    },
                    "max_results": {"type": "int", "default": 10},
                },
            },
            "transport": {
                "type": "str",
                "default": "native",
                "choices": ["native", "remote"],
            },
            "remote_url": {"type": "str"},
            "namespace": {"type": "str", "default": "gludd-searx-consumers"},
            "settings_path": {"type": "path", "default": ""},
            "timeout": {"type": "float", "default": 30},
        },
        required_if=[("transport", "remote", ("remote_url",))],
        supports_check_mode=True,
    )
    module.fail_json(msg="searxng_batch requires its controller-side action plugin")


if __name__ == "__main__":
    main()
