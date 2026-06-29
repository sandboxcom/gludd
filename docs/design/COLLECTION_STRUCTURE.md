# Collection Structure — Terraform + OPA Content

Status: **stable** · Last updated 2026-06-29

This document specifies the layout of terraform and OPA policy content shipped
inside a gludd ansible-galaxy collection, and the import-time contract enforced
by `src/general_ludd/collections/importer.py` (`TerraformCollectionImporter`).

## 1. On-disk layout

A collection that ships terraform/OPA content MUST place it under
`plugins/terraform/`:

```
<collection-root>/
├── galaxy.yml                                   # ansible-galaxy metadata
└── plugins/
    └── terraform/
        ├── modules/                             # user terraform modules
        │   └── <name>/*.tf
        ├── stacks/                              # user terraform stacks (compositions)
        │   └── <name>/*.tf
        ├── policies/                            # additive OPA deny rules
        │   └── *.rego
        └── providers.yaml                       # declared provider dependencies
```

Reference layout lives at
`collections/ansible_collections/general_ludd/agent/plugins/terraform/`.

`modules/` and `stacks/` hold user terraform; each immediate child directory is
`tall` (one module per directory). `policies/` holds Rego files that ADD deny
rules (see §2). `providers.yaml` declares the provider sources the collection
depends on; it is the terraform analogue of ansible's `requirements.yml`.

## 2. Core-vs-user OPA relationship (additive-only)

The operator ships core OPA policies at `infra/terraform/policies/core.rego`
(`package main`, set `deny[level]`). At evaluation time `conftest test` is
invoked with BOTH policy paths:

```
conftest test -p infra/terraform/policies/ -p <collection>/plugins/terraform/policies/ <plan.json>
```

User policies in `plugins/terraform/policies/*.rego` therefore live in the SAME
`package main` and target the SAME `deny[level]` set. Rego sets are additive —
user rules can only ADD reasons to the deny set; they CANNOT subtract or
overwrite core denials.

Forbidden forms (rejected at import time):

- `deny -= "..."`   — subtraction of a core denial
- `deny += "..."`   — set mutation, never idiomatic for a deny set
- `deny := ...`     — assignment / overwrite
- `deny = "..."`    — assignment / overwrite

Enforced by the regex `_DENY_REASSIGN_RE = re.compile(r"deny\s*[-+]?=")` in
`importer.py` and pinned by
`tests/unit/test_collection_terraform_layout.py::test_collection_policy_cannot_override_core_deny`.

## 3. Version & trust model

The operator anchors trust in
`infra/terraform/policies/data.json`:

```json
{
  "gludd": {
    "provider_registry": "registry.terraform.io",
    "provider_trust_list": [
      "registry.terraform.io/hashicorp/aws",
      "registry.terraform.io/hashicorp/google",
      "registry.terraform.io/hashicorp/azurerm",
      "registry.terraform.io/hashicorp/vsphere",
      "registry.terraform.io/hashicorp/random",
      "registry.terraform.io/hashicorp/null"
    ]
  }
}
```

A collection declares its provider surface in TWO places, both intersected with
the operator trust list at import:

1. `galaxy.yml` → `terraform_provider_trust: [<provider>, ...]` — the
   collection's *intent* declaration. Example (this collection):

   ```yaml
   terraform_provider_trust:
     - hashicorp/vsphere
   ```

2. `plugins/terraform/providers.yaml` → `providers: [{name, source, version}]`
   — the runtime dependency list.

   ```yaml
   providers:
     - name: vsphere
       source: hashicorp/vsphere
       version: "~> 2.8"
   ```

Matching is name-suffix tolerant: `hashicorp/vsphere` matches
`registry.terraform.io/hashicorp/vsphere`. Any provider not in the operator
trust list is an import ERROR (not a warning) — see `_check_provider_trust()` in
`importer.py`.

## 4. Import-time validations

`TerraformCollectionImporter.import_collection()` runs FOUR checks. An empty
result list means the collection passed. The four checks:

| # | Check                          | Severity on failure | Mechanism                                                   |
|---|--------------------------------|---------------------|-------------------------------------------------------------|
| 1 | `terraform validate` per module/stack dir | `error`  | `subprocess.run(["terraform","validate"], cwd=<dir>)` — skipped (warn) if terraform binary absent |
| 2 | `opa check` on every `*.rego`  | `error`             | `subprocess.run(["opa","check",...])` — skipped (warn) if opa binary absent |
| 3 | No `deny -=` / `deny +=` / `deny =` reassignment | `error`  | regex over source text — runs even without opa installed |
| 4 | Provider trust cross-check     | `error`             | intersect `galaxy.yml#terraform_provider_trust` AND `providers.yaml#providers` with operator trust list |

Validation 1–2 surface as warnings (not errors) when the respective binary is
absent so CI without `terraform`/`opa` installed does not false-fail. Validations
3–4 always run and always error on failure.

## 5. Reference

- Implementation: `src/general_ludd/collections/importer.py`
- Core policies: `infra/terraform/policies/core.rego`
- Trust anchor: `infra/terraform/policies/data.json`
- Tests: `tests/unit/test_collection_terraform_layout.py`,
  `tests/unit/test_opa_policies.py`
- Example collection content:
  `collections/ansible_collections/general_ludd/agent/plugins/terraform/`
