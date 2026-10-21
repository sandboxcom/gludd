# Canonical Coverage Source Identity

## Problem and existing owner

S98.4's complete watchdog/observer cohort passes376 cases per installed runtime,
but the native311 coverage run fails38percent after counting seven altered
fixture agent_watchdog.py copies as separate source files at20percent. Retain
.gate-logs/observed/coverage-files/20261021T161645Z-23015-f1e00019.{log,json}.
Passing the tests does not supply passing source coverage.

The existing coverage-files Make recipe captures its physical working directory
with pwd -P before the observer starts. Its private GLUDD_COVERAGE_SOURCE_ROOT
export stays fixed across subprocess CWD changes and replaces an inherited fake
value. Callers do not select this identity. The same watchdog profile requires
that variable in its two absolute include rules. Direct use outside the owning
Make environment refuses a missing variable rather than silently widening scope.

All canonical agent_watchdog and watchdog_components files remain eligible;
branch=True, parallel=True and patch=subprocess remain. Altered temporary copies
are distinct source bytes and must not be folded onto canonical paths. No omit,
path mapping, source exclusion, reduced floor, new profile, observer, parser or
cache is introduced. Existing85aggregate/75each-file line AND branch audit,
report promotion and failure cleanup stay authoritative.

## Native acceptance and repeatable use

The additive owner exercises native coverage with parent and changed-CWD child,
canonical synthetic filenames and altered sibling copies. Native extracted-Make
controls verify actual root export before observer handoff and conflicting
inherited input. These controls establish source selection; the entire real
watchdog cohort must still meet its measured coverage thresholds.

Root's repository-harness baseline retains9FAIL/98deselected in1.82s/native2.7s
before the two-line Make and two-pattern profile repair. The complete original
98 cases plus9 additive cases then pass107 on311 in3.52s/native4.3s and312 in
4.11s/native5.0s, /tmp/gludd-v012-coverage-source-complete{311,312}.{log,xml}.
Ruff and canonical strict scope pass; check-make-target-contract retains276.
The agent's full decision is /tmp/gludd-v012-coverage-source-decision.md, fully
read by Root, including its separate collection refusal and corrected new API
assertions. Every original test body/assertion remains unchanged.

Corrected real-source measurement still fails:376PASS25.10s/native26.9s,
88.7line/84.7branch aggregate and enforcement79.0line/74.6branch. Exact native
run20261021T163606Z-32234-a7ca39a1 and audit
.gate-logs/coverage-20261021T163633Z.json retain the failure. This is no longer
fixture-source pollution. Meaningful heartbeat source/latch controls exercise
the uncovered enforcement behavior below; floors/scope stay unchanged.
A previously accepted452-case lifecycle receipt measured its own four-file
profile; it cannot supply this whole-profile coverage acceptance.

The16 additive heartbeat cases then pass individually0.70s/native1.6s and within
the same13 complete owners:392PASS25.31s/native26.9s, audit
.gate-logs/coverage-20261021T164644Z.json passed:true89.2line/85.9branch.
All nine nonempty canonical files meet75 BOTH; enforcement reaches81.1line/
79.0branch. The empty package initializer remains included. Original tests and
all production enforcement bytes remain unchanged. Root fully reads
/tmp/gludd-v012-watchdog-heartbeat-decision.md and the scoped import/insertion
delta. Its pre-pytest external-observer-root refusal and strict80 legacy-import
errors remain failed evidence; neither is a strict or behavioral pass.

The first392-case312 attempt retains25FAIL/367PASS/3ERROR29.57s/native30.9s,
/tmp/gludd-v012-watchdog-secrets-owning-final312.{log,xml}. Required /bin/ps and
sysctl inspection is denied, followed by cleanup/resource errors. Exact child
39740 is later missing under the native approved host diagnostic. The same
source/cohort/limits then pass392 in20.30s/native21.7s through approved host Make
execution, /tmp/gludd-v012-watchdog-secrets-owning-host312.{log,xml}. No assertion,
source, suppression or process-identity requirement changes for that replay.

Cheap native root-spoof detection joins the first main child as family145 after
all144 predecessors. Independent literal pins first retain5FAIL/1PASS1.57s/
native3.5s, then the canonical checker passes all six pins1.46s. Original three
children2/1/1,90total/60quiet,21phases2850 and authoritative full gate remain.

## Temporary pytest fixture collection boundary

The complete eight routing owners retain6FAIL/472PASS130.34s/native132.0s at
/tmp/gludd-v012-coverage-source-routing311.{log,xml}. All six compact reporting
modes select the project configuration for files outside its root, so ancestor
collection reaches an unrelated protected /private/tmp/codex-daemon-501 entry.
That path is never explicitly inspected or granted access. A new actual-child
root/cutoff assertion retains1FAIL1.36s/native2.3s before the fixture supplies
its own physical --rootdir and --confcutdir, with -c still selecting the exact
project configuration. All original nodes, both two-worker variants, plugin/
fixture/conftest snapshots, fatal warnings and six outcome assertions remain.
Focused six modes then pass17.23s/native18.1s. The complete eight-owner receiving
cohort then passes478 on311 in121.84s/native123.3s and312 in125.39s/native126.8s,
/tmp/gludd-v012-coverage-source-routing-confined{311,312}.{log,xml}. Fatal
warnings, no test skips/collection errors and all original assertions remain.
The earlier source failure is retained; normal guarded collection and independent
source review remain separate required receipts.

[Pytest's configuration reference](https://docs.pytest.org/en/stable/reference/customize.html)
and [native option reference](https://docs.pytest.org/en/stable/reference/reference.html)
separate configuration selection from collection cutoff. Firsthand
[2023 cutoff behavior](https://github.com/pytest-dev/pytest/issues/11261) and
[2024 resolved/unresolved path report](https://github.com/pytest-dev/pytest/issues/12291)
show why explicit matching physical boundaries matter. Their historical version
cases are context; native negative/positive controls establish this repair.

Before claiming the changed Make owner works, run check-make-target-contract
and its unchanged documented ten-owner behavioral example with the installed
runtime. This is a source acceptance example, not release or hosted proof:

```console
make coverage-files \
  COVERAGE_TESTFILES='tests/unit/test_observed_command.py tests/unit/test_xdist_trace_diagnostics.py tests/unit/test_gate_refresh_streaming.py tests/unit/test_observability_guardrails.py tests/unit/test_active_work_status.py tests/unit/test_active_work_resource_observability.py tests/unit/test_runtime_startup_diagnostic.py tests/unit/test_item_progress_retention.py tests/unit/test_pytest_param_fixture_collisions.py tests/unit/test_xdist_trace_timings.py' \
  COVERAGE_CONFIG=config/coverage_observed_command.ini \
  COVERAGE_REPORT=.gate-logs/coverage-observed-command.json \
  COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 \
  OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 \
  OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20 \
  UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 \
  UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
```

The unchanged ten-owner behavioral example passes472 in62.66s/native64.8s.
Exact run20261021T163947Z-34182-e7fe11d3 and audit
.gate-logs/coverage-20261021T164052Z.json retain91.3line/85.6branch, all four
canonical files above75 in both metrics, passed:true. Make lint retains1157
unique targets and9CI/12local parity; the documented example and276contracts
are terminal source acceptance. No new public variable is required.

The native producer owns each immutable/current result. A failed test, aggregate
report or per-file audit remains failed; no terminal receipt is inferred from a
launch ID, collection count or preliminary source review. Final receiving counts,
source review and guarded collection require their own native receipts.

## Operating acceptance and command transport

The complete eight operating/documentation owners plus the live-env-doc pin
retain3FAIL/233PASS31.31s/native32.4s when native process inspection and the
Git collection lock are denied. Keep
/tmp/gludd-v012-coverage-source-operating311.{log,xml} and failed native
collection20261021T221043Z-66343-405bfe16. No collection result is inferred from
that refusal. The same236 cases then pass88.81s/native89.8s with approved host
Make access, /tmp/gludd-v012-coverage-source-operating-host311.{log,xml}.
Ruff eight scopes, separate strict named-shard/promotion/scheduling scopes,
276contracts,1157unique Make targets,Markdown eight files,SESSION structure,
registration19 and the unchanged line guard pass. The legacy strict80 diagnostic
above is not included among those strict passes.

Keep fixed nested acceptance commands in outer single quotes and double-quote
the child TESTFILES/PYTEST_ARGS values, with the selected runtime tuple explicit
for both child and outer Make. The actual native operating receipt retains the
complete argv. In this session that literal form reused the approved Make
boundary; earlier concatenated quote forms produced fresh app approval records.
This is observed transport behavior, not a general authorization guarantee.
Existing Make owners, native statuses and all source/proof limits remain.

The392-case coverage receipts measure Root's receiving working tree, including
the pending watchdog adapter. They are not exact-commit coverage proof for a
partial staged source unit. Source identity's complete107-case owner and472-case
contract behavior are separate acceptance. Normal guarded collection,
independent combined source review and required exact-SHA final proofs retain
their own authority after source integration.

## Source basis and release boundary

The [primary coverage configuration reference](https://coverage.readthedocs.io/en/latest/config.html)
documents required environment expansion, file-pattern selection, subprocess
measurement and combining parallel fragments. Its path-equivalence guidance
supports mapping identical source locations; it does not justify combining
altered fixture copies with production bytes. No primary evidence establishes a
coverage-library defect in this incident.

A [firsthand2024 Windows report](https://github.com/coveragepy/coveragepy/issues/1752)
records duplicate relative/absolute paths across parent and subprocess data.
Maintainer investigation identifies combine-order-dependent mapping and records
a fix in7.4.4. That resolved historical issue motivates explicit parent/child
identity controls; it does not diagnose this repository's altered-fixture
selection failure. No historical SQL deletion workaround is adopted, and no
installed-version upgrade is inferred from the report.

Actual volume98percent/5.8GiB against95 and original publication60907's
124/90.554s/trace_path:null remain unresolved. This source-identity repair grants
no unchanged release admission, full-gate/publication retry, exact-SHA hosted or
deployment proof. All seven S98 tasks stay unchecked;85/75 BOTH,ZDD,retention,
original3children2/1/1,90/60,21phases2850 and guarded promotion remain.
