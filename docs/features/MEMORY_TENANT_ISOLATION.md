# Fail-closed memory tenant isolation

S35 makes an omitted `project_id` mean the global memory partition, never an
unscoped read across all projects. An explicit project continues to select only
that project. The rule now matches `get`, `set`, `delete`, and
`LocalAgentMemory` for both repository calls and the HTTP list route.

## Problem and acceptance contract

`MemoryRepository.list_by_namespace()` previously added a project predicate
only when callers supplied `project_id`. Omitting it therefore generated a
query with no project predicate and returned global rows plus every project's
rows for the requested agent. The API's default query parameter is `None`, so a
normal `GET /api/memory/{agent_id}` could expose project-scoped memory.

The corrected contract is:

- omitted `project_id` adds `memory_records.project_id IS NULL`;
- explicit `project_id` adds equality for that project only;
- `namespace="*"` widens namespaces but never widens the project partition;
- agent, namespace, stable key ordering, TTL visibility, and the existing
  runtime list ceiling retain their previous behavior;
- the read remains one parameterized, bounded SQLAlchemy `SELECT`; and
- factory-owned calls retain one session/transaction per call while injected
  sessions retain caller-owned commit and rollback control.

Regression coverage seeds global and multiple project rows with the same agent,
then exercises repository and router reads. Concurrent factory calls prove the
three partitions stay disjoint and return all pool leases. Separate acceptance
tests prove the generated statement contains both `IS NULL` and `LIMIT`, a query
failure rolls back and closes its factory-owned transaction, and caller-owned
rows remain attached until the caller rolls back.

## Maintained dependency and practitioner evidence

This change uses SQLAlchemy's existing expression and session APIs; it adds no
schema, dependency, query wrapper, or tenancy framework. The official
[operator reference](https://docs.sqlalchemy.org/en/20/core/operators.html#identity-comparisons)
documents `ColumnOperators.is_(None)` as the portable expression for SQL
`IS NULL`. SQLAlchemy's official
[async concurrency guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
requires one `AsyncSession` per concurrent task, which the factory-owned path
already supplies.

Long-lived community reports reinforce making the partition explicit at the
query boundary:

- [SQLAlchemy discussion #11389](https://github.com/sqlalchemy/sqlalchemy/discussions/11389)
  asks how to ensure tenant identifiers are applied consistently to every ORM
  operation; the maintainer distinguishes per-query criteria from stronger
  database row-level security and recommends explicit enforcement.
- [FastAPI discussion #7564](https://github.com/fastapi/fastapi/discussions/7564)
  records multi-tenant users carrying tenant context through path or request
  dependencies, showing that FastAPI does not add storage isolation itself.
- [SQLAlchemy discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  records failures from sharing an async session under `asyncio.gather()`; the
  maintainer recommends creating one session at each task boundary.
- [SQLAlchemy discussion #10024](https://github.com/sqlalchemy/sqlalchemy/discussions/10024)
  records a multi-tenant schema-cache surprise caused by inconsistent mapping
  keys, supporting deterministic tenant criteria rather than context-sensitive
  omission.

Database row-level security would be a useful independent defense for a future
PostgreSQL-only design, but adding it here would require a schema/runtime policy
change and would not protect the supported SQLite path. S35 instead closes the
known application query gap without pretending to provide database-wide RLS.

## Resources, failure handling, and observability

The list path still issues one `SELECT`, orders by key, and applies the existing
bounded limit in SQL. The additional null predicate reduces eligible rows and
does not add a join, scan pass, write, retry, cache, file, process, thread,
listener, timer, queue, worker, daemon, or network request. Concurrency remains
bounded by the existing engine pool and factory-created session lifetime.

A database error propagates to the existing transaction context, which rolls
back and returns the checked-out connection. The route preserves its existing
error behavior; it never retries with a broader predicate. Operators can watch
the existing database-error and pool-saturation signals. No memory value,
project identifier, SQL text, or query result is added to logs or metrics.

## Zero-downtime rollout and rollback

S35 is an application-image-only rolling change. It has no migration, stored
data rewrite, dependency update, new process, or resource ownership transfer.
Old and new images can read the same table during a gradual rollout; updated
images simply return fewer, correctly partitioned rows when `project_id` is
omitted. Route traffic to the updated image, let in-flight requests finish, and
monitor database errors and pool saturation.

Rollback is a fail-closed image rollback decision: first stop new rollout
traffic and preserve evidence, then route traffic to the preceding image only
if the compatibility need outweighs reintroducing its known cross-project list
exposure. No database rollback or drain is necessary. If isolation is the
reason for rollback, keep the updated image serving reads and repair forward;
never compensate by deleting or rewriting tenant data.

## Verification

- warnings-fatal repository, router, local-memory, and lifecycle tests;
- branch-aware coverage using
  `config/coverage_memory_tenant_isolation.ini`, with at least 85% aggregate
  and 75% per measured file;
- scoped Ruff and strict mypy checks;
- Markdown, task-ledger, resource-ownership, and collection checks; and
- an exact-head full gate before the item can become formally complete.
