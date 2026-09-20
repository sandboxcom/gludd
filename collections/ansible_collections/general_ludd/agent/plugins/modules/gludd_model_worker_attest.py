#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_model_worker_attest
  short_description: Attest a model worker's accelerator topology and runtime
  description:
    - Uses Gludd's provider-neutral model-worker attestation engine.
    - Observes maintained NVIDIA NVML or AMD SMI interfaces on the managed host.
    - Supports additional accelerator backends through the engine probe registry.
    - Returns only content-free digests, readiness booleans, and refusal codes.
    - Performs no provisioning and is safe in check mode.
  options:
    runner_id:
      description: Attested model-runner profile identifier.
      type: str
      required: true
    source_revision:
      description: Immutable observed runner-profile revision.
      type: str
      required: true
    backend:
      description: Accelerator probe backend identifier.
      type: str
      required: true
    minimum_device_count:
      description: Minimum devices visible to the candidate service.
      type: int
      required: true
    minimum_memory_mib:
      description: Minimum memory required on every visible device.
      type: int
      required: true
    required_interconnect:
      description: Required interconnect class or C(any).
      type: str
      required: true
    expected_topology_digest:
      description: Exact SHA-256 digest of the selected host topology.
      type: str
      required: true
    runtime_probe:
      description: Tokenized, read-only runner version probe.
      type: list
      elements: str
      required: true
    expected_runtime_version_digest:
      description: Expected content-free digest of the runtime probe output.
      type: str
      required: true
    timeout_seconds:
      description: Bounded runner probe timeout.
      type: int
      default: 15

EXAMPLES:
  - name: Attest a two-device NVIDIA vLLM worker
    general_ludd.agent.gludd_model_worker_attest:
      runner_id: vllm-observed
      source_revision: vllm-profile-v1
      backend: nvidia
      minimum_device_count: 2
      minimum_memory_mib: 80000
      required_interconnect: nvlink
      expected_topology_digest: "{{ selected_topology_digest }}"
      runtime_probe: [vllm, --version]
      expected_runtime_version_digest: "{{ selected_runtime_version_digest }}"

RETURN:
  ansible_facts:
    description: Content-free model-worker attestation evidence.
    type: dict
    returned: always
"""

from __future__ import annotations

from typing import cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.model_worker_attestation import (
    ModelWorkerAttestationRequest,
    ModelWorkerAttestor,
)


def main() -> None:
    """Run one read-only guest attestation and publish it as an Ansible fact."""
    module = AnsibleModule(
        argument_spec=dict(
            runner_id=dict(type="str", required=True),
            source_revision=dict(type="str", required=True),
            backend=dict(type="str", required=True),
            minimum_device_count=dict(type="int", required=True),
            minimum_memory_mib=dict(type="int", required=True),
            required_interconnect=dict(type="str", required=True),
            expected_topology_digest=dict(type="str", required=True),
            runtime_probe=dict(type="list", elements="str", required=True, no_log=True),
            expected_runtime_version_digest=dict(type="str", required=True),
            timeout_seconds=dict(type="int", default=15),
        ),
        supports_check_mode=True,
    )
    try:
        request = ModelWorkerAttestationRequest(
            runner_id=cast(str, module.params["runner_id"]),
            source_revision=cast(str, module.params["source_revision"]),
            backend=cast(str, module.params["backend"]),
            minimum_device_count=cast(int, module.params["minimum_device_count"]),
            minimum_memory_mib=cast(int, module.params["minimum_memory_mib"]),
            required_interconnect=cast(str, module.params["required_interconnect"]),
            expected_topology_digest=cast(
                str,
                module.params["expected_topology_digest"],
            ),
            runtime_probe=tuple(cast(list[str], module.params["runtime_probe"])),
            expected_runtime_version_digest=cast(
                str,
                module.params["expected_runtime_version_digest"],
            ),
            timeout_seconds=cast(int, module.params["timeout_seconds"]),
        )
        result = ModelWorkerAttestor().attest(request)
    except (TypeError, ValueError) as exc:
        module.fail_json(
            msg=f"model worker attestation failed: {exc}",
            changed=False,
        )
        return
    module.exit_json(
        changed=False,
        ansible_facts={"gludd_model_worker_attestation": result.to_dict()},
    )


if __name__ == "__main__":
    main()
