# Writer Subprocess Bootstrap

Status: S16a implements a fail-closed, real database bootstrap for the
opt-in writer child. Durable publication and replay remain S16b work.

## Contract

The daemon continues to select this path only with
`GLUDD_WRITER_MODE=subprocess`. `WriterProcess` accepts the same flat
database mapping used by daemon startup and normalizes it to the child
protocol. Child-only controls such as `inbound_spool_path` and
`tick_interval` stay at the top level.

The child follows this order:

1. Load canonical JSON and require an explicit database URL or host.
2. Construct the locked SQLAlchemy async engine inside the child process.
3. Ensure the SQLite schema when applicable and execute `SELECT 1` through
   a real connection.
4. Apply any complete `execute_sql` envelopes already present in the spool.
5. Atomically publish the parent-generated readiness nonce.
6. Enter the EventLoop tick and spool-drain loop.

Missing, malformed, or unreachable database configuration exits non-zero
without writing readiness. There is no readiness-only stub and no
one-hour sleep fallback. The parent accepts only its exact nonce and kills a
child that cannot complete the bounded handshake.

## Zero-downtime deployment and rollback

This slice changes no schema, migration, external service, port, or broker.
For a zero-downtime rollout, start a new application generation with
`GLUDD_WRITER_MODE=subprocess`, require writer readiness as part of that
generation's health admission, shift traffic only after admission, and then
drain the old generation. A failed connection probe prevents admission
instead of exposing a half-started writer.

Rollback is configuration-only: start the replacement generation with
`GLUDD_WRITER_MODE=inline`, wait for its normal health admission, move
traffic, and drain the subprocess generation. The inline path remains the
default and this slice does not mutate its behavior.

## Resource ownership and bounds

- One `WriterProcess` instance owns at most one child. A second `start()`
  call is rejected.
- This slice adds no broker, daemon, or second restart loop. The existing
  `WriterSupervisor` retry/backoff budget remains the only supported
  supervision budget when the supervisor owns the process.
- The parent owns the temporary config and nonce files and removes them on
  stop or failed startup.
- The child owns the SQLAlchemy engine and disposes it on bootstrap failure,
  clean shutdown, and loop teardown.
- Shutdown sends SIGTERM, waits a bounded grace period, and escalates to
  SIGKILL. The default readiness and SIGTERM budgets remain 30 and 10
  seconds.
- Polling remains at a fixed 50 ms cadence; no busy loop or unbounded worker
  population is introduced.

## Upstream and community evidence

SQLAlchemy's
[multiprocessing guidance](https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork)
warns that pooled connections must not cross process boundaries because
multiple interpreters can use the same file descriptor. Creating the engine
only after the fresh child interpreter starts avoids inheriting the parent's
pool, and explicit disposal gives the child sole lifecycle ownership.

Sidekiq issue
[#5239](https://github.com/sidekiq/sidekiq/issues/5239) documents the
long-lived enqueue-before-commit failure mode: publishing work before a
database transaction commits can expose jobs whose database state does not
exist yet. S16a deliberately proves only child bootstrap and application of
an envelope that is already present. Atomic producer-side publication,
crash-safe acknowledgement, and replay semantics are deferred to S16b and
must be complete before the spool is treated as durable delivery.

## Verification

The focused suite uses a real child interpreter and SQLite database. It
proves that a flat production config is normalized, a pre-existing
`execute_sql` envelope is committed before readiness, invalid configs exit
promptly without a nonce, stop escalation stays bounded, and engine cleanup
runs across success and failure paths. The dedicated coverage configuration
measures both writer process modules, including spawned-child execution.
