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

### Temporary volume headroom authorized on 2026-10-14

At the user's explicit request, the canonical repository-volume limit is raised
from 90% to 95% for current release work. `check_disk_usage.DISK_USAGE_PCT_LIMIT`
is the only policy value: native cleanup, legacy disk aliases, commit/gate
admission and pipeline snapshots consume it. Usage up to and including 95% is
admitted; usage above 95% and unavailable or invalid measurements still fail.
This is a temporary policy change, not an automatic expiry or cleanup waiver;
restore the canonical value to 90% when the user ends this exception.

The 100 MB scratch policy is unchanged. Native preflight may admit protected or
recent scratch above that soft cap while the hard volume limit is healthy; it
still refuses ownership/inspection/cleanup errors. No unknown data is authorized
for deletion, and coverage, ZDD, exact-SHA and deployment-proof requirements
remain unchanged. The old failed gate stays failed until a fresh gate succeeds.

### Real worktree admission

The push entrypoint delegates to the existing path-qualified worktree owner
with mandatory clean-state assertion. A temporary unconditional-success stub
cannot count as admission. Six real-Git regressions require native identity on
clean success and visible refusal for modified, staged, untracked, deleted or
unknown state. The guard must not mutate, stash or conceal pending feature work.
This source acceptance does not replace gate, exact-SHA CI or deployment proof.

The normal staged-content preflight runs the existing read-only secrets hook
before collection. Native pre-commit selects files and preserves configured
filters; the canonical baseline is never refreshed by admission. Use
`make check-secrets-files SECRETS_FILES=scripts/check_clean_tree.py SECRETS_BASELINE_FILE=.secrets.baseline`
for a narrow diagnosis. A generated non-credential fixture must still be refused.
`make git-unstage-files FILES='explicit paths'` is index-only checkpoint support;
it cannot restore or discard worktree content.

Explicit `UV_PROJECT_ENVIRONMENT` selection in `test-files` also aligns the
inherited active-environment hint before invoking UV. A real nested pytest
regression checks interpreter identity and rejects UV mismatch warnings; no
active-environment override, warning suppression or environment sync is used.

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

The finite `status-heartbeat` diagnostic remains usable when the 32-command
inventory is full. Admission requires explicit ASCII `COUNT` in 1..24 and
`INTERVAL` of at least 300 seconds; the scheduled waits must fit the supervising
owner's finite positive timeout. Only the documented status variables and
single Make target qualify. Unbounded waits, recipe/Makefile overrides,
duplicate or unknown settings and extra targets retain ordinary admission.
This uses the existing observer: it neither adds a command-history slot nor
erases failures, grants release authority or increases the 32-command limit.
The native detector's public repository-argument finding is reviewed at exact
hash `7e9d0072485183cb48eb9ab08acac44f7293227e` in the fixture and its audit test.
Native refresh adds those two findings without removing others. The regression
pins both non-credential labels; original scanner settings and existing reviews
remain. No source string is rewritten to evade detection and no file-wide
exemption or hook bypass is used.

Source review of `a9738e0` refuses release acceptance: exempt diagnostics yield
no admission object, and terminal observer cleanup consequently omitted native
reference protection. Four real-process controls cover both help/finite status
waits sharing a label with a full failed inventory, and missing/malformed
progress after a diagnostic starts. Preserve all 32 immutable failure receipts
and their logs, not mutable `current.json`. The CLI now carries its validated
focus root separately from execution admission, and the existing reference
reader supplies retention protection in both routes. Lost or invalid progress
terminates with native observer failure 125 before pruning; no passing result
survives. Source review, full gate and exact-successor acceptance remain pending.

Re-review of `60b8187` identifies a second boundary: terminal preservation alone
cannot stop an initial publication from replacing a referenced failure with the
same run ID. Its owned gate was cancelled with `=== GATE: ABORTED ===` and no
survivors before changing source; that result remains non-passing evidence.

Fifteen real-process regressions fail first, covering help and finite waits with
an existing receipt, orphaned log/trace or reference-only identity, unscoped
artifact collisions, the read-only `current` alias, and competing starters.
Before initial publication, the native observer checks the same canonical
references used by retention and refuses occupied artifact identities. An
atomic exclusive filesystem link claims a fully fsynced receipt exactly once;
observed logs use exclusive creation rather than truncation. A refused starter
does not run its child, replace old receipts/logs or advance their current alias.
Separate controls exercise the actual exclusive-link refusal and a log created
after the precheck. Mutable status updates within the winning run, read-only
lookup aliases, ordinary log-open failure evidence, and unattended compatibility
retain their established contracts. Exact-successor proof remains required.

Verification reports 291 passing observer-consumer cases on Python 3.11 and
193 passing core cases on Python 3.14. Native coverage audits
`.gate-logs/coverage-20261014T215945Z.json` (four owners) and
`.gate-logs/coverage-20261014T220116Z.json` (progress owner, 193 cases) meet
unchanged aggregate 85% and per-file 75% floors. Strict typing, Ruff and the
read-only native secrets admission pass. These are repair checks, not a passing
full gate or evidence that another backlog item is deployment-ready.

One-pass source review refuses `3f7cbaf` for two remaining concurrency gaps:
the trace was checked but not allocated before child startup, and the reference
snapshot could become stale before pruning. Controlled native runs reproduce
both failures, including destruction of a competing live log and newly recorded
canonical failure evidence. A related failing control exposes pruning of a
late-finishing current run solely because its start time is older.

The existing observer now exclusively creates the trace before launching its
child and hands it to the native plugin without truncation. A competing claim
fails with observer code 125 before child execution. Standalone trace reset
semantics remain unchanged. The original progress owner holds its short lock
across reference validation and pruning; neither ordering can lose new failure
references. Current-pointer publication and pruning use the already pinned
FileLock dependency with a per-label, private metadata lock, and current-run
receipts/logs survive late completion. Neither lock spans child execution.
Ordinary unattended call shape and failure-code behavior are retained; the
existing failing-cleanup test is unchanged. Four cases fail first and the
focused compatibility matrix reports eight passed. Broader verification reports
298 passed on Python 3.11, 200 passed on Python 3.14, and four-owner coverage
meets unchanged 85/75 floors (.gate-logs/coverage-20261014T222218Z.json).
The 200-case progress-owner coverage audit also passes unchanged floors
(.gate-logs/coverage-20261014T222452Z.json); strict typing and Ruff pass and the
native secrets admission adds no finding or baseline change.
The next clean reviewed source still needs its full gate, exact-SHA hosted and
deployment acceptance; no source-only decision or unit result closes the item.

This protects execution admission, not the model's internal reasoning or every
editor action. The existing exact-SHA gate, coverage, deployment acceptance and
promotion checks remain mandatory. No runtime restart or serving change is
required for this control-plane addition.

### Native Galaxy invocation log ownership

Build and Release run `38645159173` at `3f7cbaf` fails the existing real offline
collection build/install case: the second native build reuses `galaxy.log`, and
the observer correctly refuses to overwrite the first invocation's evidence
with code 125. The failure is not a timeout or a reason to relax that refusal.

The existing collection caller now selects a fresh native-ID log for each
invocation and validates that exact immutable output. Only then does an atomic,
fsynced copy refresh the replaceable `galaxy.log` diagnostic alias. Nonzero and
warning output remains visible and retained; concurrent alias updates cannot
change another invocation's acceptance. Copy/publication failure leaves the
old complete alias and immutable receipt/log intact, removes its own temporary
file, and fails visibly. Replacing an alias symlink never writes to its target.
The explicit-path/int-return runner contract and observer collision refusal
remain unchanged; the staged collection owner retains cleanup responsibility.

Twelve added controls cover real repeated children, failures and warnings,
alias races, symlinks, failed atomic I/O cleanup and direct-run compatibility.
The existing child-isolation test now requires trace reset flag zero because
the parent already exclusively allocated its fresh trace; every environment,
cwd and parent-isolation assertion remains intact. The isolated focused cohort
reports 119 passed on Python 3.11 with fatal warnings, Ruff and strict typing.
Integrated development verification reports 124 passed, including all five
native roster cases, with four-file Ruff and strict typing success.
Four failing-first native roster controls require the new file alongside all
55 existing Azure coverage selections, with unchanged 85/75 floors and a
300-second bound. The native ownership writer admits the reviewed atomic
temporary acquisition, bringing the inventory to 259 resources.

Isolated worktrees reuse the existing locked clone engine through explicit
`DUPLICATE_CODE_ENGINE=/Users/shawnwilson/gludd/.opencode/node_modules/.bin/jscpd`;
they do not install another toolchain. The native checkpoint owner verifies
`4cb3c9ef` in the main checkout but intentionally refuses that foreign owner in
the agent worktree. Integrate only the three reviewed files through `wt-import`
into clean development and use its normal commit guards. The retained checkpoint
is never popped, dropped, rewritten or bypassed. Source review, full gate,
exact-successor hosted results and deployment acceptance still remain required.

### Native Galaxy progress visibility

Build run `38812181930` on candidate
`1ff095f19720a6b3f501238fbc92015f2b99dfe3` reaches the existing 120-second
collection-acquisition deadline with zero child bytes. The observer continues
heartbeats, but those heartbeats cannot identify the operation holding up Galaxy.
Python block-buffers piped stdout, and the pinned Ansible Display implementation
writes progress without flushing. Killing the child can therefore lose useful
diagnostics. The existing private collection environment now sets
`PYTHONUNBUFFERED=1`; it preserves stderr, fatal warnings, all exact inputs and
the original observer/process-group deadline and teardown.

The regression runs a real unflushed stdout/stderr child with a TERM-resistant
descendant and an explicitly buffered caller. It requires the progress bytes
while the native receipt is still running, then retains timeout code 124, both
process-death assertions and the unchanged parent environment. The stdout case
fails before the change while the stderr control passes. No assertion or timeout
is relaxed, and no runner, retry loop or persistent cache is added.

The unchanged-source diagnostic with native unbuffering obtains all eight locked
artifacts in 55.7 seconds (56.7 seconds including its outer owner), retaining lock
`7d176b02362222535077a47b4a4f8191e6e9b4a00be0fbc6d3a1f93d4a7a9588`.
It spends approximately 31 seconds in dependency-map preparation. A separate
native context-only run then builds all eight project collections and admits the
complete context in 4.8 seconds. This is artifact/context evidence only; it does
not explain the hosted slowdown or prove an installed image or live deployment.

[Python documents piped stdout buffering and native unbuffering](https://docs.python.org/3.11/library/sys.html#sys.stdout).
Ansible's pinned 2.19.14 implementation enumerates collection versions before
filtering an exact pin even with `--no-deps`; the same behavior is present in the
inspected 2.21.5 owner. [A June 2025 user report](https://forum.ansible.com/t/ansible-galaxy-https-galaxy-ansible-com-taking-very-long-to-respond/43406)
describes slow Galaxy responses, and [a September 2026 discussion](https://forum.ansible.com/t/timeouts-when-installing-collections-from-ansible-galaxy-https-galaxy-ansible-com/46301)
describes recurring CI timeouts around paginated/version APIs. These reports
support measuring unnecessary lookups; they do not establish this run's cause.
Any exact-metadata/native-artifact optimization is separate work and must keep
all network calls inside the existing total deadline, authentic native
checksum/token/TLS behavior and the final independent lock/metadata checks.

No serving workload is changed: existing warm, route, drain and rollback owners
remain intact. Reverting the unbuffering setting only restores the prior output
behavior. The unchanged canonical Azure/runtime coverage target passes 1,521
tests in 55.17 seconds and its 85/75 audit for all 70 measured files
(`.gate-logs/coverage-20261015T221733Z.json`); 78 focused cases and Markdown lint
also pass. Clean full gate, exact-successor hosted proof and deployment acceptance
are still required; no S98 completion follows here.

### Exact-pin acquisition without version enumeration

Acquisition already has a reviewed, complete collection lock. Native Galaxy's
CLI still enumerates all available versions before selecting those exact pins;
that extra work is unnecessary for acquisition, though refresh still needs its
resolver. A failing-first real-native regression forbids version enumeration
and exposes the old path before the acquire-only change.

The existing observed child now asks native `GalaxyAPI` for each exact version
and hands its native candidate and metadata to `ConcreteArtifactsManager`.
Metadata identity, checksum and dependency closure must match the reviewed lock
before transfers. The native manager retains the server-returned HTTPS URL,
token, certificate verification, checksum validation and retry behavior. The
parent still independently verifies sizes, hashes, archive metadata, core
compatibility and dependencies before atomically publishing a new generation.
Refresh and offline replay retain their existing native paths.

Discovery, metadata, retries and downloads all share the same existing total
120-second observed-child deadline; this is not a per-artifact timeout reset.
Progress markers identify metadata and transfer work. There is no new download
client, persistent cache, resolver, pin update or deployment authority.
The dated Galaxy reports linked above motivate reducing unnecessary requests,
but neither establish the hosted timeout's cause nor prove a speedup.

Source review also exposes an unsafe new public worker entry point: it ignores
validate-only and can escape the supervising timeout. Three failing-first
controls pin that defect. Remove the public worker mode entirely; the existing
staging owner dispatches one private function with literal Python child code,
an explicit checkout import root and JSON argument data. The normal staging
validate-only paths do not invoke transport, and both former worker CLI forms
are rejected. No token protocol, second observer or alternate timer is added.

The read-only commit guard also flags the intentionally invalid credential URL
used by both child and in-process rejection cases, then the audit test's literal
password assertion. Native baseline refresh adds exactly those two findings and
removes none of the existing 1,141 findings. Review only those exact fake-password
fingerprints as non-secret; the audit regressions pin both `.invalid` URL paths
and the precise reviewed findings in their respective files.
Keep the negative tests, detector configuration, existing audit labels and
file scanning intact: no string obfuscation, inline suppression or file-wide
exclusion is introduced.

These are upstream internal Python interfaces, not a supported public Python
API. Tests exercise the installed core 2.19.14 with real native objects and
HTTP-boundary fixtures, including custom origins, credentials, TLS, tampering
and timeout teardown. Core 2.21.5 source inspection is not runtime acceptance:
its main-owned environment is refused by strict worktree confinement, so that
runtime check remains pending without an isolation override or duplicate install.
All 98 focused staging cases pass after the entry-point correction (14.55 seconds).
The first canonical run passes 1,538 tests but fails aggregate branch coverage
at 84.6% (`.gate-logs/coverage-20261015T223103Z.json`); all 70 individual files
meet 75%. Preserve that failure while adding genuine coverage under the unchanged
85/75 policy. The subsequent run `20261015T223610Z-77401-56ab9983` loses its
observer during a server restart at 95% and has no terminal coverage result;
native status confirms its owner is inactive/orphaned. Retain that incomplete
record and recover once through the original coverage owner, not a forged
success or a duplicate live run. The recovered run passes all 1,562 tests in
58.07 seconds and the original 85/75 audit for all 70 measured files
(`.gate-logs/coverage-20261015T223912Z.json`). Both changed Python files pass Ruff
and strict typing. The next resource check reports 96% volume use, exceeding the
user-authorized 95% hard limit; native cleanup inspection must precede further
expensive proof attempts. The native cleanup owner then removes only eligible
reproducible worktree caches/bytecode and rechecks at 95%, retaining source,
historical evidence and protected/recent scratch. Full gate, exact-successor hosted build and deployment
acceptance remain required; this change does not complete a backlog item.

### Observer startup interruption readiness

Build run `38590551409`, candidate
`ffde15309895edb9675a37095f1878503880ddb1`, exposes a real observer startup
race in unit-3a: the existing SIGTERM test receives raw exit `-15` instead of
the required `143`. Publishing `running` before installing signal handlers
allows a caller to interrupt an owner that cannot yet perform graceful cleanup.

The existing streaming owner now arms its SIGINT/SIGTERM handlers before that
publication. Publication remains inside the handler-restoration boundary. If
publication fails, the owner terminates/reaps its process group and closes both
output pipe and log; genuine status errors still return `125`. No sleep, retry,
timeout increase, alternate runner, or test-expectation change is introduced.
Six deterministic boundary regressions fail first, also exposing two unclosed
pipe warnings. The original real-process interruption test remains unchanged.
Static test imports follow the owner's existing mypy module coordinate; runtime
imports and assertions remain unchanged, without type ignores or skipped imports.

All 236 related cases pass on Python 3.11 and 3.14. The unchanged four-owner
coverage profile passes at 93.1% lines and 86.3% branches, with every file meeting
both 75% floors (`.gate-logs/coverage-20261014T160142Z.json`). Strict source/test
typing, Ruff, docstrings and the unchanged 258-resource inventory pass.

The [Python 3.11 signal documentation](https://docs.python.org/3.11/library/signal.html)
specifies main-thread handler execution and warns about asynchronous exceptions.
Keep the existing handler's queued request and normal-loop cleanup rather than
raising a new exception from it. In the
[March 2025 practitioner discussion](https://discuss.python.org/t/convert-sigterm-to-an-exception-by-default/86283),
service authors describe default SIGTERM bypassing cleanup and recommend explicit
graceful shutdown. This long-lived failure class supports the design decision;
it is not independent proof of this Gludd race or its repair.

This is a control-plane ZDD correction: no serving process is restarted, and an
already running observer retains its loaded code until it terminates. Future
invocations use the repaired owner. Rollback restores the prior source through
the normal branch workflow while retaining failure receipts. Local tests and
coverage do not supply clean full-gate, exact-successor hosted, installed-image,
or deployment acceptance; release readiness remains pending. Let all required
jobs finish so additional failures from this same candidate are not discarded.

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

The [runtime admission scheduling decision](RUNTIME_ADMISSION_THROUGHPUT.md)
records the unchanged whole-roster deadline, real shared-state race regression,
bounded existing-xdist repair and native-versus-hosted proof distinction.

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

Three repaired native failures now run as exact initial selectors in that same
compatibility owner: atomic candidate/control JSON evidence, admission of a
verified main-worktree checkpoint from a registered peer, and refusal of a
dollar-bearing duplicate-code engine before Make parsing or export execution.
Their manifest families retain the original downstream tests and required full
gate. Selecting only those three nodes adds no unrelated sibling collection;
every existing early selector and the separate fresh-process help check remain.

The three executable-routing cases and the additive manifest roster first fail
before implementation. The complete promotion/workflow slice plus the three
original failure nodes then passes all 109 cases with fatal warnings on canonical
Python 3.11. The exact inventory pin grows by the same three selectors; its
existing entries and assertions stay intact. The manifest validates 78 families,
21 phases and the unchanged 2850-second ceiling, including the early owner's
90-second runtime and 60-second quiet limits. Ruff, strict test typing and the
234-target Make contract also pass. These are focused acceptance results; final
canonical gate, coverage, exact-SHA hosted and deployment proof remain required.

The actual 90/60-bounded owner replay from the isolated
`agent-early-release-admission-20261014` worktree retains its failed result in
`.gate-logs/early-release-admission-20261014.log`: 1710 cases pass, one shared-roster
pin fails and 18 SearXNG fixture setups are refused, in 70.0 observed seconds.
Those fixture Git helpers omit an explicit subprocess working directory; the
worktree confinement guard refuses their checkout and remains unchanged. This
invalid-context replay supplies no passing native admission or release evidence.

The shared scheduling `ROSTER` gains the same three exact initial nodes, retaining
every original entry and assertion. The complete promotion/workflow, scheduling,
cold-start selection and three original failure-node replay then passes all 120
cases in 18.97 test seconds with fatal warnings. Ruff and strict typing pass for
both changed test files. Required next proof is the existing 90/60-bounded owner
from the canonical checkout after integration; the failed worktree invocation is
not repeated or relabelled as green.

The long-lived [matrix ordering report][hosted-ordering-report] and
[corrected slot-replenishment report][hosted-slot-report] below still motivate
measuring actual source timestamps. They neither guarantee a particular runner
start order nor prove a current GitHub service defect. This additive early routing
does not measure a hosted speedup. It starts no serving process, listener or
migration, and preserves checkpoint restore authority and the existing ZDD gates.

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

The original hosted scheduling experiment changed only the existing eight-label
matrix creation order. Known successful long lanes were created first; `unit-2`
was explicitly provisional because its failed parent interval was censored, not
a successful full-shard duration. The complete successful interval below now
supersedes that provisional priority. The local runner's canonical shard membership
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

The original matrix followed the seven successful durations in descending order,
then the explicitly provisional failed lane. That ranked known complete intervals;
it did not estimate `unit-2`'s successful runtime or prove the experiment helped.

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

### Complete longest-lane timing supersedes the provisional priority

The existing authenticated timing owner observes Build `38483506178`, exact SHA
`fd5a6d0a46cc4c6991021c9dd226d22e8f7354d1`, with all eight canonical test shards
successful. Its longest lane, `unit-2`, runs for 3,565 seconds after a 3,364-second
delay from the completed gate. It finishes last at `2026-10-14T01:28:11Z`,
6,962 seconds after the gate at `2026-10-13T23:32:09Z`.
[Exact source run](https://github.com/sandboxcom/gludd/actions/runs/38483506178)
and [unit-2 job](https://github.com/sandboxcom/gludd/actions/runs/38483506178/job/115595858058).

The matrix now creates `unit-2` first, preserving the relative order of all seven
other lanes. The three-runner ceiling, exact eight-label membership, canonical
local registry/default order, Python version, isolated suites, fail-fast policy,
warning-fatal arguments, coverage artifacts, timeouts and release dependencies
are unchanged. No scheduler, runner, worker, dependency, daemon or retry is added.
The current hosted run is not cancelled or modified. GitHub's creation-order
semantics and the long-lived ordering/slot-replenishment forum findings above
still apply: earlier creation is not a guaranteed earlier start or measured gain.

```sh
make ci-run-summary RUN=38483506178 CI_RUN_SUMMARY_REPO=sandboxcom/gludd CI_RUN_SUMMARY_VALIDATE_ONLY=0 CI_RUN_SUMMARY_SHARD_TIMINGS=1
```

The bounded native observation is retained in
`.gate-logs/v012-ci-longest-lane-timing.log`. It preserves the run's successful
conclusion and all source timestamps, but its strict job summary returns nonzero
for three skipped tag/provider jobs. That nonzero result is retained, not waived:
the successful shard intervals are diagnostic timing evidence, not release proof.

The existing early-admitted priority assertion fails first before the one-array
change. All 340 workflow, security/privacy, canonical registry, timing and release
dependency cases then pass on Python 3.11 and 3.14 (8.1/13.4 observed seconds),
with fatal warnings. Ruff and strict test typing pass. Invalid initial test-file
selections collected no tests and are retained as failed diagnostic invocations;
only the verified eight-file replay supplies source evidence. The complete native
current-source owner then passes 1,607 cases plus its separate fresh help check in
57.1 observed seconds, retaining 90/60 limits
(`.gate-logs/v012-ci-longest-lane-admission.log`). Publication, exact-SHA hosted
timing, full gate and deployment proof remain required. Rollback restores only
the prior array and its exact-order
assertion; no serving component, image, database or traffic is touched (ZDD).

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
## Superseded successful observation retention

An observed S98.4 repair was refused before launch because 32 historical command
pointers filled the bounded administrative inventory. That lifetime count kept
growing after successful source work, although each native run already owned its
terminal result and receipt. A passing observation is not feature completion.

Before reserving a new command at capacity, the existing producer may release
one pointer only when its native result verifies success, its command hash matches,
its source or runtime context is superseded, and no reservation still owns it.
Current-context successes, failed/timed-out/interrupted runs, active owners, missing
or malformed receipts, forged identities and release-authority claims remain
protected. The same 32-command/128-KiB bounds, primary/support rules and unchanged
failure guard remain. An atomic write failure preserves the original state.

`ITEM_PROGRESS_RETENTION_RELEASED` reports the native administrative change.
Admission deletes no receipt, log, task, lease or process; the existing observer's
retention policy still owns historical run files, and referenced failed evidence
stays protected. This adds no parallel history tracker or completion authority.
The long-lived [stale workflow observation discussion][stale-workflow-observations]
supports retaining exact native identity rather than treating an old status as
current proof; it does not establish the exact Gludd inventory defect.

Two canonical capacity regressions fail first with 11 safety controls passing.
The final 88-case producer/wiring cohort passes on Python 3.11 and 3.14. Native
coverage measures 93.4% lines and 86.1% branches without changing 85/75 floors;
strict typing, Ruff and the unchanged 256-resource ownership inventory pass.
This is a zero-downtime administrative change. Rollback reverts source/acceptance
together after watched owners finish, reintroducing history-capacity refusal;
never clear operational JSON manually. Full gate and deployment proof stay pending.

## One-run diagnostic failure collection

`make ci-shards-diagnostic` exposes the existing serial runner's maintained
collect-all mode; it adds no runner, resolver, cache or release authority.
Set `CI_SHARDS_DIAGNOSTIC_SHARDS` explicitly and
`CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY=1` for a mutation-free plan. Execution requires
an explicit `0`. The documented safe example is:

```sh
make ci-shards-diagnostic CI_SHARDS_DIAGNOSTIC_SHARDS=unit-2 CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY=1
```

The target transports literal values through private
`GLUDD_CI_SHARDS_DIAGNOSTIC_SHARDS` and
`GLUDD_CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY` exports; raw public values are unexported.
It pins Python 3.11, no dependency synchronization, fatal warnings, one batch
worker and 16-file batches. It preserves the native heartbeat, no-progress,
cleanup, cancellation, isolated-test and coverage policy. Ordinary test failures
can accumulate across selected batches; safety and cleanup failures still stop
execution. A failed diagnostic remains failed, and collect-all cannot be combined
with the native required release policy or supply deployment completion proof.

The [2016 long-running pytest discussion](https://stackoverflow.com/questions/36804181/long-running-py-test-stop-at-first-failure)
illustrates the recurring first-failure/all-failures tradeoff. The
[2020 setup-error discussion](https://stackoverflow.com/questions/63248262/stop-pytest-on-errors-but-not-on-fails)
distinguishes ordinary test failures from unsafe initialization errors. Native
execution controls follow [pytest's maintained reference](https://docs.pytest.org/en/latest/reference/reference.html#test-execution-control).
The project reuses its existing orchestrator rather than adopting either thread's
custom hooks or altering release-gate policy.

All 22 canonical entrypoint controls fail first, then the 99-case native
entrypoint/collect-all/safety/dual-track cohort passes on Python 3.11. All 126
related cases, including environment-documentation checks, pass on Python 3.14.
The documented native plan, 231-target contract, strict typing, Ruff and Make
parity pass. Cold imports use test-owned restored Python paths without depending
on the suite's legacy setuptools shim. Rollout adds only a diagnostic interface;
rollback removes it after active diagnostic owners finish. Serving processes,
release producers, bounds and coverage floors remain unchanged. These checks do
not close S98.4 or replace exact-SHA hosted/full-gate/deployment evidence.

## Shared-owner coverage execution closure

Actual Build run 38447639445 fails Azure coverage despite all tests passing.
The expanded runtime-artifact owner is already included in measurement, but its
staging cases are absent from the existing 47-file execution list. The canonical
reviewed-binding replay retains the failure: 69.1% lines/61.9% branches for that
owner and 83.1% aggregate branches. This is not repaired by source inclusion alone.

The same native Azure target appends existing collection-staging, stage-target
and canonical-binding cohorts plus `test_azure_containerapp_coverage_target.py`.
All 47 originals retain their order. Five failing-first native regression cases
check actual recursive Make argv, exact original/new selections, no duplicates,
unchanged policy and child-failure propagation, including caller override attempts.
The existing pre-publication runtime owner also executes these cases; promotion
family `azure-runtime-artifact-coverage-roster` retains full-gate admission rather
than declaring a source test to be release evidence. Exact legacy expectations
add only this required file/family, without removing existing guarantees.

The actual Python 3.11 Azure owner passes all 1407 tests in 41.2 observed seconds.
Native coverage reports 92.8% lines/85.3% branches, with all 70 original production
files above both 75% floors. Runtime artifacts measure 89.9% lines/80.8% branches.
The original profile, 85/75 thresholds, 300-second observer maximum, 60-second
quiet limit and cleanup policy are unchanged. Report:
`.gate-logs/coverage-20261013T185516Z.json`. All 98 related admission/control cases
pass on Python 3.11 and 3.14; strict typing/Ruff, unchanged 256-resource ownership,
Make parity and 231 contracts pass.

The [2021 pytest selection question](https://stackoverflow.com/questions/67078075/select-suite-of-tests-for-coverage-with-pytest)
and [maintained coverage execution reference](https://coverage.readthedocs.io/en/latest/commands/cmd_run.html)
distinguish program/test selection from coverage measurement. Applied inference:
select the missing existing tests in the owner that measures their source, rather
than changing measurement scope to hide the gap. This change creates no service
or alternate coverage engine. Rollout adds the tests to new diagnostic/CI jobs;
existing jobs keep their immutable inputs. Rollback reverts selection and its
guards together after owners finish, knowingly restoring the failed admission.
Canonical full gate, successor exact-SHA CI and installed/deployment proof remain
required; no release task or backlog item is marked complete from this receipt.

### Terminal ownership for an explicitly pinned heartbeat

`STATUS_HEARTBEAT_STOP_ON_TERMINAL=1` lets the existing `status-heartbeat` owner
finish after its current invocation fully observes every workflow for the
explicit `PIPELINE_STATUS_SHA`. The default remains `0`; `COUNT=0` therefore
retains its continuous behavior. The five-minute minimum interval remains.

The existing `pipeline_status.py` producer emits an identity-bound terminal
marker only after publishing its owned snapshot, validating all run details,
and completing the existing ledger/history observation without errors. It
reuses `_terminal_verdict`, the canonical all-workflow outcome rules also used
by `require_ci_green.py`. The heartbeat streams output through its existing
log owner and retains only this invocation's matching marker in one temporary
file under the project namespace. It resets that file before each invocation
and removes it on exit or interruption. An old same-SHA snapshot or marker
cannot stop a failed current observation. No additional GitHub query, watcher,
client, daemon, or polling owner is introduced.

All workflows must be terminal, including optional sibling workflows. Pending,
missing, empty, foreign, malformed, lookup-error, ledger-error, publication-error,
and validate-only observations do not stop the loop. An unpinned invocation
remains continuous. A terminal failure stops with a nonzero result and reports
`outcome=failure release_ready=false`. Success also reports `release_ready=false`;
the snapshot never becomes gate or release authority.

Practitioner evidence illustrates why completion, failure, and missing evidence
remain separate. In [GitHub Community discussion #207496](https://github.com/orgs/community/discussions/207496),
a September 2026 report describes a workflow remaining queued for 22 days with
no jobs and contradictory cancellation responses, linking earlier occurrences
of the same class. These are practitioner observations, not proof about Gludd
or a guaranteed GitHub workaround; elapsed time cannot authorize a terminal
stop. A [July 2021 CLI report](https://github.com/cli/cli/issues/3962) also showed
already-completed failures returning zero from `gh run watch --exit-status`.
That specific defect was fixed that month; it motivates retaining the explicit
failure result in the regression contract. The [current CLI manual](https://cli.github.com/manual/gh_run_watch)
describes a per-run watcher, which does not replace Gludd's required all-workflow,
exact-SHA producer and its ledger observation.

The contract's safe example uses `STATUS_HEARTBEAT_VALIDATE_ONLY=1` together with
the explicit stop option and invokes no owners or file writes. Focused native
Make acceptance exercises the real producer and target with local adapters,
including a live-streaming handshake and stale same-SHA marker regressions.
Those local fixtures are not hosted timing, full-gate, or release evidence.

### Concurrent cleanup of superseded observer records

A normal publication replay passes 1461 cases but fails one native consumer
because parallel observers remove the same superseded status record. The existing
retention owner now tolerates only a record disappearing before read or unlink;
it uses standard `Path.unlink(missing_ok=True)`, not a new cleaner or retry loop.
Current/running records, retention bounds and namespace/identity checks remain.
Permission and other genuine I/O failures still fail the observer closed.

The [2017 CPython practitioner report](https://github.com/python/cpython/issues/73885)
documents the same class of concurrent-removal failure in recursive cleanup;
it is background evidence, not proof of Gludd's specific race. The maintained
[Python 3.11 unlink contract](https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.unlink)
provides the narrow missing-file behavior without suppressing other errors.
Two deterministic race cases fail first while two permission-error controls pass.
All 90 observer/registration cases then pass on both Python versions. The unchanged
four-owner coverage profile passes 182 tests at 92.8% lines/86.0% branches, with
all four owners above both 75% floors; the initial missing-execution receipt is
retained. Audit: `.gate-logs/coverage-20261013T220433Z.json`.
Both full observer cohorts join the existing early owner, and family 68 retains
the complete gate and original 90/60 bounds. No serving service changes; rollback
reverts the native cleanup change and its controls together after owners finish.
Source acceptance is not a successful push, hosted successor or release proof.

### Read-only capacity previews and unchanged-source admission

Research decision for S98.4: permit a fresh capacity observation through the
existing diagnostic admission owner even when source, argv and environment match
a prior failed observation. Disk usage and cleanup eligibility can change outside
those identities. Admit only the single native `disk-check` target or explicit
`disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800`
with the owner's valid grace policy and existing override/argument restrictions.
The preflight's `VALIDATE_ONLY=1` branch runs tests; its omitted/default dry-run
setting selects mutation. Neither qualifies merely by being called a preflight.

The maintained [Terraform plan reference](https://developer.hashicorp.com/terraform/cli/commands/plan)
separates current-state preview from applying changes and requires reviewing the
final plan because external state can change after a speculative preview. A
[December 2021 practitioner report](https://discuss.hashicorp.com/t/terraform-show-and-saved-plan-is-stale-while-apply-is-running/33098)
describes stale-plan refusal while trying to inspect a plan during and after an
apply. That version-specific report establishes a historical observation problem,
not a current Terraform defect or proof about Gludd. Applied inference: keep fresh
inspection available while preserving refusal of unsupported mutation retries.

This exception refreshes diagnostics only. Preserve prior failure receipts,
nonzero unhealthy/error results, the canonical 90% volume and 100 MB scratch
limits, ownership checks and observer bounds. It cannot authorize deletion,
candidate sealing, repeated final proof, task closure or release promotion.
A fresh passing scheduling check still supplies no gate or release evidence;
expensive attempts require the actual blocker to clear and fresh owned admission.

ZDD rollout changes only control-plane diagnostic routing through the existing
owner; serving processes continue without a restart or replacement watcher.
Rollback reverts that routing and its controls together after active diagnostic
owners finish, retaining observations and knowingly restoring the preview refusal.
Required failing-first routing controls, canonical full gate, 85/75 coverage,
independent review and exact-SHA hosted/deployment proof remain separate work.

The implementation reproduces seven failures first and adds 27 routing controls
without changing original assertions. All 128 policy/retention/wiring/stop cases
pass with fatal warnings on Python 3.11 and 3.14. The native coverage profile
measures 93.77% lines/87.29% branches under unchanged 85/75 floors and four
existing typing exclusions (`.gate-logs/coverage-20261014T145435Z.json`). The
initial narrower execution fails coverage; adding the existing retention cohort
closes that execution gap without changing measurement or thresholds.

The repaired native watched preview executes and still reports failure at 92%
volume use and 836 MB scratch. Its four proposed removals are dry-run only;
no retained or unknown data is deleted. This verifies diagnostic routing, not
release admission, a successful full gate or deployment-ready completion.

The unchanged scanner flags the overlong grace fixture's public argument as
Base64 entropy during publication. The canonical manager adds one identity and
removes none; review labels only hash `025e5b8af0f109ad6158df7675c43319861ae61c`
in the existing capacity test as non-credential data. A failing-first baseline
control derives the argument from the unchanged AST and pins its exact reviewed
identity; all 47 baseline cases and actual six-file read-only admission pass.
Existing finding identities/reviews, plugins, policy and thresholds remain;
native disposable filename and line metadata refresh normally. No source rewrite
to evade detection, pragma, exclusion or scanner bypass is used.

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
