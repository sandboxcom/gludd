#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: infra_destroy
  short_description: Destroy cloud GPU infrastructure deployed via Terraform/OpenTofu
  description:
    - Tears down a previously deployed compute instance through the
      general_ludd DeploymentManager.
    - The caller's role is checked against the per-project infra access
      allowlist before any destruction occurs (default-DENY).
    - Requires the C(instance_id) returned by M(general_ludd.agent.infra_deploy).
    - Refuses to destroy unknown instance IDs (deploy-before-destroy rule).
  options:
    instance_id:
      description: Instance identifier to destroy.
      type: str
      required: true
    role:
      description:
        - Role name checked against the infra access allowlist.
        - Unknown or unlisted roles are denied (default-DENY).
      type: str
      required: true
    provider:
      description: Cloud provider (required for auth env resolution).
      type: str
      required: true
      choices: [aws, azure, gcp, runpod, vast_ai, lambda_labs, modal, coreweave, digital_ocean, oracle, vmware, kubernetes, together_ai, fireworks_ai, huggingface, replicate]
    project_id:
      description: Optional project identifier for credential scoping.
      type: str
  notes:
    - Returns C(changed=true) when terraform destroy removes resources.
    - Check mode reports what-would-be-destroyed without running terraform.

EXAMPLES:
  - name: Destroy a deployment
    general_ludd.agent.infra_destroy:
      instance_id: "i-abc123"
      provider: aws
      role: admin
    register: destroyed

RETURN:
  destroyed:
    description: Whether the instance was destroyed.
    type: bool
    returned: always
  instance_id:
    description: The destroyed instance identifier.
    type: str
    returned: always
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            instance_id=dict(type="str", required=True),
            role=dict(type="str", required=True),
            provider=dict(
                type="str",
                required=True,
                choices=[
                    "aws", "azure", "gcp", "runpod", "vast_ai",
                    "lambda_labs", "modal", "coreweave", "digital_ocean",
                    "oracle", "vmware", "kubernetes", "together_ai",
                    "fireworks_ai", "huggingface", "replicate",
                ],
            ),
            project_id=dict(type="str", default=None),
        ),
        supports_check_mode=True,
    )

    instance_id: str = module.params["instance_id"]
    role: str = module.params["role"]
    provider: str = module.params["provider"]

    # Role allowlist gate (fail-closed).
    try:
        from general_ludd.permissions.infra_access import load_infra_access_policy
        from general_ludd.permissions.infra_access import InfraAccessPolicy
    except ImportError:
        try:
            sys_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
            import sys as _sys
            _sys.path.insert(0, os.path.abspath(os.path.join(sys_path, "..", "..", "..", "src")))
            from general_ludd.permissions.infra_access import (  # type: ignore[no-redef]
                InfraAccessPolicy,
                load_infra_access_policy,
            )
        except ImportError as exc:
            module.fail_json(
                msg=f"general_ludd.permissions.infra_access not importable: {exc}",
                changed=False,
            )
            return

    policy = load_infra_access_policy()
    if not policy.can_destroy(role):
        module.fail_json(
            msg=(
                f"infra destroy denied by access policy: role {role!r} is not in "
                f"allowed_destroy_roles"
            ),
            changed=False,
            role=role,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=False,
            destroyed=False,
            instance_id=instance_id,
            provider=provider,
            msg=f"would destroy instance {instance_id} on {provider} (role={role})",
        )
        return

    try:
        from general_ludd.infra.deployment import DeploymentManager
    except ImportError as exc:
        module.fail_json(
            msg=f"general_ludd.infra.deployment not importable: {exc}",
            changed=False,
        )
        return

    try:
        import asyncio

        manager = DeploymentManager()
        asyncio.run(manager.destroy(instance_id))

        module.exit_json(
            changed=True,
            destroyed=True,
            instance_id=instance_id,
            provider=provider,
        )
    except ValueError as exc:
        module.fail_json(
            msg=f"cannot destroy {instance_id!r}: {exc}",
            changed=False,
            instance_id=instance_id,
        )
    except Exception as exc:
        module.fail_json(
            msg=f"destroy failed: {exc}",
            changed=False,
            instance_id=instance_id,
        )


if __name__ == "__main__":
    main()
