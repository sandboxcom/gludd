# Watchdog Secrets Scan Lifecycle

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
native disk-check fails97percent>95 and generated scratch766.5MB>100MB;
retained artifact sizes grant no cleanup authority.

The separate [readonly-scanner profile](PUBLICATION_READONLY_SCANNER_PROFILE.md)
answers a new parent-scope publication question with1PASS/native4.534s and mostly
subprocess waiting. It selects no optimization and does not explain60907.
Its two distinct trace identities and firsthand fixture-attribution evidence
are codified there. Capacity, original publication, complete canonical gate,
exact-SHA hosted/deployment proof and guarded promotion remain pending.

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
