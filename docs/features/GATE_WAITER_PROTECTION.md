# Native gate waiter protection

## Current native process metadata repair after5e0f1c0

The previous complete repair is committed locally as
5e0f1c0c59790bcb6ace0074fba23453f09d3924:14files637insertions/18deletions.
Its normal fresh collection passes129477selected/129501collected,
24deselected/zeroerrors49.13s, native54.267s and separate lock54.180s:
.gate-logs/observed/test-count/20261023T105857Z-79973-32f0e713.json.
Native worktree-state verifies clean immediately after that commit.
The native task-watchdog-start plan grants no activation; actual mode0
acknowledges80475/startFriOct23 07:00:47 2026/PGID-SID80475.
Native status and ps independently confirm it and agent guard57626 running.
This activates5e only. There is no new full gate, hosted result or task closure.

Before expensive final proof, inspection of both actual native adapters finds
two further boundaries; independent immutable5e source review confirms them.
cleanup._parse_ps_line splits/joins lstart fields, while the producer's
_probe_pid_started_at only strips outer whitespace. Their equivalent native
single-digit-day token therefore differs internally. Normalize only the
cross-adapter comparison with the existing _normalized_start_identity; preserve
the stored producer identity and its native fresh-process comparison unchanged.

The complete native census also retains an unreaped terminal gate, whose argv
is defunct and CWD unavailable. Do not filter that PID from the census or treat
it as an ordinary live foreign process. After exact recorded start/PGID match,
the existing producer _process_is_terminal must positively establish terminal
state; only then may _process_state owned/gone retain waiter association.
owned alone is insufficient because a terminal leader can have live group
members. Reused remains unassociated; unavailable inspection raises refusal
before any signal. Ordinary live CWD/role checks and original bounds remain.

The additive whitespace witness executes both real observation adapters on the
same padded token and restores its scoped subprocess seam before native Make
resolution. Six terminal controls retain gone/owned, unavailable terminal/status,
reused identity and a live non-gate negative. Existing fake producer fixtures
now own their native terminal adapter responses, matching their controlled census;
every earlier foreign/reuse assertion remains. The same family179 [make-pid]
case also executes both new boundaries without new families/selectors/rosters.

Canonical RED11FAIL/2PASS1.43s/native2.4s is retained at
.gate-logs/observed/v012-taskwaiter-native-metadata-red/
v012-taskwaiter-native-metadata-red311-5e0f1c0.json.
Unknown controls capture the former three simulated TERM attempts, not host
signals. Strict typing first refuses a non-exported subprocess alias; the actual
shared standard-library seam replaces it. Ruff's nested-if refusal is repaired
by the same combined predicate, with no suppression or changed assertion.

The unchanged full watchdog/deadline coverage profile passes284cases each:
3117.12s and3128.09s, native runs20261023T111346Z-83676-3dfb0026 and
20261023T111534Z-84488-89c64176 under.gate-logs/observed/coverage-files.
Audits.gate-logs/coverage-20261023T111354Z.json and
.gate-logs/coverage-20261023T111544Z.json measure the complete source at
93.5line/87.5branch, preserving85/85 aggregate and75/75 per-file floors.
Raw reports use.gate-logs/v012-bound-gate-waiter-native-metadata-coverage311.json
and312 sibling. Unit coverage does not supply global/deployed proof.

All eight promotion pins pass1.73s/native3.3s before complete receiving of the
same14whole-file/class/two-node union. It passes1205cases each:
311268.93s/native272.238s and312274.76s/native278.230s, fatal warnings and
zero skips/collection errors. Exact receipts under
.gate-logs/observed/v012-taskwaiter-native-metadata-receiving are
v012-taskwaiter-native-metadata-receiving311.json and312 sibling, both
passed/inactive/native0 and tested source
b365238370daf5ea07c335121317c84be6634696d34e86e0b9586c4409800ec1.
Exact exec58760/16487 sessions are drained before this final evidence handoff.
The full native argv in observed-status remains authoritative for replay.

Independent /tmp/gludd-v012-taskwaiter-native-metadata-source-review.md
approves SOURCE ONLY for that source, no blocking finding. Root fully reads it;
reviewer readback/lint/quiescence completes without Main or process mutation.
All prior artifacts and fixed source scopes remain unchanged. This final
handoff changes only evidence prose, not accepted code, tests or routing.
Validate its status/persistence/Make/Markdown consumers, then retain normal fresh
collection for one complete source commit. The daemon80475 still has5e loaded: after reviewed
commit, verify/drain that exact daemon through native task-watchdog-stop
plan/apply and then consume a new task-watchdog-start acknowledgement/status.
Keep agent57626 alive. Admission, final combined/full-gate and exact-SHA hosted/
deployment/promotion proofs remain required; all seven S98 tasks remain open.

## Actual failure and containment (S98.4,2026-10-23 UTC)

Candidate16ef8dc52c31878724a8107548687d5d4ede400a starts its complete local gate
at09:12:15Z: run20261023T091215Z-e6e51973,PID31529, namespace
gludd-a24d2f0bddee, log.gate-logs/gate-20261023091215-e6e51973.log.
Its original failed script-existence node now passes51cases/4.62s, following
35green preflights and3473PASS/13SKIP integration cases/500.06s. The gate
never reaches a passing terminal verdict. Preserve these as partial evidence.

The outer waiter repeatedly reports sleep children terminated by signal15.
The actual project task-watchdog.log captures matching SIGTERM records from
09:29:11Z, including67949/69219/69397/70376/71303. This establishes the sender;
unit-test names, elapsed time or global plugin task IDs would not. Native read
ownership identifies Make waiter31536 with GATE_EXPECTED_PID=31529, its shell31551
and launcher31513. The waiter is separate from the protected gate tree. Later
the gate is reparented to PID1; ancestry alone cannot cover the waiter.

Root preserves the failed task-watchdog-stop mode1 refusal: its tree acquired
an unverified child. The completed native inspection changes that input; the
next mode1 verifies ownership, then mode0 drains exact daemon57964 using its
captured start/command identity. Agent watchdog57626 and the gate supervisor
remain active. Start mode1 is only a no-write/no-spawn/no-signal plan.

A private reproducer binds rootdir and confcutdir to the same physical
/private/tmp/gludd-v012-taskwaiter-red root, asserts both actual boundaries,
uses complete controlled ProcessRecords and the native protection owner, and
sends no signal. It retains1FAIL/0.12s/native0.8s: only gate/launcher protected.
The canonical addition then retains6FAIL/15PASS0.90s/native1.8s before repair.
Both native receipts remain under .gate-logs/observed/v012-taskwaiter-red and
v012-taskwaiter-canonical-red with their exact16ef8dc/canonical run IDs.

Only after confirmed RED does root end the known-bad candidate's expensive
proof through make gate-kill. The native lock pins exact31529/start/root/
namespace; the owner verifies10targets, records TERM, no KILL, no skipped or
surviving PID and outcome=terminated in.gate-logs/gate-kill-evidence.json.
The lock changes during authenticated native cancellation, so cleanup refuses
that further mutation and preserves it. Native state finishes10:09:00Z; the
exact outer exec7530 drains with ABORTED/native2. No Main edit or HEAD move
occurs before drain. This is operator-aborted proof, not a pytest failure or
a complete gate. Generated /.gate-terminalized remains retained local evidence;
its exact root-only ignore follows the other native status files.

## Existing owner, bounded repair

scripts/task_watchdog.py extends its existing deny-only protection set.
A direct make/gmake waiter must have exactly the gate-wait goal and one literal
positive GATE_EXPECTED_PID= binding. The existing --no-print-directory and
ordinary Make assignments retain their native meaning; no input is evaluated.
The waiter's current CWD must equal a registered root. Existing live PID/lock
protection remains. The same root's current background-gate producer may also
associate that exact PID with its still-live waiter after marker/PID retirement.

The existing start_gate_background GatePaths, JSON reader, identity parser and
canonical-command validator own this association. Require the matching physical
root/namespace, complete run/PID/native-start/PGID/SID identity and canonical
producer argv. An occupied PID also needs its original start/PGID, current owned
CWD and native gate role; old producer state cannot override reuse or foreign
ownership. The existing tree/ancestor owner then protects the complete waiter
through running-to-finished publication, ordinary retirement and cancellation.
Read association again during every native protection revalidation; it grants
neither terminal success nor signal/cleanup authority. Missing/malformed/
mismatched association keeps the unassociated dead-gate case negative.

Discover each waiter's registered CWD once per complete snapshot, group by that
root, and read producer association only for roots with actual bound waiters.
Do not add a cache or repeat native Make resolution for empty historical roots.
The native waiter's original7200/21600 controls remain; no grace deadline is added.

No marker, dead/wrong/unbound/duplicate/zero/alternate PID, extra goal, foreign
Makefile, non-Make/unreadable command, foreign/unknown waiter CWD, foreign gate
CWD and non-gate owner retain independent negative controls. Six valid
Make/marker combinations preserve the complete tree while ordinary stale
owned work is still terminated by the original verified cleanup boundary.
No process-name exclusion, new observer, deadline increase or signal permission
is added. Fresh identity, unknown/new-child refusal, registered ownership,
observer caps, cleanup, fatal warnings and85/75 BOTH remain unchanged.

Family179 adds one real positive case to existing TAIL before the original
last dependency-profile selector. Independent family literals/seeds and literal
rosters retain178predecessors, exactly11TAIL selectors, MAIN's audit exclusion,
three2/1/1 children,90/60 and21phases2850. Promotion RED4FAIL/4PASS1.23s/native2.8s
precedes manifest/recipe wiring; the complete eight pins then pass2.38s/native4.2s.
Existing native result guidance still requires all eight terminal successes
before broad receiving and explicitly supplies no gate/release authority.

The first broader command guesses nonexistent test_kill_owned_gate.py and
collects zero cases/native2/0.8s. Preserve that failed receipt; it supplies no
acceptance. Source search identifies complete native test_gate_process_cleanup.py
and test_gate_kill_adaptive.py owners before correcting the command. Recover
actual filenames and argv before launch rather than inferring a module's owner.

## Native terminal-drain review and correction

Independent source review rejects the live-only working delta before activation:
 /tmp/gludd-v012-taskwaiter-source-review.md is CHANGES REQUIRED, not approval.
A gate can exit and remove its PID marker while its legitimate bound waiter is
still inside the original60-second sleep before consuming the terminal result.
The old missing-gate negative had no producer association and could not establish
that lifecycle window. Preserve the rejected source/test/feature bytes under
/tmp/gludd-v012-taskwaiter-red with the live-only suffix, along with the fixed
ef0be3f4 source receipts. No rejected-source activation or commit occurs.

Five native-producer lifecycle states and a fresh-association revalidation
control are added. The original six valid cases keep every earlier assertion
and also assert protection after native terminal publication and marker removal.
The existing family179 [make-pid] case therefore checks both live operation and
terminal drain, without another family, selector or changed literal roster.
Fifteen further negatives retain absent/malformed/foreign root/namespace/PID/
schema/start/session/command facts, occupied PID reuse/CWD/role/PGID changes and
foreign waiter CWD. Fixtures use the existing producer's command/payload/state
owners and native cleanup status writer in the private root; signals are mocked.

Canonical terminal RED retains12FAIL/15PASS1.21s/native2.2s:
.gate-logs/observed/v012-taskwaiter-terminal-red/v012-taskwaiter-terminal-red311.json.
The revalidation witness records the old owner's attempted three fixture TERM
signals, then requires the new association to revoke every signal before sending.
Strict typing first refuses a non-exported namespace attribute; use its actual
resource_arbiter owner. An exact source edit refuses two matching import lines;
no dependent edit runs until TYPE_CHECKING/direct contexts are qualified.
Those native refusals remain evidence, not behavioral acceptance.

## Retained acceptance of the rejected live-only source

Complete receiving of11whole files and the complete Make variable policy class
passes1035cases on each installed runtime:311242.33s/native245.801s and
312266.84s/native270.352s, fatal warnings, zero skips/collection errors.
Both exact receipts under.gate-logs/observed/v012-taskwaiter-receiving are
v012-taskwaiter-receiving311-corrected.json and
v012-taskwaiter-receiving312-corrected.json: passed/inactive/native0, matching
tested source ef0be3f4f781cd19d686351e7520550fef1fd4d4b177f2a93682a16d0bd0979d.
This precedes the final documentation handoff; receipt scope stays fixed.

Recover the complete actual command from native observed-status with that label,
run ID and OBSERVED_ROOT=.gate-logs/observed before replay. Its11complete files
are test_task_watchdog.py, test_deadline_enforcement_deep.py,
test_gate_process_cleanup.py, test_gate_kill_adaptive.py,
test_active_work_status.py, test_reviewed_head_inputs.py,
test_gate_failure_promotion.py, test_runtime_admission_scheduling.py,
test_admission_cold_start_selection.py, test_runtime_publish_admission.py and
test_make_target_contract.py, all under tests/unit/, plus the complete
test_makefile_targets_deep.py::TestVariableConsistency class.
The original -q -W error -n0 and explicit ID= retain warning and scratch scope.
Execute runtimes serially and consume each exact terminal session before edits.

The unchanged config/coverage_task_watchdog.ini profile runs both complete
watchdog/deadline owners:256PASS each3116.57s/native8.665s and
3126.88s/native9.139s. Native coverage-files receipts are
20261023T102435Z-4423-141a80e1.json and20261023T102515Z-5103-1f709e72.json under
.gate-logs/observed/coverage-files. Actual numeric audits
.gate-logs/coverage-20261023T102444Z.json and
.gate-logs/coverage-20261023T102524Z.json both measure the one complete canonical
source at93.0line/86.2branch percent, aggregate85/85 and per-file75/75 preserved.
Raw reports are.gate-logs/v012-bound-gate-waiter-coverage311.json and312 sibling.
This is unit coverage, not global release coverage or deployed/E2E execution.
## Retained terminal-drain receiving before5e0f1c0

The unchanged full watchdog profile now passes277cases each:3116.96s and
3127.64s. Native coverage-files runs20261023T104207Z-10516-6863e418 and
20261023T104317Z-11265-a81323e0 retain actual terminal receipts under
.gate-logs/observed/coverage-files. Audits
.gate-logs/coverage-20261023T104216Z.json and
.gate-logs/coverage-20261023T104327Z.json measure the complete source at
93.4line/87.1branch, keeping85/85 aggregate and75/75 each-file thresholds.
Raw reports use.gate-logs/v012-bound-gate-waiter-terminal-coverage311.json and
the312 sibling. This unit scope supplies no global coverage or deployment proof.
The old1035/256/70 live-only receipts above remain historical with their actual
source and cannot recertify the terminal repair.

After all eight promotion pins pass1.73s/native3.4s, complete corrected
receiving passes1198cases each311248.81s/native252.110s and
312261.99s/native265.522s, fatal warnings and zero skips/collection errors.
The actual union is14whole files: the earlier11 plus native producer
test_start_gate_background.py and complete test_status_snapshot.py/
test_session_persistence_integration.py, all under tests/unit/. It also retains
the complete variable-policy class and both Markdown structural nodes.
Exact native receipts are.gate-logs/observed/v012-taskwaiter-terminal-receiving/
v012-taskwaiter-terminal-receiving311.json and312 sibling, both
passed/inactive/native0 and source
6becd283c24ba7dbf309f6bc754a9a7e49d5f7d54019914e7ae73afc3e81d9bd.
Recover the full real command from observed-status, not a guessed module name.
Both exact exec sessions64976/72637 are drained before this final evidence edit.

Independent /tmp/gludd-v012-taskwaiter-terminal-source-review.md approves
SOURCE ONLY for that frozen source, no blocking finding. Root fully reads it;
reviewer readback/lint/quiescence completes with no Main mutation or test owner.
The original rejection remains unchanged. This final handoff changes no accepted
code, tests or routing; validate its status/persistence/Make/Markdown consumers.
Keep normal fresh collection for this one complete source commit. Native
activation, admission, final combined/full-gate and exact-SHA proofs remain
separate and required; the1198-case receiving is not a completed release gate.
Use the original runtime admission alone before a new candidate or complete gate.

After reviewed source integration, use the existing native task-watchdog-start
owner once, then verify its acknowledgement/status while the agent guard stays
alive. A start plan or a previously loaded daemon never proves source activation.
Preserve the current workflow-scope refusal and unanswered operator consent
request. Changed source needs fresh review/full-gate/exact-SHA hosted/deployment
proof; all seven S98 tasks remain open. No unchanged push retry, old CI result,
manual JSON rewrite or partial gate can close them.

The existing [resource ownership research](GATE_RESOURCE_LIFECYCLE.md#2026-10-18-task-cleanup-requires-verified-checkout-ownership)
retains long-lived firsthand reports
[pytest-timeout159](https://github.com/pytest-dev/pytest-timeout/issues/159) and
[psutil2335](https://github.com/giampaolo/psutil/issues/2335). They remain
background research, not evidence of this incident's sender, which comes from
the captured native signal records. No fresh network-retrieval claim follows.
