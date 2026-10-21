# Gate Resource Lifecycle Contract

## Status

S83.112 makes three gate-adjacent lifecycle boundaries explicit: read-only Make
diagnostics do not provision the project environment, cleanup preserves tracked
release inputs, and adaptive pytest termination cannot be mistaken for an
out-of-memory retry.

## Problem

A resource snapshot ran through `uv run` after cache reclamation. Because uv
manages a project environment, that lightweight read created and populated a
roughly 500 MB `.venv`. At the same time, `make clean` removed the entire
half-tracked `dist/` tree, including release templates required by fresh clones.
Finally, the adaptive test wrapper treated every `SIGKILL`-shaped child exit as
OOM-shaped; an orchestrator stop could therefore restart the complete shard at a
lower worker count instead of staying stopped.

## Contract

### Measured cleanup and browser prefetch order (2026-10-20)

After completed source proof and retired peer testing, native preflight removes
6027eligible bytecode files/240549715bytes, three disposable caches, the exact
idle Playwright root7834entries and11UV files/12.2KiB. Six removals/0errors on the
first pass become0progress on the second; native refusal remains96% against95%.
All live owners, unknown data, branch/checkpoint locks and failed proofs survive.

This matches the pressure-only Playwright lifecycle below. Root initially calls
the browser eviction a retention defect before consulting that policy, then
corrects the interpretation. No source guard is removed or weakened. Existing
pinned presentation-browser-install restores Chromium1243 and WebKit2359 before
acceptance, observed36.0s/rc0. An initial cross-runtime UV warning is removed by
aligning the selected installed runtime across observer and installer; the aligned
312 check then reports both actual executables available/rc0 without that warning.
The corrected aligned312 actual installer replay also passes both engines in
observed1.7s/rc0 with no warning or new download; the browser feature retains its
complete existing Make recipe and exact native log.

Fresh browser/c8 proof then passes at clean5b77f02; its owning feature retains
the exact source, native94.2line/88.3branch and terminal390.5s receipt. After it,
disk measures97%,7.2GiB available. This does not clear final admission. Preserve
the failed cleanup and avoid cycling cache eviction/restoration at unchanged
capacity. Full release proof still requires a genuine changed capacity input.

### Native cache-lock owner discovery (2026-10-20)

Actual disk-cleanup-preflight with validate-only0/dry-run0/grace1800 removes16
eligible entries, including regenerable worktree caches/bytecode, with0 errors.
A no-progress pass removes0; native refusal remains rc1 at96% against95%.
Stale namespace planning at unchanged24-hour grace finds39 candidates and0
reclaimable. Preserve live namespaces, hardlink safety, checkpoints and evidence.

The existing native status producer now accepts an optional positive PID through
make active-work-status ACTIVE_WORK_STATUS_PID=68501. One fresh bounded native
row identifies uv run python3 scripts/agent_watchdog.py, PPID1, repository CWD.
CWD is separate evidence: ownership remains unverified and no kill/gate authority
is granted. Default status/table behavior remains unchanged; malformed or
unavailable inspection never reuses prior success. Native whole-file94-case
coverage clears85/75 at94.8% line/91.2% branch. Literal Make transport and the
unchanged environment audit pass45 cases after correcting the actual export form.

The [original upstream report16112](https://github.com/astral-sh/uv/issues/16112)
reproduces cleanup waiting for a live uv run child.
[Upstream cache safety](https://docs.astral.sh/uv/concepts/cache/) preserves the
lock for active work. Repair the actual long-lived watchdog launcher through
its existing acknowledged lifecycle and namespaced native lease. Keep direct
installed-Python startup, actual PID/readiness acknowledgment, verified child
drain and natural supervisor exit; do not force cache access or signal from a
diagnostic alone. The subsequent verified handoff is recorded below; the hard disk prerequisite
remains pending.

### Acknowledged agent-watchdog handoff (2026-10-20)

Source 1c2918edb189d672f2ee6f72ec0a1b62619764f2 is integrated as0e71b1ae5.
The shared task-watchdog owner now accepts the agent specification; it launches
the installed interpreter directly, retains its startup lock through actual
acknowledgment, and uses the existing namespaced lease as authority. The actual
child PID is published at .gate-logs/watchdog.pid. A checked one-byte integer
ready transition preserves valid lease JSON; interrupted/failed fsync, log flush,
PID publication, changed tokens/inodes and old matching pointers cannot admit an
unacknowledged child. Legacy stop verifies the native lease even without that
convenience pointer and waits for the UV parent to exit naturally.

The eight complete source test owners pass452 cases at94.7% line/89.7% branch,
each above91.4% line/85.4% branch, with original85/75 thresholds. Preserved RED
controls include actual SIGTERM, interrupted creator, duplicate fields,
publication faults and the native macOS TMPDIR alias. The source commit retains
fresh127157-test collection with0 errors. Its full report and exact commands are
/tmp/gludd-v012-agent-watchdog-lifecycle-report.md; source-only proof grants no
capacity, gate or CI authority.

Make exposes WATCHDOG_START_VALIDATE_ONLY=0 and WATCHDOG_AUTO_VALIDATE_ONLY=0
as their established actual-start defaults; literal1 plans. Shutdown and its
agent-watchdog-stop alias use WATCHDOG_VALIDATE_ONLY=1 by default, literal0
applies. Invalid policy is passed as the fixed word invalid without evaluation;
public policy variables are not exported. The 36 original negative controls
fail before repair; the expanded native default/alias/literal checks and whole
task/environment/guide owners pass249 cases. No background UV shell launcher
remains. The real legacy stop plan passes without writes or signals.
The integrated eleven-owner replay passes550 tests in27.87s; native audit
.gate-logs/coverage-20261020T192002Z.json retains94.7line/89.7branch and both
per-file thresholds. Its first combined attempt retains549PASS/1FAIL for two
undocumented startup variables; CONFIG_REFERENCE.md now documents the actual
native-only paths. Existing legacy non-detached identity correctly refuses the
start/auto plan until the verified stop handoff; refusal never creates a second
daemon. Exact XML is /tmp/gludd-v012-integrated-watchdog-final-coverage.xml.

For a verified legacy owner, use this sequence after source integration:

```bash
make watchdog-stop WATCHDOG_VALIDATE_ONLY=1
make watchdog-stop WATCHDOG_VALIDATE_ONLY=0
make watchdog-auto WATCHDOG_AUTO_VALIDATE_ONLY=0
make watchdog-start WATCHDOG_START_VALIDATE_ONLY=1
```

A refusal preserves ownership evidence and stops this sequence. Never signal a
supervisor from a diagnostic CWD. The native child drain and natural parent exit
must precede new direct-Python startup; re-establish both watchdogs immediately.
Only after actual supervisor absence changes the blocking input may the existing
disk cleanup run again. The real handoff at about19:21UTC verifies and signals only Python child68516;
UV parent68501 exits naturally without a signal. Native startup acknowledges
direct-Python child39922 and reuses task-watchdog29001. Exact old-PID inspection
then reports missing, new-PID inspection reports tracked-command ownership, and
make ps reports just the two Python watchdogs. All documented startup/auto plan
examples pass after this changed input.

Subsequent existing actual cleanup removes239986255 regenerable bytes and three
disposable caches; shared UV prune/clean completes and removes9files/7.6KiB.
Pass1 removes5 entries/0errors; pass2 removes0 and refuses no-progress at96%
against95%. Preserve this refusal. The historical manual native transcript is
/tmp/gludd-v012-agent-watchdog-real-handoff.md, never a current observation.
A bounded independent diagnostic at eccfcc3, using immutable main-owner exports
from7d1fec6, measures39 namespaces/0reclaimable bytes and exactly10128539648
available bytes. The separate8GiB floor passes; that does not clear the95%
ceiling. Largest2.325GB/1.503GB/0.962GB namespaces remain protected by worktree
ownership, and the0.119GB content cache by hardlinks. No outside-prefix inventory,
forced deletion, shortened retention or repeated full gate follows from size.
The readable historical decision is /tmp/gludd-v012-capacity-remediation-plan.md.

### Cleanup routes share native retirement ownership (2026-10-20)

Current source inspection found agent-cleanup and clean-stale-worktrees still
unlocked/force-removed worktrees, deleted branch refs and sometimes fell back to
recursive deletion. Their compatibility interfaces now delegate to the existing
native prune/retirement owner and default to plan. agent-cleanup requires exact
BRANCH and canonical WORKTREE; its AGENT_CLEANUP_VALIDATE_ONLY=1 plans selected
merged-owner retirement, literal0 applies. CLEAN_STALE_WORKTREES_VALIDATE_ONLY=1
plans ordinary pruning, literal0 applies. Both pass the selected registry and
literal arguments; invalid policy and Make expressions refuse before native IO.

The original30 native routing cases fail before repair. A real GNU Make -e
replay then exposes opposing inherited mode restoration during unexport; capture
the selected literal with override/value before unexport, as the existing runner
repair requires. Caller-side target assignments alone do not repair that input.
Quoting the registry as literal single-quoted data also prevents backtick
execution. Whole routing/retirement/registry/guide replay passes149 cases;
contract263 and native behavioral examples retain their real results. The
synthetic retirement example correctly refuses an absent registered owner.

Five additional native-Git controls prove ordinary pruning deleted ignored
failed-proof and history files and bypassed process/checkpoint/unknown-data
protection. Ordinary pruning now uses the same existing ownership, process,
checkpoint, identity revalidation and canonical evidence-archival routine.
It preserves its existing committed-unmerged-branch behavior through surviving
refs; selected merged retirement retains the original ancestry requirement.
No separate scanner, observer, evidence store or deletion authority is added.

The initial 154 complete cases pass in 33.68s at 93.6% line and 88.1% branch
coverage; preserve .gate-logs/coverage-20261020T193850Z.json as intermediate proof.
The expanded final replay passes all 172 cases in 36.79s at 94.3% line and 89.8%
branch coverage for the whole pruning file. Aggregate 85% and per-file 75% line
and branch thresholds remain unchanged. Final audit:
.gate-logs/coverage-20261020T194534Z.json; exact XML:
/tmp/gludd-v012-prune-preservation-sealed-coverage.xml.

The actual alias test exposes an extra unverified Make parent when delegation
recurses through another Make invocation. agent-cleanup therefore dispatches the
same native Python module directly. The existing two/three-controller bound is
unchanged; exact source, target, apply policy, path, process start and CWD checks
remain mandatory. GNU Make -e and --environment-overrides are recognized as
native flags, never ownership waivers. Sixteen positive/negative controller
cases and both real alias apply routes cover this boundary.
Two old fake-Git fixtures modeled only direct removal and could not exercise
new ownership IO; they now use confined native repositories with all original
active/idle assertions, added ref/data preservation and a genuine dirty file.
No original acceptance is dropped. RED receipts remain
/tmp/gludd-v012-legacy-worktree-cleanup-red-native.xml and
/tmp/gludd-v012-prune-preservation-red.xml; intermediate wrong-mode/fixture
failures remain as such. Ruff, docstrings and strict four-file typing pass.

The firsthand [June2026 reproduction](https://github.com/blamechris/chroxy/issues/5244)
reports ignored local data disappearing after a clean status and ordinary
worktree removal. The [September2026 practitioner report](https://github.com/cursor/plugins/issues/449)
describes an agent cleanup playbook losing untracked/ignored evidence through
force removal and fallback deletion. These support explicit ignored-data and
native-ownership checks; they do not authorize replacing a refusal. Our confined
Git tests independently reproduce the project failure and prove archival.

A safe pruning plan reports candidates with clean check deferred. It proves no
actual eligibility or reclamation. Apply uses fresh native ownership and archival;
capacity must be measured afterward before real release admission. All19 release
items remain open; source/coverage/plan are not full-gate or exact-SHA proof.

### Watchdog observations stay inside their native ownership boundary

Actual post-integration pruning at development 3b62a8b archives the finished
model-workflow checkout's proof under
.git/gludd-release-evidence/3efdba60cc11-20167a2f7ecfdff362c5f97e0ca40e1bd6147972
and removes its materialization. The native receipt retains branch
codex-v012-model-workflow-20261020 and commit
20167a2f7ecfdff362c5f97e0ca40e1bd6147972. All six retained checkpoints verify.
The public-review checkout is retained for ignored .ci-status data; the
release-note checkout remains dirty and is retained. Locks and active owners
remain protected. The native summary is removed=1/protected=4/retained=2.

The retained .ci-status contains a synthetic pending run 99999. Source tracing
finds the same mocked run in two watchdog tests: local gate/tasks/ratchet/cache
paths were confined, but the CI output path still targeted the real checkout.
Two precondition assertions reproduce that escape before any writer runs:
2 FAIL in 1.09s, /tmp/gludd-v012-watchdog-observation-confinement-red.xml.
The existing autouse fixture now confines that output for every case. Both
ownership preconditions and an actual confined CI-status write are checked;
all 97 original cases pass in 1.18s at
/tmp/gludd-v012-watchdog-observation-confinement-green.xml. Original detection,
health, stop and failure assertions remain. Production observation semantics,
current JSON and historical files are unchanged; no observation is hand-edited.

The same read-only process census labelled acknowledged Python watchdogs
29001 and 39922 ORPHAN and claimed PPID1 meant stale. Parent 1 also describes
healthy detached daemons. An initial topology-label correction is retained as
manual history. The final Make route uses the existing active_work_status.py
--daemon-process-table mode staged at 6d4e082bb (source b1378f4aaad4d4d5a93e171c202feff1a2912c16).
It filters existing watchdog/e2e-daemon classifications from one fresh native
project-owned snapshot and uses the same bounded renderer. Global grep and
parent-based stale inference are removed. Default JSON and agent_pids:false
remain unchanged; no scanner, observer, signal or cleanup authority is added.

Nine original audit-filename controls reproduce false watchdog labels. Native
execution order now distinguishes an audit naming watchdog source from a real
watchdog whose argument says pytest. All 94 original cases plus 23 additions
pass: 117 cases, whole-producer 94.6% line/90.8% branch coverage under 85/75.
Source receipts and original failures are retained in
/tmp/gludd-v012-daemon-table-report.md. The actual production Make example
exits zero and reports only acknowledged watchdogs 29001/39922, each PPID1.
The original observability source-token assertions now inspect their actual
native producer and its Make delegation; their watchdog-visibility intent is
retained by the real CLI and native snapshot controls. No gate authority follows.

The integrated complete eight-owner replay passes all 292 cases in 11.80s;
whole original producer coverage passes the unchanged 85/75 line/branch audit at
.gate-logs/coverage-20261020T202036Z.json. Exact XML:
/tmp/gludd-v012-daemon-main-coverage.xml; observed elapsed 13.6s, rc0.
Ruff, 268 target contracts, 1174 unique targets and the canonical generated
264-resource ownership inventory pass. Native Node26 compatibility separately
passes all six controls. Full release gate and exact-SHA CI remain missing.

After the real worktree removal changed capacity input, one ordinary actual
cleanup removes 6012 regenerable bytecode files (240043188 bytes) from the public
checkout, seven (174190 bytes) from the release-note checkout and disposable
caches. Pass one reports removed=6/errors=0; pass two reports removed=0/errors=0
and native no-progress failure. Scratch changes from 416.1 to 403.4 MB, while
disk remains 96% against the hard 95% ceiling. Do not repeat the same failed
input. Real admission and final proof remain blocked by capacity; source proof
does not waive that prerequisite or close any release task.

A final bounded cleanup after the completed public-review workstream was
unregistered and its fresh collection regenerated caches has a changed eligible
input: 6013 bytecode files, 240096109 bytes. The existing actual preflight
removes those files and disposable caches, reports pass1 removed=5/errors=0,
then pass2 removed=0/errors=0 and refuses no-progress (native rc1, Make exit2).
Scratch changes from416.9 to404.0 MB; disk remains96% against95%.
The release-note checkout has no eligible bytecode. All518 generated-scratch
candidates are preserved by native eligibility checks; no retention, unknown
data, checkpoint or ownership guard is weakened. Do not repeat this same input.
The final read-only state atbc324c286 is clean on development, two acknowledged
watchdogs and zero observed audit workers; exact-SHA CI has no runs and both
required workflows remain missing. These measurements supply neither a green
full gate nor permission to promote. Existing daemon connection refusal still
prevents filing the capacity HumanTodo; its unsubmitted request is configuration
only. SESSION.md and the mandatory ordered Make guide retain the exact next
admission command after real capacity changes.


### Bounded shared-UV cleanup phase diagnostics

The2026-10-19 disk repair reclaimed241,812,053 and237,600,576 bytes of
regenerable bytecode plus disposable caches, while actual disk remained96%
against the unchanged95% limit. The existing aggregate20-detail output hid
shared-cache reasons behind409 scratch skips. The existing cleanup owner now
emits one flushed shared-uv-cache phase event directly from its existing
CleanupResult before aggregation: status/full counts plus at most20 skip and
error details each. Native active-UV PIDs/refusal reasons remain visible;
refusals use stderr and other outcomes stdout. No reinspection, extra cleanup,
scanner, runner, cache or broader deletion authority is introduced.

Four failing-first cases retain409 unrelated skips/errors and prove event
ordering, full aggregation, exact inspection/operation counts, bounded detail,
streams and unchanged rc1 under96% pressure. All independent cleaners are
confined/stubbed; native UV callbacks are injected and fixture bytes survive.
Final complete owner passes4/1.45s on311 and4/1.56s on312; native observers
2.974/2.913s both rc0. Ruff and separate strict source/test checks pass.
Original4FAIL, intermediate passes and namespace/type failures remain retained
at /tmp/gludd-v012-cleanup-capacity-diagnostics-report.md. No whole-source
coverage, real reclamation, capacity clearance or gate is claimed by this proof.

The existing firsthand cache-locking research in
[automatic disk cleanup](../automatic-disk-cleanup.md) remains context, never
permission to bypass active-owner checks. Every identity/root/symlink/lock,
checkpoint,120s operation and95% disk/85-75 BOTH coverage/ZDD guard remains.
After genuine source integration, use the existing documented cleanup owner
to inspect actual reasons; do not retry expensive final proof until the real
capacity prerequisite clears. All19release items remain open.

### Complete-gate deadline and truthful terminal state

The release gate runs 244 fresh-process batches so imported state, native
allocations, coverage databases, temporary roots, and child processes retain
bounded owners. A cold run can therefore legitimately exceed one hour. The old
3,600-second defaults in `make/00-foundation.mk` and
`scripts/agent_watchdog.py` killed a healthy run in shard 5/8 even though its
per-test, quiet-output, disk-reserve, and process-identity checks remained
healthy. The background launcher and independent watchdog now use the same
21,600-second outer ceiling. This does not relax the 180-second per-test limit,
quiet-output reaping, or exact process-group cleanup.

Status is a separate ownership contract. A `RUNNING` receipt is authoritative
only while its exact background PID is live. `active-work-status` reports a
dead `RUNNING` receipt as `ORPHANED` and recognizes explicit `GATE_TIMEOUT`
evidence; `gate-status-check` emits `=== GATE: ABORTED ===` with
`reason=orphaned-running-status` instead of the contradictory
`FINISHED: RUNNING`. Neither probe fabricates a pass or silently retries.

This follows the long-lived operator evidence in
[pytest-xdist issue 220](https://github.com/pytest-dev/pytest-xdist/issues/220),
which requests a controller-owned session timeout, and
[pytest-xdist issue 61](https://github.com/pytest-dev/pytest-xdist/issues/61),
which records workers waiting before execution. The per-test/whole-session
distinction is also documented in
[pytest-timeout issue 60](https://github.com/pytest-dev/pytest-timeout/issues/60).
The rollout is ZDD because it changes only validation ownership and reporting;
it adds no worker or application process. Rollback waits for an active gate to
finish, then restores the old source values without a service restart or data
migration.

### Lightweight diagnostics

`make active-work-status` is dependency-free and runs with
`/usr/bin/python3`. Its dependency-free recipe must not invoke
`uv run`, resolve dependencies, create `.venv`, or mutate the lockfile. Targets
that need application dependencies continue to use the locked uv environment;
this exception is deliberately narrow rather than a project-wide bypass.

This matches uv's documented behavior: in a project, `uv run` creates the
project environment when absent and makes sure it is current before execution.
The long-running upstream discussion also records practitioners being surprised
when `uv run <nested-script>` creates the root `.venv`:
[uv project environments](https://docs.astral.sh/uv/concepts/projects/layout/)
and [uv issue 11302](https://github.com/astral-sh/uv/issues/11302).

### Bounded observable cache inventory

The existing `cache-resource-inventory` owner previously measured every child
with an independent 30-second native `du` timeout. Its output limit bounded
display rows, not total work, so a large temporary root could remain silent for
minutes while reporting no progress.

The same owner now shares one 30-second monotonic deadline and a 1,000-child
measurement cap. Native `scandir` streams immediate entries without first
materializing the entire directory; at most one extra entry detects overflow.
There is no recursive Python walker. Each fixed `/usr/bin/du -sk` invocation
receives the lesser of its original 30-second timeout and the remaining global
time. Expired validation, iteration or measurement starts no later child.
Directory iteration closes on success, capacity exhaustion and scan failure.

Flushed stderr announces the root and bounds before measurement, the current
child with remaining time, throttled progress between measurements at five
seconds, and terminal accounting. A blocked native call remains subject to its
remaining-time timeout; no heartbeat daemon, thread, runner or library is added.
OS process creation and filesystem system calls are not independently
interruptible, so the deadline is checked on both sides of enumeration and
before every child rather than claiming an absolute kernel-I/O guarantee.

Complete scans preserve the existing `list[CacheEntry]` API, error-first and
largest-first ordering, output limit and JSON-line schema. Bounds or scan
failure raise a `CacheResourceError` subtype carrying bounded sorted partial
rows. The CLI emits those rows with `status: incomplete` and returns 2; measured
child errors also produce an incomplete/nonzero terminal result. Partial rows
are not the largest entries of the unvisited remainder and never claim a
complete inventory. The successful terminal object remains unchanged.

All root/depth allowlists, symlink checks, validate-only behavior and exact-child
removal code remain unchanged. A temporary-root inventory grants no deletion
authority. Native Make acceptance uses two tiny owned files under the pinned
system Python; separate small controls prove deadline/entry stops, partial rows,
closed iterators and timeout child reaping. No real cache is removed or scanned
by focused acceptance, and the canonical coverage profile retains 85/75 floors.

The [monotonic clock](https://docs.python.org/3.11/library/time.html#time.monotonic),
[context-managed iterator](https://docs.python.org/3.11/library/os.html#os.scandir)
and [subprocess timeout contract](https://docs.python.org/3.11/library/subprocess.html#subprocess.run)
provide the existing standard-library primitives. A
[March 2021 operator report](https://unix.stackexchange.com/questions/636965/size-of-directory-with-millions-of-sub-directories-without-du)
describes a multi-million-entry `du` taking 17 hours; it motivates bounding and
honest incompleteness, not replacing Gludd's scanner or claiming the same host
cause. This is control-plane ZDD with no service restart or data migration.
Rollback restores this owner/test/documentation change together through normal
review; full-gate, exact-SHA hosted and release proof remain independent.

### Cross-worktree process census

On 2026-08-29, the canonical local dual-track producer was visibly active in
`make active-work-status`, including its serial runner, child controller, and
pytest worker under a `/private/tmp` linked worktree and namespaced resource
root. At the same instant, `make ps` reported no project processes because its
grep expression recognized only the main checkout's literal host path. The two
diagnostics therefore disagreed at exactly the point where an operator needed a
fast resource-safety decision.

Both surfaces now use the dependency-free `active_work_status.py` inventory.
The inventory asks Git for the repository's registered main and linked
worktrees, derives each checkout's Gludd resource namespace, and admits only
known test, audit, watchdog, daemon, or test-supervisor commands that mention
one of those complete path roots. A fixed-point parent/child pass retains a
tracked controller whose relative command line omits its checkout but whose
tracked child carries the owned path. Path-component boundaries reject sibling
names such as `gludd-copy`; generic pytest commands and another project's
Gludd resource namespace remain excluded. Output is read-only and capped at 512
rows with an explicit overflow count.

Evidence was reviewed on 2026-08-29. Git documents `worktree list --porcelain`
as a record format whose first attribute is always the worktree path, making it
the maintained repository-membership source rather than a host-specific glob:
[Git worktree porcelain format](https://git-scm.com/docs/git-worktree#_porcelain_format).
The long-lived practitioner discussion in
[psutil issue 2335, opened 2023-12-09](https://github.com/giampaolo/psutil/issues/2335)
warns that command lines are not unique process identity and that heuristic
matching can target the wrong process. Gludd therefore uses command text only
for a non-mutating census, requires an independently derived repository/resource
root, and leaves termination to the existing PID/start-time ownership guards.
The need to keep child processes visible after a controller fault is also
documented by [pytest-timeout issue 159](https://github.com/pytest-dev/pytest-timeout/issues/159),
whose practitioner report has remained open since 2022.

The rollout is ZDD: `make ps` remains a synchronous read-only command, starts
only one bounded Git query and one bounded process-table query (ten seconds
each), and creates no daemon, lock, temporary artifact, or application state.
Existing callers receive the same zero/nonzero target contract and a more
structured table. Rollback is a code-only revert to the former recipe; there is
no data migration or service restart. During rollback, use
`make active-work-status` as the authoritative cross-worktree snapshot rather
than inferring idleness from the legacy main-checkout-only view.

### Watchdog PID ownership and visibility

The OpenCode session watchdog and the Python task watchdog have independent
lifecycle owners. The plugin creates and removes only
`/tmp/gludd-watchdog.pid`; it must never unlink
`/tmp/gludd-task-watchdog.pid`, which belongs to the separately launched
`scripts/task_watchdog.py` daemon. Ending an editor session is not evidence that
the task watchdog exited, so deleting its record creates an unowned live
controller and defeats PID/start-time checks. A runtime regression now creates
an external task-watchdog record, executes the real plugin cleanup hook, and
proves the record and its contents survive.

`make ps-gludd` includes both `task_watchdog.py` and `agent_watchdog.py` in its
namespaced, read-only process census. That does not grant ownership or broaden
termination scope; it makes every destructive controller visible while
retaining the repository-root and process-lineage filters described above.
The change creates no new daemon or restart window. The plugin source itself is
loaded by OpenCode only at startup, so an operator must restart OpenCode before
the new cleanup behavior is active; the Make diagnostic is effective on its
next invocation. Rollback is a single code/test/documentation revert after
confirming neither PID file is being used to make a destructive decision.

The boundary follows the same practitioner evidence as the cross-worktree
census: [pytest-timeout issue 159](https://github.com/pytest-dev/pytest-timeout/issues/159)
shows the consequences of split subprocess ownership;
[psutil issue 2335](https://github.com/giampaolo/psutil/issues/2335) warns against
inferring identity from command text; and
[psutil issue 2534](https://github.com/giampaolo/psutil/issues/2534) explains why
the owning process group can outlive an intermediate controller. These reports
support explicit record ownership and observability; they do not establish the
sender of Gludd's still-unattributed gate `SIGTERM`.

### Concurrent gate-lite evidence

Two linked worktrees can safely execute their bounded two-worker `gate-lite`
unit phases at the same time, but they must not publish into the same evidence
file. The former recipe redirected both sessions with shell truncation to
`/tmp/gludd-gate-lite-test.log`. A later session could therefore erase the
first session's progress, while either operator could read the other session's
failure marker or tail. The pytest base directories were distinct, but the
diagnostic ownership boundary was not.

The unit phase now runs through the existing observable-command wrapper under
the invoking checkout's `OBSERVED_ROOT`, using the `gate-lite-unit` label and a
fresh run identifier. Each worktree therefore owns a different root, and each
invocation owns a different retained log and trace within that root. The
wrapper streams pytest output, emits periodic heartbeats, preserves the exact
exit status, enforces the existing quiet and whole-run deadlines, and retains
at most the configured run count. Failure output tails that label's newest
owned run instead of a host-global filename. The two-worker cap, fail-fast
behavior, unique base directory, and zero worker-restart policy remain intact.

This ownership rule matches a long-lived pytest-xdist practitioner report.
[pytest-xdist issue 331, opened in 2018](https://github.com/pytest-dev/pytest-xdist/issues/331)
documents that pytest's file logger opens in write mode and that mixing worker
output makes complete troubleshooting evidence difficult. Appending every
Gludd session to one file would retain the same ambiguity; unique worktree and
run ownership prevents both truncation and interleaving.

The change is gate-only and ZDD-safe: it adds no service, migration, listener,
or worker, and observed-run retention is already bounded. Paths come from the
fixed repository-local root and label rather than test data. Rollback is a
Make/test/documentation revert after active new-format gates exit; restoring
the shared `/tmp` log while concurrent worktrees run would knowingly restore
cross-run evidence corruption.

### Owned Node download-cache reclamation

The machine-readable disk classification on 2026-10-05 measured 143.1 MiB of
counted Gludd scratch. Its largest root was an 83.9 MiB generated cache inside a
registered, dirty worktree with a live pytest process, so that root was not a
safe cleanup candidate. The next largest proven project-owned class was the
17.3 MiB `/tmp/gludd-npm-cache-public-v1` download cache. A similarly named
`gludd-npm-cache-s83-163` root is not part of the canonical contract and remains
untouched.

The same inspection corrected a misleading initial theory: the accumulated
`gludd-test-fc-*.sock` paths were zero-byte regular-file lookalikes created by
mock tests, not Unix socket inodes. The generated-scratch cleaner continues to
refuse those files as unsupported rather than treating a suffix as ownership.
Only a real socket inode receives the existing socket-owner checks.

The automatic disk preflight now reclaims only the exact direct children
`gludd-npm-cache` and `gludd-npm-cache-public-v1` of the canonical temporary
root. A candidate must be a real directory, not a symlink or special file. A
bounded walk rejects symlinks, special entries, and trees over 50,000 entries;
the newest observed entry must be at least six hours old. Two process-table
checks refuse any `npm`, `npx`, npm CLI, or exact-cache-path owner. The complete
tree identity is then recomputed, and the root receives an immediate `lstat`
identity check before removal. Changed, fresh, ambiguous, active, raced, or
uninspectable candidates fail closed.

The 2026-10-07 preflight exposed a second, distinct lifecycle: counted scratch
was 943.4 MiB while the exact `/tmp/gludd-playwright-browsers` root accounted
for 853 MiB. The generic six-hour age floor preserved that newly downloaded
cache, so cleanup made no measurable progress and gate admission failed even
though no browser or installer owned it. Under measured disk pressure only,
the automatic cleanup may now bypass the age floor for that one canonical
Playwright root. The npm roots retain their six-hour floor. The Playwright tree
still must pass the same 50,000-entry bound, two idle process censuses,
complete-tree identity comparison, and immediate root identity check. macOS
browser frameworks legitimately contain nested symlinks, so this one cache
admits them only when Python reports symlink-attack-resistant tree removal. The
snapshot fingerprints each link and its target text without following it; a
linked root, special entry, changed link, or platform without that safe remover
is refused. Its census additionally recognizes Playwright commands and an exact
cache-path owner, so a concurrent install or browser run refuses cleanup.
Dry-run mode reports the candidate without deleting it.

Every pass emits candidate, inspected-entry, removed, skipped, and error counts.
An absent exact cache is a converged state, so repeated pressure checks remain
idempotent. This is a zero-downtime cleanup: installed dependencies and lockfiles
are outside the cache, active package-manager work is protected, and a later
locked `npm ci` recreates the download cache on demand. Rollback is a code and
test revert; already reclaimed bytes are regenerable and require no data
migration or service restart.

The Playwright extension has the same ZDD shape: it stops no service, changes no
application state, and browser binaries are regenerated by the pinned browser
install target before acceptance runs. Rollback removes the pressure-only
allowance; if the cache was already reclaimed, rerun the pinned installer rather
than restoring host state. No listener, schema migration, daemon, or coordinated
application restart is involved.

The policy follows two long-lived practitioner reports. npm CLI issue
[#3176](https://github.com/npm/cli/issues/3176) shows `_cacache` changing and
growing across repeated `npm ci` runs. The 2012 npm issue
[#2500](https://github.com/npm/npm/issues/2500) records shared-cache corruption
under concurrent installs and explicitly questions whether cleaning in parallel
is safe. Those reports support an age boundary plus two idle-owner proofs, not
an unconditional recursive deletion.

Playwright practitioners report the other side of the resource boundary:
[issue #37354](https://github.com/microsoft/playwright/issues/37354) describes a
cache exceeding 26 GB in two weeks and asks for automatic size or age limits,
while [issue #36682](https://github.com/microsoft/playwright/issues/36682)
records more than 40 GB of accumulated temporary artifacts. Conversely,
[issue #5797](https://github.com/microsoft/playwright/issues/5797) asks to retain
shared browser downloads deliberately. Gludd reconciles those durable needs by
retaining the cache while healthy and reclaiming only its exact project-owned
root under measured pressure after repeat idle and identity proofs.

### Registered-worktree disposable-cache reclamation

The canonical disk classifier measured 1,090,775,078 counted bytes across
3,425 entries after shared uv and Playwright caches were correctly classified
outside the scratch total. The largest remaining roots were generated
`.mypy_cache`, `.pytest_cache`, and `.ruff_cache` directories inside registered
worktrees. A logical workstream lease had protected each whole worktree even
when no operating-system process owned those disposable caches, so cleanup
could make no progress toward the 100 MiB admission ceiling.

Under measured pressure, cleanup may now remove only those three exact,
direct, non-symlink cache directories from a registered worktree. Git
registration and the logical lease must remain stable across the operation,
and two process censuses must prove the worktree idle. A changed lease, live
process, symlink, special file, or identity race fails closed. Source files,
evidence, `.venv`, and the worktree materialization retain the stronger
completion-proof requirement; a dirty or active worktree is never retired by
this cache-only path.

The canonical replay removed 94 proven-idle resources.
Scratch was healthy at 74.7 MiB after the bounded pass. This is zero-downtime
delivery: no service,
listener, database, dependency environment, or active test owner is stopped.
Rollback removes the cache-only allowance; deleted cache entries are generated
state and are recreated by the next owning tool invocation without migration.

Long-lived practitioner reports describe the same generated-state pressure.
[mypy issue #15731](https://github.com/python/mypy/issues/15731) reports cache
growth making ordinary runs substantially slower, while
[mypy issue #19489](https://github.com/python/mypy/issues/19489) discusses
incorrect or stale incremental cache behavior. Ruff users similarly requested
bounded cache cleanup in
[Ruff issue #12284](https://github.com/astral-sh/ruff/issues/12284). These
reports support narrow regeneration of proven-idle tool caches, not deletion of
worktree source, environments, or evidence.

### Repository gate-artifact reclamation

The cache resource manager also recognizes this checkout's canonical
`.gate-logs` directory as a removable generated-resource root. Removal remains
operator-selective and fail closed: the candidate must be one existing,
non-symlink, immediate child of that exact root. It cannot select the root,
another checkout, a nested path, or a sibling receipt. Validation mode is the
default; applying removal requires `CACHE_RESOURCE_VALIDATE_ONLY=0`. This lets
disk-pressure recovery discard reproducible downloads such as
`.gate-logs/ci-artifacts` without weakening the 90% admission ceiling or
touching the current gate status.

Practitioners report that local self-hosted runner workspaces otherwise remain
after a run and recommend explicit end-of-run cleanup to avoid a time sweep
colliding with active work in
[GitHub Community discussion #205363](https://github.com/orgs/community/discussions/205363).
An older runner-space report likewise shows large build layers exhausting the
available filesystem in
[GitHub Community discussion #179725](https://github.com/orgs/community/discussions/179725).
Gludd therefore requires an exact generated child chosen after an ownership
audit instead of broad host cleanup.

This is ZDD-safe because no service, database, listener, or active process is
restarted. Remote CI artifacts remain authoritative and may be downloaded
again. Rollback removes the repository root from the allowlist; already removed
local copies need no migration and are recreated only when requested.

### Distribution cleanup

`make clean CLEAN_VALIDATE_ONLY=1` is the safe behavioral contract. Actual mode
`0` removes normal local caches and delegates only `dist/` cleanup to
`git clean -fdX -- dist`. The pathspec confines deletion to `dist/`; `-X`
selects ignored build outputs, so Git-tracked inputs such as
`dist/debian/control`, `dist/rpm/gludd.spec`, `dist/windows/gludd.nsi`, and
`dist/install.sh` survive. Git documents `-X` as removing only ignored files and
pathspecs as restricting the affected paths:
[git-clean documentation](https://git-scm.com/docs/git-clean.html).

#### Dry-run deletion incident and prevention

An exact-head gate exposed a GNU Make recursion trap: the historical `clean`
recipe put `$(MAKE)` validation and destructive cleanup in one shell recipe
line. GNU Make intentionally executes any recipe line containing `$(MAKE)` even
under `-n`, so the Makefile audit's `make -n clean` invocation deleted the
gate's live `.venv` and caches. The failure was therefore repository-owned—not
macOS cleanup—and the next unit shard could no longer import pytest.

The recipe now runs the validation test directly through the repository's
locked Python environment and contains no recursive-Make marker. A structural
test rejects any future `$(MAKE)` in the `clean` recipe, while a behavioral test
runs the real dry-run against a sentinel `.venv` and proves its bytes survive.
Actual cleanup remains available only through the explicit
`CLEAN_VALIDATE_ONLY=0` contract; cache-pressure reclamation remains separately
bounded, observable, and lease-aware rather than being disabled. The damaged
shared uv cache was preserved for diagnosis and a new versioned cache root was
selected, avoiding an unreviewed deletion while restoring deterministic builds.

This behavior follows GNU Make's documented special handling of recursive
recipe lines in [How the `MAKE` Variable Works](https://www.gnu.org/software/make/manual/html_node/MAKE-Variable.html).
Long-lived practitioner reports show the same surprising behavior in
[recursive dry runs](https://stackoverflow.com/questions/72302726/gnu-make-recursive-dry-run-runs-commands),
[recipes calling recipes](https://stackoverflow.com/questions/73359439/makefile-calling-a-recipe-within-another-recipe-will-not-run-dry-it),
and the common recommendation to use `$(MAKE)` precisely because GNU Make runs
it despite `-n` in [recursive Make guidance](https://stackoverflow.com/questions/50510278/makefile-why-always-use-make-instead-of-make).

### Adaptive shard termination

The adaptive runner returns a result containing the child return code, captured
output, and optional termination reason. Bare `-9`/`137` exits and exact xdist
worker-crash diagnostics retain the OOM worker-halving backstop. A nonempty
termination reason always wins over that shape:

- parent `SIGINT` and `SIGTERM` are recorded as orchestrator signals, propagated
  to the child, normalized to `130` or `143`, and never retried;
- a quiet child emits heartbeats with elapsed, line-count, no-progress, and limit
  fields; after 900 seconds without output it is terminated with reason
  `no-progress-timeout` and code `124`;
- `GLUDD_ADAPTIVE_NO_PROGRESS_SECS` can lower the deadline for a bounded
  workflow but is capped at 3600 seconds; the heartbeat wake interval is the
  smaller of the heartbeat and quiet deadline, so enforcement cannot lag behind
  the configured bound;
- every final progress record says `finished` or `terminated` and includes the
  return code plus termination reason when present.

### Foreground gate ownership

The task watchdog must distinguish a dispatched task process from the gate
supervisor that owns that process tree. On 2026-09-26, an exact-head foreground
`make gate` was visibly progressing through its 3,411-test integration phase
when the five-minute stale-task watchdog killed the gate's `make` process after
496 seconds. The watchdog already excluded `.gate-background.pid`, but a
foreground gate publishes its owner atomically in
`.gate-logs/gate-run.lock`. Ignoring that second ownership record made a healthy
bounded gate indistinguishable from an abandoned task.

`scripts/task_watchdog.py` now reads both owner records and excludes the union
of each verified owner and its observed descendants. The ownership inventory
must cover every checkout registered by `git worktree list --porcelain`, not
only the watchdog's invoking checkout. The deadline ledger and process scan are
host-global, so a checkout-local exemption would let one linked worktree's
watchdog terminate another linked worktree's healthy gate. If Git cannot
establish the repository inventory, the destructive scan fails safe and sends
no signal.

Unrelated stale `pytest`, `make test`, Ansible, and Molecule processes remain
eligible when they are outside every active gate tree; missing or malformed
gate records grant no individual exemption. This preserves the watchdog's
bounded-resource and recovery behavior without allowing one control plane to
cancel another control plane's observable, independently bounded work. The
gate continues to emit progress and retains its own phase, no-progress, and
whole-run limits, so the change does not create an unbounded execution path.
Rollback is limited to removing the repository-wide ownership reader and its
regressions, with no state migration or resource mutation.

The ownership requirement matches long-lived practitioner evidence. The open
[pytest-timeout subprocess cleanup report](https://github.com/pytest-dev/pytest-timeout/issues/159)
documents child processes surviving timeout termination and recommends an
owning wrapper; [pytest issue #5243](https://github.com/pytest-dev/pytest/issues/5243)
documents that `SIGTERM` does not run ordinary fixture finalizers. Those reports
make process-tree authority—not elapsed time alone—the safe termination
boundary. [psutil issue #2335](https://github.com/giampaolo/psutil/issues/2335)
warns that command-line inference can become a security issue and kill the
wrong process, while [psutil issue #2534](https://github.com/giampaolo/psutil/issues/2534)
records practitioner experience that process groups preserve ownership even
when intermediate descendants exit. Gludd therefore discovers repository
membership from Git, protects each recorded owner tree, and verifies command
identity again immediately before signaling.

#### Identity-verified gate-tree termination

The historical force-kill path selected one PID from a host-global lock or a
background PID file, sent signals only to that process, and then removed lock
records whether or not descendants survived. Killing the root was not proof
that pytest, xdist, coverage, or shard-runner descendants had stopped. A reused
PID or another checkout's similarly shaped command could also make a broad
process-group signal destructive. The replacement contract acts only on the
tree rooted at the checkout's verified gate owner and never treats lock removal
as termination evidence.

**Marked ownership and legacy migration.** New gates publish
`.gate-logs/gate-run.lock` atomically with marker `gludd-gate-run-v1`, state
`active`, the owner PID, its stable `ps` start time, canonical checkout root,
and derived project namespace. Admission requires every field to match the
current checkout and live owner command. A marked `termination_failed` record
is retryable only through the exact target identities retained by the failed
attempt; normal gate acquisition and repository mutation continue to refuse it.

A rolling upgrade may encounter the exact legacy `{"pid", "started_at"}` lock
whose second field is the acquisition epoch. It is admitted only when all of
these independent facts agree:

- the schema has exactly those two fields and the PID is greater than one;
- the PID is live and its `ps` start time places lock acquisition from two
  seconds before through 300 seconds after process start;
- the command is one direct `make` or `gmake` invocation of only `gate` or
  `gate-refresh`, allowing only the known directory/silence flags and Make
  assignments;
- the live process working directory resolves to this canonical checkout; and
- its explicit `GLUDD_PROJECT_NAMESPACE`, or the default namespace when no
  override exists, equals this checkout's derived namespace.

Together these checks revalidate PID, start time, command, checkout root, and project namespace
before a legacy record gains destructive authority.

The complete legacy proof and unchanged two-field lock are checked again
immediately before the first signal. An ambiguous schema, unreadable working
directory or environment, extra target, clock mismatch, root mismatch, or
namespace mismatch refuses without signaling or rewriting the record. A fully
terminated legacy run removes its lock and records `lock_schema: legacy` in the
evidence. If any verified survivor remains, the lock is atomically migrated to
the marked format with state `termination_failed`, stable owner identity,
canonical root and namespace, and the exact target inventory. Migration never
turns incomplete cleanup into an active or absent lock.

**Descendant-first measured boundary.** After ownership admission, one bounded
process-table snapshot builds the owner tree through PPID lineage, including
descendants in different process groups. The order is deepest descendant first,
with PID as the deterministic tie-breaker. Immediately before every signal,
the candidate must still have the same PID, start time, and complete command;
an exited candidate is converged, while a reused or inaccessible identity is
skipped and prevents a success claim. Root and namespace come from the admitted
lock and live owner, and the ownership lock is revalidated again before any
lock mutation.

Each still-owned candidate receives `SIGTERM`. The production defaults poll at
100 ms through a 10-second `SIGTERM` grace. Only verified survivors then receive
`SIGKILL`, followed by a one-second `SIGKILL` observation using the same 100 ms
poll. No signal is sent to a sibling outside the admitted tree, and no negative
process-group signal is used. The snapshot is intentionally finite: a process
created after admission is not guessed into the target set. A replacement gate
therefore remains forbidden until the recorded owner and every admitted target
are terminal and the primary ownership lock has been released.

**Terminal evidence, refusal, and resource release.** An applied, admitted
attempt atomically publishes `.gate-status` with either `gate-kill terminated`
or `gate-kill termination-failed`, the survivor list, and
`=== GATE: ABORTED ===`. It also writes
`.gate-logs/gate-kill-evidence.json` with the lock schema, root, namespace,
recorded time, outcome, TERM/KILL/skipped/survivor PID sets, and every target's
PID, PPID, process group, start time, command, and depth. `ABORTED` is the only
terminal claim for an operator-requested kill; it can never become PASS or
ordinary test FAIL evidence.

On complete termination, the primary lock remains in place while auxiliary
`.gate-background.pid` and namespaced `async-gate.lock` / `gate.lock` records
are removed only when their PID belongs to the verified target set. The primary
lock is then removed only if its marker, PID, start time, and namespace still
match the admitted record. A live survivor, permission failure, identity
replacement, or failed post-KILL observation retains a marked
`termination_failed` lock and blocks a replacement gate. A missing lock is an
idempotent no-op; an existing unreadable or mismatched lock is a refusal. Dry
run reports the exact plan without signals, status changes, evidence writes, or
lock cleanup.

**Zero-downtime delivery and rollback.** This is a gate control-plane change:
it adds no application listener, schema, credential, daemon, or production
restart. New gates write the marked schema while an already-running legacy gate
keeps its record, so mixed-version rollout needs no hot adoption. To replace a
gate, stop new admission, terminate the exact old owner, require ABORTED status
plus JSON evidence with no survivors, and only then start the replacement gate
through normal acquisition. Application traffic continues throughout that ZDD
replacement-gate flow.

Rollback is also ordered. Stop new gate admission, use the new terminator to
finish or retry every marked `active` / `termination_failed` owner, require the
same terminal evidence and absent ownership lock, and only then revert the
scripts and tests. Manually deleting a marked lock, starting an overlapping
gate, or reverting while a marked owner remains live is outside the rollback
contract because the legacy implementation cannot prove that ownership.

The acceptance replay is warning-strict and covers a four-level tree spanning
three process groups, TERM-only exits, KILL escalation, PID reuse, wrong roots
and namespaces, permission/refusal paths, fail-closed retry, auxiliary-lock
ownership, and an unrelated sibling that receives no signal. The default
10-second + one-second deadlines and 100 ms observation interval are runtime
bounds, not estimates derived from test duration. The merged focused family
passes 47/47 tests with warnings treated as errors; targeted coverage measures
`kill_owned_gate.py` at 91%, above the 85% aggregate and 75% per-file floors.

Long-lived practitioner reports establish why every boundary is necessary:

- [CPython issue #111873](https://github.com/python/cpython/issues/111873), open
  since 2023, reproduces `ProcessPoolExecutor` workers surviving a killed parent
  on Linux and macOS and notes that parent-PID polling can fail after PID reuse.
- [GitHub Actions runner issue #3341](https://github.com/actions/runner/issues/3341),
  open since 2024, shows a runner logging "kill entire process tree" while the
  cited path killed only the root process.
- [Bazel issue #11910](https://github.com/bazelbuild/bazel/issues/11910), filed
  in 2020, reports builds and tests killed after a waited PID was reused as a
  different process-group identity. Gludd therefore revalidates stable process
  identity and signals individual descendants rather than a recycled group ID.
- [GitLab Runner issue #6189](https://gitlab.com/gitlab-org/gitlab-runner/-/issues/6189),
  reported in 2019, records cancellation leaving a stale lock that breaks the
  next CI job. Gludd removes only unchanged, proven-owned locks and retains a
  fail-closed record whenever terminal cleanup is incomplete.

#### Legacy-watchdog compatibility shield

Repository-wide owner discovery protects gates only after every task watchdog
has adopted the repaired code. A watchdog already running from an older linked
worktree cannot be upgraded atomically, and its historical candidate filter
already excludes commands containing the lowercase token `watchdog`. A new
gate therefore carries the explicit `watchdog-owned-gate` marker through every
long-lived command layer: detached recursive Make owner, integration wrapper,
integration pytest child, serial shard runner, and isolated or batched pytest
children. This closes the rolling-upgrade window without weakening the current
PID, start-time, worktree-inventory, and process-tree checks.

The marker is defense in depth, not a transferable ownership claim. It is added
only by gate-owned entrypoints; ordinary `make test`, integration, and shard
commands keep their existing shape. New watchdogs continue to derive authority
from Git membership and verified process lineage rather than trusting arbitrary
command text. Regressions construct the real child commands and require the
marker at every layer, while existing tests prove unrelated stale work remains
eligible for bounded cleanup.

Rollout is ZDD: command arguments change, but no listener, service, data schema,
or application process restarts. A running gate is not replaced. The next
detached gate gets the shield as it starts, and a rollback removes the marker
only after all legacy watchdogs are proven stopped or upgraded. During mixed
versions, removing it first would reopen the cross-worktree termination window.
The design follows long-lived practitioner reports about orphaned subprocesses
in [pytest-timeout issue #159](https://github.com/pytest-dev/pytest-timeout/issues/159),
wrong-process selection in [psutil issue #2335](https://github.com/giampaolo/psutil/issues/2335),
and durable process-group ownership in
[psutil issue #2534](https://github.com/giampaolo/psutil/issues/2534).

#### Automation-owned background gates

`gate-background` returns immediately, but it no longer delegates ownership to
`nohup` and an untracked shell sleeper. `scripts/start_gate_background.py`
starts the recursive Make gate with `start_new_session=True`; the published gate
PID is therefore also its process-group and session leader. A managed caller can
receive group `SIGTERM` without forwarding it into the gate session. The gate's
stdout and stderr remain attached to one exact `.gate-logs/gate-*.log`, so phase
markers are visible through the unchanged `gate-status-check` and `gate-wait`
interfaces.

Each admission publishes an atomic
`.gate-logs/gate-background-state.json` receipt containing a unique `run_id`,
PID, OS process-start token, PGID, SID, checkout root, resource namespace,
command, timeout, and exact log path. `.gate-background.pid` remains the
compatibility pointer and contains the real Make gate PID, not a wrapper PID.
The new session leader waits for its matching atomic receipt before it `exec`s
the gate command, retaining that PID across `exec` while preventing a fast gate
from disappearing before its ownership identity can be published.
Duplicate admission is serialized by a worktree-local file lock and compares
the receipt to live OS identity; a reused PID is never treated as the old gate.
An old watcher may update state or remove the PID pointer only while both the
`run_id` and exact PID identity still match.

Before publication, the launcher still owns the exact `Popen` child and the
wrapper cannot execute the gate application. If bounded identity capture fails,
waiting for that wrapper's publication deadline cannot help: only this parent
could publish its matching receipt. The launcher now aborts that direct child
with `Popen.terminate()`, waits at most 0.5 seconds, and escalates to
`Popen.kill()` plus one final 0.5-second reap attempt if needed. No process-table
search or unverified process-group signal is permitted. Signal/exit races still
attempt reaping; an unsuccessful final reap raises an explicit cleanup failure
instead of leaking a raw `TimeoutExpired` or claiming successful cleanup.
The original log retains an fsynced `identity-capture-failed` diagnostic, no new
PID/state/status receipt is published, and any prior state receipt is unchanged.
Healthy wrappers retain the full five-second publication allowance; watcher
ownership and all published-session timeout policies are unchanged.

This uses the existing standard-library lifecycle rather than another runner.
[Python's `Popen.wait()` documentation](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen.wait)
distinguishes a wait timeout from killing the child and permits a subsequent
wait. The long-lived 2015 practitioner report
[Understanding subprocess.TimeoutExpired](https://stackoverflow.com/questions/28131659/understanding-subprocess-timeoutexpired-want-to-kill-a-child-process-after-a-ti)
likewise reproduces a live child after its parent's timed wait expires. These
findings support explicit termination and reaping of the already-owned child,
not a shorter healthy-publication budget or broader cleanup authority.
Failing-first controls reproduce the real native wrapper's passive exit and
delayed wrappers' leaked timeout, then require exact-child TERM/KILL/reap,
preserved prior receipts/logs, no application execution, signal-error handling,
and acceptance of healthy publication near the original five-second deadline.
The unchanged branch-and-subprocess coverage profile and 85%/75% floors remain
the acceptance boundary. This startup-only repair opens no listener and changes
no application service or schema; rollback waits for owned runs to finish and
reverts the launcher without adopting or stopping an existing session.

The timeout watcher is also session-isolated, but unlike the former
`sleep 3600` sidecar it polls the exact identity and exits as soon as the gate
does. At the deadline it revalidates the current receipt, sends bounded
`SIGTERM` then `SIGKILL` to only the gate-owned process group, writes the same
`GATE_TIMEOUT` / ABORTED status contract, and removes only its own PID pointer.
If watcher creation fails, launch is rolled back and the gate is reaped before
the command returns. This closes the orphan-helper path while preserving PID,
status, timeout, log, namespace, and waiter compatibility.

`make gate-background-observed` remains the automation entrypoint when a runner
requires a visible top-level owner. It launches the same session-isolated gate
and enters `gate-wait`; `GATE_TIMEOUT` and `GATE_POLL_INTERVAL` remain explicit,
and `GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY=1` still verifies the bounded plan
without starting work. The direct target now also exposes
`GATE_BACKGROUND_VALIDATE_ONLY=1` for a side-effect-free contract check. GNU
Make dry-run stays inert because its recipe contains no direct `$(MAKE)` token.

The practitioner history is unusually consistent. A Stack Overflow question
open since 2011 reports that `nohup` children still died with their caller; its
maintained answer identifies a distinct process group and Python's modern
`start_new_session=True` as the relevant boundary
([Stack Overflow](https://stackoverflow.com/questions/6011235/run-a-program-from-python-and-have-it-continue-to-run-after-the-script-is-kille)).
GitHub Actions users have reported background build servers being collected at
job completion since 2020
([runner issue #598](https://github.com/actions/runner/issues/598)), while a
later report shows the inverse failure: a snapshot-based cleanup can miss a
new child and leave it reparented indefinitely
([runner issue #4601](https://github.com/actions/runner/issues/4601)). Together
they support explicit session boundaries plus an identity-aware, bounded owner;
neither `nohup` alone nor broad process-table cleanup supplies both properties.

This rollout is ZDD for application services: it changes only the local
release-control process tree, opens no listener, changes no schema, and neither
restarts nor adopts a running gate. Existing PID/status/wait callers keep their
paths and meanings. Rollback is a Makefile-and-launcher revert performed only
after the recorded run reaches a terminal state; no service cutover is needed.

#### Terminal receipt retirement and failed-kill ownership

A background watcher previously left `.gate-background.pid` and its own
`watchdog_pid` behind after a normal gate exit. More seriously, the timeout
path removed the PID receipt and published `timed_out` after its bounded
TERM/KILL sequence even when the exact gate session was still alive. That made
an incomplete kill look terminal, abandoned a live process tree, and allowed a
later launch to overlap it.

Normal completion now acquires the same launch lock used for admission,
revalidates the run identity and OS absence, atomically marks the receipt
finished, clears the terminal watcher identity, and removes only the matching
PID pointer. A concurrent replacement wins cleanly: the old watcher observes a
different run identity and changes nothing. If process-start inspection is
temporarily unavailable while the PID still exists, neither retirement nor a
duplicate launch is allowed.

At timeout, PID/start-token/session identity is checked through TERM, the grace
period, KILL, and the final observation. Only verified absence, verified PID
reuse, or verified terminal execution permits `timed_out` plus PID retirement.
Any live or unobservable owner
instead keeps the PID receipt, records `termination_failed` with
`gate-timeout-termination-failed`, clears only the exiting watcher PID, writes
`GATE_TIMEOUT_TERMINATION_FAILED`, and returns infrastructure status 125. The
next launch therefore refuses until the retained owner is safely terminated;
an operator never has to reconstruct authority from a process-table guess.

The boundary follows long-lived practitioner evidence. The open GitHub Actions
runner report [#3341](https://github.com/actions/runner/issues/3341) shows a
controller claiming to kill an entire process tree while killing only the
root. The psutil design discussion
[#2400](https://github.com/giampaolo/psutil/issues/2400) documents the remaining
PID-reuse race between identity validation and signaling, while runner report
[#4601](https://github.com/actions/runner/issues/4601) demonstrates descendants
escaping snapshot-based cleanup and remaining alive for hours. These reports
support retained ownership plus explicit uncertainty; they do not justify a
broader signal or a success claim after a failed observation.

Linux retains killed children in the process table until their parent reaps
them. Their start token, PGID, and SID remain valid, so those checks alone
previously reported `owned` after both TERM and KILL. The watcher now checks
the locked psutil process status after exact identity validation. A zombie or
dead leader is terminal only when every observed member of its exact PGID and
SID is also terminal; a live descendant retains ownership and KILL escalation,
and unavailable status or group evidence remains `unknown`. The watcher never
reaps the launcher's child, preserving the original `Popen.wait()` exit status.

The 2017 practitioner report
[Why does a Popen child process not die?](https://stackoverflow.com/questions/45362807/why-does-a-popen-child-process-not-die)
reproduces a child remaining visible to `ps` and signal-zero checks after KILL;
its accepted answer explains that the parent must reap the zombie.
[psutil's API documentation](https://github.com/giampaolo/psutil/blob/master/docs/api.rst)
likewise states that `is_running()` includes zombies. This distinction supports
explicit terminal-status evidence, not acceptance of a still-live or uncertain
owner. The original timeout, failed-kill, reused-identity, and replacement-receipt
assertions remain unchanged; regressions additionally cover unreaped leaders,
TERM-ignoring descendants, unknown observations, and preserved parent reaping.

The first coverage-instrumented replay additionally exposes a fixture
happens-before error: a fixed sleep can expire before the fake gate installs
its TERM-ignore handler. In conformance with observable readiness and owned
cleanup, that fixture now prints a flushed ready marker after installation,
waits for that exact marker with the existing bounded helper, and cleans its
child in `finally`. The timeout, poll interval, grace, and SIGKILL assertion are
unchanged. All 54 cases then pass at 96.36% combined coverage (96.86% line,
94.67% branch), retaining the failed 53/54 replay as diagnostic evidence.

This remains ZDD and bounded: it starts no additional process, extends no
deadline, opens no listener, and changes no application state. Normal runs
leave less stale control state; failed cleanup retains only the existing small
JSON/PID evidence and live owner it must describe. Rollout affects the next
watcher invocation. Before rollback, stop admission, resolve every retained
`termination_failed` owner with the identity-verified gate terminator, require
terminal evidence and an absent PID pointer, and only then revert. Reverting
first would restore the ownership-loss window.

#### Candidate history freeze

A clean worktree does not make HEAD immutable. During the first observed
v0.1.1 replay, the gate acquired its live owner lock at `c35a78038` and advanced
into integration preflights. A separate future-release merge then moved the
same clean `development` checkout to `8c4843b71`. No uncommitted file appeared,
so dirty-tree admission alone could not distinguish the launched candidate from
the later tree. The run remains useful diagnostics but is not exact-SHA proof.

The gate's existing worktree-local run lock is now also the Git mutation
boundary. `gate_run_lock.py assert-inactive` permits a missing lock, rejects a
live owner, fails closed on malformed ownership, and removes only a proven dead
owner. `_gate-mutation-guard` is a prerequisite of the common commit lock,
merge-strategy guard, agent/gated merge paths, checkout, and cherry-pick. Thus a
main-checkout gate freezes its own history while feature worktrees retain their
independent lock paths and can prepare repairs without touching the candidate.
The actual live-owner proof rejected a feature-worktree request aimed at the
main lock and named PID 81162; the same command passed against the feature
worktree's absent lock.

This is ZDD for services and conservative for repository control: it neither
restarts the running diagnostic gate nor blocks read-only status operations.
After that run terminates, its normal release removes the live lock and merges
can resume. A killed owner leaves a record that the next guard or gate reclaims
only after liveness proof. Rollback removes the mutation prerequisites and new
lock action, but doing so during an active candidate would intentionally reopen
the identity race. Git's
[worktree porcelain format](https://git-scm.com/docs/git-worktree#_porcelain_format)
provides the checkout boundary. GitHub runner
[issue #4601](https://github.com/actions/runner/issues/4601) and psutil
[issue #2335](https://github.com/giampaolo/psutil/issues/2335) provide long-lived
practitioner evidence for explicit, fail-closed ownership instead of process or
filesystem snapshots.

The same ownership rule now covers the Git index and reused process IDs. A
scoped commit records the exact requested path set and an immutable
`git write-tree` identity before its collection preflight, then rejects the
commit if either the staged paths or tree identity changes before publication.
This closes the observed race where another worker staged unrelated files while
the preflight was running. The gate lock likewise binds a PID to its recorded
process start time and treats a zombie as dead; a live PID with a different
start identity is a reused process, not the original gate owner. Lock refusal
includes the observed process state so an operator can distinguish live work
from stale evidence without guessing.

These failures recur in agentic and daemon workflows. OpenAI Codex
[issue #17482](https://github.com/openai/codex/issues/17482) records parallel
`git add` and `git commit` operations colliding on the index, while git-ai
[issue #1240](https://github.com/git-ai-project/git-ai/issues/1240) records
background checkpoints racing interactive Git and leaving stale index locks.
Paseo [issue #1304](https://github.com/getpaseo/paseo/issues/1304) demonstrates
the complementary PID-reuse failure on macOS and recommends comparing the live
process start time with the lock's recorded identity. Those reports support
Gludd's exact staged-tree snapshot and PID-plus-start-time contract rather than
retrying a commit or trusting liveness alone.

The change remains ZDD: it starts no process, modifies no serving application,
and only rejects a history mutation whose authority changed during validation.
Rollback is source-only after every active gate lock has reached terminal
evidence; reverting while a gate or commit is active would reopen the ownership
race and is therefore prohibited.

Presentation honesty validation now uses the same observed-command boundary.
Its repository-wide test count can take more than a minute without producing
child output, so `deck-honesty` emits an immediate start record, periodic
heartbeats, a retained log identity, and a terminal result while preserving the
checker exit code. This adds no retry and does not reinterpret silence as
success. The long-lived pytest-timeout session and child-cleanup discussions
linked below reinforce that a broad deadline and observable ownership must be
separate from an individual test timeout.

This division follows years of upstream practitioner discussion. The
pytest-timeout session-timeout request distinguishes an external CI deadline
from a stuck individual test, while the still-open child-cleanup report shows
why a wrapper must retain ownership long enough to terminate children:
[pytest-timeout issue 60](https://github.com/pytest-dev/pytest-timeout/issues/60)
and [pytest-timeout issue 159](https://github.com/pytest-dev/pytest-timeout/issues/159).
pytest-xdist has separately bounded worker crash restarts since 2019 to avoid
infinite crash loops:
[pytest-xdist changelog](https://github.com/pytest-dev/pytest-xdist/blob/master/CHANGELOG.rst).

### Bounded serial shard workers

On 2026-08-20, `unit-3` exposed a second boundary: a nominally serial xdist run
still made `gw0` collect all 38,099 selected tests. At 64%, the worker reported
`node down: Not properly terminated` during a session-start atomic write and
left a child behind, so the wrapper waited indefinitely even though the exact
test passed alone.

The named-shard runner now expands directory selectors deterministically and
subdivides them into batches of at most 64 files before starting pytest. Each
batch uses one worker, `--max-worker-restart=0`, a unique base temporary
directory, and unique plugin-state and coverage namespaces. The runner streams
all output plus periodic heartbeats. A worker-death diagnostic fails the shard
closed, stops later batches, and tears down only the process group it created
with bounded `TERM` then `KILL`; it never retries or adds workers.

This matches pytest-xdist's documented architecture: every worker performs a
full collection, even when only one worker is requested. Its supported crash
control is `--max-worker-restart`, including zero to disable replacement:
[xdist architecture](https://pytest-xdist.readthedocs.io/en/stable/how-it-works.html),
[xdist crash handling](https://pytest-xdist.readthedocs.io/en/stable/crash.html),
and [xdist distribution options](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).
Long-lived practitioner reports reinforce the fail-closed ownership boundary:
[issue 1278, opened 2025-11-18](https://github.com/pytest-dev/pytest-xdist/issues/1278)
records a nonzero worker exit not reliably failing the run;
[issue 1313, opened 2026-03-24](https://github.com/pytest-dev/pytest-xdist/issues/1313)
records an execnet receiver blocked after a worker disappears; and
[issue 1323, opened 2026-04-18](https://github.com/pytest-dev/pytest-xdist/issues/1323)
records a crashed-worker restart hanging `loadgroup` scheduling.

A 2026-08-20 follow-up exposed why the diagnostic must also be parsed rather
than searched as raw text. Pytest printed the parameterized node ID
`test_is_oom_exit_output_markers[[gw2] node down: Not properly terminated]`;
the embedded fixture value looked like a crash marker and incorrectly stopped
the healthy batch. The runner now accepts only complete xdist controller lines
after removing terminal control escapes. Test IDs, assertion payloads, and
ordinary stdout containing the same words remain data.

That grammar comes from xdist itself. `TerminalDistReporter` emits node-down
events as `[gateway-id] node down: error`, while `DSession` emits the two
restart-limit summaries as complete lines:
[xdist controller source](https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/dsession.py).
The long-lived practitioner report
[xdist issue 61, opened 2016-05-28](https://github.com/pytest-dev/pytest-xdist/issues/61)
shows the same standalone node-down line in a real hang. Matching those
boundaries, instead of a phrase anywhere in output, preserves real crash
detection without treating user-controlled output as controller state.

### Parallel-shard terminal deadline

On 2026-09-10, a foreground `unit-3a unit-3b` replica demonstrated a
controller-level stall that the 180-second per-test timeout could not own.
`unit-3a` reached a durable result, while the `unit-3b` pytest controller and
its xdist worker remained live and the wrapper emitted heartbeats indefinitely.
No JUnit document was finalized. Recovery required the existing
namespace-checked process-tree boundary, which found and reaped the worker,
pytest controller, and uv child without touching another checkout.

The foreground and background parallel-shard entry points now pass the same
strictly positive `MAX_RUNTIME_SECONDS` constraint to their supervisor. The
default is 3,600 seconds. The supervisor measures one monotonic run deadline,
adds elapsed and limit fields to every heartbeat, and assigns exit code 124 to
each still-pending shard when the deadline expires. Before cleanup it persists a
bounded per-shard summary and emits `SHARD-TIMEOUT` with only the shard name,
elapsed time, configured limit, and owned summary path. Its unconditional final
cleanup then interrupts and, after the existing ten-second grace period, kills
only the process groups it created. A completed peer retains its actual result;
timed-out work is never reported as passed or retried automatically.

This outer deadline is intentionally independent of a test-function alarm.
Practitioners have documented xdist controllers waiting forever on dead worker
pipes after tests stop producing events in
[pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313),
and the open request for master-side worker timeouts dates to 2017 in
[pytest-xdist issue 220](https://github.com/pytest-dev/pytest-xdist/issues/220).
The pytest-timeout maintainer also recommends an owning wrapper when a timed-out
pytest process can leave child processes behind:
[pytest-timeout issue 159](https://github.com/pytest-dev/pytest-timeout/issues/159).
These reports do not prove the exact local root cause; they establish that an
individual-test timeout is not a complete suite/process-lifecycle boundary.

### Deterministic statistical gate checks

A release gate must not fail merely because an acceptance test drew a new
sample from process-global random state. Distribution tests that compare a
sample statistic with a rejection threshold use named, test-local generators
and stable seeds. They keep their original sample sizes, alpha values, critical
values, and assertions; retrying a failed draw, widening a threshold, or
quarantining the test is not an acceptable repair. Tests whose purpose is true
system entropy continue to exercise the system source, while repeatable
distribution assertions control their byte or index source explicitly.

This follows the long-lived practitioner record. [pytest issue #667, opened in
2015](https://github.com/pytest-dev/pytest/issues/667) requests reproducible
random state so failures can be replayed. [pytest-randomly issue #600, opened in
2024](https://github.com/pytest-dev/pytest-randomly/issues/600) describes the
need for deterministic but distinct per-test values. NumPy's
[testing guide](https://github.com/numpy/numpy/blob/main/doc/TESTS.rst#tests-on-random-data)
states that random-data tests should use a local seeded generator because a
test that fails occasionally without a code change is not a useful regression
signal. The Gludd regression therefore fixes sample ownership instead of
rerunning the gate until chance produces a pass.

### Hermetic gate validation state

On 2026-08-20, a definitive gate started from a clean checkout but finished
with five Markdown files and `.gate-status` modified. The writer was a pytest
node that called the repository-wide documentation and package-initializer
fixers with their CLI defaults. The gate therefore passed only after one test
silently formatted inputs that later tests inspected.

Fixer CLIs retain their explicit repository-wide behavior for the corresponding
`make fix-*-drift` commands. Tests scope their module roots with pytest's
automatically restored monkeypatch fixture and exercise the implementations only
under `tmp_path`.
Regression sentinels prove that the real source and documentation trees remain
byte-identical. Existing mechanical drift was applied once as an intentional
source change rather than left for pytest to conceal.

`.gate-status` is durable operational evidence, not source. It remains available
to status and commit checks after a gate, but is ignored and untracked so an
observability update cannot dirty Git. Live-model tests likewise route `HF_HOME`
through pytest's function-scoped monkeypatch fixture or an explicit
session-scoped `MonkeyPatch.context()`. Restoration therefore runs even when
download, server startup, assertions, or teardown fail. The
`check-test-env-writes` guard rejects new bare test-environment assignments.

On 2026-08-21, the same replay exposed an older classification error:
`.ansible/.lock` was listed in `.gitignore` but remained in Git's index. Ansible
correctly removed its ephemeral lock at shutdown, leaving every otherwise-green
run with a tracked deletion. The lock is now untracked, and the root-hygiene
regression mechanically requires both `.ansible/.lock` and `.gate-status` to be
ignored operational state rather than committed source. This changes no Ansible
controller or managed-host runtime; each invocation may create and remove its
own lock without a test harness restoring it afterward.

On 2026-08-21, the definitive gate exposed a second status-ownership boundary:
the `run_gate.sh` unit harness inherited the live parent gate's
`GATE_STATUS_FILE` and `GATE_FAILED_FILE`. Its passing and failing stub gates
therefore published into the parent artifact while their own temporary status
files stayed empty. Every nested invocation now injects status and failure paths
inside its owned pytest work directory, and a regression proves the parent
markers remain byte-identical. This uses the same pytest `tmp_path` and
`monkeypatch` lifetime guarantees cited below; no production gate cleanup or
retry is added.

Evidence was reviewed on 2026-08-20. Pytest documents that
[`tmp_path` is unique to each test function](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
and that xdist places worker data under a per-run temporary root. Its
[`monkeypatch` guidance](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
guarantees fixture changes are undone after the requesting test or fixture and
provides a context manager for narrower lifetimes. Practitioner reports expose
the remaining boundaries: [pytest issue 11790, opened 2024-01-08](https://github.com/pytest-dev/pytest/issues/11790)
records collisions between concurrent invocations without unique base paths;
[issue 11789, opened 2024-01-08](https://github.com/pytest-dev/pytest/issues/11789)
records surprise that temporary directories are retained; and the long-running
[monkeypatch scope discussion opened 2018-12-25](https://github.com/pytest-dev/pytest/issues/4576)
highlights why restoration lifetime must be explicit.

### Retained module/importer pairs

On 2026-08-21, the 1-worker `unit-1b` gate batch passed every cron test in
isolation but failed all five after the preceding files. The import-state
sandbox retained ordinary modules first imported by a test while restoring
`sys.meta_path` to its earlier snapshot. That split left `six` cached but
discarded the `_SixMetaPathImporter` that implements `six.moves`; the later
`croniter` → `dateutil` import therefore failed with `No module named
'six.moves'`.

The sandbox now treats a retained package and a meta-path finder owned by that
newly retained package as one resource. It still restores the original finder
list and removes arbitrary test-injected finders. A focused regression creates
that acquisition pair mechanically, and the exact 64-file gate batch passes
1,931 tests with three intentional skips under warnings-as-errors. Rollback is
the single sandbox helper change; no scheduler fallback, import retry, or global
pre-import was added.

This boundary follows the actual upstream mechanism: the maintained
[`six` source](https://github.com/benjaminp/six/blob/main/six.py) creates a
`_SixMetaPathImporter`, registers `moves` beneath it, and installs it in
`sys.meta_path`. Practitioner reports show why the pair must stay coherent:
[python-dateutil issue 1430](https://github.com/dateutil/dateutil/issues/1430)
records the same missing-`six.moves` runtime shape on a newer Python, while
[pytest issue 12179](https://github.com/pytest-dev/pytest/issues/12179) records
pytest encountering a vendored Six meta-path importer during collection. The
reports do not establish the same root cause; they are evidence that Six's
importer is live process state rather than disposable test metadata.

The warnings-as-errors replay also exposed two smaller owner defects in the
same batch. One state-file test passed an inline `Path.open()` handle to
`json.dump` and never closed it. Two self-improvement tests constructed the
real harness around an `AsyncMock` model gateway, creating a coroutine that the
synchronous analysis path could not await. The state fixture now uses the
atomic `Path.write_text` shape it is meant to model, and the phase tests inject
a complete synchronous harness double and assert its call. Neither repair adds
garbage-collector cleanup, warning filters, retries, or production behavior.

## Security and Resource Boundaries

The status command uses a fixed system interpreter and existing fixed-argument
subprocess calls; it gains no dependency-install or network side effect. Cleanup
uses Git's tracked/ignored index as the authority and constrains its pathspec to
`dist/`, so a repository-wide ignored-file purge is impossible through this
target. Validation mode performs no cleanup.

The adaptive runner installs signal handlers only around its owned child and
restores the prior handlers in `finally`. It does not suppress a requested stop,
spawn a replacement shard, or increase worker count. One daemon heartbeat thread
per runner persists an atomic, PID-namespaced progress record. The 30-second
heartbeat, 15-minute default quiet deadline, and one-hour maximum keep both
silence and resource tenure bounded without multiplying workers.

The serial shard runner applies the same ownership rule per batch. It creates a
new process session, signals only that process group, joins its output reader,
and removes each batch workspace after coverage is preserved. External model
processes and unrelated test sessions are outside that group and remain
untouched. The fixed file bound prevents cumulative collection growth while the
strictly serial schedule keeps peak worker count at one.

### Collect-all failures versus safety stops (2026-09-27)

The serial runner now separates diagnostic test evidence from unsafe execution
state. A child pytest result of 1 (test failure), 2 (batch-local collection or
session failure without an owner cancellation signal), 5 (nothing collected),
or 6 (warning limit exceeded) is recorded and reported, but every independent
later batch and named shard still runs. The terminal summary retains every
failed phase under an exact key such as `<shard>:batch-NNN`,
`<shard>:batch-NNN:coverage`, `<shard>:cleanup`, or `<shard>:plan`. When the
plan contains only collected pytest failures, the runner returns their nonzero
maximum status; it never turns a collected failure green. Coverage is not
combined into release evidence when any such failure exists.

This is intentionally different from `continue-on-error`. Practitioners have
repeatedly needed every independent CI leg to run while keeping the aggregate
result red; the durable recommendation in
[GitHub Community discussion #45546](https://github.com/orgs/community/discussions/45546)
is fail-fast disabled with errors still enforced. Pytest users also report that
[`--continue-on-collection-errors` does not cover every stale or missing test
selection](https://github.com/pytest-dev/pytest/discussions/13213), so Gludd's
bounded runner continues at its own batch boundary and preserves the exact
failing command instead of relying on one large pytest process. The older
[`pytest` collection-hang report #6054](https://github.com/pytest-dev/pytest/issues/6054)
documents why collection silence must remain a resource stop rather than a
collect-all result.

Safety and integrity failures still stop immediately. These include an owner
SIGINT/SIGTERM (mapped to 130/143), disk-headroom failure (73), xdist worker
death (70), interpreter drift (78), no-progress termination (124), runner
exception (125), pytest internal/usage or unknown statuses, an empty shard plan,
coverage loss after a successful batch, and any unsafe or incomplete owned-root
cleanup. The runner performs bounded cleanup, emits `later-*=not-started`, and
does not launch another batch or shard. Operators should therefore read
`later-*=continuing` as complete diagnostic collection and
`later-*=not-started` as an intentional safety boundary, never as equivalent
release outcomes. A later terminal safety code takes precedence over any
earlier collected pytest status even when its number is lower. Cancellation
also takes precedence over a simultaneous cleanup failure; otherwise the first
unsafe execution result remains terminal while cleanup is retained as a
separate failed phase. The terminal attestation publishes that exact return
code with `status: fail` and never binds a stale coverage artifact to a failed
run.

Hermetic fixer tests create no source-tree lock or shared mutable workspace and
can run concurrently across xdist workers. Each invocation owns only its pytest
temporary root. Ignoring `.gate-status` changes Git classification, not status
visibility or writer ownership; the gate remains the sole producer of the
observable artifact. Environment restoration is scoped to the owning test, so
parallel live-model collectors cannot inherit a completed peer's cache root.

Process-group cleanup is idempotent across normal exit races. The owner first
checks that its group remains signalable, treats `ProcessLookupError` and
`PermissionError` as a completed or inaccessible ownership boundary, and then
uses bounded `TERM` followed by `KILL` only while that verified group remains
live. Repeated cleanup calls reap the root process but do not signal an exited
group or widen scope to unrelated PIDs.

## Zero-Downtime Delivery and Rollback

The change has no daemon, migration, network listener, or service restart. It is
safe for rolling development-to-master promotion: existing gates continue, new
status reads become cheaper immediately, and an in-flight adaptive runner keeps
the code it started with until its next invocation.

Rollback reverts the Make recipes, adaptive runner, regressions, and this
contract together. Tracked distribution templates remain source-controlled
throughout. If the adaptive change is reverted, terminate any runner started by
the new version before starting an old one; never run two wrappers against the
same shard or disable the resource/no-progress limits to mask a regression.

The bounded-batch change is also gate-only: it changes no deployed process,
database, listener, or artifact format, so rollout is zero-downtime. Rollback is
a single runner/test/documentation revert after any active new runner exits.
Rolling back restores the known unbounded-collection and retained-child risk;
do not mix old and new wrappers in one shard workspace.

The hermetic-write change is also gate-only and requires no service restart or
data migration. New and old gates may overlap because their pytest roots are
namespaced, but only the newer gate guarantees a clean checkout afterward.
Rollback must restore the tracked status snapshot and repository-global test
writes together; doing so reintroduces source mutation and is not operationally
safe while another gate is inspecting the same checkout.


## Mock Daemon Startup Ownership (2026-08-31)

Hosted run 33446746453 exposed a happens-before gap: the mock daemon could
answer `/healthz` before its atomic PID record existed. Startup now publishes
the PID file before `serve_forever` can accept a request, then publishes the
ready manifest after the serving thread starts. Callers use the existing bounded
deadline to observe either readiness or the owned child becoming terminal;
terminal diagnostics include the child PID and return code instead of waiting
until an opaque timeout.

The resource owner remains the daemon entry point. Its single serving thread,
socket, lease timer, PID file, and ready manifest share one outer cleanup
boundary on successful startup, publication failure, signal termination, and
lease expiry. No polling interval, process count, port, schema, or persistent
artifact changed. Delivery is zero-downtime and needs no migration or restart.
Rollback is one code/test/documentation revert after any active mock daemon
exits, but it restores the known readiness race and should be used only with the
previous build.

Evidence reviewed 2026-08-31:

- CPython's official `socketserver` implementation makes `serve_forever` the
  point at which requests become serviceable and pairs it with explicit
  `shutdown` and `server_close` ownership:
  <https://github.com/python/cpython/blob/main/Lib/socketserver.py>.
- systemd's official service contract documents PID-file consumption after
  startup and recommends readiness-aware service types over avoidable PID-file
  guessing:
  <https://github.com/systemd/systemd/blob/main/man/systemd.service.xml>.
- A long-lived practitioner report from 2016 describes a watchdog observing a
  daemon before its PID file was written, the same ordering failure reproduced
  by this regression:
  <https://stackoverflow.com/questions/36489529/linux-daemonize-without-pid-file-race-condition>.

## Serial and Parallel Coverage Artifact Ownership (2026-10-08)

`coverage-files` accepts both artifact layouts that coverage.py intentionally
supports. A serial run publishes the exact namespaced base file, while a
parallel run publishes one or more suffixed fragments. The target invokes
`coverage combine` only when a real fragment exists, then requires the exact
base before either `coverage report` or `coverage json` may consume it.

This keeps the ownership boundary fail closed: a missing base cannot borrow a
foreign worktree's data, stale fragments remain namespaced, and cleanup owns
the base, fragments, report, and temporary directory together. The behavior
does not change production traffic or persisted application schemas, so it is
zero-downtime. Rollback is one Make/test/documentation revert after the active
coverage producer exits; reverting restores the serial-artifact failure.

Failing-first evidence reproduced `No data to combine` after a successful
serial run. The repaired serial path passes 57/57 tests at 88% coverage; the
real parallel-fragment path passes 84/84 at 91%, with every measured file above
75%. The commit guard exercises both layouts before accepting the change.

Evidence reviewed 2026-10-08:

- The coverage.py changelog records the established parallel-data and combine
  semantics that this target now follows:
  <https://github.com/coveragepy/coveragepy/blob/main/CHANGES.rst>.
- A long-lived practitioner report reproduces the same `No data to combine`
  failure when combine is asked to consume a serial base file:
  <https://stackoverflow.com/questions/26214055/combine-python-coverage-files/26231950>.

## Source-superseded command capacity and retained failure evidence

The selected S98.4 native owner exhausted its 32 command pointers with historical
failed and timed-out invocations. A distinct locked-artifact acquisition was
refused before execution, even though its implementation source had changed.
Changing a label, run ID, timeout, notes or commit identity is not a repaired input.

The existing item-progress admission may retire only the obsolete slots needed
for its new reservation. Successful pointers retain their existing source or
runtime supersession rule. A failed or timed-out pointer additionally requires
a different actual source digest, matching native root/command identity, no
reservation, and a valid owner PID that is demonstrably inactive. Environment
changes alone cannot retire failures. Live, unknown, interrupted, missing,
malformed, symlinked and forged observations remain protected. The reservation
and pointer changes share the existing atomic write; its diagnostic follows
successful persistence. The 32-pointer/128-KiB limits and primary/support rules
remain unchanged.

Source review exposed an unnecessary retirement with 31 observations plus an
abandoned incoming reservation. Admission now compares the union of observations,
reservations and the incoming key against 32, so replacing an already-counted
reservation needs no retirement. Failed/timed-out exited-owner regressions retain
all pointers and receipt/log/trace bytes, require a fresh token, and emit no
retention-release diagnostic; overlap and genuinely new-key controls remain.
The bounded correction reproduces two failures with four passing controls first,
then passes all six focused and all 185 native progress/retention cases. Ruff and
strict typing pass; unchanged 85/75 coverage remains 95%, with retained native
audit `.gate-logs/coverage-20261015T065732Z.json`. Source re-review and the next
normal commit remain pending; this does not replace any earlier failed evidence.

Pointer retirement is not permission to delete its evidence. The existing
per-label retention scan, under its existing metadata lock, preserves every
unsuccessful record carrying native progress identity, together with its log
and trace, even after the active pointer is retired. Unknown progress identity
also cannot grant cleanup authority. Existing immutable run-name checks still
refuse reusing these artifacts. Ordinary unscoped retention and the existing
current-run/reference protections keep their original behavior.

Admission protects the latest selected context, as its existing same-command
replacement already does. Source A to B and back to A can admit a retired A
command; this is not an all-history failure-deduplication ledger. Once that
command fails in the current context, unchanged replay is refused again.
No second runner, cache, scheduler, inventory, automatic retry or waiver is added.
The existing scan still visits per-label receipts; protecting native failure
history can retain additional disk bytes. That history is not bounded by the
32 active pointers and needs the existing independently authorized cleanup
policy, rather than silent deletion or a larger admission cap.

Five failing-first cases reproduce full failed/timed-out inventory refusal after
a real source edit and deletion of unreferenced native unsuccessful artifacts.
Tests include actual exited native owners, both scoped and exempt observers at
retention one, byte-for-byte receipt/log/trace preservation, immutable run-name
reuse refusal, unchanged-input controls and atomic-write failure. Existing
record/prune orderings retain their shared lock coverage. These are focused
acceptance checks; full gate, exact-SHA hosted and release evidence remain open.

The focused consumer replay passes 231 cases with fatal warnings. Progress-owner
coverage passes 181 cases at 96.33% lines/91.04% branches; its native 85/75 audit
is `.gate-logs/coverage-20261015T003341Z.json`. The initial four-owner observer
roster passes 150 cases but fails aggregate branch coverage at 84.7%; retain
that failed audit, `.gate-logs/coverage-20261015T003437Z.json`. Including the
existing complete progress/retention cohort passes 331 cases at 93.06% aggregate
lines/86.88% branches, with every measured file above 75% in both dimensions;
the unchanged native audit is `.gate-logs/coverage-20261015T003738Z.json`.
Ruff and strict typing pass across all five Python files. These are neither
exact-candidate release proofs nor permission to ignore the retained scratch
diagnostic failure or delete its contents.

The exact invocations below append the same preexisting-toolchain Make variables
(no install or environment creation):

```text
UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never NPROC=1 GLUDD_XDIST_WORKERS=1
```

```text
make test-files TESTFILES='tests/unit/test_item_progress_retention.py tests/unit/test_observer_retention_concurrency.py' PYTEST_ARGS='-q -W error -n 0 -k "actual_source_change_releases_one_historical_failed_slot or native_unsuccessful_evidence_survives_without_an_active_capacity_pointer"'
make test-files TESTFILES='tests/unit/test_item_progress.py tests/unit/test_item_progress_retention.py tests/unit/test_observer_retention_concurrency.py tests/unit/test_observed_command.py' PYTEST_ARGS='-q -W error -n 0'
make lint-files FILES='scripts/item_progress.py scripts/stream_command.py tests/unit/test_item_progress_retention.py tests/unit/test_observer_retention_concurrency.py tests/unit/test_item_progress.py'
make typecheck-scope FILES='scripts/item_progress.py scripts/stream_command.py tests/unit/test_item_progress_retention.py tests/unit/test_observer_retention_concurrency.py tests/unit/test_item_progress.py'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_item_progress.py tests/unit/test_item_progress_retention.py' COVERAGE_CONFIG=config/coverage_item_progress.ini COVERAGE_REPORT=.gate-logs/coverage-native-command-capacity-progress.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
make coverage-files COVERAGE_TESTFILES='tests/unit/test_observed_command.py tests/unit/test_xdist_trace_diagnostics.py tests/unit/test_gate_refresh_streaming.py tests/unit/test_observability_guardrails.py tests/unit/test_active_work_status.py tests/unit/test_active_work_resource_observability.py tests/unit/test_observer_retention_concurrency.py tests/unit/test_item_progress.py tests/unit/test_item_progress_retention.py' COVERAGE_CONFIG=config/coverage_observed_command.ini COVERAGE_REPORT=.gate-logs/coverage-native-command-capacity-observer-complete.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
```

Practitioner reports about repeated Stop feedback describe nonproductive model
continuations when external inputs remain unchanged:
<https://github.com/openai/codex/issues/37937> and
<https://github.com/openai/codex/issues/34477>. They motivate preserving the actual
failure identity and changing an input before substantive retry; they do not
prove this Gludd inventory defect or authorize cleanup. This control-plane
change touches no serving process, listener, deployment or database and preserves
ZDD. Rollback restores the earlier admission/retention logic through normal
development flow while retaining failed evidence and all release requirements.


## 2026-10-18: task cleanup requires verified checkout ownership

The host-global task deadline remains audit context. Its oldest stale task never
supplies OS process authority. The legacy elapsed/name filter remains diagnostic;
the destructive boundary requires a fresh native PID/start/command record and a
live cwd equal to a registered checkout. Foreign, prefix-lookalike, unknown and
incomplete identities receive no signal. A proposed tree is refused if any
required child is unknown or newly attached.

The watchdog reuses the existing native observer inventory and process snapshot.
It protects the observer owner, child, descendants and upstream Make/UV wrappers.
A status/process display cap or live native observer absent from that inventory
refuses cleanup; display omission never grants authority. Native gate protections,
M2 focus markers, observer quiet/max limits and post-observer authentication remain
unchanged. Terminal pointers without a live owner do not disable ordinary owned
stale cleanup.

Each TERM/KILL rechecks start, command, cwd and protection through the existing
gate cleanup owner. Child-first TERM retains the five-second grace and bounded
KILL verification. A TERM-ignoring child remains owned after our own parent TERM
and confirmed parent exit reparent it; its original identity and cwd must still
match. Unrelated ancestry or newly attached children refuse cleanup. Userspace
checks reduce races; they do not make signalling atomic.

The already loaded daemon needs a native handoff. Preserve its original log,
PID/deadline/stale/kill evidence first. Run the existing stop owner in plan mode:

~~~text
make task-watchdog-stop TASK_WATCHDOG_VALIDATE_ONLY=1
~~~

This verifies the exact relative-argv Python daemon and matching UV launcher with
registered cwd and fresh start identities, without signals or writes. Root may
then apply the same owner with TASK_WATCHDOG_VALIDATE_ONLY=0. Refusal retains the
PID pointer and cannot claim successful handoff. Successful apply proves the old
tree fully drained before removing its unchanged pointer. Only afterward may root
use the existing task-watchdog-start owner once. No replacement launcher, daemon,
cache or enforcement disengagement is introduced. Fresh startup records
ownership_policy=registered-cwd-start-identity-observer-protection-v1.

Native failing-first artifacts retain five ownership/lifecycle failures and the
separate reparenting failure. The historical 67-case warning-fatal watchdog replay
passes at 91% branch-aware coverage. Seven further failures reproduce fresh or
malformed start identities and unavailable native inventories; one missing-start
control already refuses cleanup. Native start age must corroborate the unchanged
timeout, and subprocess inventory failures now return refusal without retries.
The final 76-case warning-fatal watchdog replay also passes at 91%, above unchanged
85% aggregate/75% per-file floors. Its separate report is
.gate-logs/coverage-task-watchdog-owned-cleanup-final.json.

The intermediate routing replay remains failed evidence: 207 passed and three
stale three-selector assertions failed. The additive repair keeps all 105 prior
families and exact selectors in order, adds one ownership family and one selector
to the third compatibility child, and retains three sequential children,
workers 2/1/1, 90/60 bounds, 21 phases/2850 seconds and authoritative full replay.
Final source acceptance passes all 405 cases in 57.28 seconds, including complete
gate cleanup, active-work, routing and cold-start controls; its receipt is
/tmp/gludd-v012-watchdog-owned-final-combined.xml. Exact-candidate full gate, hosted
CI and release promotion remain required.

The existing long-lived reports [pytest-timeout issue 159](https://github.com/pytest-dev/pytest-timeout/issues/159)
(2023) describe orphaned descendants after timeout; [psutil issue 2335](https://github.com/giampaolo/psutil/issues/2335)
(2023) explains why command text alone cannot identify a process. They support
retaining native tree ownership and fresh identity checks. They do not identify
Gludd's signal sender or authorize host-wide command-name cleanup. The preserved
native watchdog SIGTERM records remain the local incident evidence.


## 2026-10-18: confirm native handoff exit before clearing metadata

The first actual stop after source approval sent TERM to the verified old daemon
and then refused because tree ownership or native protection changed. The exact
triggering field was not logged. Later native inspection found both old daemon
and UV launcher absent, while the original PID pointer remained. Preserve this
failed stop; it does not prove that a zombie, cwd or command change caused it.

The existing strict reader still decides every signal. A separate wait-only
presence view keeps any occupied original PID pending even when command, start
or cwd metadata changes during exit. Its representative records never enter a
signal reader, sender or persisted observation. Any uncertainty revokes further
signals. The existing five-second TERM grace and one-second KILL wait remain;
a final successful complete native process table must show every original PID
absent. Missing cwd or changed command alone never proves death. Unknown children,
reused or foreign live identities and protected work retain signal refusal.

The existing task-watchdog-stop owner also recovers a stale pointer without any
signal. The pointer must remain a regular checkout-owned file with unchanged
content, device/inode and modification identity. Two successful complete native
snapshots must show its positive PID absent, with no matching daemon or UV
launcher in that checkout and no unlocated matching identity. Malformed, replaced,
symlinked, occupied or ambiguous pointers remain refused. Plan mode
(TASK_WATCHDOG_VALIDATE_ONLY=1) remains mutation-free; explicit actual mode
(TASK_WATCHDOG_VALIDATE_ONLY=0) may only clear that verified stale pointer.
Only the current stop PID with its exact native stop flags, checkout cwd and
start identity may be excluded from this absence check, together with its
connected, verified UV launcher. Both identities are rechecked in the next native
snapshot. Other --stop text, extra flags, unrelated launchers or uncertain
identities do not qualify. The owner never starts a replacement.
Root must preserve old evidence and finish
the verified handoff before using the existing startup owner once.

Eight failing-first controls include the actual native UV/Python fixture and
routing failures (/tmp/gludd-v012-watchdog-exit-handoff-red.xml). The first
implementation replay remains failed evidence: 100 passed and the real fixture
failed because its test parent had not reaped its exited UV child. The fixture now
owns a bounded concurrent wait/reap, preserves real ps/cwd/start reads and uses
only an isolated checkout, script and PID file. It changes cwd after TERM, exits
naturally and proves both native PIDs absent. It creates no environment or lock
and touches no main daemon, PID or global task file. All 99 warning-fatal watchdog
cases pass at 92% branch-aware coverage, retaining the 85/75 floors, in
.gate-logs/coverage-task-watchdog-exit-handoff.json.

The first combined source/routing replay passed 435 cases before the CLI-self
control was added; it remains intermediate evidence. Four additional failures and
six refusal controls reproduce the stop CLI self-blocker. The confined real CLI
now passes both plan and actual recovery, and three fresh-snapshot tamper cases
retain refusal. Final whole-owner coverage passes 112 warning-fatal cases at 92%
(.gate-logs/coverage-task-watchdog-exit-handoff-final.json), with native audit
.gate-logs/coverage-20261018T095151Z.json and raw observer run
20261018T095145Z-28823-f926bbae.

One cheap deterministic handoff-exit node is appended to the existing third
admission child. All 106 previous families/selectors/order remain, with three
sequential children, workers 2/1/1, 90/60 bounds and 21 phases/2850 seconds.
Final combined source/routing acceptance passes all 448 cases in 59.91 seconds
(/tmp/gludd-v012-watchdog-exit-handoff-complete-final.xml). Full gate, exact-SHA
hosted CI and release promotion remain separate required proof.

The first-hand [psutil issue 783](https://github.com/giampaolo/psutil/issues/783)
(February 2016) reports macOS status appearing running while name/cmdline access
raised ZombieProcess. This historical report supports separating metadata failure
from confirmed PID absence; it does not establish the exact Gludd failure cause
or a defect in the current dependency. The long-lived
[psutil issue 2335](https://github.com/giampaolo/psutil/issues/2335) (December 2023)
records command-line presentation and interpretation ambiguities.
The maintained [Python subprocess wait documentation](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen.wait)
assigns child waiting to its parent. These sources inform conservative identity
handling and fixture ownership; neither proves the unlogged field in the actual
Gludd refusal or authorizes a signal. ZDD, native observer limits, cleanup
authority and all prior failed evidence remain unchanged.


## 2026-10-18: acknowledge native detached watchdog startup

The first actual startup after handoff returned zero and printed UV PID 47780.
Six seconds later native inspection found it absent, with an empty new log and
the unchanged nine-record kill history. Root retained
/tmp/gludd-v012-watchdog-handoff-20261018/failed-start-7a1ddbe.* and used the
approved absent-pointer recovery afterward. The cause of that exit is unproven.
A shell background PID and a successful Make return do not acknowledge a daemon.

The existing lifecycle owner now reuses the gate's detached native launch pattern:
one checkout-local singleton admission lock, the selected sys.executable directly,
stdin closed, append-only output and a separate child session. PYTHONUNBUFFERED=1
keeps the exact interpreter-plus-script role accepted by native stop. The child
exclusively creates its root-local PID pointer and flushes its own PID-bearing
startup line. Inherited PID/log overrides cannot redirect that publication.
Startup polls for at most five seconds; success requires the new log bytes, its
actual child PID/start/cwd/script identity and PGID=SID=PID, revalidated in a fresh
native table with unchanged pointer/log ownership. Historical log text never
acknowledges a new child.

Already-running admission verifies a live singleton and unchanged pointer
content/device/inode/mtime through two censuses. It does not depend on retaining
an old startup line in a bounded log window. Stale, foreign, symlinked or unknown
pointers block startup; only the separate native stop recovery owner may retire
verified absent metadata. Exact current-action/self/connected-UV exemptions retain
fresh identity checks and never exempt arbitrary start/stop text. Failed startup
returns nonzero and retains diagnostics; only a positively verified newly created
child may receive cleanup signals. Parent-owned child polling reaps it while the
unchanged five-second/one-second native drain checks confirm actual PID absence.
Unknown identities remain refused and cannot authorize a retry or a duplicate.

TASK_WATCHDOG_START_VALIDATE_ONLY=0 preserves established actual-start behavior.
Literal 1 performs a plan with no writes, spawn or signals:

~~~text
make task-watchdog-start TASK_WATCHDOG_START_VALIDATE_ONLY=1
~~~

The watchdog-auto task branch explicitly calls this same start owner with 0.
Stop keeps its separate default-plan TASK_WATCHDOG_VALIDATE_ONLY=1 contract.
Unsupported detached-session platforms refuse startup before mutation; the module
adds no unconditional POSIX-only import. No environment is installed or created.

The initial six-case RED includes a fixture constant error; after correcting that
fixture, the real Make caller reproduces rc0 with unacknowledged UV PID 53520.
Seven pointer/log-retention/contract/routing failures and two new-publication
replacement failures are retained separately. The realistic native fixture
acknowledges a detached daemon, terminates the caller process group, verifies the
same daemon identity survives, and drains it through the strict native stop owner.
All deadline, stale, killed, PID and log paths remain fixture-local.

The earlier 142-case coverage pass remains intermediate at
.gate-logs/coverage-task-watchdog-native-start-final.json, native audit
.gate-logs/coverage-20261018T101037Z.json and raw observer
20261018T101030Z-60037-7b143f79. Earlier 134/141-case receipts and the 604-case
combined pass remain historical. Actual Make execution then exposed expansion of
raw command-line policy values while constructing recipe environments. The
one-case, two-case and three-case direct/recursive RED XML receipts are retained;
each creates only an isolated tripwire and rejects invalid CLI input before any
lifecycle operation. Both raw policy variables are now explicitly unexported.
The internal overridden argument still admits only exact literal 0 or 1. Direct
start, direct stop, recursive start and the promoted acknowledgement control pass
all four bounded checks. Both documented native plan examples remain mutation-free.

Final warning-fatal whole-watchdog acceptance passes all 145 cases in 5.76 seconds
with 93.2% line and 86.8% branch coverage, preserving the native 85/75 floors:
.gate-logs/coverage-task-watchdog-native-start-policy-final.json; native audit
.gate-logs/coverage-20261018T101658Z.json; raw observer
20261018T101651Z-69005-8a261a10, passed in 7.478 seconds. Its JUnit receipt is
/tmp/gludd-v012-watchdog-native-start-policy-final-coverage.xml. Final combined
source/routing acceptance passes all 607 cases in 81.08 seconds:
/tmp/gludd-v012-watchdog-native-start-policy-final-combined.xml.
The complete cleanup, gate-launch, active-work, scheduling, promotion, cold-start
and observability caller owners retain their original assertions; the exact M2
command-layer control also passes. One exact cheap acknowledgement control is
appended to the third admission child, preserving all 107 previous families/order
and three children, workers 2/1/1, 90/60 and 21 phases/2850 seconds.

The first-hand [uv issue 12830](https://github.com/astral-sh/uv/issues/12830)
(April 2025, Darwin/uv 0.6.12) describes wrapper/child signal-handling surprises and
orphaned Python processes; maintainers closed it with signal-forwarding changes.
It supplies historical context, not a diagnosis of this local empty-log failure
or a claim about the current dependency. Python's maintained
[subprocess documentation](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen)
defines the new-session behavior. The existing native gate session-survival test
and the new real watchdog fixture provide local acceptance. ZDD, observer bounds,
M2/M4/M5 refusals, cleanup authority, release gates and all failed evidence remain.

Root alone may apply the reviewed source, run the safe start plan, start once,
then verify the same native identity after the launching tool exits and at least
one normal polling interval. Logs and killed history must remain intact before
any candidate proof starts. Source acceptance does not close a release task.

## 2026-10-18: serialize watchdog startup through publication and drain

Independent review of source 080158abec83b0c89dbebe5982ea91c5b19af96f requests
changes despite its passing focused receipts. It found three connected windows:
actual existing-singleton success before the launch lock, failed-child drain after
that lock exited, and PID publication before checked readiness. That unsafe M6
commit and its evidence remain preserved and unapproved. Review the cumulative
repaired descendant against M5 7a1ddbe1e73599a7823832e83d4b03e191a890c4.

The existing lifecycle owner now acquires its checkout-local FileLock before every
actual admission or success. An ExitStack keeps that same lease through fresh
acknowledgement, verified failure cleanup/reaping and the final failure diagnostic.
A concurrent actual adopter refuses while creation or cleanup owns the lease.
Read-only plan admission remains mutation-free; actual already-running output
reports no spawn/signals without making a claim about lock bookkeeping.

The child writes its PID-bearing ready line through checked existing-log output,
flushes and fsyncs it, then exclusively creates its final positive raw PID pointer.
Failed readiness cannot publish that pointer. The creator still requires both new
ready and started log bytes plus the final pointer, revalidated native identity,
detached session and unchanged pointer/log ownership. If a creator dies before
ready, the live unpointed child blocks adoption through the native census. A
later verified singleton remains independent of historical log retention because
its final pointer commits only after checked readiness. Ordinary diagnostic file
logging remains fail-open. No second readiness cache or cleanup authority is added.

Two real-lock/cleanup controls fail first in 1.49 seconds;
three readiness/publication REDs fail in 2.43 seconds, including a real detached
child whose creator is terminated before ready and which the old source wrongly
adopts. The fixture is subsequently released and drained through strict native
stop with deadline/stale/killed/PID/log state confined. Two additive routing REDs
fail in 1.72 seconds. These receipts remain under the distinct
/tmp/gludd-v012-watchdog-start-serialization-lock-red.xml,
/tmp/gludd-v012-watchdog-start-serialization-publication-red.xml and
/tmp/gludd-v012-watchdog-start-serialization-routing-red.xml paths. Seven repaired
controls pass in 2.28 seconds. These transaction findings do not establish the
cause of the original PID 47780 empty-log exit.

Final warning-fatal whole-watchdog coverage passes 150 cases in 6.77 seconds at
94.1% lines and 88.3% branches, preserving native 85/75 floors:
.gate-logs/coverage-task-watchdog-start-serialization-final.json; native audit
.gate-logs/coverage-20261018T103257Z.json; raw observer
20261018T103249Z-97046-184e80f2 passed in 8.410 seconds. Its JUnit receipt is
/tmp/gludd-v012-watchdog-start-serialization-final-coverage.xml. Final complete
source/routing acceptance passes 619 cases in 68.22 seconds at
/tmp/gludd-v012-watchdog-start-serialization-final-combined.xml. The prior complete
cleanup, gate-launch, active-work, scheduling, promotion, cold-start and observability
caller owners, plus the exact M2 control, remain included with every original
assertion, Make literal/unexport guard, refusal, native wait and observer boundary.

Only the exact serialized-acknowledgement node/family joins the third admission
child. All 108 prior families, selector order and non-family policy remain exact;
the total becomes 109. Three sequential children, workers 2/1/1, 90/60 admission,
21 phases/2850, full authoritative replay, ZDD and cleanup authority remain.

The long-lived [filelock issue 27](https://github.com/tox-dev/filelock/issues/27)
discussion includes a 2019 explanation of split-inode races when Unix lock paths
are unlinked. The first-hand Darwin reproducer in
[issue 574](https://github.com/tox-dev/filelock/issues/574) (June 2026) revisits
that behavior. These reports support retaining the existing native lease and
avoiding a custom lock-file cleanup path; they are not a diagnosis of our three
source-level transaction windows or a claim about the current dependency.

Root owns cumulative exact-source review, integrated release-proof admission,
live startup/handoff, candidate focus, full gate and hosted release proof. This
child repair creates no approval for unsafe M6 and closes no release checkbox.

## 2026-10-18: isolate unit watchdog diagnostic logs

Frozen candidate B b9d7bf9fe7e1e8e4882d4e821947478a5ad5fadd completed its
authenticated native focus: 2202 passed, two conditional skips, 341.81 seconds;
the observer completed in about 345 seconds and the native outer command returned
zero. Preserve /tmp/gludd-v012-focus-b9d7bf9 and the complete original bundle
/tmp/gludd-v012-proof-2480122. This successful focus is not full-gate or hosted
release evidence and is not rebound to this later test-only source.

B's trace attributes the two single POLL ERROR lines at 2026-10-18T10:56:53Z to
worker 46197 executing the deliberately faulted TestRunOnce inventory-failure and
unexpected-read tests. Their exact injected errors are git unavailable and
unexpected read failure. The tests emitted them through the real logger because
only some fixtures patched WATCHDOG_LOG. They do not establish a live-daemon
runtime failure. Root preserved the healthy PID 29001 observation and unchanged
nine-record kill history; this enhancement takes no daemon action.

A module autouse fixture now sets the in-process WATCHDOG_LOG constant to each
test's tmp_path/watchdog.log through pytest monkeypatch. Existing local overrides
remain intact and monkeypatch restores the original value after each case.
Production logger code, printed diagnostics, every original assertion/fault string
and all real-child fixture environments remain unchanged. Logging continues;
its unit-test artifacts belong to the existing native run and test namespaces.

The cheap regression first requires exact fixture-local ownership before either
fault can execute. Its final RED fails safely at that precondition in 0.99 seconds;
the earlier 1.49-second ownership receipt remains intermediate. Two routing REDs
fail in 1.22 seconds. After isolation, both deliberate errors are present in the
local file while original zero-result/no-scan/no-signal behavior remains. All five
focused checks pass in 1.72 seconds, including the two original fault cases.

Final warning-fatal whole-watchdog coverage passes 151 cases in 6.50 seconds at
94.1% lines/88.3% branches, retaining native 85/75:
.gate-logs/coverage-task-watchdog-log-isolation-final.json; native audit
.gate-logs/coverage-20261018T110346Z.json; raw observer
20261018T110338Z-80710-385dbf75 passed in 8.106 seconds. Its JUnit receipt is
/tmp/gludd-v012-watchdog-log-isolation-final-coverage.xml. The three whole owners,
test_task_watchdog.py, test_runtime_admission_scheduling.py and
test_gate_failure_promotion.py, pass all 275 cases in 35.91 seconds:
/tmp/gludd-v012-watchdog-log-isolation-final-owners.xml.

The exact namespace control is appended as family 110 in the existing third
admission child. All 109 prior families, non-family policy, selector order,
three sequential children, workers 2/1/1, 90/60, 21 phases/2850, contracts 245,
authoritative replay, warning policy, authentication, ZDD and cleanup remain.

The first-hand [pytest issue 11790](https://github.com/pytest-dev/pytest/issues/11790)
(January 2024) describes temporary-path collisions across concurrent independent
pytest invocations; maintainers explain the need for distinct basetemp paths.
This feature reuses the existing Make invocation namespace and per-test tmp_path,
without another temporary-directory owner. That historical report supplies
namespace context, not a claim that a pytest dependency defect caused B's logs.

The frozen B proof, previous SESSION bytes and existing TASKS statuses/checkboxes
remain preserved. Root owns independent review, integrated admission/focus, full
gate and hosted proof. No production edit, logging suppression, daemon restart,
gate, CI, push or release-completion claim is made by this test-only enhancement.

## 2026-10-19: positively retain a recovered checkpoint owner

The normal publication-policy source commit first refused owned checkpoint
90142d0d8e82af1978fe24eaf4a155ae1429a8e4 because its archived checkout was
no longer a live registered worktree. Root recovered the genuine original
branch and canonical path at f4cfa5c, then the native checkpoint guard verified
all four retained checkpoints with zero unowned entries. Recovery and the later
34741f6 guarded source commit remain historical evidence. A fresh activity lease
alone does not promise retention after completion or lease expiry.

The existing prune owner now exposes agent-worktree-retain. Required inputs
BRANCH, WORKTREE and WORKTREE_RETAIN_REASON identify one exact linked checkout;
WORKTREE_RETAIN_VALIDATE_ONLY defaults to literal 1 and accepts only 0 or 1.
Its dependency-free SYSTEM_PYTHON invocation uses -B, without UV or environment
provisioning. This safe behavioral example starts no native Git child, writes nothing and
discloses native_ownership_verified=0 even though the requested path is absent:

~~~text
make agent-worktree-retain BRANCH=agent-retention-contract WORKTREE=/tmp/gludd-retention-absent WORKTREE_RETAIN_REASON=checkpoint-owner WORKTREE_RETAIN_VALIDATE_ONLY=1
~~~

Explicit 0 requires the invoking repository's native unredirected identity and
reuses checkpoint registered-root, common-directory and backlink checks.
It verifies the exact canonical linked checkout and branch twice before native
git worktree lock --reason, then verifies the same root/backlink identity and
lock using fresh inventory. Native retention Git calls have a 30-second bound.
The existing pruning call default is unchanged. Foreign, missing, main,
symlinked, replaced or changed registrations refuse; failures remain fatal.
Make inputs are transported as literal data; dollar-containing Make expressions
are rejected before recipe expansion. No arbitrary command or path executor,
activity renewal, checkpoint mutation or administrative-file write is added.

An already locked owner is verified without rewriting its lock. The receipt's
reason is the caller-requested reason; already_locked=true does not claim that
text is the observed existing lock reason. The native regression preserves the
original lock bytes, inode and modification time. Native lock protection applies
to existing automatic cleanup and safe pruning. Explicit authorized destructive
owners can still unlock/remove, so retention is not permanent deletion immunity.
Future checkpoint reconciliation and deliberate removal stay separate actions.

Real native Make controls fail first, then exercise plan, apply, exact quoted
path/reason, hostile inputs, ownership refusal, preserved locks and failed fresh
verification. Real clean/integrated checkpoint-owner fixtures prove both an
initial lock and one arriving at cleanup refresh retain tree/cache/branch/
checkpoint bytes while an eligible unlocked sibling actually retires. Existing
prune/cleanup assertions, registry/grace/process guards, ZDD and 85/75 line and
branch floors remain. No live recovered owner is locked by these tests.

The [Git worktree manual](https://git-scm.com/docs/git-worktree) documents native
lock/reason and automatic pruning/removal protection. The firsthand
[Recover Pruned Worktree report](https://stackoverflow.com/questions/53711126/recover-pruned-worktree)
describes a 2018 registration loss during a network outage; a 2025 response
recommends native locking. It motivates durable retention and careful recovery,
not a network diagnosis for Gludd or authority to reconstruct Git metadata.
Root owns the actual recovered-owner retention decision, integration and later
gate/hosted/release proofs. All 19 task checkboxes remain open.

## 2026-10-19: explicitly retire one merged repair worktree

Three safe cache reclamations left actual disk usage at96% against95%. Native
shared-UV diagnostics retained active-owner refusal. Several completed source
workstreams had already been explicitly unregistered; the automatic lifecycle
owner correctly lacked their positive completion leases. Registry absence
alone cannot supply that proof. Do not re-register/backdate ownership, bypass
UV owners or retry final proof merely because HEAD or elapsed time changes.

The existing wt-prune-safe owner adds an explicit single-owner mode.
WT_PRUNE_RETIRE_MERGED defaults0;1 selects literal BRANCH and canonical absolute
WORKTREE. WT_PRUNE_RETIRE_VALIDATE_ONLY defaults1 and accepts0|1.
ACTIVE_WORKSTREAM_REGISTRY selects the existing registry; WT_PRUNE_VALIDATE_ONLY
retains its original meaning in default mode. Every variable is documented in
help and config/make_target_contract.json. The original default call remains.

The native mode reuses registered-root/backlink identity, trusted integration
points and real ancestry, process census, validated checkpoint inventory and
the existing automatic evidence archival/rollback/removal owner. It requires
clean exact source/branch/HEAD, approved namespace and no active logical lease,
Git lock or process. Unknown checkpoint authority refuses; a verified sibling
checkpoint remains preserved and does not falsely own the selected checkout.
Only this explicit mode distinguishes its own exact Python/selected-UV/Make
launch chain from other active users. Existing psutil supplies lossless argv,
cwd,UID,parent and creation time; every controller must match the selected
source/request and remain outside the target. Every fence compares fresh
identity. Unknown/reused/changed identity, target-cwd ancestors and unrelated
live children still refuse. The original global process scan is unchanged.
The Make mode uses exec to avoid an extra launcher; default mode is unchanged.
An actual failed probe found Apple's Make shim selected a different executable,
so this mode reuses start_gate_background's existing native executable selector;
the shell hypothesis was not proven and no path/name fallback is added.

Before actual removal it repeats ownership and ancestry fences after evidence
archival, rejects newly appeared proof/unknown ignored data, and lets the
existing owner restore archived evidence if Git refuses. All refs and commit
objects survive; no force, unlock, raw deletion or Git-metadata reconstruction
is added. Default automatic lease/grace/cleanup authority remains unchanged.

Use explicit plan1 for the actual verified merged owner before a deliberate
plan0 apply. A plan starts native inspection and can refuse; it is not removal,
capacity clearance, admission or release evidence. The documented absent-path
example intentionally refuses rc2 without mutation:

~~~text
make wt-prune-safe ACTIVE_WORKSTREAM_REGISTRY=/tmp/gludd-active-workstream-contract.json WT_PRUNE_VALIDATE_ONLY=1 WT_PRUNE_RETIRE_MERGED=1 WT_PRUNE_RETIRE_VALIDATE_ONLY=1 BRANCH=contract/retirement WORKTREE=/tmp/gludd-worktrees/contract-retirement
~~~

Root ran that exact example through run-watched before and after fixing the
facade's recipe layout; both retained the honest native ownership refusal.
Initial target-contract checks could not inspect variables after conditional
directives inside a recipe. Root changed only recipe construction to one
native Make conditional-argument call; the original checker stayed unchanged
and passes254 contracts. Full native Make literal/default/hostile controls and
complete source-owner evidence belong to the frozen source handoff; no live
retirement or reclaimed-byte claim is supplied by this source description.

Initial311 complete original workstream/checkpoint/new-owner acceptance passes
164/95.28s, observer97.4rc0. The original include scopes with subprocess coverage
measure aggregate93.1%line/85.6%branch; prune93.4/87.9, checkpoint90.2/81.7 and
registry98.9/95.0, passing original85/75 independently for BOTH metrics. This
historical receipt precedes a typing-only new-test correction and subsequent
independent source finding; it is not relabelled as the corrected source proof.
The unchanged archive helper is not claimed fully covered by that scope.

Actual first312 whole replay retains16FAIL/148PASS99.43s/native101.7rc2.
All16 fail before policy with ModuleNotFoundError:scripts: required outer
PYTHONPATH=checkout/src command-line transport overrides fixture source paths
set only in child environment. The existing _retain_make helper and new facade
must bind their already-selected source path explicitly in child Make argv;
all original assertions, selected runtimes and inherited carriers remain.

Independent review also found that repeated -f sources could overwrite the
first observed Makefile path, allowing foreign-first/approved-last input past
that source predicate. The correction requires at most one explicit source,
with failing-first pure-controller and real-Make refusal before archival or
removal. A further confined native control reproduced the same gap through
explicit/inherited MAKEFILES: it read the harmless foreign marker and removed
only its fixture. The existing validator now refuses that extra source
authority while preserving MAKEFLAGS/MAKEOVERRIDES. Original two MAKEFILES
controls fail first; five finite controls then pass, including original
duplicate-source refusal and the recognized positive chain. GNU's
[MAKEFILES contract](https://www.gnu.org/s/make/manual/html_node/MAKEFILES-Variable.html)
documents those additional inputs; the indexed primary manual was read after
the alternate direct URL timed out. No custom flags parser is introduced.
Preserve the original CHANGES REQUIRED decision and every failed run.
Corrected complete coverage/runtime results, exact commands and original
failures are retained at /tmp/gludd-v012-worktree-retirement-report.md.
Root's facade index is /tmp/gludd-v012-worktree-retirement-root-facade-evidence.md.
Source acceptance supplies no final candidate/gate/hosted proof.

The corrected frozen source passes168whole cases/zero skips on311(77.46s,
native79.328rc0) and312(74.36s,native76.6rc0). Fresh311 subprocess coverage run
20261019T201859Z-50747-3df4603e measures aggregate93.1%line/85.8%branch,
prune93.6/88.3, checkpoint90.2/81.7 and registry98.9/95.0. All three pass75/75
and aggregate85/85; the unchanged archive helper is outside this measured scope.
The original164-case coverage, first failed312 run, repeated-source RED and
MAKEFILES RED are retained separately. Independent corrected-source review,
normal guarded commit and genuine integration still precede any live retirement.

Research revisited the [Git worktree manual](https://git-scm.com/docs/git-worktree)
and firsthand [2018 recovery report](https://stackoverflow.com/questions/53711126/recover-pruned-worktree)
on2026-10-19. The report describes accidental registration pruning while a
network drive was temporarily unavailable; its2025 reply recommends locking.
Git documents clean native removal and lock protection. This historical
experience supports preserving native registration/locks and explicit owner
selection, not a Gludd network diagnosis or permission to rebuild metadata.

The firsthand [chroxy5244 report](https://github.com/blamechris/chroxy/issues/5244)
on2026-06-06 also reproduces ignored local-data loss under ordinary clean
status and no-force native removal on Git2.51.2; downstream5249 closed it.
Root read this report on2026-10-19. It corroborates inspecting ignored data and
retaining existing archive/unknown-data refusals; it supplies no Gludd capacity
proof, upstream defect diagnosis or expanded cleanup authority.

All19release tasks remain open.85/75 BOTH coverage, fatal warnings, ZDD,
checkpoint retention, capacity95%, final admission, full gate, exact-SHA hosted
proof and promotion authority remain required. The future0.1.2 completed-item
preparation queue follows these pipeline repairs under the user's sequence.

The same repair push supplied a concrete scheduling boundary: all2003 runtime
assertions passed while a separate164-case coverage run overlapped, but the
original90-second publication observer timed out90.6s and prevented transport.
After both owners ended, make ps showed only the namespaced watchdog. Holding
further expensive checks let the unchanged90/60 owner pass73.554s; normal
pre-push checks and exact development512eaf9 transport then passed. Keep both
native receipts; this local result is not a hosted throughput improvement.
Before bounded publication/final proof, use make ps and logical workstream
ownership, finish competing test/coverage owners, and reserve that proof window
until its native terminal state. Read-only review/model work may continue.
Coordinate through existing Make/workstream owners; no new lock, scanner,
runner, daemon, cache, extra heartbeat or widened deadline is introduced.

## Native alias plan and apply must bind the same request (2026-10-20)

An actual selected-owner retirement plan at published development6161e102eee998bf94407457ad3dde5d0e4ad459 exposed a mode-binding defect. The first plan correctly refused a noncanonical /tmp path; the native registered identity is under /private/tmp. The next plan correctly refused a still-registered logical workstream. Root released only the explicitly completed, integrated and published leases through workstream-unregister. No cleanup authority came from registry absence alone.

After those prerequisites changed, agent-cleanup AGENT_CLEANUP_VALIDATE_ONLY=1 still refused with "retirement Make argv or source does not bind selected owner". The Python request carried --retire-validate-only1 while its controller validator required Make's alias mode0. Native positive registered-owner plans were missing from the earlier proof: an absent-path refusal and static argv checks could pass without exercising this valid path.

Ten additive controls retain the original23 preservation controls. Their initial native RED is6FAIL/4PASS/23deselected3.66s, observed4.9s/rc2. Both real registered-owner aliases reject valid plan1; the inverse mismatch, Pythonplan1/Makeapply0, is also accepted incorrectly. The existing Pythonapply0/Makeplan1 refusal remains an unchanged assertion. The repair compares Make's literal policy with the parsed native request mode. It preserves approved executable/source/argv/UID/CWD/creation-time identity, controller count, Make source restrictions and every ownership/ancestry/archive/rollback/ref/checkpoint fence.

A complete initial311 replay retains10FAIL/220PASS59.86s, observed61.7s/rc2. All33 preservation cases pass; ten unchanged cleanup-target fixtures fail before their child starts because mutating Make is launched in a plain temporary directory. The existing confinement guard requires a canonical isolated pytest-owned Git repository. The fixture correction reuses the existing native Git helper to initialize that repository before configuring fake Git/recorder PATH. All30 original parameter cases, argv/status/tripwire assertions and the production guard remain. A local helper-name collision retains30FAIL/1.10s; aliasing the native helper preserves the original fake-Git variable.

Separate fixture prerequisite d69c4d6c113b89f934a1e1271c591f6ba91d7d1e passes all30 cases on311(2.45s/native3.4s) and312(3.30s/native4.6s), Ruff and strict typing. Its normal guarded collection reports127848/127872,24deselected,0errors50.15s/native55.8s including the unstaged additive product controls; it is not an immutable fixture-only acceptance claim. Full source commands and failures remain /tmp/gludd-v012-retirement-alias-plan-6161e10-report.md.

Root owns documentation and receiving integration; release_note_integrity produces separate fixture d69c4d6c113b89f934a1e1271c591f6ba91d7d1e and product56d76bc75d24b6119574680b121563e18bbe550a on codex-v012-retirement-plan-6161e10-20261021 in the reused pipeline-status checkout. Complete230 cases pass on311(64.24s/native65.9s) and312(67.20s/native69.4s), zero skips. Unchanged config/coverage_workstream_prune.ini measures aggregate93.5line/85.8branch, prune92.0/84.4 and registry98.9/95.0;85/75 BOTH pass. Source audit is .gate-logs/coverage-20261021T024551Z.json. Normal product commit freshly collects127848/127872,24deselected,0errors45.63s/native52.3s. Native source state is clean; Git serializer returns to Root and the model owner is quiescent. That historical source lease was released only after producer completion and genuine receiving integration; its actual native retirement/evidence archive is recorded below. Independent exact-source approval is /tmp/gludd-v012-retirement-plan-source-review.md. Root's guarded no-commit merge receives both source commits without conflicts; complete receiving230PASS43.15s/native44.8s retains the same whole-file metrics in audit .gate-logs/coverage-20261021T025055Z.json and full report .gate-logs/coverage-retirement-alias-plan-receiving-56d76bc.json. Receiving Ruff3 and all three separate complete canonical strict scopes pass; eight complete documentation/tracking/incident owners pass110/3.96s. At that source-proof checkpoint, receiving commit/collection and actual retirement were pending; the subsequent0650/98de receipts and actual native retirement are recorded below. A distinct plan for agent-v012-gate-admission-resume-20261019 correctly refuses "git worktree lock protects this owner"; no lock is discarded.

For later selected-owner cleanup:

1. Resolve exact branch, clean HEAD and canonical path through native worktree-state/all-worktree-state. Retain dirty/unknown work and protected checkpoints.
2. Confirm genuine native integration, completed logical/model production and absence of live users. Keep publication and Root's milestone proof obligations recorded separately; the unchanged native owner decides checkout eligibility. Release only that completed source lease through workstream-unregister. The earlier broad publication prerequisite in operating prose exceeded this owner's actual contract; no production guard is changed.
3. Run documented selected-owner plan1 through agent-cleanup or wt-prune-safe with the real canonical path and existing registry. A successful plan must leave the target, ignored evidence, branch refs and registry unchanged.
4. Only then use literal apply0 through the same owner. Actual application retains evidence archival, fresh fences and rollback. Preserve refs and checkpoints; no force removal or metadata reconstruction.
5. Measure disk again. A plan, a removal or a new HEAD does not itself prove the95% capacity prerequisite.

The prior Git manual and firsthand2018/2026 ignored-data/recovery reports above remain relevant to preservation. They provide no diagnosis of this internal mode mismatch and no expanded deletion authority. Source acceptance is separate from actual retirement, capacity, final admission, full gate, exact-SHA CI, deployment and all seven open release tasks.

## Retired legacy force-prune entrypoint (2026-10-20)

During the next native cleanup review, Root found wt-prune-force-merged at
make/30-ci-and-release.mk:861. The unregistered, unused recipe enumerated all
linked checkouts and force-removed master ancestors using only two pathname
exceptions. It bypassed ignored-proof archival and native lock, checkpoint,
process and logical-owner authority. Its sole external reference was the old
target-existence audit in docs/audit/status_infra_quality.md. That historical
audit is preserved with a dated current-procedure correction.

Two additive cases reuse the complete existing registered native Git fixture.
They add an actual master integration ref, execute the original production
Make fragment in ordinary Make and -e, and require unchanged target, ignored
failure/history bytes, refs, registration, registry and archive. The original33
preservation cases and all30 routing assertions remain unchanged.

The actual RED is2FAIL/33deselected1.42s, observed2.4s/rc2:
both routes print "removed (merged HEAD)" for the confined fixture and destroy
its ignored proof. No live project checkout executes that legacy recipe.
Retain /tmp/gludd-v012-legacy-force-alias-red.log and matching .xml.
Removing the unused recipe makes the same calls refuse natively with
"No rule to make target"; both controls pass1.66s, observed2.8s/rc0.
Retain /tmp/gludd-v012-legacy-force-alias-green.log and matching .xml.

Supported cleanup remains the existing agent-cleanup/wt-prune-safe native owner.
No alias, force option, parser, observer, dependency or cleanup exception is
added. A historical existence verdict cannot authorize bypassing current policy.
Native contract validation passes269 targets; duplicate detection now reports
1166 definitions/zero duplicates. lint-make passes9CI/12local phase parity and
the actual Make help parse. The documented absent-owner agent-cleanup plan
correctly refuses rc2 at canonical linked-checkout identity; it grants no
valid-owner, removal or capacity proof.

Five complete owners pass232 cases on311 in53.08s, observed55.2s/rc0, and
on312 in40.15s, observed42.3s/rc0, with fatal warnings and no skips.
Unchanged config/coverage_workstream_prune.ini includes both whole production
files:93.5line/85.8branch aggregate, prune92.0/84.4, registry98.9/95.0.
85%aggregate/75%each-file BOTH pass. Native audit is
.gate-logs/coverage-20261021T025645Z.json; full measured report is
.gate-logs/coverage-legacy-force-alias-0650c12.json.
Ruff and complete strict preservation typing pass; the full Make contract test
owner passes11/2.35s, /tmp/gludd-v012-legacy-force-contract-owner.xml.
The original230-case receiving repair is separately committed in
0650c124f7246736dcac52da84be4ce25b909eaf after fresh127848/127872 collection,
24deselected/zero errors48.65s, native53.9s. No old failure is relabeled.

The complete current coverage command is:

~~~console
make coverage-files COVERAGE_TESTFILES='tests/unit/test_workstream_prune.py tests/unit/test_worktree_retirement.py tests/unit/test_worktree_prune_preservation.py tests/unit/test_worktree_cleanup_targets.py tests/unit/test_pytest_param_fixture_collisions.py' COVERAGE_CONFIG=config/coverage_workstream_prune.ini COVERAGE_REPORT=.gate-logs/coverage-legacy-force-alias-0650c12.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

The312 whole-owner replay uses the same five TESTFILES with -q -W error -n0
through public run-watched,180total/60quiet,15sheartbeat/20retained,
WORK_ITEM_ID=S98.4, labelv012-legacy-force-alias and
runv012-legacy-force-whole312-0650c12-20261021.
Both outer and child select the installed python-3.12 environment and
UV_PYTHON=3.12.15/UV_NO_SYNC=1/UV_PYTHON_DOWNLOADS=never.
Raw /tmp/gludd-v012-legacy-force-alias-whole312.log and its .xml retain proof.

A distinct canonical plan for codex-v012-pages-cold-render-20261020 correctly
refuses "unmerged worktree cannot be retired" after verifying all six
checkpoints/unowned0. Keep that source/ref and investigate its integration
history; implementation-looking bytes do not establish native ancestry.
The old gate-admission owner remains Git-locked. No live removal, reclaimed
bytes, capacity clearance, full gate, exact-SHA hosted/deployment, promotion or
release-task closure follows from these repairs.

## Actual completed-owner retirement and evidence location (2026-10-20)

After source56d76bc and its separate d69 fixture prerequisite are genuinely
merged into0650, all required source/receiving acceptance and normal commits
are terminal. The source model also finishes its concrete original-Pages
decision; it is quiescent. Root explicitly releases only that completed
codex-v012-retirement-plan-6161e10-20261021 lease through the existing native
workstream-unregister owner. Root retains the release milestone/proof ownership.

The canonical selected-owner plan now returns status:planned,
validate_only:true,native_ownership_verified:1 at exact56d76bc, preserving
all six checkpoints/unowned0. The same owner in literal apply0 then repeats
identity/ownership/ancestry fences, visibly archives ignored evidence and
natively removes only that selected clean materialization. Terminal return0
reports status:removed,validate_only:false,native_ownership_verified:1.
No force, unlock, branch deletion or policy exception is used.

Actual native manifest, read back through make cat-file:

~~~text
.git/gludd-release-evidence/79a7b76110a3-56d76bc75d24b6119574680b121563e18bbe550a/manifest.json
~~~

The manifest records the exact branch/HEAD/original path and rehydrate identity.
Its preserved paths include the prior pipeline/runtime/cleanup coverage audits,
full reports, observed logs, CPU profiles and .gludd/branches. Source-local
.gate-logs paths cited in older paragraphs now resolve under that archive;
all external /tmp reports remain at their original paths. Native git-show-commit
still resolves56d76bc and parentd69 with its exact two-file68insertions/1deletion.
All six checkpoint identities remain verified before/after actual removal.

Exact actual owner inputs:

~~~console
make agent-cleanup BRANCH=codex-v012-retirement-plan-6161e10-20261021 WORKTREE=/private/tmp/gludd-worktrees/codex-v012-pipeline-status-overhead-20261020 ACTIVE_WORKSTREAM_REGISTRY= AGENT_CLEANUP_VALIDATE_ONLY=0 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

The preceding successful plan uses the same inputs with literal1. A future
owner must establish current eligibility again rather than reuse this receipt.
Earlier broad prose coupled retirement to publication; the native contract
requires genuine integration/completed ownership and its existing fences.
Root clarifies that operating dependency without changing production policy.
Publication, exact-SHA CI and release proof remain independent obligations.

After removal make disk measures97%/7.5GiB available, remaining worktrees1.4GiB
and Git581MiB including retained evidence. This is an actual footprint
observation, not an exact reclaimed-byte attribution or95% clearance.
Keep the original03966 Pages materialization under its native ancestry refusal,
the locked gate-admission owner, dirty/unknown work and all checkpoints.
No unchanged final-admission/full-gate retry follows from disk still97%.
