## Key Make Targets

Run `make help` for the current categorized target list. This is the short
operating path; [the executable contract](../../config/make_target_contract.json)
owns target variables and safe examples. Use only Make commands, including
reads, edits, research helpers, tests and Git operations. Add and test a missing
target before using it. Follow the release rules in the root AGENTS.md.
The current catalog omits seven historical fixture-only echo targets, including
a misleading secret-scanner label. The [owning catalog record](../../docs/features/RUNTIME_ADMISSION_THROUGHPUT.md#accurate-project-target-catalog-2026-10-21-utc)
retains native checks and footer conformance. Static discovery and dry-run
checks do not replace the documented actual behavior command.

### Establish current state before changing it

Read current tracking views together before work:

~~~console
make show-lines FILE=TASKS.md START=1 END=9
make show-lines FILE=BUGS.md START=1 END=5
make cat-file FILE=config/ratchet.yml
make cat-file FILE=SESSION.md
~~~

TASKS and BUGS keep compact current pointers above their full retained history.
A seven-line active-task search can still truncate because each line contains a
large evidence history. These windows establish navigation, not a changed task
definition or proof. Read the selected task's full criteria and owning feature
before implementation/closure; complete native guards still evaluate the ledger.
Use the existing work-item status with explicit WORK_ITEM_ACTION=status,
WORK_ITEM_ID=S98.4, WORK_ITEM_REASON='' and WORK_ITEM_VALIDATE_ONLY=0 after
selecting the installed runtime below. It reports observations, not acceptance.
Use make active-work-status ACTIVE_WORK_STATUS_PID= for current scope/ownership.
Use make worktree-state for the current branch, SHA and staged/unstaged paths;
make all-worktree-state includes other checkouts.
Use `make active-work-status`, `make ps` and `make disk` to identify existing
owners, subprocesses, worker capacity and disk use. `make ps-gludd` covers
namespaced daemons only. Model-agent status is separate from process PIDs.
If an existing owner names a blocking OS process, inspect that exact positive PID
with make active-work-status ACTIVE_WORK_STATUS_PID=12345 (substitute its measured
PID). This bounded read-only diagnostic grants no gate or termination authority;
CWD evidence alone does not establish project ownership. Empty
ACTIVE_WORK_STATUS_PID retains the ordinary status schema.

Preserve existing staged work and immutable failure evidence. A live gate owns
its frozen candidate: do not edit that checkout, change its index or move its
HEAD during proof. Use an isolated development-based worktree for disjoint
repairs; record branch, base SHA, task and file ownership before editing.
Unknown ownership needs investigation through existing owners, not another gate.
For a legacy watchdog holding the UV cache, use the verified watchdog-stop plan
with WATCHDOG_VALIDATE_ONLY=1, then its actual mode0 only after verification.
Immediately restore both guards with watchdog-auto WATCHDOG_AUTO_VALIDATE_ONLY=0.
The same startup owner plans with WATCHDOG_START_VALIDATE_ONLY=1; auto plans both
with WATCHDOG_AUTO_VALIDATE_ONLY=1. Refusal preserves evidence and blocks the
handoff; PID diagnostics alone never authorize termination.

### Choose the next action from evidence

| Current input | Next action |
|---|---|
| Capacity, runtime, configuration or ownership prerequisite fails | Repair the named input and run its focused acceptance. |
| Focused tests pass | Run each complete changed test owner, lint and strict typing; collect before committing. |
| A Make recipe or variable changes | Run `make check-make-target-contract` and the contract's behavioral example. |
| Source is reviewed and prerequisites pass | Freeze one candidate for combined acceptance, full gate and exact-SHA hosted CI. |
| Local or hosted proof fails | Preserve its receipt and log; change the actual failing input before retrying. |
| Gate and required exact-SHA proofs pass | Record task evidence and use the guarded promotion owner from the canonical main checkout. |

Source review, focused acceptance, collection, plan mode and scheduling admission
have distinct meanings. A plan reports `gate_evidence=false`; it never supplies
a full gate, CI result, deployment proof or permission to close a task.

### Run focused checks with explicit inputs

Select the installed registered runtime before the outer Make invocation,
including Git targets with Python prerequisites. For example, git-checkout calls
_gate-mutation-guard, which invokes UV. Omitting runtime selection on the2e9
repair checkout made UV announce CPython3.14 and creation of .venv; no dependency
installation was requested. Align UV_PROJECT_ENVIRONMENT and VIRTUAL_ENV to the
registered311 toolchain, with UV_NO_SYNC=1, UV_PYTHON=3.11 and
UV_PYTHON_DOWNLOADS=never, for this release's Git/helper and child operations.
The current SESSION identifies that installed environment; reuse it before
running a command rather than allowing an implicit environment acquisition.
SESSION's existing parser expects the local Last Updated date and a leading
numeric Total tests: N field, including the colon. Keep UTC measurement dates and source provenance in
the accompanying prose; the complete persistence owner exercises those fields.
Keep the native gate marker pair during handoff compaction, then run
make status-snapshot STATUS_SNAPSHOT_VALIDATE_ONLY=0 to regenerate from the actual
retained .gate-status. Keep source/profile/age interpretation outside that region;
a copied old FAIL is not candidate proof. Put historical age/profile explanation
BEFORE the native Current Gate Status heading. Text between that heading and its
marker makes the native renderer insert another immediate heading; preserve the
native heading/markers/evidence and verify repeated regeneration. Its documented mode1 validates existing
structure without writing. Include the complete status-snapshot and session-
persistence test owners. Missing/duplicate/reversed or inline markers must fail validation,
and repeated native writes must preserve one header and surrounding handoff.

This is a safe contract example, not the complete acceptance scope for a change:

```bash
make test-files TESTFILES=tests/unit/test_make_target_contract.py PYTEST_ARGS='-q -W error -n 0' UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
```

Select the installed environment belonging to the active checkout or its
registered shared toolchain. An isolated worktree must not point at the canonical
main checkout's .venv: its subprocess-confinement guard rejects that reference.
Keep warnings fatal and preserve ordinary plugins and original assertions.
For promoted selectors, check-gate-failure-promotions executes the manifest and
Make wiring pins plus both exact roster pins. Its documented native example
exposes a stale shared roster before the bounded runtime replay. The complete
runtime-admission-scheduling and cold-start-selection owners also retain mutation
checks for omitted, duplicated and widened selectors.
When pytest parametrization changes, include the complete existing
tests/unit/test_pytest_param_fixture_collisions.py owner with the changed
test files. Names such as context are external Playwright fixtures; use a
specific parameter name such as import_context while keeping case values and
assertions. The existing repository guard remains part of native admission;
focused behavior alone does not establish fixture-namespace conformance.
`make lint-files FILES='path1.py path2.py'` and
`make typecheck-scope FILES='path1.py path2.py'` provide focused checks.
Run them as separate Make invocations; the FILES/stray-goal guard rejects mixing
path-carrying targets. Keep that refusal and use each documented native owner.
For confined static checks, preserve the original package initializer and config.
A flat export can change import classification; receiving checks run in the actual
package layout. Keep original imports and lint rules while repairing fixture input.
The commit docstring guard scopes src and can report SKIP for script-only changes.
Native make lint-docstrings DOCSTRING_FILES='scripts/changed_owner.py' checks that
script explicitly; a commit-guard SKIP is distinct from script docstring acceptance.
A yielded session ID means a process is still active. Drain each pair to a native
terminal result before launching replacements; awaiting launch responses alone
does not enforce the two-process testing bound. Serialize native watchdog lifecycle
cohorts after a live/unlocated refusal and retain its actual PID/CWD diagnostic.
The initial cause may remain unproven; never relax the ownership fence.
`make lint`, `make collect-check` and `make test-count` remain required
by the commit/completion policy. A focused pass is not a green full gate.
The operating guardrail owner itself invokes full-suite collection and Ansible
checks. Use visible case output and that diagnostic watcher's documented limits;
the general run-watched contract uses STALL_SECS=180. A retained quiet timeout
without terminal pytest evidence is not an assertion failure. Keep the ORIGINAL
runtime admission's 90total/60quiet limits separate and unchanged; the
[job-progress record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#observed-job-progress-beside-workflow-status-2026-10-21)
retains the actual configuration refusal and complete receiving proof.
The routed docs/specs/BEHAVIORAL_SPECS.md corpus is pinned historical source.
Keep its index/shard bytes, SHA and original line-count assertions intact; current
operational changes belong in the owning feature/AGENTS/this guide. Include the
complete tests/unit/test_behavioral_spec_shards.py owner when this boundary is touched.

### Attribute a runtime timeout before changing source

Read the retained native failure and preserve its exact failing input. A
test-files-cprofile controller profile can mostly measure waiting for xdist
workers; cumulative rows overlap. Use the existing test-xdist-trace-summary
with LOG, RUN_ID and TRACE_SUMMARY_TIMINGS=1 to rank already recorded protocol
spans by exact run/worker/PID. It rejects ambiguous intervals and preserves
failure evidence. Wall spans include fixtures and child waits; worker totals
overlap and supply no CPU, gate, CI-repair or release verdict. Mode0 keeps
compact output. Read the [measured boundary and commands](../../docs/features/XDIST_TRACE_SUMMARY.md#validated-test-protocol-wall-spans-v012)
before choosing a production repair. Do not widen deadlines, alter selectors,
cache test fixtures or add another observer to obtain a pass.
The [fixture-audit record](../../docs/features/PYTEST_PARAMETER_FIXTURE_COLLISION_GUARD.md#bind-native-declaration-types-once-per-traversal-2026-10-21-utc)
retains a measured per-traversal lookup repair, original field/owner/freshness
controls, whole-file coverage and separate canonical strict source/test scopes.
Its deterministic operation bound and local profile supply different evidence
from the enclosing original runtime-family receipt.

The [published-candidate decision](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#published-candidate-runtime-failure-and-bounded-repair-2026-10-21)
retains local passes followed by hosted90-second failures. Attribute the actual
failed owner, not the last visible test. Compare paired differences rather than
differences of group medians; reject unrelated micro-optimizations explicitly.
Real UV supervises its Python child. A fake exec launcher, runpy or the bootstrap
PYTHON alias does not prove that lifecycle; preserve native runtime selection,
fresh observations and every original assertion while assessing a fixture repair.
The [native duration contract](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#bounded-native-admission-phase-timings-2026-10-21)
uses pytest's bounded20-row setup/call/teardown report in the existing main
child. It does not measure collection/startup or child CPU. Independently
specified Make/native argv expectations retain every argument; their two shared
constants and both original guard cases now enter the early six-pin checker.
Preserve the prior four pins, failure propagation and original runtime bounds.

### Preserve producer status and measurement meaning

Use the native producer's return status before consuming its output. A formatter
can succeed after its producer fails. The existing sorted listing owner now
preserves that failure; its documented actual example is:

~~~console
make list-files DIR=.agents/policy
~~~

The [listing record](../../docs/features/NATIVE_FILE_LISTING_STATUS.md) retains
the missing-path negative control, original filters and exact temporary ownership.
A lock's observed absence permits normal guard re-evaluation; it does not prove
the original cause or authorize raw lock deletion or process termination.

For cold imports, reuse the existing CPython row decoder and check the child
return code before parsing. Missing, malformed or nonfinite measurements must
retain failure, not become zero or a skip. The
[measurement record](../../docs/features/IMPORT_TIME_MEASUREMENT_INTEGRITY.md)
retains the native format, all original thresholds, complete owner commands and
the TEN-owner coverage-files contract example. Select the entire documented
scope: the retained nine-owner attempt failed a real per-file branch floor.
The [canonical export record](../../docs/features/CODE_INTELLIGENCE_IMPORT_BOUNDARY.md)
covers reload, actual class identity, failure/retry and concurrent import controls.
Paired local timings, operation counts and focused coverage do not establish the
original runtime-family receipt, hosted throughput or release readiness.

Make command-line variables are exported to native child fixtures. Clearing
MAKEFLAGS alone does not clear an inherited LOG. A fixture that owns a different
log must pass that owned path explicitly to its existing native command; retain
the namespace/ownership refusal. Use approved /tmp/gludd-* names for temporary
transport artifacts, and resolve a refused mutation before dependent verification.

### Cleanup, pinned browser prefetch and acceptance

The native pressure policy may reclaim the exact idle Playwright cache after
repeat process and identity checks. Read its owning resource feature before
classifying that expected reclamation as data loss. Run cache-reclaiming preflight
before pinned browser prefetch and acceptance. Use the same selected installed CI
runtime across an outer watched owner and its installer child; an inherited
different VIRTUAL_ENV produces a UV warning.

The read-only installer check uses the current target contract:

```bash
make presentation-browser-install PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=1 PRESENTATION_BROWSER_ENGINES='chromium webkit' PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser PRESENTATION_BROWSER_INSTALL_TIMEOUT=600
```

If the cache was reclaimed, the existing actual installer mode0 restores pinned
binaries before browser acceptance. Keep installation visible through run-watched.
Follow the exact retained recipe in the browser/resource feature and select the
registered CI runtime first. Browser restoration is not capacity clearance.
A final admission still over95% remains blocked; do not cycle cleanup/install or
repeat unchanged final proof. No cleanup, ZDD, retention or coverage guard changes.

A changed presentation controller needs the complete original JS0 acceptance
for Chromium and WebKit, run serially, followed by the separate actual Chromium
JS1 measurement. Keep both coverage flags explicit, the source frozen/clean,
the original5s/600s bounds and whole-controller85/75 line AND branch checks.
Extracted-function tests and diagnostic profiles supply different evidence.
Use the [existing browser commands and provenance record](../../docs/features/PRESENTATION_JAVASCRIPT_COVERAGE.md#codified-commands).
The pinned restoration recipe explicitly aligns VIRTUAL_ENV with the selected
UV_PROJECT_ENVIRONMENT in both outer and child owners; use a fresh run identity
only when restoration is actually needed.

A cancelled hosted browser job needs its actual check-run annotation and raw
context before a budget change. The existing ci-checkrun-anno helper can expose
annotations, but its legacy retrieval exit0 grants no CI verdict. The
[Pages feature record](../../docs/features/PRESENTATION_JAVASCRIPT_COVERAGE.md#hosted-job-budget-and-required-ordering-2026-10-21-utc)
records exact commands, the verified20-minute cancellation and dependency graph.
The original JS0 matrix precedes separate ChromiumJS1; deployment requires both.
Keep child deadlines distinct from the enclosing job budget.

### Admit and prove a release candidate

First inspect the existing admission plan:

```bash
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=1 WORK_ITEM_ID='' OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=20
```

When the identified prerequisites have changed or are clear, use the real owner
for the active release task. Do not start a second proof while another owner
holds the candidate or resources:

```bash
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0 WORK_ITEM_ID=S98.4 OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=20
make gate-background-observed GATE_TIMEOUT=7200 GATE_POLL_INTERVAL=60 GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY=0 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 WORK_ITEM_ID=S98.4
```

The observed owner runs the canonical make gate policy and records the outer
lifecycle required by native receipt finalization. Run it once; a second gate
cannot manufacture missing evidence. Use the installed canonical release runtime
and the complete gate. Preserve
85% aggregate and 75% per-file line and branch coverage, fatal warnings, ZDD,
cleanup and exact-SHA promotion requirements. The native gate owns .gate-status
and its authenticated evidence. Its canonical release path already requires a
fresh all-shard Python3.11 attestation bound to the exact candidate SHA; keep
that native producer rather than adding a parallel runtime/gate wrapper. Resume only through the existing authenticated
resume owner after it verifies current source, index and policy; do not relabel
historical green output as current proof.

`make gate-tail GATE_TAIL_LINES=80` shows a bounded log snapshot.
`make gate-progress GATE_PROGRESS_MAX_HISTORICAL=10` explains phase progress.
Use existing pipeline-status/ci-await owners for generated current CI evidence;
select their full variables from the contract. Do not manually edit observation
JSON or repeatedly poll status in place of repairing a known failure.
Workflow-level queued can coexist with active jobs. Read the observed job-state
counts and bounded active/failed step rows from the existing pipeline-status
snapshot. Success on a step does not establish job or workflow success; complete
native JSON retains omitted display rows. This projection is diagnostic, with
unchanged terminal exact-SHA and failure/publication fences. The
[job-progress record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#observed-job-progress-beside-workflow-status-2026-10-21)
retains the repeated interpretation error and executable controls.

For an explicitly requested emergency cancellation, use the existing single-run
owner. Its safe actual example is make ci-cancel RUN=1 CI_CANCEL_VALIDATE_ONLY=1;
mode1 is the offline default. An authorized mode0 request sends one fixed-repo
native command with a positive RUN and preserves its failure status/stderr.
Request acceptance is separate from terminal cancellation; observe through the
existing pipeline owner. The queued-run bulk target is retired. Raw-value capture
and public unexport precede startup shell expansion; unrelated goals retain their
inputs. The [cancellation record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#explicit-cancellation-with-literal-inputs-2026-10-21)
retains whole-owner/native-bootstrap controls and Make-version research.

The immutable ci-run-summary owner with CI_RUN_SUMMARY_SHARD_TIMINGS=1 retains the exact run/SHA, all eight shard source job IDs and pending/censored intervals. Its nonterminal diagnostic can supply the numeric job binding for failure context; final timing comparisons and proof still require terminal evidence. No search or ad hoc parser over a current JSON snapshot replaces the native authority.

For production-binding mutation probes, the existing fixture extracts exactly one real declaration before fixture defaults or a child. Missing/duplicate declarations refuse; valid-one positives and the original opposite-binding native-Make controls remain. Existing text-forwarding source pins follow the complete raw-value/export/unexport/quoted-argv chain and retain atomic copy/move intent. The [hosted assertion record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#published-unit-3a-assertion-repair-2026-10-21) binds the actual failures, original cases and whole-owner acceptance.

For a completed job whose sibling is still running, use ci-job-failure-context
with its exact numeric run/job binding and a raw assertion pattern. Its contract
example sets RUN=1 JOB=1 PATTERN=FAILED BEFORE=2 AFTER=4 MAX_MATCHES=5
CI_JOB_CONTEXT_VALIDATE_ONLY=1; actual mode0 fetches the authenticated job log.
PATTERN matches raw text, which can omit workflow display-step labels. The
legacy400-line ci-job-log shortcut now preserves native retrieval errors; its
safe offline example is make ci-job-log RUN=1 JOB=gate
CI_JOB_LOG_VALIDATE_ONLY=1. Retrieval success grants no CI or release proof.

A development repair push has its own native CI failure guard. Hosted and
publication admission first verifies the exact pinned EE manifest through
check-ansible-base-image mode0, then runs check-gate-failure-promotions with the
literal canonical manifest through a separate90/60 watched stage, then the
unchanged original90/60 runtime replay. The hosted prerequisite label is
ci-gate-failure-promotions; publication uses batch-push-prerequisite-admission.
Each owns its failure log and heartbeat. A failed image or canonical contract
check starts neither runtime replay nor transport; no silent refresh follows.
The [prerequisite parity record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#run-canonical-prerequisites-before-runtime-proof-2026-10-21)
retains routing controls and distinguishes recorder tests from actual checks.
The owner's mode1 plan names both budgets but stays offline and creates no runtime.
The owner aligns the selected canonical UV/VIRTUAL_ENV inputs in both child
contexts, preserving caller overrides outside that admission. See
[the served-image recovery](../../docs/features/ANSIBLE_EE_BASE_IMAGE_LIVENESS.md)
for the native atomic refresh and independent exact-digest check. Even when disk
blocks final release proof, inspect actual open families with ci-failure-status,
then run their first evidence through ci-failure-repair in the exact clean
descendant checkout. Select explicit full families and evidence matching their
actual failures. For a workflow's internal target, use public run-watched as the
evidence target and its unchanged Make command as the quoted CMD evidence
variable; preserve original timeout, worker and runtime policy. Read
[the native repair workflow](../../docs/features/CI_FAILURE_LEDGER.md#repair-workflow)
for every required argument. The existing pipeline-status owner now reuses the
public ledger CLI in the same interpreter while retaining both fresh queries.
[Its runtime boundary record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md)
retains whole-source coverage, cleanup120 versus reporting-only1, final-message
precedence, prior-success invalidation and both negative source decisions.
The documented validate-only behavior remains offline planning; its fabricated
PASS rows grant no native repair, CI, gate or release authority. A local repair receipt can permit a development
push; it does not supply hosted success, deployment acceptance or release proof.

Passing component tests or SOURCE ONLY review cannot clear an outer runtime
timeout: retain the failed watcher receipt and require an actual changed input
before its next original-budget replay. Never infer worker timing from the last
visible node or a null trace_path. The existing native observer owns the terminal
reason and timing; no second observer/cache is needed.

Before a repair push, use the existing pipeline-status owner for the exact
previous published SHA and inspect its terminal-record marker. A recorded failed
verdict satisfies the history-observation latch without supplying green CI.
If that marker is present, do not repeat ci-verdict-safe or edit history by hand;
otherwise follow the guard's exact previous-SHA recovery. Terminal job enrichment
can reopen a family with a changed fingerprint: obtain fresh evidence through
ci-failure-repair for that actual input before guard reevaluation. The
[publication record](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#reviewed-source-blocked-at-publication-deadline-2026-10-21)
retains this sequence, both local original passes and the later failed90/60
publication. Its faster main-only trace is diagnostic, with all selectors,
worker identities and proof limits retained; it does not clear publication.

An admitted new repair attempt must invalidate older current repair authority
before its evidence starts and retain old success as history. A running, failed,
timed-out, interrupted or failed-postcheck attempt cannot inherit an ancestral
receipt to permit publication. Invalid/plan-only requests remain nonmutating;
only successful exact clean-source evidence and the native final concurrency
fence can restore authority. Use the same ledger owner, not manual JSON edits.
The [attempt incident](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#failed-new-repair-must-invalidate-older-authority-2026-10-21)
retains the real zero-open/stale-receipt contradiction and proof limits.

Keep batch-push's default COMMIT_THRESHOLD=5. For a coherent genuine CI repair
batch below five, release rule6 permits the existing ordinary git-push-sandboxcom
owner with all native guards and normal Git hooks. First obtain the actual
repair-owned receiving receipts, verify-state and select the documented ledger,
full SHA, branch, selected runtime and release-policy variables. A threshold
nonpublication is not a transport result; do not change the threshold, manufacture
commits, force or skip hooks. verify-remote and exact-SHA native hosted observations
remain separate. [The receiving decision](../../docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#original-receiving-deadline-still-fails-on312-2026-10-21)
retains the failed prerequisite and the source-only publication-route decision.

The edd9 receiving attempt instrumented all35 complete repair owners and timed
out at240.6s during the final worktree owner; it recorded no repair receipt.
For its successor, retain all35 owners in test-files under run-watched with the
same240-second bound, and run the13 complete readiness owners through
coverage-files/config/coverage_v012_readiness.ini separately with a90-second
bound. Bind both to the same clean immutable SHA and retain their distinct logs.
This changes the instrumented workload while keeping every acceptance owner,
all five complete source files and both85/75 coverage metrics. The
[receiving record](../../docs/features/INTEGRATION_ADMISSION.md#bounded-receiving-proof-after-instrumented-timeout-2026-10-21-utc)
owns exact scope and results. Use public run-watched as the native repair
evidence target for the complete35-owner command; do not record a receipt from
collection, a plan or an incomplete timed-out coverage run. Preserve each
terminal result before the next lifecycle cohort. Final gate/global coverage
and exact-SHA release proof remain separate.

### Commit, integrate and promote through their owners

After integration, accept the complete changed owners and registered whole-file
coverage in the receiving checkout. Source-branch receipts do not prove that an
overlapping merge retained every test. Compare the tested immutable files through
make git-show-file-to before repairing an overlap; preserve original cases and
assertions. Keep diagnostic pytest flags in the selected owner rather than an
ambient PYTEST_ADDOPTS inherited by deliberately plugin-isolated fixture children.
The coverage-files owner is already serial. A refused edit must be repaired before
starting its dependent verification; native nonzero status remains authoritative.

When a script is also imported as scripts.<name>, use separate typecheck-scope
invocations for its source and its complete test owner. A combined scope can
refuse the same file under flat/package module names before checking. The
[native inventory decision](../../docs/features/NATIVE_REVIEWED_HEAD_INPUTS.md#fresh-typed-worktree-inventory-2026-10-21)
retains the actual refusal and strict individual PASS receipts. Select the same
installed runtime for both; no package-layout or suppression change is needed.

`make agent-worktree-dev BRANCH=feature-name` creates an isolated branch from
development. For a bounded source repair, use
`make ship-commit-files FILES='explicit paths' MSG='one tested change' PUSH=0`;
its native content, index-scope and fresh collection guards still apply.
Serialize the commit and other checks in that checkout: pre-commit can temporarily
stash unstaged files. Wait for its terminal result before inspecting the same
checkout, or use an isolated checkout. The source owner already streams observed
collection; an outer run-watched CMD must not carry another checkout's paths
into its test environment.
`make git-commit MSG='one tested change'` additionally requires a fresh green
gate. Keep each small tested change in its own commit.
Before a push, `make verify-state` must identify the intended current branch.

From a clean development checkout, plan integration of the reviewed source:

```bash
make development-merge-forward SOURCE=HEAD MODE=content APPLY=0
```

Replace SOURCE with the reviewed branch or exact ref before applying. Inspect
overlapping files: content mode retains development's version on overlapping
content and collection alone cannot prove semantic integration. Ancestry-only
mode is exceptional and needs a documented decision that development already
contains or intentionally supersedes the source deliverable. Preserve failed
proofs; source integration does not close release tasks.

Master receives development through the release owner, after local and hosted
exact-SHA proof, task evidence and the reviewed-head integration receipt.
The following validates policy/topology only and does not publish:

```bash
make release-promote TAG=v0.1.2 MSG=release-notes RELEASE_PROMOTE_VALIDATE_ONLY=1 RELEASE_PROMOTE_PUBLISH=0 REVIEWED_HEAD_INTEGRATION_RECEIPT=
```

Real promotion runs only from the canonical main checkout with the genuine
receipt and validated candidate. Follow
[Release Proof Throughput](../../docs/features/RELEASE_PROOF_THROUGHPUT.md) for
missing-proof records and
[the release runbook](../../docs/RELEASE_RUNBOOK.md) for artifact/publication proof.

### Human input and external blockers

Use the existing HumanTodo module through the supported Make owner:

```console
make human-todo-module HUMAN_TODO_ARGS_JSON='{"title":"Make HumanTodo contract","body":"Validation only","agent_id":"gludd-release-staging","timeout":5}' HUMAN_TODO_VALIDATE_ONLY=1
```

Supply the real title/body, category, priority and known parent_agent_todo_id
through the module's documented JSON arguments. Literal policy 0 sends the
request; native API errors remain failures. Check mode's changed=true/id=null
is validation, never a filed HumanTodo. Preserve the actual request ID when
filing succeeds; do not invent one if the daemon is unavailable. Keep credentials
out of request prose and shell arguments; use existing module authentication.
This entrypoint grants no additional permission or release authority.

### Read native Git diagnostic status correctly

~~~console
make git-is-ancestor A=HEAD B=HEAD
~~~

Both literal commit inputs are required. Git0 means ancestry, including equal
commits;1 means non-ancestry; other nonzero means an error. Make reports2 for a
failed recipe and preserves the native exit=N/ErrorN diagnostics. The [owning
native-input record](../../docs/features/NATIVE_REVIEWED_HEAD_INPUTS.md#read-only-ancestry-status-and-literal-inputs-2026-10-20)
retains failing-first controls, safe argument transport and full-source proof.
Keep patch equivalence, actual ancestry, cleanup eligibility and authenticated
release proof distinct. Follow the existing release/promotion owners above.

Legacy wt-reap and direct force/unlock removal aliases are retired; historical
presence audits do not authorize their use. The existing cleanup owners below
retain native ownership, integration, locks, ignored proof and rollback.
Make -n/-q can execute an entire recursive recipe line; they are not a mutation
barrier. Use the owner's explicit validation mode and retained native tests.

### Other supported work

Use `make search PATTERN=regex SEARCH_PATH=directory-or-file` for bounded
repository searches and `make show-lines FILE=path START=1 END=80` for slices.
Use `make write-text-b64 FILE=path TEXT_B64=encoded` or
`make replace-text-b64 FILE=path OLD_B64=encoded NEW_B64=encoded` for literal edits.
Use standard base64 containing valid UTF-8 without NUL; both inputs to a
replacement are validated before the destination is opened. Invalid transport
leaves existing bytes untouched. Make requires nonempty encoded fields and
replacement requires exactly one match. The existing native text owner preserves
write bytes and replacement newline behavior; it creates no temporary decoder
files. For a line range, use make replace-lines FILE=path START=1 END=1
NEW_FILE=path-to-replacement; the existing wrapper preserves mode and reports
native failures before publishing. Do not place shell interpolation in Make arguments.

Setup through `make init`, dependency synchronization, packaging, Ansible,
Terraform, secrets,
resource cleanup and recovery each have dedicated owners in `make help`.
Read their contract before execution. Cleanup must retain live owners and failed
proof evidence. clean-stale-worktrees now plans through the native prune owner
with CLEAN_STALE_WORKTREES_VALIDATE_ONLY=1. agent-cleanup requires explicit BRANCH
and canonical WORKTREE and plans native merged-owner retirement with
AGENT_CLEANUP_VALIDATE_ONLY=1. Both retain active streams, locks, checkpoints and
branch refs. The alias and parsed native request must bind the same literal
plan/apply mode; valid plan1 and apply0 need real registered-owner controls,
alongside both mismatched-mode refusals. An absent-path refusal or recorder-only
argv check does not establish that a valid plan works.

Resolve the exact branch, clean HEAD and canonical absolute path through
worktree-state/all-worktree-state. macOS may register /private/tmp when the
supplied /tmp path resolves there; use the native identity. Confirm genuine
integration, completed logical/model production and absence of live users before
releasing only that completed source lease through the existing owner. Root's
milestone, publication and exact-SHA release obligations remain recorded
separately; the unchanged native cleanup owner supplies checkout eligibility:

~~~text
make workstream-unregister BRANCH=actual-completed-branch ACTIVE_WORKSTREAM_REGISTRY=''
~~~

Replace the branch with its measured completed identity; an empty registry input
selects the existing native default. Never fabricate completion or discard a Git lock.

Run selected-owner plan1 with that same BRANCH, WORKTREE and existing registry.
It must preserve the target, ignored evidence, refs and registry. Literal0 applies
only after the same native owner validates eligibility; actual removal retains
archival, fresh ownership fences and rollback. Measure disk afterward: a plan,
removal or new HEAD does not establish capacity clearance. Preserve dirty,
unknown and checkpoint-owned work. The [owning resource record](../../docs/features/GATE_RESOURCE_LIFECYCLE.md#native-alias-plan-and-apply-must-bind-the-same-request-2026-10-20)
retains actual failures and complete-source proof. A target name, dry run or old
threshold in prose is not evidence that current policy admits an action.
The unused wt-prune-force-merged entrypoint has been removed after real native
fixtures reproduced ignored-proof loss. Its historical existence audit grants
no current authority; use the same supported plan/retirement owners above.
