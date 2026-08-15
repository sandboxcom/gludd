# Gunicorn Cross-Worker Transport Decision

**Status:** Accepted design; implementation and live Azure acceptance remain gated
**Decision date:** 2026-08-01
**Scope:** durable Gludd events, work claims, and serialized writes across Gunicorn
workers

## Decision

Use PostgreSQL tables as the durable authority for inbox events, work commands,
outbox events, acknowledgements, leases, and consumer checkpoints. Use PostgreSQL
`LISTEN`/`NOTIFY` only as a low-latency wake-up hint. Every listener must also poll
from its durable checkpoint, so a lost, folded, delayed, or duplicated notification
cannot lose or duplicate an externally visible effect.

This is deliberately not an "exactly once" claim. Delivery is at least once. Each
work item has a stable idempotency key, and each side effect is protected by a
database uniqueness constraint or a persisted effect ledger. Competing consumers
claim queue rows in short transactions with `FOR UPDATE SKIP LOCKED`, a lease, and
a monotonically increasing fencing token. Expired work can be reclaimed; a stale
worker cannot commit after its fence has been superseded.

Keep SQLite limited to one Gunicorn worker. The clamp may be made conditional only
after the PostgreSQL schema, migration, failover, and live Azure tests below pass.
The current in-process broker and write queue remain useful local implementation
details, but they are never the source of truth for cross-process correctness.

This supersedes the Redis/POSIX-MQ notification direction in
`docs/POSTGRES_MULTI_WORKER.md` for Gunicorn event and write coordination. Redis
Streams or NATS JetStream should be reconsidered only when measured PostgreSQL
queue latency/throughput or a multi-region event-mesh requirement justifies a
second stateful service.

## Why this transport

Gludd already declares psycopg and has PostgreSQL URL plumbing, while the runtime
still rejects PostgreSQL because migrations and schema initialization are
SQLite-only. Completing that migration is already required for honest multi-worker
operation. Keeping the durable queue and business mutation in one PostgreSQL
transaction avoids a dual-write window and avoids adding a Redis or NATS service,
client, private endpoint, backup policy, availability model, and cost line.

PostgreSQL documents that `NOTIFY` is delivered only after commit and recommends
putting larger data in tables and sending a key in the notification. It also warns
that identical channel/payload notifications can be folded, payloads are normally
limited to 8,000 bytes, and a long transaction can prevent notification-queue
cleanup. Therefore the notification payload is only a row id or high-water mark;
event bodies, secrets, status, and retry state stay in tables. Monitor
`pg_notification_queue_usage`, and never hold a listener transaction open.

A listener uses a dedicated psycopg autocommit connection. On startup or rotation
it commits `LISTEN`, takes a fresh durable snapshot/checkpoint, then consumes hints
and polls. It reconnects with jitter and polls throughout an outage. Production
poolers must preserve session state for that dedicated connection; transaction or
statement pooling is not an acceptable listener path.

The implementation must raise the tested psycopg floor to at least 3.2.10 and pin
below the next major version. This is defense in depth, not correctness: a psycopg
notification regression must only increase polling latency, never lose work.

## Durable flow

1. An HTTP, Terraform, Azure Event Grid, or internal producer normalizes the event
   and inserts an inbox/work row with its source idempotency key.
2. The same transaction inserts any outbox row and calls `pg_notify` with only a
   queue key/high-water mark. Rollback makes neither durable work nor a wake hint
   visible.
3. A worker wakes from `NOTIFY` or its periodic poll, then atomically claims rows
   with `FOR UPDATE SKIP LOCKED`, a bounded lease, attempt counter, and fencing
   token.
4. The worker persists its result/effect ledger and terminal state transactionally.
   A crash before commit causes lease recovery; a retry sees the idempotency ledger.
5. Fan-out consumers advance their own durable checkpoint. Rows are retained until
   every required consumer has acknowledged or reached a terminal dead-letter
   state.

For writes that must remain serialized, request workers insert durable write-command
rows concurrently and one logical writer consumes them. The API returns `202` with
the command id, or waits on a persisted result row for a bounded interval. Only
commutative, independently fenced partitions may gain multiple writers later.

Correctness queues must never use `DROP_OLDEST`. When queue depth, oldest age,
connection pressure, or predicted completion time crosses the configured admission
limit, Gludd returns a retryable `503`/`Retry-After` before accepting more work.
Azure/Event Grid delivery can then retry. Queue depth, oldest age, claim latency,
lease takeover, redelivery, dead letters, poll fallback, listener reconnects,
notification-queue usage, and database-pool saturation are required metrics.

## Alternatives considered

| Option | Delivery and failure behavior | Operations/cost | Decision |
|---|---|---|---|
| PostgreSQL table + `LISTEN`/`NOTIFY` hint | Durable rows and checkpoints; at-least-once claims; notification loss only affects latency; business state and outbox commit atomically | Reuses required database, psycopg, private networking, backup, and HA controls | **Chosen** |
| Redis Pub/Sub | Explicitly at-most-once; a disconnected subscriber permanently loses messages | Adds a managed service/client/private endpoint; Azure Cache for Redis is retiring in favor of Azure Managed Redis | Rejected for correctness traffic |
| Redis Streams | Persistent consumer groups, pending entries, `XACK`/`XAUTOCLAIM`; at-least-once means duplicates; trimming and pending-entry recovery require policy and monitoring | Adds a second transactional boundary and stateful Azure service; cannot atomically commit PostgreSQL state and a stream append without an outbox | Defer until measured scale requires it |
| Core NATS | Best-effort at-most-once | Adds a NATS service and client | Rejected for correctness traffic |
| NATS JetStream | Persistent/replayable, acknowledgements, redelivery, and flow control; duplicates remain possible | Requires storage, replication, backup, upgrade, and cluster ownership; PostgreSQL-to-JetStream publication still needs an outbox | Defer for an explicit event-mesh/multi-region requirement |
| Gunicorn-local broker/queue | Workers share no memory; restart loses queued work; current `DROP_OLDEST` mode can discard data | Cheap only because it omits durability | Keep only for SQLite single-worker development and local fan-in |

PostgreSQL Flexible Server HA uses a synchronous standby and is designed for zero
loss of committed data on supported failovers. It also bills both primary and
standby compute. Gludd cost estimates for every applicable work item must include
that standby, storage/IO, backup retention, private networking, and expected queue
hold time. The chosen design must not include Redis or NATS charges unless that
service is actually enabled.

## User-report evidence and failure lessons

These reports are not treated as product specifications; they capture failure
patterns that remained confusing in real deployments and are converted into tests.

- [psycopg issue #962](https://github.com/psycopg/psycopg/issues/962) (opened
  2024) reports notifications lost between generator calls on psycopg 3.2.3, then
  duplicate observations when generator and handler APIs were mixed. Maintainer
  discussion also notes that middleware or managed infrastructure can break
  long-lived connections. Result: notification hints are non-authoritative, one API
  style is used, the tested version is pinned, connections rotate, and polling is
  continuous.
- [Redis issue #8635](https://github.com/redis/redis/issues/8635) (opened 2021)
  documents prolonged confusion around Streams consumer-group memory, pending
  entries, acknowledgements, and trimming. Result: Streams are not "durability for
  free"; adoption requires explicit retention, pending-entry recovery, and capacity
  tests.
- [Redis issue #5570](https://github.com/redis/redis/issues/5570) (opened 2018)
  shows a slow-consumer/trim interaction producing surprising repeated stream
  entries. Result: any future Streams design must remain idempotent and test slow
  consumers under retention pressure.
- [NATS discussion #4246](https://github.com/nats-io/nats-server/discussions/4246)
  (2023) reports JetStream redelivery despite an application acknowledgement.
  Result: JetStream would still require durable idempotency and duplicate-effect
  tests.
- [Gunicorn issue #2082](https://github.com/benoitc/gunicorn/issues/2082) (opened
  2019) confirms that worker processes share nothing and recommends external shared
  storage/messaging. Result: a Python global, `asyncio.Queue`, or in-process broker
  can never establish multi-worker correctness.

## Primary references

- PostgreSQL [`NOTIFY`](https://www.postgresql.org/docs/current/sql-notify.html),
  [`LISTEN`](https://www.postgresql.org/docs/15/sql-listen.html), and
  [`SKIP LOCKED`](https://www.postgresql.org/docs/16/sql-select.html)
- psycopg [asynchronous notifications](https://www.psycopg.org/psycopg3/docs/advanced/async.html)
- Redis [Pub/Sub delivery semantics](https://redis.io/docs/latest/develop/pubsub/)
  and [Streams/consumer groups](https://redis.io/docs/latest/develop/data-types/streams/)
- NATS [Core delivery model](https://docs.nats.io/nats-concepts/core-nats),
  [JetStream semantics](https://docs.nats.io/nats-concepts/jetstream), and
  [consumer flow control](https://docs.nats.io/nats-concepts/jetstream/consumers)
- Gunicorn [pre-fork worker design](https://docs.gunicorn.org/en/stable/design.html)
- Azure PostgreSQL Flexible Server
  [HA behavior](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-high-availability)
  and [HA billing](https://learn.microsoft.com/en-in/azure/postgresql/high-availability/how-to-configure-high-availability)
- Azure [Cache for Redis retirement](https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-whats-new)
  and [Azure Managed Redis overview](https://learn.microsoft.com/en-us/azure/redis/overview)

## ZDD migration slices

Each slice is independently deployable and reversible by feature flag; migrations
are expand/contract and remain compatible with the previous worker version.

1. **Schema parity:** add PostgreSQL migrations and contract tests, deploy managed
   PostgreSQL privately, but keep SQLite and PostgreSQL at one worker.
2. **Durable shadow path:** add inbox, outbox, work-command, checkpoint, lease,
   fencing, effect-ledger, and dead-letter tables. Dual-record in shadow mode while
   the legacy single-worker path stays authoritative.
3. **Wake hints:** add a dedicated autocommit listener plus checkpoint polling,
   reconnect/rotation, health metrics, and notification-queue monitoring. Keep one
   worker.
4. **Canary concurrency:** enable two, then four Gunicorn workers for PostgreSQL
   tenants while retaining one logical serialized writer. Roll back by routing to
   one worker; do not roll back the expanded schema.
5. **Authority switch:** make durable claims authoritative after reconciliation
   proves shadow parity. Remove in-process transport only from correctness paths.
6. **Live Azure gate:** pass duplicate/out-of-order Terraform event, worker crash,
   database failover, rolling deploy, cost, and cleanup tests. Only then advertise
   PostgreSQL multi-worker support or relax the SQLite clamp conditionally.
7. **Scale review:** consider Streams or JetStream only with captured evidence that
   the PostgreSQL SLO cannot be met or a new topology requires an event mesh.

## Executable acceptance contract

Implementation is incomplete until the following Make-only targets exist in the
target contract, stream progress, clean up resources on failure, and pass:

```text
make test-postgres-multiworker-e2e WORKERS=4 COMMANDS=10000
make test-postgres-transport-failover WORKERS=4 POLL_INTERVAL_MS=250
make test-e2e-azure-provision-sourced AZURE_E2E_ENV_FILE=/tmp/general-ludd.env WORKERS=4
```

The first target must launch actual Gunicorn worker processes against PostgreSQL,
not mocks, and terminate with an auditable marker equivalent to:

```text
POSTGRES_TRANSPORT_PASS workers=4 accepted=10000 lost=0 duplicate_effects=0
```

It must prove unique source ids, duplicate and out-of-order input, concurrent
claims, worker death after claim but before commit, lease takeover/fencing,
serialized writes, payloads larger than the notification limit, a saturated queue
returning retryable admission errors without drops, clean drain, and zero unhandled
warnings. The p95 enqueue-to-claim latency is at most 250 ms under the declared test
load when hints work.

The failover target must kill the listener connection, suppress wake hints, restart
the database, and kill workers at each transaction boundary. Every committed row is
eventually processed, every uncommitted row has no effect, duplicate effects remain
zero, and polling claims eligible work within twice the configured poll interval.
It must exercise listener rotation and verify a long-running listener never pins
notification-queue cleanup.

The live Azure target must load credentials only from the explicitly supplied
`/tmp/general-ludd.env`, redact them from output, and use uniquely namespaced
resources. Terraform readiness events must enter the durable inbox as soon as each
resource is usable; duplicate/out-of-order Event Grid deliveries must not allocate
duplicate compute. It must canary two then four workers, interrupt one worker and a
rolling deployment, prove ongoing API availability, right-size and drain compute,
verify cleanup, and emit terminal markers equivalent to:

```text
AZURE_EVENT_MULTIWORKER_PASS workers=4 lost=0 duplicate_effects=0
COST_PREDICTION_PASS includes_postgres_ha=true redis=false nats=false
CLEANUP_VERIFIED leaked_resources=0
```

The cost assertion compares Gludd's prediction with the test's observed Azure cost
inputs, including PostgreSQL primary and HA standby, storage/IO, backup retention,
private networking, compute boot/drain/runtime, and idle grace period. The accepted
error budget must be declared by resource class; no aggregate percentage may hide a
resource class outside its budget.

Finally, the normal gate must remain green with at least 85% aggregate coverage and
at least 75% per changed file. Unit coverage alone cannot replace these live
process, failover, and Azure assertions.
