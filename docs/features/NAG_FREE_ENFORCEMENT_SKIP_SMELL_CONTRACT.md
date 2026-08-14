# Nag-Free Enforcement and Skip-Smell Contract

## Purpose

Enforcement must be quiet when its trigger is absent and fail closed when its
trigger is present. Test infrastructure must not hide a missing enforcement
behavior with a skip, inspect the wrong hot-reload layer, or rewrite reviewed
evidence merely because a test ran.

This contract covers two related evidence boundaries:

- delegation warnings and zero-dispatch blocks; and
- repository-wide `skip` and `xfail` hygiene.

## Executable enforcement boundary

An OpenCode plugin contract can contain a public facade, an implementation, and
a hot-reload wrapper. Structural assertions select the marker segment containing
the behavior under test. They do not choose a segment because it is longest and
do not require implementation constants to be copied into the public facade.

The canonical nag-free invariants are:

1. `DELEGATE_FIRST_THRESHOLD` is a positive integer.
2. The delegate-first warning is below a strict streak comparison.
3. `MAX_ZERO_STREAK` is a positive integer exported by
   `.opencode/lib/multitask_config.ts` and imported by the plugin.
4. Every subagent guard returns before streak state is read or a warning/block
   branch can execute.
5. Main-thread warning and denial behavior remains fail closed; subagent
   isolation is not a general disable switch.

The test helper has its own regression with duplicate facade, implementation,
and wrapper markers. That pin prevents a later loader refactor from making a
structural test pass against inert text.

## Skip-smell boundary

`pytest.skip` control flow is identified from Python's AST ancestry. A skip is
guarded only when it is nested below explicit conditional or exception control
flow. Source-line proximity is not evidence: a legitimate guard can be separated
from its skip by setup statements, while an unrelated nearby `if` can create a
false exemption.

Unconditional skips must name a concrete unavailable capability or opt-in
precondition. Strict xfails remain forbidden. Enforcement and hook tests may not
use a CI explanation to turn a missing behavior into green output. The supported
release-path assertion therefore verifies that the CI check precedes tag push
instead of skipping a low-level utility-target mismatch.

Every retained xfail explicitly chooses `strict=False` and carries a durable
task, issue, or repository-document reference. This makes expected-failure
semantics independent of pytest configuration while keeping an XPASS visible.
Slow-marker explanations belong to the decorated test or class docstring (or an
adjacent comment); the audit resolves decorator ownership with the AST instead
of pretending only comments can explain a test.

The skip-count snapshot is reviewed repository data. Tests read it and fail when
it is missing or exceeded. They never create or refresh it during collection,
test execution, or `pytest_sessionfinish`; an automatic rewrite would convert a
regression into its own new baseline.

S83.110 reconciles the snapshot once against its declared full-suite scope. The
old `143/0/16` values predated 203 call sites and 20 decorators that the same
scanner now inspects. Schema version 2 records the scope and reviewing task next
to the `346/20/16` ratchet. Future decreases need no baseline edit; any increase
still fails and requires a separate reviewed change.

The same evidence rule applies to `TASKS.md`: a historical row whose source
record says its gate rerun was outstanding remains unchecked. The tick guard is
not weakened to accept `evidence: pending`, and no replacement success claim is
invented without a terminal artifact.

## Practitioner evidence

These contracts respond to durable practitioner reports rather than a
Gludd-specific style preference:

- The 2012 Stack Overflow discussion
  [How do I display why tests were skipped?](https://stackoverflow.com/questions/13495950/how-to-i-display-why-some-tests-where-skipped-while-using-py-test)
  shows why durable reasons and visible skip reporting matter to operators.
- The 2018 question
  [Make coverage only count successful tests and ignore xfailing tests](https://stackoverflow.com/questions/53191930/make-coverage-only-count-successful-tests-and-ignore-xfailing-tests)
  describes the practical need to execute expected-failure cases so unexpected
  success remains visible instead of being hidden.
- [pytest issue #9515](https://github.com/pytest-dev/pytest/issues/9515), opened
  in 2022, records demand for conditional skipping of dependent tests while
  preserving the originating failure. This supports precise control-flow
  detection rather than a blanket ban on guarded skips.
- [OpenCode issue #757](https://github.com/sst/opencode/issues/757) records
  practitioner concern that agents can overuse tools and struggle with large
  combined instruction surfaces. Gludd's inference is that enforcement feedback
  should be conditional and actionable, not an unconditional stream of nags.

The upstream reports are analogous evidence. They do not claim to reproduce
Gludd's facade-selection or snapshot-mutation defects exactly.

## Security and fail-closed behavior

No production permission, `.env`, shell, stop, or delegation rule changes.
Subagents bypass orchestration nags before any shared streak access, while the
main-thread path continues to enforce positive thresholds and pending-work
policy. AST parsing reads tracked test sources as data and executes none of them.
Malformed Python remains a visible parse failure.

## Resources and observability

The scanner performs one bounded AST parse per tracked Python test file and keeps
only small in-memory metadata. It starts no daemon, watcher, subprocess, or
network request. Failures report the file, line, and offending reason. Removing
the session-finish writer also eliminates hidden filesystem work and concurrent
snapshot races.

## Compatibility, ZDD, and rollback

This is a test/spec/ledger-only rollout. Runtime hook exports, state-file formats,
threshold values, Make targets, and deployment artifacts do not change, so old
and new workers can overlap with zero downtime. No OpenCode restart is required.

Rollback is a normal commit revert. It restores only the earlier structural and
test-evidence behavior; there is no runtime state or data migration to reverse.
