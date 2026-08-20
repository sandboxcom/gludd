#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: infra_deploy
  short_description: Deploy cloud GPU infrastructure via Terraform/OpenTofu
  description:
    - Submits an infrastructure deployment request through the general_ludd
      DeploymentManager, which provisions GPU compute via Terraform/OpenTofu.
    - The caller's role is checked against the per-project infra access
      allowlist before any provisioning occurs.
    - Returns the instance id and endpoint URL on success.
    - Use M(general_ludd.agent.infra_destroy) to tear down the deployment.
  options:
    stack:
      description: Terraform stack name to deploy.
      type: str
      required: true
    provider:
      description: Cloud provider to provision on.
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
    engine:
      description: Model server engine to deploy.
      type: str
      required: true
      choices: [vllm, llamacpp]
    config:
      description: Dict of additional terraform and deployment variables.
      type: dict
      default: {}
    workload_type:
      description: Type of workload (inference, training).
      type: str
      default: inference
    role:
      description:
        - Role name checked against the infra access allowlist.
        - Unknown or unlisted roles are denied (default-DENY).
      type: str
      required: true
    gpu_type:
      description: GPU model to request.
      type: str
      default: a100_80
    gpu_count:
      description: Number of GPUs to request.
      type: int
      default: 1
    model_name:
      description: Model identifier (HuggingFace id).
      type: str
      default: ""
    region:
      description: Cloud provider region.
      type: str
    spot:
      description: Whether to request spot/preemptible instances.
      type: bool
      default: true
    max_cost_usd:
      description: Maximum cost per hour in USD.
      type: float
      default: 10.0
    timeout_minutes:
      description: Deployment timeout in minutes.
      type: float
      default: 60.0
    project_id:
      description: Optional project identifier for credential scoping.
      type: str
  notes:
    - Returns C(changed=true) when terraform apply creates resources.
    - Check mode returns what-would-be-deployed without provisioning.
    - The role allowlist is configured per-project; only roles listed in
      C(allowed_infra_roles) may deploy.

EXAMPLES:
  - name: Deploy vLLM on AWS with A100
    general_ludd.agent.infra_deploy:
      stack: gpu-inference
      provider: aws
      engine: vllm
      gpu_type: a100_80
      gpu_count: 1
      model_name: meta-llama/Llama-3.1-8B-Instruct
      role: admin
    register: deployment

  - name: Use endpoint
    ansible.builtin.debug:
      msg: "Model is ready at {{ deployment.endpoint_url }}"

RETURN:
  instance_id:
    description: Provisioned compute instance identifier.
    type: str
    returned: on success
  endpoint_url:
    description: URL the model server is reachable at.
    type: str
    returned: on success
  engine:
    description: Engine that was deployed.
    type: str
    returned: always
  provider:
    description: Cloud provider used.
    type: str
    returned: always
  stack:
    description: Terraform stack name.
    type: str
    returned: always
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            stack=dict(type="str", required=True),
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
            engine=dict(type="str", required=True, choices=["vllm", "llamacpp"]),
            config=dict(type="dict", default={}),
            workload_type=dict(type="str", default="inference"),
            role=dict(type="str", required=True),
            gpu_type=dict(type="str", default="a100_80"),
            gpu_count=dict(type="int", default=1),
            model_name=dict(type="str", default=""),
            region=dict(type="str", default=None),
            spot=dict(type="bool", default=True),
            max_cost_usd=dict(type="float", default=10.0),
            timeout_minutes=dict(type="float", default=60.0),
            project_id=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    stack: str = module.params["stack"]
    provider: str = module.params["provider"]
    engine: str = module.params["engine"]
    config: dict[str, Any] = module.params["config"]
    workload_type: str = module.params["workload_type"]
    role: str = module.params["role"]
    gpu_type: str = module.params["gpu_type"]
    gpu_count: int = module.params["gpu_count"]
    model_name: str = module.params["model_name"]
    region: str | None = module.params["region"]
    spot: bool = module.params["spot"]
    max_cost_usd: float = module.params["max_cost_usd"]
    timeout_minutes: float = module.params["timeout_minutes"]
    project_id: str | None = module.params["project_id"]

    if module.check_mode:
        module.exit_json(
            changed=False,
            instance_id="",
            endpoint_url="",
            engine=engine,
            provider=provider,
            stack=stack,
            msg=f"would deploy {stack} on {provider} with {engine} (role={role}, workload={workload_type})",
        )
        return

    body = dict(config)
    body.update(
        {
            "provider": provider,
            "engine": engine,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "model_name": model_name,
            "region": region,
            "spot": spot,
            "max_cost_usd": max_cost_usd,
            "timeout_minutes": timeout_minutes,
            "project_id": project_id,
            "role": role,
            "workload_type": workload_type,
            "stack": stack,
        }
    )
    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=max(30, int(timeout_minutes * 60) + 30),
    ).post("/admin/compute/deploy", body)
    if response.get("_error") or response.get("_status") not in {200, 201}:
        module.fail_json(
            msg=f"deployment failed: {response.get('detail') or response.get('_error') or 'daemon rejected request'}",
            changed=False,
            engine=engine,
            provider=provider,
            stack=stack,
        )
        return
    module.exit_json(
        changed=True,
        instance_id=response.get("instance_id", ""),
        endpoint_url=response.get("endpoint_url", ""),
        engine=engine,
        provider=provider,
        stack=stack,
        gpu_type=response.get("gpu_type", gpu_type),
    )


if __name__ == "__main__":
    main()
