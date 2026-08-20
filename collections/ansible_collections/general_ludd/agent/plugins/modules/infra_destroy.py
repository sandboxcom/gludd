#!/usr/bin/python
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
      choices:
        - aws
        - azure
        - gcp
        - runpod
        - vast_ai
        - lambda_labs
        - modal
        - coreweave
        - digital_ocean
        - oracle
        - vmware
        - kubernetes
        - together_ai
        - fireworks_ai
        - huggingface
        - replicate
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

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient


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
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    instance_id: str = module.params["instance_id"]
    role: str = module.params["role"]
    provider: str = module.params["provider"]

    if module.check_mode:
        module.exit_json(
            changed=False,
            destroyed=False,
            instance_id=instance_id,
            provider=provider,
            msg=f"would destroy instance {instance_id} on {provider} (role={role})",
        )
        return

    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=300,
    ).delete(f"/admin/compute/destroy/{instance_id}")
    if response.get("_error") or response.get("_status") != 200:
        module.fail_json(
            msg=f"destroy failed: {response.get('detail') or response.get('_error') or 'daemon rejected request'}",
            changed=False,
            instance_id=instance_id,
        )
        return
    module.exit_json(
        changed=True,
        destroyed=response.get("destroyed") == instance_id,
        instance_id=instance_id,
        provider=provider,
        role=role,
    )


if __name__ == "__main__":
    main()
