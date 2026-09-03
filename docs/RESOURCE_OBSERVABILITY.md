# Resource Observability

Gludd can run several projects, model workers, SearX jobs, and Terraform
operations on one host. Every operation therefore needs an auditable project
namespace and a bounded admission decision; a bare PID or a process-wide lock
is not sufficient evidence of ownership.

## Status contract

`make active-work-status` emits a `resource_observability` object with:

| Field | Meaning |
| --- | --- |
| `project_namespace` | Stable, path-safe project identity. `GLUDD_PROJECT_NAMESPACE` overrides the derived value. |
| `resource_root` | The per-project lock root under `${TMPDIR}/gludd-resources/<project_namespace>`. |
| `lease_owner` | The process identity currently producing the snapshot (`pid:<pid>`). |
| `leases` | Lock paths owned by this project; every path must remain below `resource_root`. |
| `lease_inventory` | One record per project, model, SearX, Terraform, gate, and E2E lease, including its namespaced path and owner. Resource names are unique within a snapshot. |
| `worker_count` | Number of workers admitted for this project at snapshot time. |
| `worker_limit` | Positive hard ceiling for admitted workers. `worker_count` must never exceed it. |
| `leased_worker_count` | Count of unique top-level worker leases after singleton de-duplication. |
| `top_level_worker_count` | Tracked worker roots whose parent is outside the tracked process set. |
| `descendant_process_count` | Tracked child processes (pytest launchers, xdist workers, and shells) excluded from lease admission. |
| `duplicate_worker_leases` | Singleton lease names observed more than once in this namespace; `gate-refresh` is rejected by its project lock and remains visible here if stale processes overlap. |
| `reclaimed_worker_pids` | Gate-refresh PIDs excluded because they do not hold the live project lease. This makes stale orphan trees auditable without counting them as active work. |
| `leased_workers` | Auditable `{pid, task, lease}` records for admitted top-level leases. |

The same JSON document includes a bounded top-level `observed_processes` list.
Each record is a live OS process proven to belong to an atomic observed-command
tree and carries `pid`, `ppid`, `task`, `observer_label`, and `observer_role`.
`make ps` renders those same processes, including `self-improve-observer`,
`self-improve`, and `self-improve-model-worker` roles. The observer status is
not trusted as a PID source by itself: Gludd verifies the owner command and
label against the live process table, verifies the child relationship, follows
only real descendants, ignores unknown fields such as `agent_pid`, and caps the
snapshot. Model-agent activity still has no fabricated OS PID.

The namespace is part of every lock identity. `project.lock`, `model.lock`,
`searx.lock`, `terraform.lock`, `gate.lock`, `async-gate.lock`, and `e2e.lock`
in one project must not collide with the same logical lock in a second project.
Lease owners are informational evidence; lock acquisition and release still
verify the namespace before acting on a PID. Consumers should use
`lease_inventory` to detect duplicate resource entries and confirm that every
path stays inside the reported project root.

## Why this is required

Docker Compose documents the same isolation rule: a project name prefixes
containers, networks, and volumes so two stacks can use the same service names
without replacing one another ([Compose project-name documentation](https://docs.docker.com/compose/how-tos/project-name/)). A long-running Docker forum report describes
parallel `compose up` calls failing when they share a project name and
stabilizing after assigning independent project names ([parallel Compose
execution discussion](https://forums.docker.com/t/parallel-execution-of-docker-compose-up-commands-with-same-configuration/136142)).
Gludd applies that lesson to local locks and worker admission: project identity
is explicit, lease paths are namespaced, and the worker ceiling is visible in
the status evidence. Observer discovery follows the same project boundary by
reading only registered worktrees' `.gate-logs/observed` trees; the forum's
long-lived collision report is why a global observer directory or unqualified
PID match is not accepted.

## Verification

Run the focused contract tests with:

```text
make test-files TESTFILES='tests/unit/test_active_work_resource_observability.py'
make lint-files FILES='tests/unit/test_active_work_resource_observability.py'
```

These tests intentionally invoke the Make target rather than importing an
implementation detail, so a second terminal can reproduce the same evidence.
