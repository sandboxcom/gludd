# Terraform / vLLM / llama.cpp / VMware Infrastructure - Design Doc

**Status:** Implemented static-module layout with legacy cleanup remaining
**Author:** infra workstream
**Last updated:** 2026-07-22

## Purpose

gludd now has reviewable Terraform under `infra/terraform/` for GPU-serving and provider onboarding infrastructure. The original design goal was a hybrid static-module plus dynamic-composition model. That model is now the active implementation for the checked-in stacks, while the older Python Terraform generator remains as a compatibility path until Phase 4 removes inline HCL resources.

This document records the current state, the remaining gaps, and the expected validation workflow for vLLM, llama.cpp, and provider infrastructure.

Current implementation points:

- Shared engine modules live under `infra/terraform/modules/`.
- Provider stacks live under `infra/terraform/stacks/`.
- Provider versions are pinned centrally in `infra/terraform/versions.tf`.
- A shared Terraform plugin cache is managed by make targets.
- vSphere, Kubernetes, QEMU, AWS, GCP, Azure, RunPod, and Vast.ai stacks are present for vLLM and llama.cpp where applicable.
- Backend configuration uses the gludd daemon HTTP backend by default and can be overridden with normal Terraform backend configuration.
- `.gitignore` covers Terraform state, plan, plugin cache, and real tfvars files while keeping example inputs trackable.

Grounds in:

- `infra/terraform/modules/vllm-server/`
- `infra/terraform/modules/llamacpp-server/`
- `infra/terraform/modules/gpu-cost-watchdog/`
- `infra/terraform/modules/network/`
- `infra/terraform/modules/qemu-vm/`
- `infra/terraform/modules/kubernetes-deploy/`
- `infra/terraform/modules/onboard-iam/`
- `infra/terraform/modules/onboard-iam-gcp/`
- `infra/terraform/modules/onboard-iam-azure/`
- `infra/terraform/stacks/aws-vllm/` and `aws-llamacpp/`
- `infra/terraform/stacks/gcp-vllm/` and `gcp-llamacpp/`
- `infra/terraform/stacks/azure-vllm/`, `azure-llamacpp/`, and container app variants
- `infra/terraform/stacks/runpod-vllm/` and `runpod-llamacpp/`
- `infra/terraform/stacks/vast-vllm/` and `vast-llamacpp/`
- `infra/terraform/stacks/vsphere-vllm/` and `vsphere-llamacpp/`
- `infra/terraform/stacks/kubernetes-vllm/` and `kubernetes-llamacpp/`
- `infra/terraform/stacks/qemu-vllm/` and `qemu-llamacpp/`
- `tests/unit/test_terraform_validate.py`
- `tests/unit/test_terraform_modules.py`
- `tests/unit/test_vsphere_provider.py`
- `tests/unit/test_terraform_escape.py`

## 1. Current state

### 1.1 Static modules and stacks

The repo contains the `infra/terraform/` tree and checked-in `.tf` artifacts. Engine modules define reusable vLLM and llama.cpp serving behavior. Provider stacks compose those modules with provider setup, networking, IAM or equivalent access, and backend configuration.

Operators validate stacks through make targets:

```bash
make tf-cache-warm
make tf-init STACK=stacks/aws-vllm
make tf-validate STACK=stacks/aws-vllm
make tf-versions-check
```

Use `make tf-clean` to remove the shared plugin cache. Do not commit real state, plan, lock, or tfvars files.

### 1.2 Compatibility generator

`TerraformGenerator` in `src/general_ludd/infra/terraform.py` still exists. It supports legacy call sites and several provider-specific generation paths while stacks are migrated to module-backed behavior. The compatibility generator is not the long-term source of truth for reviewable infrastructure. Phase 4 is complete only when no `_generate_*` method emits inline resource HCL.

### 1.3 Local inference and smoke tests

`LocalInferenceManager` handles local vLLM and llama.cpp serving for dev loops and CI smoke surfaces. Provider smoke tests can also use provisioned compute paths to stand up a cloud or private-cloud endpoint, run a minimal model task, probe health and metrics, and destroy the resource in cleanup. See `docs/SMOKE_TESTS.md`.

---

## 2. Remaining gaps

The original gaps have mostly been closed by the static module and stack layout.
Current status:

- G1 resolved: reviewable `.tf` artifacts exist under `infra/terraform/`.
- G2 resolved: VMware/vSphere provider support and stacks exist.
- G3 partially resolved: module-backed stacks and tfvars remove the main reviewability problem, but the legacy generator still needs Phase 4 cleanup.
- G4 resolved for model-serving infrastructure: shared vLLM and llama.cpp modules exist.
- G5 partially resolved: provider E2E and smoke surfaces exist, but real live provider coverage depends on operator credentials and remains skip-by-default in CI.

The active gap is therefore not whether Terraform infrastructure exists. It is eliminating the legacy inline generator paths and keeping smoke-test coverage broad enough to catch provider drift.

---

---

## 3. Options

### Option A — Keep dynamic generation, harden it

Tighten the HCL escaping in `TerraformGenerator` (use `hcl.escape`-style
helpers, never bare f-string substitution). Add round-trip tests that run
`terraform validate` on every generated `main.tf`. Add VMware as a 7th
generator method.

| Pros | Cons |
|---|---|
| Lowest effort — touches only `terraform.py` + tests | Does **not** fix G1 (still no reviewable artifacts) |
| Preserves the single-dispatch flexibility | Injection surface is narrowed, not removed — any new field reintroduces it |
| No migration risk | Operator still cannot `terraform plan` offline or diff stacks in PRs |

### Option B — Hybrid: checked-in static modules + dynamic composition **(recommended)**

Create real `.tf` **modules** under `infra/terraform/modules/` for the
engine-level concerns (`vllm-server`, `llamacpp-server`, `gpu-cost-watchdog`,
`network`). The Python `TerraformGenerator` becomes a **composer**: instead of
emitting inline HCL, it emits a small `main.tf` that contains

```hcl
module "vllm" {
  source = "./modules/vllm-server"
  image  = var.image
  gpus   = var.gpus
  ...
}
```

plus a `terraform.tfvars` file carrying the per-deployment values. The actual
HCL bodies live in version-controlled, reviewable module files.

| Pros | Cons |
|---|---|
| Eliminates G1 (reviewable `.tf`) and G3 (tfvars cannot inject HCL structure) | Medium effort — extract modules from current strings, wire composer, round-trip tests |
| Preserves the dynamic dispatch gludd needs — a `compute_launch` call still works without hand-editing `.tf` | Two sources of truth during the migration window (legacy inline path + new module path); deprecation phase resolves it |
| G4 collapses — one `vllm-server` module serves every provider | Requires `terraform` binary present for `validate` in CI (it already is — `deployment.py` shells out) |
| Small module set stays small as the provider×engine matrix grows | |

### Option C — Full static: one checked-in stack per provider×engine pair

`infra/terraform/<provider>-<engine>/main.tf` for every combination. The
Python layer becomes a stack-selector that copies the right directory and
writes tfvars.

| Pros | Cons |
|---|---|
| Maximally reviewable — every runnable stack is a literal directory | High effort — N×M stacks to maintain |
| No composition logic | Duplicative: the vLLM launch script is copied into every stack; a fix touches N files |
| | Hardest to maintain as the matrix grows (11 providers × 2 engines today = 22 stacks; every new provider doubles) |

---

## 4. Recommendation: Option B (hybrid)

Option B is the only path that resolves **all four** of G1/G3/G4 plus the
reviewability root cause without sacrificing the dynamic dispatch gludd
requires. Concretely:

- **Modules eliminate the HCL-injection surface.** tfvars carry values, not
  structure — a stray `}` in a config field becomes a tfvars parse error, not
  an arbitrary HCL fragment.
- **Composition preserves flexibility.** A `compute_launch` CLI invocation
  still works end-to-end without the operator editing `.tf` — the Python layer
  picks modules and emits tfvars, exactly as today, but the HCL bodies are
  static and reviewed.
- **The module set stays small.** Engine-level concerns (`vllm-server`,
  `llamacpp-server`, `gpu-cost-watchdog`) + one `network` module per provider
  family. The 11-provider × 2-engine matrix collapses to ~6 modules + N thin
  stacks.
- **Option A is a subset of Option B.** Hardening the escaping (A) is still
  worth doing as Phase 0, but stopping there leaves the reviewability gap
  permanently.
- **Option C's reviewability win is illusory** — duplication erodes it the
  first time a fix lands in 22 places and one is missed.

---

## 5. Implemented directory structure

```text
infra/
  terraform/
    versions.tf
    modules/
      vllm-server/
      llamacpp-server/
      gpu-cost-watchdog/
      network/
      qemu-vm/
      kubernetes-deploy/
      onboard-iam/
      onboard-iam-gcp/
      onboard-iam-azure/
    stacks/
      aws-vllm/                 aws-llamacpp/
      gcp-vllm/                 gcp-llamacpp/
      azure-vllm/               azure-llamacpp/
      azure-container-app-vllm/ azure-container-app-llamacpp/
      runpod-vllm/              runpod-llamacpp/
      vast-vllm/                vast-llamacpp/
      vsphere-vllm/             vsphere-llamacpp/
      kubernetes-vllm/          kubernetes-llamacpp/
      qemu-vllm/                qemu-llamacpp/
    policies/
      core.rego
      trust.rego
      data.json
```

- `modules/` holds reusable provider-neutral engine and platform logic.
- `stacks/` holds provider and engine entry points that compose modules.
- `policies/` holds OPA policy material for infrastructure validation.
- Real credentials, state, plans, and tfvars files are not committed.
- Example values should stay in example files only.

---

## 6. VMware / vSphere provider status

vSphere is implemented as part of the provider matrix:

- `ComputeProvider.VMWARE` exists in `src/general_ludd/infra/compute.py`.
- `ProviderInfo` for VMware exists in `src/general_ludd/infra/providers.py` with `vmware/vsphere` metadata.
- `TerraformGenerator._generate_vsphere` exists and emits module-backed HCL.
- `infra/terraform/stacks/vsphere-vllm/` and `infra/terraform/stacks/vsphere-llamacpp/` exist.
- `tests/unit/test_vsphere_provider.py` covers enum, provider info, generator shape, and lazy dependency behavior.

Credentials must flow through the configured secrets path or operator environment at runtime. They must not be committed into stack files, tfvars files, plans, state, reports, or screenshots.

---

## 7. Rollout status

- Phase 0 is implemented through escape and validation tests.
- Phase 1 is implemented for the checked-in module and stack layout, with legacy generator compatibility still present.
- Phase 2 is implemented for VMware/vSphere provider metadata, generator behavior, stacks, and unit tests.
- Phase 3 is implemented as env-gated vLLM and llama.cpp E2E coverage plus smoke-test reporting. Live cloud coverage depends on operator credentials and is intentionally skip-by-default in normal CI.
- Phase 4 remains open. The completion condition is that no `_generate_*` method emits inline resource HCL and all supported paths use module-backed stacks or tfvars composition.

New provider or engine work should extend the checked-in module and stack layout first, then add smoke-test evidence for the real provider path.

---

---

## 8. Security considerations

- **Credentials via runtime secret sources, never committed files.** Provider credentials must come from configured secret stores or operator runtime environment and must not be written into stack files, state, plans, reports, or screenshots.
- **tfvars discipline.** Real values never enter `infra/terraform/`. Example files may contain placeholders only. Real `.tfvars` files are ignored.
- **State and plan hygiene.** `.gitignore` covers `*.tfstate`, `*.tfstate.*`, `*.tfplan`, `.terraform/`, `.terraform.lock.hcl`, the shared plugin cache, and real tfvars files.
- **HCL-injection removal path.** Module-backed stacks and tfvars avoid generating HCL structure from arbitrary strings. Legacy inline generator paths remain compatibility code until Phase 4 removes them.
- **Provider plugin pinning.** Provider versions are pinned centrally in `infra/terraform/versions.tf` and enforced by the version-check target.
- **Policy checks.** OPA policy material under `infra/terraform/policies/` is part of the infrastructure review surface and should be updated with any new provider trust requirement.

---

## 9. Testing strategy

| Test file | Scope | Phase |
|---|---|---|
| `tests/unit/test_terraform_validate.py` | Round-trip: `TerraformGenerator` output for every implemented provider×engine passes `terraform fmt -check` + `terraform validate`. Skips if `terraform` binary absent. | 0 / 1 |
| `tests/unit/test_terraform_modules.py` | Every module under `infra/terraform/modules/` and every stack under `infra/terraform/stacks/` passes `terraform fmt -check` + `terraform validate`. Asserts that stacks reference modules by `source`, not inline resources. | 1 |
| `tests/unit/test_vsphere_provider.py` | `ComputeProvider.VMWARE` exists; `providers.py` has the vsphere `ProviderInfo` with the three env vars; `_generate_vsphere` produces HCL that references `../modules/vllm-server`; `pyvmomi` is lazily imported (assert `pyvmomi` not importable at module top-level). | 2 |
| `tests/unit/test_terraform_escape.py` | Phase 0 escape helper: config fields containing `"`, `${`, `\n`, `}` produce valid tfvars values (quoted, escaped) and never produce valid HCL structure. | 0 |
| `tests/e2e/providers/test_vllm_e2e.py` | Real vLLM spin-up gated on `VLLM_BASE_URL` + `VLLM_E2E_SPAWN`. Validates `/v1/models`, `/v1/completions` round-trip. Skips if env vars unset. | 3 |
| `tests/e2e/providers/test_llamacpp_e2e.py` | Same shape for `llama_cpp.server`. | 3 |

All unit tests must be hermetic (no real cloud calls). `terraform validate` is
the boundary: it exercises real HCL parsing without provisioning. E2E tests
are env-gated and never run in default CI.

---

## 10. Open work

1. **Legacy generator cleanup.** Remove inline resource HCL from compatibility generator paths once every supported provider and engine path is module-backed.
2. **Live provider evidence.** Keep adding real provider smoke output for credentials and platforms that operators exercise manually.
3. **Backend hardening.** Keep daemon HTTP backend behavior documented with deployment profiles, and keep local state limited to ephemeral development paths.
4. **Policy coverage.** Extend OPA policies whenever a provider adds a new trust boundary, network exposure, or credential type.

These items do not block the static module layout. They are the current follow-up work after the Option B migration.

---

## Appendix A - Why not just write a YAML schema and generate HCL from it

A YAML or DSL to HCL generator would move string interpolation from Python to another templating layer without making the runnable infrastructure reviewable in native `.tf` form. The checked-in modules and tfvars composition already solve the reviewability problem in the language Terraform natively speaks.

## Appendix B - Related files verified

- `infra/terraform/versions.tf` - central provider pinning.
- `infra/terraform/modules/` - shared engine, platform, network, cost, and onboarding modules.
- `infra/terraform/stacks/` - provider and engine stacks.
- `infra/terraform/policies/` - OPA policy material for infrastructure checks.
- `src/general_ludd/infra/terraform.py` - compatibility generator and module-backed provider generation.
- `src/general_ludd/infra/compute.py` - compute provider, GPU type, and inference engine definitions.
- `src/general_ludd/infra/providers.py` - provider registry and metadata.
- `src/general_ludd/infra/local_inference.py` - local vLLM and llama.cpp serving manager.
- `src/general_ludd/infra/deployment.py` - deployment lifecycle integration.
- `tests/unit/test_terraform_validate.py` - generated HCL validation.
- `tests/unit/test_terraform_modules.py` - module and stack validation.
- `tests/unit/test_vsphere_provider.py` - vSphere provider coverage.
- `tests/unit/test_terraform_escape.py` - tfvars and escaping coverage.
- `.gitignore` - Terraform state, plan, plugin cache, and real tfvars hygiene.
