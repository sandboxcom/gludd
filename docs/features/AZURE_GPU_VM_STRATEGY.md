# Azure GPU VM inventory and strategy selection

## Implemented boundary

S83.162 separates read-only admission from mutation. The admission layer discovers
Azure GPU virtual-machine offers and decides among an already-attested Azure
Container Apps placement, one host-local VM, and an eligible VM Scale Set. The
mutation layer then uses `AzureGpuWorkerInfrastructureRuntime` to materialize and
execute exactly one reviewed OpenTofu module. It never creates or deletes the
pre-existing exact-scope resource group, requests quota, or grants a role.

OpenTofu owns the VM or Uniform VMSS and every paid network/storage child. The
provider-neutral lifecycle invokes `AnsibleModelWorkerConfigurationRuntime` only
after independently reading back exact hosts. The Ansible roles attest the driver,
runtime, topology, immutable runner generation, and health before an endpoint may
be published. Self-improvement is one consumer of this lifecycle; chemistry,
firmware, and other universal tasks use the same boundary.

The immutable availability scope, assessment, and index contracts live in
`general_ludd.infra.azure_operational_availability`. They are infrastructure
evidence consumed by chemistry, firmware, self-improvement, and any other
universal workload. The legacy
`general_ludd.self_improve.azure_operational_availability` module retains the
self-improvement evidence-store adapter and re-exports the exact contract objects
for compatibility; generic infrastructure never imports the self-improvement
package.

The production adapter invokes only these maintained SDK operations:

- `ComputeManagementClient.resource_skus.list()` for subscription-visible offers,
  locations, zones, restrictions, and advertised capabilities; and
- `ComputeManagementClient.usage.list(region)` for the exact VM-family and total
  regional vCPU quota.

Pricing reuses Gludd's bounded Azure Retail Prices client. Its new VM lookup uses
the discovered `armSkuName` and region as server-side coordinates. It accepts only
one current primary USD, consumption, hourly, non-Windows, non-Spot, non-Low
Priority meter. Pagination retains the existing HTTPS host/path allowlist, page
bound, byte bound, request timeout, finite cache TTL, and ambiguity refusal. There
is no static price or stale-cache fallback.

## Provider-neutral inventory

Every eligible SDK record is normalized into `AcceleratorTopology`; the Azure VM
size is data in `resource_key` and `model`, never a routing configuration key. The
adapter derives GPU count, memory per GPU, backend, vendor, vCPUs, intra-host
interconnect and group size from generic capability names. Unknown future SKUs can
therefore qualify without a code change, while absent or malformed facts refuse the
candidate. No A100, H100, T4, TPU, or other hardware model appears in strategy
logic.

Admission requires all of the following at the same observation boundary:

1. the region is advertised and no location restriction applies;
2. when zones are advertised, at least one remains after normalized restrictions;
3. both exact-family and total-regional quota leave room for one VM;
4. an exact fresh ARM-SKU retail meter is present;
5. GPU count, per-device memory, backend, vendor, vCPU count, and any claimed
   multi-GPU interconnect are internally consistent; and
6. recent S83.160 terminal evidence contains at least one success and still marks
   the exact region/SKU/runtime/topology scope feasible.

Resource SKU visibility and quota do not prove physical capacity. Missing capacity
evidence therefore fails closed instead of converting a necessary condition into a
capacity claim. S83.160's finite TTL also prevents an old success from keeping a
placement eligible forever. Its availability probability is not used to score
model quality or rank two model identities.

## Container Apps versus VM and VMSS

The selector first applies privacy permission, operator approval, immutable
identity attestation, runtime compatibility, evidence freshness, topology, and
hourly budget gates. Availability cannot override any of them. It then runs the
provider-neutral topology planner independently for each option.

Container Apps can win only when the model fits one whole isolated GPU, the
profile and runner are attested, a comparable single-VM option is eligible, and
measured completion time plus projected cost Pareto-dominate that VM. A cheaper but
slower option, or a faster but more expensive option, does not count as a measured
win. Unsupported runtime/backend, insufficient single-device memory, or explicit
host-local tensor parallelism selects one VM when that VM passes every hard gate.

VM Scale Sets are eligible only after replica semantics, high-availability
behavior, RDMA topology, provisioning, bootstrap, rich health, telemetry, work
dispatch, and teardown are independently attested. An eligible VMSS displaces a
feasible single VM only on a measured cost-and-completion-time Pareto win, unless
VMSS is the sole topology that can satisfy the demand. Replica count remains
separate from `devices_per_replica`, so multiple hosts are never mistaken for
host-local model parallelism.

## VMSS implementation constraints

The implemented VMSS module uses Uniform orchestration because the admitted
multi-host and RDMA layouts require one immutable, homogeneous topology with rich
application health and rolling replacement. Microsoft recommends Flexible
orchestration for general availability, mixed VM types, and ordinary VM APIs, but
its orchestration-mode matrix says Flexible does not support InfiniBand while
Uniform supports it only in one placement group. Gludd therefore refuses a VMSS
unless the measured evidence justifies this Uniform boundary; an ordinary
single-host request remains a single VM or Container Apps placement. Orchestration
mode is immutable after creation:
[VMSS orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes),
[ND family topology](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family),
and [InfiniBand-enabled N-series guidance](https://learn.microsoft.com/en-us/azure/virtual-machines/overview-hb-hc).

Every scale set must report the runner's local readiness endpoint through Azure's
Application Health extension before it can receive work. Rolling upgrades and
automatic instance repairs depend on that signal; ARM provisioning success alone
is not readiness. Accelerated networking is enabled only when discovered SKU
capabilities attest it, and Flexible scale sets require explicit outbound
connectivity:
[Application Health extension](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-health-extension),
[automatic instance repairs](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-automatic-instance-repairs),
and [VMSS networking](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-networking).

Driver installation remains an observable Ansible phase with immutable image,
vendor, driver, and runtime compatibility evidence. Microsoft's Linux extension
guidance says installation may reboot the VM, requires outbound access, does not
automatically update installed drivers, and cannot be used with Secure Boot. Gludd
must therefore reject an incompatible image/security posture before provisioning
and independently attest the device after configuration:
[NVIDIA GPU Driver Extension for Linux](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/hpccompute-gpu-linux).

Long-lived practitioner reports add two regression cases to that normative model:

- VMSS Custom Script Extension provisioning can remain at `Plugin enabled` until
  timeout when scripts block, outbound dependencies are unreachable, extension
  state is stale, scale-out reruns differ, or the GPU driver cannot attach. Gludd
  therefore bounds bootstrap, records per-instance extension/Ansible phases, and
  never equates extension installation with runner readiness:
  [VMSS CSE timeout report](https://learn.microsoft.com/en-us/answers/questions/5692668/how-to-fix-provisioning-of-vm-extension-vmsscse-ha).
- An NVv4 operator selected the NVIDIA extension for an AMD MI25-backed SKU and
  repeatedly failed installation. Gludd derives vendor from live SKU evidence and
  refuses a mismatched driver role before mutation:
  [NV8as v4 driver mismatch report](https://learn.microsoft.com/en-us/answers/questions/2338751/new-azure-vm-using-standard-nv8as-v4-%288-vcpus-28-g).

Container Apps remains the single-whole-GPU alternative: Microsoft's current
serverless GPU contract permits neither multi-GPU nor fractional GPU replicas and
only the first container receives the device. Multi-device model parallelism must
therefore route to an attested VM or VMSS host topology:
[Container Apps serverless GPU limits](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview).

## Observability and privacy

Inventory and strategy emit bounded typed traces containing only event kind,
candidate/accepted/rejected counts, strategy class, and stable reason code. They do
not include model identifiers, prompts, endpoints, credentials, SKU names, vendor
names, meter IDs, runtime digests, provider messages, or resource keys. Provider
exceptions collapse to censored refusal classes such as `price_unavailable`.

Mutation uses a second content-free trace containing only OpenTofu phase, state,
elapsed seconds, and a desired-state digest. Every init, validate, plan, show,
apply, output, and destroy phase acquires a fresh bounded credential from the
operator-configured OpenBao Azure role and revokes that exact lease afterward.
Credentials enter only the child-process environment. They are excluded from
tfvars, state inputs, output parsing, Ansible desired state, and traces.

Before apply, Gludd audits the saved plan against an exact resource-address and
resource-type allowlist for the selected module. The materializer rejects symlink
roots, foreign state, mismatched ownership markers, mutable image versions,
cross-resource-group identities, broad controller CIDRs, and unattested cache
disks. VMSS private addresses are resolved with maintained Compute and Resource
Management SDK clients; single-VM addresses and all owned IDs are validated
against exact OpenTofu outputs.

The credential-free unit and integration suites use SDK-shaped fakes and an
arbitrary future SKU. They prove restriction and zone normalization, both quota
tiers, exact-price freshness, capacity freshness, content-free traces, hard-policy
precedence, single-GPU Container Apps wins, host-local multi-GPU VM selection, and
VMSS lifecycle admission. A VMSS option is refused until provisioning, bootstrap,
replica, HA, RDMA topology, rich health, telemetry, work-dispatch, and teardown
evidence are all independently attested. If one VM remains feasible, VMSS must
Pareto-win on measured projected cost and completion time; if only VMSS satisfies
the topology, the fully attested option can run directly. The same tests run
locally and in ordinary GitHub Actions without an Azure secret; an opt-in protected
workflow supplies live read-only evidence.

## ZDD and rollback

Discovery and selection are side-effect free, so failure leaves the serving path
unchanged. The `azure-gpu-vmss-worker` OpenTofu module creates a uniquely owned
private Uniform scale set with explicit NAT egress, surge rolling upgrades, rich
application health, automatic repair, and an exact-scope deployment role. The
single-VM module creates one controller-restricted public worker for host-local
multi-GPU layouts.

The universal lifecycle keeps the old serving route while it provisions the
replacement, configures hosts serially, and admits only the complete attested host
set. Apply or output failure invokes destroy against the same owned state before
returning a censored error. Close drains the route, retires the exact systemd
generation, destroys infrastructure, and then uses an independently constructed
Azure SDK client to enumerate the resource group and prove every recorded ARM ID
absent. A successful OpenTofu exit alone is not cleanup proof.

Rollback is immediate and non-destructive at selection time: remove the new
strategy option or stop supplying fresh exact-scope evidence. The next selection
refuses it. Expired capacity or price evidence has the same effect. For an already
materialized candidate, the lifecycle supervisor drains and destroys only the
state- and lease-owned resources, then requires final Azure absence.

## Verification

The infrastructure path is covered by
`tests/unit/test_azure_gpu_worker_materializer.py`,
`tests/unit/test_azure_gpu_worker_sdk.py`, and
`tests/unit/test_azure_gpu_worker_runtime.py`. They cover both VM strategies,
private state inputs, foreign-state and symlink refusal, exact plan auditing,
per-phase credential leases, SDK inventory normalization, compensation, censored
failures, owned teardown, and independent absence. Their branch-aware focused
profiles report 95%, 96%, and 93% respectively; each exceeds the 85% aggregate
and 75% per-file release floors.

## Source evidence and operator reports

This tranche reuses the practitioner evidence already curated in
`AZURE_ACCELERATOR_LIVE_PROOF.md`; it did not launch a second research pass.
Normative sources are Microsoft's
[Resource SKUs API](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list),
[regional and VM-family quota guidance](https://learn.microsoft.com/en-us/azure/quotas/regional-quota-requests),
[allocation-failure guidance](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure),
and [Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).

Long-lived operator reports remain diagnostic evidence rather than service
guarantees:

- [GPU instances can be region-limited](https://learn.microsoft.com/en-us/answers/questions/692301/use-gpu-instances-from-another-region),
  so region and both quota tiers are explicit.
- [A100 allocation failed after quota approval](https://learn.microsoft.com/en-us/answers/questions/2143000/allocation-failed-for-standard-nc24ads-a100-v4-com),
  so SKU visibility, quota, and terminal capacity remain separate facts.
- [A GPU family can expose zero quota](https://learn.microsoft.com/en-us/answers/questions/2133321/i-need-to-create-nc24ads-a100-series-gpu-but-when),
  so the inventory never requests quota or attempts a speculative allocation.
- [WALinuxAgent issue 1938](https://github.com/Azure/WALinuxAgent/issues/1938)
  documents a package-manager race between agent extensions and cloud-init, which
  is why a later VM lifecycle must attest driver, runner, and endpoint readiness
  independently of ARM completion.
