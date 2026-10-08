# Azure single GPU worker

This module is the bounded OpenTofu ownership layer for one Linux GPU worker. It
creates only the virtual network, subnet, public IP, network security group,
network interface, managed OS disk, and virtual machine inside one **existing
exact resource group**. It never creates or deletes that group, assigns RBAC,
registers providers, runs guest commands, or creates a VM scale set.

The Gludd planner supplies a live-discovered `Standard_*` ARM SKU and whether
that SKU supports accelerated networking. The module intentionally contains no
GPU-model or SKU allowlist. The image version is immutable, the controller is
the only allowed `/32`, password login is disabled, and VM extension operations
are disabled. Every priced child resource carries the lease owner, trace, and
expiration tags and appears in `owned_resource_ids` for final-absence evidence.

The next phase consumes `ansible_host` and `ansible_user` through Gludd's
Ansible collection to install the vendor-supported driver and model runner,
attest device topology, and expose the canonical worker envelope. OpenTofu
remains the sole infrastructure mutator. Ansible is the sole guest configurator;
neither VM RunCommand nor Custom Script Extension is part of this contract.

## Permission boundary

`config/infra/azure-gpu-worker-iam-policy.json` is a separate custom role from
the Container Apps deployer. An operator or OpenBao control-plane identity
creates and assigns it at the exact existing resource-group scope. A short-lived
component lease may then create and destroy only this module's VM, managed OS
disk, and network children and read their metrics. It cannot mutate the group,
IAM, provider registrations, registries, secrets, VM extensions, RunCommand, or
VM scale sets. Subscription-wide SKU, quota, price, and capacity discovery stays
with the separate read-only inventory identity.

The implementation deliberately uses the maintained
[`hashicorp/azurerm`](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_virtual_machine)
provider instead of custom ARM request code. Microsoft's current
[compute role reference](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/compute)
and [network permission catalog](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking)
are the source for the explicit, wildcard-free operations.

The consuming root module must configure AzureRM with
`resource_provider_registrations = "none"`. Provider registration is an
operator bootstrap concern and is intentionally absent from the worker lease;
an apply must fail closed if Compute or Network was not registered beforehand.

## Long-lived field reports incorporated

- Users have reported destroy jobs silently losing remote resources from state
  and then failing on a still-attached NIC. Gludd therefore retains one
  lifecycle-owned state artifact, outputs the complete child-resource set, and
  requires final ARM absence rather than treating a successful command as proof:
  [AzureRM issue #23488](https://github.com/hashicorp/terraform-provider-azurerm/issues/23488).
- Azure `custom_data` has produced repeated "did not run" confusion and weak
  completion evidence. This module sends no bootstrap payload; the observable
  Ansible phase follows successful provisioning:
  [AzureRM issue #9344](https://github.com/hashicorp/terraform-provider-azurerm/issues/9344).
- A multi-year VMSS report shows Azure Linux Agent extension processing can
  break when settings are omitted and that the apparent workaround causes
  perpetual diffs. This single-VM slice excludes extensions and defers VMSS
  until replica demand and a separately tested lifecycle justify it:
  [AzureRM issue #23688](https://github.com/hashicorp/terraform-provider-azurerm/issues/23688).

Azure's NVIDIA extension documentation also notes that it can reboot a host,
does not automatically update drivers, and has SKU/image-specific exceptions.
Those facts reinforce keeping driver choice and attestation in the Ansible
phase rather than hiding it inside provisioning:
[Microsoft GPU driver extension guidance](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/hpccompute-gpu-linux).
