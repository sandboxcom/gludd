# Release Proof Throughput

## Problem and decision

Repeated source resealing, partial replays, and metadata repairs consumed work
without closing more v0.1.2 backlog items. The immutable source08 integration
`2564d61449d2782583f311ecbde9198c3662298b` passed 1,182 focused tests
(12 skipped) and received source-only approval. Its actual full gate still
failed at disk usage 91% against the unchanged 90% hard limit, before costly
phases ran. Hosted Build `38266403588` and Pages `38266402434` also exposed
downstream failures. Those facts are not completed release proofs.

The remedy is blocker-first scheduling, not weaker acceptance or more agents.
Reuse the existing Make owners, failure-promotion manifest, CI failure ledger,
readiness checker, and observed gate. No additional scanner, runner, daemon,
dependency resolver, or proof cache is introduced.

## Mechanical scheduling admission

Run before sealing a replacement candidate, final combined replay, or a full
gate:

```sh
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0
```

This serial facade stops on the first nonzero existing owner:

1. `disk-cleanup-preflight` uses live inspection with dry-run cleanup. It cannot
   delete caches, history, worktrees, environments, or application state.
2. `gate-admission-config` fixes the release profile, fresh proof, 16-file
   batches, and one worker. It does not allocate another proof environment.
3. `check-task-registration` and `check-task-integrity` reject unowned work.
4. `check-gate-failure-promotions` verifies executable early-failure ownership.
5. `check-resource-ownership` checks the existing inventory without rewriting it.
6. `_pytest-runtime-compatibility-admission` executes the existing warning-fatal
   fixture, AST, async/background-launcher, and handoff tests with one worker.

Each owner emits a start and terminal phase marker. Failure emits
`PROOF-ADMISSION-BLOCKED phase=<owner>` and prevents later owners from running.
All owners passing emits `admitted_for_proof=true release_ready=false
gate_evidence=false`: permission to spend work collecting proof, never proof
itself. The facade writes no gate attestation and changes no Git refs.

The safe contract example is:

```sh
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=1
```

It prints `PROOF-ADMISSION-PLAN`, executes no owner, and cannot claim admission.
Behavioral tests inject failure into each real recipe phase, verify ordered
short-circuiting, and prove that changing a claimed SHA cannot clear an
unchanged capacity failure. Invalid plan modes fail before any owner runs.

## Missing-proof record and work order

Keep one current record owned by the orchestrator, rather than repeated
readiness summaries. For every task, record: implementation commit(s), focused
acceptance, source-review scope, full-gate run/result, hosted SHA/run/result,
remaining blocker, and next action. Missing evidence stays explicitly pending.
TASKS.md owns task status; SESSION.md is a handoff, not a gate authority.

As observed during the 2026-10-12 repair session, all seven S98 tasks still lack
their final passing canonical gate and replacement exact-SHA hosted proof.
Their focused evidence is recorded in TASKS.md, not converted into completion.
The required S97 repairs remain in scope; excluded backlog work does not.

| Task | Required final evidence | Current next action |
| --- | --- | --- |
| S98.1 | Stable-policy acceptance and exact candidate admission | Keep full admission and source-CI identity intact |
| S98.2 | Final native/hosted compatibility proof | Run the retained compatibility checks on the frozen candidate |
| S98.3 | Complete canonical coverage and runtime proof | Preserve 85% aggregate and 75% per-file line/branch floors |
| S98.4 | Hosted container, EE, Windows, and Linux artifacts | Batch the proven provenance, prerequisite, acquisition, and version repairs |
| S98.5 | Native build warning-graph and preserved import proof | Replay exact warning evidence without widening accepted drift |
| S98.6 | Stable promotion and release-cut evidence | Keep promotion, tagging, and publication blocked until final proof is green |
| S98.7 | One complete canonical gate and hosted presentation/shards | Clear capacity, then replay the repaired launcher, handoff, and browser paths |

Normalize each blocker by owner/node and actual failing input. A new SHA,
observer suffix, time passage, source-only approval, or unrelated green check
does not clear it. Retain the original failed run and specify the input change
needed before another expensive attempt. This is a standing scheduling rule,
not a new persisted fingerprint engine or a cache that reuses proof across SHAs.

While blocked, continue bounded failing-first repair tests, mandatory actual
collection before each small commit, and normal ledger-guarded development
repair pushes. Do not repeat full gates, reseal candidates, broaden into
unrelated features, or silently skip hosted failures. Batch known independent
repair siblings before another CI push. Never edit tests just to obtain green;
conform obsolete interface assertions only while retaining their intent.

The actual latest capacity failure is a hard prerequisite. Ambiguous toolchain
ownership and shared OpenCode history remain protected; this workflow does not
grant deletion authority or relax lock, link, lease, or cleanup checks. Local
browser prerequisites are also absent, so focused JavaScript/CSS acceptance is
not a substitute for the required fresh browser run.

After hard prerequisites clear: freeze once, obtain independent source review,
run the combined acceptance set, run the complete canonical gate, and obtain
required hosted success for that exact immutable SHA. No candidate edits during
final proof. A failed final run returns to bounded repair work; it is never
relabelled as green. Only then may the existing guarded promotion proceed.

## Move known failures ahead of costly fan-out

Hosted unit-2 batch 008 exposed an obsolete lock-first async-gate assertion.
Hosted unit-1b batch 026 exposed missing explicit CI wording in the primary
objective. An earlier combined replay mistook a telemetry suffix for a Git
handoff anchor. The existing compatibility owner now executes the complete
async-launcher and session-persistence files plus the exact CI-objective node
before broader admission. The manifest binds each original failing node to
that owner, and new failing-first tests verify executable routing.

This preserves the 90-second early phase, warning-fatal policy, one-worker
bound, and the authoritative full-gate replay. SESSION.md is repaired; the
original objective and persistence assertions remain unchanged.

The three original hosted unit-3b timeout failures are also mapped to that
owner, which runs the complete background-launcher file. Build's Python 3.11
and 3.12 quality legs execute it through the existing observer after locked
dependency installation and before required shard/platform fan-out, retaining
90-second runtime, 60-second quiet, and ten-second heartbeat bounds. The new
workflow acceptance fails first on absent routing and pins every prerequisite
edge without skipping authoritative downstream checks.

## Existing tools and practitioner evidence

The repository already provides the necessary capacity owner, `pytest`, Make
composition, `scripts/stream_command.py`, task integrity, exact-CI failure
ledger, and release readiness. The research decision therefore rejects a
second workflow engine. GitHub's maintained [workflow syntax][workflow-syntax]
describes the existing `needs` dependency mechanism for ordering jobs; this
local facade composes owners rather than introducing an external scheduler.

The long-running [Merge Queue running checks twice?][duplicate-checks]
discussion starts in 2023, records wasted Actions time from duplicate checks,
and warns that blindly skipping the integrated replay can admit stale-trunk
code. The [Merge Queue Feedback][queue-feedback] discussion records queue-time
and feedback concerns. These are practitioner reports, not release guarantees.
Our inference is to move cheap feedback earlier while preserving exact-candidate
final replay, rather than treating earlier green checks as reusable proof.

## Zero downtime and rollback

This is a validation/control-plane change. It starts no serving process,
listener, deployment, migration, or model worker and leaves the active artifact
unchanged. Test temporary paths stay namespaced and are cleaned by their
existing owners. Rollback removes the scheduling facade and early mappings
only after any invocation exits; retain all original failed evidence and
authoritative gate phases. No plugin source changes or runtime restart are
required.

[workflow-syntax]: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
[duplicate-checks]: https://github.com/orgs/community/discussions/43988
[queue-feedback]: https://github.com/orgs/community/discussions/14801
