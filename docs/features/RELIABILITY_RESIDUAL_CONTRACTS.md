# Reliability Residual Contracts

Status: implemented 2026-08-20

This slice closes the focused reliability gaps found in notification, policy,
secret authorization, training export, catalog, rendering, retry-queue, and
saga tests. It preserves fail-closed security boundaries and treats terminal
state as authoritative so recovery cannot repeat completed side effects.

## Runtime contracts

- A retry-queue peek is observational: it neither removes nor activates an
  item. A delayed high-priority item does not hide a lower-priority ready item.
- A saga timeout remains `timed_out`, compensates completed prior steps, and
  persists the terminal result. Restoring a completed or compensated saga
  returns its saved results without invoking actions again.
- Saga persistence may omit intermediate step snapshots, but it always records
  the terminal state when a store is configured.
- A literal OpenBao denial `path_prefix` covers the named path and descendants
  only on a segment boundary; similarly named sibling paths remain accessible
  when positively granted.
- Ornith export tests select a confined test root. Production continues to
  reject every explicit path outside configured export roots.
- OPA verification requires a zero exit status and the exact expected policy
  markers. The parser accepts both verbose layouts emitted by supported OPA
  releases without weakening the rule set.

## Dated upstream and forum evidence

- On 2025-06-02, pytest 8.4.0 made an empty `pytest.raises(match="")`
  emit a warning because it matches every exception message, and strengthened
  collection of unhandled thread exceptions. Gludd therefore omits a regex
  when only exception type is portable and transfers every test-created thread
  outcome back to the owning test before joining it:
  [pytest 8.4.0 release notes](https://github.com/pytest-dev/pytest/releases/tag/8.4.0).
- On 2025-09-30, pytest maintainers documented practitioner reports of unsafe
  cross-thread test state and recommended a queue for returning worker results.
  Gludd's daemon regression follows that ownership boundary with `SimpleQueue`
  and an explicit join:
  [pytest issue #13768](https://github.com/pytest-dev/pytest/issues/13768).
- On 2019-10-04, SQLAlchemy 1.3.9 documented its warning when a Session sees a
  second object with an existing primary-key identity, because implicit
  identity-map replacement can detach the first object unexpectedly. A Gludd
  uniqueness regression now expunges the first flushed test object before the
  duplicate insert, so the asserted `IntegrityError` comes from the database
  constraint rather than an unrelated ORM identity conflict:
  [SQLAlchemy 1.3.9 changelog](https://docs.sqlalchemy.org/en/20/changelog/changelog_13.html#change-4890).
- On 2024-09-12, a user reported concurrent saga events with one correlation
  ID arriving while a saga was intermediate or already complete. The
  MassTransit maintainer recommended retry plus a state machine that tolerates
  event ordering and still reaches the terminal state. This supports treating
  persisted terminal state as a replay barrier rather than executing actions a
  second time: [MassTransit discussion #5489](https://github.com/MassTransit/MassTransit/discussions/5489).
- On 2025-10-31, the upstream OPA v1.10.0 announcement added
  `opa test --fail-on-empty`, another example of the CLI test surface evolving
  independently of Rego semantics. Gludd therefore keys verbose output on
  exact `data.*` rule markers in either supported layout while retaining the
  process exit code as the primary pass/fail contract:
  [OPA discussion #723](https://github.com/orgs/open-policy-agent/discussions/723).

## Zero-downtime and failure behavior

All changes are in-process and require no data migration or service restart.
Queue inspection preserves work, saga recovery suppresses duplicate side
effects, terminal state is durable, and secret/export checks fail closed. The
focused regressions run with warnings promoted to errors and include sibling
prefix collision, delayed-priority selection, timeout compensation, terminal
restore, and terminal persistence cases.

The warning-contract repair changes tests and documentation only. It does not
alter daemon exit codes, database schema, sessions, or deployed traffic. Test
threads are synchronously released and joined, and database uniqueness remains
enforced by the same primary-key and unique constraints, so rollout and
rollback are both zero-downtime operations with no persistent artifacts.
