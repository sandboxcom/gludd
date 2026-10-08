# Validation Request Orchestration

## Outcome

S15b turns one reviewed `TaskDecision.validation_requests` entry into one
durable validation round trip. It is deliberately disabled by default:

```yaml
validation_requests:
  enabled: false
  commands:
    - make test-count
  timeout_seconds: 300
```

S15a, the worker validation-job pipeline, must be deployed and verified before
an operator sets `enabled: true`. With the flag absent, false, or malformed,
existing completion behavior is unchanged.

When enabled, a non-empty request intercepts `COMPLETE`. The event loop builds
a deterministic `VALIDATE-<todo>-<decision-digest>` `JobSpec`, posts it to the
existing `/jobs/validate` endpoint, and persists the canonical response through
the existing `TaskReturnRepository`. Persisting the return advances the todo to
`AWAITING_RESULT`, where the normal review lifecycle examines the validation
result. A non-zero exit code is evidence for that review; it is never rewritten
as transport failure or optimistic success.

## Trust Boundary

Reviewer text is only a request signal. It is never copied to `prompt_text`,
`budget_context`, a subprocess argument, a path, or a command. All executable
inputs come from trusted state:

- `worktree_path` is the resolved project repository path already selected by
  completion reconciliation;
- `test_commands` is the operator-configured list of one through sixteen exact
  `make <target>` strings;
- `playbook` is always `validate_task.yml`;
- `work_type` is always `validation`; and
- the request timeout defaults to 300 seconds and must be finite, positive, and
  no greater than 600 seconds.

Only one request entry is admitted per decision. More than one entry is treated
as malformed rather than starting multiple jobs. The process-local validation
ledger prevents a reconciled row from dispatching twice, while the durable todo
transition prevents replay after restart. The SHA-256 suffix uses Python's
documented deterministic digest interface: [Python `hashlib` documentation](https://docs.python.org/3/library/hashlib.html).

## Failure Contract

| Condition | Todo outcome | Rationale |
| --- | --- | --- |
| Missing/unresolvable project path | `NEEDS_MORE_WORK` | The work artifact lacks a usable validation binding. |
| Missing, empty, excessive, or non-make command list | `NEEDS_MORE_WORK` | Operator-owned validation inputs need correction. |
| Malformed request JSON or more than one request | `BLOCKED` | Persisted review state cannot be interpreted safely. |
| Invalid or unbounded timeout | `BLOCKED` | Runtime policy is unsafe or ambiguous. |
| HTTP timeout, unavailable worker, non-2xx response | `BLOCKED` | Validation did not produce reviewable evidence. |
| Malformed worker response or persistence failure | `BLOCKED` | No durable, attributable result exists. |
| Valid response with any integer exit code | `AWAITING_RESULT` | The honest result remains reviewable. |

The same finite value is placed on `JobSpec.timeout` and passed to the HTTPX
request. HTTPX documents both per-request timeout configuration and its typed
timeout failures in its [timeout guide](https://www.python-httpx.org/advanced/timeouts/).
The response reader accepts the synchronous `httpx.Response.json()` contract
and awaitable protocol fakes; Python documents this distinction through
[`inspect.isawaitable`](https://docs.python.org/3/library/inspect.html#inspect.isawaitable).

TaskReturn creation and the todo transition reuse the existing SQLAlchemy
`AsyncSession.begin_nested()` boundary. SQLAlchemy defines that operation as a
nested transaction/SAVEPOINT in its
[asyncio API](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html), so
a failed persistence unit does not masquerade as a successful dispatch or
force rollback of unrelated tick work.

## Long-Lived Runtime Research

The long-running FastAPI user report
[#611](https://github.com/fastapi/fastapi/issues/611) describes worker timeouts
when multi-minute subprocess work was detached with `asyncio.create_task`; its
migrated [discussion #7930](https://github.com/fastapi/fastapi/discussions/7930)
also records the difference between blocking calls and properly awaited async
subprocess work. S15b therefore does not add `BackgroundTasks`,
`asyncio.create_task`, a local subprocess, or another in-process queue. It
awaits S15a's existing authenticated worker request and consumes the actual
runner result under a finite timeout.

## Zero-Downtime Deployment and Rollback

Rollout is additive and ordered:

1. Deploy S15a workers while `validation_requests.enabled` remains false.
2. Verify the new `/jobs/validate` behavior on every worker generation.
3. Roll out S15b event-loop instances with the still-disabled configuration.
4. Enable one canary project or instance, observe validation latency, worker
   capacity, TaskReturn creation, and todo transitions, then roll forward.

Old and new S15b event-loop instances may overlap because the flag is off by
default and there is no database schema, queue, or wire-schema migration. The
deterministic job identity and existing persistence boundary make retries
observable. Each decision requests at most one job, each job carries at most
sixteen make targets, and both HTTP and worker execution have finite ceilings;
this bounds network occupancy, command fan-out, job workspace use, and retained
database evidence.

Rollback begins by setting `enabled: false`, which immediately stops new S15b
dispatches on reloaded instances. Already accepted requests finish or time out
under their original bound and remain reviewable or blocked. Operators can then
roll back S15b instances, followed by S15a workers if needed. No data downgrade,
queue drain, process cleanup, or schema reversal is required.

## Verification

The focused suite proves default-off compatibility, completion interception,
one-dispatch idempotency, deterministic routing, non-executable reviewer text,
bounded make commands and timeouts, fail-closed transport/response handling,
SAVEPOINT-backed persistence, and preservation of non-zero validation results.
The coverage profile measures both orchestration modules with an 85 percent
aggregate floor and a 75 percent per-file floor.
