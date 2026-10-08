#!/usr/bin/python
"""Evaluate one bounded chemical inventory lot without external I/O."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.chemistry.plugins.module_utils.lot_admission import (
    LotAdmissionError,
    evaluate_lot_admission,
)

DOCUMENTATION = r"""
---
module: chemical_lot_admission
short_description: Evaluate one chemical inventory lot locally
description:
  - Rejects expired, restricted, or under-purity chemical lots.
  - Performs the same read-only evaluation in normal and check mode.
  - Never contacts a service, mutates state, or substitutes another lot.
options:
  lot:
    description: Exact declared lot identifier, bounded to 128 characters.
    type: str
    required: true
  purity:
    description: Declared finite purity fraction in the inclusive range 0 through 1.
    type: float
    required: true
  expiry:
    description: Strict ISO expiry date in YYYY-MM-DD form.
    type: str
    required: true
  restrictions:
    description: Up to 32 declared restrictions, each bounded to 128 characters.
    type: list
    elements: str
    default: []
  required_purity:
    description: Required finite purity floor in the inclusive range 0 through 1.
    type: float
    required: true
  as_of:
    description: Strict ISO evaluation date in YYYY-MM-DD form.
    type: str
    required: true
author:
  - Gludd
"""

EXAMPLES = r"""
- name: Admit a declared lot for a reviewed protocol
  general_ludd.chemistry.chemical_lot_admission:
    lot: LOT-2026-0042
    purity: 0.999
    expiry: "2027-06-30"
    restrictions: []
    required_purity: 0.995
    as_of: "2026-10-08"
  register: lot_admission
"""

RETURN = r"""
result:
  description: Bounded verdict containing the declared lot and fixed reason codes.
  returned: success
  type: dict
"""

ARGUMENT_SPEC: dict[str, dict[str, object]] = {
    "lot": {"type": "str", "required": True},
    "purity": {"type": "float", "required": True},
    "expiry": {"type": "str", "required": True},
    "restrictions": {"type": "list", "elements": "str", "default": []},
    "required_purity": {"type": "float", "required": True},
    "as_of": {"type": "str", "required": True},
}


def run(module: Any) -> None:
    """Evaluate module parameters without changing state in either mode."""
    try:
        result = evaluate_lot_admission(
            lot=module.params["lot"],
            purity=module.params["purity"],
            expiry=module.params["expiry"],
            restrictions=module.params["restrictions"],
            required_purity=module.params["required_purity"],
            as_of=module.params["as_of"],
        )
    except LotAdmissionError as exc:
        module.fail_json(changed=False, msg=str(exc))
        return
    module.exit_json(changed=False, result=result)


def main() -> None:
    """Build the narrow Ansible argument contract and evaluate it."""
    module = AnsibleModule(
        argument_spec=ARGUMENT_SPEC,
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
