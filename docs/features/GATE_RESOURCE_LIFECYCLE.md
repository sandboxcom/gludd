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

### Lightweight diagnostics

`make active-work-status` is dependency-free and runs with
`/usr/bin/python3`. It is listed in `_NO_UV_SYNC_GOALS` and must not invoke
`uv run`, resolve dependencies, create `.venv`, or mutate the lockfile. Targets
that need application dependencies continue to use the locked uv environment;
this exception is deliberately narrow rather than a project-wide bypass.

This matches uv's documented behavior: in a project, `uv run` creates the
project environment when absent and makes sure it is current before execution.
The long-running upstream discussion also records practitioners being surprised
when `uv run <nested-script>` creates the root `.venv`:
[uv project environments](https://docs.astral.sh/uv/concepts/projects/layout/)
and [uv issue 11302](https://github.com/astral-sh/uv/issues/11302).

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
