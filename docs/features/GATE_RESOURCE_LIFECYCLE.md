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

### Distribution cleanup

`make clean CLEAN_VALIDATE_ONLY=1` is the safe behavioral contract. Actual mode
`0` removes normal local caches and delegates only `dist/` cleanup to
`git clean -fdX -- dist`. The pathspec confines deletion to `dist/`; `-X`
selects ignored build outputs, so Git-tracked inputs such as
`dist/debian/control`, `dist/rpm/gludd.spec`, `dist/windows/gludd.nsi`, and
`dist/install.sh` survive. Git documents `-X` as removing only ignored files and
pathspecs as restricting the affected paths:
[git-clean documentation](https://git-scm.com/docs/git-clean.html).

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
