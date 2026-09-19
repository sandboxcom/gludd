# Azure GPU VMSS worker

This module is Gludd's bounded OpenTofu ownership layer for a replicated Linux
GPU worker pool. It creates a Uniform Azure Virtual Machine Scale Set and only
the network, explicit NAT egress, rich application-health extension, and managed
disks needed inside one **existing exact resource group**. It does not create or
delete that group, assign RBAC, register providers, store credentials, expose an
inbound public address, or run guest bootstrap commands.

Uniform orchestration is intentional for this module. Azure's current
[orchestration-mode matrix](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes)
says Flexible is generally preferred but does not support InfiniBand, while
Uniform supports InfiniBand only in a single placement group. Gludd therefore
uses this module when measured replica or RDMA topology requires a VMSS and
forces a single placement group for an RDMA plan. A future Flexible module must
use AzureRM's distinct `azurerm_orchestrated_virtual_machine_scale_set`
resource; it must not pretend the two modes are a mutable flag on this resource.

The planner supplies a live-discovered `Standard_*` ARM SKU, replica count,
zones, fault domains, accelerated-networking support, and RDMA requirement.
There is no NC/ND/NV allowlist because Azure inventory, quota, capacity, price,
GPU vendor, device memory, and interconnect evidence are authoritative. Azure
is not EC2, and Azure does not offer an attachable TPU device. TPU work remains
provider-neutral and is routed only when another provider's live inventory
proves a compatible accelerator.

## Observable lifecycle and ZDD

The VMSS begins with at least two instances, uses surge-based rolling upgrades,
allows zero unhealthy instances during a rollout, and waits between batches.
The only guest extension is Microsoft's
[Application Health extension](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-health-extension)
in rich-state mode. It probes the local `/healthz` endpoint and gates both
rolling replacement and automatic repair. The runner must return the documented
rich health body only after driver, topology, model, and service attestation is
complete; otherwise the instance remains outside a healthy rollout.

OpenTofu provisions desired infrastructure. Gludd's Ansible collection then:

1. resolves current private instance addresses from the emitted Resource Graph
   predicate over a privately routed controller connection;
2. identifies the actual accelerator vendor and installs the matching pinned
   driver and runtime without VM RunCommand or Custom Script;
3. validates every visible GPU, VRAM total, peer/RDMA topology, and runner
   tensor/pipeline/replica layout before starting vLLM, Ollama, or llama.cpp;
4. mounts an optional per-instance cache only when its regional managed-disk
   price was independently attested, then treats it as disposable;
5. publishes health and telemetry, drains work on termination notice, and
   proves final ARM absence after OpenTofu teardown.

The module emits no per-instance public IP. The controller must already have a
private route through peering, VPN, or an operator-owned network boundary. Its
single IPv4 `/32` alone may reach SSH and inference; explicit Standard NAT owns
outbound connectivity for package, model, and telemetry endpoints.

## Permission and credential boundary

`config/infra/azure-gpu-vmss-worker-iam-policy.json` is a new custom role rather
than an expansion of either Container Apps or single-VM authority. An operator
assigns it only at the exact resource group and configures AzureRM with
`resource_provider_registrations = "none"`. The OpenBao control plane leases
short-lived credentials for that role to the infrastructure component. The
module may attach an existing user-assigned identity to guests but cannot create
the identity, grant it roles, read secrets, or broaden its own authority.

The implementation uses the maintained
[`hashicorp/azurerm`](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_virtual_machine_scale_set)
provider. The state contains a public SSH key and resource IDs but no private
key, password, token, model credential, or OpenBao lease secret. Ownership,
trace, and expiry tags plus `owned_resource_ids` define the cleanup boundary;
implicit VMSS NICs and disks are verified as scale-set children during final
absence attestation.

## Long-lived field reports incorporated

- A VMSS Custom Script Extension can sit at “Plugin enabled” until timeout when
  scripts block, outbound access fails, state becomes stale, scale-out reruns
  non-idempotent work, or a GPU driver cannot attach. This module excludes that
  extension, creates explicit egress, and leaves observable idempotent bootstrap
  to Ansible:
  [Microsoft Q&A 5692668](https://learn.microsoft.com/en-au/answers/questions/5692668/how-to-fix-provisioning-of-vm-extension-vmsscse-ha).
- Users selected NV8as v4 and applied an NVIDIA extension even though that SKU
  contains an AMD MI25. Gludd derives vendor choice from inventory plus guest
  PCI attestation and never guesses it from an `NV` prefix:
  [Microsoft Q&A 2338751](https://learn.microsoft.com/en-us/answers/questions/2338751/new-azure-vm-using-standard-nv8as-v4-%288-vcpus-28-g).
- AzureRM users have reported destroy/state races around attached networking.
  The module retains one lifecycle state, exposes every top-level child, and
  requires final Azure absence instead of trusting a successful destroy exit:
  [AzureRM issue 23488](https://github.com/hashicorp/terraform-provider-azurerm/issues/23488).
- Azure Linux Agent extension settings have produced perpetual diffs and broken
  extension processing. The health extension settings here are complete,
  deterministic JSON; no driver or application extension is hidden in the
  infrastructure phase:
  [AzureRM issue 23688](https://github.com/hashicorp/terraform-provider-azurerm/issues/23688).
