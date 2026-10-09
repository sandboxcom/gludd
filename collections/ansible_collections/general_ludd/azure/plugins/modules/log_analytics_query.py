#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: log_analytics_query
  short_description: Execute one digest-bound Azure Log Analytics query
  description:
    - Uses azure-monitor-query 2.0.0 on the Ansible controller.
    - Rejects partial, truncated, ambiguous, or oversized results atomically.
    - Selects one exact non-developer-chain Azure credential mode.
  options:
    workspace_id:
      type: str
      required: true
    query:
      type: str
      required: true
    query_sha256:
      type: str
      required: true
    timespan_minutes:
      type: int
      default: 5
    endpoint:
      type: str
      choices: [public, government, china]
      default: public
    credential_mode:
      type: str
      choices: [managed, workload, environment]
      default: managed
    server_timeout_seconds:
      type: int
      default: 15
    managed_identity_client_id:
      type: str
    workload_tenant_id:
      type: str
    workload_client_id:
      type: str
    workload_token_file:
      type: path
    validate_only:
      type: bool
      default: false

EXAMPLES:
  - name: Validate the digest-addressed Log Analytics canary
    general_ludd.azure.log_analytics_query:
      workspace_id: 00000000-0000-4000-8000-000000000001
      query: print gludd_canary=1
      query_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      timespan_minutes: 5
      credential_mode: managed
      validate_only: true

RETURN:
  validated:
    type: bool
    returned: always
  executed:
    type: bool
    returned: always
  query_sha256:
    type: str
    returned: after successful validation
  tables:
    type: list
    returned: after successful execution
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

ARGUMENT_SPEC = {
    "workspace_id": {"type": "str", "required": True},
    "query": {"type": "str", "required": True, "no_log": True},
    "query_sha256": {"type": "str", "required": True},
    "timespan_minutes": {"type": "int", "default": 5},
    "endpoint": {
        "type": "str",
        "choices": ["public", "government", "china"],
        "default": "public",
    },
    "credential_mode": {
        "type": "str",
        "choices": ["managed", "workload", "environment"],
        "default": "managed",
    },
    "server_timeout_seconds": {"type": "int", "default": 15},
    "managed_identity_client_id": {"type": "str", "no_log": True},
    "workload_tenant_id": {"type": "str", "no_log": True},
    "workload_client_id": {"type": "str", "no_log": True},
    "workload_token_file": {"type": "path", "no_log": True},
    "validate_only": {"type": "bool", "default": False},
}


def main() -> None:
    """Fail closed if Ansible bypasses the controller-side action plugin."""
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    module.fail_json(
        msg="log_analytics_query requires its controller-side action plugin"
    )


if __name__ == "__main__":
    main()
