# beta.3 Azure Accelerator Release Scope

## Release decision

`v0.1.0-beta.3` includes the completed Azure accelerator work already present
on `master` or `development`, plus the release-closure wiring required to make
that work usable as one end-to-end path. It does not include unrelated,
unfinished development cleanup.

The supported path is an ephemeral Azure Linux VM running either vLLM or
llama.cpp. Gludd performs a read-only eligibility preflight, creates an exact
accelerator SKU through Terraform/OpenTofu, waits for the NVIDIA driver and
inference health endpoint, registers only the ready endpoint with the
scheduler, and destroys the isolated Terraform state on failure, explicit
request, idle teardown, or hard expiry.

No paid Azure resource was created while validating this release slice.
Provider-aware Terraform initialization and validation were run locally; a
real allocation remains subscription-, quota-, region-, and capacity-dependent.

## Supported accelerator shapes

The mappings are explicit. Gludd does not ask Azure for a generic VM and hope
that it contains the requested GPU.

| Gludd request | Azure VM size | GPUs / memory | vCPUs |
|---|---|---:|---:|
| `a100_40`, 1 GPU | `Standard_NC24ads_A100_v4` | 1 A100 / 80 GB | 24 |
| `a100_80`, 1 GPU | `Standard_NC24ads_A100_v4` | 1 A100 / 80 GB | 24 |
| `a100_80`, 2 GPUs | `Standard_NC48ads_A100_v4` | 2 A100 / 160 GB | 48 |
| `a100_80`, 4 GPUs | `Standard_NC96ads_A100_v4` | 4 A100 / 320 GB | 96 |
| `h100`, 1 GPU | `Standard_NC40ads_H100_v5` | 1 H100 NVL / 94 GB | 40 |
| `h100`, 2 GPUs | `Standard_NC80adis_H100_v5` | 2 H100 NVL / 188 GB | 80 |
| `t4`, 1 GPU | `Standard_NC4as_T4_v3` | 1 T4 / 16 GB | 4 |

An `a100_40` request is deliberately promoted to Azure's 80-GB A100 rather
than under-provisioned. Unsupported GPU types or counts fail before any
Terraform operation.

The shape contract follows Microsoft's
[NC A100 v4 specification](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series),
[NCads H100 v5 specification](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series),
and [NC family documentation](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family).
Microsoft notes that net-new NC capacity is focused on H100 v5, so A100
eligibility does not imply plentiful A100 capacity.

## What is included

### Preflight before spend

`general_ludd.infra.azure_accelerator` performs only:

- `ComputeManagementClient.resource_skus.list`
- `ComputeManagementClient.usage.list`

It verifies all three conditions independently:

1. the exact VM SKU is visible and unrestricted in the requested region;
2. the accelerator VM-family vCPU quota has enough remaining cores; and
3. the total regional vCPU quota has enough remaining cores.

Missing quota records fail closed. The deploy API repeats this preflight
immediately before provisioning and returns HTTP 409 without calling Terraform
when a blocker exists. Azure documents the two-tier quota model and the
separate physical-capacity decision in
[Check vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).

### Authentication and least privilege

The onboarding module creates `General Ludd Accelerator Deployer`, a custom
subscription-scoped role containing the read, create, monitor, and delete
actions used by this path:

- resource groups;
- SKU, location, and regional/family usage reads;
- virtual networks, subnets, security groups, public IPs, and NICs;
- VMs, disks, instance view, and lifecycle actions; and
- VM extension read/write/delete for the NVIDIA driver and bootstrap.

It does not grant `Owner`, `Contributor`, or role-assignment write/delete.
The same role can bind either to the module's user-assigned managed identity
or an existing app/service-principal object ID. The OPA contract rejects a
missing assignment scope and the unit contract keeps the Terraform definition
and `config/infra/azure-iam-policy.json` aligned.

Gludd accepts standard `AZURE_*` SDK variables. For Terraform it translates
them into an isolated subprocess-only `ARM_*` environment; it never mutates
the daemon's global environment or persists secret values. When a client ID is
present without a client secret, it enables AzureRM managed-identity mode.

### Real Terraform stacks

Both `infra/terraform/stacks/azure-vllm` and
`infra/terraform/stacks/azure-llamacpp` now create real Azure resources:

- isolated resource group, VNet, subnet, security group, public IP, and NIC;
- Gen2 Ubuntu Linux VM with Premium storage and the exact resolved GPU size;
- optional Spot priority;
- `Microsoft.HpcCompute` / `NvidiaGpuDriverLinux` extension version `1.6`;
- a dependent bootstrap extension that installs Docker and NVIDIA Container
  Toolkit, configures the NVIDIA runtime, and starts the selected engine as a
  restarting systemd service;
- an `nvidia-smi` hardware marker and one-minute GPU telemetry timer; and
- local `/health` polling before Terraform reports the bootstrap complete.

The extension identity and version match Microsoft's
[NVIDIA GPU Driver Extension](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/hpccompute-gpu-linux).
Bootstrap evidence is written to:

- `/var/log/gludd/accelerator-bootstrap.log`
- `/var/log/gludd/gpu-metrics.jsonl`
- `/var/lib/gludd/accelerator-ready.csv`
- `/var/lib/gludd/inference-ready`

The source Terraform modules are included in both wheel/sdist packaging and
the PyInstaller bundle; installed gludd artifacts do not depend on a source
checkout to find the stacks.

### Ready endpoint routing and workload controls

After Terraform and bootstrap succeed, the daemon registers the endpoint,
model, GPU type/count, and concurrency with `UtilizationTracker`. It is not
advertised to scheduler traffic before readiness.

The launch CLI exposes the controls required for a non-demo workload:

- model and vLLM/llama.cpp engine;
- accelerator type and exact count;
- region, Spot/on-demand, disk size, and optional container image;
- hard lifetime, maximum cost, and a price-sheet hourly rate;
- workload profile and scheduler concurrency; and
- SSH key path and ingress CIDR.

The default ingress is `127.0.0.1/32` because the engine's HTTP endpoint is not
an authentication boundary. Operators must supply their actual trusted CIDR
or put an authenticated gateway/private network in front of the worker. Do
not use `0.0.0.0/0` for an unprotected inference server.

## Operator workflow

### 1. Provision authorization

Use the module instructions emitted by `gludd onboard azure`. For a service
principal, obtain its **object ID**, then pass it as
`operator_principal_id`; the application/client ID is not interchangeable
with the object ID.

For a service principal, export:

```bash
export AZURE_SUBSCRIPTION_ID="<subscription>"
export AZURE_TENANT_ID="<tenant>"
export AZURE_CLIENT_ID="<application-client-id>"
export AZURE_CLIENT_SECRET="<secret-from-a-secret-manager>"
```

For gludd running inside Azure with the assigned user identity, export the
subscription and managed-identity client ID; omit the client secret.

### 2. Resolve locally without credentials or cloud calls

```bash
make azure-harness LIVE=0
```

Optional harness inputs are `AZURE_GPU_TYPE`, `AZURE_GPU_COUNT`, and
`AZURE_LOCATION`.

### 3. Run the live read-only eligibility check

```bash
make azure-harness LIVE=1
gludd compute azure-preflight \
  --gpu a100_80 \
  --gpu-count 1 \
  --region eastus
```

`LIVE=1` requires `AZURE_SUBSCRIPTION_ID` and uses
`DefaultAzureCredential`, so a service principal, managed identity, Azure CLI
login, or another supported SDK credential can satisfy authentication. It
does not invoke Terraform or create/delete a resource.

### 4. Launch a bounded worker

Start the gludd daemon, then choose a trusted ingress CIDR and a real hourly
rate from the subscription's price sheet:

```bash
gludd compute launch \
  --provider azure \
  --gpu a100_80 \
  --gpu-count 1 \
  --engine vllm \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --region eastus \
  --disk-size-gb 512 \
  --timeout-minutes 240 \
  --max-cost 20 \
  --hourly-rate "<USD_PER_HOUR>" \
  --allowed-cidr "<TRUSTED_PUBLIC_IP>/32" \
  --ssh-public-key-path ~/.ssh/id_ed25519.pub \
  --max-concurrent 8
```

Use `--no-spot` when eviction risk is unacceptable. An optional
`--container-image` selects an operator-controlled serving image.

Destroy explicitly when work finishes:

```bash
gludd compute destroy "<deployment-id>"
```

## ZDD and money-leak behavior

This path uses the following zero-downtime deployment boundary:

1. each worker gets a unique Terraform directory, state, resource group, and
   deployment ID;
2. the GPU driver must become visible through `nvidia-smi`;
3. the engine must return success from local `/health`;
4. only then is the endpoint registered for routing;
5. a registration failure triggers Terraform destroy rather than leaving an
   unrouteable paid VM; and
6. an apply, output, or identifier failure requests a best-effort destroy
   before the original error is returned.

The hard expiry is persisted with credential **alias names**, not credential
values. The recurring daemon phase reloads expired records after restart and
retries failed cleanup on later ticks. When `hourly_rate_usd` is supplied,
the expiry is shortened to the earlier of the requested lifetime or
`max_cost_usd / hourly_rate_usd`.

Both Azure VM stacks also compose the existing `gpu-cost-watchdog` module into
the VM's cloud-init `custom_data`. That installs and starts the on-host
cost/TTL service at first boot using the same `max_cost_usd`,
`timeout_minutes`, and region inputs as the deployment registry. The on-host
watchdog is a fast safety layer; the registry reaper's Terraform destroy
remains the authoritative cleanup because merely powering off a VM can leave it
allocated and billable, while even a deallocated VM retains chargeable disks
and related resources. Those recurring operator surprises are documented in
[Microsoft Q&A: VM not shutting down properly](https://learn.microsoft.com/en-us/answers/questions/1521795/issue-with-azure-vm-not-shutting-down-properly)
and the user report
[stopped VMs still charging for disks months later](https://learn.microsoft.com/en-us/answers/questions/2157013/i-stopped-two-vms-on-azure-and-didnt-realize-until).
Gludd therefore tests the in-VM watchdog wiring and retains full resource-group
destroy as the money-leak boundary.

This prevents a failed replacement from displacing a healthy registered
endpoint, but it is not a claim that Azure guarantees capacity or that an
evicted Spot VM has uninterrupted service.

## Long-lived Azure incidents reflected in the design

The implementation and operator guidance account for recurring reports, not
just the happy-path documentation:

- New N-series subscriptions can expose `0 of 0` family cores until a
  region-specific quota increase is approved. This is reported for A100 in
  [Microsoft Q&A: NC24ads A100 quota at zero](https://learn.microsoft.com/en-us/answers/questions/2133321/i-need-to-create-nc24ads-a100-series-gpu-but-when).
  Gludd therefore fails closed when the family quota record is absent or too
  small.
- Quota approval and physical allocation are independent. Users report
  NC24ads A100 allocation failures after quota was granted in
  [North Europe](https://learn.microsoft.com/en-us/answers/questions/2143000/allocation-failed-for-standard-nc24ads-a100-v4-com)
  and zone capacity failures in
  [Southeast Asia](https://learn.microsoft.com/en-us/answers/questions/2261634/allocation-failed-we-do-not-have-sufficient-capaci).
  Gludd reports this limitation, isolates state, and destroys partial
  resources; retrying another zone/region/SKU remains an operator decision.
- GPU quota requests can be disabled, rejected, or closed without capacity
  being granted, especially in high-demand regions. See the reports for
  [East US](https://learn.microsoft.com/en-us/answers/questions/1409219/unable-to-request-quota-for-gpu-in-east-us-region)
  and [repeated T4/A100 quota rejection](https://learn.microsoft.com/en-us/answers/questions/2283074/why-are-my-all-my-gpu-quota-increases-failing-and).
  The harness is a diagnostic, not a quota-approval mechanism.
- A family available in one region can be used in another eligible region,
  but quota is still regional. See
  [Microsoft Q&A: use GPU instances from another region](https://learn.microsoft.com/en-us/answers/questions/692301/use-gpu-instances-from-another-region).
  Gludd therefore makes region an explicit preflight and launch input.
- Azure ML users have observed an out-of-region error despite apparently
  sufficient A100 quota:
  [Central US A100 report](https://learn.microsoft.com/en-us/answers/questions/5823533/azure-ml-deployment-fails-with-out-of-region-quota).
  Gludd checks both regional and family quota instead of treating one displayed
  quota as sufficient.
- Azure CLI can surface a generic traceback before revealing the underlying
  family quota error, as recorded in
  [Azure CLI issue #32266](https://github.com/Azure/azure-cli/issues/32266).
  The gludd preflight returns structured blockers before entering the
  provisioning path.

For allocation failures after a green preflight, follow Microsoft's
[allocation-failure guidance](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure):
retry later, remove zone constraints where appropriate, choose another
supported size, or select another region.

## Source lineage

| Branch | Commit | Completed work carried into beta.3 |
|---|---|---|
| `master` | `0b1dcb501b7537cea7af50b1e916da7ca0879bde` | Terraform stack/module materialization baseline and Azure stack examples |
| `master` | `346236a8b940a17f49242f0f0d023a2de2e2b417` | compute cost caps, hard watchdogs, Spot support, GPU metrics, idle teardown, and compute-aware scheduling |
| `master` | `37e620f962c980c3a0a77bc79301a9ce96dbc246` | Azure onboarding provider and IAM Terraform module |
| `master` | `2543152b4fab4be385f9bf67c09d13145c2b8700` | onboarding CLI/wiring and GPU metric hardening |
| `development` | `f9fba7056714fb3557ff27348b92ee3f3901031f` | Azure provider smoke harness and Azure IAM policy-test intent |
| `development` | `7f8580cf0c8941c2e236b88cf938341a8eae96fe` | task evidence for the provider harness; no separate runtime delta |
| release closure | `e69b20bcf484a90f7e3de261da3636efb3354381` | exact A100/H100/T4 resolution, quota preflight, deploy gating, real stacks/bootstrap, packaging, routing, TTL cleanup, rollback, IAM closure, and tests |

The development harness was reconciled to the current master interfaces rather
than copied byte-for-byte: beta.3 keeps the Azure path, replaces token-only
credential checking with Azure SDK SKU/quota checking, supports managed
identity, and excludes unrelated RunPod changes from this release slice.

## Validation evidence

The release slice was validated without paid provisioning:

- Azure accelerator and harness unit contracts;
- CLI/daemon preflight, spend refusal, and scheduler registration contracts;
- Terraform materialization, exact SKU, rollback, persisted expiry, and
  managed-identity contracts;
- Azure onboarding/IAM parity contracts;
- deployment manager and idle-cleanup regressions;
- OPA IAM policy tests;
- Ruff and mypy;
- Terraform `init -backend=false` plus `validate` for both Azure engine stacks;
- Terraform `init` plus `validate` for the Azure onboarding module; and
- dry-run `make azure-harness LIVE=0`.

The live read-only harness and a real A100/H100 allocation require an operator
subscription and were intentionally not run with repository credentials. A
green local validation does not override Azure quota approval, regional SKU
eligibility, Spot eviction, or physical capacity.
