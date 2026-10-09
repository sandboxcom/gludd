#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: openapi_query
  short_description: Execute one digest-bound read-only OpenAPI operation
  description:
    - Validates one root-confined OpenAPI 3.1 file on the controller.
    - Executes only GET or HEAD through the DNS-pinned Gludd HTTPS transport.
    - Validates the bounded response against the selected OpenAPI operation.
  options:
    root:
      type: path
      required: true
    spec_path:
      type: path
      required: true
    spec_sha256:
      type: str
      required: true
    base_url:
      type: str
      required: true
    allowed_host:
      type: str
      required: true
    operation_id:
      type: str
      required: true
    parameters:
      type: dict
      default: {}
    secret_env:
      type: dict
      default: {}
    records_pointer:
      type: str
      default: ''
    timeout_seconds:
      type: float
      default: 15.0
    max_response_bytes:
      type: int
      default: 1048576
    max_records:
      type: int
      default: 100
    validate_only:
      type: bool
      default: false

EXAMPLES:
  - name: Query an admitted service inventory operation
    general_ludd.infrastructure.openapi_query:
      root: /srv/gludd/contracts
      spec_path: inventory.openapi.json
      spec_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      base_url: https://inventory.example.com
      allowed_host: inventory.example.com
      operation_id: listServices
      secret_env:
        Authorization: INVENTORY_API_AUTHORIZATION
      records_pointer: /records

RETURN:
  validated:
    type: bool
    returned: always
  executed:
    type: bool
    returned: always
  records:
    type: list
    returned: successful execution
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

ARGUMENT_SPEC = {
    "root": {"type": "path", "required": True},
    "spec_path": {"type": "path", "required": True},
    "spec_sha256": {"type": "str", "required": True, "no_log": False},
    "base_url": {"type": "str", "required": True},
    "allowed_host": {"type": "str", "required": True},
    "operation_id": {"type": "str", "required": True},
    "parameters": {"type": "dict", "default": {}},
    "secret_env": {"type": "dict", "default": {}},
    "records_pointer": {"type": "str", "default": ""},
    "timeout_seconds": {"type": "float", "default": 15.0},
    "max_response_bytes": {"type": "int", "default": 1024 * 1024},
    "max_records": {"type": "int", "default": 100},
    "validate_only": {"type": "bool", "default": False},
}


def main() -> None:
    """Fail closed if Ansible bypasses the controller action plugin."""
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    module.fail_json(msg="openapi_query requires its controller-side action plugin")


if __name__ == "__main__":
    main()
