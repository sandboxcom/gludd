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
| `worker_count` | Number of workers admitted for this project at snapshot time. |
| `worker_limit` | Positive hard ceiling for admitted workers. `worker_count` must never exceed it. |

The namespace is part of every lock identity. `gate.lock`, `async-gate.lock`,
and `e2e.lock` in one project must not collide with the same logical lock in a
second project. Lease owners are informational evidence; lock acquisition and
release still verify the namespace before acting on a PID.

## Why this is required

Docker Compose documents the same isolation rule: a project name prefixes
containers, networks, and volumes so two stacks can use the same service names
without replacing one another ([Compose project-name documentation](https://docs.docker.com/compose/how-tos/project-name/)). A long-running Docker forum report describes
parallel `compose up` calls failing when they share a project name and
stabilizing after assigning independent project names ([parallel Compose
execution discussion](https://forums.docker.com/t/parallel-execution-of-docker-compose-up-commands-with-same-configuration/136142)).
Gludd applies that lesson to local locks and worker admission: project identity
is explicit, lease paths are namespaced, and the worker ceiling is visible in
the status evidence.

## Verification

Run the focused contract tests with:

```text
make test-files TESTFILES='tests/unit/test_active_work_resource_observability.py'
make lint-files FILES='tests/unit/test_active_work_resource_observability.py'
```

These tests intentionally invoke the Make target rather than importing an
implementation detail, so a second terminal can reproduce the same evidence.
