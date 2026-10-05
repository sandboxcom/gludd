# Project Collections Precedence Contract

**Status:** implemented
**Last updated:** 2026-06-29
**Resolver:** `src/general_ludd/ansible/paths.py` (`resolve_collections_paths`)
**Diagnostic CLI:** `gludd project paths [PROJECT_DIR] [--json]`
**Init CLI:** `gludd project init --namespace <ns> [--collection <name>]`

## Problem

gludd ships a bundled ansible collection — `general_ludd.agent` — that carries
the generic, project-agnostic roles and modules the daemon invokes from its
work-type playbooks (`agent_task`, `implement_change`, `gludd_facts`, …). As
operators started running gludd against real codebases, two needs emerged that
the bundled collection alone could not satisfy:

1. **Project-specific automation.** A repo often has deployment wrappers,
   on-call remediation roles, or legacy-interop playbooks that are *not*
   shareable across projects but must be callable from the same gludd-driven
   playbooks.
2. **Per-project overrides of bundled behaviour.** The generic
   `general_ludd.agent.implement_change` may be wrong for a particular repo
   (custom commit conventions, mandatory ticket-link hooks, etc.). Operators
   need a way to *shadow* a bundled FQCN with project-local code without
   forking gludd itself.

gludd addresses both with a 3-tier collections precedence contract, layered
on top of standard ansible-core collection resolution.

## Contract — 3-tier search order

`resolve_collections_paths(project_root)` returns the ordered list:

| # | Source label | Path                                                            |
|---|--------------|-----------------------------------------------------------------|
| 1 | `project`    | `<project_root>/.gludd/collections/`                            |
| 2 | `user`       | `${XDG_CONFIG_HOME:-~/.config}/gludd/collections/`              |
| 3 | `bundled`    | `<install_root>/collections/`                                   |

The bundled tier is ALWAYS present (install-time fallback). The project and
user tiers are included only when their directories exist on disk — a project
without `.gludd/collections/` simply skips the project tier silently.

### Override rule (shadowing)

A FQCN present in a higher-precedence tier **shadows** the same FQCN in every
lower tier. This is exactly the behaviour ansible-core gives you when
`ANSIBLE_COLLECTIONS_PATH` is a colon-separated list ordered project-first,
which is what `to_ansible_env()` produces.

### Resource name resolution (and the same-namespace caveat)

FQCNs in ansible are always fully qualified: `namespace.collection.resource`.
To override `general_ludd.agent.implement_change` at the project tier, the
project-local collection MUST:

1. Be named `general_ludd.agent` (same namespace + collection name), AND
2. Live at
   `<project>/.gludd/collections/ansible_collections/general_ludd/agent/`.

This is the only way ansible will resolve the FQCN to the project copy
before falling through to the bundled copy. A project collection named
`acme.project.implement_change` is a *different* resource — it adds a new
role, it does not shadow the bundled one.

| Goal                              | Collection namespace+name to use             |
|-----------------------------------|----------------------------------------------|
| Shadow `general_ludd.agent.<r>`   | `general_ludd.agent` (same name, project tier) |
| Add a net-new project-only role   | any `<projns>.<projcoll>` you like           |

The trade-off is deliberate: same-name override requires the operator to
opt into namespace+collection collision (signalling intent), but unlocks
transparent shadowing of any bundled resource without touching playbooks.

### Direct callability

Roles and modules in ANY tier are callable from any playbook by FQCN with no
special registration, `requirements.yml`, or `ansible-galaxy install` step.
gludd's resolver puts the tier paths on `ANSIBLE_COLLECTIONS_PATH` at
adapter init and on every project switch; ansible-core does the rest. This
is standard ansible-collections behaviour — gludd does not customise it.

## Custom business logic workflow

Project-local collections are the preferred place for internal automation that should not ship in the bundled gludd collection. Use them for company deployment wrappers, internal service catalog lookups, policy evidence formatting, remediation roles, and private Terraform helper material.

Use this decision rule:

- Use a custom namespace and collection when adding net-new project-only behavior.
- Use `general_ludd.agent` in the project tier only when intentionally shadowing a bundled role or module.
- Keep reusable, project-agnostic behavior in the bundled collection and cover it with repo tests.
- Keep private or organization-specific behavior in the project collection and cover it with the project test suite.

Suggested workflow:

1. Scaffold the project collection with `gludd project init --namespace <ns> --collection <name>`.
2. Put private roles under `roles/`, modules under `plugins/modules/`, shared helpers under `plugins/module_utils/`, and Terraform conventions under `plugins/terraform/`.
3. Call new behavior by FQCN from project playbooks.
4. Shadow bundled behavior only by using the same FQCN in the project tier.
5. Run `gludd project paths --json` to confirm the project tier resolves before user and bundled tiers.
6. Capture smoke-test or playbook output when debugging project-specific behavior so a repair agent can see the exact role, module, variables, events, and logs.

## Initialisation

`gludd project init --namespace <ns> [--collection <name>] [--force]` scaffolds
the project-local collection at
`<project_dir>/.gludd/collections/ansible_collections/<ns>/<coll>/`, with:

- `galaxy.yml`, `README.md`, `.gitignore`
- empty skeleton dirs: `roles/`, `plugins/modules/`, `plugins/module_utils/`,
  `plugins/terraform/`
- writes the chosen namespace+name back to `.gludd/config.yml` under a
  `collection:` key, so the daemon and tooling can read it without re-parsing
  galaxy.yml.

### Customizing `gludd project init`

The CLI is a **thin wrapper** over the `general_ludd.agent.project_init` role
(located at
`collections/ansible_collections/general_ludd/agent/roles/project_init/`).
The role is the single source of truth for the scaffold shape — all file
creation lives there. The Python CLI does only arg parsing + role invocation
via `AnsibleRunnerAdapter.run_playbook("playbooks/project_init.yml", ...)`.

Operators can override the scaffold for a single project by placing a
same-FQCN role at
`<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/`.
The project-collection precedence system (see *Override rule* above) shadows
the bundled role automatically. The CLI keeps working unchanged — it invokes
`general_ludd.agent.project_init`, and ansible-core resolves that FQCN to the
project-local copy first.

#### Worked example — add a pre-commit hook to the scaffold

A project that wants every scaffolded collection to ship a `.pre-commit-config.yaml`
overrides the role:

1. Scaffold the project-local collection with the SAME FQCN as bundled:

   ```text
   $ cd /Users/x/acme-internal
   $ gludd project init --namespace general_ludd --collection agent --force
   ```

2. Drop the override role at the project tier:

   `<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/tasks/main.yml`:

   ```yaml
   ---
   # Copy the bundled tasks/main.yml verbatim (so the base scaffold still
   # happens), then append project-specific extras:
   - name: Write pre-commit config from template
     ansible.builtin.template:
       src: pre-commit-config.j2
       dest: "{{ _pi_collection_root }}/.pre-commit-config.yaml"
       mode: "0644"
       force: "{{ force | bool }}"
   ```

   `<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/templates/pre-commit-config.j2`:

   ```yaml
   repos:
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.5.0
       hooks:
         - id: ruff
   ```

3. Re-run init — the project-local role wins:

   ```text
   $ gludd project init --namespace acme
   ```

   The scaffold now includes `.pre-commit-config.yaml` next to `galaxy.yml`,
   with no change to the CLI, the playbook, or the daemon.

## Operations — daemon resolution

The daemon resolves the 3-tier path at TWO moments:

1. **Startup.** `daemon.py` reads the active project's `.gludd/` directory
   from `find_project_gludd_dir()` and constructs an
   `AnsibleRunnerAdapter(default_env=...)` whose env includes the resolved
   `ANSIBLE_COLLECTIONS_PATH` / `ANSIBLE_ROLES_PATH`.
2. **Project switch.** `AnsibleRunnerAdapter.set_project_root(new_root)`
   re-runs `_refresh_collections_env()`, which calls
   `resolve_collections_paths(project_root=new_root)` and overwrites
   `_collections_env`. The next `run_playbook()` call picks up the new
   project's tier-1 paths with no adapter re-instantiation.

`run_playbook()` merges the resolved collections env into the per-call
`extra_env` it hands to `CoreAnsibleRunner`, with caller-supplied `env=`
taking highest precedence, then the collections env, then the adapter's
`default_env`. This keeps gludd's process env pristine across concurrent
playbook invocations (no `os.environ` mutation).

## Diagnostic CLI

`gludd project paths [PROJECT_DIR] [--json]` prints the resolved precedence
table so operators can verify the order before debugging a "wrong role
won" incident:

```text
Collection search path (highest precedence first):
  1. PROJECT   /Users/x/acme-internal/.gludd/collections/   (exists, 3 roles, 5 modules)
  2. USER      /Users/x/.config/gludd/collections/          (exists, 1 role, 0 modules)
  3. BUNDLED   /Users/shawnwilson/gludd/collections/        (exists, 70 roles, 18 modules)
```

With `--json` the same data is emitted as a list of objects (`source`,
`path`, `exists`, `roles`, `modules`) for scripting / CI assertions.

Role and module counts are computed by scanning the tier's
`ansible_collections/*/*/roles/*/` and `ansible_collections/*/*/plugins/modules/*.py`
globs — they reflect what is actually on disk, not a cached registry.

## Terraform / OPA content

Project collections may also carry Terraform / OPA content under
`plugins/terraform/{modules,stacks,policies}/`. These follow the SAME
precedence contract when the OPA importer scans a collection directory: the
importer walks `resolve_collections_paths()` in order and the first hit on a
given policy/module FQCN wins. See `docs/design/COLLECTION_STRUCTURE.md`
for the directory layout and `docs/design/TERRAFORM_INFRA_STRUCTURE.md`
for the importer integration.

## Worked example — `acme-internal` shadowing `deploy_legacy_widget`

Setup:

- Bundled collection ships a stub role `general_ludd.agent.deploy_legacy_widget`
  that prints "not implemented for generic case".
- The `acme-internal` project has a real deployment routine for the legacy
  widget and wants gludd-driven playbooks to call the project copy.

Step 1 — scaffold the project collection with the SAME FQCN as bundled:

```text
$ cd /Users/x/acme-internal
$ gludd project init --namespace general_ludd --collection agent --force
Scaffolded project collection at: .gludd/collections/ansible_collections/general_ludd/agent
FQCN prefix: general_ludd.agent.<role_or_module>
Config updated: .gludd/config.yml
```

Resulting tree (only the relevant leaves shown):

```text
/Users/x/acme-internal/
└── .gludd/
    ├── config.yml
    └── collections/
        └── ansible_collections/
            └── general_ludd/
                └── agent/
                    ├── galaxy.yml
                    └── roles/
                        └── deploy_legacy_widget/
                            └── tasks/
                                └── main.yml    ← real acme deploy logic
```

`roles/deploy_legacy_widget/tasks/main.yml`:

```yaml
---
- name: Set role source for runtime precedence assertion
  ansible.builtin.set_fact:
    role_source: project

- name: Run acme-internal's legacy widget deploy
  ansible.builtin.command: /usr/local/acme/deploy-legacy-widget.sh
  changed_when: true
```

Step 2 — verify the precedence:

```text
$ gludd project paths /Users/x/acme-internal
Collection search path (highest precedence first):
  1. PROJECT   /Users/x/acme-internal/.gludd/collections/   (exists, 1 role, 0 modules)
  2. USER      /Users/x/.config/gludd/collections/          (missing)
  3. BUNDLED   /Users/shawnwilson/gludd/collections/        (exists, 70 roles, 18 modules)
```

Step 3 — call the role from any playbook by FQCN. The project copy wins:

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: general_ludd.agent.deploy_legacy_widget
```

At runtime ansible-core resolves `general_ludd.agent.deploy_legacy_widget`
by walking `ANSIBLE_COLLECTIONS_PATH` in order. The project tier's copy is
found first and executed; the bundled stub is never reached. Asserting
`role_source == "project"` after the run proves the precedence held (this
is exactly what `tests/integration/test_project_collection_precedence.py`
does).

## Enforcement

The contract is enforced at three layers:

1. **Resolver unit tests** — `tests/unit/test_collection_paths.py` pins
   `resolve_collections_paths`, `to_ansible_env`, `to_ansible_cfg`,
   `find_resource`, and the `AnsibleRunnerAdapter` wiring.
2. **Integration test** — `tests/integration/test_project_collection_precedence.py`
   runs a real playbook through `AnsibleRunnerAdapter.run_playbook()` and
   asserts the runtime fact (`role_source`) reflects the winning tier, not
   just that the path-resolution order is correct.
3. **Diagnostic CLI tests** — `tests/unit/test_project_paths_cli.py` pins
   the human-readable table, JSON output, per-tier counts, and the
   missing-dir skip behaviour.

A change to the resolver that breaks precedence will fail at the path layer
(unit) AND at the runtime layer (integration), making precedence regressions
structurally visible before they ship.
