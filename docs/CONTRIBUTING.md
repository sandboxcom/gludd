# Contributing

This guide covers the Ansible collection layout so contributors know where
roles, modules, playbooks, and molecule scenarios belong. The layout is
enforced by guardrail tests; deviating from it breaks `make gate`.

## Collection layout

There is **one canonical home** for roles and modules:

```text
collections/ansible_collections/general_ludd/
├── agent/                      # general_ludd.agent — roles + modules
│   ├── roles/<name>/
│   ├── plugins/modules/gludd_<name>.py
│   └── ...
└── formal/                     # general_ludd.formal — TLA+ tooling
```

`ansible.cfg` sets `collections_path = ./collections:...` and does **not** set
`roles_path`. Every role invocation uses the fully-qualified collection name
(FQCN): `general_ludd.agent.<name>`, never the bare name.

Molecule scenarios live in **one central place**, not co-located with roles:

```text
molecule/
├── mock_daemon/server.py            # shared stdlib HTTP stub of the daemon
└── playbooks/
    ├── role_<name>/                 # one scenario per role
    │   ├── molecule.yml
    │   └── default/{prepare,converge,verify}.yml
    └── test_gludd_<name>/           # one scenario per module
        ├── molecule.yml
        └── default/{prepare,converge,verify}.yml
```

## Adding a new role

1. Create the role skeleton under the collection:

   ```text
   collections/ansible_collections/general_ludd/agent/roles/<name>/
   ├── tasks/main.yml
   ├── defaults/main.yml
   ├── meta/main.yml
   └── README.md
   ```

2. Invoke it from any playbook using the FQCN:

   ```yaml
   - name: Run my role
     ansible.builtin.include_role:
       name: general_ludd.agent.<name>
   ```

3. Add a molecule scenario at `molecule/playbooks/role_<name>/` using the
   four-file skeleton:

   ```text
   molecule/playbooks/role_<name>/
   ├── molecule.yml               # see template below
   └── default/
       ├── prepare.yml            # e.g. start molecule/mock_daemon/server.py
       ├── converge.yml           # include_role: general_ludd.agent.<name>
       └── verify.yml             # assert role outputs / side effects
   ```

   `molecule.yml` must point the collection path at the repo's collection tree:

   ```yaml
   ---
   driver:
     name: default
   platforms: []
   provisioner:
     name: ansible
     env:
       ANSIBLE_COLLECTIONS_PATH: "${MOLECULE_PROJECT_DIRECTORY}/collections"
     playbooks:
       prepare: default/prepare.yml
       converge: default/converge.yml
       verify: default/verify.yml
   verifier:
     name: ansible
   ```

4. If you are not writing the scenario yet, add `<name>` to
   `_NOT_YET_COVERED_ROLES` in `tests/integration/test_molecule_coverage.py`.
   Leaving it out entirely fails the coverage test.

## Adding a new module

1. Create the module at
   `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_<name>.py`.
   Module names MUST start with `gludd_`.

2. Invoke it via FQCN: `general_ludd.agent.gludd_<name>`.

3. Add a scenario at `molecule/playbooks/test_gludd_<name>/` using the same
   four-file skeleton as roles (see above). The naming convention
   `test_gludd_<name>` is enforced by `tests/integration/test_molecule_coverage.py`.

4. If deferring the scenario, add `gludd_<name>` to
   `_NOT_YET_COVERED_MODULES` in that test file.

## Adding a new playbook

- Drop it at `playbooks/<name>.yml`.
- All role invocations MUST use FQCN (`general_ludd.agent.<role>`). Bare-name
  `include_role` is rejected by the guardrail tests.
- The playbook must pass `make ansible-syntax` before it lands.

## Running tests

| Command | What it does |
|---|---|
| `make molecule-test SCENARIO=<name>` | Run a single scenario (e.g. `role_scrum_leader`). |
| `make molecule-test-all` | Run every scenario under `molecule/playbooks/`. |
| `make ansible-syntax` | `ansible-playbook --syntax-check` over `playbooks/`. |
| `make ansible-collection-test` | Validate the `general_ludd.agent` collection builds and imports. |
| `make gate` | Full pre-merge gate: lint + typecheck + collect-check + tests. |

## What NOT to do

- **Do NOT create a root `roles/` directory.** The legacy `roles/` was deleted;
  `tests/integration/test_role_layout_single_home.py` fails if it reappears.
- **Do NOT set `roles_path` in `ansible.cfg`.** The same guardrail rejects it.
  Role resolution uses `collections_path` + FQCN only.
- **Do NOT use bare-name `include_role: name: <role>`.** Always FQCN:
  `general_ludd.agent.<role>`.
- **Do NOT co-locate molecule scenarios inside role directories.** They belong
  in the central `molecule/playbooks/` tree.
- **Do NOT add a role or module without a scenario or a coverage-list entry.**
  `tests/integration/test_molecule_coverage.py` will fail the gate.
