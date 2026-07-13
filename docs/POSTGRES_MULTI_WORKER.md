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

### SQLite-specific patterns in source

Several code paths are SQLite-only and need Postgres branches:

| Pattern | Locations | Postgres equivalent |
|---------|-----------|---------------------|
| `sqlite_insert().on_conflict_do_nothing()` | `repository.py` (4 sites), `session.py:161-184` | `postgresql_insert().on_conflict_do_nothing()` or native `INSERT ... ON CONFLICT DO NOTHING` |
| `PRAGMA journal_mode=WAL` etc. | `session.py:47-56` | N/A (skip for Postgres; set `statement_timeout` etc. on connect) |
| `PRAGMA query_only=ON` | `session.py:107-108` | Use read-only connection role / `SET SESSION default_transaction_read_only=on` |
| `Base.metadata.create_all` | `session.py:151` | Replace with Alembic upgrade head for Postgres; `create_all` is dev-only |
| SQLite connection string construction | `session.py:32-34` | `_compose_db_url` at `session.py:60-76` already handles Postgres URL construction |
| Dialect-specific `insert()` imports | `repository.py` (4 sites), `session.py:161` | Switch on `engine.url.get_dialect().name` at each site |

## Migration Plan (5 Steps)

### Step 1 — Un-gate Postgres URLs

Remove the `ValueError` block from `init_engine_from_config` and
`init_read_only_engine_from_config` (`db/session.py:88-93`, :118-123). Replace
with a dialect-aware guard:

```python
if not is_sqlite_url(url):
    logger.warning("Postgres mode: migrations and schema creation must be "
                   "verified against Postgres 16 before production use")
    # No hard block; let the engine boot. Failures surface as connection errors.
```

### Step 2 — Alembic Against Postgres

**Current state:** `alembic.ini` hardcodes a sqlite URL. 34 migrations exist
(001–033) under `alembic/versions/`. `get_alembic_config()` at `db/migrations.py:12-21`
accepts a `url` parameter but defaults to `DATABASE_URL` env var or `sqlite:///./test.db`.

**Required changes:**

1. **Add `ALEMBIC_DB_URL` env var override** to `get_alembic_config()` (trivial).
2. **Audit all 34 migrations** for SQLite-only DDL. Migrations `007` and `008`
   already have `_is_postgres()` branches for partial unique indexes.
   Known patterns needing Postgres branches:
   - `CREATE UNIQUE INDEX ... WHERE` (SQLite syntax) → `CREATE UNIQUE INDEX ... WHERE` (same in PG) or `CREATE UNIQUE INDEX ON ... (col) WHERE cond`
   - `ALTER TABLE ... RENAME COLUMN` — fine in both
   - `sa.Text()` type — lenient in SQLite, maps to `TEXT` in both
   - `server_default=sa.text("CURRENT_TIMESTAMP")` — SQLite uses UTC; Postgres defaults to `now()`
   - Auto-increment (`autoincrement=True` on Integer PK) — SQLite uses `INTEGER PRIMARY KEY`; Postgres uses `SERIAL`/`IDENTITY`
3. **Run `alembic upgrade head`** on Postgres 16; iterate on failing migrations.
4. **Replace `Base.metadata.create_all`** in `daemon.py` startup with `alembic upgrade head`
   when the engine is Postgres (SQLite keeps `create_all` for dev mode).

### Step 3 — Schema Creation on Postgres

`Base.metadata.create_all` runs at daemon startup for SQLite dev mode. For Postgres:

- Use `alembic upgrade head` instead — `create_all` is a dev convenience that skips
  migration history and can produce tables that differ from the migration chain.
- `QueueModel` + `INITIAL_QUEUES` seeding (`session.py:155-197`) uses
  `sqlite_insert().on_conflict_do_nothing()` — needs a dialect branch.
- Column-type mismatches: SQLite accepts any type affinity; Postgres is strict.
  Known risks: `JSON` columns (SQLite stores as TEXT; Postgres has native `JSONB`),
  `Boolean` (SQLite uses INTEGER 0/1; Postgres has native `BOOLEAN`),
  `DateTime` with `server_default` strings (SQLite stores as TEXT; Postgres needs
  `server_default=func.now()`).

### Step 4 — Remove Worker Clamp

In `_clamp_workers_for_sqlite` (`cli.py:3345-3363`): skip clamping when the
engine URL is Postgres. Default worker count: `min(4, os.cpu_count())` in
Postgres mode. The clamp currently always returns 1 because the Postgres block
in `session.py` prevents any other path. Once the block is removed, the clamp
must check `is_sqlite_url(str(engine.url))` before clamping.

### Step 5 — Connection Pool Tuning

Postgres uses SQLAlchemy's `AsyncAdaptedQueuePool` (pool_size=5, max_overflow=10);
SQLite uses `NullPool` (no pooling — WAL mode with one writer). Tuning:

| Component | Pool type | Pool size | Notes |
|-----------|-----------|-----------|-------|
| HTTP worker (read-only) | QueuePool | 3 per worker | `pool_size=3, max_overflow=2` |
| Writer subprocess | QueuePool | 1 | Single writer; no contention |
| Admin CLI commands | NullPool | 1 per invocation | Transient; not a daemon process |
| **Total max** | — | `(workers × 5) + 1` | Keep under DB `max_connections` (default 100) |

Configure per-engine-pool via `connect_args` and `poolclass` in `init_async_engine()`.

## Cross-Worker IPC (Future)

With Postgres and N>1 workers, the `Broker` swaps from `InProcessBroker` to
Redis pub/sub (existing MCP catalog entry) or POSIX MQ. Cross-worker IPC is
only needed for *notifications* (job status changes, config reloads) — not for
data consistency, which Postgres handles via its own locking.

### What the IPC layer currently does

| Component | File | Role |
|-----------|------|------|
| `Broker` protocol | `ipc/broker.py` | Transport seam — `InProcessBroker` default |
| `WriteQueue` | `ipc/queue.py` | Bounded queue of mutating `Envelope` objects |
| `bridge.py` | `writer/bridge.py` | HTTP worker → writer subprocess bridge |
| `process.py` | `writer/process.py` | Writer subprocess lifecycle |
| `supervisor.py` | `writer/supervisor.py` | Watches writer process, restarts on crash |

All five are tested for single-worker operation. Multi-worker requires:

1. Replace `InProcessBroker` with Redis (or RabbitMQ/Posix MQ)
2. Add fan-out notifications: config reload, schema cache invalidation
3. Session affinity is NOT needed — HTTP workers are stateless; all state is in Postgres

## Alembic Migration Inventory

34 migrations in `alembic/versions/` (001–033). Each needs Postgres audit:

| Migration | Summary | SQLite-specific risk |
|-----------|---------|----------------------|
| 001 | Initial schema (todos, queues, tasks, etc.) | `autoincrement=True` on PKs, `JSON` columns |
| 002 | Add projects + project_id FK | `ALTER TABLE ADD COLUMN` — fine in both |
| 003 | Add plan_artifact table | `JSON` column, `TEXT` defaults |
| 004 | Add benchmark tables | `Float`, `JSON` columns |
| 005 | Add runtime tables | `TEXT` / `DATETIME` columns |
| 006 | Add D9 foreign keys | FK constraints — stricter in Postgres (need indexes on FK columns) |
| 007 | Add task_embeddings | Has `_is_postgres()` branch — partial unique index |
| 008 | Add project_relationships | Has `_is_postgres()` branch — partial unique index |
| 009 | Add benchmark project_id | FK constraint |
| 010–033 | Various: todo columns, indexes, FKs, perf indexes | `CREATE INDEX`, `ALTER TABLE ADD COLUMN` — mostly portable |

**Key audit checklist per migration:**
- [ ] `sa.JSON` / `sa.JSON()` → Postgres uses `JSONB` (prefer explicit `postgresql.JSONB`)
- [ ] `autoincrement=True` → Postgres needs `Identity()` or `SERIAL`
- [ ] `server_default=sa.text("CURRENT_TIMESTAMP")` → fine in both (UTC)
- [ ] `server_default=sa.text("'value'")` → SQLite accepts; Postgres needs `server_default="value"`
- [ ] `sa.Boolean()` → SQLite uses INTEGER; Postgres uses BOOLEAN (auto-mapped by SQLAlchemy)
- [ ] `CREATE UNIQUE INDEX ... WHERE` → fine in Postgres 9.5+
- [ ] `ON DELETE SET NULL` / `ON DELETE CASCADE` → fine in both
- [ ] `sa.Text().with_variant(...)` → dialect-specific; check Postgres variant

## Gated Prerequisites (Before Owner Go-Ahead)

These must be resolved BEFORE Step 1 of the migration plan is un-gated:

### Owner decision items

- [ ] **Database server provisioning.** Self-hosted Postgres 16+ or managed (RDS, Cloud SQL,
  Azure PostgreSQL Flexible Server). Decide on instance size, backup policy, and
  high-availability config.
- [ ] **Secrets management.** Postgres credentials must be stored in OpenBao (HashiCorp Vault)
  or env vars, never committed. The daemon already reads `DATABASE_URL` from the
  environment; a Vault path would need `get_secret("postgres/creds/gludd")`.
- [ ] **Network access.** Workers and writer subprocess need TCP access to the Postgres
  server. Containerized deployment needs service discovery or a static host:port.
- [ ] **SQLite backward compatibility commitment.** Will SQLite remain a supported
  development mode after Postgres is production? This determines whether the SQLite
  code paths stay or get `DeprecationWarning`.
- [ ] **Migration rollback plan.** If Postgres migration fails in production, the
  rollback path is: stop daemon → restore SQLite from backup → restart with
  `GLUDD_DB_DIALECT=sqlite`. Script and test this path.

### Technical prerequisites

- [ ] **CI pipeline has Postgres 16 service container.** `.github/workflows/build.yml`
  needs a `services: postgres:16` block for the `requires_postgres` test marker.
- [ ] **Test infrastructure.** `docker compose` or `pg_tmp` for local Postgres testing.
  All developers need a way to run the `requires_postgres` test suite locally.
- [ ] **Alembic multi-dialect test.** A CI job that runs all 34 migrations against
  SQLite *and* Postgres, asserting both produce identical table schemas (modulo
  dialect differences in column types).
- [ ] **Container image updated.** Add `postgresql-client` or `psycopg` to the
  Dockerfile; update `docker-compose.yml` with a `postgres` service.

## Container Deployment Considerations

### SQLite in containers (current)

The SQLite WAL file lives in the container filesystem and is lost on redeploy.
Workaround: bind-mount a persistent volume at the `GLUDD_DB_PATH` directory.
This works but couples the container to a host filesystem path.

### Postgres in containers (target)

Two patterns:

1. **Sidecar Postgres container** (dev / small deploys):
   ```yaml
   services:
     gludd:
       environment:
         - DATABASE_URL=postgresql+psycopg://gludd:${PG_PASSWORD}@postgres:5432/gludd
     postgres:
       image: postgres:16-alpine
       volumes:
         - pgdata:/var/lib/postgresql/data
   ```
2. **Managed Postgres** (production): The daemon connects to an external Postgres
   via `DATABASE_URL`. No sidecar. The container is stateless — redeploy freely.

### Connection handling at container start

- `init_engine_from_config` must be connection-retry-aware (exponential backoff on
  `OperationalError: could not connect to server`). Current behavior: raises immediately.
  The container orchestrator retries, but a 3-retry loop in the daemon is more robust.
- Health check endpoint (`GET /health`) should verify DB connectivity, not just
  process liveness. Without Postgres, the daemon is alive but non-functional.

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Migration 00N fails on Postgres due to SQLite-specific DDL | High | Medium | Pre-audit all 34 migrations; run in CI against Postgres before merge |
| Column type mismatch (JSON → JSONB, Boolean → BOOLEAN) | Medium | High | SQLAlchemy handles most type mapping; verify with `\\d+ tablename` in Postgres |
| WAL pragma removal causes SQLite performance regression | Low | Low | WAL pragmas are SQLite-only and already guarded by `is_sqlite_url()` |
| Writer subprocess queue loss on Postgres | Medium | Low | Writer uses bounded `WriteQueue` with at-least-once delivery; Postgres transactions are ACID |
| Connection pool exhaustion (workers × pool_size > max_connections) | High | Medium | Tune pool sizes; add `pool_pre_ping=True` to detect stale connections |
| FK constraint violations (SQLite was lenient) | Medium | Medium | `PRAGMA foreign_keys=ON` already set; Postgres enforces by default. Existing data may have orphans — run `ANALYZE` + FK check before migration |
| Redis dependency for cross-worker IPC | Low | Low | `InProcessBroker` works for N=1; Redis is only needed for N>1 pub/sub. Can defer to Phase 2 |
| No rollback path without SQLite backup | High | Low | Documented in gated prerequisites above; script it before enabling |

## Verification Gate

- [ ] `make gate` green with `GLUDD_DB_DIALECT=postgresql`
- [ ] `pytest -m "requires_postgres"` passes (local Postgres instance required)
- [ ] `make gate` green with `GLUDD_DB_DIALECT=sqlite` (backward compat)
- [ ] `gludd daemon start --workers 4` boots + serves requests with Postgres
- [ ] Writer subprocess survives SIGTERM + restart without queue loss
- [ ] All 34 migrations run cleanly: `alembic upgrade head` against Postgres 16
- [ ] FK constraint audit: no orphan rows in migrated data
- [ ] Connection pool monitoring: `pool.checkedout` + `pool.overflow` metrics wired to `/api/facts`

## Related Files

| File | Role |
|------|------|
| `src/general_ludd/db/session.py` | Engine factory + Postgres block (lines 88-93, 118-123) |
| `src/general_ludd/cli.py:3345` | Worker clamp for SQLite (`_clamp_workers_for_sqlite`) |
| `src/general_ludd/db/migrations.py` | `get_alembic_config()` — needs `ALEMBIC_DB_URL` env var |
| `alembic.ini` | Hardcoded SQLite URL — needs env var override |
| `alembic/versions/` | 34 migrations (001–033); 007+008 have `_is_postgres()` branches |
| `src/general_ludd/db/repository.py` | 4 `sqlite_insert()` sites need dialect branches |
| `src/general_ludd/ipc/broker.py` | Transport seam for cross-worker pub/sub |
| `src/general_ludd/ipc/queue.py` | Bounded WriteQueue (Phase 1) |
| `src/general_ludd/writer/bridge.py` | HTTP worker → writer bridge |
| `src/general_ludd/writer/process.py` | Writer subprocess lifecycle |
| `src/general_ludd/writer/supervisor.py` | Writer supervisor with restart |
| `src/general_ludd/connectors/postgres_stats.py` | Read-only stats connector |
| `src/general_ludd/daemon.py:1072-1079` | Alembic stamp_head call on SQLite startup; needs Postgres branch |
| `.github/workflows/build.yml` | CI pipeline — needs Postgres service container |
| `docker-compose.yml` | Container deployment — needs Postgres service |
