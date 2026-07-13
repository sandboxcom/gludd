# Postgres Migration Path & Multi-Worker Architecture

Status: PLAN (gated on owner go-ahead — do NOT implement)

## Motivation

SQLite is the default with two hard limits:
1. **No cross-process coordination.** Gunicorn pre-forks N workers but SQLite WAL
   mode has no row-level locking — concurrent dispatching races on the same rows.
   `_clamp_workers_for_sqlite` (`cli.py:3345`) forces workers=1 as a safety measure.
2. **No durability in containers.** SQLite files inside a container are lost on
   redeploy without a manually mounted persistent volume.

The IPC layer (broker, write queue, writer subprocess) is already built. The hard
block on Postgres (`init_engine_from_config` raises `ValueError` at `db/session.py:89`)
is a deliberate safety gate — migrations and schema creation are SQLite-tested only.

## Current Architecture (Phase 1 — SQLite, IPC Ready)

HTTP workers open read-only DB sessions; one writer subprocess owns all mutating
sessions via the `WriteQueue` + `Envelope` IPC. The `Broker` protocol (`ipc/broker.py`)
provides an `InProcessBroker` default with a transport seam for Redis/POSIX MQ.
`_clamp_workers_for_sqlite` forces workers=1; with Postgres this clamp is removed.

## Migration Plan (5 Steps)

**Step 1 — Un-gate Postgres URLs:** Remove the `ValueError` block from
`init_engine_from_config` and `init_read_only_engine_from_config`
(`db/session.py:88-93`, :118-123). Keep a deprecation warning for SQLite if
`GLUDD_DB_DIALECT` != `postgresql`.

**Step 2 — Alembic Against Postgres:** `alembic.ini` hardcodes a sqlite URL;
add env-var override (`ALEMBIC_DB_URL`). Migrations `007` and `008` already
have `_is_postgres()` branches for partial unique indexes. Audit all other
migrations for SQLite-only DDL needing Postgres branches. Run `alembic upgrade
head` on Postgres 16; fix any failing migrations.

**Step 3 — Schema Creation on Postgres:** `Base.metadata.create_all` runs at
daemon startup. Test against Postgres; fix column-type mismatches (SQLite is
lenient, Postgres is strict). `QueueModel` + `INITIAL_QUEUES` seeding must be
idempotent on Postgres.

**Step 4 — Remove Worker Clamp:** In `_clamp_workers_for_sqlite`: skip clamping
when the engine URL is Postgres. Default to `min(4, os.cpu_count())` in Postgres
mode.

**Step 5 — Connection Pool Tuning:** Postgres uses SQLAlchemy's default pool
(pool_size=5, max_overflow=10); SQLite uses NullPool. Writer subprocess gets
its own 1-connection pool; each HTTP worker has a read-only pool. Total =
`(workers × pool_size) + 1`. Keep under the DB server's `max_connections`.

## Cross-Worker IPC (Future)

With Postgres and N>1 workers, the `Broker` swaps from `InProcessBroker` to
Redis pub/sub (existing MCP catalog entry) or POSIX MQ. Cross-worker IPC is
only needed for *notifications* (job status changes, config reloads) — not for
data consistency, which Postgres handles via its own locking.

## Verification Gate

- [ ] `make gate` green with `GLUDD_DB_DIALECT=postgresql`
- [ ] `pytest -m "requires_postgres"` passes (local Postgres instance required)
- [ ] `make gate` green with `GLUDD_DB_DIALECT=sqlite` (backward compat)
- [ ] `gludd daemon start --workers 4` boots + serves requests with Postgres
- [ ] Writer subprocess survives SIGTERM + restart without queue loss

## Related Files

| File | Role |
|------|------|
| `src/general_ludd/db/session.py` | Engine factory + Postgres block |
| `src/general_ludd/cli.py:3345` | Worker clamp for SQLite |
| `src/general_ludd/ipc/broker.py` | Transport seam for cross-worker pub/sub |
| `src/general_ludd/ipc/queue.py` | Bounded WriteQueue (Phase 1) |
| `src/general_ludd/writer/bridge.py` | HTTP worker → writer bridge |
| `src/general_ludd/writer/process.py` | Writer subprocess lifecycle |
| `src/general_ludd/writer/supervisor.py` | Writer supervisor with restart |
| `src/general_ludd/connectors/postgres_stats.py` | Read-only stats connector |
| `alembic.ini` | Hardcoded SQLite URL |
| `alembic/versions/007_*.py`, `008_*.py` | Migrations with Postgres branches |
