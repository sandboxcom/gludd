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

## Migration Checklist (17 Items)

Execute in order. Each item gates the next — do not skip ahead.

### Pre-Migration

- [ ] **1. Provision Postgres 16+ instance.** Confirm `pg_isready -h <host> -p <port>` returns accepting connections. Record server version: `SELECT version();`.
- [ ] **2. Create gludd database and role.** Run as superuser:
  ```sql
  CREATE ROLE gludd_app WITH LOGIN PASSWORD '<vault-generated>' VALID UNTIL 'infinity';
  CREATE ROLE gludd_readonly WITH LOGIN PASSWORD '<vault-generated>';
  CREATE DATABASE gludd OWNER gludd_app ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8';
  \c gludd
  GRANT CONNECT ON DATABASE gludd TO gludd_readonly;
  GRANT USAGE ON SCHEMA public TO gludd_readonly;
  ALTER DEFAULT PRIVILEGES FOR ROLE gludd_app IN SCHEMA public GRANT SELECT ON TABLES TO gludd_readonly;
  ```
- [ ] **3. Set `DATABASE_URL` in vault/env.** Value: `postgresql+psycopg://gludd_app:<password>@<host>:5432/gludd`. Never commit. Confirm the env var resolves in the deployment environment.
- [ ] **4. Add Postgres 16 service container to CI.** `.github/workflows/build.yml` needs:
  ```yaml
  services:
    postgres:
      image: postgres:16-alpine
      env:
        POSTGRES_USER: gludd_app
        POSTGRES_PASSWORD: test
        POSTGRES_DB: gludd
      ports: ["5432:5432"]
      options: >-
        --health-cmd pg_isready
        --health-interval 10s
        --health-timeout 5s
        --health-retries 5
  ```
- [ ] **5. Add `psycopg` to project dependencies.** Run: `uv add psycopg[binary]`. Track in `pyproject.toml`. Pin to psycopg 3.x (psycopg2 is legacy).

### SQLite Backup (Before Touching Postgres)

- [ ] **6. Create a verified SQLite backup.** From the daemon host:
  ```bash
  sqlite3 /path/to/gludd.db ".backup /backups/gludd-pre-pg-$(date +%Y%m%d-%H%M%S).db"
  sqlite3 /backups/gludd-pre-pg-*.db "PRAGMA integrity_check"  # must print "ok"
  ```
  Keep the backup for at least 30 days. Verify `PRAGMA integrity_check` returns `ok`. This is the rollback anchor (see Rollback Plan below).

### Migration Execution

- [ ] **7. Stop the daemon.** `systemctl stop gludd` or `pkill -f gludd`. Confirm no processes: `pgrep -f gludd`. A writer subprocess with an active SQLite WAL will corrupt the migration start point.
- [ ] **8. Run alembic upgrade head against Postgres.** From the gludd root:
  ```bash
  DATABASE_URL=postgresql+psycopg://gludd_app:<pw>@<host>:5432/gludd alembic upgrade head
  ```
  Expected: 35 migrations applied (34 existing + any new Postgres-specific ones added during audit). Any SQLite-specific DDL must be dialect-guarded via `_is_postgres()` before this step. Capture output to `/tmp/gludd-migration-$(date +%s).log`.
- [ ] **9. Verify table parity.** Run schema diff:
  ```bash
  # Produce SQLite schema
  sqlite3 /path/to/gludd.db ".schema" | sort > /tmp/sqlite-schema.txt
  # Produce Postgres schema
  PGPASSWORD=<pw> psql -h <host> -U gludd_app -d gludd -c "\d+" | sort > /tmp/pg-schema.txt
  # Diff — only dialect differences (JSON → JSONB, INTEGER → BIGINT) expected
  diff /tmp/sqlite-schema.txt /tmp/pg-schema.txt
  ```
  Known allowable diffs: SQLite `TEXT` → Postgres `JSONB` (sa.JSON columns); SQLite `INTEGER` → Postgres `BIGINT` (autoincrement PKs); SQLite `BOOLEAN` → Postgres `BOOLEAN` (semantically equivalent). Anything beyond these is a migration bug.

### Data Migration & Validation

- [ ] **10. Migrate SQLite data to Postgres.** Use `pgloader` or a custom script:
  ```bash
  pgloader sqlite:////path/to/gludd.db postgresql://gludd_app:<pw>@<host>:5432/gludd
  ```
  Or use SQLAlchemy core for a controlled transfer:
  ```python
  # dump-restore pattern: iterate SQLite tables, insert rows into Postgres
  from sqlalchemy import create_engine, select
  src = create_engine("sqlite:////path/to/gludd.db")
  dst = create_engine("postgresql+psycopg://gludd_app:<pw>@<host>:5432/gludd")
  for table in reversed(Base.metadata.sorted_tables):
      with dst.begin() as conn:
          for row in src.execute(select(table)):
              conn.execute(table.insert(), row._mapping)
  ```
- [ ] **11. Run FK constraint audit.** Postgres enforces foreign keys strictly; SQLite was lenient even with `PRAGMA foreign_keys=ON`:
  ```sql
  -- Find orphan rows in every FK column
  SELECT 'projects.project_id' AS fk_col, COUNT(*) AS orphans
  FROM todos WHERE project_id IS NOT NULL
    AND project_id NOT IN (SELECT project_id FROM projects)
  UNION ALL
  SELECT 'todos.project_id',  COUNT(*) FROM task_decisions t
  WHERE t.todo_id IS NOT NULL AND t.todo_id NOT IN (SELECT todo_id FROM todos);
  -- ... repeat for every FK defined in models.py
  ```
  Any orphan row > 0 must be cleaned up: either set FK to NULL (if nullable) or delete. Run before data goes live.
- [ ] **12. Rebuild indexes and ANALYZE.** After bulk-loading data:
  ```sql
  REINDEX DATABASE gludd;
  ANALYZE;
  VACUUM ANALYZE;
  ```
- [ ] **13. Seed queues.** The `QueueModel` bootstrap (`session.py:155-197`) uses `sqlite_insert().on_conflict_do_nothing()`. Run the Postgres equivalent:
  ```sql
  INSERT INTO queues (name, description, concurrency, active, created_at, updated_at)
  VALUES ('core', 'Default work queue', 5, true, now(), now()),
         ('urgent', 'High-priority work', 3, true, now(), now()),
         ('background', 'Low-priority background work', 2, true, now(), now()),
         ('scheduled', 'Cron-triggered jobs', 10, true, now(), now()),
         ('external', 'External-system jobs', 5, true, now(), now()),
         ('batch', 'Batch processing jobs',  3, true, now(), now())
  ON CONFLICT (name) DO NOTHING;
  ```
  Adjust `INITIAL_QUEUES` in `session.py` to match if the list has changed.

### Post-Migration

- [ ] **14. Un-gate Postgres in `session.py`.** Remove the `ValueError` blocks from `init_engine_from_config` (`session.py:88-93`) and `init_read_only_engine_from_config` (`session.py:118-123`). Replace with the dialect-aware guard from Step 1 of the Migration Plan.
- [ ] **15. Update `_clamp_workers_for_sqlite`.** In `cli.py:3345`: skip clamping when the engine URL dialect is `postgresql`. Set default workers to `min(4, os.cpu_count())`.
- [ ] **16. Start daemon in Postgres mode and run smoke tests.** Boot with:
  ```bash
  DATABASE_URL=postgresql+psycopg://gludd_app:<pw>@<host>:5432/gludd gludd daemon start --workers 4
  ```
  Verify: `curl http://localhost:8000/health` returns 200. `curl http://localhost:8000/api/facts | jq '.pool'` shows connected.
- [ ] **17. Run the `requires_postgres` test suite.** `pytest -m "requires_postgres" -v`. All tests must pass. Re-run `make gate` with `GLUDD_DB_DIALECT=sqlite` to confirm backward compatibility.

## Rollback Plan

If the Postgres migration fails or post-migration issues are discovered, follow this plan. **Never skip the SQLite backup step (item 6 above) — it is the rollback anchor.**

### Rollback Triggers

Initiate rollback if ANY of these conditions are met:
- `alembic upgrade head` against Postgres fails on any migration.
- Schema diff (item 9) shows unexpected differences beyond dialect type mappings.
- FK constraint audit (item 11) finds unfixable orphan count > 0.
- The `requires_postgres` test suite (item 17) does not pass.
- Connection pool exhaustion or Postgres performance regression is observed in staging.

### Rollback Procedure (5 Steps)

**Step R1 — Stop the daemon.**
```bash
systemctl stop gludd || pkill -f "gludd daemon"
while pgrep -f gludd; do sleep 2; done  # confirm stopped
```

**Step R2 — Restore SQLite from backup.**
```bash
sqlite3 /path/to/gludd.db < /backups/gludd-pre-pg-YYYYMMDD-HHMMSS.db
# Verify integrity
sqlite3 /path/to/gludd.db "PRAGMA integrity_check"  # must print "ok"
```

**Step R3 — Re-enable the Postgres hard gate.**
Revert Step 14 (the `session.py` un-gate). Restore the `ValueError` blocks at `session.py:88-93` and `session.py:118-123`:
```python
if not is_sqlite_url(url):
    raise ValueError(
        "PostgreSQL is not yet supported. Set GLUDD_DB_DIALECT=sqlite or "
        "use a SQLite URL. See docs/POSTGRES_MULTI_WORKER.md."
    )
```

**Step R4 (if workers were unclamped) — Restore worker clamp.**
Revert Step 15. In `cli.py:3345`, restore `_clamp_workers_for_sqlite` to always clamp to 1:
```python
# Revert to: workers=1 clamp active for all URLs
effective_workers = 1
```

**Step R5 — Start daemon in SQLite mode and verify.**
```bash
DATABASE_URL=sqlite:////path/to/gludd.db gludd daemon start --workers 1
curl http://localhost:8000/health  # expect 200
curl http://localhost:8000/api/facts | jq '.todo_count'  # verify data intact
make test  # confirm full test suite passes
```

### Rollback Recovery Validation

After rollback, run these checks before resuming normal operations:
```bash
make gate                         # full gate green on SQLite
make git-log                      # confirm no migration-adjacent commits landed
make verify-state                 # branch state clean
sqlite3 /path/to/gludd.db "SELECT COUNT(*) FROM todos"  # row count preserved
```

### Post-Rollback Actions

- [ ] Document the failure in `BUGS.md` with: failing step number, error message, and migration log.
- [ ] Fix the root cause (migration DDL, data integrity, pool config).
- [ ] Re-run the full migration checklist from item 1.
- [ ] Do NOT delete the Postgres database — it contains the failure evidence needed for debugging.

## Testing Strategy

### Test Pyramid for Postgres Migration

| Layer | What | Tool | Success Criterion |
|-------|------|------|-------------------|
| **Unit** | `_is_postgres()` dialect helpers; `get_alembic_config()` URL resolution; `_compose_db_url()` Postgres branch | pytest (SQLite backend) | 100% code coverage on all `is_postgres` / dialect-switch branches |
| **Migration** | All 35+ alembic migrations run cleanly offline + online against Postgres 16 | `tests/cli/test_migration_dialects.py` | `alembic upgrade head` succeeds; `alembic downgrade base` succeeds; no migration leaves dangling tables |
| **Integration** | ORM models instantiate, query, commit against Postgres; FK constraints enforced | `tests/integration/conftest.py` with `requires_postgres` fixture | Full CRUD cycle for every model; FK violation raises `IntegrityError`; `pool_pre_ping` reconnects after server restart |
| **Schema parity** | SQLite schema == Postgres schema (modulo dialect diffs) | `tests/unit/test_schema_parity.py` | Diff output contains only allowed dialect differences |
| **E2E** | Daemon boots, serves requests, processes a todo lifecycle with N>1 workers | `tests/e2e/test_multi_worker_postgres.py` | `gludd daemon start --workers 4` → health 200 → create/assign/complete todo → `curl /api/facts` shows the closed todo |

### Test Infrastructure

**Local (developer workstation):**
```bash
# Start ephemeral Postgres via docker
docker run -d --name gludd-pg-test -p 5432:5432 \
  -e POSTGRES_USER=gludd_app -e POSTGRES_PASSWORD=test -e POSTGRES_DB=gludd \
  postgres:16-alpine

# Run migration tests
DATABASE_URL=postgresql+psycopg://gludd_app:test@localhost:5432/gludd \
  pytest tests/cli/test_migration_dialects.py -v

# Run integration tests
DATABASE_URL=postgresql+psycopg://gludd_app:test@localhost:5432/gludd \
  pytest -m "requires_postgres" -v

# Clean up
docker rm -f gludd-pg-test
```

**CI (GitHub Actions):**
```yaml
test-postgres:
  runs-on: ubuntu-latest
  services:
    postgres:
      image: postgres:16-alpine
      env: {POSTGRES_USER: gludd_app, POSTGRES_PASSWORD: test, POSTGRES_DB: gludd}
      ports: ["5432:5432"]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: {python-version: "3.11"}
    - run: pip install '.[dev]' psycopg[binary]
    - run: pytest -m "requires_postgres" -v --cov=src/general_ludd/db
      env:
        DATABASE_URL: postgresql+psycopg://gludd_app:test@localhost:5432/gludd
```

### Tests to Write (Before Un-Gating)

| Test file | What it covers | Priority |
|-----------|---------------|----------|
| `tests/cli/test_migration_dialects.py` | `alembic upgrade head` + `alembic downgrade base` against Postgres 16; offline + online modes; all 35+ migrations | P0 (blocking) |
| `tests/unit/test_schema_parity.py` | Dump schema from SQLite and Postgres; diff; assert only dialect-expected differences | P0 (blocking) |
| `tests/integration/test_postgres_fk_enforcement.py` | Insert orphan row → assert `IntegrityError`; verify SET NULL / CASCADE behavior per FK definition | P1 |
| `tests/integration/test_postgres_pool.py` | Set `max_connections=10`; spawn 12 concurrent queries; assert `pool.overflow` fires; no `QueuePool limit` errors | P1 |
| `tests/unit/test_dialect_switches.py` | Mock `engine.url.get_dialect().name`; assert `_is_postgres()` branches in `repository.py` (4 sites), `session.py` (2 sites) | P0 (blocking) |
| `tests/integration/test_postgres_jsonb.py` | Insert/query `JSON` columns; assert round-trip fidelity; assert `->>` and `@>` operators work | P1 |
| `tests/e2e/test_multi_worker_postgres.py` | Daemon boot with 4 workers; create todo via API; claim + complete; verify `/api/facts` reflects state | P1 |
| `tests/unit/test_rollback.py` | Mock alembic failure; assert rollback script exits 0; assert `PRAGMA integrity_check` passes on restored SQLite | P2 |
| `tests/integration/test_postgres_timestamp_tz.py` | Insert row with `CURRENT_TIMESTAMP`; assert it is UTC (not local); assert `DateTime(timezone=True)` columns store `timestamptz` | P1 |

### Dialect-Specific Test Helpers

```python
# tests/conftest.py or tests/integration/conftest.py
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

def is_postgres(engine: AsyncEngine) -> bool:
    return engine.url.get_dialect().name == "postgresql"

@pytest.fixture
def pg_engine() -> AsyncEngine:
    """Require DATABASE_URL pointing to a reachable Postgres instance."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("DATABASE_URL is not a postgresql URL")
    engine = create_async_engine(url, echo=False)
    return engine

def assert_schema_parity(sqlite_url: str, pg_url: str) -> None:
    """Dump both schemas, compare, assert only dialect diffs."""
    ...

# Mark tests requiring a live Postgres
# In pytest.ini / pyproject.toml:
# [tool.pytest.ini_options]
# markers = ["requires_postgres: tests that need a live Postgres instance"]
```

## Verification Gate

- [ ] `make gate` green with `GLUDD_DB_DIALECT=postgresql`
- [ ] `pytest -m "requires_postgres"` passes (local Postgres instance required)
- [ ] `make gate` green with `GLUDD_DB_DIALECT=sqlite` (backward compat)
- [ ] `gludd daemon start --workers 4` boots + serves requests with Postgres
- [ ] Writer subprocess survives SIGTERM + restart without queue loss
- [ ] All 35+ migrations run cleanly: `alembic upgrade head` against Postgres 16 (online + offline)
- [ ] `alembic downgrade base` runs cleanly against Postgres 16 (no dangling tables)
- [ ] FK constraint audit: no orphan rows in migrated data
- [ ] Schema parity diff: only dialect-expected differences (JSON→JSONB, INTEGER→BIGINT, BOOLEAN→BOOLEAN)
- [ ] Connection pool monitoring: `pool.checkedout` + `pool.overflow` metrics wired to `/api/facts`
- [ ] Rollback procedure tested end-to-end: Postgres failure → SQLite restore → daemon boots → data intact

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
