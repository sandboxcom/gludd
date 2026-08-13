# Blocker Detector Timestamp and Deduplication Contract

## Status

Implemented for the beta.4 remediation detector.

## Problem

Two adjacent contracts had drifted apart.

First, the missing-creation-time test inserted a real `HumanTodoModel` through
the repository and flushed it. The model's non-null Python default populated
`created_at`, so the test never exercised the production branch that skips an
unageable external or legacy row.

Second, `BlockerDetector.scan()` appended both the blocked parent todo and its
stale linked human-todo. That emitted two remediation findings for one blocked
task even though the stale scanner's contract says the parent finding wins when
it is already visible.

## Contract

1. A repository row whose `created_at` is absent or invalid is skipped because
   no safe age can be calculated.
2. Persisted Gludd human-todos retain their non-null UTC creation default.
3. Tests of missing external or legacy metadata inject a repository seam; they
   do not misuse a flushed ORM row that guarantees the field.
4. `scan()` emits the richer blocked-on-human finding when a stale human-todo
   points at a parent already surfaced in the same project scan.
5. A parentless human-todo, or a linked parent excluded by the project filter,
   remains independently discoverable.
6. Chronic-requeue findings remain separate signals and preserve their existing
   ordering.

## Zero-Downtime Development Evidence

The clean gate first reported one stale-human-todo failure. Replaying its complete
family proved the fixture had acquired a current timestamp during flush. After
the repository seam was corrected, an adjacent integration test reproduced two
findings for one permission-escalation parent before the production fan-in was
deduplicated.

The exact two boundaries are 2/2 green. The complete blocker detector family is
142/142 green under strict warnings, and
`src/general_ludd/remediation/blocker_detector.py` reaches 97.57 percent branch
coverage.

The detector remains read-only. The change modifies only in-memory result fan-in,
with no migration, write, API shape, daemon restart, or background worker. Old
and new readers can overlap during a rolling deployment.

## Security and Resource Boundaries

Unknown timestamps fail closed by declining to invent age or trigger an
automated escalation. Deduplication prevents one permission request from
scheduling two retries or filing redundant human actions while retaining the
auditable, richer parent record. Work is bounded by the findings already in
memory: one set of surfaced parent IDs and one linear filter, with no additional
query, file, socket, process, or persistent allocation.

## Practitioner Evidence

[The long-lived Stack Overflow discussion “How to apply Column defaults before a
commit in SQLAlchemy”](https://stackoverflow.com/questions/13791487/how-to-apply-column-defaults-before-a-commit-in-sqlalchemy)
documents the practitioner-visible rule that Python column defaults are applied
when INSERT/UPDATE statements are emitted during flush. That behavior explains
why a flushed ORM object cannot represent a missing creation timestamp here and
supports testing the legacy/external boundary through the repository interface
instead.
