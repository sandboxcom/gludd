# Terraform multi-worker coordination

Gludd uses PostgreSQL as the shared source of truth for deployment lifecycle
state and as the wake-up path between Gunicorn workers. A deployment created by
one worker can therefore be listed or safely destroyed by another worker without
merging stale `deployments.json` snapshots.

## Design

- `deployment_records` stores one row per chargeable deployment. Dialect-native
  `INSERT ... ON CONFLICT DO UPDATE` preserves concurrent writes to different
  deployments. The `revision` field makes each mutation observable.
- Destroy is a database transition from `running`/`destroy_failed` to
  `destroying`, stamped with `destroy_owner`. Only that owner may finish or
  release the transition, so two workers cannot run Terraform destroy against
  the same state directory.
- Every Terraform event is first written to `audit_events`. Terminal deploy
  events call `pg_notify()` in that same transaction, with only the durable audit
  row id in the payload. PostgreSQL delivers the wake-up only if the audit commit
  succeeds.
- Each Gunicorn worker has one dedicated autocommit Psycopg listener. It executes
  `LISTEN`, queries the durable audit high-water mark, then consumes
  `Connection.notifies()`. Audit ids deduplicate the harmless overlap between
  catch-up and live delivery.
- A broken listener reconnects with bounded backoff and repeats the audit-table
  catch-up. The work itself remains in PostgreSQL, so a notification is an
  acceleration signal rather than the only copy of an event.
- Shutdown unsubscribes the process-local event bridge, cancels and awaits the
  dedicated listener, stops the event loop, and only then disposes the database
  engine. This preserves zero-downtime worker replacement and avoids orphaned
  listener connections.

## Runtime listener boundary and zero-downtime rollout

As of 2026-08-20, `WakeupListener` is explicitly `@runtime_checkable`. Health
checks and composition roots can therefore verify the three lifecycle members
(`start`, `close`, and `aclose`) on an injected listener without importing or
subclassing the PostgreSQL implementation. The check is deliberately shallow:
it proves member presence, not signatures, database reachability, identity, or
authorization. Connection and durable-event validation remain authoritative.

The decorator changes only protocol runtime metadata. It starts no task, opens
no connection, and writes no state, so old and new workers can overlap during a
rolling deployment. Rollback removes the decorator and its focused regression;
listener startup, bounded cancellation, audit catch-up, and database schemas do
not change in either direction.

## Upstream evidence and user reports

PostgreSQL documents `NOTIFY` as inter-process communication and specifies that
a notification issued in a transaction is delivered only after commit. It also
recommends storing larger/durable data in a table and sending its key as the
payload. Gludd follows that pattern with `audit_events.id`:
[PostgreSQL NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html).

PostgreSQL also documents the initial subscription race and prescribes the exact
sequence used here: commit `LISTEN`, inspect database state, then rely on later
notifications. Some overlap is expected and must be harmless:
[PostgreSQL LISTEN](https://www.postgresql.org/docs/current/sql-listen.html).

Psycopg recommends an autocommit connection for timely notification delivery and
a dedicated `notifies()` generator for a connection reserved for listening:
[Psycopg asynchronous notifications](https://www.psycopg.org/psycopg3/docs/advanced/async.html#asynchronous-notifications).

Two long-lived user reports directly shaped the acceptance criteria:

- Gunicorn users observed that mutable globals diverge between workers. The
  maintainers confirmed in 2019 that workers share nothing and require external
  storage or messaging. This is why `deployments.json`/in-memory state is not the
  production registry:
  [Gunicorn issue #2082](https://github.com/benoitc/gunicorn/issues/2082).
- Psycopg users reported notifications lost around generator timing, leading to
  fixes and changed semantics in the 3.2 series. Gludd neither combines handlers
  with the generator nor trusts notifications as durable data; it deduplicates
  and catches up from the audit table:
  [Psycopg issue #962](https://github.com/psycopg/psycopg/issues/962).

Python's maintained typing documentation specifies that only protocols marked
`@runtime_checkable` may participate in `isinstance`, and warns that the check
validates member presence rather than method signatures. It also records the
Python 3.12 switch to static attribute lookup and frozen protocol members:
[CPython typing documentation](https://github.com/python/cpython/blob/main/Doc/library/typing.rst).
The typing community's practitioner discussion opened on 2023-03-06 documents
the descriptor side effects that motivated static lookup. `WakeupListener` has
methods only, keeping this boundary narrow and side-effect-free:
[python/typing issue #1363](https://github.com/python/typing/issues/1363).

## Live acceptance

Run the namespaced disposable PostgreSQL 16 acceptance target:

```text
make test-e2e-postgres-multiworker \
  POSTGRES_E2E_RUNTIME=podman \
  POSTGRES_E2E_IMAGE=postgres:16-alpine \
  POSTGRES_E2E_TIMEOUT_SECS=180 \
  POSTGRES_E2E_VALIDATE_ONLY=0 \
  PODMAN_MACHINE=gludd \
  PODMAN_START_TIMEOUT_SECS=120 \
  PODMAN_RECREATE=0 \
  PODMAN_MEMORY_MB=4096 \
  PODMAN_CPUS=4 \
  PODMAN_DISK_GB=20
```

The test migrates a real PostgreSQL instance, starts two real Gunicorn/Uvicorn
workers, and verifies:

1. concurrent registry writes from separate processes are both retained;
2. a committed Terraform event becomes visible from another connection;
3. both workers receive the event within five seconds;
4. both dedicated listener connections are forcibly terminated;
5. an event committed during the disconnect is found by both workers' catch-up;
6. SIGTERM produces a clean Gunicorn exit and closes both listeners.

`GLUDD_PG_WAKE_RECONNECT_SECONDS` may pin the retry delay for deterministic fault
injection. Production defaults start at 100 ms and back off to five seconds.
