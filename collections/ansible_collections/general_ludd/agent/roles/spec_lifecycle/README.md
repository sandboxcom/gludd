# general_ludd.agent.spec_lifecycle

Spec-Driven Development lifecycle role. The gludd equivalent of the opencode
`spec.md` (deep-spec) skill — a 3-stage pipeline

    drafts/  --(approve_task)-->  active/  --(Review Gate + complete_task)-->  archive/

where each task carries the **A-B-C** documentation flow:

| Document               | Stage written | Purpose                                  |
| ---------------------- | ------------- | ---------------------------------------- |
| `APPROACH.md`          | create_task   | **How** — the execution plan (immutable once approved) |
| `BUSINESS_CONTEXT.md`  | create_task   | **Why** — problem, outcome, acceptance criteria |
| `COMPLETION_REPORT.md` | complete_task | **Evidence** — shipped state + AC evidence (the "C" doc) |

## Operations

The role dispatches on the `operation` variable.

| Operation              | Effect                                                                  | Model call? |
| ---------------------- | ----------------------------------------------------------------------- | ----------- |
| `init`                 | Bootstrap `.spec.md/{drafts,active,archive,_artifacts}/` + `tracking.json` + `memory.md` | no |
| `create_task`          | Draft `drafts/<task>/{APPROACH.md,BUSINESS_CONTEXT.md,task.yml}` — **no app code** | no |
| `approve_task`         | Move `drafts/<task>` → `active/<task>`; mark `stage: active`            | no          |
| `complete_task`        | Review Gate (every AC `checked: true`), then move `active` → `archive` + write `COMPLETION_REPORT.md` | no |
| `discard_task`         | Move `drafts/active` → `archive/<task>` + `DISCARDED.yml` marker        | no          |
| `list`                 | Read-only enumeration of every task across stages → JSON artifact       | no          |
| `revise`               | Move `active/<task>` → `drafts/<task>` (re-plan before Review Gate)     | no          |
| `repair`               | Rebuild `tracking.json` from on-disk state (idempotent)                 | no          |
| `interview`            | One-question-at-a-time APPROACH refinement (deep-spec "Sabatina")       | **yes**     |
| `map_codebase`         | Deep-scan repo → write `AGENTS.md` map                                  | **yes**     |
| `diagram_architecture` | Mermaid `ARCHITECTURE.md` per task                                      | **yes**     |

Model-backed operations are skipped with a clear opt-in message unless
`enable_model_call=true`.

## Variables

| Variable              | Default                          | Required | Description                                    |
| --------------------- | -------------------------------- | -------- | ---------------------------------------------- |
| `operation`           | _(none — user MUST set)_         | yes      | One of the operations above.                   |
| `spec_root`           | `{{ playbook_dir }}/.spec.md`    | no       | Root of the spec tree.                         |
| `artifact_dir`        | `{{ spec_root }}/_artifacts`     | no       | Where result JSON artifacts are written.       |
| `task_name`           | `""`                             | see note | Required for create/approve/complete/discard/revise. |
| `task_size`           | `medium` (`small`/`medium`/`large`) | no    | Drives doc ceremony (deep-spec sizing).        |
| `discard_reason`      | `no longer needed`               | no       | Recorded in `DISCARDED.yml` on discard_task.   |
| `daemon_url`          | `http://localhost:8000`          | no       | Gludd daemon URL (model ops only).             |
| `psk`                 | `""`                             | no       | Daemon pre-shared key (prefer `GLUDD_AUTH_PSK`).    |
| `model_profile`       | `""`                             | no       | Model profile for agent-backed ops.            |
| `agent_max_iterations`| `10`                             | no       | Cap for `gludd_agent_run` calls.               |
| `enable_model_call`   | `false`                          | no       | Opt-in for interview/map/diagram. **Safe-by-default.** |
| `enable_git_write`    | `false`                          | no       | This role never mutates git; flag is reserved for wrapping roles. |
| `capability_role`     | `spec_lifecycle`                 | no       | Per-role default-DENY db policy identity.      |

## Safety

- **Non-destructive by default.** No git mutations. The `enable_git_write`
  flag exists for forward compatibility and is unused by this role.
- **Model ops are opt-in.** `interview`, `map_codebase`, and
  `diagram_architecture` are skipped unless `enable_model_call=true`.
- **Check-mode safe.** File ops (`file`/`copy`/`stat`/`find`/`slurp`) honor
  `ansible_check_mode` natively; `gludd_facts` and `gludd_agent_run` are
  explicitly skipped in check mode.
- **Review Gate enforced.** `complete_task` asserts that every
  `acceptance_criteria[].checked` in `task.yml` is `true` before archiving.

## Dependencies

None. The gludd modules (`gludd_facts`, `gludd_agent_run`) are dynamically
loaded via `ANSIBLE_COLLECTIONS_PATH`.

## Examples

### 1. `init` — bootstrap the spec tree

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.spec_lifecycle
      operation: init
      spec_root: "{{ playbook_dir }}/.spec.md"
```

Creates:

```
.spec.md/
├── drafts/
├── active/
├── archive/
├── _artifacts/
│   └── spec_lifecycle_init.json
├── tracking.json
└── memory.md
```

### 2. `create_task` — draft a new task

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.spec_lifecycle
      operation: create_task
      task_name: add-permissions-intersection
      task_size: medium
      spec_root: "{{ playbook_dir }}/.spec.md"
```

Creates `drafts/add-permissions-intersection/` with the A-B-C skeleton
(`APPROACH.md`, `BUSINESS_CONTEXT.md`, `task.yml`). **No application code
is produced.** A result artifact is written to
`_artifacts/spec_lifecycle_add-permissions-intersection.json`.

Follow-up flow: `approve_task` → implement in another role (e.g.
`implement_change`) → tick every AC in `task.yml` → `complete_task`
(runs the Review Gate, archives, writes `COMPLETION_REPORT.md`).
