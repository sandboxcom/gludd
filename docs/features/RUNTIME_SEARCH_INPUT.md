# Confined runtime search input

S98.4,2026-10-22 UTC. Root receives this test-input repair from exact
51b782ba9b962d916f51ec04f35e5b9770ffb352 on
release-candidate/v012-git-mutex-20261022, retaining sole Main/shared/Git ownership.
The consumed private decision and permanent postimage are in
/tmp/gludd-v012-runtime-search-input-proposal/; its fixed Main-root execution
bridge does not land. The actual test derives REPO_ROOT from its repository path.

## Measured trigger and changed input

Exact eb772 Build39826135809 Python3.12 admission fails124/90.5s. Main2371
passes85.10s and fresh help1 passes1.23s; no tail completion is observed. The
native job log retains a4.29s span for the original default-search test. This
locates one unnecessary input cost; it proves no CPU share, critical path,
saving, stable margin or cause for the censored tail. Do not sum overlapping
xdist spans into elapsed-time savings.

The original test invokes make search across the whole checkout to verify its
workspace default. Use its exact current raw-capture/recipe block from
compose_makefile in a tiny owned workspace containing make/workspace-only.mk.
Keep a matching outside-only.mk in a sibling PATH directory. Omit SEARCH_PATH;
clear only stale fixture SEARCH_PATH/private search transport, retaining actual
PATH tools. Both original success/output assertions stay verbatim. Seven other
original test bodies remain; five additive native cases cover real -e success
and ordinary/-e shell-PATH/valid-foreign-root mutations. The actual rg/grep runs;
wrapping subprocess.run delegates instead of replacing execution. Verify its
complete argv, kwargs and CWD outside the wrapper context.

## Failing-first and canonical acceptance

Canonical tests-first input retains1FAIL/7PASS1.44s, native2/about2.3s:
.gate-logs/observed/runtime-search-receiving-red/v012-runtime-search-receiving-red311.json.
The two original assertions pass; the new CWD guard fails at None versus the
owned workspace. This is actual boundary RED, not a collection/tool failure.

The complete help owner plus complete parameter/fixture collision owner then
passes72 on each runtime, fatal warnings/zero skips:

| Native receiving | Actual result |
| --- | --- |
| runtime-search-receiving/v012-runtime-search-receiving311 |72PASS5.64s/native0/about6.5s|
| runtime-search-receiving/v012-runtime-search-receiving312 |72PASS6.33s/native0/about7.4s|

Receipts live under .gate-logs/observed/; raw logs and XML are
/tmp/gludd-v012-runtime-search-receiving311.{log,xml} and the312 siblings.
Ruff and separate strict typing of the canonical test pass. Exact native argv,
installed311/312 runtime identity and ordinary/-e outcomes remain in those logs.
The private decision retains its earlier source RED, two complete13-case final
positives and actual I001 formatting refusal/correction; those are not Main runs.

To replay, use make test-files with TESTFILES='tests/unit/test_make_help_audit.py
tests/unit/test_pytest_param_fixture_collisions.py', PYTEST_ARGS='-q -W error -n 0'
and the mandatory Make guide's complete installed runtime tuple, once per runtime.
Use the existing bounded run-watched owner with WORK_ITEM_ID=S98.4, an explicit
unique label/run/log, trace1,60quiet/90total and native10s heartbeat. Never sync
or download into a live runtime. Changed test code needs complete-owner/static
acceptance and normal fresh per-commit collection; a receipt-only prose update
does not justify repeating an unchanged expensive admission.

## Retained authority and next proof

Production search, selectors/order, three2/1/1 admission children,90/60,
21phases2850,eight promotion pins,fatal warnings,ZDD,cleanup and85aggregate/
75each LINE AND BRANCH remain. No new cache/parser/runner/observer/dependency,
production Python or coverage exemption is introduced. This fixture already runs
in the original admission; no new promotion family is needed for its unchanged
public assertion boundary.

Normal collection, source review, the original family repair through its native
ledger, guarded push and exact-SHA hosted proof remain required. Batch the known
independent Pages input repair before the next hosted run. A local focused pass
or the controlled changed-input times cannot clear either hosted failure, the
full gate, deployment acceptance or any of the seven unchecked release tasks.
