# gludd_update

Operator self-update surface — **#81 part 3**. Turns an
`update gludd: <text>` request into a **prioritized** todo.

This is the Ansible counterpart of the CLI `scripts/gludd_update.py`. Both share
one priority ladder so the operator gets the same triage whether they shell out
to the CLI or run this role.

## Flow

```
"update gludd: <text>"  ──►  classify target kind  ──►  derive priority  ──►  gludd_db todo_create
                              (config/role/code)        (high/medium/low)      (queue=core)
```

1. **Parse** — strip a leading `update gludd:` prefix from `update_request`.
2. **Classify** — `update_target_kind` is the request's target kind. (The real
   classification is done by the `UpdateRequestRouter` in parts 1/2; until the
   daemon `/api` endpoint wires it in, the role accepts the kind directly.)
3. **Prioritize** — derive priority + a `needs_review` flag from the kind:

   | target kind     | priority | needs_review | rationale                         |
   |-----------------|----------|--------------|-----------------------------------|
   | `config`/`yaml` | `high`   | no           | low-risk knob → fast-track        |
   | `role`          | `medium` | no           | behavior change, bounded blast    |
   | `code` / other  | `low`    | **yes**      | self-modifies code → human review |

4. **Create** — `gludd_db todo_create` posts the prioritized todo to the daemon
   (`POST /api/todos`, never raw SQLite), gated by write_back.

## Variables

| Variable | Default | Description |
|---|---|---|
| `update_request` | `"update gludd: improve the system"` | The operator request (full `update gludd: …` form or bare body) |
| `update_target_kind` | `config` | Classified target kind: `config`/`yaml`/`role`/`code` |
| `update_priority_map` | see defaults | Kind → priority ladder (mirrors the CLI) |
| `update_priority_default` | `low` | Priority for an unclassified kind |
| `update_review_kinds` | `[code]` | Kinds that set `needs_review` |
| `write_back` | `false` | Create the todo in gludd_db (else report-only) |
| `capability_role` | `gludd_update` | Logical capability label for this role |
| `capability_db_ops` | `[todo_create]` | DB ops this role declares it needs (#44 policy) |
| `db_capability_role` | `operator` | Role handed to gludd_db's capability gate (must be granted `todo_create`) |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |
| `psk` | `""` | Pre-shared key |
| `artifact_dir` | `/tmp/gludd-update` | Output directory for the audit artifact |

## Capability policy (#44)

`gludd_db todo_create` is gated by the **default-DENY** per-role capability
policy (`module_utils/capability_policy.py`). This role declares the db ops it
needs (`capability_db_ops: [todo_create]`) under the logical label
`capability_role: gludd_update`.

Because the policy is default-DENY and `gludd_update` is not (yet) a built-in
granted role, the create is routed through `db_capability_role` — defaulting to
`operator`, a built-in role the policy grants `todo_create`. To run the create
under the `gludd_update` label itself, grant it via the policy's config override
(`{"roles": {"gludd_update": {"db_ops": ["todo_create"]}}}`) and set
`db_capability_role: gludd_update`.

## Artifacts

- `gludd_update.json` — the emitted todo spec (title, request_text, target_kind,
  priority, needs_review, capability metadata) for audit.

## Safe-by-default

Report-only unless `write_back: true`. With `write_back` off (the default) the
role classifies, prioritizes, and writes the audit artifact but creates no todo.

## See also

- `scripts/gludd_update.py` — the operator CLI (same priority ladder; emits the
  spec as JSON or via an injected todo-creator callable).
