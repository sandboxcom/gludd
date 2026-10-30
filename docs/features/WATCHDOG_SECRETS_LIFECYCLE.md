# Watchdog Secrets Scan Lifecycle

## Native async-gate inherited-signal boundary (2026-10-27 UTC)

Independent review of published a403ed858bb561f7bbc8353c54ad057be4020f89
finds scripts/gate_async.sh resetting INT/TERM only inside a short-lived Python
child. That cannot change Bash's inherited dispositions or blocked mask. Its
test fixture had reset signals before Bash and changed the original group-INT
case to wrapper-only, masking the production boundary.

The isolated feature/v012-signal-boundary-repair-20261026 starts at that exact
source. Tests first restore the original group-INT call and ten-second child
communication bound, remove the fixture reset and add real exec inheritance
controls. All original development and a403 assertions/test functions remain.
The native host RED is 9FAIL/5PASS in82.69s, watched native2/84.038s:
.gate-logs/observed/v012-signal-boundary-red/
v012-signal-boundary-red311-a403-20261027.json. Its exact session2199 is drained;
observed-status verifies inactive/failed2. Four active unguarded Bash controls
and original group-INT pass. All eight dirty group/wrapper cases retain their
ten-second TimeoutExpired failures. The ninth failure executes the owned CWD
signal.py payload in the old non-P bootstrap, after a successful active witness.
These are actual source failures, not sandbox process-permission failures.

The repair stays in the same wrapper. Selected SYSTEM_PYTHON -P probes only
inherited INT/TERM state before any lock/status/child ownership. Dirty state
triggers same-PID exec of that Python, native disposition/mask reset, then
os.execve of the same actual Bash/script/arguments with a copied environment.
The next probe observes clean state. No environment sentinel or bypass flag is
added; -c preserves stdin. The original body from REF onward remains byte-exact,
including PID/group/session, locks, full gate command, native status, signal
cleanup and escalation. Unrelated environment and signal-mask carriers remain.

Ruff, strict complete test-owner typing, the unchanged test-environment guard,
shell syntax and working-file line limits pass. The native Make-reading AST
runs first in repaired focus:15PASS20.30s/native21.541s at
.gate-logs/observed/v012-signal-boundary-green/
v012-signal-boundary-green311-a403-20261027.json. Exact session92403 is drained;
observed-status verifies inactive/passed0. Its trace has33events/15pairs,
zero issues, unfinished cases or failure reports; RED retains40events/14pairs
and9failures. These spans establish no CPU or performance improvement.
The native Markdown owner initially refuses a missing peer .venv/bin/rumdl;
authorized reuse of the verified Main linter checks the exact peer file and
unchanged policy successfully, without sync/install or runtime override.
Both complete consumers, tests/unit/test_gate_async.py and
tests/unit/test_shell_scripts_deep.py, pass 173 cases each with no skips:
Python 3.11 in 5.18s/native 6.500s and Python 3.12 in 6.00s/native 7.397s.
Their receipts are under .gate-logs/observed/v012-signal-consumers/:
v012-signal-consumers311-a403-20261027.json and
v012-signal-consumers312-a403-20261027.json. Exact sessions 56388 and 92601
are drained; observed-status verifies inactive/passed0 for each. Each trace
has 349 events/173 pairs and zero issues, unfinished cases or failure reports.
These are focused behavior receipts, with no CPU or speed claim.

Independent code/test and research review approves source only. Root's earlier
three-path staged content guard passed native0 in 7.742s before the research
and final receipt prose. Final restaging/content guard, normal fresh collection
and the small source commit remain Root-owned and pending.
Every watcher and child uses the same explicit installed runtime, pinned UV0.13.1,
literal ID= and checkout-owned ledger/observation paths. No Python production
coverage profile is changed; canonical global85/75 line AND branch, combined
acceptance, full gate, exact-SHA hosted/promotion, ZDD and complete release
proofs remain required. The Root-owned Build40427263941 and
Molecule40427577982 dispatches select a403, not this uncommitted repair, and
do not certify its source.

### Inherited-signal research and proof limits

The [2017 firsthand process-group SIGINT report](https://unix.stackexchange.com/questions/401169/sending-sigint-to-process-groups-sometimes-gets-ignored)
describes ignored interrupts through nested/background Bash. It is historical
operational context, not proof of a current Bash defect or Gludd's cause.
Our active native inheritance controls and the retained source RED establish
this repository's boundary independently.

The [Bash signal manual](https://www.gnu.org/software/bash/manual/html_node/Signals.html)
describes ignored INT/QUIT in asynchronous commands when job control is off.
[POSIX exec](https://pubs.opengroup.org/onlinepubs/9799919799/functions/exec.html)
retains ignored dispositions and the calling thread's signal mask across exec.
[Python's pthread_sigmask contract](https://docs.python.org/3.12/library/signal.html#signal.pthread_sigmask)
changes the calling thread's mask. Those boundaries explain why a short-lived
child cannot reset its Bash parent and why the repair resets the actual process
before re-entering the same Bash script. Root's bounded public retrieval
returned HTTP200 for these sources; raw research remains at
/tmp/gludd-v012-signal-public-research-20261026. External context supplies no
test, global coverage, hosted or deployment proof.

## Ownership and proof boundary

S98.4 support begins at reviewed development
0a302b8eacfaeeae90149abca5d144a324110169. Its independent disk-source decision
is /tmp/gludd-v012-disk-0a302b8-source-review.md, fully read by Root. Normal
hooks retain zero new clones, six checkpoints and128482/128506 collection,
24 deselected/zero errors,56.47s/native63.5s. Source approval is not release proof.

Native ancestry identifies watchdog39922 launching make secrets-scan10939.
After its60-second subprocess timeout, shell10947 has PPID1 while UV10953,
scanner10954 and six workers remain. Later disappearance does not erase the
cleanup gap. No diagnostic PID is signalled. Native process evidence and
model-agent names/status remain separate.

## Existing owner and runtime contract

The typed Path adapter reuses the existing stream_command API lazily, with the
same secrets-scan command, workspace and findings producer. Scan work remains
60 seconds, quiet deadline0, heartbeat15, retention20 and cooldown300. Existing
session/descendant cleanup finishes before return; cleanup grace is not more
scan work. The scanner worker budget, ZDD and release limits remain unchanged.
There is no new scanner, runner, observer, parser, daemon, cache or dependency.

Evidence uses a private0700 native namespace and refuses symlink namespaces.
Raw output stays in the protected log; phase/heartbeat/terminal output streams.
The legacy snippet reads at most500 bytes from its handle. Timeout and
observer/I/O errors replace prior violation=False with current unknown evidence.
The exact native run distinguishes observer-signal from child-signal: the former
invalidates success and propagates SystemExit after cleanup through CLI lease
finally; the latter retains a nonzero finding without stopping the daemon.
Native producers own their current JSON; no manual observation is written.

The child uses its parent's executable/prefix with UV_NO_SYNC=1 and downloads
disabled. Select the installed runtime for both outer helpers and children.
Canonical strict typing uses the existing TYPE_CHECKING flat/package convention
for the observer and ReleaseData. A temporary-file strict pass did not establish
repository package-context typing: the native scope exposed an import-prefix
collision, then passed after precise import alignment. No suppression, new
type interface or module-layout change is retained.

## Failing-first receiving and fixture confinement

The eight-case fixture binds the actual repository producer and synthetic Make,
with confined HOME/TMPDIR and recorded command/CWD/environment. Only synthetic
fault clocks shorten; production asserts60. Initial canonical8setupERROR/.87s
comes from redundant syspath_prepend hitting the harness's pkg_resources stub.
Removing only that unnecessary path mutation preserves every assertion and
yields7FAIL/1PASS3.08s/native4.2s on unchanged production. Four native signal
controls expand the RED to11FAIL/1PASS7.09s/native7.9s. The baseline timeout
returns with three synthetic descendants live; they drain by bounded natural
exit. No baseline PID is signalled.

The first complete47-case replay is failed evidence despite its green filename.
A missed legacy integration mock reaches the actual read-only scan path:
native child20015/owner19958 times out124/60.515s at exact run
scan-a004eb6d25334b7fa7bff1d447384f88. The outer focused owner records
quiet-output-timeout124/63.464s, not47-case acceptance. Root measures20015
missing. Separately measured20305 is PPID39922, the old watchdog's concurrent
scan; it is not attributed to the fixture.

A new CWD-confinement assertion fails1/.68s/native1.6s. Existing legacy setup
then confines command workspace as well as state; the missing adapter mock is
supplied. All35 original IDs and outcome/command/CWD/deadline/cooldown/schema
assertions remain. Twelve new native controls plus one CWD guard yield48PASS311
in2.39s/native3.4s and312 in2.92s/native4.0s:
 /tmp/gludd-v012-watchdog-secrets-confined-green{311,312}.{log,xml}.
Retain /tmp/gludd-v012-watchdog-secrets-canonical-{behavior-red,full-red}311.*
and /tmp/gludd-v012-watchdog-fixture-cwd-red311.*. The external pilot8PASS
precedes final signal/typed changes; it is not final canonical acceptance.

## Complete source measurement and remaining proof

All13 complete watchdog/observer owners pass376 under311 coverage in27.26s
and376 on312 in20.42s/native21.7s. The broad existing coverage profile then
fails38percent because seven altered temporary native-agent facade copies are
counted as separate20-percent producer files. Preserve the native failure:
.gate-logs/observed/coverage-files/20261021T161645Z-23015-f1e00019.{log,json}.
Correct source identity through the existing owner, keeping every canonical
watchdog file/test and85aggregate/75each-file line AND branch floors. Altered
copies must not be remapped onto canonical source bytes.

Use the installed311 tuple from SESSION.md for this existing serial owner;
it already streams native observation and must not be wrapped in another
observer. The complete coverage command preserves every owning test file:

```console
make coverage-files \
  COVERAGE_TESTFILES='tests/unit/test_agent_watchdog.py tests/unit/test_watchdog.py tests/unit/test_watchdog_deep.py tests/unit/test_watchdog_component_behavior.py tests/unit/test_agent_watchdog_split_compat.py tests/unit/test_agent_watchdog_lifecycle.py tests/unit/test_watchdog_singleton.py tests/unit/test_scripts_enforcement.py tests/unit/test_watchdog_ci_gate_injection.py tests/unit/test_watchdog_secrets_lifecycle.py tests/unit/test_watchdog_secrets_signals.py tests/unit/test_observed_command.py tests/unit/test_observer_retention_concurrency.py' \
  COVERAGE_CONFIG=config/coverage_agent_watchdog_split.ini \
  COVERAGE_REPORT=.gate-logs/coverage-v012-watchdog-secrets.json \
  COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 \
  WORK_ITEM_ID=S98.4 OBSERVED_ROOT=.gate-logs/observed \
  OBSERVED_HEARTBEAT_SECS=10 OBSERVED_QUIET_SECS=60 \
  OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20 \
  UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
```

The [source-identity record](COVERAGE_SOURCE_IDENTITY.md) preserves the broad-pattern
failure and the same-owner physical-root repair. Its first corrected measurement
retains376PASS25.10s/native26.9s but fails88.7line/84.7branch and enforcement74.6branch.
Sixteen meaningful heartbeat/latch controls then yield392PASS31125.31s/native26.9s,
89.2line/85.9branch and all nine nonempty canonical files above75 BOTH. The native
audit .gate-logs/coverage-20261021T164644Z.json reports passed=true; the native
observer run is20261021T164617Z-38752-abb1e686. The identical392 cases pass312
20.30s/native21.7s through the authorized host Make boundary. Preserve its earlier
sandbox25FAIL/367PASS/3ERROR29.57s/native30.9s: process-inspection denial is failed
proof, and unchanged identity/cleanup requirements need the authorized boundary.
The legacy deep-owner strict diagnostic retains80 pre-existing imported errors;
it is not a strict pass. The source and two new native test owners separately
pass canonical strict scopes, without suppression or baseline changes.

The receiving source-identity unit is425e47444284a555a2211db13988edae6ed82b61,
parent0a302b8. Normal hooks retain128520selected/128544collected,24deselected,
zero collection errors48.63s/native54.1s, zero new clones and six checkpoints.
The first EOF hook normalization remains retained. The392-case receipts measure
the combined receiving bytes including this then-uncommitted adapter; they are
not partial425 exact-commit proof or full-gate evidence.

## Deterministic admission and model operating guidance

A Root transport mistake combines lint-markdown with two additional Make goals;
the literal-input guard refuses before execution. Use its documented single-target
invocation and drain each separate check. Markdown7, line limits and SESSION
validation then pass without changing that guard.

The first236-case operating replay mistakenly selects60quiet/240total instead of
the prior native operating helper180quiet/240total. It fails124/69.938s,
quiet60.554s, before any terminal test summary; preserve exact run
v012-watchdog-secrets-operating311. Its captured test-count child
20261021T222634Z-9931-22171430 is subsequently orphaned, not successful collection.
The earlier actual operating receipt coverage-source-operating-host-0a302b8-311-20261021
records limits[180,240]. Restore that established caller configuration before
another cohort attempt. No production timeout, test assertion or original
runtime/publication90/60 is changed. No concurrent scan is inferred to be the
cause of this quiet-output failure. With established180/240 restored, the same
236 complete operating cases pass84.51s/native85.5s; retain
/tmp/gludd-v012-watchdog-secrets-operating-established311.{log,xml}.

Append only family146 watchdog-secrets-owned-timeout and family147
watchdog-scan-fixture-workspace after all145 predecessors. Their exact native
timeout and default CWD guard run once in the existing first child. Independent
count/order/two-roster pins retain5FAIL/1PASS1.17s/native2.6s before wiring, then
all six pass2.13s. Complete eight-owner routing then passes478 on311 in128.82s/
native130.2s and478 on312 in125.84s/native127.2s, with fatal warnings and zero
skips/errors. Logs/XML: /tmp/gludd-v012-watchdog-secrets-routing147-{311,312}.*.
Native contracts276, Make1157/zero duplicates/9CI12local, Ruff six files,
strict source/routing scopes, docstrings, Markdown7, line limits, SESSION
structure and15-path task registration pass. Node26 compatibility passes all
six controls10.97s. Preserve all original selectors/order, three children with
2/1/1 workers,90total/60quiet,21phases2850/full gate, fatal warnings,85/75 BOTH,
ZDD, retention and promotion. The full twelve fault controls remain complete
source acceptance; two cheap controls only move deterministic failures earlier.

AGENTS and the mandatory Make guide explain the actual adapter mock seam,
workspace-plus-state fixture defaults, installed runtime, descendant drain and
source-versus-live activation. Normal guarded commit/fresh collection,
independent source review and actual daemon handoff remain separate evidence. At the source49 receiving boundary,39922
still had old code loaded; source tests did not activate it. Use the [acknowledged lifecycle](GATE_RESOURCE_LIFECYCLE.md#acknowledged-agent-watchdog-handoff-2026-10-20)
after reviewed integration, with the installed runtime and no active readers.
The actual receiving stop plan WATCHDOG_VALIDATE_ONLY=1 refuses: native observer
status inventory reached its display cap, scripts/task_watchdog.py:422. No stop
or raw signal follows; preserve the guard and repair complete inventory through
its existing owner before activation. Source approval cannot clear this refusal.

Original60907 publication124/90.554s/trace_path:null remains unexplained.
Fresh receiving disk is97percent/6.9GiB against95; earlier98percent/5.8GiB is
historical. The changed observation supplies no cleanup or final-proof authority. No unchanged release admission, startup, cleanup or full-gate
retry follows. All seven S98 tasks stay unchecked; exact-SHA CI/deployment,
global coverage and guarded promotion remain pending.

## Terminal pointers and native handoff

Exact development49a373b1ae2081e7209e4be5253d3ce270d58e09 commits the adapter,
parent425e474. Normal hooks retain zero new clones/six checkpoints and128520/
128544 collection,24deselected/zero errors46.46s/native51.7s. Receipt:
.gate-logs/observed/test-count/20261021T223029Z-12749-dd1c0aeb.{log,json}.
Root fully reads /tmp/gludd-v012-watchdog-coverage-49a373b-source-review.md:
independent APPROVE SOURCE ONLY against0a302b8 for both source units/nineteen
paths. That approval does not apply to this later empty-projection repair.

The failed stop plan hits the existing64-current-status display bound even
without a live CLI observer. The existing producer can admit an observer owner
only when its complete native process row contains stream_command.py.
The cleanup owner now skips only that provably empty metadata projection.
Native gate PID/lock protection, ancestor/descendant protection, fresh process
identity/CWD/start checks and verified signal/drain ownership remain unchanged.
If any process command contains that token, even an uncertain or foreign one,
the original metadata path and64-status/128-process refusals still run. No
bound is raised, pointer deleted, inventory claimed complete from a truncated
view, new parser/observer/cache introduced or unknown process authorized.

Six native fixture controls fail first1.33s at the exact existing cap.
They cover64/65 historical pointers with absent/pid/lock gate markers and retain
every pointer. All six plus the existing live/omitted/damaged/capped safeguards
then pass12/.91s. Complete task-watchdog acceptance159PASS3115.54s/native7.2s
measures the unchanged one-file profile at92.8line/85.2branch, all85/75 BOTH
floors intact; native audit .gate-logs/coverage-20261021T223430Z.json passed=true.
The same159PASS3124.83s/native6.1s; source/test strict and Ruff pass.
Actual watchdog-stop plan1 then returns verified apply=False old_tree_drained=False.
No daemon has been stopped or activated by that result.

A mistaken single-file RESOURCE_OWNERSHIP_PATHS request against the full manifest
reports unrelated entries stale. The documented empty-path/full-scope native
check passes264 resources:29async-task/73client/36process/9service/117temp-artifact.
The inventory is unchanged. Correct caller scope rather than regenerating data
to match a partial scan.

Family148 appends only the exact absent-64 control after all147 predecessors.
Its independent count/order/two-roster pins retain5FAIL/1PASS1.19s before wiring,
then six PASS1.59s. Complete nine-owner routing/lifecycle acceptance passes533
on311130.22s/native131.9s and312127.71s/native129.1s, zero skips/errors and fatal
warnings. The complete five documentation/handoff/ledger owners pass98/4.40s/
native5.7s. Ruff four files, four separate strict scopes, docstrings,276contracts,
Make1157/zero duplicates/9CI12local, Markdown6, SESSION structure,12-path
registration and the unchanged file-line guard pass.
All original selectors/order, three2/1/1 children,90total/60quiet,21phases2850,
full gate, fatal warnings,85/75 BOTH,ZDD/cleanup/retention/promotion remain.

After normal source commit and independent review, quiesce all checkout readers,
model owners, tests and observers. Use the installed311 tuple above for every
native owner. Plan and apply the agent handoff first, immediately restore both
guards, then plan/apply the task handoff and immediately restore both again:

~~~console
make watchdog-stop WATCHDOG_VALIDATE_ONLY=1
make watchdog-stop WATCHDOG_VALIDATE_ONLY=0
make watchdog-auto WATCHDOG_AUTO_VALIDATE_ONLY=0
make task-watchdog-stop TASK_WATCHDOG_VALIDATE_ONLY=1
make task-watchdog-stop TASK_WATCHDOG_VALIDATE_ONLY=0
make watchdog-auto WATCHDOG_AUTO_VALIDATE_ONLY=0
make watchdog-start WATCHDOG_START_VALIDATE_ONLY=1
make task-watchdog-start TASK_WATCHDOG_START_VALIDATE_ONLY=1
~~~

A refusal stops the dependent sequence and preserves evidence. Use native
active-work-status/ps to measure predecessor absence and acknowledged successor
identities; do not invent PIDs, raw-signal a process or infer activation from
source tests. Keep the other watchdog alive during each handoff. Native current
scan evidence must show the installed successor's observer lifecycle; an actual
timeout remains unknown, not a clean secrets finding or release proof.

## Reviewed source9ac and actual owned handoffs

Exact9ac03d9f32f81c53ffcca842c2132d470919fb2c follows49. Normal twelve-path hooks
retain128526selected/128550collected,24deselected/zeroerrors47.92s/native53.2s,
zero new clones(two existing records)/six checkpoints. Collection receipt:
.gate-logs/observed/test-count/20261021T224329Z-55885-b5b48fcd.{log,json}.
Root fully reads independent APPROVE SOURCE ONLY at
/tmp/gludd-v012-watchdog-history-9ac03d9-source-review.md. It verifies the
necessary-token condition, unconditional gate protection, retained live caps,
six additive controls and exact family148. No source-only approval closes S98.

After all readers/tests/observers/model owners quiesce, actual agent plan1/apply0
drains old39922 with native fresh identity and SIGTERM/wait evidence. Immediate
auto acknowledges57626 at2026-10-21 18:46:35 local, PID=PGID=SID, using the
registered311 executable; task29001 is already-running without spawn/signals.
Actual task plan1/apply0 then drains29001. Immediate auto reuses57626 and
acknowledges57964 at18:47:03 local with PID=PGID=SID and the same interpreter.
Thus the other guard remains during each native handoff.

Native start plans1 subsequently reuse57626/57964 with writes=false/signals=false.
Exact old39922/29001 active-work-status diagnostics report missing; native ps
shows only the two successor watchdogs and no audit/test processes. These
native identities describe OS controllers, not model-agent PIDs. No raw signal
or CWD-only authority is used, and no serving component is restarted.

After its unchanged300-second cooldown, the first natural successor scan starts
2026-10-21T22:50:58.994740Z: scan-801b14b84dbf4538bdf543d3bd6bab08,
owner57626/child59561, label watchdog-secrets. Native observed-status reports
inactive/timed_out, exit124, elapsed60.552s, max-runtime-timeout and null trace.
Exact active-work-status for59561 is then missing; native ps shows only57626/
57964, with no test/audit/scanner processes. The60-second lifecycle bound and
post-timeout child absence are measured on the installed reviewed source.
Successful secrets scanning and its timeout cause remain unproved; retain this
failure and old scan-a004eb6d separately. No scan is forced and no native
cooldown/current JSON is edited.

The final five documentation/handoff/ledger owners pass98 in5.82s/native7.0s,
run v012-reviewed-watchdog-profile-docs311, fatal warnings/zero skips. Latest
native disk-check at the0ab boundary fails97percent>95 and scratch766.5MB>100MB.
Later [native cleanup](CACHE_RESOURCE_INVENTORY.md#native-capacity-cleanup-and-direct-owner-2026-10-21)
changes97to96 with8.1GiB free/711.7MB counted scratch, then stops at no-progress.
Capacity still fails95; retained artifact sizes grant no cleanup authority.

The separate [readonly-scanner profile](PUBLICATION_READONLY_SCANNER_PROFILE.md)
answers a new parent-scope publication question with1PASS/native4.534s and mostly
subprocess waiting. It selects no optimization and does not explain60907.
Its two distinct trace identities and firsthand fixture-attribution evidence
are codified there. Capacity, original publication, complete canonical gate,
exact-SHA hosted/deployment proof and guarded promotion remain pending.

## Closed-pipe leader failure: retained unresolved RED (2026-10-24 UTC)

The bounded scanner design identified a separate observer lifetime boundary.
On source 9647b0a96125b78126e009c56d9b4769127c5a3d, the new
test_observed_command.py::test_failed_leader_with_closed_pipes_drains_positively_owned_descendant
runs only an owned synthetic leader and one self-expiring descendant. The
running-publication barrier verifies both live native PID/create-time/CWD/
PGID/SID associations before releasing the leader. The descendant redirects
both output pipes and expires naturally after 1.5s; teardown sends no signal.

Actual RED: 1 FAIL in 2.36s / native 3.357s, retained at
.gate-logs/observed/observer-closed-pipe-red/observer-closed-pipe-red311-9647b0a.json,
with its unchanged log/native trace. Exact exec 96422 drained. The inner native
observer published failed/raw 17 in 0.1s while positively bound descendant 11564
was still live. All preceding identity, original exit 17/log/receipt assertions
passed; only the terminal-descendant assertion failed. Natural-expiry teardown
then verified no positively bound fixture process remained live.
The outer receipt is inactive/failed/exit 2, started 01:51:44.405179 through
01:51:47.762508 UTC. This controlled failure does not establish a host scanner leak
or explain a historical scanner timeout.

Admission waited for the actual natural scanner/collection owners to drain.
The fresh complete native process view contained only watchdogs, workers 0/8,
collection available and load 1.97/8; the focused case required three execution
processes, not just one nominal cohort. No host scanner or daemon was forced,
stopped or reloaded. No host PID was signaled and no cleanup was applied.

Root accepted stopping at the demonstrated RED/authority boundary. The current
observer can reap its leader through poll before finalization. Its existing
group terminator accepts Popen/grace and reconstructs numeric PGID/SID; this
step has not established an original-generation proof for a new post-reap
signal path. A reused/foreign-group case was not executed. Do not turn the
final conditional into unconditional group signaling without that proof.

Production stream_command.py remains unchanged. The new regression stays
failing in the isolated worktree, with this record; no assertion, xfail,
ratchet, deadline or profile was weakened. There is no GREEN or completed
scanner coordination. Lease/capture/watchdog-route/Make/inventory changes and
activation are outside this first step. A later authorized repair still needs
independent ownership-negative controls, unchanged full four-module
coverage_observed_command.ini on both runtimes and its original 85/75 line AND
branch floors, before any broader acceptance. No profile was launched here.
The critical diagnostic feature intake/publication path remains independent.

## Non-reaping prerequisites: native boundaries, not a repair (2026-10-24 UTC)

Root authorized prerequisite observations before any production mutation.
The existing Popen.poll/wait paths can consume the leader's native status.
A held-leader design must defer every such reap until descendant drain, retain
the original status and use exactly one eventual original Popen.wait. It cannot
call Popen.send_signal/kill/terminate during the retained window: the firsthand
[CPython42558 report](https://bugs.python.org/issue42558#msg382524)
describes their internal poll/reap behavior. The
[installed-version source](https://github.com/python/cpython/blob/v3.11.14/Lib/subprocess.py)
and [2019 split-wait discussion](https://bugs.python.org/issue38630/msg356085)
are relevant boundaries, not evidence that this observer is repaired.
The latter also suggests kqueue on macOS. The
[POSIX process-ID lifetime rule](https://pubs.opengroup.org/onlinepubs/009696699/basedefs/xbd_chap04.html#tag_04_12)
does not by itself establish this implementation's complete signal authority.

The public [waitid API](https://docs.python.org/3.11/library/os.html#os.waitid)
is subject to native availability. Both installed Darwin interpreters expose
P_PID/WEXITED/WNOHANG/WNOWAIT and CLD_EXITED/CLD_KILLED/CLD_DUMPED, but neither
has callable os.waitid. Zero-child probes retain actual missing-API failures:
observer-waitid-capability311-9647b0a, exec50022, 1FAIL/0.86s/native2.025s,
03:54:49.901238 through03:54:51.926786 UTC; and its312 counterpart, exec26165,
1FAIL/0.88s/native1.942s, 03:55:55.061937 through03:55:57.004003 UTC.
Both receipts live under .gate-logs/observed/observer-waitid-capability.
Their original failures remain; the current metadata diagnostic reports a
platform refusal instead of imposing universal waitid availability or an xfail.
No wait syscall, child, signal or fallback was used in those probes.

The public [kqueue API](https://docs.python.org/3.11/library/select.html#kqueue-objects)
documents KQ_FILTER_PROC/KQ_NOTE_EXIT and the six required filter/event flags.
Both installed runtimes expose callable kqueue/kevent and those flags.
Optional KQ_EV_RECEIPT is absent and is not a prerequisite.
The311 zero-child metadata receipt, exec38504, 1PASS/0.82s/native1.991s at
04:01:08.050082 through04:01:10.041277 UTC, initially overrequired that optional
flag and classified the candidate as refused. The corrected312 metadata probe,
exec90354, 1PASS/0.82s/native1.862s at04:03:39.985757 through04:03:41.847963 UTC,
identifies kqueue as a candidate. Both retain native_behavior_proved=false under
.gate-logs/observed/observer-native-capability; no queue or child was created.
This classifier correction is not a production fix or native lifecycle proof.

The subsequent approved two-case native311 prerequisite failed twice:
exec92463, 2FAIL/0.86s/native1.872s, 04:09:06.792087 through04:09:08.664978 UTC.
Its exact inactive/failed/exit2 receipt and unchanged log/trace are
.gate-logs/observed/observer-kqueue-prerequisite/observer-kqueue-prerequisite311-9647b0a.json.
Both cases bind live PID/birth/PPID/CWD/PGID/SID before allowing exit17, instrument
only the owned Popen instance against premature poll/wait/signal methods and
restore its methods for exactly one original final wait returning17.
Each owned child expires independently, and every opened queue closes.

Before-exit registration delivered a native NOTE_EXIT event for53779:
filter=-5, flags=32821, fflags=2147483648, data=0. The child retained returncode=None
and the same native birth/PPID/zombie state. Its subsequent os.getpgid lookup
raised ESRCH, so the complete retained-group assertion failed.
In the after-zombie case, the same lookup for53780 raised ESRCH before queue
construction. Late registration has therefore not been measured.
No post-zombie CWD was invented, no process was signaled and both final waits
returned17. The final native process view contained only the two watchdogs.
A delivered event alone does not prove the proposed group-identity authority.

Dependent312 execution and all production edits stopped at this failed input.
Do not replace the group assertion, treat ESRCH as safe disappearance or infer
late-registration support without a separately reviewed proof decision.
The original closed-pipe descendant RED remains unchanged and unreplayed.
No GREEN, scanner coordination, coverage, gate, CI or release proof is supplied.

## Corrected retained-session proof obligation (2026-10-24 UTC)

An independent proof audit accepted a narrower conditional argument: while the
original leader PID is retained under a strict sole-reaper/default-SIGCHLD
contract, a foreign process cannot create a new group with that numeric ID.
The argument depends on PID lifetime and the POSIX setpgid/setsid group-creation
rules. It does not require Darwin to support getpgid on an unreaped zombie.
This changes the diagnostic hypothesis; the two previous failed receipts and
their exact pre-correction source remain preserved. The source snapshot is
/tmp/gludd-v012-observer-before-zombie-prerequisite-correction.py.
The original SUT descendant RED remains unchanged.

The approved next witnesses bind both leader and one self-expiring descendant
while live. After NOTE_EXIT, fresh native Process instances must verify the
same leader PID/birth/PPID/zombie state and the same still-live descendant
PID/birth/CWD with PGID=SID=retained leader PID. No cached create_time supplies a
fresh observation; no post-zombie leader CWD or group lookup is invented.
Native getpgid ESRCH is recorded as an unknown field, never gone/safe authority.
The separate late-registration case must positively observe the zombie before
actually reaching queue registration, retaining an event or exact native error.
A refused registration certifies no backend support. Event data0 supplies no
exit-code authority; only one eventual original Popen.wait may supply17.

Owned-instance reaping methods remain forbidden until the descendant exits
naturally through its private release marker or TTL. No OS signal is authorized.
Default SIGCHLD, positive native zombie observation and one final original wait
make the sole-reaper assumption explicit. Production still requires a separate
review decision, including every early wait and Popen signal-helper fallback,
before any final group operation can rely on the retained leader.
No production code has changed and no cleanup/coordination is authorized here.

## Actual retained-session witnesses on both installed runtimes (2026-10-24 UTC)

The changed-input two-case prerequisite runs are terminal and inactive:
311 exec81172 passes2 in0.94s/native1.974s, 04:20:24.574232 through
04:20:26.548820 UTC;312 exec91429 passes2 in1.03s/native2.076s,
04:20:41.980800 through04:20:44.057191 UTC. Exact receipts and unchanged logs/traces
live under .gate-logs/observed/observer-kqueue-session as
observer-kqueue-session311-9647b0a.json and its312 counterpart.
Both use original90/60/n0/fatal-warnings/ID= plus native trace, with installed
3.11.14 and3.12.15 explicitly selected.

Before-exit registration delivers NOTE_EXIT on both runtimes. Fresh native
leader59574/59807 retains original birth/owned PPID/zombie/returncode=None;
fresh native descendant59575/59808 retains original birth/CWD and
PGID=SID=retained leader PID while still running, now reparented toPPID1.
Leader getpgid ESRCH is retained only as unknown diagnostic metadata.
This supplies the approved conditional session prerequisite, not production
cleanup authority or coverage of every initial/failed-inspection boundary.

After positive zombie observation, both separate late-registration cases
actually construct their queue and register. Native EV_ERROR flags16405/data3
is ESRCH, with registration_errno=3, outcome=refused-native-registration and
session_prerequisite_proved=false. A passing refusal control does not certify
late backend support. The NOTE_EXIT event's data0 in the first case never
supplies the exit17 status.

All four descendants reach native gone through their own private marker;
all opened queues close; every leader has exactly one original final wait17.
The retained-window owned-instance guards forbid poll/wait/signal helpers.
No OS signal, forced scan, daemon change, cleanup or production edit occurred.
Fresh census after the pair contains only the two watchdogs. Admission first
waited for actual natural scanner58207, its tracker58455 and five workers58456
through58460 to drain; low load alone did not admit three more fixture processes.

The next production decision must address late-registration refusal and every
premature-reap path, including helper wait268, Popen terminate/kill fallbacks,
loop poll551/647/654 and wait557. Original final wait656 must remain the sole
eventual reaper after proven terminal group drain. A successful pre-exit
registration alone cannot justify a race-prone backend or guessed fallback.
Existing return statuses, default API, merged streams, prerequisite90/60 bounds,
60-second scanner/300-second cooldown and the original four-module coverage
profile remain unchanged. Original SUT RED and all earlier failed prerequisite
receipts remain separate. No production GREEN, scanner coordination or release
proof is claimed.

## First retained-leader repair: failures and native decision (2026-10-24 UTC)

The subsequent authorized production WIP changes only stream_command.py, its
existing test_observed_command.py owner and this feature. Base9647 and every
earlier failed receipt remain protected. The first implementation uses fresh
native private-child identity/zombie observations; no kqueue, waitid, alternate
reaper, observer, cache or daemon was added. Known nondefault SIGCHLD refuses
before child creation. Private Popen/default-SIGCHLD/no-competing-reaper is an
explicit contract; this cannot atomically defend against external violations.

Actual pure controls: exec7251 has5FAIL/0.81s/native1.692s,04:29:50.129407 through
04:29:51.821299 UTC, observer-reaper-controls-red311-9647b0a. Three controls reach
the old premature helper wait/Popen signal fallback; two reach forbidden child
creation under ignored/custom SIGCHLD. The preceding exec4326 has2FAIL/0.80s/
native1.878s,04:28:38.978131 through04:28:40.856508 UTC. Its AssertionError sentinel
also bypassed native OSError cleanup and exposed fatal unclosed-log warnings.
The exact failed source is /tmp/gludd-v012-observer-policy-assertion-sentinel-red.py.
The corrected OSError sentinel retains no-child-start/state assertions and
exercises the supported native error boundary; no production warning was hidden.

The real already-zombie control, exec24518, has1FAIL/0.80s/native1.774s,
04:32:22.927535 through04:32:24.701327 UTC, observer-fast-leader-red311-9647b0a.
Native old reaps are poll(None), poll(17), wait(17), all before cleanup; original
exit17 is retained. The source before repair is preserved at
/tmp/gludd-v012-stream-command-before-retained-leader-repair.py.

First postrepair exec94282 selects the unchanged original9 focused criteria:
7PASS/2FAIL1.56s, outer125/native2.968s,04:41:29.663184 through04:41:32.631301 UTC,
observer-retained-leader-focus311-9647b0a. The original closed-pipe descendant
RED now passes and all five pure controls pass, but fast17/originalchild7 become
125 after cleanup EPERM. This is not GREEN. Exact first production bytes are
/tmp/gludd-v012-stream-command-retained-first-7pass-2fail.py.
Per-operation diagnostic exec66460 then selects only the fast native input.
Its1FAIL/0.84s and outer125 identify os.killpg on owned zombie80181:
SIGTERM15 and SIGKILL9 both return errno1. The outer pytest zombie80142 repeats
that result. There are no census/member errors; no foreign-UID inference applies.
Receipts/logs/traces use .gate-logs/observed/<label>/<run-id>, with labels
observer-reaper-controls-red, observer-fast-leader-red,
observer-retained-leader-focus and observer-cleanup-operation-diagnostic.

The sole HTTP review owner found a matching primary/firsthand boundary.
[XNU killpg1](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_sig.c)
excludes zombie members from its group filter and can return EPERM when no
eligible member is found; permission refusal can produce the same result.
This is published-main source, not an exact installed-kernel binding.
The [2012 firsthand report](https://stackoverflow.com/questions/12521705/why-would-killpg-return-not-permitted-when-ownership-is-correct)
independently describes zombie-only EPERM and ESRCH after wait.
EPERM alone and a successful original-PID fallback prove no group absence.
The research record is /tmp/gludd-v012-darwin-zombie-killpg-eperm-research.md,
including retained Open Group403s and bounded retrieval/proof limits.

Root authorized a separate zero-signal lifecycle prerequisite before policy
changes. Both installed runtimes observe live group0; retained original zombie
plus fresh same-birth live original descendant0; original descendant gone with
leader zombieEPERM1; exactly one original wait17; post-reap group0ESRCH3.
No fixture physical signal is sent. Original leader/member max(grace,1)=1
budgets remain; no timeout or canonical gate changed.
311 exec76321 has INNER1PASS0.69s, OUTER125/native1.900s,04:50:16.245544 through
04:50:18.145335 UTC;312 exec73153 has INNER1PASS0.97s, OUTER125/native2.517s,
04:50:38.605234 through04:50:41.122554 UTC.
Exact receipts/logs/traces are under observer-group-zero-prerequisite as
observer-group-zero-prerequisite311-9647b0a and its312 counterpart.
The outer known zombie cleanup failure remains failed/inactive125.
These native inner observations do not supply a green watched execution.

The approved next semantic change may defer ONLY raw native group TERM/KILL
EPERM classifications. It retains every diagnostic, the private PID pin through
the LAST physical signal/full original grace, one bounded original wait and
independent known-member drain. After reap, killpg(P,0) is observation only.
ESRCH may resolve only those deferred group EPERMs;0/EPERM/unknown stays125.
There can be no later physical group/PID signal or fallback.
Identity/census/member/FD/other-errno failures never clear through group absence.
PID reuse can add conservative refusal, and escaped descendants remain outside
this group-scoped proof. No snapshot/UID shortcut or extra budget is authorized.

Independent review also identified positive PPID/birth contradiction followed
by numeric cleanup, FD-finalization gaps and the new top-level psutil dependency
on previously passive stdlib readers. Initial pure exec38429 has3FAIL0.78s but
its helper-call/direct-wait seam overstates consumed-status failure: that state
is already refused at the actual signal boundary. Preserve it as a scope-
incorrect hypothesis, not three established unsafe-signal REDs.
The exact source is /tmp/gludd-v012-observer-positive-identity-helper-seam-red.py.
Corrected controls must observe actual os.killpg/os.kill/native-wait boundaries:
consumed-status preserves its existing guard; wrong PPID/changed birth still
require genuine RED. Mere unavailable inspection differs from positive foreign
identity. FD closure must still finalize handlers/log/status; passive
status/tail/module loading must retain lazy execution-only psutil dependency.
Their genuine controls and fixes are pending; no approval is inferred here.

No312 focused receiving, whole profile, resource-inventory acceptance, full
gate, exact-SHA hosted/deployment or release proof follows these diagnostics.
The unchanged complete four-module observed profile and85/75 line AND branch
floors, complete consumers on both runtimes and full264 resource owner remain
mandatory after genuine repair/focus/staged review. No blind inventory update
is authorized. Root retains sole Git/Main ownership.

## Reviewed repair order and focused acceptance (2026-10-24 UTC)

Genuine review regressions retain exec67312:7FAIL/1PASS1.14s, outer125/
native2.639s,04:56:07.368446 through04:56:10.007606 UTC,
observer-review-boundaries-red311-9647b0a. Wrong PPID/changed birth reach actual
numeric killpg/native-wait adapters; consumed-status preserves its existing guard.
Three real standard-library-only child modes fail at the new top-level psutil
import. Two owned native close wrappers close their actual FD then raise,
exposing missing resource/handler/terminal finalization. Fixture teardown
restores only its own resources; no fatal warning is hidden.
The earlier3FAIL helper-seam hypothesis stays distinct from these genuine7REDs.

The reviewed implementation keeps all work in the existing owner:

1. Known ignored/custom SIGCHLD refuses125 before child creation. The private
   Popen remains the sole eventual reaper under default SIGCHLD; no claim covers
   atomic defense against an external competing reaper or policy change.
2. Each POSIX identity sample creates a fresh Process and checks native
   PPID/birth/state. Positive zombie means completion without consuming status.
   A typed positive contradiction invalidates numeric signal/reap authority.
   Unavailable inspection stays failed while the original private PID pin still
   owns safe cleanup; it cannot trigger an early return that skips that cleanup.
3. Original group TERM, full grace and final KILL run before reap. Original-PID
   fallback uses native os.kill while pinned; Popen signal helpers are excluded.
   The ephemeral cleanup result retains original member identities, only native
   group EPERM classifications and independent hard errors. No new resource
   registry/cache/observer or discovered-PID signal target exists.
4. The caller performs exactly one bounded original Popen.wait. Native wait
   success supplies the child status; a failed wait never permits post-reap
   observation. Independent member drain and any group-zero observation share
   the original max(grace,1) member budget; the leader budget remains separate
   and unchanged.
5. After reap, signal-zero is observation only. ESRCH can resolve only deferred
   group TERM/KILL EPERM.0/EPERM/unknown remains failed; no physical group/PID
   signal or fallback follows reap. Hard census/member/identity/FD/other errors
   cannot clear through absence. No snapshot or UID proves universal descendants.
6. Selector/stdout/log close errors retain a failed result while other teardown,
   handler restoration and terminal publication continue. Pending handled signals
   during cleanup remain visible. Execution-only lazy psutil imports preserve
   passive module/status/tail use with the standard library.
   The non-POSIX execution branch retains its prior behavior; no native Windows
   or Linux proof is inferred from the installed Darwin runs.

The helper's private return contract now carries cleanup outcomes to the caller.
Pure-order controls still assert full grace, no premature Popen reaping, exact
physical signal order and native error preservation; their error-propagation
assertion follows the deferred finalization phase. The fast native control
returns that result through its recorder instead of dropping it. Original
assertions/criteria remain, and no xfail/ratchet/profile/bound changed.

The first repaired17-case union passes17/1.94s, exec78321, outer0/native3.612s,
05:02:25.004885 through05:02:28.616629 UTC,
observer-retained-reviewed-focus311-9647b0a.
The complete narrow union adds9 required negatives. Three real original live
descendant controls use controlled group-signal refusals and an omitted census:
actual post-reap0, controlled EPERM and controlled EIO all remain125 while the
same fresh native descendant is live at SUT return. Their marker/TTL then drains
the fixture naturally; no actual physical signal follows the final wait.
Four failure_kind cases seed the same EACCES-shaped hard-error object with
different labels. They prove hard-error invariance, not four native-operation
fault injections. Separate controls cover unreaped observation refusal and an
actual private child cleaned after unavailable inspection.

Exact26-case union passes BOTH installed runtimes with original90/60/n0/fatal
warnings/ID= and native trace, serializing the identical command reservation:
311 exec68743:26PASS4.43s, outer0/native5.802s,
05:09:33.370179 through05:09:39.172319 UTC;
312 exec50547:26PASS4.68s, outer0/native6.251s,
05:10:09.323948 through05:10:15.574588 UTC.
Exact receipts/logs/traces are under observer-retained-negative-focus as
observer-retained-negative-focus311-9647b0a and its312 counterpart.
Both native receipts are inactive/passed/exit0. Raw native EPERM diagnostics
remain visible, followed only by actual post-reap absence resolution.

Ruff, the test-environment-write guard and strict source-plus-test typing pass.
Initial long-line/explicit-return/export-reference static failures were repaired
at their actual inputs; no check was disabled. Preliminary independent review
at the frozen1897/2303-line production/test snapshots reports no remaining core
blocker and retains final scope limits. It does not supply final approval.
Native state1792818626 is HEAD9647, exactly the three owned tracked WIP paths,
staged0/untracked0; final native process census has only the two watchdogs.

Next proof remains Root staged-content guard, the unchanged complete four-source
observed profile with original85/75 line AND branch floors on both runtimes,
complete affected consumers and native full resource-ownership check in read
mode. Native acquisition arguments remain unchanged; never regenerate inventory
blindly. No serving process, scanner/watchdog route, daemon, lease or release
setting changed. ZDD activation, normal commit/source review, exact-SHA full gate,
hosted/deployment and release completion are still separate pending proofs.

## Complete-profile stderr regression and correction (2026-10-24 UTC)

The first unchanged13-owner coverage execution, exec51933, collected757 cases
and finished3FAIL/754PASS169.57s. Native coverage-files receipt
20261024T053457Z-62448-45430665 is inactive/failed1/native171.432s,
05:34:57.335375 through05:37:48.766843 UTC, with its full log/native trace.
The three original empty-stderr assertions fail at observed_command.py:88 and
gate_refresh_streaming.py:91/116. Captured bytes are only our native deferred
TERM/KILL EPERM diagnostics plus resolution text after successful cleanup.
The coverage audit never executes after failed pytest; no floor/report pass is
claimed. All original stdout/log/status/field and stderr assertions remain.

A requested exact-three scope without coverage confirms3FAIL2.44s, exec9508,
native3.783s,05:40:04.010146 through05:40:07.793712 UTC,
observer-stderr-contract-red311-9647b0a. The failed production postimage is
/tmp/gludd-v012-observer-stderr-contract-red-postimage.py.
Successful cleanup must preserve the existing silent stderr contract; expected
native EPERM is not a reason to rewrite tests or append bytes to the child log.

The correction buffers original operation/PID/signal/errno context in the same
ephemeral cleanup result. Only unresolved or independently failed cleanup emits
those deferred records, labeled deferred_from=before-reap so delayed logging
cannot be mistaken for a physical signal after wait. Hard errors remain visible.
ESRCH resolution and successful cleanup add no stderr bytes. No public status
field, additional output file, logger, observer, identity policy or budget changes.

Exact-three GREEN, exec2043:3PASS2.42s, native3.932s,
05:41:49.154371 through05:41:53.086823 UTC,
observer-stderr-contract-green311-9647b0a. A separate existing real-descendant
unknown-observation control, exec80469, passes1/0.68s with outer0 under
observer-stderr-failure-diagnostic311-9647b0a. Its live SUT remains125 and
actually prints post-reap signal-zero EIO5 plus both retained native group
TERM15/KILL9 EPERM1 records marked before-reap. The fixture then exits naturally.
This is output-retention evidence, not another complete26 or coverage proof.

Ruff, strict source-plus-test typing, test-environment-write guard and293 native
Make target contracts pass after the correction. Tests and all assertions stay
unchanged. Full13 coverage on BOTH runtimes,14 remaining complete consumers and
the native full264 resource-owner check still require actual terminal proof
after the fresh Root staged guard. Prior stage/review/focus receipts retain
their exact previous bytes and scope; no unchanged full-profile retry is allowed.

## Transient post-reap observation diagnostics (2026-10-24 UTC)

The stderr-fixed presequence source completed the unchanged13-owner profile:
exec9771,757PASS178.79s, native181.933s,05:47:04.978847 through
05:50:06.911200 UTC. Receipt coverage-files/20261024T054704Z-98761-d94f4500
is inactive/passed0. Its immutable audit coverage-20261024T055006Z.json retains
native passed:true and displayed aggregate89.9% line/85.0% branch. The auditor
compared a rounded branch percentage: raw719/846 is84.98817967%, so strict
unrounded85% aggregate branch coverage is NOT proved by that passed flag.
Preserve the native result verbatim and repair the separate auditor defect;
never relabel this historical receipt as strict-floor acceptance. Displayed
per-file line/branch percentages are active_work_status85/82.1,
stream_command88/83, summarize_xdist_trace98.8/97.1 and
xdist_trace_plugin96.7/87.5. These are OLD presequence bytes, not current
whole-profile acceptance or proof for312, consumers, resource ownership or release.
For each new profile retain raw covered/total line and branch counts and both
unchanged floors before claiming acceptance; neither display rounding nor
test padding establishes the required threshold.

Independent source review then identified a remaining path: post-reap signal-zero
EPERM was printed immediately even when the next bounded observation returned
ESRCH. Preserve the earlier full-profile receipt; it does not cover this new
sequence. Add a small control to the existing observed-command owner before
changing production. The two cases drive the actual os.killpg API boundary
through EPERM then ESRCH or EIO, assert exactly two observation-only calls, and
retain every original test assertion. This is a controlled boundary sequence,
not a claim about measured native kernel timing.

Exec49697 is genuine1FAIL/1PASS0.83s, native2.331s,
05:57:05.977487 through05:57:08.308664 UTC, under
observer-transient-probe-red/observer-transient-probe-red311-9647b0a.
Only the verified-absence case fails its empty-stderr assertion. The failed
production bytes remain at /tmp/gludd-v012-observer-transient-probe-red-postimage.py.

The minimal correction buffers potentially resolved post-reap EPERM context in
the existing cleanup function until a final unresolved or independent failure.
Retained text is marked deferred_from=after-reap; this is an observation, never
a physical signal after reaping. The EIO case still retains that observation,
the terminal EIO and both original TERM/KILL EPERM contexts. No status, log,
signal target, identity policy, grace, drain budget, public API or release gate
changes. Verified absence resolves only the already-authorized groupEPERM class.

Exec19335 is2PASS0.60s, native1.997s,05:58:12.535430 through
05:58:14.532595 UTC, under
observer-transient-probe-green/observer-transient-probe-green311-9647b0a.
Ruff and strict typing of the complete source and test files pass afterward.
No repeated26-case run is claimed. Freeze all three owned files for a fresh
Root staged guard, then receive the complete13-owner four-source profile BOTH,
the complete14 remaining consumers BOTH and full264 native resource inventory
in read mode. Keep the original300/60 bounds and85/75 line/branch floors.
Normal commit, independent final source review, full gate, exact-SHA hosted,
ZDD deployment and release acceptance remain separate required proofs.

## Complete local acceptance and lifecycle inventory (2026-10-24 UTC)

These receipts bind the unchanged1902-line source and2344-line test postimages
based on9647b0a96125b78126e009c56d9b4769127c5a3d in the creation peer.
They are not Main, a new immutable commit, hosted CI, deployment or release proof.
Independent source-only review is retained in the transient-probe review artifact.
Root's fresh staged guard exec56151 passed/native6.8s/zero new clones.
The separate current-postimage environment guard exec56900 passed/native2.1s
under observer-transient-env-guard311-9647b0a. The earlier lost direct guard
exit remains unverified; a new receipt never supplies that missing old result.

The original complete13-owner/four-source configuration remains
config/coverage_observed_command.ini,300/60,n0/max1, fatal warnings and literal
ID=.311 exec37040 completed759PASS168.87s/native171.252s; receipt
coverage-files/20261024T061515Z-57346-7d1623ef is passed0,
06:15:15.138064 through06:18:06.389895 UTC.312 exec8260 completed
759PASS204.51s/native207.799s; receipt
coverage-files/20261024T061833Z-69599-568cd429 is passed0,
06:18:33.281352 through06:22:01.111642 UTC. Both producers are inactive.

Both native raw JSON reports have identical counts, not inferred rounded flags:

| Source | Covered/total lines | Covered/total branches |
| --- | --- | --- |
| Aggregate | 1960/2179 | 721/848 |
| active_work_status.py | 374/440 | 161/196 |
| stream_command.py | 1023/1162 | 377/454 |
| summarize_xdist_trace.py | 244/247 | 99/102 |
| xdist_trace_plugin.py | 319/330 | 84/96 |

Aggregate raw line89.949518% and branch85.0235849% both satisfy85%;
every individual raw line/branch ratio satisfies75%. The raw reports remain
coverage-retained-leader311.json and coverage-retained-leader312.json beneath
.gate-logs; immutable native audits are coverage-20261024T061805Z.json and
coverage-20261024T062200Z.json. Preserve the historical719/846 below85% result.

Complete13 coverage roster:

```text
tests/unit/test_observed_command.py
tests/unit/test_xdist_trace_diagnostics.py
tests/unit/test_xdist_trace_timings.py
tests/unit/test_gate_refresh_streaming.py
tests/unit/test_observability_guardrails.py
tests/unit/test_active_work_status.py
tests/unit/test_active_work_resource_observability.py
tests/unit/test_pytest_param_fixture_collisions.py
tests/unit/test_runtime_startup_diagnostic.py
tests/unit/test_observer_retention_concurrency.py
tests/unit/test_item_progress_retention.py
tests/unit/test_owned_grandchild_cleanup.py
tests/unit/test_item_progress.py
```

The initial complete14 ordinary-consumer union collected817 but timed out:
exec18053, observer-retained-consumers311-transient-9647b0a,
06:27:49.725207 through06:32:50.277534 UTC/native300.549s, timed_out124
(GNU Make2). Its exact native summary has1305events,651 paired finishes,
652 starts, zero failure reports and one unfinished node:
test_legacy_and_forced_publication_cannot_skip_or_duplicate_runtime[-batch-push-nv]
in test_runtime_publish_admission.py. No partial acceptance is inferred.
Paired wall spans are diagnostic only: reviewed-head196/206.441555s and
runtime-publication23/42.885128s; they are not CPU, critical path or savings.

Root approved a changed, disjoint COMPLETE-owner partition preserving all14
files and817 cases: A=reviewed-head196, B=runtime-publication73, C=remaining12
owners548. Every partition keeps300/60,n0,Werror,literal ID= and native trace.
Serialize identical CMD strings across runtimes and drain the exact terminal
owner. A larger union is not automatically faster; measure the actual bounded
failure before changing the partition. No source or assertion was changed.

All six ordinary producers are inactive/passed0:

| Partition/runtime | Exec | Cases | Pytest seconds | Native seconds |
| --- | --- | --- | --- | --- |
| C311 | 34580 | 548 | 95.70 | 97.665 |
| B311 | 44266 | 73 | 116.56 | 118.257 |
| A311 | 54020 | 196 | 239.55 | 242.825 |
| C312 | 6235 | 548 | 101.14 | 103.146 |
| B312 | 10794 | 73 | 118.06 | 119.752 |
| A312 | 74330 | 196 | 238.37 | 241.746 |

Their native labels are observer-retained-consumers-a, -b and -c.
Each exact RUN_ID is observer-retained-consumers- followed by its partition
letter/runtime and -transient-9647b0a, for example
observer-retained-consumers-a312-transient-9647b0a. Retain the failed817 receipt.

Complete ordinary A and B files:

```text
tests/unit/test_reviewed_head_inputs.py
tests/unit/test_runtime_publish_admission.py
```

Complete ordinary C roster:

```text
tests/unit/test_molecule_validation_budget.py
tests/unit/test_molecule_native_closure.py
tests/unit/test_molecule_native_diagnostics.py
tests/unit/test_ansible_collection_log_ownership.py
tests/unit/test_molecule_validation_resources.py
tests/unit/test_import_time_deep.py
tests/unit/test_ansible_collection_staging.py
tests/unit/test_dependency_uv_acquisition.py
tests/unit/test_run_watched_trace.py
tests/unit/test_backlog_audit_target.py
tests/unit/test_run_watched_command_boundary.py
tests/unit/test_presentation_make_targets.py
```

The mandatory full native inventory then discovered a real lifecycle-pin change.
Exec94818 failed2/native6.691s under
observer-resource-full311-transient-9647b0a,07:28:45.579922 through
07:28:52.271204 UTC. Acquisition arguments are identical, but the native hash
covers kind, owner, acquisition AND teardown. Moving the final POSIX bounded
wait/nonPOSIX original wait into finally adds both direct wait branches to
structural teardown evidence. Unchanged argv does not imply unchanged lifecycle
fingerprint. Inspect the native rule; never blindly regenerate inventory.

Root first authorized one literal inventory row rebind: line484 to713,
old d1375ceeade37d7107eb065caa350bda3646a815e5ad2971b2e6a6c8e817b4da to
f1689201c61bcf3343719711ce2b8b742a6332b793cd077e36d70518daac5c14.
Owner/kind/column/owned/acquisition and all263 other rows remain identical.
That first description omitted two transfer entries. Exec37749 nevertheless
passed the native WRITE0/all-paths owner under90/60:
observer-resource-full311-lifecycle-rebound-9647b0a,
07:31:54.812932 through07:32:01.372854 UTC/native6.560s. Preserve this actual
native pass while withholding semantic approval of its incomplete descriptor.

The checker loader validates shape/nonempty fields/hash syntax, and admission
groups by semantic_key; it does not recompute the stored descriptor's digest.
An independently checked digest exposed the mismatch before final freeze.
Do not guess the new teardown from only visible wait calls or interpret a
matching stored hash as proof that manually entered descriptive fields agree.

Root authorized the existing all-path producer to write ONLY a private
diagnostic inventory, never regenerate the canonical file:
 /tmp/gludd-v012-observer-native-inventory-diagnostic.json.
Exec69371 completed0/resources264 under90/60,
observer-resource-diagnostic311-native-row-9647b0a. This metadata generation is
not admission evidence. Its six-entry stream_command row adds BOTH
_finish_posix_group_cleanup transfers, one with cleanup/reaped=True and one
with group_cleanup/reaped=child_reaped, before the two original transfers and
the two final wait branches. The native sort order explains the exact digest;
independent SHA matches old and actual new native payloads. Source lines and
hash syntax alone cannot replace native metadata inspection.

After separate approval, only row83 teardown was corrected to that exact
six-entry native string. All other row fields, all263 other canonical rows and
count264 remain unchanged. Full native readback equals the original inventory
with precisely the one reviewed row replacement, and the corrected row equals
the native diagnostic byte-for-byte after canonical JSON serialization.
No source/test or checker was changed; Root owns the parallel checker guard.

With fresh no-scanner census and native load1.98x/OK below the unchanged2x
ceiling, exec78998 passes the full original WRITE0/all-paths owner under90/60:
observer-resource-full311-native-six-entry-9647b0a,
07:40:58.683601 through07:41:04.973115 UTC/native6.289s.
RESOURCE_OWNERSHIP_PASS resources264: async-task29/client73/process36/service9/
temp-artifact117. Preserve the original6.691s failure, the metadata-limited
6.560s pass, held prior approval and private native producer evidence. The
complete-row correction proposal remains in the private native-row handoff.

Scope is now exactly four paths including the one reviewed inventory row.
Root must restage/review all four, run the fresh content guard and normal peer
checkpoint; this receipt-only documentation does not justify repeating accepted
source tests. Main receiving, canonical full gate, exact-SHA hosted/deployment
and release acceptance remain pending. Scanner lease coordination remains
separate from this observer repair; no serving process or daemon was activated.

## Long-lived practitioner evidence

The [Python311 subprocess reference](https://docs.python.org/3.11/library/subprocess.html)
describes direct-child timeout and existing session/group controls. Firsthand
[2018 captured-output timeout](https://stackoverflow.com/questions/50338959/subprocess-run-isnt-timing-out-even-though-timeout-is-specified)
and [2024 orphaned-descendant](https://stackoverflow.com/questions/78425430/how-to-kill-a-subprocess-and-all-of-its-descendants-on-timeout-in-python)
reports show recurring operator confusion. Primary
[CPython issue81605](https://github.com/python/cpython/issues/81605) distinguishes
its resolved pipe-hang fix from continuing grandchildren. Reuse the mature
native boundary; those answer snippets are not replacement implementations
or proof that installed311 has the historical resolved hang.
