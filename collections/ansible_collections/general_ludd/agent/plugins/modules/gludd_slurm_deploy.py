#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_slurm_deploy
  short_description: Deploy a vLLM or llama.cpp model server on a Slurm cluster
  description:
    - Submits a Slurm batch job that launches C(vllm serve) or
      C(llama_cpp.server) on an allocated GPU node and polls until the server
      is servable (writes a servable.json artifact with servable_url).
    - Wraps C(general_ludd.infra.slurm_deployment.VllmSlurmDeployment) and
      C(LlamacppSlurmDeployment).
    - Use this when the operator has a Slurm cluster and Slurm should
      arbitrate GPU access (fairshare + accounting). For cloud GPU, use
      Terraform; for local dev, use C(make local-model-vllm).
  options:
    engine:
      description: Which model server to deploy.
      type: str
      required: true
      choices: [vllm, llamacpp]
    model_id:
      description: Model id (HuggingFace id or path to .gguf for llamacpp).
      type: str
      required: true
    gpu_count:
      description: Number of GPUs to request per node.
      type: int
      default: 1
    gpu_type:
      description: GPU type Slurm should allocate (e.g. a100, h100).
      type: str
      default: a100
    port:
      description: Port the model server binds on the compute node.
      type: int
      default: 8000
    max_hours:
      description: Job walltime in hours.
      type: int
      default: 4
    mem_gb:
      description: Host memory to request (GB).
      type: int
      default: 32
    partition:
      description: Slurm partition to submit to.
      type: str
      default: gpu
    max_ctx:
      description: Maximum model context length.
      type: int
      default: 32768
    artifact_dir:
      description: >
        Shared-filesystem directory the batch script writes servable.json to.
        Must be readable from the controller host.
      type: str
      required: true
    poll_timeout:
      description: Seconds to wait for the server to become servable.
      type: int
      default: 300
    poll_interval:
      description: Seconds between polls of the artifact file.
      type: float
      default: 5.0
    module_loads:
      description: List of environment modules to load on the compute node.
      type: list
      elements: str
      default: []
    extra_args:
      description: Extra argv tokens to pass to vllm serve / llama_cpp.server.
      type: list
      elements: str
      default: []
  notes:
    - Returns C(changed=true) when a job was submitted, C(false) in check mode.
    - C(servable_url) is empty until the artifact file appears with a non-null
      URL; on failure, C(error) carries the diagnostics.

EXAMPLES:
  - name: Deploy vLLM on Slurm
    general_ludd.agent.gludd_slurm_deploy:
      engine: vllm
      model_id: meta-llama/Llama-3.1-8B-Instruct
      gpu_count: 2
      gpu_type: a100
      port: 8000
      max_hours: 4
      artifact_dir: /scratch/gludd/job-{{ job_id }}
    register: deploy

  - name: Wait for servable
    ansible.builtin.assert:
      that: deploy.servable_url | length > 0

RETURN:
  job_id:
    description: Submitted Slurm job id (empty in check mode).
    type: str
    returned: always
  servable_url:
    description: URL the model server is reachable at (empty on timeout/failure).
    type: str
    returned: always
  engine:
    description: Engine that was deployed.
    type: str
    returned: always
  model_id:
    description: Model id that was deployed.
    type: str
    returned: always
  error:
    description: Diagnostics on failure (empty on success).
    type: str
    returned: always
"""

from __future__ import annotations

import hashlib
import json
import os

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import GluddClient


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            engine=dict(type="str", required=True, choices=["vllm", "llamacpp"]),
            model_id=dict(type="str", required=True),
            gpu_count=dict(type="int", default=1),
            gpu_type=dict(type="str", default="a100"),
            port=dict(type="int", default=8000),
            max_hours=dict(type="int", default=4),
            mem_gb=dict(type="int", default=32),
            partition=dict(type="str", default="gpu"),
            max_ctx=dict(type="int", default=32768),
            artifact_dir=dict(type="str", required=True),
            poll_timeout=dict(type="int", default=300),
            poll_interval=dict(type="float", default=5.0),
            module_loads=dict(type="list", elements="str", default=[]),
            extra_args=dict(type="list", elements="str", default=[]),
            idempotency_key=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    engine: str = module.params["engine"]
    model_id: str = module.params["model_id"]
    gpu_count: int = module.params["gpu_count"]
    gpu_type: str = module.params["gpu_type"]
    port: int = module.params["port"]
    max_hours: int = module.params["max_hours"]
    mem_gb: int = module.params["mem_gb"]
    partition: str = module.params["partition"]
    max_ctx: int = module.params["max_ctx"]
    artifact_dir = os.path.abspath(module.params["artifact_dir"])
    poll_timeout: int = module.params["poll_timeout"]
    poll_interval: float = module.params["poll_interval"]
    module_loads: list[str] = module.params["module_loads"]
    extra_args: list[str] = module.params["extra_args"]

    if module.check_mode:
        module.exit_json(
            changed=False,
            job_id="",
            servable_url="",
            engine=engine,
            model_id=model_id,
            error="",
            msg=f"would submit {engine} slurm deployment for {model_id}",
        )
        return

    request_body = {
        "engine": engine,
        "model_id": model_id,
        "gpu_count": gpu_count,
        "gpu_type": gpu_type,
        "port": port,
        "max_hours": max_hours,
        "mem_gb": mem_gb,
        "partition": partition,
        "max_ctx": max_ctx,
        "artifact_dir": artifact_dir,
        "poll_timeout": poll_timeout,
        "poll_interval": poll_interval,
        "module_loads": module_loads,
        "extra_args": extra_args,
    }
    body_digest = hashlib.sha256(
        json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    request_body["idempotency_key"] = (
        module.params["idempotency_key"] or f"slurm-deploy:{body_digest}"
    )
    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=max(30, poll_timeout + 30),
    ).post(
        "/admin/slurm/deploy",
        request_body,
    )
    if response.get("_error") or response.get("_status") not in {200, 201}:
        detail = str(response.get("detail") or response.get("_error") or "daemon deployment failed")
        module.fail_json(
            msg=f"submit failed: {detail}",
            changed=False,
            engine=engine,
            model_id=model_id,
            error=detail,
        )
        return

    module.exit_json(
        changed=True,
        job_id=response.get("job_id", ""),
        servable_url=response.get("servable_url", ""),
        engine=engine,
        model_id=model_id,
        error=response.get("error", ""),
    )


if __name__ == "__main__":
    main()
