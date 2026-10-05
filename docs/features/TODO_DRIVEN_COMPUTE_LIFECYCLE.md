# Todo-driven compute lifecycle

Status: implemented with hermetic local/Azure-provider E2E coverage; a paid live
Azure proof remains an environment-gated release check.

## Invariant

Gludd does not allocate model compute merely because the daemon is running.
Control-plane bootstrap and due discovery run first. Those producers persist work
as durable todos. One shared scheduler then ranks and atomically claims ordinary,
model, infrastructure, and managed self-improvement work.

Self-improvement is not a separate scheduler. It differs only at the effect
boundaries that require stronger controls: project-private path filtering,
immutable human approval, sandboxing, evidence comparison, and verified Git
promotion.

The event-loop order is:

1. Load configuration and recover control-plane state.
2. Promote due scheduled work.
3. Run bounded self-improvement and issue discovery producers.
4. Reconcile compute from the resulting durable todo state.
5. Claim returns for review and claim todos for execution.
6. Dispatch, reconcile decisions, and emit metrics.

The ordering lets a newly discovered todo request capacity in the same tick. It
also ensures a restarted daemon restores capacity before it reviews or executes
work.

## Demand states

The database, not a process-local queue, is the source of truth.

| Todo state | Retains compute | Reason |
|---|---:|---|
| `queued` | yes | Claimable work exists now. |
| `active` | yes | A worker owns execution. |
| `awaiting_result` | yes | A durable result still needs review. |
| `reviewing_return` | yes | Review is active work and can call a model. |
| `needs_more_work` | yes | The work is non-terminal and eligible for remediation. |
| `scheduled` | no | A future schedule is not current demand. |
| `approval_required`, `approved` | no | Human-gated work cannot execute yet. |
| `blocked`, `blocked_on_human`, `manual_hold` | no | No automated work can currently proceed. |
| terminal states | no | Completed, failed, budget-exhausted, or cancelled work does not need capacity. |

An unknown database state is fail-closed: Gludd preserves owned resources but
does not claim more work. It never interprets a monitoring failure as proof that
resources are idle.

## Atomic multi-worker ownership

Gunicorn workers do not coordinate through Python globals. `claim_runnable()`
uses a guarded database update containing the original `queued` status and todo
version. Multiple workers may read the same candidate, but exactly one update can
move it to `active`; losers return no todo and therefore cannot dispatch it.
The winning transaction is committed and its database session is closed before
the dispatch phase begins. PostgreSQL additionally uses `FOR UPDATE SKIP LOCKED`;
SQLite, which silently drops row locks, retains the same safety through the
status-and-version compare-and-swap and treats `SQLITE_BUSY` as a lost claim.
The WIP ceiling is part of that same guarded update: PostgreSQL serializes
project claimers on the project row, and every backend re-evaluates the scoped
active count inside each `queued -> active` statement. A count error, negative
count, boolean, string, or non-integral count refuses all new claims.
Neither a bucket lease nor an in-process lock is trusted as the ownership fence.

The execution backend owns its deadline. Native Ansible work runs in Gludd's
terminable process group, while execution-environment work passes the same
absolute `job_timeout` into `ansible-runner`. Managed self-improvement, including
model acquisition and candidate evaluation, runs in a Gludd-owned child process
group. On deadline or internal cancellation, Gludd sends TERM then KILL if needed,
joins the exact child, and only then reports the terminal result. A reverse proxy,
Gunicorn timeout, CI watchdog, or operator shell is never the normal cancellation
mechanism. Lease renewal and fail-closed requeue fencing are tracked as part of
this same lifecycle; a missing heartbeat must not, by itself, authorize a second
execution.

`self_improve.managed_execution_timeout_seconds` sets the absolute deadline for
the complete approval-bound run. It defaults to 1,800 seconds, must be finite and
positive, and cannot exceed 7,200 seconds. Both the daemon-local and worker-hosted
paths apply the same policy.

TaskReturn persistence and `active -> awaiting_result` advancement share one
SQLAlchemy savepoint. Review claim then advances the matching todo to
`reviewing_return`. This prevents an executable todo and a reviewable return from
representing two owners of the same work.

The in-process managed-self-improvement lock is only a local resource bound. It
is not used as the ownership primitive.

## Project-owned runtime control

Every scheduler node now resolves through the canonical
`ProjectWorkIdentity(project_id, todo_id, queue)` value object. Project-owned
todo and queue resources include that project in their scheduler labels, so two
owners can use the same human-facing todo or queue name without one being
dropped from the planner map or unnecessarily serialized behind the other.
Legacy unscoped work is explicitly namespaced as `unowned`; it is never aliased
to a valid project named `default`. Canonical execution leases use
`project:<project>:queue:<queue>:todo:<todo>` (or the disjoint `unowned` form),
and lease acquisition refuses both a key/project mismatch and any attempt to
adopt an existing projectless lease. During the v0.1.1 rolling upgrade, each new
worker atomically holds the canonical key and the former `queue:todo` key. That
temporary compatibility fence lets old and new workers drain side by side
without creating two owners; it may serialize coincident cross-project todo IDs
until pre-v0.1.1 workers are retired.

Explicit `Todo.dependencies` participate at both boundaries. The repository
scans one bounded candidate page and leaves a todo queued unless every named
dependency exists in the same project and is complete. Within an already
claimed batch, the scheduler maps those dependencies to project-scoped graph
nodes and emits the prerequisite in an earlier batch. A predecessor omitted
from the batch requires same-project `complete` proof from the repository.
Missing predecessors, malformed dependencies, and cycles dispatch nothing;
they no longer escape through an input-order sequential fallback.

Work in progress is bounded even when no floor or PID controller is installed.
`event_loop.max_active_todos` defaults to 10, is validated in the range 1–10,000,
and the claim budget subtracts the selected project's current active count
before any rows move to `active`. Floor and PID limits may only reduce that
remaining budget. `claim_runnable()` repeats the count under the project lock and
in the conditional update itself, closing the count-then-claim race between
workers. The dispatch semaphore remains a second, independent bound on executing
coroutines.

Crash checkpoints persist their project, queue, todo version, execution-lease
holder, and canonical resume-shard identity under a project-scoped storage path.
Enumeration verifies the durable HMAC before reading ownership fields; a missing
or corrupt key with prior checkpoints refuses startup recovery instead of
minting replacement proof. Startup then performs an exact project-scoped todo
lookup and uses the maintained `filelock` implementation plus an owner-only claim
record to serialize refresh or stale takeover. Missing scope, a forged shard,
corrupt claim JSON, repository failure, or any status other than `queued` fails
closed. In particular, an `active` checkpoint never reuses its stale holder
token: lease recovery must first prove termination and return the todo to
`queued`. Successful task-return persistence clears only the exact project's
checkpoint and resume claim.

This is a zero-downtime additive rollout: the database schema is unchanged and
the dual-key compatibility fence lets old instances drain beside new instances.
Pre-v0.1.1 task-only checkpoint paths are deliberately non-actionable because
their owner cannot be proved; rollback may ignore the new scoped snapshots and
sidecars. Operators must not delete a resume claim to force progress; first
prove the owning process is gone or let the bounded claim expire, while the
database execution fence continues to prevent duplicate effects.

## Capacity providers

`EventLoop.reconcile_compute_demand` calls the concrete Ansible runner's bounded
`reconcile_execution_environment()` API. That API owns the namespaced Podman
machine, execution-environment image, immutable image identity, health facts,
and exact teardown. Runners without that lifecycle method are explicitly treated
as externally managed for compatibility.

The role's Molecule scenario exercises an explicit, non-mutating `prepare` phase
before validating both the `present` and `absent` plans. This keeps bootstrap
readiness, idempotence, and exact teardown in the same CI-visible lifecycle.

When an approved self-improvement plan includes Azure Container Apps, the normal
managed candidate factory lazily composes the Azure SDK, OpenTofu-in-EE plan,
model-serving requirement, topology, call budget, and owned release callback.
The same todo claim reaches both the local candidate and Azure candidate; there
is no Azure-only self-improvement dispatcher. Model parameter count, weight
precision, KV cache, runtime overhead, token budget, and task classification
determine whether the admitted T4/A100 topology is large enough and how many
bounded replicas may be used.

With zero demand, the event loop reconciles its owned execution environment to
`absent`. An Azure candidate independently releases the exact app revision and,
when ownership and idle policy permit, its exact managed environment. Neither
path performs subscription-wide deletion, pruning, or inferred cleanup.

Utilization-triggered idle teardown follows the same proof boundary. If no
deployment owner is wired, or the owner's exact destroy call fails, Gludd keeps
the endpoint registered together with its accumulated idle evidence. It may log
that teardown is pending, but it must not append a torn-down receipt. Only a
successful owner-side destroy may unregister the endpoint and record teardown;
a later tick retries once ownership has recovered. This prevents a missing
lifecycle dependency from hiding compute that may still be running and billed.

## Observability

Every lifecycle transition is content-free and secret-safe:

- Event-loop phase start/completion logs identify demand ordering.
- Tick facts expose demand state, demand count, readiness, and desired execution
  environment without prompt or source content.
- The Ansible runner emits `execution_environment_reconcile_started`,
  `execution_environment_reconcile_completed`, or
  `execution_environment_reconcile_failed`.
- Managed candidate progress emits provider discovery, acquisition, request,
  evaluation, learning, and release phase markers with operation digests.
- Every owned blocking child emits start and periodic heartbeat markers plus an
  explicit timeout or cancellation marker. Process names contain only a digest
  of the approved attempt identity.
- Azure authentication values, private project paths, prompts, and proposal
  content are excluded from events and errors.

## Verification

The hermetic E2E in
`tests/e2e/test_self_improve_private_policy_e2e.py` runs the real managed
self-improvement service for local and Azure-shaped provider modes. It persists
ordinary and self-improvement todos in the real repository, proves common
priority ranking, reconciles compute, applies an approved file edit, records the
provider/evaluator/outcome evidence, and reconciles compute absent after demand
reaches zero.

`tests/security/test_eventloop_redteam.py` runs the same two-session claim race
with simultaneous `asyncio.gather()` calls for ordinary and managed
self-improvement todos. Exactly one session wins, exactly one work object is
returned to a dispatcher, and the surviving row is `active` at version 2.

`tests/unit/test_todo_compute_demand_lifecycle.py` covers phase order, idempotent
present/absent reconciliation, all in-flight states, approval-only and empty
queues, bootstrap failure, and unknown database state. These tests are hermetic
and run in GitHub Actions. The paid live Azure proof is separately gated by
explicit credentials, scope, cost, TTL, and acknowledgement so pull requests do
not create cloud resources.

`tests/unit/test_compute_idle_teardown.py` additionally proves the owner-loss
recovery boundary: an idle endpoint remains registered and is not reported as
torn down while the deployment owner is unavailable; after that exact owner is
restored, the next tick destroys, unregisters, and records the endpoint once.

`tests/unit/test_owned_process_supervisor.py` deliberately wedges child work and
proves that timeout and internal cancellation terminate and reap the exact process
group while emitting progress. `tests/unit/test_managed_self_improve_process.py`
proves the same boundary around an immutable approved plan, including coroutine
cancellation. The daemon and worker dispatch suites prove both production paths
select that executor instead of an unkillable background thread.

Ansible execution carries the same ownership signal through its public adapter.
The native backend polls that callback while supervising its existing finite,
spawned process group and returns `cancelled` only after TERM/KILL/join. The
execution-environment backend uses Ansible Runner's maintained
`cancel_callback`, preserving Runner's container cleanup authority, and
normalizes the terminal result. A callback can never select the legacy inline
path, and callback failures fail closed without rendering their message. These
rules keep Gunicorn and CI outside the termination protocol.

Migration 046 makes a dispatch bucket a durable single-owner execution lease,
not a replaceable liveness hint. Each lease records the todo version, last
heartbeat, cancellation request, and exact-owner termination confirmation.
Renewal is a holder/version-fenced compare-and-swap. Expiry retains the mutex and
requests cancellation; it never makes still-running work claimable. Requeue is a
second compare-and-swap permitted only after the exact attempt reports that its
owned process group has terminated. Non-active or missing todo orphans remain
safe to delete. The red-team suite exercises competing database sessions,
intruder heartbeats, expiry, termination acknowledgement, version fencing, and
replacement attempts, while migration parity compares `create_all` with a full
Alembic upgrade.

EventLoop holds one process-stable lease identity across ticks. It acquires the
complete claimed batch in a database savepoint, fails the whole batch closed on
any live-owner conflict, and returns denied claims to `QUEUED` without dispatch.
Pending estimate mutations are flushed before the todo-version fence is sampled;
this order matters because `AsyncSession.begin_nested()` flushes automatically.
A normally returned owned execution releases only its exact holder lease, while
an exception with an uncertain remote outcome retains ownership for the terminal
handshake. Real-session tests prove both conflict preservation and equality
between the persisted lease fence and the post-flush ACTIVE todo version.

Each database-backed dispatch now starts an independent short-session lease
supervisor before model or Ansible work begins. It awaits renewal of the exact
bucket/holder/todo-version fence before opening the job transaction, then starts
the periodic loop with its first renewal delayed by the configured interval.
This ordering prevents a single-connection database pool from interleaving the
supervisor session with an uncommitted task return while retaining continuous
fencing for long jobs. Renewals publish content-free
`execution_lease_heartbeat` events. A cancellation flag or lost fence reaches
the Ansible adapter through its thread-safe callback. If
the asyncio caller is cancelled, Gludd first persists the owner-scoped request,
then waits for the blocking runner to terminate and reap its process boundary,
and only then records terminal proof. Confirmed cancellation publishes
`execution_lease_cancellation_requested` and
`execution_lease_termination_confirmed`, atomically returns the still-matching
todo to `QUEUED`, and removes the lease. Any database failure, stale fence, or
uncertain remote outcome keeps the lease rather than risking duplicate effects.
`execution_lease_ttl_seconds` defaults to 300 and
`execution_lease_heartbeat_interval_seconds` defaults to 30; invalid or
non-renewable timing fails the complete claim closed before dispatch.

## v0.1.1 durable scheduler proof receipt (2026-09-27)

The proof was run from the isolated `feature/v011-scheduler-live-proof`
worktree with a uniquely named two-CPU, 2 GiB Podman machine. The first live
PostgreSQL run failed before scheduling because migration 040 rendered SQLite's
`strftime()` as a PostgreSQL server default. Replacing it with SQLAlchemy's
dialect-safe `now()` expression let a fresh database migrate through revision
047. That failing run is retained as the test-first receipt for the migration
repair.

The next live run passed four cases and failed the two-worker Gunicorn teardown
case: both wake listeners started, but neither emitted its owned close receipt,
and Python warned about leaked semaphores. The resource lifecycle signal hook
was replacing Gunicorn's existing graceful handler and re-sending the default
signal. It now performs its exact provider cleanup and then delegates to the
previous runtime handler. A focused unit test pins that chaining behavior while
the existing default-handler test still proves crash termination.

The final `make test-e2e-postgres-multiworker` run passed all five cases in
11.32 seconds against PostgreSQL 16. It proved fresh migrations, disjoint claims
from independent worker processes, cost-lease fencing, concurrent deployment
registry writes, cross-connection Terraform event visibility, notification
recovery after forced disconnect, and two-worker Gunicorn shutdown. Both worker
PIDs emitted `Terraform PostgreSQL wake listener closed`; no semaphore leak
warning remained. The target removed its database container and stopped the
machine, and `make podman-project-delete` then removed the exact proof machine.

The consolidated deterministic scheduler, lease, migration, idle-teardown, and
resource-lifecycle profile passed 184 tests with warnings treated as errors. Its
execution-lease coverage receipt reports 98% aggregate coverage, with 99% for
supervision and 96% for the lease repository. The separate lifecycle receipt
reports 85% total coverage for `resource_lifecycle.py` (87.9% lines and 75.9%
branches); no measured file is below 75%.

An independent adversarial review then tightened the receipt boundary itself.
Each PostgreSQL listener start now receives a fresh UUIDv4 `proof_id`, and the
live acceptance requires the same `(worker PID, proof_id)` pair on ready,
notification, reconnect, durable catch-up, and close. Calling cleanup on a
listener that never reached `LISTEN` cannot emit a close receipt, repeated close
is idempotent, and a restart receives a fresh identity. The same review made
partial signal-handler installation transactional, requires an exact version
for every bucket in a fenced lease batch, drains cleanup tasks created during
shutdown cancellation, and retains a confirmed idle-destroy tombstone while a
failed local unregister retries without issuing a second provider destroy.
Migration 040 now has a dialect-compilation regression test proving its
`created_at` default renders as `CURRENT_TIMESTAMP` for SQLite and `now()` for
PostgreSQL.

The hardened receipt was replayed from
`feature/v011-scheduler-review` against a newly created PostgreSQL 16 container
and a namespaced two-CPU, 2 GiB Podman machine. All five live cases passed in
10.29 seconds. Worker `17716` retained proof identity
`8c50959118074b228e0ba7278dca3f6e`, and worker `17717` retained
`b3bbfb524fc1461692c9fa454dcfef8a`, across ready, notification, forced
disconnect, durable catch-up, and close. Graceful shutdown emitted neither the
old false event-loop error nor the false daemon-task error. The target removed
its PostgreSQL container and stopped the VM; the exact-machine delete target
then removed `gludd-scheduler-review` in one second. The adversarial unit profile
passed 333 tests with warnings treated as errors. The lifecycle profile passed
198 tests at 92% aggregate coverage, with both measured files above 89%; the
execution-lease profile remained at 95% aggregate coverage with every measured
file above 90%.

The hosted `Build and Release` dispatch contract passed its committed-head
example preflight. An actual GitHub Actions dispatch deliberately remains gated
on the exact branch commit existing on the configured remote: this isolated proof
does not push or merge. Once an authorized publisher pushes that commit, the same
`ci-trigger-committed-head` target can dispatch only the matching remote SHA and
return the hosted run URL. No schema rewrite or service outage is required for
this repair on an already-migrated database; rolling workers may adopt the
chained shutdown handler independently, preserving zero-downtime operation.

## Long-lived operator reports that shaped the design

Research refreshed on 2026-09-27 includes these scheduler and worker reports:

- SQLAlchemy users have repeatedly asked how to make competing updates safe;
  maintainers clarify that `with_for_update()` affects a preceding `SELECT`,
  while a guarded `UPDATE` relies on database locking/isolation. Gludd therefore
  keeps both the PostgreSQL project-row lock and the predicate inside the write:
  [SQLAlchemy issue #5704](https://github.com/sqlalchemy/sqlalchemy/issues/5704).
- Kubernetes operators reported a leader that had lost its lease but continued
  believing it was leader, producing a split-brain risk. Gludd never treats a
  stale checkpoint owner token as current authority and requires exact
  termination proof before requeue:
  [Kubernetes issue #23731](https://github.com/kubernetes/kubernetes/issues/23731).
- Celery users report worker-loss requeue races multiplying one task into several
  broker copies. Gludd keeps resume claiming, todo CAS, and execution leases as
  separate durable fences and makes malformed claim state fail closed:
  [Celery discussion #9460](https://github.com/celery/celery/discussions/9460).

- APScheduler issue
  [#579](https://github.com/agronholm/apscheduler/issues/579), opened in 2021,
  describes a concurrency limit attached to the function rather than the unique
  job identity, serializing unrelated jobs while still leaving same-ID overlap
  concerns. Gludd therefore scopes runtime resource labels to the canonical
  project and todo identity instead of a callable or global queue name.
- APScheduler issue
  [#881](https://github.com/agronholm/apscheduler/issues/881), opened in 2024,
  reports a worker stopping after roughly 1,000 simultaneous jobs were admitted.
  Gludd subtracts active work before claiming and keeps both candidate scans and
  execution concurrency bounded.
- Gunicorn issue
  [#2510](https://github.com/benoitc/gunicorn/issues/2510), opened in 2021,
  records an auto-restart cutting off executor work after the request had already
  returned. Gludd persists resume identity and ownership outside worker memory;
  a replacement worker must win the durable shard claim and the database fence
  before it can represent that work as resumed.

- Gunicorn maintainers explain that workers are separate processes and do not
  share mutable globals. That is why todo ownership lives in the database rather
  than the event-loop instance: [Gunicorn issue #2082](https://github.com/benoitc/gunicorn/issues/2082).
- A long-running request report describes work apparently starting again after a
  worker was replaced. Regardless of the hosting-layer cause, effects therefore
  need durable claim fencing and idempotency: [Gunicorn issue #2905](https://github.com/benoitc/gunicorn/issues/2905).
- A Gunicorn operator trying to add SIGTERM cleanup reported that replacing the
  worker's handler caused shutdown to hang for the full graceful timeout. Gludd
  therefore chains the captured runtime handler and rolls back the first handler
  if installing the second one fails:
  [Gunicorn discussion #3054](https://github.com/benoitc/gunicorn/discussions/3054).
- Psycopg operators have reproduced async cancellation leaving server work or
  connections alive, and later reported pool connections leaking specifically
  across `CancelledError`. Gludd owns and awaits the dedicated LISTEN task and
  accepts a close proof only for a lifecycle that actually reached ready:
  [psycopg issue #543](https://github.com/psycopg/psycopg/issues/543),
  [psycopg issue #1208](https://github.com/psycopg/psycopg/issues/1208).
- A months-long LISTEN/NOTIFY user reported notification-loss concerns around
  repeatedly entering the `notifies()` generator. Gludd keeps one dedicated
  generator per connection and uses durable audit-table catch-up after every
  reconnect instead of treating notifications as the source of truth:
  [psycopg issue #962](https://github.com/psycopg/psycopg/issues/962).
- A KEDA Azure Queue report shows zero-replica metrics becoming unknown and a
  workload waking only one replica despite queued demand. Gludd consequently
  records todo demand and desired topology itself instead of treating replica
  telemetry as the ownership ledger: [KEDA issue #6183](https://github.com/kedacore/keda/issues/6183).
- Azure Container Apps operators report dedicated profiles taking more than 20
  minutes to wake from zero, with no guaranteed warm-up SLA. Gludd keeps startup
  bounded, observable, retryable, and separate from the atomic claim:
  [Microsoft Q&A 2200802](https://learn.microsoft.com/en-us/answers/questions/2200802/jobs-getting-suspended-in-azure-container-apps-%28ke).
- GPU operators have also reported T4/A100 replicas remaining in
  `AssigningReplica` after scale-from-zero. A provisioning response is therefore
  insufficient; the owned Azure path requires health and invocation proof before
  admitting the candidate:
   [Microsoft Q&A 5572527](https://learn.microsoft.com/en-us/answers/questions/5572527/container-app-using-serverless-gpu-stuck-assigning).
- A resource-group creator question open since 2020 gives the generic recommendation
  to grant `resourceGroups/write` through a custom role at subscription scope.
  Gludd tested the narrower alternative instead: on 2026-09-08 its existing
  future-resource-group-path assignment created and read back the absent exact
  group. The role therefore remains resource-group scoped, and this observed
  provider behavior is pinned rather than widening authority from forum guidance:
  [Microsoft Q&A 48782](https://learn.microsoft.com/en-us/answers/questions/48782/assigning-create-resource-group-permission-only-to).
- Azure CLI users reported in 2025 that repeated `az role assignment create` could
  return `RoleAssignmentExists` rather than behaving idempotently. Gludd keeps the
  CLI renderer as a one-time credential bootstrap and uses deterministic IDs with
  Microsoft's SDK for its supported repeatable IAM path:
  [Azure CLI issue #31995](https://github.com/Azure/azure-cli/issues/31995).
- Ansible Runner users report that stdout can stop while the underlying playbook
  continues and its event artifacts remain complete. Gludd therefore does not
  treat quiet output as proof of a stall:
  [ansible-runner issue #1371](https://github.com/ansible/ansible-runner/issues/1371).
- Operators of Runner's worker/process streaming mode reported intermediary idle
  timeouts severing healthy long-lived work, motivating protocol-level keepalive
  rather than external connection state as execution ownership:
  [ansible-runner issue #1187](https://github.com/ansible/ansible-runner/issues/1187).
- Operator SDK users reported a failed Ansible finalizer allowing the owner custom
  resource to disappear while its external resources remained. Gludd therefore
  retains the endpoint and its idle evidence until the exact deployment owner
  confirms destruction; missing ownership can never become a successful teardown
  receipt:
  [operator-sdk issue #2546](https://github.com/operator-framework/operator-sdk/issues/2546).

Azure's own scaling documentation remains the normative platform reference:
[Azure Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app).
Ansible Runner's documented `cancel_callback`, `finished_callback`, status events,
and runner settings remain the normative local execution contract:
[Ansible Runner Python interface](https://ansible.readthedocs.io/projects/runner/en/stable/python_interface/).
