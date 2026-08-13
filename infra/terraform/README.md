# Terraform Infrastructure — Gludd

Declarative cloud infrastructure for GPU inference servers across 9 providers.

## Layout

```
infra/terraform/
  versions.tf       canonical provider-version contract (single source of truth)
  .plugin-cache/    shared provider binary cache (TF_PLUGIN_CACHE_DIR)
  modules/          reusable Terraform modules (9 modules)
  stacks/           per-provider deployment stacks (18 stacks across 9 providers)
  examples/         11 tfvars.example files for quick-start
  policies/         OPA policies: core.rego, trust.rego, data.json
```

## Provider contract

`versions.tf` is the single source of truth for provider versions. Every stack under `stacks/` pins its `required_providers` to match. Drift is blocked by `make tf-versions-check` (`scripts/check_tf_provider_versions.py`).

| Provider | Source | Version |
|---|---|---|
| AWS | `hashicorp/aws` | `~> 5.0` |
| Google (GCP) | `hashicorp/google` | `~> 5.0` |
| Azure | `hashicorp/azurerm` | `~> 4.55` |
| Azure ARM escape hatch | `Azure/azapi` | `~> 2.0` |
| Kubernetes | `hashicorp/kubernetes` | `~> 2.31` |
| vSphere | `vmware/vsphere` | `~> 2.8` |
| RunPod | `runpod/runpod` | `~> 1.0` |
| Libvirt | `dmacvicar/libvirt` | `~> 0.7` |

The shared plugin cache (`TF_PLUGIN_CACHE_DIR`) downloads each provider binary once into `.plugin-cache/`. All stacks reuse the same cache — no per-stack re-download.

QEMU stacks are implemented through `dmacvicar/libvirt` with a
`qemu:///system` URI; there is no second QEMU provider to download.

### Provider-registry maintenance findings

- VMware announced in May 2025 that the vSphere provider moved from
  `hashicorp/vsphere` to `vmware/vsphere`, with prior releases re-signed under
  the new namespace. The old address now emits a migration warning:
  [HashiCorp Discuss announcement](https://discuss.hashicorp.com/t/terraform-provider-for-vmware-vsphere-has-now-moved-to-vmware-vsphere/74955).
- A long-lived user report shows how undeclared third-party provider addresses
  fall back to a nonexistent `hashicorp/*` provider and break
  `terraform init`; the maintainer confirms every consuming module must declare
  the correct source:
  [HashiCorp Terraform issue #32247](https://github.com/hashicorp/terraform/issues/32247).
- QEMU/KVM is provided by the maintained `dmacvicar/libvirt` provider, whose
  documented local URI is `qemu:///system`:
  [upstream provider documentation](https://github.com/dmacvicar/terraform-provider-libvirt).
  The stale `jvzq/qemu` cache-only declaration was never used by a stack and
  caused the release cache warm-up to fail, so it is intentionally removed.

## Modules vs stacks

- **Modules** (`modules/`) — reusable Terraform building blocks. Stateless, provider-agnostic where possible. Composed into stacks.
- **Stacks** (`stacks/`) — concrete deployments that wire modules together for a specific provider + inference engine. One stack = one provider × one engine (e.g. `aws-vllm`, `gcp-llamacpp`).

## Common commands

```bash
# Warm the shared provider cache (run once after clone or version bump)
make tf-cache-warm

# Init a single stack using the shared cache
make tf-init STACK=stacks/aws-vllm

# Validate a single stack
make tf-validate STACK=stacks/aws-vllm

# Enforce provider-version consistency across all stacks
make tf-versions-check

# Remove the shared cache
make tf-clean
```

## Adding a new stack

1. Create `infra/terraform/stacks/<provider>-<engine>/main.tf` referencing the relevant modules.
2. Copy the matching `.tfvars.example` from `examples/` and customize.
3. Run `make tf-init STACK=stacks/<provider>-<engine>`.
4. Run `make tf-validate STACK=stacks/<provider>-<engine>`.
5. Ensure `make tf-versions-check` passes.

## OPA Policies

`policies/` contains Open Policy Agent rules for Terraform plan evaluation:

| File | Purpose |
|---|---|
| `core.rego` | Core infrastructure policy — resource naming, tags, security group constraints |
| `trust.rego` | Trust/security policy — IAM least-privilege, public-access gates |
| `data.json` | Constraint data (allowlisted regions, instance types, CIDR blocks) |
