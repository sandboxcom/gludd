# project_init

Scaffolds a project-local ansible collection under
`<project_dir>/.gludd/collections/ansible_collections/<namespace>/<collection_name>/`.

The `gludd project init` CLI is a **thin wrapper** over this role — it does
only arg parsing and role invocation. **This role is the single source of
truth for the scaffold shape.** All file I/O lives here.

## What it creates

```
<project_dir>/.gludd/
├── config.yml                              # collection: section merged in
└── collections/
    └── ansible_collections/
        └── <namespace>/
            └── <collection_name>/
                ├── galaxy.yml
                ├── README.md
                ├── .gitignore
                ├── roles/.gitkeep
                └── plugins/
                    ├── modules/.gitkeep
                    ├── module_utils/.gitkeep
                    └── terraform/.gitkeep
```

Refuses to overwrite an existing `galaxy.yml` unless `force: true`.

## Variables

| Variable         | Default            | Description                                            |
|------------------|--------------------|--------------------------------------------------------|
| `namespace`      | `""` (required)    | Galaxy namespace — empty triggers validation failure.  |
| `collection_name`| `project`          | Collection name.                                       |
| `project_dir`    | `{{ playbook_dir }}` | Project root to scaffold into.                       |
| `force`          | `false`            | Overwrite existing `galaxy.yml`.                       |
| `license`        | `MIT`              | License written to `galaxy.yml`.                       |
| `version`        | `1.0.0`            | Version written to `galaxy.yml`.                       |

## Overriding the scaffold (project-collection precedence)

To customize the scaffold for a single project — add a pre-commit hook, change
the directory layout, write extra template files, etc. — drop a same-FQCN role
at:

```
<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/
```

The project-collection precedence system shadows the bundled role automatically.
The CLI keeps working unchanged: it invokes `general_ludd.agent.project_init`,
and ansible-core resolves that FQCN to the project-local copy first.

### Worked example — add a pre-commit hook to the scaffold

`<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/tasks/main.yml`:

```yaml
---
- name: Run the bundled scaffold first (import it for the base tree)
  ansible.builtin.import_role:
    name: general_ludd.agent.project_init  # resolves to bundled copy via lower tier
  # NOTE: if you name your override role the same FQCN, this self-references.
  # In that case copy the bundled tasks/main.yml here and append your additions.

- name: Write pre-commit config from template
  ansible.builtin.template:
    src: pre-commit-config.j2
    dest: "{{ _pi_collection_root }}/.pre-commit-config.yaml"
    mode: "0644"
```

Run `gludd project init --namespace acme` and the project-local role wins; the
scaffold now includes the pre-commit hook.

## Direct invocation (no CLI)

```sh
ansible-playbook playbooks/project_init.yml \
  -e namespace=acme \
  -e collection_name=platform \
  -e project_dir=/path/to/proj
```
