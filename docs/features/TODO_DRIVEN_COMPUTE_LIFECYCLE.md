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
Neither a bucket lease nor an in-process lock is trusted as the ownership fence.

TaskReturn persistence and `active -> awaiting_result` advancement share one
SQLAlchemy savepoint. Review claim then advances the matching todo to
`reviewing_return`. This prevents an executable todo and a reviewable return from
representing two owners of the same work.

The in-process managed-self-improvement lock is only a local resource bound. It
is not used as the ownership primitive.

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

## Long-lived operator reports that shaped the design

- Gunicorn maintainers explain that workers are separate processes and do not
  share mutable globals. That is why todo ownership lives in the database rather
  than the event-loop instance: [Gunicorn issue #2082](https://github.com/benoitc/gunicorn/issues/2082).
- A long-running request report describes work apparently starting again after a
  worker was replaced. Regardless of the hosting-layer cause, effects therefore
  need durable claim fencing and idempotency: [Gunicorn issue #2905](https://github.com/benoitc/gunicorn/issues/2905).
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

Azure's own scaling documentation remains the normative platform reference:
[Azure Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app).
