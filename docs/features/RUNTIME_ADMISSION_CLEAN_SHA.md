# Clean-SHA runtime admission

## Literal script-reference admission after fa50e0b

Root retains Main/shared/Git/S98.4 ownership on the candidate branch. Frozen
fa50e0b099b59d3681cc243501f61c9b31d6f895 passes its nine native admission owners;
the runtime child joins2396/1/11 with native0/80.543s inside90total/60quiet.
Independent source-only approval is retained at
/tmp/gludd-v012-fa50e0b-source-review.md. Its final21-owner combined acceptance
passes893 each on3.11.14 (195.48s/native197.448s) and3.12.15
(194.13s/native196.329s), with matching tested source
a782d4dec7e428da9935b20f69d7ddab028e9a81222c9125f5ac532d3d26ca6c.
Native receipts are .gate-logs/observed/release-proof-combined/
v012-fa50e0b-combined311.json and v012-fa50e0b-combined312.json.

The actual full gate at that SHA then FAILS, retaining
.gate-logs/gate-20261023074307-fe8f7480.log and native run
20261023T074307Z-fe8f7480. All35 preflights, including181 integration files with
zero failures in517.6s, lint, typing and collection pass. The isolated unit child
fails1/34PASS4.87s at
tests/unit/test_makefile_audit_deep.py::TestScriptReferencesExist::test_all_scripts_exist.
Later shards and coverage are not started; the signed attestation is failed.
Its two reported paths, scripts/build/configure-apt.sh and
scripts/build/configure-apt-sources.sh, occur inside fixed upstream runner-image
HTTPS research URLs in make/00-foundation.mk. They are external inputs, while
the audit's substring classifier wrongly treats them as missing project files.

The existing scripts/makefile_layout.py owner now exposes literal
local_script_references. Ordered matching consumes HTTP(S) source tokens before
embedded scripts/ fragments. Whitespace, quotes and shell separators end that
exclusion; existing relative, absolute and Make-prefixed local references remain.
The audit delegates classification to that owner. Its original missing-file
assertion and stale-reference set are unchanged, as are all research URLs,
retrieval bounds, refusal statuses and result guidance. Fifteen additive boundary
cases retain py/sh/mjs, mixed remote/local and separator controls. A separate
confined control executes the original assertion: a present local script passes
beside a remote URL, and exactly one missing local script still fails.

Canonical RED retains11FAIL/34PASS1.13s/native2 about2.1s at
.gate-logs/observed/v012-script-ref-red/v012-script-ref-red311-fa50e0b.json.
The first four-owner receiving retains1FAIL/94PASS23.61s/native24.5s:
v012-script-ref-receiving/v012-script-ref-green311.json under the same root.
All classifier and original audit cases pass; the new control incorrectly pins
plain exception formatting despite pytest assertion rewriting. Its corrected
control asserts the actual missing count/path and exclusion of the remote path,
preserving the original assertion and failure behavior.

Initially promote the original failing node into MAIN of the existing runtime
owner as make-local-script-reference-boundary, family178. Update the native manifest,
independent seed/counts, both literal rosters and open-ended tail expectations
together. Routing RED retains4FAIL/4PASS1.22s/native3.3s; the intermediate
7PASS/1FAIL1.72s/native3.1s identifies a missed independent literal roster.
A refused exact edit changes no bytes; native lint also rejects a167-character
line before broad receiving. Split its literal across the existing two lines,
retaining the2499-line policy. Final eight pins pass1.70s/native3.1s:
.gate-logs/observed/v012-script-ref-routing/v012-script-ref-routing-green311-final.json.
Native structural validation still retains21phases/2850seconds. Original
three-child2/1/1 scheduling,90/60, descendant drain, fatal warnings and85/75 BOTH
remain. Moving this cheap node earlier is routing, not a measured hosted saving.

Complete641-case receiving then retains1FAIL/640PASS150.88s/native152.2s at
.gate-logs/observed/v012-script-ref-receiving/v012-script-ref-receiving311.json.
The unchanged fresh-HELP assertion requires this entire audit file to remain
absent from MAIN. Preserve that assertion and its original selector: move only
the new exact node to existing TAIL. Its independent roster and the added
family's exact route now require counts0/0/1; every predecessor keeps1/0/0.
A refused two-match exact edit stops the dependent seed edit until its actual
function context is read and bounded. The correction retains three children,
existing selectors/flags and native status/drain. Corrected Tail routing passes
all eight pins1.82s and the original fresh-HELP control1/0.44s under one actual
native0/4.3s compound watcher:
.gate-logs/observed/v012-script-ref-tail-routing/v012-script-ref-tail-routing311.json.
The existing eight-pin roster test also now independently excludes this audit
file from MAIN, preserving the same original constraint if a future edit
changes both rosters. Those final eight pins pass1.72s/native3.2s:
.gate-logs/observed/v012-script-ref-tail-routing/v012-script-ref-tail-pins311.json.
The next native cohort collects647 and retains4FAIL/643PASS168.26s/native169.5s
at v012-script-ref-receiving/v012-script-ref-receiving311-tail.json.
Three reviewed-finalization cases pin nine original selectors; the profile
composition guard independently requires its selector last. Preserve that last
assertion and insert the new exact node before the profile guard. Conform the
independent selector count to ten: nine original selectors plus this one.
The same existing eight-pin roster now also independently requires ten TAIL
selectors and the original last profile selector. This carries every observed
sequence constraint into cheap admission without adding a child or observer.
Expanded eight pins pass1.82s, then all five affected original structural
cases pass0.54s, native0/4.5s for the actual whole command:
.gate-logs/observed/v012-script-ref-tail-routing/v012-script-ref-tail-boundaries311.json.
Complete stable receiving passes647 each:3.11.14/149.63s/native150.773s and
3.12.15/171.68s/native172.967s, zero skips/collection errors with -W error retained.
The union is fourteen complete test files, the complete variable-consistency
policy class and two existing structural link nodes. It includes the complete
runtime-publication consumer required for a cohort-schedule change. Both actual
receiving receipts bind unchanged tested source
1ccb5f974b464d87d8bb8a647bbde8edc7d980568c8fd7032082b0a2813d73b0,
before this final manual handoff:
.gate-logs/observed/v012-script-ref-receiving/v012-script-ref-receiving311-final.json
and v012-script-ref-receiving312-final.json. The raw commands retain all selectors,
-n 0, fatal warnings, PID-owned ID= basetemps and explicit installed-runtime tuples.
Drain311 before launching the identical312 CMD; no reservation guard changes.

The unchanged config/coverage_makefile_layout.ini profile covers both native
files through the complete audit/layout/split owners:79PASS each runtime,
55.72s/native57.833s on311 and48.98s/native51.594s on312. Native receipts are
.gate-logs/observed/coverage-files/20261023T083129Z-25438-ebadff18.json and
20261023T083303Z-28968-8d9718ad.json. Actual data reports are
.gate-logs/v012-script-ref-layout-coverage311.json and its312 counterpart.
Both native audits measure97.8percent lines/93.9percent branches aggregate;
makefile_layout96.7/92.3 and split_makefile98.4/95.0 each clear75/75, with85/85
aggregate floors unchanged. Audit reports retain
.gate-logs/coverage-20261023T083227Z.json and coverage-20261023T083355Z.json.
These are unit-source measurements, with no deployed/E2E or full-project claim.
At most two bounded test owners overlap receiving and coverage after native ps
shows only the existing guards; the original runtime admission still runs alone.
This is no measured hosted timing or global resource-lease claim.

Ruff and separate native/test strict scopes pass. Final Markdown/line checks,
contract289 and native Make validation1164/zero duplicates pass. After the final
manual handoff edit, complete status/persistence owners and the two structural
link nodes pass59 each:311/1.66s/native2.6s and312/1.96s/native3.0s.
Exact receipts under .gate-logs/observed/v012-script-ref-handoff are
v012-script-ref-handoff311.json and v012-script-ref-handoff312.json.
Mandatory normal fresh collection precedes the source commit; accepted source
replay is not repeated for this manual receipt update alone. A new frozen candidate
still needs admission, independent review, combined acceptance, the complete
canonical gate and exact-SHA hosted/deployment/reviewed-head/promotion proof.
The fresh native GitHub read still lacks workflow scope; the existing operator
consent request remains pending. HumanTodo is UNFILED and separate from admission.
All seven S98 tasks remain open; prior passes and this repair do not close them.

## Generated probe environments after423153a

Root owns Main/shared/Git/S98.4 on release-candidate/v012-git-mutex-20261022.
The receiving base is423153a15ef1b3b7e6ba903399f76ed5eeb43336, whose normal guarded
collection selects129405/129429,24deselected/zeroerrors49.55s,
native0/54.775s and separate lock54.689s:
.gate-logs/observed/test-count/20261023T035633Z-32732-a1450698.json.
Both prior750-case authorization cohorts remain bound to their earlier tested
source, not this fixture repair. Real workflow consent and publication remain
pending; all seven S98 tasks stay unchecked.

After the actual volume improves from98percent/5.7GiB to95percent/10GiB, a new
native mode0 release-proof-admission clears capacity, gate configuration, task
registration/integrity, all eight promotion pins (1.82s) and resource ownership
(264 entries). Native capacity admits protected/recent876.6MiB scratch as soft
pressure with zero inspection/cleanup errors. Its33 dry-run candidates are plans,
not removals. HumanTodo service facts remain separate from admission.

The next owner, check-test-env-writes, then refuses the generated PATH assignment
at tests/unit/test_root_make_startup_probes.py:46. A bounded failing-first replay
retains native Make2/1.250s:
.gate-logs/observed/env-fixture-red/v012-env-fixture-red311-423153a.json.
The checker and production Make behavior are unchanged. The generated shell
adapter now copies the inherited environment, sets PATH on that private mapping
only for the Ollama lookup, and passes it explicitly to os.execve. All18 original
root-probe cases/assertions remain:16-to0 unused lookups, missing-tool status,
ordinary/-e caller precedence, terminal/worker/container/SHA capture and no
execution of the fake tool. No suppression, allowlist or parent environment write
is added. The same native guard passes0/1.222s after the input correction:
.gate-logs/observed/env-fixture-focus/v012-env-fixture-green311-423153a.json.
Ruff and the separate strict test scope pass.

Complete root-probe and environment-guard owners pass23 under3.11.14,
11.36s/native12.426s, and3.12.15,14.39s/native15.786s:
.gate-logs/observed/env-fixture-receiving/v012-env-fixture-receiving311.json and
.gate-logs/observed/env-fixture-receiving/v012-env-fixture-receiving312-serial.json.
Both bind tested source
be52e6678b0631d4fdd59d4cc41f83769f59e08a0cc4d1d3ace1f77bf6f72dfb
before this documentation/catalog receiving. The first simultaneous312 invocation
is refused before execution because the identical CMD is reserved; no native
receipt exists for v012-env-fixture-receiving312. It supplies no test evidence.
Only the311 terminal receipt clears that ownership input for the serial312 run.
Do not infer the cause of earlier unrecorded runs from this distinct refusal.

The fast repair loop now names the existing cheap environment guard before broad
receiving; its native policy already belongs to admission. The agent-facing
catalog adds its no-variable read-only example. Native contract validation first
refuses that entry because the existing owner is missing from human help; add its
explicit help description before broader receiving. No new checker, runner or
guard exception is introduced. Final complete six-owner receiving passes101 on
each runtime,13.54s/native14.688s and14.47s/native15.743s, fatal warnings with
zero collection errors/skips:
.gate-logs/observed/env-fixture-receiving/v012-env-catalog-receiving311.json and
.gate-logs/observed/env-fixture-receiving/v012-env-catalog-receiving312.json.
Both bind source
108067dcbc3c52af370d2b04ea8fcdee2231bdf90919c3a0a951907bcaed714b
before this manual handoff/source commit. The union retains the complete root
probe, environment guard, both catalog owners, snapshot and session persistence
owners. Native contract289, actual static example0, Make lint/parity, line limits,
all9 changed-path registrations, task integrity and Markdown/status validation
pass. Normal fresh collection remains required before the repair commit; rerun
native admission only after this input is accepted. Admission itself remains separate
from independent review, the canonical full gate/global85-75 BOTH, exact-SHA
hosted/deployment and promotion proof.

Bounded existing model-cache investigation is retained in
/tmp/gludd-v012-423153a-external-input-readback.md. Native inventory is complete
for7 children, zero errors: default general-ludd parent allocates32768bytes and
huggingface0; the model-cache override is absent. That measured default route
cannot repair a GiB-scale deficit. No cache apply or invented allocation pressure
occurs, and no claim is made about unmeasured storage. The source candidate and
failed evidence remain intact while actual external inputs are pending.

## GitHub operator handoff receiving afterd54d7a5

Root now prepares the already-required account-consent step as a reviewable
Make interface, after finding no permanent owner. The
[authorization decision](GITHUB_PUBLICATION_HTTPS.md#make-owned-workflow-consent-receiving-afterd54d7a5)
owns native CLI research, the read-only status owner, offline-default preview,
explicit account binding, provider consent and uncertain-write reconciliation.
This is a concrete blocker repair, not a source/receipt-only cycle for a stop
challenge. No real consent flow has started and no unchanged publication retries.

Prior d54 native collection is129368/129392 with24deselected/0errors48.09s,
watcher53.201s distinct from lock53.117s. Its immutable receipt is
.gate-logs/observed/test-count/20261023T002520Z-41043-db0ac046.json.
Recent native diagnostics retain absent workflow scope, disk97percent/6.1GiB and
four absent explicit HumanTodo runtime bindings. They grant no final admission.

Canonical RED2/0.89s/native1.9s, retained34PASS/1fixtureFAIL7.43s/native8.4s,
corrected35PASS7.14s/native8.1s and read/footer RED4/2.97s/native3.9s precede
full receiving. No old assertion is removed; fixture input is conformed to actual
macOS command resolution. A real unguarded payload witness also first fails1;
quote its space-containing marker with shlex.quote before requiring that same
payload to execute unguarded and remain inert in guarded Main.
Complete16-owner receiving passes750 each311/312,181.70s/native183.654s and
189.45s/native191.456s; both exact receipts and the matching receiving digest are
in the authorization decision. This is focused receiving, not final combined
acceptance. Contract288/1164 declarations, strict/Ruff and native static checks
pass. The actual read example still lacks workflow; preview is offline and the
batch-push safe example refuses at dirty-tree admission. Normal source collection
and final required proofs remain pending. Keep exact714 runtime/source evidence
below bound to714, not a new SHA.
All seven S98 tasks remain unchecked and normal branch/promotion guards stay.

## Bounded external-input waiting after4b4d142

Root owns the coupled S98.4 repair on release-candidate/v012-git-mutex-20261022.
The frozen receiving base is4b4d1428262fd259ee590f6971562ca920ffc6d7, whose normal
collection selects129357/129381,24deselected/zeroerrors48.81s, native0/54.283s;
collection-lock54.195s is separate. Exact receipt:
.gate-logs/observed/test-count/20261023T000850Z-35062-350f2770.json.
That is the earlier handoff commit's collection, not new-source acceptance.

Repeated stop challenges exposed missing guidance in scripts/codex_stop_hook.py:
pending tasks always block stopping, but the reason only says to execute another
task even when progress needs a genuinely unavailable external input. Keep that
blocking decision, fresh challenge, selected native focus, ratchet/milestone
semantics and exact-SHA/deployment requirements. Add explicit ask-once,
interruptible waiting in intervals no longer than60s, preserving the open task,
failed evidence and pending request. A new challenge/elapsed time is no changed
prerequisite. Do not re-finalize, retry unchanged failures or manufacture
source/receipt-only changes solely to appease the hook. AGENTS.md and the mandatory
Make guide codify the same existing event-loop/no-repeat policy.

Four new cases cover initial/continuation attempts with and without selected
focus. Canonical tests-first gives4FAIL/13deselected0.76s, native2/1.8s:
.gate-logs/observed/stop-wait-red/v012-stop-wait-red311-4b4d142.json.
All fail on the absent external-input guidance after the old blocking decision
and challenge assertions pass. No original case/assertion is removed.

Focused Ruff first finds one import-group issue, repaired without suppression.
Strict scope first rejects mixed bare/package module identities for task_scope
and item_progress. Preserve all runtime package imports while making TYPE_CHECKING
imports use the existing bare-script convention; annotate original test fixtures
without changing their assertions. Separate source/test strict scopes then pass.
No native checker option, Make target, pending latch or release criterion changes.

The initial complete five-owner union passes209 each311/312,10.09s/8.69s.
The311 coverage owner nevertheless correctly FAILS1/native11.955s because the
hook has92.9percent lines and65percent branches: the combined87percent report
cannot satisfy85aggregate/75per-file BOTH line and branch. Retain failed receipt
.gate-logs/observed/coverage-files/20261023T002116Z-39087-b5c36653.json and audit
.gate-logs/coverage-20261023T002128Z.json. The initial312 receipt is
.gate-logs/observed/stop-wait-receiving/v012-stop-wait-receiving312.json,
native0/9.747s. Passing assertions did not make that coverage gate green.

Add seven genuine error/entrypoint controls: four malformed inventory shapes
must emit blocking protocol JSON, a missing task ledger cannot clear a ratchet,
an active milestone without excluded backlog stays open, and the native module
entrypoint retains blocking JSON. No original case/assertion changes.
Final complete union passes216 each311/312,10.58s/9.07s, with no skips/errors and
fatal warnings. The311 native coverage owner passes0/12.242s, measuring all70
hook statements and all20 branches at100percent without exclusions or relaxed
thresholds. Report:.gate-logs/v012-stop-wait-coverage311.json; audit:
.gate-logs/coverage-20261023T002316Z.json; exact terminal receipt:
.gate-logs/observed/coverage-files/20261023T002304Z-39662-000c70c1.json.
The312 exact passed/inactive/0 receipt is
.gate-logs/observed/stop-wait-receiving/v012-stop-wait-receiving-final312.json,
native10.046s, tested source digest
c9612162a41992964ef3420147c3628c59df5d2ed04db2f276e9908c68ad7c07.
The five complete owners are test_codex_stop_hook, test_codex_stop_guard_contract,
test_active_work_status, test_pytest_param_fixture_collisions and
test_status_snapshot under tests/unit/. Both exact native terminal receipts are
fully consumed before any later handoff edit.

These are working-checkout focused receipts before manual handoff/source commit;
the100percent measurement covers the changed hook, not global project coverage.
Normal per-commit collection remains pending. No new full gate, hosted run,
authorization, capacity clearance, filed HumanTodo or deployment is claimed.
No plugin TypeScript/configuration is changed and no restart is required.

This is a bounded real repair, not permission to keep editing a frozen candidate
while waiting. Once accepted/committed, await the already-requested inputs. Keep
the exact714 source/runtime proofs below scoped to714; do not rerun its609-case
receiving, original90/60 or unpublished CI lookup solely for this guidance change.

## Retained external prerequisites after tested code714ce2d

Root owns S98.4/Main/shared/Git on release-candidate/v012-git-mutex-20261022.
The last tested code is714ce2d65e3dbeec9d3dbbadced9f983c13c2a3d, clean before
proof with0staged/0untracked. This handoff-only update preserves that fixed ref;
it neither recertifies a later documentation SHA nor authorizes another replay.
The [caller decision](PUBLICATION_CALLER_CONFORMANCE.md) retains complete
receiving, REDs, source scope and the process other models should follow.

Its normal collection selects129357/129381,24deselected/0errors47.54s.
The native watcher passes0/54.341s at
.gate-logs/observed/test-count/20261022T234100Z-13723-e7e319e7.json;
collection-lock54.257s is a separate interval. Root fully consumes independent
APPROVE SOURCE ONLY against23b at /tmp/gludd-v012-714ce2d-source-review.md.
No blocking patch; reviewer/read/test owners quiesce and the existing secrets
scan drains naturally. Native ps shows only the two existing watchdogs before
original proof; neither is disabled.

The actual177-family original312 repair passes2396MAIN78.16s,1HELP0.83s and
11TAIL8.84s, joined all0, native0/81.472s under unchanged90total/60quiet.
Receipt:.gate-logs/observed/runtime-repair-714ce2d/v012-runtime-repair-714ce2d-312.json;
matching trace and /tmp/gludd-v012-runtime-repair-714ce2d-312.log remain retained.
Tested source digest6b29dc397d15f7b162a4469efa9a0c6e4249af2b7cbf062be2edc99bc8af29ff.
Native ci-failure-repair records the selected dc864ae family at full714 after
invalidating old current authority and retaining prior23b history. This is local
original-budget repair, not a full gate, hosted speed gain or deployment proof.

One fresh pipeline-status for714 reports open0/repaired6/resolved49/runs160,
no exact714 runs, Build+Molecule missing,0failed/0pending/0passed/2missing.
Its generated current pointer remains producer-owned, pending/release_ready=false
with capacity hard_blocked=True. The old Oct20 full gate stays FAIL.
No push follows the actual prior23b OAuth workflow-scope denial; remote eb772
remains the last verified/pushed source. Fifteen local commits follow it at714.
Do not poll the same unpublished source or retry unchanged publication/cleanup.

The [HTTPS record](GITHUB_PUBLICATION_HTTPS.md) now retains one bounded
all-account/public-agent diagnostic: only the same sandboxcom OAuth account is
reported, and the agent connection is unavailable. It is no private-key
inventory or write-authorization proof. Actual account authorization and disk
use below95percent are required changed inputs. Separate asynchronous human
requests are pending. The source-only HumanTodo bootstrap decision below now
identifies the existing native route. HumanTodo remains UNFILED until the actual
canonical service/store and explicit credential binding are available; a plan is
not a request or an ID.

After actual credential authorization changes, use make verify-state, the same
normal guarded batch-push with canonical explicit runtime/observer variables,
full expected-SHA verify-remote and native pipeline-status/ci-await. Recheck final
capacity admission before expensive final proof. A handoff edit alone justifies
no repeated accepted source cohort or original-budget repair. Fresh normal
collection remains required per commit. Full gate/global85-75 BOTH, exact-SHA
hosted/deployed-head/reviewed-head and promotion remain pending; all seven
release tasks stay unchecked.

## HumanTodo bootstrap decision at714ce2d: source plan, not filing

Root fully consumes /tmp/gludd-v012-human-blocker-bootstrap-decision.md after
its owner reads it back and quiesces all sessions/readers. This is a frozen-source
decision, with no startup, API/auth retry, credential inspection or Main change.
The existing human-todo-module already supplies check/apply, explicit credential
binding and returned-ID validation. No replacement queue, daemon implementation,
supervisor, observer, cache or new Make target is required.

The native client defaults to http://localhost:8000; the daemon defaults to
127.0.0.1:8000. These resolve a declared address, not current availability or
ownership. create_daemon_app uses GLUDD_CONFIG_DIR or native configuration
discovery and the project overlay. README:280-307 explains that checkout config/
is not automatically discovered. Selecting it explicitly does not establish the
intended live service/store or permission to start another event loop. An explicit
directory lacking general-ludd.yml can fall back to user configuration; do not
use it to bypass the permitted-prefix boundary.

Store precedence in src/general_ludd/db/session.py:171-195 is configured URL,
DATABASE_URL, configured PostgreSQL host, then native SQLite. The SQLite fallback
is declared durable storage, not intrinsically a temporary queue, but it does not
prove the operator service uses it. GLUDD_DB_PATH cannot override a configured
URL/host. Reuse the actual intended persistent store; never create a replacement
TMP database to manufacture a returned ID. Do not require a user-created config
file when an existing authorized native default resolves the fact.

The collection client deliberately has no ambient GLUDD_AUTH_PSK fallback.
Use the existing [explicit controller binding](MAKE_RESULT_GUIDANCE.md#explicit-controller-credentials)
for the intended provisioned name, such as GLUDD_HUMAN_TODO_PSK; missing/empty
binding must fail closed. Never copy the secret into chat, JSON, argv, logs or
receipts. The public readback CLI separately reads GLUDD_AUTH_PSK; that transport
difference must not be mistaken for collection-client fallback. GitHub OAuth and
SSH credentials do not authenticate HumanTodo. Reuse actual request/agent fields;
omit an unknown optional parent_agent_todo_id rather than inventing one.

Prefer the already-owned canonical service. If a local full product startup is
actually required, first clear capacity and establish intended config/store,
port/resource ownership and an explicit authorized credential binding. The public
source daemon wrapper suppresses child stdout/stderr; make bootstrap is
init/lint/test/healthcheck, not service provisioning. The existing run-watched can
instead bound the same foreground Gunicorn factory:
gunicorn 'general_ludd.daemon:create_daemon_app()' with UvicornWorker, workers1,
the native/default or confirmed bind, and visible access/error logs. Use the
installed no-sync311 runtime, STALL_SECS=0, MAX_SECS=300 and the normal project
observation root. No new supervisor, dependency acquisition, numeric-PID adoption
or unrelated service termination is authorized. This is a conditional route,
not a command executed at97percent disk use or persistent/ZDD deployment proof.

Once those actual inputs exist, validate the complete retained request with
human-todo-module HUMAN_TODO_VALIDATE_ONLY=1, then use mode0 for the authorized
request. Supply HUMAN_TODO_ARGS_JSON with the complete real request and explicit
nonsecret credential lookup expression; the mode alone is not a runnable input.
Check-mode changed=true/id=null is not filing. Capture the genuine returned ID
and read it back from the same service/store with the public human-todo show
owner. Health/readiness alone cannot prove configured execution, storage identity,
filing or human notification delivery. After a potentially committed failed POST,
reconcile list/show before another write.

Only unresolved service/store ownership and required credential provisioning
need human facts; native defaults, path selection and health remain native work.
Existing asynchronous questions stay pending. HumanTodo is still UNFILED; do not
repeat a refused endpoint, start a guessed queue, demand manual files or weaken
the95percent capacity guard. This plan grants no gate, CI, deployment or task
completion evidence.

## Retained publication-caller receiving after clean23b96a9

Clean source23b96a98bdcff7143f30596d50d67acdd1faebce has fully consumed
independent APPROVE SOURCE ONLY review at
/tmp/gludd-v012-23b96a9-source-review.md. Normal guarded collection selects
129355/129379,24deselected/zero errors55.65s/native61.3s; receipt
.gate-logs/observed/test-count/20261022T230240Z-24620-adc48150.json.
This fixed-source collection is not a later receiving/commit measurement.

Its actual original312 ci-failure-repair passes2395MAIN81.19s,1HELP1.08s
and11TAIL9.26s, all joined0, native0/84.936s under unchanged90total/60quiet.
Receipt:.gate-logs/observed/runtime-repair-23b96a9/v012-runtime-repair-23b96a9-312.json.
The native ledger records family
dc864ae9899fce43a978cb71e9ffc5898adaaa3b1b40a1d474dc93d3ad8646f5 repaired
at exact23b; this is local repair, not hosted resolution or a full gate.

One normal batch-push then passes all eight pins1.69s/native3.2s and original311
runtime2395MAIN75.79s/1HELP0.54s/11TAIL8.43s, joined0/native0/78.838s.
Receipts:.gate-logs/observed/batch-push-prerequisite-admission/batch-push-prerequisite-admission-38984.json
and .gate-logs/observed/batch-push-runtime-admission/batch-push-runtime-admission-38984.json.
Both runtime observations bind source digest
a74b1a40c80a3139c61c4230932ed7de6629afce1a0d087d180da95789399c7d.
Normal pre-push lint/typing/secrets/collection hooks pass; no separate collection
count was printed, so the earlier commit count is not that hook's measurement.
All model/test readers quiesced before proof; no watchdog was disabled.

Native transport then rejects the changed Pages workflow for missing OAuth
workflow scope: Git1/Make2/no success. Fresh full-SHA remote verification returns
2/mismatch and still names eb772a658a01db4485087813e4478eb6c59cc328.
Fourteen newer commits remain local; no successor GHA or deployment exists.
The [HTTPS record](GITHUB_PUBLICATION_HTTPS.md) preserves actual denial, retrieved
scope/forum context and pending account consent. Do not retry unchanged auth.
Native pipeline-status for23b reports open0/repaired6/resolved49/runs160 and
missing Build+Molecule, pending/release_ready=false. The old local full gate
remains FAIL; disk97percent/7.1GiBfree still blocks expensive final proof.
HumanTodo stays UNFILED: canonical service/config/store/auth facts remain missing;
no invented endpoint/ID or unchanged service retry. The user-input request is
not represented as a filed HumanTodo.

Root now owns one substantive publication-caller repair and its shared guidance.
The omitted whole consumer exposes43FAIL/29PASS58.17s/native59.2s from old
shell-envelope/six-pin/serial expectations. Preserve exact original assertions
while conforming inputs. Additive AGENT_NEXT and family177 first fail before
source edits. The [caller record](PUBLICATION_CALLER_CONFORMANCE.md) retains
REDs and complete twelve-owner609PASS each311/312,152.84s/native154.026s
and160.17s/native161.723s, exact terminal receipts passed/inactive/0 at the
same tested source digest before this manual handoff/source commit. All176 predecessors,
eight pins, original2395 cases and2/1/1 bounds remain, plus one cheap caller
selector. New clean-source original90/60, full gate/global85-75 BOTH, exact-SHA
hosted/deployed-head and promotion remain pending; all seven tasks stay unchecked.
No local pass is a hosted throughput measurement. Current JSON remains native-owned.

## Retained bounded cohort receiving from6387304

Root owns Main/shared/Git on release-candidate/v012-git-mutex-20261022; HEAD
remains6387304ef5edc1b38ad8a2062c27e92ba430e344 while the changed working
checkout receives the native overlap proposal. Its complete decision/research/
postimages are consumed from /tmp/gludd-v012-runtime-concurrency-proposal;
that model owner and all private test/read sessions are quiescent. No production
checker/config/roster change, additional runner, observer or cache is introduced.

MAIN2 runs beside the unchanged fresh HELP1 then TAIL1 branch, at most two
active admission cohorts/three execution workers, not a global process lease.
Literal ID= keeps each child in test-files' existing PID basetemp/Ansible temp.
HELP failure skips TAIL; all ordinary paths join MAIN and preserve every status.
The unchanged outer observer alone drains timeout/signal descendants. All raw
selectors/flags,176families/eightpins,90/60,21phases2850,85/75 BOTH,ZDD/cleanup
and retention remain. The [throughput decision](RUNTIME_ADMISSION_THROUGHPUT.md#bounded-native-cohort-overlap-2026-10-22-utc)
retains native research, firsthand context, isolation and platform limits.

Canonical tests-first RED8FAIL/3PASS16.58s/native17.8s is retained at
.gate-logs/observed/runtime-overlap-red/v012-runtime-overlap-red312.json.
Root's added AGENT_NEXT guidance control first fails1/1.37s/native2.6s at
.gate-logs/observed/runtime-overlap-guidance-red/v012-runtime-overlap-guidance-red312.json;
the implementation then retains original status exits and teaches next proof.
Original roster/no-replay assertions remain; only canonical phase comparison
and independent-side completion conform to the requested schedule. Native
ID-removal collision83 and124/143 descendant drain remain additive controls.

Cheap lint/strict and native285contracts/1162targets/1073help/line/task checks
pass. All eight native pins pass1.80s/native3.5s at
.gate-logs/observed/runtime-overlap-pins/v012-runtime-overlap-pins311.json.
The first whole ten-owner497-case receiving fails24/473PASS93.93s/native95.0s:
.gate-logs/observed/runtime-overlap-receiving/v012-runtime-overlap-receiving311.json.
Twenty-three old route checks assume separate recipe prefixes; four literal
reader edits retain exact three-child, selector, flag and policy assertions.
One reviewed-head fixture read now uses compose_makefile while preserving its
original exact bytes. No guard or assertion is weakened; preserve the failure.

The corrected complete twelve-owner union passes738 cases on each runtime:
311227.79s/native230.710s;312229.45s/native232.480s. Both exact terminal
observed-status reads report passed/inactive/exit0, without errors or skips.
Native receipts under .gate-logs/observed/runtime-overlap-receiving are
v012-runtime-overlap-receiving-final311.json and
v012-runtime-overlap-receiving-final312.json; matching tested source digest:
ba9606009ac99fec1eeea3ccfad97492d1a895a70474f96e2b1fb48f6a6cc144.
Both run actual ordinary Main harness/plugin controls; no production source
changes after acceptance. These working-checkout receipts precede this manual
evidence handoff and normal source commit; they are not final exact-SHA proof.

Normal guarded collection, independent
new clean-source review and first changed-input original90/60 repair remain
pending. Thirteen newer commits remain local; the current native handoff still
reports638 missing exact workflows/pending and hard-blocked capacity. No local
focus/source approval supplies full gate, exact-SHA CI, deployment or release
proof. All seven tasks stay unchecked. Generated current JSON remains producer-owned.

## Retained original-budget failure at6387304

Source6387304ef5edc1b38ad8a2062c27e92ba430e344 is the coherent candidate
after the two independent source commits. Normal guarded collection selects
129348/129372,24deselected/zero errors46.23s/native51.4s, receipt
.gate-logs/observed/test-count/20261022T221903Z-62261-afdf4695.json.
Native worktree-state confirms clean, zero staged/untracked before proof.
Root consumes the complete independent APPROVE SOURCE ONLY decision at
/tmp/gludd-v012-6387304-source-review.md; every model reader/test owner quiesces.
The existing natural security scan drains; neither watchdog is stopped.

The first changed-input original312 repair FAILS124/90.559s, enclosing
ci-failure-repair2. Main passes2395/79.53s; fresh HELP passes1/0.69s.
Tail collects11; historical focus passes, finalization/native background
producer is unfinished. There is no runtime-family repair receipt or push.
Exact native receipt:
.gate-logs/observed/runtime-repair-6387304/v012-runtime-repair-6387304-312.json;
trace and /tmp/gludd-v012-runtime-repair-6387304-312.log are preserved.
Tested source digest:
05ca2a278af79b019c2ecb7bd70f5c7789cdea19e0dcfe5f8c8743abbde46148.

Native trace summary is PARTIAL:4808events,2398starts,2397pairs,
zero failure reports and one unfinished node. Paired-wall worker totals
68.486884/68.389598s overlap; they are not CPU or the enclosing critical path.
Historical-focus pair is2.682431s, HELP pair0.292557s; unfinished duration
and remaining obligations are unknown. Inventory5.642849s/setup and
SearXNG-source25cases/5.022801s are measured spans, not removable work.
Do not repeat any unchanged inventory/stress/heartbeat measured decline.

The unused Ollama count reduction remains real operation evidence but this
actual run demonstrates no original-budget success or measured speed gain.
Root now owns a bounded architecture proposal about overlapping the existing
MAIN2 with sequential fresh HELP1/TAIL1, at most two active cohorts/three
workers. No scheduling change, extra worker allowance or proof waiver is
accepted yet. Research mature native Make/POSIX facilities and actual resource/
namespace/status/drain prerequisites before choosing a concrete implementation.
No new runner, observer, daemon or cache, selector pruning, deadline increase,
plugin disabling, weaker assertion or unchanged90/60 replay follows.

Separately, actual native mode0 HTTPS configuration returns0 and verifies the
already-published branch/full eb772a658a01db4485087813e4478eb6c59cc328.
The changed HTTPS missing-head example retains native2/no VERIFIED.
Manual operating history:/tmp/gludd-v012-6387304-transport-observation.md;
the [HTTPS feature](GITHUB_PUBLICATION_HTTPS.md) distinguishes that authority.
Native verify-state reports CLEAN6387304 and remote eb772, no exact638 workflows,
missing Build and Molecule; its report exit0 is not CI success.
Native pipeline-status then returns2 and writes the existing current pointer
.gludd/release-proof-status-release-candidate-v012-git-mutex-20261022.json
for6387304 with state=pending/release_ready=false, no exact workflows and
hard_blocked capacity. Its native producer owns that JSON; no manual observation
edit or stale eb772 success is adopted. The old failed local gate remains failed.
All thirteen newer repair commits remain local. Last hosted eb772 remains
Build/PagesFAIL and six Molecule lanesPASS. Capacity/full gate, global85/75
BOTH, exact-SHA hosted/deployed-head proof and promotion remain pending.
All seven release tasks stay unchecked; all176families/eightpins,11tailcases,
90/60,2/1/1,21phases2850,ZDD/cleanup/retention remain required.

## Retained receiving after64bafe6

Root consumes both frozen TMP decisions; all model readers/executions quiesce.
Only Make10's optional Ollama discovery and its existing install-check change
for the runtime input. Preserve eager gate-SHA capture, TERM/fallback, worker
integer/auto/0, container discovery, original selectors and all176families.

Canonical tests-first unused-probe RED is2FAIL6.28s/native2/7.4s:
.gate-logs/observed/root-make-probe-red/v012-root-make-probe-red312.json.
Both ordinary and environment-priority calls finish every original native
focus/watchdog assertion, then fail only the added count: Ollama16, TERM16,
workers16, container16. The recursive default removes unused lookup; the
consumer binds it once and retains missing-tool status and caller precedence.
This is operation evidence, not measured speed or an original90/60 pass.

The distinct HTTPS input reproduces5FAIL2.16s/native2/3.4s in
.gate-logs/observed/https-publication-red/v012-https-publication-red312.json.
Its complete source and receiving are documented in the separate
docs/features/GITHUB_PUBLICATION_HTTPS.md proposal owner. At this
receiving point no live auth/configuration, remote success or push has occurred.

One deduplicated eleven-owner union passes471 each under installed311/312:
160.77s/native165.326s and168.62s/native171.481s, fatal warnings, no skips or
collection errors. Native observed-status confirms passed/inactive/exit0 for
.gate-logs/observed/publication-probe-receiving/v012-publication-probe-receiving-canonical311.json
and its canonical312 counterpart. Both bind tested source digest
dd5cee8dba3d954f02739c8977af07b0f410406ab59e3ba94120a35bf7e71e12.
This is combined working-checkout acceptance before sequential source commits,
not isolated intermediate-commit proof, a full gate or hosted success.
The union retains complete new73, original verify8/SSH3, both Make-contract
owners, help throughput, variable-context, trace-input, reviewed-head196 and
namespace59 owners. No test in an existing owner is altered.

Failed diagnostics remain immutable: private tests.py shadows Main's tests
package (0collected/1ERROR); the next/final comparison is10FAIL/10PASS15.86s.
Its ordinary native case demonstrates16to0 unused lookups, while new harness
controls expose inherited Make/PYTEST_ARGS carriers and one wrong legacy
environment-priority count. The canonical additive owner isolates MAKEFLAGS,
MFLAGS, MAKEOVERRIDES and inherited PYTEST_ARGS; all original assertions stay.
The legacy environment-priority eager lookup remains1; CLI remains0.
Canonical Ruff additionally requires its normal Main import-group separator.
No third private execution or favorable wall comparison is claimed.

Root's first combined receiving command guesses a nonexistent fixture filename:
both runtimes collect0/pytest4, watched2/1.1s. Preserve run_ids
v012-publication-probe-receiving311/312, their logs/XML/JSON. The actual prior
producer receipt names test_pytest_param_fixture_collisions.py; correcting only
that command input produces the471-case results above. Read exact prior native
argv before constructing a reused cohort; never relabel a collection refusal.

Make contract/duplicate/line checks, strict one-file test scopes and actual
documented behavior are separate. The HTTPS mode1 plan returns0 without writes/
auth/network. Deliberate missing Ollama and explicit missing SSH-key examples
remain nonzero; neither is failure of those refusal controls. Original90/60,
full capacity/gate/global85/75 BOTH, exact-SHA GHA/deployment and promotion remain
pending; all seven release tasks stay unchecked. Newer repairs are local only.
Last hosted eb772 remains Build/PagesFAIL and Molecule all6PASS.

First normal source commit is ef41a2273534fde218a3f9b01a81af287cc6584d:
Make10/default-and-consumer, additive18-case owner and existing guidance/history.
Native collection selects129348/129372,24deselected/zero errors48.51s/native53.7s;
receipt test-count/20261022T221636Z-60852-f5eb7d40.json under .gate-logs/observed.
This intermediate collection includes unstaged HTTPS Make/Foundation source and
its untracked test. Its source-only authority is not a coherent clean-candidate
or isolated ef41 proof. The separate HTTPS source commit follows; all accepted
source tests remain unchanged. No metadata-only commit or repeated union follows.

## Retained original-budget failure at64bafe6

Reviewed source is64bafe6aab4aefac3205a8cc4348ecf6268e66b1 on
release-candidate/v012-git-mutex-20261022. Root owns Main/shared/Git/S98.4.
Normal guarded collection selects129275 of129299 collected,24deselected,
zero errors,53.35s/native59.7s; receipt:
.gate-logs/observed/test-count/20261022T213702Z-96206-9c09bda0.json.
Root consumes the complete independent APPROVE SOURCE ONLY decision at
/tmp/gludd-v012-tail-64bafe6-source-review.md. All model readers/executions
quiesce; source is clean and frozen before the actual original-bound repair.

The existing watchdog's natural scan-9c9ddc4495b2409fbfa2ee0cb36bbc25
terminates124/60.550s and its process group drains before runtime starts.
No guard is disabled, scan killed or deadline changed to manufacture idle.
The first actual changed-input312 repair then FAILS124/90.571s; enclosing
ci-failure-repair exits2 and grants no runtime-family repair receipt.
Main passes2395 in78.07s and fresh HELP passes1 in0.70s.
Tail collects11; historical-focus and finalization pass, then native focus/
watchdog ownership is unfinished. Remaining obligations stay unproved.

Exact failed receipt:
.gate-logs/observed/runtime-repair-64bafe6/v012-runtime-repair-64bafe6-312.json;
matching trace and /tmp/gludd-v012-runtime-repair-64bafe6-312.log are retained.
The tested source digest is
915c5fe4c0f937583a0405d74ce5f69508167d9a46b0b972c75063385b808b79.
Native trace summary is PARTIAL:4810events,2399starts,2398complete pairs,
zero failure reports and one unfinished node. Exit0 is generation only.
Main worker paired-wall totals67.121432s/66.877923s overlap; neither is CPU,
critical path or a forecast. The unfinished node is not proved the sole cause.

The17-case secrets-resource input, measured5.669940 paired-wall seconds on
one worker, receives a bounded diagnosis and measured decline below. Its
standalone fragment never loads Foundation. The distinct current runtime
proposal investigates actual root-Make probe execution in the required native
focus/watchdog case; no duplicate or savings is inferred from source presence.
It preserves all native core/env/argv/status checks, previous measured declines
and landed batching. Another TMP owner prepares repo-local HTTPS support: native ci-auth reports active
sandboxcom authentication/protocolhttps/repo scope, while the remote is SSH
and the workspace deploy key is missing. Neither owner mutates Main/Git/CI.
Root receives tested postimages and owns shared help/contract/guidance.
At most two execution/testing owners; no new scanner/parser/runner/cache.
No unchanged original replay or publication bypass follows either proposal.
All176families,2/1/1,90/60,21phases2850,85/75 BOTH,ZDD remain authoritative.

The release-throughput guide now opens with the actual hosted trigger/proof map
and links active handoff owners before dated history. Its first edit is refused
at2533physical lines. Reflowing three historical paragraphs retains every word,
link and fact and returns the unchanged less-than2500 checker to PASS. No line
threshold, assertion or evidence is removed. Status-snapshot validation also
passes; this documentation check is separate from acceptance/gate/hosted proof.

## Consumed standalone Make40 startup decline

Root consumes /tmp/gludd-v012-make-startup-throughput-proposal/decision.md
and its complete terminal receipts. Original native failure-status/cleanup case
passes1/0.72s, observed0/1.7s, before instrumentation. The complete original
owner then passes17/6.06s, observed0/6.8s, with fatal warnings/no skips/errors.
Profile:profile-14122-c5454d2662f239f9 under .gate-logs/test-files-cprofile.
No third execution or speculative production postimage follows.

The actual15 native Make calls parse a copied standalone Make40. They do not
load Foundation or execute its TERM/worker probes. Two remaining cases inspect
routing/text only. Parent communicate/poll5.697s/5.692s enclose actual children
and reveal no child CPU/startup cost or redundant operation. All17 original
assertions retain actual core/env/argv/status, below-four refusal, five workers,
readonly baseline, malformed/new findings and disposable cleanup. This fixture
uses the established controlled scanner, not a successful full repository scan.

No Foundation patch can be justified by that workload. The distinct actual
root-Make producer/plan/invalid-literal calls in the required native-focus case
are the next diagnostic input. Preserve TERM capability/fallback, workers,
ordinary/-e/literal inputs, aliases and exact gate capture; demonstrate actual
unused work before changing source. All readers/children drain. Current disk
measurement remains97percent/7.2GiB free; no unchanged cleanup/final gate follows.

## Actual live-worker sample, separate from runtime

Root captures one bounded native sample during that naturally scheduled scan,
without starting another scan or changing its argv. Native ps/active-work-status
bind worker99204 -> scanner99194 -> adapter99193 -> uv99192 -> shell99189 ->
Make99181 -> watchdog57626, with installed311 and repository CWD. Intermediary
rows remain explicitly unverified; actual parent relationships, tracked worker/
scanner identities and the native scan receipt establish the chain.

An initial observer start refuses before child launch because TMP OBSERVED_ROOT
is outside the project-private item-progress boundary. No new sampler receipt
or output exists from that refusal. Root changes only the root to the existing
.gate-logs/observed, retains owned TMP log/output, rechecks the live chain and
runs native sample for1second/10ms under max15/quiet10, heartbeat5/retain2.
Native result0/3.8s is diagnostic only; worker identity is rechecked afterward.
Receipt:.gate-logs/observed/secrets-worker-profile/v012-secrets-worker-99204.json.
Raw output:/tmp/gludd-v012-secrets-throughput-proposal/evidence/live-worker-99204.sample.txt.

The sample matches worker99204/parent99194 at17:44:11.387-0400. Its83main-thread
samples include regex search/match/count frames; collapsed top rows report
sre_ucs1_match34 and sre_ucs1_count19. This establishes regex activity in one
worker/window. It does not identify a detector, Python function, file, CPU
share, whole-scan cause or clean scan. No speculative scanner patch or second
sampler follows. The next missing attribution is the actual Python operation/
input binding; all27plugins, verification/full scope, canonical occurrence/
baseline comparisons, readonly cleanup and60/300/five-worker policy remain.
The retained earlier measured decline below keeps its original historical scope.

## Required-tail prerequisite batching

Root consumes the terminal proposal and frozen postimages under
/tmp/gludd-v012-tail-throughput-proposal; all proposal executions/readers drain.
The changed input is only the existing reviewed-head test fixture. Preserve its
first real Makefile commit, then materialize TASKS, both exact adapter files,
optional child probe and native test before one native add/commit/rev-parse.
This removes9/12 actual Git launches per plain/probe setup. Source, integration,
tags, cherry-pick/merge and all production validation remain real and unchanged.
The genuine first ancestor preserves the original source~2 refusal.

The two additive native controls verify complete tracked prerequisite paths,
exact fixture content/adapter hashes, real application/parent bindings and the
SAME wrong-parent ValueError before any patch inspection. All existing cases
and assertions remain. Root receives tests-first into Main:2FAIL4.46s,
native2/5.8s, at the redundant commit-count assertion after those semantic
controls pass. Receipt:tail-prerequisites-red/v012-tail-prerequisites-red312.json
under .gate-logs/observed. Root then receives the minimal fixture postimage.
No production Python, Make, selector, family or coverage configuration changes.

Both private profile attempts fail and remain preserved:baseline collection
ERROR0cases, then paired4FAIL2PASS8.45s from missing inherited Main-root import
context after child CWD changes. They are not compatibility, timing or CPU
proof. No third profile is run. The bounded operation GREEN3PASS2.34s is distinct
from complete acceptance. Root uses the ordinary canonical Main harness for
complete reviewed-head and fixture-namespace owners on installed311/312,
with fatal warnings and native watched receipts. The actual original90/60
runtime family and all11 tail cases remain required; a smaller Git operation
count does not establish material deadline improvement.

Canonical complete receiving passes255cases each:311129.92s/native132.792s,
312133.77s/native136.316s, no failures/skips/collection errors. This is the
complete reviewed-head owner196 plus fixture-namespace owner59, serial pytest
per runtime with two runtime owners in parallel. Native observed-status
confirms passed/inactive/exit0 for
.gate-logs/observed/tail-prerequisites-receiving/v012-tail-prerequisites-receiving311.json
and its312 counterpart; both tested source digests are
575d92836cb18ad6c190449d4f92aa751c360d181a8fe265f5d43a261a52ee9e.
All three original reviewed-head tail cases pass in each complete owner.
Ruff and separate strict test scope pass. These300-second scoped observations
are neither the original90-second combined owner nor a speed benchmark.
Only manual guidance/receipt changes follow these fixture tests.

Root also runs the exact complete11-case required tail together on312,
retaining its original9selectors, parameter expansions, -n0/maxprocesses1,
ignore-glob and fatal warnings. All11PASS8.36s, native0/9.427s, no skips/errors.
Receipt:.gate-logs/observed/tail-prerequisites-tail/v012-tail-prerequisites-tail312.json;
tested digest1bc31809c4e58ef8478d1479582eeff3abdd6d88066544c88a935fea06c6b565.
This tail-only component run does not establish that main/help/tail together
fit the original90/60 envelope. No paired timing/savings claim follows.

## Separate watchdog scan: measured decline

Root consumes the complete terminal decision and receipts under
/tmp/gludd-v012-secrets-throughput-proposal. Two actual pinned1.5.0 CLI child
controls pass1case each in0.41s/0.44s, watched0/1.106s and0/1.144s. All27
native default plugins and verification remain enabled. The private one-file
branch runs serially despite requested cores5, so these synthetic64/1000-space
fixtures do not measure the real five-worker repository scan or reproduce60s.
Child profile wall0.226s/0.263s does not identify the actual timeout cause.
No speculative production scanner patch or extra execution follows.

Natural watchdog scans remain failed, including
scan-ce21a57fc82a470d806ce5591f6f5716 at124/60.521s. They are separate from
runtime admission; no causal relationship is inferred. Preserve full canonical
scope, baseline/plugin/occurrence comparisons, readonly bytes, native failures,
five workers,60scan/300cooldown and owned-group cleanup. Do not replace traversal
with a Git filename list or remove detectors/verification to obtain green.

At that earlier decision, the missing input is a bounded stack from a verified currently live worker
of a naturally scheduled scan. Use ps and active-work-status to bind the fresh
worker/scanner/watchdog, interpreter and workspace before and after sampling;
expired625xx/662xx identities are not reusable authority. The decision retains
the mature native Darwin sample diagnostic and its unexecuted command. Attach
refusal, exited/idle worker or unavailable symbols stays inconclusive. A stack
may narrow an operation but supplies no Python file identity, CPU or clean scan
by itself. No new scanner, observer, daemon or proof cache is authorized.

Firsthand upstream reports [433](https://github.com/Yelp/detect-secrets/issues/433)
and [868](https://github.com/Yelp/detect-secrets/issues/868) motivate attribution
and a tiny whitespace control; neither diagnoses this repository. [Apple's native sampling guidance](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/CodeSpeed/Articles/DiagnosingSlowness.html)
supports that bounded diagnostic. That proposal ran no sampler and cleared no scan timeout;
the later actual sample above retains its separate diagnostic scope.

## Retained original-budget failure at2ca8f8e

Source2ca8f8e8e108e3d03ff61bc30adb37a71e64c683 is natively committed and
clean before the first original-budget repair. Branch remains
release-candidate/v012-git-mutex-20261022; Root owns S98.4/Main/shared/Git.
Mandatory collection is129273selected/129297collected,24deselected/0errors
48.75s/native54.1s at
.gate-logs/observed/test-count/20261022T211547Z-47053-6f447087.json.
Root consumes the complete independent APPROVE SOURCE ONLY decision at
/tmp/gludd-v012-shell-2ca8f8e-source-review.md after all reviewer sessions
and readers terminate. It is not a runtime or platform approval.

Existing watchdog-secrets run scan-914609abb9da4eb3be793ae0ad28b26b terminates
TIMEOUT124/60.562s under its own owner57626. Root preserves that failure and
checks native ps after its process group drains; only existing watchdogs remain.
No guard is disabled, scanner killed or source changed to manufacture idle.
All model proposal/review owners are quiescent before the original repair.

Root then runs actual ci-failure-repair for the runtime family on this exact
clean SHA, through run-watched and the unchanged
_pytest-runtime-compatibility-admission command on installed no-sync312,
STALL_SECS=60/MAX_SECS=90. This first changed-input attempt FAILS124/90.585s;
the enclosing Make returns2 and no repair receipt is granted.

The main child passes2395 in79.46s and fresh HELP passes1 in0.72s.
The required tail collects11. Its first historical-focus case passes; the
second finalization/native-background-producer case is unfinished when the
watcher terminates the group. Remaining required work stays unproved.

Exact evidence:

- .gate-logs/observed/runtime-repair-2ca8f8e/v012-runtime-repair-2ca8f8e-312.json
- The matching .pytest.jsonl and /tmp/gludd-v012-runtime-repair-2ca8f8e-312.log.
- Native tested source digest ae351198905550db193870cd44f4621c8f259ab73696deb5c94b9cbdc75fa8ec.

Native test-xdist-trace-summary generates a PARTIAL diagnostic:4808events,
2397complete pairs,2398starts, zero failure reports, one issue/unfinished
node at test_finalization_accepts_actual_native_background_producer_command.
Its exit0 means summary generation only. The first tail pair is2.851290s;
HELP is0.320388s. Main worker paired spans68.296764s/66.668638s overlap and
are not CPU or critical path. The unfinished node is not established as the
sole deadline cause. No measured causal speed improvement is claimed.

The next actual repair input is that required native tail, preserving every
selector and all11cases. A bounded TMP owner profiles the three existing
reviewed-head cases and prior prepare_inputs/Git decisions before proposing
an operation/input reduction. A second bounded TMP owner investigates the
separate existing full-scan timeout without replaying an unchanged full scan.
Root owns canonical receiving, shared files and commits; at most two execution
owners, no new scanner/runner/observer/cache, no coverage/warning/deadline cuts.
No next original-budget attempt is admitted until actual failing inputs change.

The e82 local Pages-input repair remains valid ancestral push evidence under
the existing ledger guard; it is not reattributed to2ca or called hosted-green.
The ledger's native contract distinguishes repaired descendant-push permission
from exact-SHA hosted resolution. Do not repeat unchanged optional component
proof merely to move a receipt to a newer descendant. Native push/freshness
guards remain authoritative. Full gate/capacity, global85/75BOTH, exact-SHA GHA,
deployed reviewed-head acceptance and promotion remain pending. All seven
release tasks stay unchecked, and published baseline remains eb772.

## Preserved failure at35c08ba, 2026-10-22 UTC

Root owns S98.4 and the sole Main/shared/Git writer on
release-candidate/v012-git-mutex-20261022. The immutable candidate is
35c08ba94e4e5af2d6f994d29e9562ee35ed72a5. Native worktree-state reports clean,
zero staged and zero untracked paths before the actual repair execution.
All seven S98 tasks remain unchecked;132 backlog items are excluded and the
ratchet is empty. This manual feature record is not a current observation.

The four small native guarded commits are32bb619 (status),074e29f (SESSION),
0f31a44 (URI) and35c08ba (trace). Their mandatory working-checkout collections
each report129268selected/129292collected,24deselected and zero errors.
The first three preflights include other uncommitted receiving paths and do
not certify their isolated intermediate committed trees. The final trace
commit's complete coherent batch collection takes47.85s/native53.0s:
test-count/20261022T203931Z-88280-fded419d.json under
.gate-logs/observed. Owning feature receipts retain actual focused and coverage
scope; none supplies a final gate or hosted result.

Independent review approves SOURCE ONLY for this exact SHA against
5dc22cb9d885636df107dbbda6d28ead94a048c2. Root consumes the complete decision
at /tmp/gludd-v012-boundary-35c08ba-source-review.md after all reviewer readers
and exec sessions terminate. The earlier ad4 URI rejection remains preserved.
The reviewer runs no tests, live observations or Git mutations. Approval
does not close runtime admission, a release task or deployment acceptance.

Root then executes the first original-bound repair through ci-failure-repair
for runtime family
dc864ae9899fce43a978cb71e9ffc5898adaaa3b1b40a1d474dc93d3ad8646f5,
with the exact clean SHA, evidence target run-watched, the unchanged
_pytest-runtime-compatibility-admission command, Python3.12 installed no-sync,
STALL_SECS=60, MAX_SECS=90 and tracing enabled.

Native terminal result is TIMEOUT124 at90.542seconds; the enclosing repair
Make exits2 and grants no receipt. The first two children report2390PASS
in83.78s and fresh HELP1PASS in0.87s. The required final child has no terminal
result. Its absence must not be relabeled as passing or attributed to a
particular unfinished node.

Evidence remains at:

- .gate-logs/observed/runtime-repair-35c08ba/v012-runtime-repair-35c08ba-312.json
- The matching .pytest.jsonl in that same owned observation directory.
- /tmp/gludd-v012-runtime-repair-35c08ba-312.log

The existing test-xdist-trace-summary diagnostic reports4794events and
2391complete protocol pairs, zero failure reports, zero structural issues and
zero unfinished traced nodes. This diagnostic does not turn the missing
required child or timeout into a pass.

## Consumed inventory proposal: decline a speculative change

The bounded inventory proposal is terminal and Root consumes its complete
decision at /tmp/gludd-v012-inventory-throughput-proposal/decision.md.
Both original repository assertions pass on installed312 in8.08s/native9.0s,
with fatal warnings and no skips. One module-scoped provider already shares
its fresh4,181-source result between the consumers.

The single process-local instrumented profile records native compile4.390s,
declaration traversal2.335s cumulative and all read_text calls0.311s.
These overlapping instrumented costs are not the original cohort's CPU or
forecast savings. Existing native AST parse, C-backed field iteration,
freshness, custom-schema and GC/node-release controls leave no demonstrated
safe reduction. Caching, pruning sources or weakening fields is rejected.
No optimized source, new failing-first production test, second profile,
complete-owner replay or311 result is invented.

The private root/confcutdir retains project config and original assertions
with only REPO_ROOT bound to Main. It deliberately omits Main's global
conftest, so the2PASS profile is attribution rather than the original xdist
schedule. Original source/test exports, raw output, report and exact command
tuple remain in that proposal directory; native profile is
.gate-logs/test-files-cprofile/profile-4333-350a00b5cb90011f.
The initial wrong-cwd transport and wrong FILES lint argument are retained
refusals; corrected execution/lint follow them. All readers and executions
terminate before the decision is received.

The next distinct bounded input is the two real-child failure controls in
test_ci_collect_all_stress.py, measured3.66s/2.53s call wall in the immutable
35c log. A separate proposal must attribute their actual native work while
retaining branch coverage, warning/assertion failures, independent status,
drained children/threads and owned-root cleanup. This inventory decline
does not justify an unchanged admission retry or a stress-fixture change.

## Consumed real-child stress proposal: reject the EOF hypothesis

Root consumes the complete terminal decision at
/tmp/gludd-v012-stress-throughput-proposal/decision.md.
Both original cases pass installed312 twice:2PASS4.35s/native5.1s and
2PASS6.80s/native7.8s. These are bounded private diagnostic profiles, not
a paired speed experiment, full-owner acceptance or the original admission.

The first native profile attributes overlapping parent queue/thread waits.
The second wraps actual stdlib Queue.get and Popen only to record operations
inside the owned TMP fixture. It retains the real selected interpreter,
-m pytest, branch coverage/config, all original arguments and five real
children. Their warning/passing/assertion statuses are1/0/1 and the two
independent assertion statuses are1/1. Original failure and process/thread/
owned-root cleanup assertions execute unchanged.

Actual operation records contain three/two EOFs and ZERO gets after EOF.
Thus the proposed100ms post-EOF empty-queue wait is absent in this workload.
No conditional-wait patch, disabled fence, combined failure child, cache,
production observer or speculative startup repair lands. No failing-first
production RED or broader/311 replay is invented.

Both native reports and raw operation/argv/status receipts remain in the
proposal directory. Native profiles are profile-7175-1e6caea835c6e74d and
profile-13122-d379d7720b6042c7 under .gate-logs/test-files-cprofile.
Negative native searches return the expected non-match status; they are not
passing test receipts. Both executions and all readers terminate.

Further attribution inside the actual child startup is an unmeasured input,
not authorization for another unchanged outer replay. Receive the separately
demonstrated heartbeat launcher-input repair first, then form a clean candidate
and execute exactly one changed-input original-bound evidence run. Select any
further repair from its actual result.

The [existing phase-attribution record](PIPELINE_STATUS_PROCESS_BOUNDARY.md#bounded-native-admission-phase-timings-2026-10-21)
retains the official pytest reporter contract and firsthand June2021 upstream
fixture-attribution report. Their lesson is to distinguish setup/call/teardown
wall spans and censored startup. They do not diagnose this candidate or
establish removable CPU.

## Heartbeat launcher input: operation reduction, negative wall benchmark

Root consumes the terminal proposal decision and both Main-ready postimages
under /tmp/gludd-v012-heartbeat-throughput-proposal. No private ROOT bridge,
profile wrapper or artifacts.mk lands. All proposal processes/readers are
quiescent before canonical receiving.

The existing fixture starts selected Python on uv.py only to validate
run/python and exec selected Python again on the actual producer wrapper.
The proposed fixture uses POSIX sh to validate those same tokens, shift2 and
exec the actual sys.executable through a quoted fixture-only environment
binding and literal "$@". The Make UV path uses stdlib shlex.quote.
This removes one demonstrated Python initialization; sh still starts.
It proves neither fewer OS PIDs nor equivalence to real UV supervision.

All39 original cases/assertion bodies, actual pipeline_status producer,
current JSON writing, identity, invalidation, fatal/error/streaming/cleanup
and original boundary mocks remain. Three additive controls cover native
selected-interpreter initialization count, status0/7, spaced/hostile literal
argv, no injection and invalid-prefix1 before a producer.
Only the two new startup probes inject CPython's native sitecustomize counter
under their owned TMP/PYTHONPATH. Original cases receive no counter.

Private instrumented312 baseline is2FAIL40PASS19.01s/native2/19.621s;
candidate is42PASS25.72s/native0/26.951s. All39 originals pass both.
The two new failures occur specifically at observed2-versus-required1
initializations, after original native status/literal checks.
Parent poll spans17.698s/23.985s are waits, not child CPU.
The candidate's wall benchmark is NEGATIVE. Do not explain it by unmeasured
load, subtract reporting overhead or claim a speed/deadline improvement.
This change is justified only as redundant fixture-input removal pending
the authoritative original-bound run.

The complete proposal decision, repair.patch, raw evidence/JUnits and native
profile summaries retain the exact private scope. Profiles are
profile-7398-693d2fc0db556ea3 and profile-12997-e846181381a16228.
Both summaries have42complete pairs and zero issues/unfinished; RED retains
two failure reports. No current observations are hand-edited.

Root copies the tests-only postimage with the original Python shim into Main.
The exact additive two-case native selector then FAILS2 in0.93s/native2/2.0s:
.gate-logs/observed/heartbeat-launcher-red/v012-heartbeat-launcher-red312.json.
Both failures show the actual selected interpreter initializing for shim and
child, rather than an artificial elapsed-time expectation. Root then copies
the optimized dynamic-ROOT postimage; Ruff and strict one-file typing pass.

The existing main TESTFILES already selects the complete heartbeat owner.
All three controls therefore enter admission without another family, selector,
Make/config edit or count relaxation. All176families remain unchanged.
No production Python, coverage source/config/floor or real-UV implementation
changes. Root's complete deduplicated both-runtime receiving and normal
zero-error collection, independent exact-source review and actual changed-input
90/60 evidence remain separate pending owners. No completion or task tick.

## Explicit shell receiving frome82f774

The first launcher change is natively committed at
e82f7741d956c06cf36ca615c204a6cf3011682f. Its mandatory collection reports
129271selected/129295collected,24deselected/0errors50.26s/native59.3s:
.gate-logs/observed/test-count/20261022T210617Z-32044-71167502.json.
The clean SHA receives the complete26-case Pages-input repair evidence,
26PASS20.57s/native0/21.614s, and the native ledger records its one repair:
pages-input-repair-e82f774/v012-pages-input-repair-e82f774-312.json under
.gate-logs/observed. This is local input-fixture evidence, not acquisition,
browser launch, deployed Pages or final release proof.

Independent source assessment identifies a practical limit: directly executing
an extensionless script via Popen relies on host shebang execution. Darwin
and the intended Ubuntu gate scope do not establish native-Windows support.
Root chooses the minimal explicit-shell prefix before the first original
runtime replay. No Windows pass, skip or new performance claim is introduced.

Failing-first control extends only the newly added startup probe across
executable and readable-non-executable owned scripts, for both status0/7.
All existing cases/assertions stay. On the current direct-script launcher,
the executable cases pass and both0644 cases fail at actual Popen with
PermissionError13:2FAIL2PASS1.48s/native2/2.4s, receipt
heartbeat-shell-red/v012-heartbeat-shell-red312.json.
This demonstrates the actual execute-bit requirement on Darwin; it is not
a simulation or measurement of Windows CreateProcess.

Replace only the UV fixture prefix with stdlib
shlex.join(['sh', str(uv)]). The existing Make owner already requires a POSIX
shell; direct probes now invoke it explicitly too. The same selected Python,
token checks, literal argv, native statuses and one-initialization assertions
remain. All39 original heartbeat cases and the prior42-case scope remain,
with just two additional permission variants. The complete owner is now44.
Ruff and strict typing pass before complete both-runtime receiving.

For future fixture-input optimization, retain the existing explicit transport
capability when removing an intermediate interpreter; do not add reliance on
script executable bits or infer another platform's success. Native behavior
controls must verify the new input rather than assert a fragile timing gain.
No production code, Make/config,176-family roster, deadline, warning policy,
coverage85/75BOTH or cleanup obligation changes. Complete receiving, normal
small source commit/collection, exact-source decision and one actual changed
clean-SHA original90/60 run remain the next owners.

## Explicit-shell complete receiving and retained base review

Root consumes the full e82 APPROVE SOURCE ONLY decision at
/tmp/gludd-v012-heartbeat-e82f774-source-review.md and its exact unapplied
one-line patch at /tmp/gludd-v012-heartbeat-e82f774-explicit-shell.patch.
Its POSIX/Darwin/Ubuntu scope, native-Windows limit, negative benchmark and
missing release proofs remain. Root applies that same prefix with the actual
new permission controls above; the base e82 reference remains immutable.

The complete affected union is now162cases:heartbeat44,inventory59,
SESSION19,snapshot38 and the two original repository link controls.
Both installed runtimes run concurrently under the two-owner ceiling,
one serial pytest process per runtime, fatal warnings and no skips/errors.

- Python3.11.14:162PASS24.57s, native0/25.739s.
- Python3.12.15:162PASS24.47s, native0/25.691s.

Native observed-status confirms passed/inactive/exit0 for
.gate-logs/observed/heartbeat-shell-receiving/v012-heartbeat-shell-receiving311.json
and its312 counterpart. Both tested input digests are
de4701e2d8376606890c821152a0b9152e2c570f1e3a9d4bbc8a3e4ab6ba2e58.
These concurrent component measurements are not an original-budget replay,
paired benchmark, hosted improvement or native-Windows proof. All scoped
original assertions and new permission cases pass; no production source changes.

Native ps also exposes a separate existing watchdog secrets scan.
Existing observed-status/watchdog-secrets identifies actual owner57626 and
run scan-60dd7ce01d364f84af3085ff671aa035:TIMEOUT124/60.556s, not a security pass.
Its existing cooldown is300seconds. No model PID is invented or scanner killed;
no guard is disabled and no earlier unknown PID is retroactively attributed.
Use that existing native producer plus ps to drain active audits before the
original replay; retain this separate failed observation. It does not diagnose
the runtime timeout or justify another scanner/cache or a changed deadline.

Only receipt/handoff prose follows component acceptance. Native guarded
collection, clean successor review and one actual original90/60 repair remain.
Full capacity/gate/global coverage, exact-SHA GHA/deployment and promotion are
still pending. All seven release tasks remain unchecked.

## Retained canonical complete receiving before explicit-shell hardening

After the actual two-case Main RED and optimized postimage, Root runs one
deduplicated union of complete heartbeat42, inventory59, SESSION19,
snapshot38 and the two original whole-repository link controls:160cases
per installed runtime with fatal warnings and no skipped/error cases.

- Python3.11.14:160PASS29.68s, native0/30.720s.
- Python3.12.15:160PASS30.77s, native0/31.755s.

The exact receipts are
.gate-logs/observed/heartbeat-launcher-receiving/v012-heartbeat-launcher-receiving311.json
and the matching312.json. Native observed-status confirms passed, inactive,
exit0 and both actual traces. Their shared tested input digest is
fc8c4e955e012b1e68bb58ecc615f434e3f0b8c0058c165ff57430ca0f9b4b57.
This120-second scoped union is not the original90-second admission and
supplies no replacement deadline, repair receipt or final gate.

Only receipt prose follows this union; source, test assertions, fixtures,
Make/config and runtime inputs remain as tested. Root retains those results
and performs the mandatory collection for the small native guarded commit.
The last native ps shows only existing watchdogs57626/57964; all model
proposal/review owners explicitly report quiescence. A transient detect-secrets
PID20092 was not attributed to a model or killed; it is absent at this boundary.
Quiescence is scoped to that observation, never inferred from daemon-only output.

## Optimize the actual input before replaying

The complete run exposes the repository fixture audit's setup at5.553s and
a34.47MB RSS increase. The heartbeat test module contributes23pairs/9.349s on
one worker and16pairs/8.104s on the other. These are summed paired wall spans
including setup/children/waits. They are not CPU, critical-path duration,
suite elapsed, causal bottleneck proof or forecast savings.

Use at most two disjoint bounded proposal owners to inspect those concrete
inputs. They write only owned TMP proposals; Root receives canonical source
and owns shared docs/Git. Reuse existing test-files-cprofile and native trace
summary, installed interpreters, stdlib and mature existing helpers. Inspect
previous declaration/GC/import improvements before adding another mechanism.
Do not introduce another parser, observer, runner, daemon or proof cache.

For each proposal, preserve every original case/assertion and complete owner.
Inventory must remain fresh, parse unique inputs as intended and release AST
nodes under the existing GC contract. Heartbeat must retain actual current
JSON production, identity, stale-success invalidation, failure precedence,
streaming and cleanup semantics. Do not replace additional production paths
with stubs to claim a speed improvement. Keep private rootdir/confcutdir,
project config, namespace, child lifetime and both runtime identities explicit.

Bound profiling to two focused executions per proposal. Require a concrete
semantic or operation-count regression, not a fragile elapsed-time assertion.
An optimization rejected by measurement still produces a versioned decision
and identifies the actual alternate input; it is not grounds for an unchanged
90-second retry. Parallel proposal tests are diagnostic and cannot measure
uncontended whole-admission throughput.

Root receives compatible repairs together, applies their failing-first
controls, runs one deduplicated complete affected-owner union per runtime,
and obtains unchanged85aggregate/75each-file LINE AND BRANCH coverage for
changed production Python through its existing whole-source profile.
Keep small source commits and mandatory zero-error collection per commit.
All other model readers/tests must be quiescent before Git hooks.

Only after actual inputs change and a coherent clean candidate exists should
the native ci-failure-repair owner run the original90/60 admission again.
Run this whole-admission evidence alone. Preserve all176families, exact
selectors/order,2/1/1 children, fatal warnings,21phases/2850 and original
cleanup/coverage/ZDD obligations. Do not increase a deadline or trim tests.

## Publication and release authority stay separate

The latest remote-VERIFIED candidate remains
eb772a658a01db4485087813e4478eb6c59cc328. Its exact push GHA results are
[Build39826135809 FAIL](https://github.com/sandboxcom/gludd/actions/runs/39826135809),
[Molecule39826135844 PASS](https://github.com/sandboxcom/gludd/actions/runs/39826135844)
and
[Pages39826135878 FAIL](https://github.com/sandboxcom/gludd/actions/runs/39826135878).
All six Molecule lanes pass; the eight downstream Build shards remain
skipped/unknown rather than zero-duration passing work. The new local commits
have no hosted result.

After actual local repair receipts, use the ordinary policy-guarded candidate
batch push, verify-remote and branch-owned pipeline-status/ci-await. Their
native producers own generated current JSON; never edit those observations.
A changed SHA or local green result supplies no hosted or deployment proof.
Candidate pushes validate; Pages deployment requires the master push condition
and validation/JS fan-in. Release publication requires version-tag/fan-in
conditions. Protected Azure deployment is a separate explicit owner.

Final capacity admission remains blocked by the previously measured volume
pressure, with no reclaimable authorized cleanup demonstrated. Do not retry
unchanged cleanup or final proof. HumanTodo filing remains unfiled because
the actual service/config/store/auth prerequisites are unavailable; null IDs
are not filed requests. Normal bounded prerequisite repairs and guarded CI
repair pushes remain allowed. Canonical full gate, global coverage,
exact-SHA hosted success, deployed reviewed-head acceptance and normal
development/master promotion remain pending.

## Native c7 admission and complete-selector redistribution (2026-10-26 UTC)

Normal Main merge c7c01337e8482927eb74c87fee34059a3d5d45f3 retains parents
17e20e5e37a965a089c073ac54e3e738ca49eae6 and b4ea72bfa151b718cc6f99ad8c25d79e7b951997.
Its fresh normal collection passes131194 selected/131218 collected,
24deselected/0errors at test-count/20261026T211716Z-19315-e95e642e.json.
The128-owner/static receipts retain their unchanged source and actual scopes.

Two actual mode0 admissions at c7 fail the unchanged90total/60quiet runtime
owner with native124: proof-admission-20261026T211952Z-21001 (90.558s) and
proof-admission-20261026T220416Z-78640 (90.584s), both under
.gate-logs/observed/release-proof-runtime-admission. The second selects verified
native UV0.13.1; that actual build-prerequisite correction supplies no timing
repair. Each first eight admission stages passes, including81 source cases;
HELP passes1 and the original12-selector TAIL passes14. MAIN collects2509
but has no terminal result; both native traces are null. No timeout cause or
speed benefit is inferred from that incomplete output.

A bounded same-entrypoint witness passes2 original MAIN cases, then the complete
unchanged142-selector MAIN passes2509 in101.66s/native105.242s under diagnostic
300/60, at v012-runtime-main-profile/v012-main-profile-c7c0133-311-20261026.json.
Its authoritative cprofile trace has5026events/2509complete pairs and zero
issues, unfinished or failed reports. It is not original-bound admission.
Two worker protocol-wall sums are not elapsed time, worker CPU or forecast
savings; the controller profile mostly measures waiting. The actual trace
selects observed_command75 and heartbeat44 as a bounded redistribution pilot.

No process-group grace shortcut lands: a pinned zombie leader does not prove
the group empty, and a process census is not an atomic absence proof.
A private native CLI import experiment measures only about6ms in its selected
cleanup import; its additive probe is restored and no production change lands.
Neither experiment establishes an admission repair.

The actual routing change moves only those two whole-file selectors from MAIN2
to the existing TAIL1, immediately before the unchanged final dependency-profile
guard. The155 unique original selector strings remain exactly once, in
140/1/14 child inventories. Every earlier TAIL selector keeps its relative
order. All three literal ID= assignments,2/1/1 widths, fatal warnings,
initial-path filters, HELP-before-TAIL barrier, HELP failure skip, MAIN join,
failure precedence,90/60 and outer timeout/signal descendant drain remain.
There is no fourth child, new observer, deadline/grace change or family
exception; current180families/21phases/2850 and full gate remain authoritative.

The additive immutable155-token regression first passes the original inventory
comparison and then fails solely at observed_command coverage[1,0,0] instead
of[0,0,1]:1FAIL0.69s after logical-Makefile AST1PASS18.31s, native21.109s at
v012-runtime-tail-red/v012-runtime-tail-red311-c7c0133-20261026.json.
GREEN changes exactly two native TESTFILES values plus the three independent
routing owners. All predecessor node mutations remain; the new whole-file
controls narrow to actual existing nodes so no mutation becomes inert.
Independent review approves source only. Ruff, three separate strict scopes,
environment-write guard,303target contracts, AST, all eight promotion pins and
the new GREEN regression pass native35.361s at v012-runtime-tail-static/
v012-runtime-tail-static311-c7c0133-20261026.json.

Initial complete16-owner receiving retains103FAIL/1164PASS on EACH runtime:
311373.17s/native379.995s and312384.81s/native400.919s. Native process inspection
and signaling fail with errno1 (psutil.process_iter/sysctl and os.killpg), and
the outer observer seals125/observer-process-group-cleanup-failed. The compact
output child writes all20 bytes before cleanup refusal. These sandboxed
receipts remain v012-runtime-tail-receiving/v012-runtime-tail-receiving{311,312}-
c7c0133-20261026.json. No assertion or cleanup rule changes in response.
A native stop plan refuses the relative observer command's namespace; it
applies no termination, and both original sessions reach terminal naturally.
The required changed input is approved native execution outside that sandbox,
with the exact same source, commands, bounds and original assertions.

Approved native execution of the exact16 complete owners passes1267 on EACH
installed runtime, with no skips:311417.21s/native423.633s and
312430.89s/native446.095s. Receipts are v012-runtime-tail-native/
v012-runtime-tail-native{311,312}-c7c0133-20261026.json. The actual source
fingerprint is791a474104c6a727c47d8a578906328a6d62180dc2088885b7cbd9ee5a1cc4c5.
EACH trace has2713events/1306complete pairs, zero issues or unfinished cases;
10 reports belong only to original nested warning/assertion controls.
All pass/fail-MAIN/fail-HELP/fail-TAIL, native124/143 descendant-drain and
controller signal restoration/reap controls pass; the complete publication
consumer remains green. These establish behavior, not an admission speed claim.
Ordinary collection/commit and the actual clean-SHA90/60 admission remain next. Full canonical21600 gate,
global85/75 line AND branch BOTH, exact-SHA push CI, normal reviewed
development/master promotion, ZDD and signed complete artifacts remain separate.

## Original-bound admission at8d2c97e (2026-10-26 UTC)

The normal runtime checkpoint is8d2c97e8ca7887aa68dba27e25a601e176523ad4.
Mutation commit-transaction/20261026T230431Z-80709-73a72e87.json passes71.090s;
fresh collection131207/131231,24deselected/0errors takes48.37s/native53.917s at
test-count/20261026T230447Z-81983-be5be52b.json. An initial no-staged-changes
refusal starts no mutation; staging exactly the nine reviewed paths repairs
that caller input. Normal hooks, clone/line checks and collection remain intact.

Actual release-proof-admission mode0 passes all nine owners in151.016s at
v012-admission-main/v012-admission-tailpilot-8d2c97e-311-20261026.json.
Its original90/60 runtime owner passes86.989s at
release-proof-runtime-admission/proof-admission-20261026T230731Z-82524.json:
MAIN2390 passes83.59s, HELP1 passes0.52s, TAIL133 passes51.42s;
main_status=0/help_status=0/tail_status=0. Exact outer/inner sessions are drained,
native statuses verified and Main remains clean at that full SHA throughout.
The runtime trace remains null; no complete phase profile or percent speed
improvement follows. This establishes one actual original-bound admission pass.

The later packaging hook is a separate release-required source change.
Candidate sealing still requires its own coherent source and all authoritative
combined/full-gate/exact-SHA hosted, reviewed promotion, ZDD and release proofs.
