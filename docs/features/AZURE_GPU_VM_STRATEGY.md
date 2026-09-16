# Azure GPU VM inventory and strategy selection

## Tranche boundary

S83.162 tranche 1 is a read-only admission and planning layer. It discovers Azure
GPU virtual-machine offers and decides between an already-attested Azure Container
Apps placement and one host-local VM placement. It does not create a resource
group, VM, scale set, disk, network, quota request, or role assignment. OpenTofu and
the Ansible worker lifecycle remain the owners of later mutation tranches.

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

## Container Apps versus one VM

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

VM Scale Sets are represented only by a future-admission evidence object. Replica
semantics, high-availability behavior, and RDMA topology must each be independently
attested before that object becomes eligible; tranche 1 still refuses VMSS
selection after those flags are present. This prevents replica count from being
mistaken for host-local model parallelism.

## Observability and privacy

Inventory and strategy emit bounded typed traces containing only event kind,
candidate/accepted/rejected counts, strategy class, and stable reason code. They do
not include model identifiers, prompts, endpoints, credentials, SKU names, vendor
names, meter IDs, runtime digests, provider messages, or resource keys. Provider
exceptions collapse to censored refusal classes such as `price_unavailable`.

The credential-free unit and integration suites use SDK-shaped fakes and an
arbitrary future SKU. They prove restriction and zone normalization, both quota
tiers, exact-price freshness, capacity freshness, content-free traces, hard-policy
precedence, single-GPU Container Apps wins, host-local multi-GPU VM selection, and
VMSS deferral. The same tests run locally and in ordinary GitHub Actions without an
Azure secret; a later opt-in protected workflow can supply live read-only evidence.

## ZDD and rollback

Discovery and selection are side-effect free, so failure leaves the serving path
unchanged. A later provisioning tranche must create a uniquely owned replacement,
attest driver/runtime/topology and health, register it without traffic, switch
traffic only after readiness, drain the prior worker, and then destroy only the
prior owned resources. Allocation, attestation, price, policy, or routing failure
must destroy the replacement and retain the old endpoint.

Rollback is immediate and non-destructive: remove the new strategy option or stop
supplying fresh exact-scope evidence. The next selection refuses it; no Azure
resource is changed by this module. Expired capacity or price evidence has the same
effect. VMSS stays unreachable until a later tested tranche explicitly consumes
all three attestations.

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
