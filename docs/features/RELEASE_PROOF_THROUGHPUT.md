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

### Real worktree admission

The push entrypoint delegates to the existing path-qualified worktree owner
with mandatory clean-state assertion. A temporary unconditional-success stub
cannot count as admission. Six real-Git regressions require native identity on
clean success and visible refusal for modified, staged, untracked, deleted or
unknown state. The guard must not mutate, stash or conceal pending feature work.
This source acceptance does not replace gate, exact-SHA CI or deployment proof.

### Item-scoped execution controls

`make work-item` owns administrative primary/support selection. One primary
stays selected while its canonical task remains open; at most two active tasks
may be declared as direct support with an explicit bounded reason. Native
observer success does not tick tasks, prove deployment acceptance, or close
the item. A ledger transition permits administrative advancement only.

The existing watched-command owner integrates this policy once focus is
initialized. It binds actual argv, content identity, effective environment and
native observer results. Its bounded record references existing observations,
not a second runner or reusable proof cache. Failed or timed-out unchanged
inputs cannot be retried merely by changing a commit, bookkeeping notes, run
name or observer label. Diagnostics and legitimate external-state waits remain
available. Native gate defaults retain full HEAD/content identity.

Independent supporting commands must not hold a singleton lock for their
entire execution. Selection and publication use short serialized sections;
the existing observer retains child-process, timeout and cleanup ownership.
Referenced native evidence must survive ordinary observer retention.

This protects execution admission, not the model's internal reasoning or every
editor action. The existing exact-SHA gate, coverage, deployment acceptance and
promotion checks remain mandatory. No runtime restart or serving change is
required for this control-plane addition.

### Research decisions for this pipeline

- DORA recommends limiting simultaneous work and following items across the
  complete delivery path, not treating development-only throughput as success.
  Apply one primary with bounded direct support and visible missing-proof
  stages. Do not enlarge the existing three-agent ceiling.
  [DORA WIP limits](https://dora.dev/capabilities/wip-limits/).
- DORA's small-batch guidance emphasizes independently testable changes and
  deployment feedback, including for AI-assisted development. Keep small source
  commits, but do not count them as feature completion.
  [DORA small batches](https://dora.dev/capabilities/working-in-small-batches/).
- Fowler describes fast early feedback followed by more expensive, higher
  confidence stages. Keep cheap multi-failure diagnostics early and retain all
  final integration checks; never reuse unit acceptance as deployment proof.
  [Deployment pipelines](https://martinfowler.com/bliki/DeploymentPipeline.html).
- Google's release engineering uses reproducible artifacts and independent
  system tests on packaged builds. Reuse the existing immutable EE identity and
  ZDD lifecycle acceptance rather than another build or rollout engine.
  [Google release engineering](https://sre.google/sre-book/release-engineering/).
- The 2023 Actions runner discussion documents that an aggregate check can
  misrepresent skipped dependency jobs. Preserve the existing all-required,
  success-only exact-SHA verdict; skipped jobs cannot close failure families.
  [Actions practitioner discussion](https://github.com/actions/runner/discussions/2419).

These are design inputs, not measured Gludd speed improvements. Evaluate future
scheduling changes with exact hosted timing; retain censored failures and
separate queue delay from test runtime. Do not cancel an active validation run,
change required workflows or relax resource limits to produce faster numbers.

### Native build progress

The EE build in run 38340797931 emitted no native build output between
03:28:22 and 03:30:52. The existing Builder parser defaults to verbosity 2;
the wrapper now explicitly selects its supported verbosity 3 for Docker and
Podman. This streams the maintained tool's existing output without adding an
observer or increasing a timeout. Two parser-forwarding regressions fail first,
then all 117 related tests and 33 tests in the existing Python 3.11 environment
pass. Original IID, import, offline, naming, failure and cleanup guards remain
unchanged. The SearXNG acceptance failure and final hosted/full-gate proof remain
separate requirements.

### Complete SearXNG image acceptance

The real image in run 38340797931 passed OpenTofu but its required Python smoke
failed because VCS-installed SearXNG lacked frozen version metadata and retained
the production default secret. Keep all nine imports and fix these inputs.

The final image stage now runs the pinned upstream
[native freezer](https://github.com/searxng/searxng/blob/7b4612e86250389dc9d5ee67e4cc2cd64d06602a/searx/version.py)
in a private namespaced clone detached at the exact verified revision. It copies
the generated physical module only after canonical PEP 610 URL/full-commit checks
and [normalized version comparison](https://packaging.pypa.io/en/stable/version.html).
The installed short hash must match the verified full source commit; the frozen
hash records that full commit. Native Git metadata and Docker-tag derivation stay
intact. Acceptance independently checks the installed module path and identity.

Only the non-serving smoke child sets a fresh 32-byte random secret through the
supported [SEARXNG_SECRET setting](https://docs.searxng.org/admin/settings/settings_server.html).
No secret is baked into the image and production default-key refusal remains.
The [2022 upstream user report](https://github.com/searxng/searxng/issues/1519)
documents similar Git-version noise and default-key refusal when settings were
misapplied. That is supporting context, not proof that our image is repaired.

Ten cases fail first and the real pinned freezer already passes. All 128 related
cases then pass under Python 3.14 and existing Python 3.11, with 92.06% combined
coverage and unchanged 85/75 floors. Source fixtures retain upstream AGPL notices.
The fixture proof does not replace the full installed nine-import image smoke,
successor hosted proof, or canonical full gate.

Independent review also required execution of the emitted smoke program rather
than relying only on AST/text inspection. Nine cases now exercise its actual
metadata predicates with dependency availability isolated: matching full and
normalized-short versions succeed; foreign/missing provenance, version, frozen
commit or installed-file identity fail without a success marker. All 137 related
cases pass in both native environments, with the same focused coverage. These
tests do not claim that isolated imports prove the real image dependency graph.

### Verdict caller compatibility

Hosted run 38340797931 found seven assertions still inspecting the old inline
verdict recipe. Actual release pollers also consumed its CI GREEN/RED labels.
The native owner now restores a compatibility line after validated publication
and recording checks, retaining every exact-SHA workflow row and actual run ID.
Validate-only, owner errors and incomplete/foreign history emit no terminal
proof marker. Explicit CI_BRANCH takes precedence over legacy BRANCH; otherwise
the native recipes capture the current branch once, alongside the captured SHA.

Original tests retain identity, format and green/red/pending expectations while
following the shared owner and executing real Make output. Complete RED reports
21 failures with seven controls; branch selection reproduces four failures with
two precedence controls. All 287 related cases pass, and the two production
owners measure 98% and 100% coverage. Existing consumer grep filters are exercised
directly without running their polling loops or making hosted queries.

The [GNU Make manual](https://www.gnu.org/software/make/manual/html_node/Running.html)
defines Make's own exit statuses; it does not preserve arbitrary recipe codes.
A [2016 user report](https://stackoverflow.com/questions/39875208/recursive-make-loses-status-code)
documents this long-lived confusion. Tests retain both native outcome and
recipe Error 1/Error 2 evidence instead of interpreting a wrapper exit as proof.
Focused/source acceptance does not close the canonical gate or hosted proof.

### Scheduling owners

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

## Hosted shard scheduling and automatic timing

### Fresh-process help admission

Hosted Build `38325261900` exposed a duplicate public help row in its isolated
lane. The original assertion failed natively before the source description was
consolidated; all 35 original isolated cases now pass in 4.61 seconds. The failing
help node runs in its own second pytest process inside the existing early owner,
not in the combined cohort. Routing and roster regressions fail first; neither
the owner's 90-second runtime/60-second quiet limits nor the exhaustive gate are
relaxed. A native repair is not an exact hosted resolution or release proof.

### Automatic terminal push verification

Current TASKS input also runs through the real read-only detect-secrets owner in
the early cohort. The old public-span hash assertion alone could remain green
while a later quoted receipt extended the detector's actual match. The new
regression fails first on that exact condition and proves the canonical baseline
is not rewritten. Preserve the reviewed span, record later receipt paths as
unquoted prose, and keep every detector/hook enabled; do not auto-approve drift.
All 120 scanner/routing cases pass. The expanded early owner passes 1059 cases
plus its fresh help node in 56.9s, keeping its 90/60 bounds, 21 phases and required
complete gate unchanged. Scanner settings and baseline bytes are unchanged.

The exact snapshot producer now feeds terminal evidence into the existing native
cooldown/history writer, avoiding a separate manual bookkeeping step or extra
query. Snapshot validation and publication must succeed first. Every selected
run's details must confirm completion and conclusion, from the canonical repository
with both default required workflows. Pending/missing workflows, narrowed or foreign
selectors, observation errors, changed snapshot identity and a different last pushed
SHA cannot verify it. Repeated same-verdict observations do not rewrite state.
Write failures remain visible and fail closed. Gate and release authority remain false.

The legacy verdict command now reuses the canonical all-workflow verifier:
successful Pages cannot hide failed Build. One captured SHA/branch reaches the
backend and recorder; direct native exit codes distinguish failure from pending
without recursive Make aliasing. The Python owner, not a numeric shell-exit guess,
authorizes recording; pending, mixed failure/pending and owner crashes leave pushed
history and restart budget intact. Validation never queries or records. Initial
native and publisher regressions fail first. Independent source review then supplies
22 publication/authorization cases: 21 fail first and one original-parity case passes.
All 140 publisher, native-target and cooldown cases pass. The unchanged coverage
profile passes 133 cases at 97% combined coverage with 85/75 audit.
Both failure families join the existing early owner, without new phases or
relaxed bounds. Full gate and exact hosted successor proof remain mandatory.

The historical [operator report about incomplete job listings][context-operator-completeness]
supports treating a selected workflow or human listing as incomplete evidence;
it does not assert that today's locked CLI has that old defect. Aggregate,
identity-bound native observations are the source of truth here.

### Search diagnostics are not complete-scan proof

The original transient-error and empty-PATH fallback cases fail before the
correction; all 28 search/audit and literal-transport cases now pass. The existing
system mktemp is invoked absolutely. Discovered matches remain usable, but
original stderr is retained and `SEARCH-PARTIAL` explicitly marks incomplete
scans. Errors without matches (including invalid regex) remain errors, distinct
from ordinary no match. Both original failure families and the complete audit
file join the existing early owner without changing its bounds or full-gate
requirement.

The [2025 operator discussion and maintainer answer][search-partial-discussion]
confirm that ripgrep can find matches and still return 2 for a scan error. Gludd's
existing query contract reports match presence, not exhaustive scan success; it
does not silence diagnostics or manufacture a release verdict. The
[2019 invocation-error report][search-error-report] is historical evidence for
keeping error and no-match states distinct, not a claim about today's version.

[search-partial-discussion]: https://github.com/BurntSushi/ripgrep/discussions/2976
[search-error-report]: https://github.com/BurntSushi/ripgrep/issues/1159

### Byte-bounded failure context

Exact job context must be bounded in bytes, not only lines. One failed workflow
assertion produced over 60,000 tokens despite a short context window. The
existing formatter now limits each displayed line to 2048 UTF-8 bytes and the
whole output to 32768 bytes, including newlines. Truncation is explicit, preserves
whole code points and a late literal hit when it fits, and never changes the full
source match count, source line numbers, overlap order or input file. Normal
fitting output and existing success/refusal statuses remain unchanged.

The [2022 GitHub CLI operator report][context-operator-completeness] describes
incomplete human job listings preventing reliable failure comparisons. The
[2023 long-job-name report][context-operator-names] describes archive-name
truncation causing missing logs; it was closed through upstream work. These are
historical evidence about identity and presentation, not claims that today's
locked tooling reproduces them. Gludd retains authenticated numeric job/run
binding and exact artifact roots; a bounded display is never a complete verdict.
Downloaded artifact files stay unchanged. The native job reader's existing
temporary-download cleanup still applies; its complete source remains the
exact hosted job, not an alleged new local cache.

Fourteen cases fail first, then all 41 formatter/hosted-tooling cases and all
28 coverage cases pass at 94% combined coverage, retaining 85/75 floors. Three
early routing/roster/wiring cases fail before both complete formatter cohorts
gain ownership. Actual authenticated replay of the same failed hosted job returns
11143 bytes with visible truncation and the actionable context intact. This
optimization bounds diagnostic cost; it does not prove release readiness.

[context-operator-completeness]: https://github.com/cli/cli/issues/6378
[context-operator-names]: https://github.com/cli/cli/issues/7642

### Safe workflow parsing within the unchanged budget

Build 38336411509 at 6ba81dd5 passes Python 3.11 quality, but Python 3.12's early
owner expires at 90.0s with passing cases reaching 89%. Existing workflow helpers
reparse full YAML through the Python loader on every assertion. Use PyYAML's
existing C-safe backend when installed, retaining the Python-safe fallback and
fresh parses/results. Do not cache source, raise the timeout, drop cases or
weaken schema, normalization, safe-tag or error behavior.

[Official documentation][safe-yaml-docs] and [library source][safe-yaml-source]
establish that safe_load selects SafeLoader; installing LibYAML alone does not
change that selection. The [2020 operator performance report][safe-yaml-perf]
and [2022 C-loader error report][safe-yaml-error] reinforce explicit backend and
error checks; neither alone proves the cause or current timing of this pipeline.

Three actual-reader routing cases fail first and 45 safety/parity cases pass.
All 265 parser/workflow/registry/privacy cases then pass in 10.10s. Both old
matrix-mutation tests inject at their existing workflow helper instead of the
replaced parser call, preserving all input data, cases and assertions. Actual
bounded-owner and exact-SHA hosted success still determine throughput acceptance.
The complete 48-case backend cohort now joins early admission after two routing
and roster failures reproduce first. All 104 focused routing cases pass, and the
expanded owner passes 1107 cases plus its fresh help node in 57.5s within the same
90/60 bound. The existing Python 3.11.14 environment also passes 162 parser and
original-workflow cases. This is native acceptance, not successor CI success.

[safe-yaml-docs]: https://pyyaml.org/wiki/PyYAMLDocumentation
[safe-yaml-source]: https://github.com/yaml/pyyaml/blob/main/lib/yaml/__init__.py
[safe-yaml-perf]: https://github.com/yaml/pyyaml/issues/439
[safe-yaml-error]: https://github.com/yaml/pyyaml/issues/655

### Exact timing and creation order

The security contract compares the exact eight-label multiset, not scheduling
order. Its original assertion and reversed-order regression fail before the
correction; all 67 security cases now pass, including duplicate, missing and
unknown-label rejection. The complete file joins the existing early owner.
Separate unchanged checks still pin canonical local order, hosted creation
priority and the three-runner cap. No shard, Python version, warning policy or
coverage database is omitted; exact hosted successor proof remains required.

The hosted scheduling experiment changes only the existing eight-label matrix
creation order. Known successful long lanes are created first; `unit-2` remains
explicitly provisional because its failed parent interval is censored, not a
successful full-shard duration. The local runner's canonical shard membership
and default order stay unchanged. The three-runner cap, Python 3.11, isolated
fresh-process suites, worker and batch policy, warning-fatal arguments, timeouts,
all eight coverage databases and the 85/75 audit remain mandatory.

The automatic replay of terminal Build `38296150696`, exact SHA
`f38ce53d15a8e51514b0e517fbda22a965777f87`, gives this baseline. It keeps the
run's failure exit and confirms both gate legs completed before these jobs.
An earlier provisional interpretation overstated the two short unit-1a timings;
the source-derived measurements below replace that interpretation, not the
original failed evidence.

| Shard | Test-step seconds | Delay after gate (seconds) | Timing state |
| --- | ---: | ---: | --- |
| other | 3214 | 1633 | successful complete interval |
| unit-3a | 2566 | 1338 | successful complete interval |
| unit-3b | 1813 | 1470 | successful complete interval |
| unit-1b | 1431 | 3 | successful complete interval |
| unit-1d | 1241 | 347 | successful complete interval |
| unit-1a2 | 415 | 3 | successful complete interval |
| unit-1a1 | 302 | 3 | successful complete interval |
| unit-2 | 850 | 455 | failed/censored, not a full-success duration |

The matrix follows the seven successful durations in descending order, then
the explicitly provisional failed lane. This ranks known complete intervals;
it does not estimate `unit-2`'s successful runtime or prove the experiment helps.

GitHub documents matrix job creation order and a concurrency cap, not guaranteed
runner acquisition order. Earlier creation is therefore a reversible experiment,
not a measured throughput result. Compare job starts, test-step durations and
coverage fan-in on the replacement's exact SHA before claiming a speedup.
[Matrix creation semantics][hosted-matrix-syntax],
[concurrency cap][hosted-matrix-cap].

The existing immutable run-summary producer exposes timing without another
runner, tracker, cache, dependency or daemon:

```sh
make ci-run-summary RUN=38296150696 CI_RUN_SUMMARY_REPO=sandboxcom/gludd CI_RUN_SUMMARY_VALIDATE_ONLY=0 CI_RUN_SUMMARY_SHARD_TIMINGS=1
```

It uses the same fetched job/step payload, a visible lookup-start marker and a
15-second fetch timeout. The `SHARD_TIMINGS` JSON is bound to the returned run ID
and full commit SHA. It preserves source job/step timestamps and conclusions,
reports all eight canonical Python 3.11 shards, and derives test-step seconds
and delay from the latest completed gate timestamp. Missing, queued, failed,
partial, cancelled, invalid or ambiguous evidence stays explicit; failed elapsed
intervals are marked censored rather than ranked as successful completion times.
Both `release_ready` and `gate_evidence` are always false. Legacy job-summary
output and failure exits remain unchanged. A failed run still returns nonzero
even when its timing diagnostics are usable.

The documented validate-only behavior performs no lookup and emits no durations:

```sh
make ci-run-summary RUN=123 CI_RUN_SUMMARY_REPO=sandboxcom/gludd CI_RUN_SUMMARY_VALIDATE_ONLY=1 CI_RUN_SUMMARY_SHARD_TIMINGS=1
```

Long-lived forum evidence supports measuring instead of assuming scheduling
behavior. A December 2024 report describes unordered starts despite a
one-job matrix cap. An August 2020 report initially described batch barriers,
but its responder observed replenishment as jobs completed and the author
accepted that correction. These are practitioner reports, not guarantees about
the current service or proof of Gludd's queueing cause.
[Ordering report][hosted-ordering-report],
[corrected batch-barrier report][hosted-slot-report].

Use the existing runner and standard-library timestamp/JSON handling. Richer
duration partitioning is a separate change requiring exact-once collection,
fixture/resource lifetime and complete coverage reconciliation; applying another
splitter independently to the current batches would not establish those
properties. This administrative change touches no serving component and needs
no service restart or data migration. Rollback restores the prior array and
removes the optional timing flag without changing shard ownership.

The instruction-conforming parity test now compares the exact shard multiset,
not the hosted creation order against the local default order. Its original
path/exclusion checks and local-order assertion stay intact; three mutation
cases reject duplicates, omissions and unknown labels. One pre-existing callback
typing error is repaired with an equivalent explicit function, retaining all
original event and early-stop assertions.

Build `38325261900` exposes a second representation-dependent assertion in the
private-policy release contract: `other]` requires the privacy lane to be last.
The instruction-conforming repair uses PyYAML to require exactly one `other`
matrix member, preserving every original local/hosted privacy, warning and
isolation assertion. Four new regressions accept either creation order and
reject missing or duplicate privacy ownership; all 182 workflow, registry and
privacy cases pass. The complete five-case file now executes in the existing
early owner, not only late in `unit-3b`. Mapping/roster acceptance fails first,
then all 45 promotion/plan cases pass with 44 families and unchanged budgets.
The existing scheduling references above remain the authority for the
creation-order versus runner-start distinction; this repair claims no speedup.

Actual acceptance: the timing mode first fails 17 new cases with 10 legacy cases
passing; native unfinished timestamps add another failing-first case. Three
source-interval/job-identity regressions then expose invalid intervals reported
as success and fail before the producer is repaired. The final 46-case producer
and Make/policy coverage replay measures 92.24% combined, 93.78% line and 88.30%
branch coverage, above the unchanged 85/75 requirements. Complete scheduling,
canonical registry, isolated-suite and fail-closed artifact acceptance passes
154 warning-fatal cases. Early mapping/roster and exact-wiring checks fail first;
the complete 40-case promotion/plan slice then passes. The actual existing early
owner executes 717 cases in 37.84 test seconds (38.8 observed), retaining all 39
families, 21 phases, the 2850-second ceiling and 90/60-second phase bounds.
These are focused acceptance results, not a passing full gate, exhaustive
coverage proof, hosted optimization result or release completion.

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

### Current capacity owns local proof scheduling

The producer now reads `automatic_disk_cleanup.inspect_usage()` on each actual
snapshot, without calling cleanup. It records current scratch and volume use,
the canonical owner's limits, and their soft/hard distinction. It does not
derive present capacity from a historical failed gate. An unavailable, boolean,
non-finite or out-of-range measurement is an observable lookup error, not green
admission.

Missing-proof actions prioritize observation errors, then real hosted failures,
then clearing hard capacity before local proof. Hosted success retains its
original CLI meaning; blocked capacity never becomes a passing local gate.
Soft scratch pressure alone does not invent a new hard floor. Hosted work may
continue independently while local proof waits. No cleanup authority, serving
change, daemon, extra tracker or policy bypass is added.

The April 2026 [Bun user report][native-extraction-pressure] describes persistent
native-library extraction copies growing temporary storage. It motivates
observing storage alongside work, but does not establish Gludd ownership or
authorize deleting shared cache files. The repository-specific scheduling gap
is reproduced by eleven failing-first cases and four additional boolean cases;
all 85 exact-workflow/verdict cases then pass. Both measured source files meet
unchanged 85/75 coverage floors, at 97% combined coverage. The complete existing
early cohort owns this regression; actual full gate and exact-SHA proof remain
mandatory.

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

Remaining proof requirements are explicit: complete EE dependency resolution and
replacement image proof; validated final source-review/canonical gate/coverage
receipts; and measured hosted critical-path optimization. Hosted creation order
has been changed, but has not been demonstrated faster. Capacity
admission and protected-history ownership are already codified and still
require real capacity or operator authority, not another status-file edit.

This automation leaves every missing-proof requirement above in force. A
generated success cannot substitute for the passing full canonical gate,
85% aggregate/75% per-file coverage, required hosted workflows and artifacts for
the same frozen SHA, independent source review, or required native/browser
acceptance. It does not clear the retained capacity prerequisite. Partial
local evidence and historical failures stay explicit, and TASKS.md cannot be
ticked from a current-observation JSON.

[native-extraction-pressure]: https://github.com/oven-sh/bun/issues/29585

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
[hosted-matrix-syntax]: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstrategymatrix
[hosted-matrix-cap]: https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#defining-the-maximum-number-of-concurrent-jobs
[hosted-ordering-report]: https://github.com/orgs/community/discussions/147882
[hosted-slot-report]: https://github.com/orgs/community/discussions/26042
