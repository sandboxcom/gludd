#!/usr/bin/python
"""Run a typed chemistry operation through the authenticated Gludd daemon."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    run_typed_daemon_operation,
    typed_operation_argument_spec,
)

_OPERATIONS = (
    "route",
    "identity",
    "reaction",
    "molar_mass",
    "moles",
    "dilution",
    "yield",
    "hazard",
)


def run(module: Any) -> None:
    """Execute the module contract against one authenticated daemon client."""
    run_typed_daemon_operation(
        module,
        namespace="chemistry",
        endpoint="/api/chemistry/resolve",
        client_factory=GluddClient,
    )


def main() -> None:
    """Create the Ansible argument contract and execute it."""
    module = AnsibleModule(
        argument_spec=typed_operation_argument_spec(_OPERATIONS),
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
