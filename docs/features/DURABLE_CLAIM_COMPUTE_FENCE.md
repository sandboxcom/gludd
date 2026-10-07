# Durable claim commit compute fence

Status: implemented for the EventLoop compute-provider boundary; exact-candidate
gate and hosted replay remain release evidence.

## Invariant

Compute is an external side effect. A non-empty in-memory claim batch is not
enough authority to create it. Gludd may reconcile an execution environment to
`present` only after the claim transaction has committed and the tick has
released its active database session.

The ordinary session-factory tick already provides this order:

1. discover, approve, rank, and atomically claim durable todos;
2. commit the claim and execution-lease transaction;
3. clear the tick-scoped repositories and active session;
4. reconcile task-shaped compute for the committed claim batch; and
5. dispatch, verify, persist the terminal decision, and release exact ownership.

The compatibility path also accepts a caller-owned live `AsyncSession`. It now
commits the claim and execution leases before either compute reconciliation or
job dispatch, while leaving the reusable caller-owned session attached. If a
provider runner is configured, that still cannot prove the claim session was
released: Gludd emits the bounded `claim_transaction_open` state, marks compute
not ready, makes no provider call, and blocks dispatch. If no provider lifecycle
exists, reconciliation performs no provider side effect, marks the committed
claim `externally_managed`, and permits the existing sequential dispatch path.
Empty demand retains its existing idle/owned-resource behavior because it cannot
create a new compute side effect.

This check deliberately does not inspect `in_transaction()`. A successful
driver return, a reusable session, or a caller assertion is weaker evidence than
the lifecycle boundary that cleared the session after commit. Ambiguity fails
closed without rolling back, closing, or otherwise taking ownership of a
caller-supplied session.

## Operator decision tree

The decision is local to one tick and one exact project. Operators can diagnose
it without inspecting todo text, provider errors, or credentials:

```text
claimed batch empty?
├─ yes → allocate nothing
│  ├─ same-loop compute already present → retain; terminal release owns removal
│  └─ no owned compute → report idle
└─ no → provider lifecycle exists?
   ├─ no → require successful claim commit; allocate nothing; dispatch sequentially
   └─ yes → active claim session still attached?
      ├─ yes → reject with claim_transaction_open; allocate and dispatch nothing
      └─ no → exact project root and bounded constraints valid?
         ├─ no → fail closed; allocate nothing
         └─ yes → reconcile present under the existing deadline and receipt checks
```

For `claim_transaction_open`, the first operator action is to identify the caller
that injected a live session and move provider-managed work to the supported
session-factory tick. Do not retry the provider directly and do not clear
`_active_session` from caller code: either action would manufacture ownership
evidence. Providerless compatibility callers may continue because the tick has
already committed the claim and the branch performs zero compute lifecycle
calls.

If rejection rises after rollout, use this rollback tree:

```text
provider call count remained zero?
├─ no → stop rollout; the fence is not active at the effect boundary
└─ yes → are rejected callers compatibility/test-only?
   ├─ yes → migrate them to a session factory; keep the fence enabled
   └─ no → is durable work still queued and healthy?
      ├─ yes → pause rollout, repair composition, and retry through a fresh tick
      └─ no → preserve compute and claims, diagnose the database first
```

Source rollback is appropriate only after the second tree proves there is no
active caller relying on the fence for safety and no ambiguous claim outcome.
Provider-side creation is never a rollback step.

## Bounded resource and observability contract

The rejected branch has a deliberately small resource envelope:

- provider calls, subprocesses, threads, network requests, cloud resources, and
  execution environments created: **zero**;
- compatibility claim commits added: **one before dispatch**; provider-fence
  rejection adds no further commit, close, refresh, or query;
- caller-owned session closes: **zero**;
- wait/retry loop iterations and sleep time: **zero**; and
- retained in-memory evidence: one fixed state mapping and one integer metric.

The tick emits only fixed, content-free evidence:

- `compute_demand.state = claim_transaction_open`;
- `compute_demand.execution_environment = unchanged`;
- `compute_demand.runnable_todos = <count>`;
- `compute_ready = false`; and
- `compute_claim_fence_rejections += 1`.

The count is operational cardinality, not task content. Logs must not include todo
titles, descriptions, provider payloads, repository paths, database URLs, or
exception text. Existing provider deadline, idempotency, and receipt validation
remain authoritative after admission; the fence neither adds a second retry loop
nor widens concurrency.

Resource ownership remains separated:

| Resource | Owner | Fence behavior |
|---|---|---|
| injected `AsyncSession` | caller | commit the claim boundary; never close it |
| todo row and execution lease | durable repository | preserve for a later fenced tick |
| provider runner | application composition | do not invoke before commit/session release |
| existing owned compute | durable-demand release path | preserve until a fresh terminal-demand read |

Recommended rollout is one ordinary deployment with the rejection metric,
compatibility commit result, and provider-call count observed together. A
rejection is actionable composition drift, not a reason to increase a timeout or
capacity limit. Alert on any rejection in production and on the impossible
combination of a rejection plus a new provider call in the same tick.

## Zero-downtime delivery and rollback

The fence is evaluated immediately before the existing provider call. It changes
no schema, listener, credential, deployment shape, or provider receipt. Existing
session-factory workers continue through the same path because they clear the
claim session before reconciliation. A legacy caller that retained a live
session now commits before dispatch. It receives a retryable no-allocation state
when a provider is configured, while a providerless caller dispatches without
attempting compute provisioning.

Rollout therefore needs no drain or restart coordination beyond the normal
application deployment. Rollback is a source revert, but it must occur only
after confirming that no caller depends on the unsafe live-session behavior;
already-owned compute remains governed by the independent durable-demand release
read and is neither removed nor transferred by this fence.

## Practitioner evidence

Research refreshed on 2026-10-07 found the same transaction/effect race in
long-lived worker communities:

- Sidekiq issue [#5239](https://github.com/sidekiq/sidekiq/issues/5239), opened
  in 2022, describes publishing jobs before database commit as a problem present
  “since Day One” and moved enqueueing behind an `after_commit` boundary.
- Sidekiq discussion [#5725](https://github.com/sidekiq/sidekiq/discussions/5725)
  records operators evaluating transaction-aware pushes for established
  production applications rather than treating process-local enqueue success as
  durable database authority. One operator later reported enabling the feature
  after more than 237 million processed jobs with no observed issue, which
  supports retaining compatibility while moving its enqueue boundary after the
  commit rather than deleting the path.
- Celery's maintained
  [task guide](https://github.com/celery/celery/blob/main/docs/userguide/tasks.rst)
  documents the race where a worker starts before a creating transaction commits
  and exposes `delay_on_commit` as the supported boundary.

Those reports concern brokers rather than GPU providers, but the ownership rule
is identical: an effect consumer can run immediately, so the durable state that
authorizes it must commit first. Gludd makes the rule stricter for compute by
also requiring the tick to release its active database session before invoking
the provider.

## Verification

`tests/unit/test_todo_compute_demand_lifecycle.py` pins both compatibility cases:
claimed todos plus a live tick session and provider runner must produce zero
runner calls, `compute_ready = false`, and the content-free
`claim_transaction_open` receipt; without a provider runner, the committed claim
is externally managed and dispatch-ready. The same contract is pinned for a
playbook-only runner that has no `reconcile_execution_environment` lifecycle:
its presence does not manufacture a compute effect or block dispatch.
`tests/unit/test_tick_session.py` proves that the live-session path commits
before dispatch. The two exact
project-isolation integration nodes and the concurrent-tick serialization node
are also part of `integration-admission` so this compatibility regression fails
before the long integration phase.
