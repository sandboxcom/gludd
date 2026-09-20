# Universal model-worker lifecycle

## Scope

`general_ludd.infra.model_worker_lifecycle` owns the provider-neutral transition
from selected model topology to a dispatchable model service. It is not a
self-improvement subsystem. Chemistry, firmware, application development,
self-improvement, and every other authorized task use the same model-worker
contract.

The lifecycle composes three independently replaceable adapters:

1. an infrastructure runtime provisions exact owned hosts and later destroys
   them;
2. a configuration runtime attests hardware, driver, runtime, topology, model
   runner, and health before returning endpoints; and
3. a dispatcher publishes only those attested endpoints and drains them before
   retirement.

Azure VM and VMSS, local Linux, Slurm, and future accelerator providers can
implement the infrastructure boundary without changing task or model code. The
selected `RunnerLaunchPlan` already carries right-sized vLLM, Ollama, llama.cpp,
or compatible future-runner arguments, including replica and devices-per-replica
counts.

The Azure implementation is concrete rather than a routing placeholder:

- `azure_gpu_worker_materializer` copies the reviewed single-VM or VMSS module
  into an owned OpenTofu root and writes only public desired state;
- `azure_gpu_worker_runtime` performs init, validation, exact saved-plan audit,
  apply, bounded output parsing, compensation, destroy, and absence proof with a
  fresh OpenBao credential lease per cloud phase;
- `azure_gpu_worker_sdk` resolves current VMSS private addresses and independently
  enumerates the exact owned ARM IDs through maintained Azure SDKs; and
- `ansible_model_worker_runtime` creates a mode-0600 ephemeral inventory, runs the
  serial deploy or retire playbook, and accepts endpoints only from exact
  content-free attestation facts.

## Owned state machine

The success path is strictly ordered:

`provision -> attest/configure -> health-ready -> publish -> work`

Closing an owned pool runs the reverse ownership path:

`drain -> retire service -> destroy infrastructure -> verify absence`

An acquisition failure compensates only resources that were actually acquired.
A configuration failure never publishes an endpoint. A publication failure
retires the unrouted candidate and destroys its infrastructure. Cleanup attempts
every remaining owner even when drain or retirement fails, and the lifecycle is
not clean until an independent provider read proves that all exact owned
resources are absent.

Every progress record contains only a typed event, a desired-state digest, a
worker count, and an optional stable reason code. It excludes prompts, model
names, endpoint addresses, resource IDs, credential paths, provider errors, and
dispatch leases.

## ZDD and rollback

The existing healthy route remains unchanged while a replacement is provisioned
and attested. The dispatcher receives the replacement only after all hosts have
passed the same immutable runner and topology contract. Rolling remote
configuration is serial, so a multi-host pool never replaces every generation
at once. Traffic is drained before the explicit prior service generation is
retired. If any replacement phase fails, the candidate is removed and the old
route remains available.

Infrastructure credentials are outside the launch plan and Ansible facts.
Provider adapters obtain short-lived component credentials from OpenBao, while
the model-worker role receives only non-secret desired state and a local private
key path for the controller's SSH transport. The private key contents never
enter OpenTofu state, lifecycle traces, or model-runner environment files.

## Long-lived operator reports incorporated

- A vLLM operator reported a two-GPU tensor-parallel process hanging after NCCL
  initialization while the same setup served correctly on one GPU. Process
  liveness therefore never qualifies a candidate; topology attestation and the
  bounded health endpoint must both pass before publication:
  [vLLM issue 33041](https://github.com/vllm-project/vllm/issues/33041).
- Ollama users reported layers distributed across two GPUs while memory use
  remained materially uneven. The lifecycle retains the planner's exact
  devices-per-replica contract and requires post-launch telemetry rather than
  inferring balance from command arguments:
  [Ollama issue 7047](https://github.com/ollama/ollama/issues/7047).
- Azure VMSS Custom Script Extension bootstrap has timed out in a partially
  enabled state when scripts, egress, or driver setup stalled. Gludd keeps guest
  bootstrap in observable, bounded Ansible phases and does not use Custom Script
  or RunCommand for the worker:
  [Microsoft Q&A 5692668](https://learn.microsoft.com/en-au/answers/questions/5692668/how-to-fix-provisioning-of-vm-extension-vmsscse-ha).
- AzureRM users reported destroy/state races around dependent networking. A
  successful OpenTofu destroy exit is therefore necessary but insufficient;
  the provider adapter must read back every recorded owned resource and prove
  absence:
  [AzureRM issue 23488](https://github.com/hashicorp/terraform-provider-azurerm/issues/23488).

These reports are regression inputs, not provider guarantees. Gludd still uses
maintained provider documentation and live inventory as the authoritative
capability source.

## Verification

`tests/unit/test_model_worker_lifecycle.py` covers success, idempotent close,
configuration and publication compensation, cleanup failure, exact endpoint
admission, validation, and content-free traces. The Azure materializer, SDK,
infrastructure runtime, and Ansible configuration adapter add 166 focused tests;
the combined affected lifecycle replay passes 189 tests. Every focused production
file exceeds both the 85% aggregate and 75% per-file release floors.
