# Reviewed Head Integration

## Outcome

Independently reviewed content heads are integrated in a deterministic linear
order. Each head remains attributable during conflict handling, all affected
focused checks run once against the combined tree, and one exact full gate runs
once against that same final tip.

The executable contract is
`general_ludd.git_release.reviewed_head_integration`. It emits only this shape:

```text
apply one reviewed head
apply one reviewed head
...
run the stable union of focused validations once
run the named exact gate once
```

It rejects duplicate head SHAs, refs, or review receipts; a validation step
between heads; a full-gate command in any head's focused scope; missing or
repeated final phases; and any application step containing multiple source
heads.

## Operator behavior

1. Pin the integration base SHA. For every source, record its full SHA, source
   ref, reviewed-base SHA, review-receipt digest, focused validation IDs, and
   application mode.
2. Apply sources in plan order, one invocation at a time. Use
   `make git-cherry-pick SHA=<full-sha>` for a reviewed single commit or
   `make git-merge MSG=<branch>` when branch ancestry must remain visible.
3. After each success, record the before/after SHA and actual parents. A cherry
   pick has exactly the prior tip as parent; a merge has exactly the prior tip
   and reviewed source SHA. Stop on a conflict. Do not skip to the next source.
4. Build the stable ordered union of every head's focused checks. Run that
   focused phase once against the final combined tip. For example, combine the
   affected test paths into one `make test-files TESTFILES='...'` invocation
   and the affected Python paths into one `make lint-files FILES='...'`
   invocation; do not replay the same focused set after every head.
5. If focused validation passes, run the plan's explicit exact gate once. Both
   receipts must name the same final SHA. A later mutation invalidates both and
   requires a new focused phase and gate.

`git-cherry-pick-list`, `gated-merge`, and an octopus merge are not valid
reviewed-content integration shortcuts. Their aggregate invocation hides the
single source responsible for a conflict or makes it too easy to attach one
review receipt to several heads. Likewise, a full gate after each head is not
additional safety for the final combined state: it consumes the expensive gate
budget before later heads change that state.

## Evidence boundary

A successful receipt proves all of these together:

- plan order equals application order;
- every source ref, source SHA, application mode, and review digest matches;
- before/after SHAs form one continuous chain from the pinned base;
- parent shape is one-parent cherry-pick or two-parent merge, never octopus;
- one focused receipt covers every reviewed source and the unioned commands;
- one exact-gate receipt covers the same final integrated SHA; and
- both validation phases passed with `run_count == 1`.

The contract is evidence validation, not a Git subprocess wrapper. Existing
Make targets retain mutation ownership, approval behavior, conflict recovery,
and observable output. This keeps the model hermetic and lets tests exercise
adversarial ordering and provenance without mutating a repository.

## Receipt generation and release boundary

After the single focused phase and exact gate pass, construct a
`ReviewedHeadIntegrationReceipt` from the recorded plan and application
evidence, then serialize it with
`encode_reviewed_head_integration_receipt`. The encoder emits canonical,
schema-versioned JSON; it does not inspect or mutate Git. Store that JSON as an
immutable candidate artifact rather than editing it by hand. The checked
fixture at `tests/fixtures/reviewed_head_integration_receipt.json` is a shape
example, not release evidence.

For v0.1.1, `release-readiness` is the consuming release boundary:

```sh
make release-readiness TAG=v0.1.1 \
  RELEASE_READINESS_VALIDATE_ONLY=1 \
  RELEASE_COMPLETED_STAGES= RELEASE_OBSERVATIONS= \
  REVIEWED_HEAD_INTEGRATION_RECEIPT=artifacts/reviewed-head-receipt.json \
  RELEASE_CANDIDATE_SHA=<full-40-character-sha>
```

Validate-only mode is hermetic: it parses the bounded JSON through the
canonical JSON Schema and typed semantic model, checks provenance and the
single-run invariants, and binds both validation records to the supplied exact
candidate SHA. Real readiness replaces that caller-supplied comparison with
the invoking worktree's HEAD and fails closed if the artifact is absent,
malformed, tampered, stale, or from another candidate. Errors identify only
the rejected boundary and never echo receipt content.

This requirement is deliberately limited to v0.1.1 release readiness and
publication. Ordinary development `make gate`, focused checks, commits, and
beta4 readiness do not require a receipt. `release-promote` forwards the
artifact and its exact development SHA to `release-cut`, which repeats the
hermetic receipt check before any push or tag mutation. Promotion fast-forwards
master to that captured SHA, never to a moving branch ref; it still owns no
receipt-generation shortcut.

## Ancestry-only exception

`development-merge-forward-batch` remains valid only for already-reviewed,
semantically superseded refs whose content is intentionally discarded by the
`ours` strategy. It records historical ancestry and is not content integration.
Any head whose patch is meant to affect the candidate follows this document and
must never enter that octopus transaction.

## Practitioner evidence

The long-running Stack Overflow discussion
[Resolve conflicts on git merge octopus][so-octopus-conflict] recommends normal
sequential merges when conflict attribution or three-way tooling matters. A
second discussion, [Is there ever a time when only an octopus merge will
do?][so-octopus-need], explains that sequential merges can represent an octopus
result, while a conflicting sequential series cannot always be represented by
an octopus merge. Those reports support the one-source application boundary.

GitHub's multi-year merge-queue discussion
[Merge Queue running checks twice?][github-duplicate-gates] documents the real
compute and latency cost of duplicate full checks. It also captures the safety
boundary: results before integration cannot replace validation of the final
queue state. Gludd therefore keeps narrow per-head review evidence, consolidates
focused checks after application, and preserves one full gate on the final tip.

## ZDD, resources, and rollback

Planning and receipt validation are pure data operations: no checkout, process,
port, daemon, service, deployment, or cloud resource changes. Integration uses
the existing namespaced worktree and Make-owned Git operations, so production
traffic is unaffected. One focused phase plus one final gate bounds CPU, memory,
disk, and test-process pressure while retaining final-state coverage.

Before the exact gate passes, rollback is the existing Make-owned abort/revert
path for the current application. After a receipt exists, any history rewrite,
new head, or candidate mutation invalidates its final SHA; quarantine the stale
artifact, build a new plan, and rerun both final phases rather than editing the
old receipt. Readiness is validate-only and makes no traffic change, so rollout
and rollback remain zero-downtime (ZDD): release publication starts only after
the immutable final candidate has passed every existing release check. If
publication fails after the local fast-forward, leave the validated commit in
place and rerun the idempotent checks; never rewrite a shared branch to make a
stale receipt fit.

[github-duplicate-gates]: https://github.com/orgs/community/discussions/43988
[so-octopus-conflict]: https://stackoverflow.com/questions/14424414/resolve-conflicts-on-git-merge-octopus
[so-octopus-need]: https://stackoverflow.com/questions/44976184/is-there-ever-a-time-when-only-an-octopus-merge-will-do
