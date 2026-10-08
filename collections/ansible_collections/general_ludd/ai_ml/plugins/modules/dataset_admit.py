#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: dataset_admit
  short_description: Validate controller-local CSV resources with Frictionless
  description:
    - Validates bounded, root-confined CSV resources against one JSON Table Schema.
    - Runs only through the controller action plugin and never transfers dataset bytes.
    - Returns a deterministic SHA-256-bound data card for an admitted dataset.
  options:
    root:
      description: Absolute controller directory that confines every input file.
      type: path
      required: true
    resources:
      description: Relative or root-confined absolute CSV resource paths.
      type: list
      elements: str
      required: true
    schema:
      description: Relative or root-confined absolute JSON Table Schema path.
      type: path
      required: true
    name:
      description: Stable dataset name written to the data card.
      type: str
      default: ""
    description:
      description: Dataset description written to the data card.
      type: str
      default: ""
    license:
      description: Dataset license identifier written to the data card.
      type: str
      default: ""

EXAMPLES:
  - name: Admit a controller-local training dataset
    general_ludd.ai_ml.dataset_admit:
      root: /srv/gludd/datasets/customer-classes
      resources:
        - train.csv
        - evaluation.csv
      schema: schema.json
      name: customer-classes
      license: CC-BY-4.0
    register: dataset_admission

RETURN:
  admitted:
    description: Whether every resource passed validation.
    type: bool
    returned: always
  data_card:
    description: Stable metadata bound to schema and resource SHA-256 digests.
    type: dict
    returned: success
  data_card_sha256:
    description: SHA-256 of the canonical JSON data card.
    type: str
    returned: success
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def main() -> None:
    """Fail closed when Ansible bypasses the controller-side action plugin."""
    module = AnsibleModule(
        argument_spec={
            "root": {"type": "path", "required": True},
            "resources": {"type": "list", "elements": "str", "required": True},
            "schema": {"type": "path", "required": True},
            "name": {"type": "str", "default": ""},
            "description": {"type": "str", "default": ""},
            "license": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    module.fail_json(msg="dataset_admit requires its controller-side action plugin")


if __name__ == "__main__":
    main()
