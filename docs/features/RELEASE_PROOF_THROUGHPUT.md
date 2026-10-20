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

## Repair-owned development evidence before an environment-only handoff (2026-10-20)

The stop challenge correctly continued S98.4 after a clean-source handoff.
Capacity still blocks final release proof, but it does not excuse missing
repair-owned evidence for an otherwise permitted development repair push.
Native work-item status retains S98.4, release_ready=false and gate_evidence=false.
The existing batch-push guard freshly verifies ahead159/behind0 at immutable
1fd5fda8241346c41e7c5d9a3348a8473c3df439, then refuses three open families:
Pages WebKit family=89b7005f13dc5d2675e9addd2b900acb1581eb681467db7bc5a3e45ceb8e9f4d,
Python3.12 gate family=ce0164d707f6d521ab1269146bd7541dee47e0ad74355556f785c3f0ad9936e2,
and Python3.11 gate family=cf5b089df8ad5c0921ed70ee9d01916f40baeacd98f086caf110f7f139404596.
No transport occurs and no hosted success is inferred.

Use ci-failure-status to select each actual full family and ci-failure-repair
to execute its first evidence in the exact clean descendant checkout.
The repair owner accepts a plain public Make evidence target; an underscore
target is rejected before a child or receipt exists. For the actual workflow's
internal runtime admission, select run-watched and put its unchanged
make _pytest-runtime-compatibility-admission command in the quoted CMD evidence
variable. Preserve original90s total/60s quiet, three2/1/1 children, release/0/16/1,
fatal warnings, installed selected runtime and all original checks.
The existing CI_FAILURE_LEDGER feature documents exact selection/argv and
fresh-checkout/fingerprint authority. Do not use collection, a plan, another
family's focused test or a default all-open selection as repair proof.

The first repair-owned311 execution on1fd5fda records87FAIL/1932PASS59.80s,
observed rc2/62.9s; no receipt is issued. Immutable native evidence:
.gate-logs/observed/v012-runtime-repair311/v012-repair311-1fd5fda-20261020.log.
Most failures share pipeline_status -> active_work_status -> kill_owned_gate's
bare resource_arbiter import in a fresh package process. Ordinary warmed pytest
imports hid that boundary. Two other actual inputs are a handoff hash joined
to its prefix (the legacy reader instead selects a numeric gate timestamp)
and adjacent target-specific declarations violating original blank-line syntax.
Root retains every original assertion, separates all four declarations without
changing UV/MAKEFLAGS values and exposes the existing source hash as a token.
All48 original syntax/parser/guide/session cases pass2.97s; contracts268 and
unique targets1174/0duplicates pass. Original runtime failure remains retained.

The isolated source owner commits 9016ca9e8ed019823872db8b868698a7fd2d4de6;
Root integrates its two-line binding and12 fresh package/flat native controls
as8344287f7. Native RED7FAIL/5PASS precedes12PASS. Complete ten-owner acceptance
passes269/0skip; unchanged whole kill_owned_gate.py coverage passes92.8% line
and91.1% branch under85/75, audit20261020T204425Z. Ruff/strict2files pass;
source collection127236/127260,24deselected/0errors, remains distinct from Main.
Exact commands and terminal source evidence are in
/tmp/gludd-v012-observer-import-repair.md. The three source-format fixes retain
original assertions: Make146b5e8 and handoff8e9947e each have native zero-error
collection. Native read-only inspection also verifies Main's registered
ci-shards/python-3.12 slot as CPython3.12.15/ci, zero mismatches; no environment
is acquired or changed. Another repair-owned311 execution needs this actual
changed source;312 needs its own runtime and WebKit its actual browser owner. These remain missing proofs,
alongside final capacity admission, full gate, exact-SHA CI and deployment.
All19release records stay open. The mandatory guide and AGENTS.md now expose
this existing guarded route so a smaller model does not stop at source receipts.

The next repair-owned311 execution on763e6447c4a946bdc509b351fd88f9b83fcd105d
retains1FAIL/2018PASS61.94s, observed rc2/64.8s. Its actual remaining node is
the existing external pytest-fixture collision guard: the new observer tests
parametrize context at three sites, shadowing Playwright's fixture. Root renames
only those pytest-side parameters to import_context, keeping all12 values,
assertions, child payload keys and fresh-process/CLI checks. Production source
and the unchanged full-file coverage scope do not change.
Both complete owners pass69/0skip6.65s; Ruff/strict pass. Exact focused XML:
/tmp/gludd-v012-observer-fixture-conformance.xml. Original failed evidence:
.gate-logs/observed/v012-runtime-repair311/v012-repair311-763e644-20261020.log.
No repair receipt is issued by the failed replay. The mandatory early-focus
recipe now includes the existing complete fixture-collision owner whenever
pytest parametrization changes; ordinary plugins and its repository assertion
remain authoritative. Do not obtain a receipt until a changed clean source
passes the original complete90/60 owner.

Normal root source commit e2e7685495819d9428100ce90491d86fdd1329fe retains fresh
collection127516/127540,24deselected/0errors. Root then reviews and integrates
the retained Pages product40392bdba7e228f84f87fd51958f6330ac541c93 as90a4f08a9.
Its original three-file scope moves successful-chart summary work to one batch
reconciliation; immediate failures, fresh health, geometry and original browser
bounds remain. Native tests-only RED2FAIL/12PASS precedes complete50PASS/0skip,
Ruff/strict/syntax and locked Markdown. Source collection127522/127546 has
24deselected/0errors. The original db4 checkpoint and worktree lock remain.
The existing PRESENTATION_JAVASCRIPT_COVERAGE feature now distinguishes that
integration from its older pre-product94.2/88.75 coverage; fresh real browser/c8
and the three repair-owned families remain required. No release record is ticked.

The next native311 replay on3819295a60a3e4d9ae26974a67a8ef8a2b85ef30 passes
all2019 main cases60.79s, then the original isolated help owner fails0.96s:
agent-cleanup appears twice. The retained native90/60 observation exits2/66.3s
at .gate-logs/observed/v012-runtime-repair311/v012-repair311-3819295-20261020.log.
No repair receipt exists. Root removes only the stale duplicate help entry,
retains the native planned-retirement entry and its variables, and corrects
adjacent legacy master-helper guidance to the existing development/promotion
owners. The worktree lifecycle comment follows the same existing branch policy.
The original duplicate-help assertion remains unchanged. The three complete
Make audit/contract/syntax owners pass51/0skip2.95s; native target contract
passes268 targets and actual help exits0. Focused XML is
/tmp/gludd-v012-help-conformance.xml. These clear that failed source input;
the original complete runtime replay remains required.

## Automatic browser observation and measured remaining runtime cost (2026-10-20)

The actual original311 repair on e2912f58334e09b97eee2ff5c43a5c0517a51e9c
passes2019 main,1help and11tail cases in82.9observed seconds. The native ledger
records its selected family locally repaired; this is not hosted resolution.
The corresponding312 run passes MAIN2019/75.08s, help1/1.10s and ten tail cases,
then times out90.6s during the last node. Its receipt remains withheld.
The original failed log is
.gate-logs/observed/v012-runtime-repair312/v012-repair312-e2912f5-20261020.log.

One explicitly diagnostic180s-cap replay retains every original selector,
plugin and2/1/1 child. It passes2019/62.19s,1/1.06s,11/8.79s, native76.4s,
with largest source-inventory setup6.07s. This warm diagnostic is not deadline
clearance or a repair receipt. A native three-control cProfile then passes3
cases/11.3observed seconds:560subprocess.run calls/4.543cumulative seconds,
receipt-owner Git280calls; cumulative rows overlap, and child CPU/cold imports
are unobserved. Retain v012-runtime-diagnostic312 and v012-review-tail-profile312
under .gate-logs/observed, plus native profile87486-9b5857b8ecb6cd7f.

[UV's native bytecode option](https://docs.astral.sh/uv/reference/cli/#uv-sync--compile-bytecode)
trades installation work and disk for startup. The firsthand March2026
[macOS report18577](https://github.com/astral-sh/uv/issues/18577), now closed as
duplicate, instead attributes its measured first-import lag to native-extension
security scans. Neither diagnoses Gludd. No link mode, compiler, package,
platform security, plugin, cache or timeout is changed from those hypotheses.
The measured Git component remains a bounded source-repair opportunity.

The first real installed WebKitJS0 replay records cold1.740s/cached1.344s,
then desktop20PASS/1FAIL/3ERROR289.41s; compact is not reached. Cleanup reports
TargetClosedError at the original SVG fault node. Native task-watchdog history
identifies the interruption:2026-10-20T21:17:20, parent74910 make ci-failure-repair
is killed at301s, with its descendant tree. Native killed audit independently
records74910 at1792531040.02602. Preserve the raw history copy
/tmp/gludd-v012-webkit-watchdog-failure-history.log and original browser artifacts
/tmp/gludd-v012-pages-webkit-ci-repair-installed-e2912f5/.
The complete original six-fixture node then passes unchanged1/32.97s,
native34.0s under existing run-watched; pw:browser91308 exits0 without a signal.
Exact diagnostic log is .gate-logs/observed/v012-webkit-svg-diagnostic/v012-svg-webkit-e2912f5-20261020.log.
The [bounded research proposal](/tmp/gludd-v012-webkit-svg-closure-proposal.md)
retains firsthand lifecycle/reentrancy hypotheses; the actual watchdog evidence
controls this repair. No collector, controller, assertion or engine is changed.

Root integrates the separate native visibility source46118498c8bdfeb65a8078e25bb714d70d590d74
as e318ad7f6: exact registered resource-root plus its native -toolchain sibling,
with the existing path-boundary/ancestry filter and browser driver accounting.
Whole seven-owner acceptance211PASS; whole producer94.7line/91.0branch under85/75,
audit20261020T212900Z; source fresh127533/127557,24deselected/0errors.
No broader parent namespace, extra PID provider or termination authority is added.

The public presentation-browser-test owner now automatically places executed
engine commands under the same stream_command owner used by run-watched.
Plans stay read-only, native argv/Make carriers stay intact, failures stop the
matrix and preserve their original exit in native metadata. Standard OBSERVED
root/heartbeat/quiet/max/retention variables are explicit in its target contract.
Watchdog policy/exclusions, per-viewport600s,5s readiness,10s geometry, hosted20min,
both engines and85/75 line and branch floors remain unchanged. New native routing
RED is3FAIL/2PASS. After conforming both original exact-schema expectations to
the five added variables, complete311 owners pass102/10.83s and312 Make owner
passes34/4.67s, no skips. The earlier101PASS/1schemaFAIL remains in
/tmp/gludd-v012-browser-observer-routing-green.xml; final311/312 XML names are
/tmp/gludd-v012-browser-observer-routing-final311.xml and -final312.xml.
Real protected WebKit, ChromiumJS0/JS1-c8,312repair, full gate, hosted exact-SHA,
deployment and promotion remain missing. All release records remain open.

## Receiving-checkout browser coverage repair (2026-10-20)

The native repair-owned WebKit replay on clean156eb240db478515d5106fa976e42ee58de2df86
passes23desktop/312.10s and1compact/43.58s, observed362.52s/rc0.
Cold1.727s/cached1.421s remain below5. The ledger records only its selected
family locally repaired. Native log:
.gate-logs/observed/presentation-browser-webkit/20261020T214148Z-114-6368b67b.log.
The same clean source passes ChromiumJS0:23desktop/312.94s,1compact/44.48s,
native365.8s/rc0; cold1.510s/cached1.292s. Native log:
.gate-logs/observed/presentation-browser-chromium/20261020T214803Z-5768-ead36536.log.

Fresh JS1 instead retains23setupERROR/3.30s, native5.5s/rc1; compact and c8
never execute. Log:
.gate-logs/observed/presentation-browser-chromium/20261020T215418Z-24992-f5679d49.log.
The real producer writes chromium/javascript-coverage/source.json; the viewport
child incorrectly derives it from BrowserPlan.output_root.parent. Root changes
that single runtime path to the same output_root used by the producer. No controller,
browser assertion, collector behavior, dependency, watchdog or timeout changes.
The final native docstring owner exposes nine missing public API descriptions;
source documentation now names existing CDP/source/lifecycle contracts without
changing those operations. Original assertions remain unchanged.
Four real Python-child controls cover both viewports with JS0/JS1, actual producer
JSON, source hash, paths containing spaces and inherited stale carriers:
tests-only2FAIL/2PASS1.27s precedes4PASS. RED XML:
/tmp/gludd-v012-js-manifest-routing-red.xml.

Immutable50c85a846c2ef591ac8379cd215de59f8adb0e4e contains original JavaScript
runner controls absent from receiving156eb24. Root recovers their complete block
into tests/unit/test_presentation_javascript_runner.py; original controller,
witness, raw/normalized parity, threshold and failure/order assertions remain.
Only fake browser supervision seams and fixture output shape conform to the
current native owner; its outer600s assertion is retained at that owner and
the existing610s child fallback is checked separately. Existing runner strict
typing also receives direct public types, TypedDict inputs and typed callbacks;
all original runtime assertions remain. No suppression or Any is added.

Retain initial2fixtureFAIL/278PASS11.97s and failed coverage2FAIL/278PASS17.30s.
Correct complete six-owner311 coverage passes280/18.35s, native20.3s/rc0,
whole two-file96.3line/90.3branch; runner95.9/89.6 and conftest100/100 satisfy
unchanged85aggregate/75each. Audit:.gate-logs/coverage-20261020T220133Z.json.
The same complete312 owners pass280/13.18s,0skip. Exact focused312 XML:
/tmp/gludd-v012-js-manifest-recovered-final312.xml.
After final type-only fixture conformance, the documented browser scope passes
224 cases on311/16.57s and312/12.79s. The larger earlier280-case scope additionally
includes the complete57-case fixture-collision owner instead of its single
repository node. Current native coverage again measures96.3line/90.3branch,
runner95.9/89.6 and conftest100/100, audit
.gate-logs/coverage-20261020T221227Z.json; observed18.5s/rc0.
Current312 XML:/tmp/gludd-v012-browser-receiving-final312.xml.
After the public docstrings conform, current311 coverage passes224/16.72s,
observed18.7s/rc0, at96.4line/90.3branch; runner96.0/89.6 and conftest100/100.
Audit:.gate-logs/coverage-20261020T221409Z.json. Ruff, strict four-file typing and
native docstrings pass. The complete original session/objective/guide/collision
owners pass103/6.91s, XML:/tmp/gludd-v012-receiving-guide-session-final.xml.
Native failure mapping114+two structural controls, contracts268,1174targets/zero
duplicates,264resource owners and all seven Markdown files pass. The documented
integration-admission example validates all21phases/2850s only; it is not gate proof.

The existing early runtime owner now selects the cheap four-case native manifest
node in its original first child. Mapping/roster tests fail2 first; all original
exact roster/count assertions conform to one additional family without removing
any selector, safety assertion or original family. Intermediate missing-recipe
7FAIL/139PASS and stale schema5FAIL/141PASS remain failed evidence.
The complete promotion/Make-audit/syntax/contract owners then pass146/8.54s,
XML:/tmp/gludd-v012-js-manifest-admission-routing-final-complete.xml.
Current114families retain the original113, three2/1/1 children,90total/60quiet,
21phases/2850ceiling and mandatory full gate. The Make guide now requires
receiving-checkout whole-owner/coverage acceptance and describes the actual
diagnostic PYTEST_ADDOPTS child-plugin pitfall. Existing enforcement remains.

## Accepted bounded candidate-adapter metadata enhancement (2026-10-20)

Root independently inspects exact edc52adff1ac14481bc69be25f4d717656b86ec7,
two files only, before integration. It reuses ordinary native Git batch-check
metadata for the two candidate blobs, validates all paths and complete ordered
metadata before bodies, and retains both binary body reads/content hashes,
replacement/configuration views and independent freshness/signature fences.
[Git's native manual](https://git-scm.com/docs/git-cat-file) and
[the maintainer's2021 replacement fix](https://git.powerel.org/kernel/git/commit/5c5b29b459dd6e169c9114910adca9203532f7d7)
support ordinary-input parity; the long-lived
[community discussion](https://stackoverflow.com/questions/1595631/how-to-get-a-list-of-all-blobs-in-a-repository-in-git/70162248)
is discovery context. Native replacement fixtures provide application evidence.

Real failing-first call-count controls prove5-to4 launches per fresh operation,
including shared OIDs. Complete380PASS/317.44s, native319.4s/rc0, whole producer
95.4line/89.6branch, Ruff/strict and fresh127572/127596collection/0errors pass.
The initial1FAIL/379PASS was the caller's inherited diagnostic -n flag in an
intentionally plugin-isolated child; removing only that input passes the same
original assertion. The failed native log and exact changed input remain in
/tmp/gludd-v012-adapter-metadata-batch-report.md.
The three-control source profile records551subprocess/271receipt-Git calls
versus the earlier Main560/280, but cumulative time rises8.228s versus4.543s
and executing runtimes differ. No latency, deadline-clearance or hosted gain
is claimed. Actual original31290/60 repair remains required after integration.
No release checkbox, full gate, hosted exact-SHA or deployment proof is supplied.

## Frozen5b77f02 native source proof (2026-10-20)

Native worktree-state retains clean development5b77f02715ddb8cc4c5dcf80a2dc7ac602fe8fc0
through all proof below. Its guarded source collection selects127698/127722,
24deselected,0errors49.09s; observed55.1s/rc0.

The original312 repair now passes2023main/63.48s,1fresh-help/1.08s and11tail/9.12s.
Native observed78.3s/rc0 records CI_FAILURE_REPAIR_RECORDED for that exact SHA.
The selected original90total/60quiet, release/0/16/1 and2/1/1 children remain.
Log:.gate-logs/observed/v012-runtime-repair312/v012-repair312-5b77f02-20261020.log.
Native ledger reports open0/repaired12/resolved38/runs139. Repaired is a local
receipt state; hosted resolution remains pending. Preserve the earlier failures.

Complete receiving metadata coverage passes all380cases/221.25s,0skip;
observed223.3s/rc0. The whole499-statement/212-branch producer measures95.4line/
89.6branch and retains85aggregate/75each. Audit:
.gate-logs/coverage-20261020T223053Z.json. Report:
.gate-logs/coverage-adapter-metadata-main-5b77f02.json. The four complete source,
receipt, startup and fixture-collision owners use the same native coverage owner.
Its retained INI is /tmp/gludd-v012-adapter-metadata-main.ini: branch/parallel/
subprocess tracking and the complete Main producer path, with no omitted function.
The peer INI names its isolated checkout and cannot supply Main coverage.
No ambient PYTEST_ADDOPTS is introduced. Different run times establish no gain.

For this canonical Main checkout, the complete actual retained INI is:

```ini
[run]
branch = True
parallel = True
patch = subprocess
include =
    /Users/shawnwilson/gludd/scripts/reviewed_head_inputs.py
[report]
show_missing = True
```

The following two existing Make owners reconstruct that exact input and replay
the same complete proof. This is a Main-specific recipe; an isolated owner must
bind its own actual checkout from worktree-state rather than point at Main.

```text
make write-text-b64 FILE=/tmp/gludd-v012-adapter-metadata-main.ini TEXT_B64=W3J1bl0KYnJhbmNoID0gVHJ1ZQpwYXJhbGxlbCA9IFRydWUKcGF0Y2ggPSBzdWJwcm9jZXNzCmluY2x1ZGUgPQogICAgL1VzZXJzL3NoYXdud2lsc29uL2dsdWRkL3NjcmlwdHMvcmV2aWV3ZWRfaGVhZF9pbnB1dHMucHkKW3JlcG9ydF0Kc2hvd19taXNzaW5nID0gVHJ1ZQo=
make coverage-files COVERAGE_TESTFILES='tests/unit/test_reviewed_head_inputs.py tests/unit/test_build_reviewed_head_integration_receipt.py tests/unit/test_runtime_startup_native_chain.py tests/unit/test_pytest_param_fixture_collisions.py' COVERAGE_CONFIG=/tmp/gludd-v012-adapter-metadata-main.ini COVERAGE_REPORT=.gate-logs/coverage-adapter-metadata-main-5b77f02.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_QUIET_SECS=120 OBSERVED_MAX_SECS=600 OBSERVED_RETAIN_RUNS=20 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11
```

release_note_integrity independently APPROVES this exact source against156eb240d.
Root reads the complete persisted decision:
 /tmp/gludd-v012-candidate-5b77f02-source-review.md.
It inspects every changed production/test owner, raw/normalized source coverage,
native Git replacement/order/bounds/freshness and four fatal exact-roster pins.
No blocking source defect or patch is identified. This is source review, not a
genuine native reviewed-head integration receipt or final release authority.

Fresh public ChromiumJS1 passes23desktop/334.76s and1compact/45.34s;
native390.5s/rc0. Cold1.799s/cached2.113s retain the5s readiness assertion;
600s viewport and10s geometry assertions remain. Actual c8 reports94.2line/
88.3branch,732/777lines and219/248branches,0skipped;53/54functions98.14%.
Both native aggregate85 and each-file75 line AND branch checks exit0.
The report contains the entire controller, with no omitted lines or fake ranges:
 /tmp/gludd-v012-chromium-js1-5b77f02/chromium/javascript-coverage/reports/coverage-summary.json.
Source.json and runtime.json retain clean5b77f02/tree05e8c59420f12027f2615a0908f535b5dc25b780,
controller b8da13d77eee1e5dcc41c88286a5fc5e7e9490daa1f072727d861b6373b8c661,
actual Chromium153.0.8010.12, Node26.0.0/c8 12.0.0, Playwright1.63.0 and Python3.12.15.
Log:.gate-logs/observed/presentation-browser-chromium/20261020T223516Z-3682-6d466df5.log.

After the prior proof exits, normal owned cleanup removes6027peer bytecode files/
240549715bytes, three disposable caches, idle Playwright cache7834entries and
11UV files/12.2KiB. First pass removes6 with0errors; second makes0progress and
still refuses96% against95%. The resource feature explicitly permits this idle
browser eviction under pressure. Root initially mislabels it a retention bug,
then corrects that interpretation after reading the actual policy/tests.
No cleanup guard changes. The pinned installer restores both engines in36.0s;
the aligned312 availability check then reports both actual executables/rc0.
An initial outer311/child312 UV warning is retained. The corrected aligned312
actual-mode replay passes both engines in observed1.7s/rc0 with no warning or
new download; selecting one installed runtime removes the configuration mismatch.
Log:.gate-logs/observed/v012-browser-prefetch-aligned/v012-browser-prefetch-aligned-5b77f02-20261020.log.
After restoration/browser proof, disk measures97%,7.2GiB available. No unchanged
cleanup/reinstall loop, final admission or full gate is attempted at that input.

The mandatory guide now explains cleanup before pinned prefetch and acceptance;
keep the existing long-lived practitioner references in GATE_RESOURCE_LIFECYCLE.
Both model agents are quiescent. Native ps after browser exit shows only the two
acknowledged watchdogs, no test/browser owner. Preserve all six checkpoints,
source refs/locks, failed proofs and the unsubmitted HumanTodo configuration.
Source/acceptance/local repair receipts permit the normal guarded development
repair-push route. Full canonical gate, genuine integration receipt, exact-SHA
hosted CI/deployment and promotion remain missing; all seven tasks stay open.
The final original guide/session/objective owners pass46/2.95s, XML:
/tmp/gludd-v012-proof-handoff-final.xml. Native Nodev26 compatibility passes
all six controls/11.12s. Locked Markdown passes all seven changed files and
native worktree line limits remain below2500. These are scoped source guards.

## Original312 repair exposed a stale shared roster (2026-10-20)

The normal guarded browser source commit is d929ce676b7413ff6125783ff9089cef5b6d7946:
127656/127680 selected/collected,24deselected,0errors,58.13s; native collection65.2s.
The independently inspected metadata source integrates as88b176db1a3125760c33e73da527c1740856a0bc.
Its original31290/60 repair retains1FAIL/2022PASS61.65s, observed64.4s/rc2.
The failing owner is test_real_admission_filters_only_never_selected_main_siblings;
the static shared ROSTER omits the new manifest node. The main failure correctly
prevents help/tail children and no native repair receipt is recorded. Preserve
.gate-logs/observed/v012-runtime-repair312/v012-repair312-88b176db1-20261020.log.

Root conforms that literal expected roster and its seven114-family count assertions,
preserving every original entry/order, budget and safety assertion. Six additional
mutation cases reuse both existing guards for omission/duplication/whole-file
widening of the exact new selector. Their native quoted last-main boundary is
handled like the existing last-tail boundary. No selector is removed or widened.

The existing check-gate-failure-promotions owner now runs the original two pins
plus both exact native roster pins, serially with the original30s test timeout.
Tests-only failure injection first retains5FAIL/1PASS1.63s; the actual four-pin
owner then retains2FAIL/2PASS1.70s against the unfixed roster. Preserve
/tmp/gludd-v012-promotion-exact-roster-red.xml.
The first complete replay retains7FAIL/267PASS61.97s, observed63.5s/rc2:
six new fixture cases lacked the last-main quote and the original target-result
assertion still expected two pins. Both literal fixture expectations now conform
to the actual four-pin route, retaining every original assertion. Failed XML:
/tmp/gludd-v012-promotion-exact-roster-complete.xml.
Final complete three-owner replay passes274/74.85s, observed76.1s/rc0;
XML:/tmp/gludd-v012-promotion-exact-roster-complete-final.xml. Native documented
check-gate-failure-promotions passes114families and all four pins/1.99s.
The original guide/session/objective owners pass46/2.80s; Ruff, strict three-file
typing,268target contracts,1174targets/zero duplicates, line limits and locked
Markdown pass. The guide codifies both cheap roster checks and complete owners.
Original31290/60 replay, complete final gate and exact-SHA hosted proof remain
pending; source acceptance supplies none of them. No budget, coverage, cleanup,
hosted or promotion authority changes.

## Bounded current handoff with immutable history (2026-10-20)

The mandatory SESSION.md had reached2498 lines with superseded objectives and
historical Next Steps mixed into the current release handoff. Root replaces it
with a short current-source/owner/evidence/blocker/action record. Its entire prior
body remains verbatim at156eb240db478515d5106fa976e42ee58de2df86, retrievable by the
copyable native git-show-file-to command in SESSION. TASKS and native milestone
owners retain outstanding work; historical records grant no current success.
No generated observation, stop condition, coverage floor or retained proof changes.
Original session-persistence/objective tests remain the verification owners and
pass in the complete103-case cohort above. The current TASKS preamble now names
seven native milestone items and labels older19-item source counts as history;
no checkbox or status is changed.

## Model-independent release operating path (2026-10-20)

The mandatory [.agents Make guide](../../.agents/policy/key-make-targets.md)
previously recommended feature completion and agent merges directly into master,
a shared-history rebase helper and a generic asynchronous gate. It omitted the
release admission and promotion owners, described obsolete disk thresholds and
called gate-audit an 85% per-file gate. These instructions could direct a model
away from the existing branch, capacity, canonical-runtime and 85/75 policies.

The guide now orders work by observed state: establish ownership, repair the
actual prerequisite, accept the complete changed owners, check the Make contract,
freeze once and obtain complete exact-SHA proof before guarded promotion.
AGENTS.md routes agents to that recipe through its existing mandatory reference.
No new enforcement restriction, proof owner, scanner, daemon or cache is added.

| Assessed surface | Existing authority | Codified behavior |
| --- | --- | --- |
| Agent instructions and target discovery | AGENTS.md, make help and target contract | One short mandatory recipe uses current release owners and explicit inputs. |
| Make and build proof | release-proof-admission, canonical gate and native CI producers | Plan, focus, collection, full gate, hosted proof and completion remain separate. |
| Shared checkout and resources | worktree-state, active-work-status, ps and disk | Preserve staged work and a live candidate; isolate disjoint repairs and respect capacity. |
| Branch integration | development-merge-forward and release-promote | Inspect overlap semantics, retain source ancestry, require genuine receipts and canonical-main promotion. |
| Model reliability | existing test_make_target_contract owner | Regressions reject legacy release shortcuts and examples missing declared variables. |

The original two new controls fail on the unchanged guide: missing admission
owner and no copyable variable-complete admission example. After the guide
repair, the complete owner passes nine cases on canonical CPython 3.11.14
(2.14s, fatal warnings, serial pytest). This is source acceptance only.
The initial shared-main .venv attempt is a separate INTERNALERROR: the existing
worktree subprocess guard correctly refuses its canonical-checkout reference.
The next run uses the installed registered shared toolchain. No confinement,
plugin, warning, timeout, cleanup or coverage rule is changed.

### Practitioner findings and limits

The firsthand [Codex issue 25884](https://github.com/openai/codex/issues/25884),
opened 2026-06-02, reports instructions read correctly and then applied
inconsistently in later turns; follow-up users describe resumed-session drift.
This is a reported failure mode, not proof about every model or the present
runtime. The engineering decision is to make critical choices short, local and
testable through existing owners, rather than depend on remembering more prose.

The long-lived [recursive Make variable report](https://stackoverflow.com/questions/32637204/why-doesnt-gnu-makes-override-pass-through-to-sub-makes)
shows command-line carriers surviving into child Make despite a parent export.
The [GNU Make manual](https://www.gnu.org/software/make/manual/make.html)
documents recursive variable and option propagation. Gludd retains its existing
native carrier tests and literal argv owners; the guide supplies every declared
variable in its release examples. Native regression evidence below requires
scoped use of GNU Make's own MFLAGS and MAKEOVERRIDES; no shell parser or broad
carrier deletion is introduced. Neither reference supplies Gludd release proof
or measured model parity.

Lower-capability models have the same executable path and failure messages.
Comparable success still requires actual complete-owner and release evidence;
this change does not claim a cross-model benchmark. Keep all open task statuses,
historical failures, ZDD and 85% aggregate/75% per-file line and branch floors.
The complete four-owner replay retains116PASS/3FAIL in194.06s: all guide,
policy and split controls pass; the whole Markdown scanner names20 missing
release-note links, four untagged runbook fences and32 trailing-space lines.
The missing source documents are assigned to the release-note integrity owner;
main already stages the formatting repairs. These failed source snapshots stay
failed until genuine integration and whole-owner replay. Ruff, strict typing,
254 target contracts,10786-file line limits and five-file locked Rumdl pass.
The admission behavioral plan prints its native nine-owner route and retains
gate_evidence=false/release_ready=false. The locked Rumdl binary is materialized
through existing mkdir-p/copy-file owners; no environment sync or new daemon.
The live main-checkout gate and previously staged repairs are separate work.
No full-gate, hosted, release-readiness or task-completion result follows from
these documentation/source controls.

## Validated literal text edits (2026-10-20)

An agent-side encoding error produced NUL in Python source and invalid UTF-8 in
a diagnostic report. The original collection/read failures remain evidence.
The old write-text-b64 decoder could truncate an existing destination to an
invalid byte and exit zero. The native library repair is staged as dd098f282;
both production Make targets now dispatch that same scripts/replace_text.py
owner. There is no second decoder, temporary payload file, dependency or CLI.

Inputs use standard-library base64.b64decode(validate=True), strict UTF-8 and
NUL refusal before opening the destination. Replacement validates both fields
before any destination IO. Make captures literal public inputs with override,
exports only its four documented internal carriers and unexports public values;
neither Make expressions nor shell substitutions become executable input,
including under -e. Keep required nonempty Make fields, path confinement,
exactly-one-match replacement, original file mode, write bytes and replacement
universal-newline behavior. The original three-path CLI remains supported.

The production Make reproduction retains 40 failures and 17 passing controls
in 4.84s at /tmp/gludd-v012-native-text-make-red-native.xml. Its earlier wrong-CWD
fixture failure remains /tmp/gludd-v012-native-text-make-red.xml and is not
project failure proof. The corrected fixture copies the actual native owner
unchanged into a confined checkout. All 101 new target/library cases then pass
in 4.98s at /tmp/gludd-v012-native-text-make-green.xml.

The original owner separately retains 12 passes and one obsolete structural
failure at /tmp/gludd-v012-text-original-contract-red.xml. Only assertions about
the removed temporary decoder files conform to the native function dispatch;
new assertions require that dispatch, returned failure status and absence of
inline decoding. All original CLI, exact-match and guard assertions remain.
The first whole replay retains 155 passes and one environment-documentation
failure at /tmp/gludd-v012-native-text-production-coverage.xml. Explicit export
assignments align the real Make bindings with the existing audit; no scanner,
allowlist or assertion is weakened. Opposing ambient internal values under -e
are also covered. Final whole replay passes all 156 cases in 15.61s at 100% line
and branch coverage for the entire text owner, retaining 85/75 thresholds:
.gate-logs/coverage-20261020T195344Z.json and
/tmp/gludd-v012-native-text-production-final.xml. Ruff and strict four-file typing
pass. The target contract passes 265 entries; both documented examples execute
and the native read returns beta plus newline. These are focused source proofs;
canonical full gate, exact-SHA hosted proof and release completion remain absent.

The firsthand [Python user report from 2006](https://github.com/python/cpython/issues/43175)
records non-alphabet characters being silently ignored; the migrated issue
retains its 2010 resolution and discussion metadata.
The [Python standard-library documentation](https://docs.python.org/3.11/library/base64.html#base64.b64decode)
specifies that default decoding discards non-alphabet characters and
validate=True rejects them. Reuse that mature implementation. UTF-8, NUL refusal
and validation before destination IO are Gludd text requirements, not upstream
guarantees. These executable controls reduce dependence on model memory; they
are not a benchmark proving equal performance across models.

## Ranged edits retain native failures and executable modes (2026-10-20)

The real replace-lines Make wrapper ran its Python helper, then unconditionally
moved a temporary copy and cleared the trap. A missing input or invalid range
therefore printed the native error but returned zero. Its plain copy also
replaced an executable destination with the temporary file's 0600 mode.
Three confined cases fail before repair; two added literal-input canaries
produce five native failures at /tmp/gludd-v012-ranged-edit-expanded-red.xml.
The initial three-case receipt remains /tmp/gludd-v012-ranged-edit-red.xml.

The existing wrapper now enables local shell failure propagation before
creating its adjacent temporary copy, copies with preserved metadata and
publishes only after the same unchanged scripts/replace_lines.py succeeds.
A failure cleans the temporary path and retains original bytes and mode.
Four literal internal carriers retain selected FILE/START/END/NEW_FILE values
under -e without Make or shell evaluation; public values are unexported.
No new editor, parser, temporary owner or helper CLI is introduced.

All ten native Make controls and the complete original editor, guide-contract
and environment owners pass: 65 cases in 4.61s at
/tmp/gludd-v012-ranged-edit-complete-owners.xml. The earlier 18-case receipt
remains /tmp/gludd-v012-ranged-edit-green.xml. Existing assertions remain.
The documented real example separately replaces the prepared /tmp contract
file and a native read returns gamma plus newline.

The firsthand [2012 Make user report](https://stackoverflow.com/questions/10559064/terminate-makefile-command-when-subcommand-throws)
describes a combined recipe proceeding after failed tests. Its later macOS
follow-up also reports needing explicit failure handling. This supports keeping
the critical command chain local and executable; Gludd's own native errors,
unchanged bytes/modes and shell-canary controls establish this repair.
Do not infer global shell behavior or release readiness from that discussion.

## Native HumanTodo entrypoint for real blockers (2026-10-20)

The required HumanTodo route existed as general_ludd.agent.gludd_human_todo,
but there was no named Make owner. A Make-only agent could not use it without
inventing another HTTP client or violating its command contract.
human-todo-module is a thin entrypoint to that same module, installed Ansible,
a local connection and one fork. Its two documented public variables are
HUMAN_TODO_ARGS_JSON and HUMAN_TODO_VALIDATE_ONLY. The latter defaults to 1;
literal 0 applies. Make preserves literal JSON and selected policy under -e.
The existing module owns schema, check mode, authentication, HTTP status and
HumanTodo lifecycle; the wrapper adds no authority, daemon, dependency or store.

The original routing reproduction retains 6 FAIL/4 PASS at
/tmp/gludd-v012-human-todo-target-red.xml. All 20 expanded target controls and
nine original module controls pass in 2.06s at
/tmp/gludd-v012-human-todo-target-green.xml. Opposing ambient values, Make/shell
canaries, invalid policy, exact native argv, one fork, interpreter identity,
per-invocation namespaced Ansible temporary paths and cleanup on exit 0/7
are exercised. The complete four-owner replay adds the existing target-contract
and environment-documentation controls: 71 PASS in 5.06s at
/tmp/gludd-v012-human-todo-complete-owners.xml; Ruff and strict typing pass.
The native default contract example independently exits zero,
reports changed=true and id=null, and writes no HumanTodo. It is validation
only. A live API response with an actual ID is required to claim filing.

The actual capacity request uses category=blocker, priority=high and three named
native alternatives. Native apply returns changed=false and daemon error
[Errno61] Connection refused, Make exit2, in 0.60s. No HumanTodo was filed and no
ID is invented. The exact unsubmitted input is retained at
/tmp/gludd-v012-capacity-human-todo-request.json; it is request configuration,
not a generated current observation. No broader file access or unsafe cleanup
is requested. The daemon endpoint must become available to file this request;
capacity must actually change before final release admission is retried.

The [official Ansible CLI guide](https://docs.ansible.com/projects/ansible/latest/command_guide/intro_adhoc.html)
documents module invocation and JSON arguments; its quoting guidance supports
literal transport. The [native configuration reference](https://docs.ansible.com/projects/ansible/latest/reference_appendices/config.html)
documents Ansible home and temporary configuration. Existing firsthand workflow
and recursive-Make findings above motivate this executable route; neither
upstream documentation nor check mode supplies an actual human request or
release proof.

## Native workflow repairs and retained decisions (2026-10-20)

The release preparation audit found failures that additional instructions alone
could not prevent. Repair the existing executable owners and retain their
original assertions. This support remains owned by S98.4; source acceptance
does not close a release task or authorize master promotion.

| Actual failure | Existing owner repaired | Decision and regression |
| --- | --- | --- |
| Empty TAG admitted the v0.1.1 runbook; argument-bearing and fenced commands were missed; only the include-only root Makefile was read | check_runbook_currency.py | Resolve project.version even without TAG, require one explicit active release marker, validate explicit tags, and compose the native Make fragments. |
| A target-specific variable after a recipe erased its contract; a variable alone invented an owner | check_make_target_contract.py | Reuse the duplicate-target owner's assignment pattern and retain actual recipe bodies in either declaration order. |
| Valid override/export/private assignments counted as duplicate recipes | check_duplicate_targets.py | Exclude variable assignments while preserving real duplicate detection across native fragments and all failing exit statuses. |
| Ambient UV and deferred parent flags displaced selected native inputs under recursive -e | Existing duplicate-code, collection and admission Make owners | Bind selected UV at the target; re-export native MFLAGS plus MAKEOVERRIDES only at affected owners, retaining literal canaries and unrelated-consumer isolation. |
| A prior repair removed the omitted-UV negative control | Original precommit-quality owner | Restore all six owned/omitted/opposite cases and every original assertion; isolate only unrelated outer Make flags in the synthetic fixture. |
| Generated ledger notes lost operator, safety, disabled-default and incomplete-proof boundaries | release_page_notes.py and both original test owners | Preserve the original structural assertions and derive the full preview from the versioned integration manifest, alongside ledger inventory. |
| Public-looking source prefixes could grant automatic secret review | Native baseline manager and exact public-review CLI | Review only six fixed filename/detector/digest identities; reject missing, duplicate, malformed or conflicting inputs and publish atomically. |

The runbook controls first produce14 failures. Complete release-pipeline and
runbook owners then pass299 cases. The first coverage replay still fails the
unchanged aggregate branch floor at78.6%; six missing metadata/tag failure
controls raise whole-file coverage to98.1% lines/92.9% branches. Explicit and
empty-TAG native AC013 executions both identify active v0.1.2 and resolve every
documented owner. A native run catches a nonexistent collection-interop helper
in the initial draft; the corrected runbook invokes the complete existing
collection-interop test owner and preserves its actual repository graph check.

The duplicate-declaration controls first retain13FAIL/4PASS, then17PASS.
The contract checker retains3FAIL/7PASS before its repair; the complete
contract, promotion and disk-admission replay passes152 cases plus37subtests.
The original precommit owner retains41 cases, including all six negative/
positive carrier controls. A subsequent combined replay retains522PASS/1FAIL:
the logical-Makefile audit identifies a synthetic fixture variable that aliases
a different function's project reader. Naming that argv-derived fixture
probe_makefile conforms to the existing reader policy; no assertion or guard
is removed.

The final nine-owner native coverage replay passes523 cases, no skips, on
CPython3.11.14 with fatal warnings and serial pytest. Observer:
coverage-files/20261020T183132Z-81710-ea40cdda;73.79s body/75.6s observed.
Native audit .gate-logs/coverage-20261020T183247Z.json records97.5% aggregate
line/95.1% branch coverage. Full-file line/branch pairs are93.0/94.4 for the
duplicate owner,99.0/96.0 for the contract owner, and98.1/92.9 for AC013.
All original85% aggregate/75% individual line and branch floors remain.
Ruff, strict five-file typing, production docstrings,255 target contracts and
the duplicate-code behavioral plan pass. A temporary type-check failure from
mixed flat/package module identities is repaired in typing-only imports;
runtime script and package entry points remain available.

The admission fixture also had a hardcoded desired UV declaration, so production
could lose its binding while tests remained green. Two production-mutation
controls fail first. Read the actual binding and recipe from one composed native
snapshot; retain distinct omitted and opposite cases. All36 complete admission
cases then pass in5.36s with fatal warnings. Two further guide controls fail first
until the mandatory recipe names gate-background-observed and its full explicit
release/0/16/1 inputs; the complete nine-case guide owner passes in1.95s.
Its validate-only behavioral example reports gate_evidence=false.

The existing public-review Make recipe passed an unsupported --tasks argument;
the native CLI refuses rc2. Correct it to baseline/policy argv, document both
native compaction and review contracts, and remove three uncommitted inline
Python debugging helpers after preserving their prior source. Existing
scan-secrets already owns inventory. Both native behaviors then preserve1352
findings/390files and the scanner-settings digest;257 declared contracts and
1173 target declarations pass with zero duplicates. No new identity is reviewed.

The mandatory enforcement reference also retained a three-agent minimum and
quick examples that disabled guards, contradicting root precedence. Two new
controls fail first. The current reference derives its roster from list-plugins,
states the existing adaptive0/maximum3 policy and preserves native verification,
code-restart and fundamental pending-work protection. The complete existing
guide/contract owner passes11 cases in1.98s. Historical inventories remain in
the versioned policy source/Git history; no plugin TypeScript or guard is changed.

Bounded process diagnosis extends active_work_status rather than adding another
observer. Initial20FAIL/13PASS and native-parent bound2FAIL precede complete
94PASS/0SKIP,94.8% line/91.2% branch full-file coverage; native audit
.gate-logs/coverage-20261020T184602Z.json retains85/75 on both metrics. Source
005fed3 lands as7e8359357. Make literal transport controls pass14 cases under
ordinary and environment-override Make, including expression/backtick/quote/
newline sentries and no public-variable export. The original reverse environment
audit first retains1FAIL/44PASS: a split export is not the established consumed
carrier form. Reuse the existing override plus literal export assignment pattern;
all45 complete transport/environment cases then pass in2.69s. No audit is weakened.
Native default and requested-PID behavioral examples and257 contracts pass.

Source commits50e1fb9, d9d95c0,01fcf50,4776423 and2f1e923 retain independent small
parser, runner, runbook, status-contract and PID-wiring changes. Each normal
source owner passes content/index/secret/clone guards and fresh collection with
0errors; latest127188/127212 has24 deselected. These overlapping focused scopes
are not a unique project-wide test total, full gate or exact-SHA hosted proof.

The current [release runbook](../RELEASE_RUNBOOK.md) now follows v0.1.2 through
real admission, native source review and combined acceptance, the observed
outer canonical release gate, receipt finalization, exact-SHA hosted CI,
readiness, canonical-main promotion and artifact verification. The
[same-directory v0.1.1 archive](../RELEASE_RUNBOOK_V011.md) preserves historical
procedure and exceptions. Both retain all28 categories/30 minimum assets,
15smoke checks, signatures, immutable rollback, ZDD and failed-proof boundaries.
Original documentation, pipeline, deck and complete interop owners pass322
cases in the runbook branch; both files pass locked Markdown lint.

The release-note owner restores five genuine missing source contracts/schema
from their original Git objects. Only22 trailing ASCII spaces on11 metadata
lines in three documents change; the remaining two artifacts are byte-exact.
Provenance and failed snapshots are retained in
/tmp/gludd-v012-release-note-integrity-report.md. Both complete original note
owners pass56 cases with98.7% line/97.3% branch coverage. The normal clone guard
rejects the initial adjacent-string representation; one multiline literal
preserves byte-exact generated output and passes that guard. A separate
collection refusal identifies an outer run-watched CMD containing a canonical
checkout path. Direct ship-commit-files retains native observed collection
without forwarding that unrelated wrapper carrier.

Public-source review is explicit, not inferred from AST or prefixes. Baseline
prerequisites cbac69d,572489c and d93a680 retain six original fixture reviews,
eight native findings for public audit-identity literals, and six generated
release-snapshot identities respectively. Each passes49 baseline controls and
fresh zero-error collection before commit. Native compaction records1352
findings in390files with unchanged scanner settings. The public-review helper
passes97 complete cases at98.8% line/96.5% branch coverage and cannot authorize
those additional findings. Unknown identities remain unknown. Native scan
inventory is diagnostic only; read-only admission supplies the actual verdict.

A normal source commit also exposes a missing registered owner for preserved
checkpoint db4b17f. Restore the original pages-cold-readiness branch/path, then
apply the existing native checkpoint-owner retention lock. Do not pop, apply,
drop or relabel that saved work. Five original checkpoints then verify with
unowned0. A later owned two-file integration checkpoint retains both versions
while the guide lands; reconcile only the named TASKS overlap. The native
guard now verifies all six checkpoints with unowned0. This is source recovery,
not gate, hosted or release evidence.

One parser-source commit attempt captures a real transient index.lock failure
from its first git write-tree. Fresh collection passes127132/127156 with24
deselected and0errors, but comparison correctly refuses the empty snapshot;
no commit is made. The existing owner did not propagate either snapshot's
native status, needlessly spending that collection. Three failing-first controls
cover first, second and both failures; the last reproduces a false successful
harmless capture commit when both snapshots are empty. Propagate each native
status immediately in the existing recipe. Keep current-tree drift, all guards
and one fresh collection on the successful path; never delete an unknown lock
or retry while the same failing input remains. Evidence:
/tmp/gludd-v012-commit-tree-capture-red.xml. All38 complete native collection/
commit-owner cases then pass in17.93s; the corrected real source commits retain
one fresh collection and unchanged staged-tree authority.

Serialize source commits and their temporary pre-commit stashing with checks
in the same checkout. A parallel check can otherwise inspect restored HEAD
bytes instead of the intended working tree. Preserve such observations as
transient failures, then validate the actual restored inputs. Use another
isolated checkout for independent work; do not add an observer or cache.

The earlier native-profile gate remains FAILED in
.gate-logs/gate-20261020173636-0d36c909.log. Its exact FIFO trace node reaches a
LangSmith deprecation during fresh plugin discovery on CPython3.14 before its
original assertion;624 sibling cases pass. Do not disable plugins, filter the
warning, waive the assertion or substitute that native profile for canonical
release proof. The actual final release runtime/profile, full gate and hosted
SHA still need proof. Disk measurement subsequently reaches96% against95%.
The actual existing disk-cleanup-preflight runs with dry-run0 and grace1800,
removes16 eligible entries with zero cleanup errors, then refuses rc1 because
disk remains96%. A second no-progress pass removes0; retained evidence, live
owners and checkpoint locks remain protected. Shared UV cache pruning is
withheld for actual active PID68501. Existing stale-resource namespace planning
finds39 candidates and0 reclaimable at the unchanged24-hour grace. Do not force
pruning, shorten grace, signal an unknown process, raise95%, or retry unchanged
admission/full proof. Extend the existing active-work producer with a bounded
read-only PID input to identify that actor before choosing an ownership-safe
remedy; diagnostic visibility grants no cleanup or kill authority.

Actual make active-work-status ACTIVE_WORK_STATUS_PID=68501 identifies
uv run python3 scripts/agent_watchdog.py, PPID1, CWD this repository. The native
diagnostic correctly leaves ownership unverified: CWD alone is not a termination
receipt. Existing watchdog-status reports no convenience PID file while the
old watchdog log is live. The next repair belongs to the existing acknowledged
watchdog lifecycle and native namespaced lease; it must launch installed Python
directly and preserve checked startup/identity/drain authority.

The [original upstream user report16112](https://github.com/astral-sh/uv/issues/16112)
from2025-10-03 reproduces prune/clean waiting behind an open uv run child.
[Current upstream cache documentation](https://docs.astral.sh/uv/concepts/cache/)
confirms exclusive cleanup waits and its bounded timeout; it prohibits direct
cache modification. These findings support fixing the long-lived wrapper and
using the existing native cleanup owner. They do not authorize force, broad
process kills, changed95% policy or removal of live environments/evidence.

## Mechanical scheduling admission

### Known hosted failures in the existing source-contract owner

Terminal Q Build39293586802 exposed24 original failing nodes across six shards.
The existing test-files source-contract phase selects27 entries: its original
nine plus18 additions. All24 exact Q nodes remain, together with three support
selections: the complete oversized-document owner, isolated configuration class
and native-profile admission owner. The authenticated integration resume
positive fixture is an actual Q failure. Every original selector and phase
remains; no new scanner, runner or proof cache is introduced.

The initial23-entry patch used an incomplete20-node summary: original RED
1FAIL/33PASS/6.50s identified14 missing entries, then whole routing34PASS each
runtime proved that narrower selection. Independent review then retained all
six immutable native job contexts and found four omitted unit-3a failures:
two DC.1 assertions, the exact owned/environment-overrides UV carrier case
and the P30 fix-forward prohibition. Native counts3+5+3+6+6+1 prove24;
/tmp/gludd-v012-terminal-q-failure-evidence/MANIFEST.md indexes unchanged logs
and observer JSON. Those retrieval successes do not turn Q's failures green.

The corrected assertion failed first1FAIL/33PASS/8.40s/native10.0rc2, naming
four missing inputs. After the actual four-selector Make expansion, complete
routing passes34/zero skips each:3115.02s/native5.806rc0 and3125.33s/native6.146rc0.
The original23-entry source snapshots, RED/GREEN and changes-required review
remain separate. Corrected commands/native receipts:
/tmp/gludd-v012-known-failure-admission-routing-v2.md.

Existing PHASES controls require no runtime-watch artifact after every
prerequisite failure. Original owner/order/refusal, raw-UV, fatal-warning and
serial assertions remain. Strict typing/Ruff,253 target contracts and the
documented plan-only example pass. Runtime remains113 families,2/1/1 children
and90max/60quiet; plan/admission stays release_ready=false/gate_evidence=false.

This source's base predates the separately reviewed policy/watchdog successors.
Only the genuinely combined development source may execute the expanded real
selection. Actual capacity admission, combined candidate replay, full gate,
85/75 BOTH coverage floors and exact-SHA hosted evidence remain required.
No open task or historical failed proof is relabelled by this routing change.

### Existing fixture and policy contracts before expensive proof

The full gate on da99429 passed integration preflight in495.6s, then the
existing environment-write phase refused a bare assignment in the native
profile FIFO fixture. Hosted Build39293586802 on development1bbd47e also
exposed five compact-policy assertions, two home-boundary assertions and the
Markdown owner's three displaced archive links. Preserve those failed inputs
and the natively aborted local gate; neither is a completed release proof.

The existing release-proof-admission owner now calls check-test-env-writes
and test-files after resource ownership, before its original runtime observer.
The selected tests retain all seven original policy assertions, the original
Markdown link assertion and the complete oversized-document-split owner.
Nine scheduling owners replace seven; no new scanner, runner or proof cache
is added. Original full-gate phases still replay their checks. The113 runtime
families/selectors,90max/60quiet observer,2/1/1 children,85/75 line and branch
floors, ZDD, cleanup and promotion authority remain unchanged.

The embedded child fixture now uses pytest.MonkeyPatch.setenv and undo,
preserving its actual no-writer FIFO, bounded drain and refusal assertions.
The native whole-tree environment guard failed first, then passed after this
instruction-conformance repair. Three new admission routing cases also failed
first: both cheap failures previously reached runtime, and the source-test
selection was absent. Their existing Make probe now verifies stop ordering,
the exact original node set and fatal-warning single-process invocation.

Complete diagnostic, environment-guard and admission-target owners pass all90
cases under both selected runtimes:3.11 takes9.22s/native10.4s;3.12 takes11.06s/
native12.7s, each rc0 with fatal warnings and no skips. Ruff, strict typing,
253 target contracts and file-line limits pass; the documented validate-only
behavior prints the new plan and remains gate_evidence=false/release_ready=false.
Raw native logs, observers, commands and JUnit are indexed in
/tmp/gludd-v012-source-contract-admission-evidence/MANIFEST.md.

This isolated source does not contain the independently owned compact-policy
repair. Actual final admission remains blocked until that reviewed repair is
genuinely merged. Re-run real admission only after changed failing inputs;
then freeze a candidate for combined acceptance, full canonical gate and its
own exact-SHA hosted proof. No task is closed by this source acceptance.

### Native watchdog and profiler fixture conformance

Terminal hosted Build39293586802 identified stale watchdog/deadline test
interfaces and the exact _TEST_FILES_CPROFILE reset-key omission. The current
cleanup owner still requires fresh process identity, repository roots and
protected-tree checks. Tests now reuse its existing ProcessRecord inventory
and owned poll fixture; command mismatches, TERM-before-KILL, exit during wait,
leaf-first drain and foreign/unknown-descendant refusal remain executable.
No host process is signalled and production cleanup authority is unchanged.
Three configuration probes reuse the existing isolated module loader and
scoped environments; six failing-first controls prove original environments,
canonical module configuration and aliases survive normal and failed asserts.

A native3.12 replay exposed the old sys.getprofile expectation: CPython3.12
cProfile uses sys.monitoring rather than the legacy hook. The firsthand
[CPython issue130377](https://github.com/python/cpython/issues/130377)
reports that getter incompatibility; it is not performance evidence for Gludd.
The selected native backend now proves real cProfile ownership, all seven
independent events and actual test-body pstats. The original3.11 getter assert
remains; ordinary nested Make and xdist workers also prove monitoring absence.
Pinned source and the public ancillary-event grouping contract are linked in
/tmp/gludd-v012-watchdog-env-conformance-report.md. Narrow Protocols and the
typed optional getattr boundary introduce no Any or production shim.

The report indexes every original failed and corrected receipt. Whole seven
owners pass319/zero skips on each runtime before the final typing-only delta;
post-delta finite acceptance passes19each, with strict typing and Ruff green.
Earlier219PASS/1FAIL,17PASS/1FAIL and312PASS/1FAIL stay failed. The reset scanner
already passed locally before its key addition; do not invent a local RED.
Final combined candidate acceptance, original90/60 scheduling admission,
complete gate, unchanged85/75 line-and-branch floors and exact-SHA hosted
proof remain required. No task checkbox or completion status changes here.

### Retained source handoff history

The following records are moved verbatim from TASKS/SESSION to keep the
active ledgers navigable. They remain historical source claims; their pending
states are not relabelled as current proof. The original bytes also remain at
immutable development da994299b3479fa074bc7a765766369c7e9692f5.

```text
## S98.4 AST field traversal enhancement — recorded base ca91f33743014d6667646757f34616457a81b987

Last Updated: 2026-10-19. Branch agent-v012-ast-field-traversal-20261019 uses the
existing scanner's stdlib field-values iterator; every current field, statement/
wrapper/LIFO/owner rule, source signature, fresh parse and exact GC/tree release
remains. No pruning/cache/schema flag/runner/dependency is added. Details and the
retained diagnostic's non-predictive2.459s overlap are in the owning feature doc.

Eight additive controls: RED5FAIL/3PASS1.39s; real field-pair operations23→0 with
findings intact. Final complete57-case owner passes3.11/5.37s and3.12.15/6.72s.
Unchanged full-file coverage passes57/8.01s,96%combined/97.7line/94.0branch with
85/75 retained (observer20261019T103422Z-34695-5321c9fe rc0/9.543s). Final passes
include the typed test-constructor correction; production is unchanged since coverage.
Exact commands/roster/JUnits/native log/status/actual coverage trace/audit:
 /tmp/gludd-v012-ast-field-traversal-evidence. Source review and guarded commit/
collection remain pending; no original90, diagnostic replay, gate/CI or task closure.
S98.4/19-task states and90/60/three-child/21phase2850/ZDD/cleanup/promotion stay intact.

S98.4 user-authorized temporary disk headroom, 2026-10-14: raise the single
canonical volume limit from 90% to 95% in `scripts/check_disk_usage.py`, with
help in `make/00-foundation.mk` and regression coverage in
`tests/unit/test_check_disk_usage.py`, `tests/unit/test_disk_alias_admission.py`,
`tests/unit/test_automatic_disk_cleanup_resources.py`, and
`tests/unit/test_pipeline_status_exact_sha.py`. Twenty-one new boundary/alias
checks fail first. The 251 related cases and 37 alias subtests pass on Python
3.11/3.14; existing failure assertions now use measurements above the explicitly
authorized limit. Native two-owner coverage meets unchanged 85/75 floors
(.gate-logs/coverage-user-disk95-20261014.json). Actual observed read-only
preflight `v012-user-authorized-disk95/v012-disk95-capacity-check-6dbcabc` passes
at disk 91.0%/limit 95%, scratch 836.3 MB/soft cap 100 MB, zero removals,
211 protected/skipped entries and zero errors. No gate failure is rewritten,
unknown-data cleanup authorized or release item completed. Restore 90% when the
user ends this exception; fresh gate/exact-successor/deployment proof is pending.

```

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

### Frozen-candidate repair admission (2026-10-18)

Candidate `31b2b66be80fb96cdbb2fcb6a8e03a775e5cd0a6` has terminal
[Build and Release failure](https://github.com/sandboxcom/gludd/actions/runs/39084793178):
unit-1b rejects retired CSS contracts and helper Git mode, unit-3a rejects
duplicate hook/read boundaries and the original plugin budget, and unit-3b
rejects the same budget through its unchanged six-repair contract. All other
shards, both quality legs, Molecule and Pages pass for that historical SHA.
The local diagnostic gate is explicitly ABORTED after owned termination with
no survivors; its log `.gate-logs/gate-20261018010048-53f46f80.log` and native
`gate-kill-evidence.json` remain failed/non-passing evidence.

The existing first compatibility cohort gains the complete three-case CSS
workflow file, five exact plugin/mode/budget nodes, the unchanged native CSS
config pin and the complete 15-case native scanner resource file. The manifest
grows from 86 to 96 families.
The scanner family captures the separately reproduced native CPU fan-out
defect; it does not invent another hosted failure. No original family,
selector, child invocation, fatal-warning flag or downstream assertion is
removed. The 21 phases, 2850-second ceiling and 90/60-second owner bounds remain.

Failing-first repair acceptance records 22 failures and 99 passes before
implementation; the scanner resource controls separately record 14 failures
and two passes. The scanner's new whole-file admission control fails against
the 94-family owner before its routing is added; the native CSS pin's exact-node
control separately fails against 95 before its selector is added. The latter
preserves the unchanged original dependency-ignore assertion while restoring
the existing vendor boundary. After implementation, the
combined repair cohort passes 234 cases, the explicit resource bindings pass
62, and the final promotion/resource files pass 90. These overlapping focused
runs are not full-gate proof. Native manifest validation reports 96 families
and Make-contract validation reports 243 targets.

Actual source `a521b8e` scheduling admission stops before candidate sealing:
1823 cases pass and the original cold-start exact-roster control fails. A bounded
replay of that file retains one failure and 53 passes. The shared literal
`ROSTER` now gains the same eight already-owned selectors in their actual order;
every original roster entry, exact-match assertion, mutation control, native
flag and fresh-process boundary remains. No family or runtime phase is added.
The complete scheduling/cold-start/promotion slice then passes 142 cases in
47.20 seconds. The native failed admission is retained at
`/tmp/gludd-v012-admission-a521-failure.log`; source-only approval of a521b8e
is not approval or admission for its successor. Real scheduling admission must
pass after this declaration repair before sealing or final proof.

The terminal hosted timing report is produced by the existing
`ci-run-summary` owner with `CI_RUN_SUMMARY_SHARD_TIMINGS=1`, retaining exact
run/SHA identity and all eight shards. Failed shard intervals remain censored.
No scheduling improvement is claimed before measuring the successor's hosted
SHA. Independent source review, actual native admission, canonical coverage/
full gate and matching hosted/deployment acceptance remain required.

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

### Logged release admission and authenticated resume

The canonical release-profile `make gate` owns scheduling admission before its
preflights, lint, collection and eight-shard test phase. Its existing profile and
clean-candidate prerequisites still run before lock acquisition. After taking
the native gate lock, the recipe preserves the previous raw status in a
content-addressed, exclusive `.gate-logs/gate-resume-prior-<digest>.status`
archive before publishing this invocation's running status. It never names old
proof bytes after a newly selected commit.

The release recipe emits `=== GATE PHASE: admission ===` and streams the
existing `release-proof-admission` owner into a fresh private native log.
The existing seven owners retain their order, read-only capacity preview and
configuration, registration, task-integrity, promotion and resource checks.
Only the final runtime compatibility owner runs through `run-watched`, with
its original command, selected installed environment, three sequential children,
2/1/1 workers, 90-second total / 60-second quiet bounds and fixed ten-second
heartbeat. The facade does not advertise a heartbeat override. No interpreter,
plugin, selector or proof authority is substituted.

A failing admission records its exact owner and native log pointer in the
partial status. The existing terminalizer writes authenticated `FAILED` with
test and smoke `NOT-STARTED`; later phases do not start. The diagnostic log
retains the real failing node, assertion, timeout, capacity reason or unchanged
input refusal. A passing admission permits gate work to start; it supplies no
green gate, exact-SHA CI, deployment proof or release authority.

The watched runtime uses the current valid native work-item focus. On a fresh
checkout, provide the actual active `WORK_ITEM_ID` or initialize it through
`make work-item WORK_ITEM_ACTION=focus WORK_ITEM_ID=ACTIVE_TASK_ID WORK_ITEM_REASON='' WORK_ITEM_VALIDATE_ONLY=0`.
`ACTIVE_TASK_ID` is a placeholder for a canonical open task, not an automatic
milestone selection. Missing, inactive or unrelated IDs fail before the runtime
child starts. A supporting ID must already have an explicit native declaration.
Existing unattended observer callers keep their established behavior. The new
entrypoint references carry task IDs as literal data: Make functions, shell
substitutions, backticks and quote-bearing invalid values fail before child
creation or plan output. The guard does not change unrelated observer callers.

The unchanged-input retry guard remains in the existing item-progress owner.
For the exact runtime compatibility command only, the candidate SHA and gate
owner PID fields are lifecycle metadata rather than semantic test input.
Profile, resume policy, batch size, workers, selected UV/environment, pytest
options, warning policy and unknown variables remain semantic. Native Make
carries changed command assignments in `MAKEFLAGS`; the existing volatile
treatment remains. `MAKEOVERRIDES` is retained byte-for-byte, including its
native opaque reference or any explicit escaped value. It is never parsed as
shell words or broadly stripped. A new SHA, observer label or PID cannot alone
authorize unchanged failed or timed-out runtime inputs.

`make gate-resume` authenticates the failed status against fresh strict
repository identity, the current index and signed profile/files/workers policy.
Only a fresh HMAC-valid current-state status with one successful preflight
record permits the original resume-always subset. New HEAD, changed content or
mode, index mismatch, stale or unsigned status, malformed/duplicate records and
policy changes take an explicit cold path. The gate revalidates the archived
snapshot at its preflight boundary; a facade decision cannot bypass that fence.
Existing exact batch receipt validation and original full-gate replay remain.

Use the safe, mutation-free behavior example:

```text
make gate-resume GATE_TIMEOUT=3600 GATE_BACKGROUND_VALIDATE_ONLY=1 GATE_TEST_PROFILE=release GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 WORK_ITEM_ID=''
```

This plan validates configuration and describes the prospective launch. It
does not start a child, copy a status, publish a PID or create gate evidence,
including in a fresh checkout. Actual resume retains the failed-status and
prior-log prerequisites and uses the existing isolated-session launcher without
changing its canonical gate argv, PID ownership, watchdog or cleanup lifecycle.

`gate-progress` derives admission diagnostics from the selected native log,
including the observed runtime run/log pointer. An unrelated status file or
another live gate cannot relabel a selected terminal log. Every admission-only
state, including PASS followed by interruption before full-gate work, reports
zero completed gate segments and unknown ETA. Native `work-item:` refusals
retain their missing-focus or invalid-primary reason without inventing an
observer identity. The public Make target uses the selected installed `UV`
runtime and the current checkout parser; a local `.venv` is not required. Its
cooldown record stays in the checkout-owned `.gate-logs` namespace. Existing
force, cooldown and print-before-sleep behavior remain. Use
`make gate-progress GATE_PROGRESS_MAX_HISTORICAL=20 GATE_PROGRESS_COOLDOWN_SECS=0 GATE_PROGRESS_FORCE=1 GATE_PROGRESS_SLEEP=0 GATE_PROGRESS_ARGS=--help`
for the safe behavioral example. The next action is to repair the named owner
and its actual input before resuming; changing a run ID supplies no proof.

An isolated implementation branch is not the development pipeline source.
The timed native original-90 run at clean development `ca91f33` failed its
90-second bound; that historical failure remains. Reviewed source commits merge
once into development. Dependent active worktrees then merge-forward those same
commits before combined acceptance and exact-source proof. No feature is
independently recreated in a second branch, and a worktree test never relabels
the last hosted development result.

The existing `ci-failure-repair` owner may record a meaningful changed-owner
repair on the exact clean descendant SHA as `repaired`, awaiting hosted CI.
The old private-3.12 admission does not have to pass first. This local repair
state remains distinct from `resolved`, which requires later matching hosted
success. Native `batch-push` independently requires the canonical Python 3.11
90/60 admission and all its normal guards. A focused repair cannot substitute
for that admission, clear the retained `ca91f33` timeout or establish final
gate/release proof. Do not add an extra local-3.12 prerequisite that idles
otherwise policy-admitted development CI.

In [GitHub Community discussion #26071](https://github.com/orgs/community/discussions/26071),
the July 2020 reporter initially suspected manual Actions runs used an old
commit. Their August follow-up traced it to scripts baked into a container
rebuilt only on master, so a different branch still executed old scripts.
This is firsthand evidence to distinguish a trigger or commit label from the
code actually loaded. It is not evidence of Gludd's timeout cause, a current
GitHub defect or a measured benefit from resume.

The finite native fixtures exercise the actual gate recipe with a deliberately
failing admission owner, real terminal authentication and the native progress
parser. Their retained log/status show the exact blocked owner and unstarted
later phases. These checks use a seven-file coverage profile with nine complete
test owners and keep 85% aggregate and 75% per-file line and branch floors. They
are source acceptance only; the canonical gate, exact-SHA hosted CI, deployment acceptance
and branch promotion requirements remain separate. ZDD changes only this
control-plane workflow; no serving process is restarted or replaced.

### 2026-10-19: authenticate the integration resume fixture

The full gate at frozen development `1bbd47ed0e27c62f2e49c9542baa0d1e4222ca1f`
failed `test_preflights_skip_when_exact_sha_resume_receipt_exists`:
1 failed, 3467 passed and 13 skipped. The old integration fixture wrote an unsigned
`preflights PASS 35` line under a synthetic SHA filename. The production owner
correctly requires an explicit prior-status pointer, a native terminal signature,
fresh current repository/index identity and the matching profile/files/workers
policy. The retained failed gate remains failed.

The fixture now follows the existing preflight unit owner's committed private
Git repository pattern. Its key, prior receipt and status outputs stay under
pytest's temporary namespace. It uses `sign_terminal_status` and the actual
`repository_state_id`, then executes the current Make preflight recipe and native
verifier. The resume-always targets delegate to the real repository's registration,
line-limit and dead-code-baseline checks; all original positive skip assertions
remain. No main index, key or status is staged or rewritten.

Unsigned, tampered, stale and changed-policy receipts must take the cold path
and run the original deliberately failing full-preflight target. A separate
control proves that a valid signed failed receipt can authorize preflight reuse
while `verify_status` still refuses it as green full-gate evidence. Disabling
resume also runs the full-preflight target even with a genuine signed receipt.

The [firsthand branch/code report above](https://github.com/orgs/community/discussions/26071)
supports distinguishing the source actually executed from its commit label.
It does not describe this fixture failure or establish an authentication defect.
Here the native refusal and the existing signed unit fixture identify the
concrete input mismatch; production authentication, freshness and ZDD remain
unchanged. Complete integration and authentication-owner acceptance on both
installed runtimes is source evidence, never a replacement for the retained
failed gate, exact-SHA hosted CI or release proof. All 19 tasks remain open.

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
