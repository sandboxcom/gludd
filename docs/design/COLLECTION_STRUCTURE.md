# Collection Structure — Terraform + OPA Content

Status: **stable** · Last updated 2026-07-29

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
      "registry.terraform.io/vmware/vsphere",
      "registry.terraform.io/hashicorp/random",
      "registry.terraform.io/hashicorp/null"
    ]
  }
}
```

A collection declares its provider surface in
`plugins/terraform/providers.yaml` as
`providers: [{name, source, version}]`:

```yaml
providers:
  - name: vsphere
    source: vmware/vsphere
    version: "~> 2.8"
```

`galaxy.yml` contains only keys from Ansible Galaxy's collection schema.
Provider metadata is intentionally kept in the collection-local Terraform
manifest so `ansible-galaxy collection build` does not emit unknown-key
warnings.

Matching is name-suffix tolerant: `vmware/vsphere` matches
`registry.terraform.io/vmware/vsphere`. Any provider not in the operator
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
| 4 | Provider trust cross-check     | `error`             | intersect each `providers.yaml#providers[].source` with the operator trust list |

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

## 6. Project-local collections

A project may ship its own ansible-galaxy collection alongside the bundled
`general_ludd.agent` collection. Project-local collections hold
project-specific business logic — internal deploy patterns, team-private
automation, project-specific terraform modules and OPA policies — and travel in
the project repository rather than with gludd releases.

### On-disk layout

Project-specific content lives at:

```
<project_dir>/.gludd/collections/ansible_collections/<namespace>/<collection>/
├── galaxy.yml                         # ansible-galaxy metadata
├── roles/
│   └── <role_name>/                   # project-specific roles
├── plugins/
│   ├── modules/                       # project-specific modules
│   ├── module_utils/                  # shared module helpers
│   └── terraform/                     # project terraform/OPA content (§1 layout)
│       ├── modules/
│       ├── stacks/
│       ├── policies/
│       └── providers.yaml
└── ...
```

This mirrors the bundled collection layout (galaxy.yml, roles/,
plugins/modules/, plugins/module_utils/, plugins/terraform/) but is scoped to a
single project under `<project_dir>/.gludd/collections/`.

### 3-tier precedence contract

When gludd resolves an FQCN such as `acme.project.deploy_x`, it searches three
roots in order — first match wins:

| # | Root                                                | Precedence | Purpose                                                            |
|---|-----------------------------------------------------|------------|--------------------------------------------------------------------|
| 1 | `<project_dir>/.gludd/collections/ansible_collections/...` | Highest    | Project-specific business logic, team-private automation           |
| 2 | `~/.local/share/gludd/collections/ansible_collections/...` | Middle     | Operator-personal overrides shared across projects                 |
| 3 | `general_ludd.agent` (bundled with gludd)           | Lowest     | General-purpose, shareable roles/modules shipped with gludd        |

First match wins; a role/module found in an earlier root shadows the same FQCN
in a later root. This mirrors the Ansible `ANSIBLE_COLLECTIONS_PATH`
multi-root resolution model.

### Scaffolding via `gludd project init`

```
gludd project init --namespace <ns> [--collection <name>] [--force] [PROJECT_DIR]
```

- Creates the layout above under
  `<project_dir>/.gludd/collections/ansible_collections/<ns>/<collection>/`.
- Writes a `collection:` section into `<project_dir>/.gludd/config.yml` so the
  daemon registers the project-local collection at startup.
- `PROJECT_DIR` defaults to the current working directory.
- `--collection` defaults to `project`.
- `--force` overwrites an existing scaffold.

### When to add a role here vs the bundled collection

| Target                                       | Use for                                                                                        |
|----------------------------------------------|------------------------------------------------------------------------------------------------|
| Project-local (`acme.project.*`)             | Project-specific business logic, internal deploy patterns, team-private automation, project-specific terraform modules / OPA policies. Lives in the repo so it travels with the codebase. |
| Bundled (`general_ludd.agent.*`)             | General-purpose SDLC automation (`write_tests`, `security_review`, `ci_pipeline_repair`, …). Shared across all projects and shipped with gludd releases.       |

Rule of thumb: if the automation is project-specific or carries project
secrets/context, it belongs in the project-local collection; if it is useful to
every gludd project regardless of codebase, it belongs in the bundled
collection.
