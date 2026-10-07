# Release predecessor admission

## Scope and current state

S83.166 is the terminal v0.1.1 promotion and publication action. The readiness
model deliberately excludes that action from its own pre-publication task check
to avoid a circular dependency, while requiring every other declared S83.157
through S83.168 task to be effectively complete. A checked box with a pending
or in-progress status remains incomplete.

The terminal `release-promote` target currently delegates twice to
`release-readiness`: once in validation mode and once immediately before the
development-to-master fast-forward. The underlying readiness checker also has
two explicit diagnostic escape hatches:

- `RELEASE_ALLOW_INCOMPLETE_TASKS=1` skips the predecessor-task inventory.
- `RELEASE_ALLOW_INVALID_RECEIPT=1` skips the reviewed-head receipt check.

Those escape hatches are useful for isolated diagnosis, but they must not be
ambient authority for the terminal promotion path. Environment and recursive
Make state can otherwise carry a value from an earlier diagnostic command into
a later release command without the promotion target naming that policy.

The resulting contract is narrow: every `release-readiness` delegation owned
by `release-promote`, in validation and live modes, explicitly forces both
values to `0`. Direct diagnostic readiness calls retain their existing escape
hatches. S83.166 remains excluded from its own predecessor cycle; every other
milestone task and the reviewed-head receipt remain mandatory.

## Failing-first regression

`tests/unit/test_release_predecessor_admission.py` reads the canonical composed
Makefile rather than a fragment and selects the two readiness calls inside
`release-promote`. It requires both calls to carry:

```text
RELEASE_ALLOW_INCOMPLETE_TASKS=0
RELEASE_ALLOW_INVALID_RECEIPT=0
```

The regression was staged before production code and failed 1/1 because the
validation delegation did not pin the incomplete-task bypass. After the two
terminal call sites were repaired, the unchanged regression passed 1/1. The
complete exact-candidate gate remains authoritative release evidence; focused
success cannot authorize publication.

Focused verification selects 91 admission, promotion, and readiness cases under
warnings-as-errors. They pass with 97% branch-aware coverage for the measured
readiness checker, above the 85% aggregate and 75% per-file floors. Scoped Ruff
and strict mypy, Markdown, Make validation, the public target contract, task
ledger/integrity, and resource ownership also pass. The separately owned
documentation-shard assertion is outside this candidate and remains excluded
from this focused claim. Collection reports 119,693/119,711 tests with 18
intentional deselections and zero errors; no full-gate or publication claim is
inferred.

## Practitioner and upstream evidence

- The GNU Make manual's [recursive variable contract](https://www.gnu.org/software/make/manual/html_node/Variables_002fRecursion.html)
  explains that variables originating in the environment are exported to
  recipe commands and that command-line variable definitions are propagated to
  sub-Make through `MAKEFLAGS`. The manual separately warns in
  [Variables from the Environment](https://www.gnu.org/software/make/manual/html_node/Environment.html)
  that outside environment state makes identical Makefiles behave differently.
  A terminal release boundary therefore cannot assume an omitted bypass is
  false.
- The practitioner report [au-ts/sddf issue #337](https://github.com/au-ts/sddf/issues/337),
  opened in January 2025 after reproductions on GNU Make 3.81 and 4.4.1,
  documents the difference between environment and command-line values and the
  persistence of command-line assignments in recursive Make. Its maintainers
  converged on explicit values at the sub-Make call site, which is the boundary
  proposed here.
- The long-lived GitHub Actions runner report
  [actions/runner issue #789](https://github.com/actions/runner/issues/789),
  opened in 2020 and reopened in 2022, records environment values surviving
  repeated composite-action use and resisting an expected overwrite. It is a
  practical example of why stale ambient release state must be neutralized at
  the destructive call site instead of inferred from an earlier step.
- GitHub's maintained [deployment-environment contract](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments)
  requires every configured protection rule to pass before a deployment job is
  sent to a runner. Gludd applies the same fail-closed principle locally: a
  predecessor or receipt exception used for diagnosis cannot become an
  implicit release approval.

## ZDD, resources, and observability

This admission check completes before any master ref mutation, tag creation,
artifact upload, daemon restart, schema change, or paid provider allocation.
An invalid predecessor set or receipt therefore leaves the running application
and all published artifacts untouched. Existing workloads continue serving
while the candidate remains on development.

The repair adds no process, poll, network request, worktree, or cache. It only
sets two scalar values on the two already-bounded readiness delegations. The
existing readiness JSON remains the diagnostic surface: it names incomplete
tasks, ledger contradictions, or receipt failure. `RELEASE-PROMOTE-VALIDATED`
may appear only after the strict validation call returns successfully, and live
promotion must stop before the fast-forward on the same errors.

Resource and output bounds remain unchanged:

- one composed-Makefile structural regression;
- exactly two terminal readiness call sites;
- the existing finite milestone inventory and reviewed-head receipt;
- no secret, credential, or receipt payload printed by the new boundary.

## Rollout and rollback

1. Run the new regression against the unchanged target and retain its expected
   failure as the TDD receipt.
2. Pin both bypass variables to `0` on the validation and live readiness calls.
3. Replay the focused promotion/readiness contracts, static checks, coverage,
   and the complete exact-candidate gate. Aggregate coverage must remain at
   least 85%, with every measured file at least 75%.
4. Merge through development, obtain exact-SHA hosted evidence, and only then
   invoke terminal promotion.

Before promotion, rollback is a source revert of the isolated Makefile change;
it changes no runtime state. After publication, do not rewind master or delete
release evidence. Any newly discovered incompatibility follows the existing
fix-forward release process while the published artifact and receipts remain
auditable.

## Acceptance boundary

The feature is complete only when validation and live promotion both ignore an
ambient value of `1`, direct diagnostic readiness retains its explicitly scoped
override behavior, the predecessor and receipt errors remain observable, and
the exact candidate passes the full local and hosted release evidence path.
