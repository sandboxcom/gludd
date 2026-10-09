# Agent message expiry admission

S34 makes time-to-live eligibility part of inbox admission instead of a
post-query cleanup step. Persistent and degraded inboxes now reject expired
messages before enforcing one stable 100-message response bound. The database
schema, HTTP payloads, repository result types, dependencies, and transaction
ownership do not change.

## Problem and acceptance contract

`AgentMessageRepository.inbox()` previously selected the oldest rows, applied
`LIMIT`, and only then removed expired rows in Python. A run of expired rows
could consume the whole limit and hide a live message immediately behind it.
The degraded in-memory route did not apply TTL admission at all and returned
every matching entry in its 5,000-row store. Rows tied on `created_at` also had
no unique ordering key, so bounded calls could return different subsets.

The corrected contract is:

- a message is live only while its elapsed age is strictly less than its
  positive TTL; the exact boundary is expired and therefore fail-closed;
- messages without a TTL remain live, while malformed degraded TTL metadata is
  rejected instead of becoming immortal;
- recipient, broadcast, unread, and project filters continue to apply before
  response construction;
- persistent inboxes execute one `SELECT`, put TTL admission in its `WHERE`
  clause, order by `created_at, id`, and apply `LIMIT` last;
- caller limits are clamped to `0..100`, preventing negative SQL limits from
  becoming unbounded and large limits from bypassing the resource ceiling;
- degraded inboxes apply the same TTL boundary and stable order, then return at
  most 100 entries from the already bounded 5,000-entry deque; and
- purge and unread-count queries share the same live/expired predicates, so an
  exact-boundary row cannot appear in one view after disappearing from another.

PostgreSQL computes elapsed seconds with native epoch extraction. SQLite uses
its existing Julian-day calculation with a one-millisecond rejection bias to
cover the representation's sub-millisecond floating-point uncertainty. The
bias is deliberately fail-closed: uncertainty near the exact boundary cannot
extend message lifetime.

## Maintained dependency and practitioner evidence

This repair uses SQLAlchemy's existing expression builder and the current
bounded in-memory deque. It does not introduce a message broker, scheduler,
cleanup worker, cache, schema column, or third-party dependency.

Long-lived user reports show why expiry must be an admission rule rather than a
best-effort cleanup after selection:

- A user in [RabbitMQ discussion #3852](https://github.com/rabbitmq/rabbitmq-server/discussions/3852)
  reported in 2021 that expired messages did not move until a retrieval was
  attempted. The maintainer explained that head-of-line state determines when
  expiry is processed. S34 avoids the analogous Gludd failure by evaluating TTL
  before a bounded result set is chosen.
- A production operator in
  [RabbitMQ discussion #14524](https://github.com/rabbitmq/rabbitmq-server/discussions/14524)
  reported in 2025 that TTL-related queue segments could persist for weeks and
  consume resources. The maintainer described TTL as a distinct lifecycle path
  rather than equivalent to acknowledgement. S34 likewise keeps admission and
  physical purge separate while making both predicates agree.
- RabbitMQ's maintained
  [TTL guide](https://www.rabbitmq.com/docs/ttl) documents that expired messages
  can remain behind other messages even though consumers must not receive them.
  That is the precise distinction enforced here: storage may retain a row until
  purge, but inbox admission cannot expose it or let it consume the live bound.
- SQLAlchemy's maintained
  [ordering FAQ](https://docs.sqlalchemy.org/en/20/faq/ormconfiguration.html#why-is-order-by-recommended-with-limit-especially-with-subqueryload)
  recommends a deterministic unique ordering key whenever `LIMIT` selects a
  subset. The `(created_at, id)` ordering follows that guidance.

These sources were reviewed on 2026-10-08. They support fixing the existing
query and fallback directly; adding another broker or background reaper would
expand operational state without repairing admission correctness.

## Resources, observability, and failure behavior

The persistent path issues one bounded query through the caller-owned
`AsyncSession`; it does not commit, roll back, close, or retain that session.
Database cardinality is capped at 100 before ORM materialization. The existing
recipient/read/project indexes remain available, and no connection or
transaction lifetime changes.

Degraded storage remains capped at 5,000 entries by its existing
`deque(maxlen=5000)`. Each inbox call examines at most those 5,000 entries,
holds at most that bounded candidate set, and serializes at most 100 results.
It creates no process, thread, task, listener, timer, file, or persistent cache.
Malformed TTL metadata is omitted from the response; ordinary repository or
database failures keep their existing visible exception path.

Operators can observe the existing `/api/messages` response count and daemon
database errors. A sustained count of 100 indicates that the hard response
bound is active, not that expired rows were returned. Physical removal remains
the responsibility of the existing `purge_expired()` path.

## Zero-downtime rollout and rollback

S34 is a code-only rolling change. Old and new processes read and write the
same rows and payloads because there is no migration, new field, index,
dependency, or serialization change. During a mixed-version rollout, updated
processes may reveal live rows that an old process hid behind expired rows; no
row is rewritten and no caller transaction is taken over.

Deploy the updated application image normally, let in-flight inbox requests
finish, and monitor message-route errors, response counts, latency, and database
pool saturation. Rollback is an application-image rollback: route new requests
to the preceding image and let updated requests drain. Do not migrate or purge
data as part of rollback. Rows written by either version remain readable by the
other, and the existing 5,000-entry degraded-store ceiling is unchanged.

## Verification

- `make test-files` over the focused integration and degraded acceptance suites
  with warnings fatal
- `make test-files` over message repository, router, facts, and project-scope
  compatibility suites with warnings fatal
- `make coverage-files` with
  `config/coverage_agent_message_expiry_admission.ini`, aggregate minimum 85,
  and per-file minimum 75
- scoped `make lint-files`, `make typecheck-scope`, and `make lint-markdown`
- `make check-task-integrity`, `make validate-task-ledger`,
  `make check-resource-ownership`, and `make test-count`
