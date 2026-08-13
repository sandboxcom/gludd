# Azure Terraform release continuity

Status: implemented for the beta.4 release closure. Repository gate evidence is
tracked in `TASKS.md` as S83.25.

## Purpose

Azure accelerator tests and implementation must move through branch
reconciliation as one deployable unit. A release is not Azure-ready merely
because accelerator shape and quota tests are present: the CLI, SDK contract,
Terraform materializer, deployment lifecycle, packaged stacks, and cleanup
path must all remain connected.

This specification closes a merge-integrity gap found by the beta.4
authoritative gate. Historical Azure tests had reached `development`, while
part of their production implementation was absent after conflict resolution.

## Behavioral contract

1. `gludd compute azure-preflight` exposes the read-only SKU and quota check.
2. Azure launch options reach the daemon payload without dropping the bounded
   timeout, storage, image, ingress, SSH key, price, or concurrency controls.
3. Azure SDK-shaped result dataclasses remain reconstructible from serialized
   dictionaries and use isolated collection defaults.
4. Azure VM deployments materialize one of the checked-in
   `azure-vllm` or `azure-llamacpp` roots with the exact resolved SKU.
5. Azure Container App deployments materialize their checked-in module and
   variables into an isolated root.
6. Other providers retain generic `main.tf` materialization; Azure support
   does not narrow the established multi-provider API.
7. Apply or output failure requests best-effort destroy before returning the
   original failure. Expired persisted deployments can be destroyed after a
   daemon restart using credential alias names rather than secret values.
8. Terraform provider addresses use their current registry owners, including
   `vmware/vsphere`; stale source addresses must not be introduced by a merge.
9. Process-global lifecycle cleanup belongs to the registered shutdown hook.
   An individual deployment-manager instance must not sweep resources owned by
   unrelated managers.

## Zero-downtime and rollback

Every deployment has a unique working directory and Terraform state. A new
accelerator endpoint is registered only after Terraform output provides an
identifier and reachable endpoint. A failed replacement therefore cannot
displace a healthy endpoint. Partial apply and incomplete output both enter
the same best-effort destroy boundary, and the persisted hard-expiry record
allows cleanup to resume after restart.

Rollback restores the previous application worker while retaining the
isolated state directory and expiry record needed to destroy any resource
created by the failed worker. Release promotion remains development-first and
requires the complete repository gate before master/tag deployment.

## Security and compatibility

Azure SDK variables are translated to AzureRM variables in a subprocess-local
environment. Global process environment and persisted deployment records never
receive credential values. Managed identity remains valid when a client ID is
present without a secret.

Generic provider materialization remains backward compatible. The beta.4
repair changes no public serialized field names and adds a read-only
`CustomEvent.name` compatibility property backed by the existing payload.

## Observability and verification

The focused release slice covers CLI routing, quota preflight, exact
accelerator resolution, contract serialization, materialization, provider
generation, apply rollback, registry expiry, credential translation,
telemetry, and provider configuration. The broader gate retains the repository
85% aggregate and 75% per-file coverage floors.

The exact compute-subcommand inventory is part of that release contract. A
beta.4 gate exposed a stale five-command assertion that omitted the independently
tested `azure-preflight` path. The inventory, parser, payload, daemon, and
adjacent CLI family is now 167/167 green under strict warnings.

The checked-in `tf-init-local` target validates the Azure stack choice and can
run a state-free `terraform init -backend=false`. Its validate-only mode is
the safe behavioral example used by the Make target contract.

## Practitioner evidence

Two long-lived provider reports shaped the fail-closed lifecycle boundary:

- HashiCorp Terraform issue
  [#25951](https://github.com/hashicorp/terraform/issues/25951) records provider
  source-address drift that left users unable to destroy existing resources.
  Gludd pins the registry owner in generated configuration and tests that
  address.
- AzureRM provider issue
  [#16155](https://github.com/hashicorp/terraform-provider-azurerm/issues/16155)
  records resource-group deletion failing while Azure still reported lagging
  nested resources. Gludd preserves isolated state and treats destroy as a
  retryable lifecycle responsibility rather than deleting local evidence.

The maintained vSphere provider repository identifies its registry address as
[`vmware/vsphere`](https://github.com/vmware/terraform-provider-vsphere),
which is the source emitted by the generator.
