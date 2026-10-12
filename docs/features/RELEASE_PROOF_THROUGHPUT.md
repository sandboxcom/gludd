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

Keep one generated current handoff projected by the existing observation
producers, rather than an orchestrator-maintained JSON tracker. The projection
uses the existing task, local proof, hosted workflow, and failure-ledger owners;
it does not independently decide their outcomes. Its current task map records
the observed local HEAD, hosted SHA/state and next action. Canonical gate,
coverage and source review remain `not_collected`: this producer does not yet
validate those authoritative receipts. Local HEAD alone does not describe a
tested or clean working tree. Focused acceptance and historical evidence stay
in TASKS.md; missing final evidence stays explicitly pending. TASKS.md owns task status; SESSION.md and the
generated handoff are not gate authorities.

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

### Producer-owned current observations

The automatic observation contract addresses a specific gap: a manually edited
handoff or observation JSON can retain an earlier success after a producer sees
pending work, a lookup error, a timeout, or a different candidate. Timestamp and
identity fields must come from the producing command, not from prose or an
editor. The existing producers now generate those records automatically;
historical manually curated JSON remains preserved, not updated or relabelled.

Normal `make ci-await` with a full SHA writes `.gludd/ci-await.json` through
`CI_AWAIT_OBSERVATION_OUTPUT`. Normal `make pipeline-status` writes
`.gludd/release-proof-status.json` through `PIPELINE_STATUS_OUTPUT`.
`status-heartbeat` reuses that complete producer instead of constructing a
timestamp-only JSON file; its existing bounded loop starts no extra daemon.
Its contract's validate-only example invokes no owners and writes no files.

`scripts/proof_observations.py` is shared serialization and validation glue for
the existing producers. It reuses `scripts.stream_command._atomic_write_json`
rather than introducing another tracker, scanner, runner, cache, or daemon.
That existing writer creates its temporary file beside the destination, flushes
and fsyncs JSON, replaces the visible file, and fsyncs the directory. Python
documents atomic successful replacement and warns that replacement can fail
across filesystems; using the destination directory preserves the established
publication path. [Python replacement semantics][observation-replace].

The producer contract is:

- `ci-await` updates its owned current observation at each poll and on lookup
  error, timeout, and terminal result. A new non-success state replaces the
  previous current success; it cannot leave an earlier green value looking
  current. The observation adds no polling loop or waiting process.
- `pipeline-status` automatically projects the generated handoff from its
  existing all-workflow report, local status, task ledger, and CI failure-ledger
  owner. Failure-ledger observation errors remain visible failures. Generating
  the handoff cannot clear a ledger entry or synthesize missing proof.
  Job-detail identity is checked against the exact push-run index, using the
  existing ledger query's real field set. Known failed jobs appear immediately
  even while their overall workflow remains queued or running. Malformed steps,
  mismatched run identity and contradictory success become lookup errors.
- Each record declares schema version, `kind=proof_observation`, producer,
  producer-generated `generated_at`, state, evidence, and the complete
  repository/branch/commit-SHA identity. UTC timestamps are timezone-aware;
  Python recommends aware UTC values for unambiguous comparisons.
  [Python UTC timestamps][observation-utc].
- Readers require their expected producer and complete identity, validate the
  schema, and reject stale or future records. Missing or malformed JSON is not
  usable current evidence. A record from another repo, branch, or SHA cannot be
  relabelled merely by advancing an observer name or changing HEAD.
- `release_ready=false` and `gate_evidence=false` are mandatory even when the
  observed state is success. Altering these flags invalidates the schema. The
  records describe observations; the existing admission, gate, exact-SHA CI,
  coverage, artifact, and promotion owners remain authoritative.

Only the owning producer refreshes its generated current record. Writers refuse
a destination containing unrelated/manual history or a symlink target. Retain
the original handoff, failed logs, immutable candidate refs, and protected
OpenCode history. This contract grants no deletion, cache cleanup, migration,
or external-state authority. It also does not provide cryptographic attestation
merely by naming a producer in JSON.

### Fresh observation is not complete proof

`generated_at` establishes when the local producer wrote an observation. It does
not establish that an upstream API response was fresh or complete. Hosted
records must retain the source run identity and outcome provided by the existing
CI owner. GitHub's API exposes `head_sha`, `head_branch`, run ID/attempt,
status/conclusion, and source timestamps; its `head_sha` filter supports exact
candidate lookup. Consumers must retain that identity instead of substituting
the latest branch tip for the tested commit.
[GitHub workflow-run API][observation-api].

The long-lived [workflow-run filtering report][stale-workflow-observations]
began in March 2021 and continued into 2026. Users reported missing filtered
runs and stale responses, including normal HTTP 200 responses that omitted
recent results. These are practitioner observations, not a guarantee about
GitHub's current service or proof that Gludd has the same API failure. The
engineering inference is to preserve explicit unknown/error states, inspect
returned identity, and keep generated observations diagnostic. A fresh local
write cannot make a stale remote result authoritative.

The writer/reader behavior is specified in
`tests/unit/test_proof_observations.py`: producer-derived UTC time, replacement
of prior success, identity mismatch, stale/future time, schema/authority
tampering, protected manual history, symlink targets, and missing/malformed
JSON. Actual shared-writer, waiter, pipeline and ledger-facade replay passes
139 warning-fatal cases. Branch-aware coverage is 94.50% combined (95.77% line,
91.12% branch), with every measured file above 75% in both dimensions.
Four additional actual-response/contradiction cases first fail with 33 passing,
then pass without changing the original selector or exit-code assertions.
Four early-routing/roster cases first fail with 25 passing, then the complete
promotion/plan slice passes all 35. Both new families route complete producer
cohorts through the retained 90/60-second owner, without changing its 21-phase,
2850-second schedule or mandatory full gate. These are focused results, not
complete release evidence. The actual expanded warning-fatal one-worker owner
passes all 590 cases in 32.35 test seconds (33.1 observed), within those unchanged
bounds; the 216-target Make contract and all three actual public safe examples
also pass.

Confirmed remaining gaps are explicit: complete EE dependency resolution and
replacement image proof; validated final source-review/canonical gate/coverage
receipts; and measured hosted critical-path optimization. A shard-ordering
proposal exists, but has not been implemented or demonstrated faster. Capacity
admission and protected-history ownership are already codified and still
require real capacity or operator authority, not another status-file edit.

This automation leaves every missing-proof requirement above in force. A
generated success cannot substitute for the passing full canonical gate,
85% aggregate/75% per-file coverage, required hosted workflows and artifacts for
the same frozen SHA, independent source review, or required native/browser
acceptance. It does not clear the retained capacity prerequisite. Partial
local evidence and historical failures stay explicit, and TASKS.md cannot be
ticked from a current-observation JSON.

Observation publication is a control-plane file update. It changes no serving
process, listener, deployment, database, model worker, or active release
artifact, so ZDD requirements remain intact. Existing resource namespaces and
cleanup authority remain unchanged. Rollback removes observation integration
through the same development flow while retaining all original proof and
protected history; it cannot turn cached observations into promotion authority.

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
[observation-api]: https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository
[observation-utc]: https://docs.python.org/3.11/library/datetime.html#datetime.datetime.now
[observation-replace]: https://docs.python.org/3.11/library/os.html#os.replace
[stale-workflow-observations]: https://github.com/orgs/community/discussions/24626
