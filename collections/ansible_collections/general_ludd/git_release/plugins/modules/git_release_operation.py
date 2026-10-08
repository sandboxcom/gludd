#!/usr/bin/python
"""Run a typed git-release operation through the authenticated Gludd daemon."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    run_typed_daemon_operation,
    typed_operation_argument_spec,
)

_OPERATION_PHASES = {
    "artifact_build": "provenance",
    "artifact_verify": "verification",
    "conflict_resolve": "proposal",
    "deploy_orchestrate": "zdd-decision",
    "helper_build": "helper-plan",
    "helper_discover": "helper-discovery",
    "helper_select": "helper-selection",
    "pipeline_triage": "proposal",
    "release_plan": "proposal",
    "release_recover": "proposal",
    "work_recover": "proposal",
}
_OPERATIONS = tuple(_OPERATION_PHASES)


def run(module: Any) -> None:
    """Execute one git-release operation using a bounded daemon request."""
    operation: str = module.params["operation"]
    request: dict[str, Any] = module.params["request"]
    run_typed_daemon_operation(
        module,
        namespace="git_release",
        endpoint="/api/git_release/resolve",
        client_factory=GluddClient,
        planned_result={
            "planned": True,
            "operation": operation,
            "phase": _OPERATION_PHASES[operation],
            "request": request,
            "mutation_performed": False,
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
