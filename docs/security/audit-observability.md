# Security audit observability

`make security-audit` runs the complete local secrets, SAST, Python dependency,
Node dependency, and security-backlog audit. Every phase emits one compact JSON
`started` event, bounded `running` heartbeats, and a terminal event with elapsed
time and the real exit code. The aggregate result is written atomically to
`dist/security-audit-summary.json` by default.

The secrets phase is deliberately different: detect-secrets stdout and stderr
are never forwarded. Only phase status and timing are observable, so credential
values cannot be copied into terminal, agent, or CI logs. This follows the
[detect-secrets baseline workflow](https://github.com/Yelp/detect-secrets),
which keeps hashed findings in a baseline and uses `detect-secrets-hook` for new
findings. The wrapper does not parse or reimplement secret detection.

Normal commit and push gates run `scripts/detect_secrets_readonly.py`. It copies
the canonical baseline to a namespaced temporary file, delegates detection to
the upstream `detect-secrets-hook`, and removes the copy on both success and
failure. Non-update statuses propagate unchanged. For upstream status 3, the
wrapper compares counted filename, detector, and hashed-secret identities in
the canonical and disposable baselines. It returns success only when the update
adds no identity; a new or duplicated finding, malformed scanner output, or
operational error remains fail-closed. Line numbers and generation timestamps
are deliberately excluded because they are presentation metadata, not secret
identity. The upstream hook remains available only at the explicit `manual`
pre-commit stage for baseline maintenance. This keeps the gate zero-downtime:
scanning can reject a candidate, but it cannot mutate the candidate or force a
gate/commit/push retry. Rollback is limited to removing the wrapper and restoring
the prior hook stage; detection remains owned by the upstream scanner.

This boundary follows upstream's documented design: the hook automatically
keeps baselines current, while line numbers are presentation metadata rather
than secret identity. Long-lived practitioner reports
[#149](https://github.com/Yelp/detect-secrets/issues/149) and
[#212](https://github.com/Yelp/detect-secrets/issues/212) document the resulting
baseline rewrites and failed commit loops. Gludd therefore preserves upstream
detection but confines its intentional mutation to a disposable copy during
admission gates.

## Baseline refresh and canonical compaction

`scripts/manage_secrets_baseline.py` keeps `.secrets.baseline` as the one
supported, non-slim detect-secrets JSON document. Refresh delegates discovery
and stale-entry pruning to the pinned `detect-secrets scan --baseline` CLI. The
`--baseline` form is important: it initializes upstream's
`detect_secrets.filters.common.is_baseline_file` filter with its required
filename context. Gludd then verifies that the plugin configuration and every
scan-surviving filename/detector/fingerprint occurrence are unchanged, carries
forward surviving audit labels, sorts deterministically, and serializes compact
JSON. It never parses source text or implements a detector.

The exact exclusion regex is versioned in
`config/detect_secrets_baseline_policy.json`. It excludes the baseline itself;
the generated `uv.lock`, plugin-hash, and resource-ownership inventories; the
retired sandboxcom key paths; root Git, virtual-environment, distribution,
build, worktree, cache, dependency, and vendor directories; plus recursive
Python/cache, `node_modules`, and vendor directories. Near-name source paths
such as `build_plan.py` and `vendor-selection.md` remain scanned. A policy test
pins both sides of that boundary.

Replacement is zero-downtime: the mature scanner updates a disposable copy,
all semantic checks finish before publication, the compact candidate is flushed
and `fsync`ed in the destination directory, and one `os.replace` publishes it.
Any scanner, schema, plugin, filter, or write failure leaves the prior baseline
in place. Rollback is the inverse single-file Git revert together with the
policy/manager commit; normal admission continues to use a disposable copy
during either version.

This is JSON compaction, not upstream `--slim`: audit labels and line metadata
remain available, and `detect-secrets audit --stats` is tested against the
compact artifact. It is not an adjudication of existing findings, a claim that
excluded generated/vendor material is secret-free, or a substitute for live
verification. A dynamically assembled credential regression proves that the
upstream hook still blocks a genuinely new finding. Only counts and digests may
reach logs; neither baseline payloads nor scanner stdout/stderr are emitted.

The separation responds to the long-lived update-versus-hook confusion in
[detect-secrets issue #246](https://github.com/Yelp/detect-secrets/issues/246)
without replacing the maintained tool. Issues
[#149](https://github.com/Yelp/detect-secrets/issues/149) and
[#212](https://github.com/Yelp/detect-secrets/issues/212) remain the evidence
for keeping intentional metadata refresh out of commit/push admission.

The manager's direct, content-free behavioral equivalents are:

```console
uv run python scripts/manage_secrets_baseline.py refresh --baseline .secrets.baseline --policy config/detect_secrets_baseline_policy.json --repo-root . --executable .venv/bin/detect-secrets
uv run python scripts/manage_secrets_baseline.py check --baseline .secrets.baseline --policy config/detect_secrets_baseline_policy.json --repo-root . --executable .venv/bin/detect-secrets
```

Project automation exposes these through Make; operators do not run the direct
commands under the repository's make-only execution policy.

Bandit remains the SAST engine. Its documented
[JSON formatter](https://bandit.readthedocs.io/en/1.7.3/formatters/json.html)
feeds `scripts/summarize_sast.py`; the summary intentionally excludes source
code and issue text. The audit passes `--ignore-nosec`, so legacy suppression
comments cannot hide findings or produce repeated suppression warnings. It
contains counts grouped by severity, rule, and file. It also emits source-free
coordinates for every high- or medium-severity finding so remediation can be
automated without exposing snippets or issue text:

```json
{
  "totals": {"baseline": 10, "current": 12, "delta": 2},
  "by_rule": {"B104": {"baseline": 1, "current": 2, "delta": 1}},
  "actionable_findings": [
    {"filename": "src/server.py", "line": 42, "rule": "B104", "severity": "MEDIUM"}
  ]
}
```

Pass either a prior Bandit JSON report or a prior generated summary through
`SAST_BASELINE`. This makes changes actionable without conflating Bandit's
reporting threshold with the audit's exit policy. That separation addresses the
long-lived operator request in
[Bandit issue #696](https://github.com/PyCQA/bandit/issues/696), opened in 2021,
whose CI use case requires retaining all findings while gating at a separately
chosen threshold. Upstream's 2026
[progress-to-stderr change](https://github.com/PyCQA/bandit/pull/1422) also
confirms that scanner progress belongs on a side channel rather than inside the
machine report.

## Usage

Run the full audit with explicit operational bounds:

```console
make security-audit SECURITY_AUDIT_HEARTBEAT_SECS=15 SECURITY_AUDIT_PHASE_TIMEOUT_SECS=1800 SECURITY_AUDIT_VALIDATE_ONLY=0 SECURITY_AUDIT_SUMMARY=dist/security-audit-summary.json SAST_REPORT=dist/sast-report.json SAST_SUMMARY=dist/sast-summary.json SAST_BASELINE= GLUDD_WORKER_LIMIT=8
```

Generate only the SAST comparison:

```console
make sast-summary SAST_REPORT=dist/sast-report.json SAST_SUMMARY=dist/sast-summary.json SAST_BASELINE=
```

Heartbeats are limited to 5–300 seconds and each phase timeout to 60–7200
seconds. A timeout terminates the phase process group and records exit code 124.
All phases run even when one fails, so a single invocation yields the complete
actionable failure set. `SECURITY_AUDIT_VALIDATE_ONLY=1` exercises all telemetry
and summary-writing paths without running scanners or contacting registries.

Do not attach raw detect-secrets process output to bug reports. Share the
aggregate audit summary and the SAST summary; neither contains credential values
or source snippets.

## Native scanner resource budget

The audit secrets phase still calls the existing `make secrets-scan ARGS=` owner.
That owner and `make scan-secrets` retain the disposable baseline, detector
configuration, thresholds, scan arguments and scanner exit status. They pass
the installed detect-secrets 1.5.0 global option before its subcommand:
`detect-secrets --cores N scan --baseline <temporary-copy>`.

The existing `GLUDD_WORKER_LIMIT` setting defaults to 8 and keeps the canonical
1..128 clamp in `scripts/active_work_status.py:_worker_limit`. Native scanner
parallelism is `min(multiprocessing.cpu_count(), worker_limit - 3)`. The same
host-CPU function supplies the scanner's existing default ceiling, so this cap
cannot raise parallelism on a smaller runner. Three slots remain reserved for
the active gate, resource tracker and possible Python 3.14 forkserver. On the
observed 8-core/8-slot host the cap is 5 scanning workers. A configured effective
limit below 4 refuses before starting the scanner; it is not silently widened.

The active-work report retains every observed process. A resource tracker is
native multiprocessing infrastructure, rather than another scanning worker;
its presence under spawn/forkserver is documented by
[Python](https://docs.python.org/3.14/library/multiprocessing.html#contexts-and-start-methods).
No lease-accounting rule changes or new scanner, runner, daemon or cache are
introduced. Existing heartbeat/phase bounds and all five audit selectors remain.
After the failed frozen gate ended, both public scan examples pass. Native
process inspection records five scan children and one resource tracker; the
active-work owner reports six workers against the unchanged limit of eight.
The actual five-phase audit passes in 88.378 seconds, including a 67.808-second
secrets phase, with the original 15-second heartbeat and 1800-second phase bound.
Its native result is `dist/security-audit-summary.json`; this is focused repair
acceptance, not canonical full-gate or hosted release evidence.

The first actual audit retains a separate failed diagnostic at
`/tmp/gludd-v012-security-audit-stale-baseline-failure.json`: the former usage
example referenced a nonexistent comparison baseline. The examples now pass
`SAST_BASELINE=` explicitly, matching the existing optional-baseline default;
operators comparing reports supply an actual prior report or summary. Scanner
settings, SAST reporting policy and all audit phases remain unchanged.

The flag and default are supplied by the
[version-pinned CLI parser](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/core/usage/__init__.py);
the [native collection](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/core/secrets_collection.py)
owns its process pool and child settings. The long-lived
[Python 3.8 multiprocessing report](https://github.com/Yelp/detect-secrets/issues/452)
shows why preserving that settings initializer matters. This change retains it;
the report is historical context, not evidence of a current detector failure.


## Full scan refresh and finding admission (2026-10-21)

S98.4 source base is reviewed development e946b08. The existing full-tree
Make owners previously ran detect-secrets scan against a temporary baseline,
discarded its updated inventory and trusted only the scanner exit code.
Pinned [v1.5.0 scan](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/main.py#L18-L79)
merges new findings into the baseline and can return0. That is refresh success,
not a decision that no new finding exists. The
[native hook](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/pre_commit_hook.py#L16-L67)
has a different finding-admission contract; the earlier single-TASKS profile
does not measure this full-tree workload.

The actual native public-target controls first return2FAIL in1.92s/native3.1s:
a synthetic new fingerprint and malformed updated inventory are both wrongly
accepted when the fake upstream scanner returns0. Preserve
native-scan-semantics-red/v012-native-scan-semantics-red312 and its immutable
log/XML. No real repository scanner is substituted for those controlled cases.

Both public Make owners now invoke the existing scripts/detect_secrets_readonly.py
with --scan-cores N. The same existing Counter records filename, detector,
fingerprint and occurrence multiplicity from the private copy before scan.
Only after native0 does it compare the updated private copy with that snapshot.
A positive difference returns1, including equal-total removal/duplicate
substitution. Malformed initial/final inventory refuses with cleanup; CLI2
remains fail-closed. All nonzero native scan statuses retain precedence.
No baseline data, detector or source parser is reimplemented.

The worker calculation and exact inner command remain:

~~~text
detect-secrets --cores N scan --baseline <private-copy> <original ARGS>
~~~

The existing min(host CPU, worker_limit - 3) calculation keeps five workers
on this8-slot host. Both target names, GLUDD_WORKER_LIMIT, ARGS, native status
propagation and the push-blocking side effect remain. Default hook mode uses
scan_cores=None and retains its exact CLI forwarding kwargs, native statuses,
metadata-only status3 normalization and output contracts. No shared environment
is synced and no controller restart is required for a fresh Make/helper child.

### Bind the private baseline before native postprocessing

Later native arguments can replace --baseline, including equals forms and
unambiguous --basel abbreviations. Four actual API controls fail before the
guard, with two literal-token positive controls:4FAIL2PASS/.81s/native1.8s,
scan-baseline-override-red/v012-scan-baseline-override-red312.

Scan mode lazily reuses the pinned ParserBuilder console grammar through its
existing _parser accessor. Its
[public parse_args](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/core/usage/__init__.py#L144-L167)
also loads baselines and processes custom settings before returning. The helper
therefore validates the raw namespace first, requiring action scan and the exact
owned disposable baseline string. It does not resolve an override path or load
its baseline. The command vector stays unchanged after validation. This is one
version-pinned private API, already exercised by the native worker controls;
a future dependency change must retain the actual grammar controls.

Native raw parsing can check path syntax/existence and built-in plugin names.
It is not described as free of IO/imports. The
[plugin](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/core/usage/plugins.py)
and [filter](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/core/usage/filters.py)
postprocessors remain outside this validation. Two actual extension-file
tripwires prove custom code stays unexecuted before the mocked child; an import
finder, with cached modules scoped out/restored, proves default hook invocation
does not import native scan grammar. No parser or public API is stubbed.

Three actual parse-exit controls first fail1.29s/native2.4s at
scan-parse-exit-red/v012-scan-parse-exit-red312. Help is not a validated scan;
invalid/ambiguous native arguments now become content-free ValueError/CLI2
without a child. Native help/error text and caller values are not published
from this preflight. Literal -- filenames and baseline-like option values still
pass unchanged through the maintained grammar. Private copies are removed on
every tested terminal path; canonical bytes,mode,inode stay unchanged.

### Require an actual inventory scan

Pinned [scan control flow](https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/main.py#L37-L79)
also returns0 before refreshing the inventory for plugin listing, truthy string
detection and allowlisted-only display. Eight public API controls reproduce
false acceptance before this guard, alongside five genuine file-scan controls:
8FAIL5PASS/.91s/native2.0s at
scan-inventory-mode-red/v012-scan-inventory-mode-red312. Preserve its log/XML
under /tmp/gludd-v012-scan-inventory-mode-red312.

The same raw namespace now refuses list_all_plugins, truthy string and
only_allowlisted before the child. Native abbreviations populate those fields;
no option blacklist or second parse is needed. Match upstream truthiness:
empty --string= actually falls through to inventory refresh and stays valid.
Regex values and literal filenames after -- retain their exact argv and
native meaning. The new controls preserve canonical bytes/mode/inode, private
copy removal and content-free refusal. No timeout attribution follows.

### Native receiving proof and reproducible owner

The research/test proposals are retained at
/tmp/gludd-v012-watchdog-native-scan-semantics-decision.md,
/tmp/gludd-v012-readonly-full-scan-decision.md and
/tmp/gludd-v012-readonly-parser-isolation-decision.md.
Root rejected an earlier recorder that invented a run token. The accepted
fixture records actual scanner sys.argv with executable basename normalization
and records actual outer UV argv independently. It executes the real readonly
wrapper and mocks only its detect-secrets child. Existing worker/order/one-flag/
ARGS/status/baseline/cleanup assertions remain; only obsolete transport indexes
and an old shell-copy pin conform to the new existing-owner boundary.
The full old-pin replay1FAIL/66PASS7.64s/native8.6s remains failed evidence.

Ten complete source/caller owners pass257 each:31124.64s/native26.4s and
31225.27s/native27.7s, fatal warnings/zero test skips/errors. Native coverage
audits .gate-logs/coverage-20261022T001045Z.json and
.gate-logs/coverage-20261022T001007Z.json both pass99.0% line/96.4% branch for
the one canonical measured producer, retaining85 aggregate/75 each-file BOTH.
The208 passes/99.1line97.4branch remain intermediate history at
.gate-logs/coverage-20261022T000712Z.json and
.gate-logs/coverage-20261022T000639Z.json. The earlier195 passes/99.1line97.2branch remain intermediate history at
.gate-logs/coverage-20261021T235020Z.json and
.gate-logs/coverage-20261021T234914Z.json. Earlier92/100percent and127/100percent
receipts also remain intermediate history. These are whole-owner unit/source
profiles; legacy e2e-labelled audit fields supply no deployment or E2E proof.

The exact312 command is:

~~~console
make coverage-files COVERAGE_TESTFILES="tests/unit/test_detect_secrets_readonly.py tests/unit/test_detect_secrets_readonly_diagnostics.py tests/unit/test_detect_secrets_full_scan.py tests/unit/test_detect_secrets_parser_isolation.py tests/unit/test_secrets_scan_resources.py tests/unit/test_workflow_failure_class_guards.py tests/unit/test_watchdog.py tests/unit/test_watchdog_secrets_lifecycle.py tests/unit/test_security_audit_observability.py tests/unit/test_secrets_baseline_compaction.py" COVERAGE_CONFIG=config/coverage_detect_secrets_readonly.ini COVERAGE_REPORT=.gate-logs/coverage-v012-native-scan-reuse312.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=5 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=180 OBSERVED_RETAIN_RUNS=20 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 UV_NO_SYNC=1 UV_PYTHON=3.12.15 UV_PYTHON_DOWNLOADS=never
~~~

The311 invocation uses the installed python-3.11 sibling for both environment
paths, UV_PYTHON=3.11 and its separate reuse311 report. Its known requested-ci
jsonpointer refusal is distinct from this compatibility acceptance; no profile
identity or corruption claim follows. Actual registered312 inspection verifies
CPython3.12.15/ci/mismatches[]. Do not overwrite a shared live311 prefix to hide
the historical requested-profile diagnostic.

Family150 native-full-scan-success-inventory appends after all149 predecessors.
Its real new-digest API witness appears exactly once in the existing first
child, with no whole-owner widening. Native route RED1FAIL/.80s/native1.7s
precedes six canonical pins1.81s. Complete four routing/workflow files pass298
each:31152.05s/native53.2s and31246.21s/native47.2s. Preserve
/tmp/gludd-v012-native-scan-routing{311,312}.{log,xml}.
Only expected selector/ID data and the fixed149th-family position conform to
the appended150th record; prior values/order/assertions remain.
Three children2/1/1,90total60quiet,21phases2850 and full gate authority remain.

The actual full-scan source behavior examples retain the original variables:

~~~console
make scan-secrets GLUDD_WORKER_LIMIT=8 ARGS=
make secrets-scan GLUDD_WORKER_LIMIT=8 ARGS=
~~~

Receiving uses the installed312 tuple for outer run-watched and child Make,
one real scanner at a time after a natural watchdog drain. These are actual
tracked-tree behaviors before the later non-inventory guard and documentation
receiving, not sealed-candidate or timing benchmarks. The final257-case profile
checks that guard, canonical-manager reuse and both real-Make caller fixtures. Preserve native-full-scan-behavior receipts
v012-scan-secrets-behavior312 and v012-secrets-scan-behavior312, and their
/tmp/gludd-v012-<target>-behavior312.log logs. The first passes0/native70.2s.
Its180quiet/240total focus observer is separate from the unchanged watchdog60
and original publication90/60. The paired secrets-scan example passes0/native68.7s with its own
90quiet/120total caller bounds. Both use one real five-worker scanner serially;
post-behavior native worktree-state reports exactly17 owned changed paths,
with no canonical baseline or unrelated path changes.

### Firsthand context and release limits

The longstanding [baseline-versus-hook report246](https://github.com/Yelp/detect-secrets/issues/246)
matches the contract confusion; pinned native source establishes this defect.
A [2021 report433](https://github.com/Yelp/detect-secrets/issues/433) describes
full scans varying from minutes to hours. A
[2024 report868](https://github.com/Yelp/detect-secrets/issues/868) supplies a
whitespace-heavy nonlinear-cost example. These are historical operator evidence,
not attribution of either Gludd timeout or permission to disable detection.

This semantic repair supplies no speed optimization or60907 cause.
Natural scan-e60b7ebb522d49809c16801192c9f64b remains inactive124/60.555s,
max-runtime-timeout/null trace with drained descendants. Original60907 remains
124/90.554s/null trace/no transport. Native capacity96>95 and711.7MB scratch
still block final admission/proof. Full gate/global85aggregate75each-file line
AND branch, exact-SHA hosted/deployment, source review, reviewed-head receipt
and guarded promotion remain separate. All seven S98 tasks stay unchecked.
No baseline, verification, detector/filter/file set, worker budget,60scan/
300cooldown, original90/60, ZDD, cleanup/retention or promotion gate is weakened.


Final receiving static checks retain Ruff for all seven changed Python paths and
seven separate strict scopes, explicit production docstrings,276 Make contracts,
1157 targets/zero duplicates and full264 resource ownership. The existing native
inventory writer refreshes the readonly mkstemp location and two pre-existing
task_watchdog acquisition coordinates against current tracked source; all264
ownership classifications remain. The full empty-PATHS check then passes. The first isolated-test import-order failure is
retained; one blank line conforms to the actual repository import classification.
Six complete docs/handoff/ledger/parser-isolation files pass101 in5.40s/native6.3s
with fatal warnings, native-scan-reuse-handoff/v012-native-scan-reuse-handoff312;
retain /tmp/gludd-v012-native-scan-reuse-handoff312.{log,xml}. Earlier101 passes
at5.55s/native6.5s and5.53s/native6.7s remain intermediate final-handoff and
native-scan-handoff history. Markdown6, SESSION native-region
validation,17-path registration and the unchanged strict line guard pass.
The startup synopsis is compacted through immutable e946b08 exports; every old
criterion, source decision and failed receipt remains retrievable. The native
gate region is preserved. Normal receiving commit/fresh collection and exact
source review are owned by native HEAD and
/tmp/gludd-v012-native-scan-source-review.md; read that boundary before repeating
finished source work. None of these source receipts closes a release item.

### Reuse the existing canonical finding identity

The first normal receiving commit stops in the production duplicate-code guard,
before fresh collection. Locked jscpd reports one14-line/82-token renamed clone:
detect_secrets_readonly.py44-57 versus manage_secrets_baseline.py135-148.
Preserve the native failed hook output and
/Users/shawnwilson/tmp/gludd-duplicate-code-brtpgxpv/report/jscpd-report.json.
The finding-validation block already exists in the e946 readonly source;
no claim about the detector's fingerprint cause follows from the changed file.

The readonly loader now delegates to the existing canonical manager's public
finding_counts, rather than maintaining another identity validator. It retains
its JSON/root boundary and maps the manager's content-free BaselineError into
the public ValueError/CLI2 contract. Existing filename fallback, multiplicity,
metadata, malformed-data and cleanup assertions stay unchanged. The established
audit_public_secrets import/bootstrap pattern supports direct-script and package
entrypoints without loading detect-secrets in default hook mode. The complete
canonical manager test file joins the receiving cohort; no manager code,
clone baseline, detector threshold or test assertion is changed.
