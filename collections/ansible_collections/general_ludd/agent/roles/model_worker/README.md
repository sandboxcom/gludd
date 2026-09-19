# Provider-neutral model worker role

`general_ludd.agent.model_worker` turns an already selected, immutable
`RunnerLaunchPlan` into one health-attested Linux service. It does not select a
model, infer a GPU vendor from a product name, download packages, mint
credentials, or know which infrastructure provider owns the host. Azure VM and
VMSS workers, local Linux hosts, Slurm nodes, and future accelerator providers
all consume the same role.

This boundary is deliberately broader than self-improvement. Self-improvement
is one task that can call a model worker; chemistry, embedded software,
application development, research, and every other authorized Gludd task use
the same runner contract. Backend-specific model sizing remains in Gludd's
provider-neutral topology planner and its vLLM, Ollama, and llama.cpp launch
profiles. The role executes only the tokenized command that planner emitted.

## Admission contract

The caller supplies two independently produced objects:

- a schema-v1 runner plan containing immutable adapter/source identity, a
  tokenized command, non-secret environment, request options, replica count,
  and devices per replica;
- live guest attestation proving the driver and runtime are ready, the observed
  device count covers the plan, and the source revision and topology digest
  match what was selected.

Environment names resembling credentials are rejected. OpenBao leases and
model-provider credentials use a separate in-memory delegation path and never
enter the systemd unit, environment file, Ansible facts, or attestation record.
The role never installs or upgrades the runner in place; an immutable runtime
must exist before admission.

## ZDD lifecycle

The main task file creates a release-addressed candidate unit, starts it, waits
on a loopback health URL, writes a content-free attestation, and publishes the
candidate fact. It never stops the previous service. The controller switches
traffic only after health and topology evidence are durable, then invokes
`tasks_from: retire` with the exact prior unit. This separation gives ZDD and a
simple rollback: keep routing to the prior healthy unit and retire the failed
candidate.

Systemd supervises the exact command without a shell, restarts bounded failures,
and applies filesystem/kernel hardening while retaining explicit writable state
and log paths. Model identity, command arguments, environment values, request
content, and credentials are absent from the attestation artifact.

## Operator reports incorporated

- vLLM users have observed multi-GPU startup hanging after NCCL initialization
  even though a single-GPU launch works. Gludd therefore does not equate a live
  process with readiness: the selected topology and runtime must be attested and
  the candidate must pass bounded health before publication:
  [vLLM issue 33041](https://github.com/vllm-project/vllm/issues/33041).
- Ollama users have observed layers distributed across two GPUs while memory
  remained materially uneven. Gludd records selected devices per replica and
  requires post-launch telemetry to agree with that topology rather than
  assuming multi-GPU balance from process arguments:
  [Ollama issue 7047](https://github.com/ollama/ollama/issues/7047).
- Azure operators have reported VMSS Custom Script Extension timeout loops when
  bootstrap blocks or egress/driver setup fails. This role runs after
  infrastructure creation over Ansible, emits each phase, and never uses Custom
  Script or RunCommand:
  [Microsoft Q&A 5692668](https://learn.microsoft.com/en-au/answers/questions/5692668/how-to-fix-provisioning-of-vm-extension-vmsscse-ha).
