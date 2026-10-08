# Validation Job Pipeline

## Outcome

`POST /jobs/validate` executes a real validation job through the worker's
existing `/jobs/execute` coroutine. The route replaces caller-provided routing
fields with the canonical values:

- `playbook`: `validate_task.yml`
- `work_type`: `validation`

Every other validated `JobSpec` field is preserved. The response is the
canonical worker result, including the return ID, runner exit code, summary,
artifacts, and events. A non-zero Ansible return code remains visible to the
caller; the route never converts failed validation into an acknowledgement.

## One Execution Boundary

The endpoint does not create a second queue, scheduler, runner, or lifecycle.
It delegates once to the in-process `execute_job` coroutine. That keeps these
existing controls authoritative:

- bearer-token authentication in the worker middleware;
- exact playbook-registry admission for `validate_task.yml`;
- duplicate `job_id` rejection from `prepare_job_dirs` (`HTTP 409`);
- the `GLUDD_JOB_TIMEOUT_MAX` ceiling and the runner's finite timeout;
- blocking filesystem and Ansible work offloaded with `asyncio.to_thread`;
- unconditional workspace cleanup after success, failure, or cancellation;
- the standard task-return schema and unmodified runner failure evidence.

Validation inputs such as `worktree_path` and a bounded `test_commands` list
continue through `budget_context` into the canonical extravars file. The
playbook itself only accepts up to 16 commands whose shape is `make <target>`.

## Background-Task Research

FastAPI's official
[background-task guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/)
positions `BackgroundTasks` for small same-process work and recommends a
separate job system for heavy computation. The long-lived FastAPI community
[issue #611](https://github.com/fastapi/fastapi/issues/611), now represented by
[discussion #7930](https://github.com/fastapi/fastapi/discussions/7930), records
worker-timeout reports from attempts to detach multi-minute subprocess work;
the accepted guidance says that this is outside the intended short-task use
case.

Accordingly, `/jobs/validate` does not use `BackgroundTasks` or fire-and-forget
`asyncio.create_task`. It awaits the already-bounded worker pipeline so that a
completed HTTP response always contains the actual runner outcome. Long-lived
dispatch remains the responsibility of the project's existing external job
producer and worker deployment, not a second in-process scheduler.

## ZDD and Rollback

The change is additive and suitable for zero-downtime deployment. Old worker
instances continue returning `HTTP 501`; new instances execute validation.
Callers already had to treat `501` as unavailable and can retry against a new
instance. No schema, database, queue, or playbook migration is required, so old
and new instances may overlap during a rolling replacement.

Rollback is a normal worker-image rollback. It restores the explicit `501`
behavior without undoing persistent state because the route adds none.
In-flight requests retain the timeout and cleanup guarantees of the worker
version that accepted them.

## Resource and Failure Contract

- Validation uses the existing per-request timeout ceiling; it cannot request
  an unbounded Ansible run.
- The event loop remains responsive while blocking runner operations execute
  in the worker's thread offload.
- The playbook's command-count and command-shape assertions constrain child
  work to repository make targets.
- Existing workspace cleanup prevents abandoned job directories from
  accumulating across rolling replacements.
- Missing playbook admission fails before workspace creation; duplicate work
  fails with `409`; runner return codes, summaries, events, and artifacts are
  returned without optimistic rewriting.

## Verification

The focused contract suite covers canonical routing, exact runner delegation,
authentication, playbook admission, duplicate-job conflict, timeout clamping,
honest non-zero runner evidence, and preservation of the remaining `501`
stubs. The dedicated coverage profile measures the worker application with an
85 percent aggregate floor and a 75 percent per-file floor.
