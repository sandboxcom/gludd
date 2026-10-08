# Dialect-native repository upserts

S37 makes the variable, feature, and prompt-profile repositories select the
native SQLAlchemy `INSERT ... ON CONFLICT` builder for the database bound to
their caller-owned `AsyncSession`. SQLite and PostgreSQL are supported;
unrecognized dialects fail before database I/O. The change adds no schema,
migration, dependency, process, or public API.

## Contract

- `dialect_insert(model, dialect_name)` is the shared, fail-closed selection
  seam for SQLite and PostgreSQL insert subclasses.
- `VariableNamespaceRepository.set_var()`, `FeatureRepository.upsert()`, and
  `PromptProfileRepository.upsert()` derive the dialect from
  `session.get_bind().dialect.name`; no global engine or URL is consulted.
- The repositories retain their existing atomic conflict keys, last-writer
  semantics, identity-map refreshes, and `flush()` behavior.
- Repositories never commit, roll back, close, or replace the supplied session.
  Transaction lifetime and error recovery remain the caller's responsibility.
- An unsupported backend raises `ValueError` before `execute()`, avoiding a
  misleading SQLite statement on a database whose conflict behavior is not
  covered by Gludd's acceptance tests.

## Maintainer and practitioner evidence

The implementation follows the SQLAlchemy maintainers' answers to two
long-lived usage reports:

- [Cross Dialect Compatibility #7199](https://github.com/sqlalchemy/sqlalchemy/discussions/7199)
  explains that SQLite and PostgreSQL use separate conflict-capable insert
  constructs. The maintainer recommends selecting after a connection or
  session reveals the actual backend, failing on unsupported backends, and
  testing the operation against every supported database. Gludd therefore
  performs dispatch at the bound-session repository boundary and tests both
  generated SQL and live behavior.
- [SQLite `on_conflict_do_nothing` #7007](https://github.com/sqlalchemy/sqlalchemy/discussions/7007)
  records the common `Insert`-has-no-conflict-method failure. The accepted
  answer states that generic `Table.insert()` lacks dialect extensions and the
  SQLite dialect's `insert()` must be used. Gludd returns the corresponding
  dialect subclass instead of trying to attach conflict methods to a generic
  statement.

This is intentionally a small dispatch helper rather than a new abstraction
over SQLAlchemy's conflict API. Each repository still declares its own unique
key and update set, where its data contract is visible and testable.

## Resource, isolation, and failure boundaries

Statement selection is synchronous and constant-space. It creates no engine,
connection, session, retry loop, queue, thread, process, or background task.
Concurrency stays bounded by the existing engine pool and worker limits. The
live PostgreSQL acceptance uses exactly two pooled connections, permits no
overflow, and applies a 20-second convergence deadline.

Project identity continues to flow through the existing variable and feature
values. No tenant predicate, serialization rule, secret boundary, or logging
payload changes. SQL parameters remain bound values; the helper does not
interpolate user data into SQL or log statement contents.

If statement construction or execution fails, the exception propagates through
the existing repository call. The caller must roll back its transaction before
reusing that session, exactly as before. The helper performs no hidden retry:
retry ownership and idempotency remain with the existing job/session boundary.

## Zero-downtime delivery and rollback

The release is schema-free and is safe for a rolling application deployment.
Old and new workers read the same tables and constraints; drain or replace old
workers before directing PostgreSQL writes through these three repositories.
Monitor existing database-error and pool-saturation signals while the new image
rolls out. SQLite single-worker development remains compatible.

Rollback is an application-image rollback only. Stop new work, allow active
transactions to finish or explicitly roll them back, then restore the previous
image. No migration downgrade, row rewrite, cache flush, or data deletion is
required. If PostgreSQL writes are active, repair forward is preferred because
the previous implementation hard-codes SQLite's statement subclass.

## Verification

- unit compilation proves both SQLite and PostgreSQL emit native
  `ON CONFLICT (name) DO UPDATE` SQL;
- unit regressions prove all three repositories reject an unknown dialect
  before their first database call;
- existing warning-fatal SQLite concurrency tests prove conflicting variable,
  feature, and prompt-profile first writes converge to one row;
- the existing namespaced disposable PostgreSQL 16 target now runs two
  concurrent sessions through all three repositories, with a pool size of two,
  zero overflow, a bounded deadline, and one-row convergence assertions; and
- warning-fatal focused and adjacent coverage executed 354 tests with no
  failures; `config/coverage_dialect_native_repository_upserts.ini` measured
  94% aggregate coverage, with `shared.py` at 87%, `projects.py` at 98%, and
  `metrics.py` at 91%; and
- scoped Ruff, strict mypy, Markdown, ownership/task checks, collection, and an
  exact-head full gate remain release prerequisites.
