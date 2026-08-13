# Task Ledger Integrity API

## Purpose

`TASKS.md` is an append-only evidence ledger whose active entries follow the
current schema while archived session snapshots preserve the schema that existed
when they were written. The integrity checker exposes one pure audit boundary so
the command-line gate and focused tests enforce the same policy.

## Contract

`audit_content(content)` returns an ordered list of line-addressed violations
and the number of active checklist items examined. It does not read or write the
filesystem.

The active-ledger grammar is:

- checklist markers may use `-` or `*`, and checked state accepts `x` or
  `X`;
- each checklist item occupies one physical line, and an embedded second marker
  is rejected instead of being absorbed into the first item's metadata;
- an `Archived` heading excludes its complete Markdown subtree until a heading
  at the same or higher level begins a new section;
- active priorities are `critical`, `high`, `medium`, or `low`;
- active effort accepts `XS`, `S`, `M`, `L`, `XL`, `small`,
  `medium`, or `large`;
- active rows require priority, effort, and status metadata;
- checked active rows require measurable evidence rather than a wave/session
  label; and
- duplicate identifiers are rejected within the active ledger.

The CLI remains a fixed-path, read-only adapter over this function. It reports
the active item count and exact violations, returning zero only for a clean
ledger.

## Safety and observability

The checker never renders Markdown, follows links, imports ledger content, or
executes embedded text. Diagnostics retain source line numbers so a failing gate
is actionable without copying the ledger to temporary scripts. Archived content
is ignored only by explicit heading scope; malformed current content continues
to fail closed. A joined checklist marker is diagnosed at the source line before
its trailing fields can be mistaken for metadata of the preceding item.

## Zero-downtime adoption

The pure API is introduced behind the existing `make check-task-integrity`
entry point, so callers and CI retain the same command and exit-code contract.
Canary validation runs the focused audit suite and the real ledger before the
full gate. Rollback is a code-only revert: no task data is migrated or rewritten.
During a mixed-version rollout, old checkers remain able to read the ledger while
new checkers stop applying current metadata rules to historical snapshots.

## Practitioner evidence

A long-running GitHub Community discussion from 2021 records two durable
checklist realities: practitioners use checklists for goals other than reaching
100% completion, and both lowercase and uppercase checked markers are used
interchangeably. That supports an explicit active/archive scope and syntax-level
normalization instead of treating every historical checkbox as a current task:

- [GitHub Community discussion #4261](https://github.com/orgs/community/discussions/4261)

A GitHub Community task-list report documents content after a line boundary
being assigned to the wrong checklist item, causing task data to be lost or
misattributed. The ledger checker therefore treats physical item boundaries as
data-integrity boundaries and rejects a second marker on the same line:

- [GitHub Community discussion #148714](https://github.com/orgs/community/discussions/148714)

## Verification

The focused suite covers active/archive transitions, nested archived sections,
accepted vocabulary, evidence failures, duplicate identifiers, joined physical
rows, invalid values, and all CLI outcomes. It passes 16 tests under strict
warnings with 94.48% branch coverage for `scripts/check_task_integrity.py`.
