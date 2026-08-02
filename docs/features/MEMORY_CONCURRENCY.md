# Concurrent memory isolation and durability

Gludd treats memory as shared, security-sensitive state. Concurrent agents may
retain and recall at the same time, but callers must never receive a mutable
reference to another work item's state, observe a half-persisted transaction, or
lose an item because two writers selected the same identifier.

## Contract

- Every memory-bank configuration, fact, mental model, observation, and metadata
  value crossing a public store boundary is copied. Mutating a returned object
  cannot mutate stored state or leak data into another session.
- `MemoryBank.retain_fact()` deduplicates equal normalized content while holding
  the bank lock. Concurrent identical retains therefore converge on one fact;
  different facts remain independent.
- The in-process Hindsight fallback creates UUID identifiers while holding its
  lock. Recall first prefers exact normalized content and otherwise requires all
  query tokens, preventing weak single-token matches from crossing sessions.
- `ObservationStore` makes persistence transactional from the caller's point of
  view. If its durable write fails, `put`, `put_all`, `delete`, and `clear` restore
  the prior in-memory snapshot and propagate the failure. Each snapshot is
  flushed and synced through a private temporary file in the destination
  directory before an atomic replace, so independent workers cannot collide on
  a shared `.tmp` name or expose a partially written JSON document.
- Separate `ObservationStore` instances retain last-writer-wins semantics. The
  atomic-file boundary guarantees a complete valid snapshot, not a merge of two
  independently cached snapshots; callers needing cross-process merge semantics
  must use the configured durable multi-worker store.
- Observation consolidation keeps compatible facts as supporting evidence and
  classifies only explicit, subject-related disagreement as a contradiction.
  Negations, replacement/exclusive language, and competing terse single-value
  claims are recognized symmetrically, while claims about different contexts
  remain compatible. Empty updates return an independent unchanged snapshot.

## Upstream evidence and operator reports

The implementation is deliberately stricter than relying on CPython's GIL.
CPython's multi-year free-threading effort includes dedicated work to make lists,
dictionaries, weak references, and caches safe without the GIL, demonstrating
that incidental interpreter serialization is not an application-level isolation
contract: [CPython issue #108219](https://github.com/python/cpython/issues/108219).

Hindsight operators have reported process crashes during consolidation on macOS
ARM64, with the failing consolidation worker entering a restart loop. The report
records that ordinary parallelism settings did not solve the problem and that
deferring initialization was required. Gludd's fallback consequently stays
small, deterministic, locked, and independent of heavyweight model startup:
[Hindsight issue #270](https://github.com/vectorize-io/hindsight/issues/270).

Hindsight's own release history records several concurrency and isolation fixes:
retaining all documents in async batches, breaking a retain foreign-key cascade
deadlock, making chunk insertion idempotent, preventing per-session overwrites,
and avoiding duplicate retain webhook deliveries. These long-lived production
signals shaped Gludd's atomic rollback, idempotence, and session-isolation tests:
[Hindsight releases](https://github.com/vectorize-io/hindsight/releases).

Mem0's maintained contradiction-linking prompt likewise treats a new memory as
contradictory only when it conflicts about the same entity or topic, and warns
against linking merely related themes. Gludd encodes that boundary in deterministic
tests instead of delegating correctness to a model prompt:
[Mem0 memory-linking prompt](https://github.com/mem0ai/mem0/blob/main/mem0/configs/prompts.py).

## Acceptance

The focused regression command is:

```text
make test-files TESTFILES='tests/integration/test_memory_pipeline_e2e.py tests/unit/test_memory_concurrency.py tests/unit/test_observation_consolidator.py tests/unit/test_observation_contradiction_resilience.py tests/unit/test_observation_store_atomicity.py'
```

It covers concurrent retain/recall, identical-content races, bank and session
isolation, defensive-copy boundaries, failed-persistence rollback, parallel
retrieval strategies, symmetric contradiction handling, collision-free atomic
snapshots, consolidation under load, and rapid put/delete cycles.
Per-file coverage must remain at least 75%, and the repository gate remains at
least 85% overall.
