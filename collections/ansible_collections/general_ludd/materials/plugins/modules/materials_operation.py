#!/usr/bin/python
"""Run a typed materials operation through the authenticated Gludd daemon."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    run_typed_daemon_operation,
    typed_operation_argument_spec,
)

_OPERATIONS = (
    "requirements_capture",
    "material_select",
    "polymer_process_plan",
    "metal_forming_plan",
    "strength_assess",
    "joining_plan",
    "welding_plan",
    "machining_plan",
    "additive_plan",
    "textile_plan",
    "molding_plan",
    "multiphysics_model",
    "tolerance_model",
    "failure_analyze",
    "manufacturing_plan",
    "inspection_plan",
)


def run(module: Any) -> None:
    """Execute one materials operation using a bounded daemon request."""
    operation: str = module.params["operation"]
    request: dict[str, Any] = module.params["request"]
    run_typed_daemon_operation(
        module,
        namespace="materials",
        endpoint="/api/materials/resolve",
        client_factory=GluddClient,
        planned_result={
            "planned": True,
            "operation": operation,
            "request": request,
        },
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
