# Project-isolated persisted tool outputs

S36 binds every persisted generated-tool result to the validated project of
the todo that produced it. Project-scoped variable reads still inherit global
configuration, but they never inherit the legacy global `tool_results`
namespace. No schema, dependency, or public API type changes are required.

## Problem and acceptance contract

Generated-tool dispatch had two successful-result paths: the direct structured
result path and the phase-two model tool-call path. Both wrote variables in the
`tool_results` namespace without passing the todo's validated `project_id`.
Those rows therefore entered the global partition. The project variable loader
also merged every global namespace into a project read, so one project's tool
output could become input to another project.

The corrected contract is:

- both dispatch paths pass the already validated todo `project_id` to
  `VariableNamespaceRepository.set_var()`;
- a generated result is stored under that project and remains visible to the
  originating project;
- project B cannot see project A's result or a pre-existing global
  `tool_results` row;
- global namespaces other than `tool_results` still merge into project reads,
  while project values retain their existing precedence;
- an unscoped global read keeps its legacy global-partition behavior; and
- failed tool calls, result recording, session ownership, and tenant checks
  retain their existing semantics.

The repository change is deliberately namespace-specific. It quarantines old
global tool-result rows in place instead of deleting or rewriting them, and it
does not alter the behavior of global configuration such as shared model or
runtime settings.

## Maintained dependency and practitioner evidence

The implementation uses existing SQLAlchemy predicates and the current
FastAPI/event-loop repository boundary. It adds no tenancy library, migration,
table, index, worker, or dependency. SQLAlchemy's official
[async concurrency guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
requires a separate `AsyncSession` for each concurrent task; S36 preserves the
S33/S35 factory-owned and caller-owned session rules instead of introducing a
shared session.

Long-lived practitioner reports reinforce enforcing context at the persistence
and query boundaries:

- [FastAPI discussion #11625](https://github.com/fastapi/fastapi/discussions/11625)
  records parallel-request failures when a session is shared and recommends
  separate dependency-provided sessions.
- [FastAPI discussion #4223](https://github.com/fastapi/fastapi/discussions/4223)
  describes the request dependency as the unit that creates, yields, and
  closes a database session for an endpoint.
- [SQLAlchemy discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  records failures from sharing one async session across `asyncio.gather()`;
  the maintainer recommends a session per task.
- [SQLAlchemy discussion #9201](https://github.com/sqlalchemy/sqlalchemy/discussions/9201)
  recommends explicit per-request async sessions and notes that scoped async
  session management is intricate.
- [MCP issue #1087](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1087)
  asks how to prevent cross-user state exposure; an MCP member explains that
  server developers own the session-isolation mechanism.
- [MCP discussion #193](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/193)
  discusses carrying multi-tenant client context through tool execution rather
  than relying on shared server state.

These reports do not make application isolation automatic. S36 therefore
passes the validated project identity explicitly through both writes and makes
the project read predicate fail closed for the sensitive namespace.

## Resources, failure handling, and observability

Project variable loading remains one parameterized SQLAlchemy `SELECT` with
the existing runtime list ceiling and ordering. The additional predicate
reduces eligible global rows; it adds no query, join, retry, cache, background
task, process, thread, timer, queue, listener, or network request. Each
successful generated result still performs the existing bounded upsert, now in
the project partition. Concurrency remains bounded by the existing database
engine pool and event-loop dispatch caps.

Repository and dispatch exceptions continue through their existing rollback
and nonfatal-result paths. Factory-owned sessions are closed by their existing
context; injected sessions remain caller-owned. No result payload, project
identifier, SQL text, or tenant data is newly emitted to logs or metrics.

## Zero-downtime rollout and rollback

S36 is an application-image-only rolling change. There is no migration or data
rewrite, so old and new images remain database-compatible while in-flight work
finishes. Deploy the reader and both writer-path changes together, route new
todos to updated instances, and monitor existing database-error, pool, and
tool-dispatch failure signals. Legacy global tool rows remain stored but are
quarantined from project-scoped reads.

Rollback requires an explicit confidentiality decision: reverting the reader
predicate **re-exposes every legacy-global `tool_results` row to every
project-scoped variable read**. If isolation motivates the rollback, keep the
updated reader serving traffic and repair forward. Never delete, rewrite, or
move tenant data as a rollback shortcut. If a compatibility emergency requires
the prior image, stop new generated-tool dispatch first, preserve evidence,
accept the documented re-exposure risk, route traffic back, and then restore
the isolated reader as soon as possible.

## Verification

- failing-first dispatch and repository regressions for the missing project
  write and legacy-global read leak;
- warning-fatal focused unit and integration tests for both dispatch paths,
  project partitions, error paths, global configuration, and session behavior;
- branch-aware coverage through
  `config/coverage_tool_output_project_isolation.ini`: 91.0% aggregate line
  coverage and 85.1% aggregate branch coverage, with `projects.py` at 98.2%
  line/90.0% branch and `execution_dispatch.py` at 88.7% line/84.2% branch;
- scoped Ruff, strict mypy, Markdown, task-integrity, resource-ownership, and
  repository collection checks; and
- an exact-head full gate before S36 can become formally complete.
